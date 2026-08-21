from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.graph.nodes.common import json_dumps
from app.graph.nodes.parallel_reply_chain import _v3_available_assets_for_turn
from app.graph.nodes.reply_nodes import _validated_parallel_reply_payload
from app.graph.nodes.reply_validation import validated_model_messages
from app.policies.business_rules import load_business_rules, parallel_reply_business_rules_for_model
from app.prompts.reply_synthesizer import build_parallel_reply_messages
from app.services.deepseek_semantic_client import DeepSeekSemanticClient
from app.services.follow_knowledge_client import FollowKnowledgeClient
from app.services.model_client import ModelClient
from app.services.sop_execution_service import SopExecutionService
from app.services.sop_reply_pack_service import SopReplyPackService
from app.services.v3_semantic_router_service import V3SemanticRouterService, script_content_candidates


SCENARIOS = {
    "distance": {
        "history": [
            ("assistant", "这家是按您位置排下来最近的门店，我把门店卡发您了。"),
            ("customer", "还是太远了"),
        ],
        "current": "还是太远了",
    },
    "price": {
        "history": [
            ("assistant", "您是看线上淡斑活动加进来的吧？"),
            ("customer", "多少钱"),
        ],
        "current": "多少钱",
    },
    "store": {
        "history": [
            ("customer", "上海有吗"),
            ("assistant", "您具体在哪个区呀？"),
            ("customer", "上海浦东有门店吗"),
        ],
        "current": "上海浦东有门店吗",
    },
    "hesitation": {
        "history": [
            ("assistant", "活动内容、费用和效果参考我都给您说清楚了。"),
            ("customer", "我再考虑一下"),
        ],
        "current": "我再考虑一下",
    },
}


async def _run(args: argparse.Namespace) -> Path:
    settings = get_settings()
    fallback = ModelClient(
        settings.model_copy(
            update={
                "model_fast": "gpt-5.4-mini",
                "model_fast_fallbacks": "gpt-5.4",
                "model_emergency_fallbacks": "",
                "model_hedge_max_parallel": 1,
            }
        )
    )
    reply_client = ModelClient(settings)
    deepseek = DeepSeekSemanticClient(settings, fallback)
    knowledge = FollowKnowledgeClient(settings)
    router = V3SemanticRouterService(
        semantic_client=deepseek,
        knowledge_client=knowledge,
        script_threshold=settings.deepseek_semantic_script_threshold,
        max_scripts=settings.deepseek_semantic_max_scripts,
    )
    try:
        shared = _shared_context(SCENARIOS[args.scenario])
        route_result = await router.route(shared_context=shared)
        candidates = [
            *_approved_assets(settings, shared),
            *script_content_candidates(route_result.get("knowledge_evidence") or {}),
        ]
        payload = _reply_payload(shared, route_result, candidates)
        reply_messages = build_parallel_reply_messages(payload, json_dumps=json_dumps)
        reply_output: dict[str, Any] = {}
        reply_error = ""
        reply_validation = {"status": "not_run", "error": ""}
        if not args.skip_reply:
            try:
                reply_output = await reply_client.chat_json(reply_messages, tier="reply", temperature=0.2)
                validation_state = {
                    "evidence_join": payload["evidence"],
                    "business_rules": load_business_rules(),
                    "request_context": {
                        "interface_version": "v3",
                        "reply_chain_mode": "model_led_sales_brain_v3",
                    },
                }
                validated = _validated_parallel_reply_payload(
                    state=validation_state,
                    payload=reply_output,
                    validated_model_messages=validated_model_messages,
                    warnings=[],
                )
                reply_validation = {
                    "status": "pass",
                    "message_count": len(validated),
                    "messages": validated,
                    "error": "",
                }
            except Exception as exc:
                reply_error = f"{type(exc).__name__}: {exc}"[:1000]
                reply_validation = {"status": "fail", "error": reply_error}
        report = {
            "schema_version": "v3_prompt_preview_v1",
            "created_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
            "scenario": args.scenario,
            "counts": {
                "router_system_chars": len(route_result.get("prompt_preview", {}).get("router_messages", [{}])[0].get("content", "")),
                "router_user_chars": len(route_result.get("prompt_preview", {}).get("router_messages", [{}, {}])[-1].get("content", "")),
                "selector_chars": sum(len(item.get("content", "")) for item in route_result.get("prompt_preview", {}).get("selector_messages", [])),
                "reply_system_chars": len(reply_messages[0]["content"]),
                "reply_user_chars": len(reply_messages[1]["content"]),
                "sequence_candidates": len((route_result.get("knowledge_evidence") or {}).get("sequence_candidates") or []),
                "script_candidates": len((route_result.get("knowledge_evidence") or {}).get("candidates") or []),
                "content_candidates": len(candidates),
            },
            "semantic_route": route_result.get("semantic_route") or {},
            "knowledge_evidence": route_result.get("knowledge_evidence") or {},
            "router_messages": route_result.get("prompt_preview", {}).get("router_messages") or [],
            "selector_messages": route_result.get("prompt_preview", {}).get("selector_messages") or [],
            "reply_messages": reply_messages,
            "reply_output": reply_output,
            "reply_error": reply_error,
            "reply_validation": reply_validation,
            "reply_model_usage": reply_client.last_usage or {},
        }
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = output_dir / f"v3_prompt_preview_{args.scenario}_{stamp}.json"
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        output_path.with_suffix(".md").write_text(_markdown(report), encoding="utf-8")
        return output_path
    finally:
        await deepseek.aclose()
        await fallback.aclose()
        await reply_client.aclose()
        await knowledge.aclose()


def _shared_context(scenario: dict[str, Any]) -> dict[str, Any]:
    conversation = [
        {
            "message_ref": f"conv_{index:03d}",
            "role": role,
            "content": content,
            "sent_at": f"2026-08-20 14:{index:02d}:00",
        }
        for index, (role, content) in enumerate(scenario["history"], start=1)
    ]
    return {
        "schema_version": "shared_context_v2",
        "current_time": {"iso": "2026-08-20T14:10:00+08:00", "timezone": "Asia/Shanghai"},
        "current_message": {
            "message_ref": "current_message",
            "content": scenario["current"],
            "message_type": "text",
            "sent_at": "2026-08-20 14:10:00",
        },
        "conversation": conversation,
        "authoritative_facts": {
            "orders_and_payment": {"resolved_payment": {"deposit_state": "required_unpaid"}},
            "registration_facts": {},
            "sent_messages": {},
        },
        "derived_observations": {},
        "rules": parallel_reply_business_rules_for_model(),
    }


def _approved_assets(settings, shared: dict[str, Any]) -> list[dict[str, Any]]:
    service = object.__new__(SopExecutionService)
    service.sop_reply_pack_service = SopReplyPackService(settings)
    state = {"business_rules": load_business_rules()}
    return _v3_available_assets_for_turn(
        state,
        service.reply_chain_available_assets(),
        sent_summary=(shared.get("authoritative_facts") or {}).get("sent_messages") or {},
    )


def _reply_payload(
    shared: dict[str, Any],
    route_result: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    refs = ["current_message", *[item["message_ref"] for item in shared.get("conversation") or []]]
    content_ids = [str(item.get("content_id") or "") for item in candidates if str(item.get("content_id") or "")]
    evidence = {
        "shared_context": shared,
        "semantic_route": route_result.get("semantic_route") or {},
        "knowledge_evidence": route_result.get("knowledge_evidence") or {},
        "content_candidates": candidates,
        "tool_facts": {},
        "normalized_tool_facts": {},
        "missing_facts": [],
        "authority_conflicts": [],
    }
    return {
        "evidence": evidence,
        "structured_delivery_options": {},
        "valid_message_refs": refs,
        "valid_customer_message_refs": ["current_message", *[item["message_ref"] for item in shared.get("conversation") or [] if item.get("role") == "customer"]],
        "valid_deposit_evidence_refs": refs,
        "allowed_selected_content_ids": content_ids,
        "content_candidate_reference_options": [f"content_asset:{item}" for item in content_ids],
        "follow_sequence_reference_options": [
            item.get("sequence_id")
            for item in (route_result.get("knowledge_evidence") or {}).get("sequence_candidates") or []
        ],
        "follow_script_reference_options": [
            item.get("source_id")
            for item in (route_result.get("knowledge_evidence") or {}).get("candidates") or []
        ],
        "valid_commit_evidence": [],
        "current_turn_structural_constraints": [],
    }


def _markdown(report: dict[str, Any]) -> str:
    counts = report["counts"]
    route = report.get("semantic_route") or {}
    return "\n".join(
        [
            "# V3 Prompt Preview",
            "",
            f"- 场景：`{report['scenario']}`",
            f"- 卡点：`{(route.get('checkpoint') or {}).get('primary_code', '')}`",
            f"- 门店查询：`{(route.get('store_query') or {}).get('required', False)}`",
            f"- Router 字符：`{counts['router_system_chars'] + counts['router_user_chars']}`",
            f"- Selector 字符：`{counts['selector_chars']}`",
            f"- Reply 字符：`{counts['reply_system_chars'] + counts['reply_user_chars']}`",
            f"- 序列/话术/素材候选：`{counts['sequence_candidates']}/{counts['script_candidates']}/{counts['content_candidates']}`",
            f"- 线上结构准入：`{(report.get('reply_validation') or {}).get('status', 'not_run')}`",
            "",
            "## 客户可见输出",
            "",
            "```json",
            json.dumps(report.get("reply_output") or {"error": report.get("reply_error")}, ensure_ascii=False, indent=2),
            "```",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview V3 DeepSeek and Reply prompts with synthetic data.")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="distance")
    parser.add_argument("--skip-reply", action="store_true")
    parser.add_argument("--output-dir", default=".tmp_runtime/v3_prompt_preview")
    args = parser.parse_args()
    print(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
