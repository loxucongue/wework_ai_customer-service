from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.graph.nodes.common import json_dumps
from app.graph.nodes.reply_contract import (
    _v3_available_assets_for_turn,
)
from app.graph.nodes.reply_generation import (
    _validated_parallel_reply_payload,
)
from app.graph.nodes.reply_validation import validated_model_messages
from app.policies.business_rules import load_business_rules, parallel_reply_business_rules_for_model
from app.prompts.reply_synthesizer import build_parallel_reply_messages
from app.services.deepseek_semantic_client import DeepSeekSemanticClient
from app.services.follow_knowledge_client import FollowKnowledgeClient
from app.services.v3_sop_execution_service import SopExecutionService
from app.services.sop_reply_pack_service import SopReplyPackService
from app.services.v3_semantic_router_service import V3SemanticRouterService, script_content_candidates


SCENARIOS = {
    "effect": {
        "history": [
            ("assistant", "这是参加活动顾客做完后的真实对比，您先看看改善方向。"),
        ],
        "current": "请问祛斑是一次性的吗？",
    },
    "price": {
        "history": [
            ("assistant", "您是看线上淡斑活动加进来的吧。"),
        ],
        "current": "多少钱？",
    },
    "distance": {
        "history": [
            ("assistant", "这家是按您位置排下来相对近的门店，门店卡已经发您了。"),
        ],
        "current": "还是太远了。",
    },
    "hesitation": {
        "history": [
            ("assistant", "活动内容、费用和效果参考都给您讲清楚了。"),
        ],
        "current": "我再考虑一下。",
    },
}


async def _run(args: argparse.Namespace) -> Path:
    _assert_deepseek_models(args.router_model, args.reply_model)
    settings = get_settings()
    router_settings = settings.model_copy(
        update={
            "deepseek_semantic_model": args.router_model,
            "deepseek_semantic_timeout_seconds": args.router_timeout,
            "deepseek_semantic_max_tokens": args.router_max_tokens,
            "service_rule_data_enabled": False,
        }
    )
    reply_settings = settings.model_copy(
        update={
            "deepseek_semantic_model": args.reply_model,
            "deepseek_semantic_timeout_seconds": args.reply_timeout,
            "deepseek_semantic_max_tokens": args.reply_max_tokens,
            "service_rule_data_enabled": False,
        }
    )
    router_client = DeepSeekSemanticClient(router_settings, fallback_client=None)
    reply_client = DeepSeekSemanticClient(reply_settings, fallback_client=None)
    knowledge = FollowKnowledgeClient(settings)
    router = V3SemanticRouterService(
        semantic_client=router_client,
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
        reply_validation: dict[str, Any] = {"status": "not_run", "error": ""}
        if not args.skip_reply:
            try:
                reply_output = await reply_client.chat_json(reply_messages)
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

        router_preview = route_result.get("prompt_preview") or {}
        reply_usage = reply_client.last_usage or {}
        report = {
            "schema_version": "v3_prompt_context_audit_v2",
            "created_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
            "scenario": args.scenario,
            "providers": {
                "router": "deepseek_official_only",
                "router_model": args.router_model,
                "reply": "deepseek_official_only",
                "reply_model": args.reply_model,
                "gpt_called": False,
            },
            "counts": {
                "router_system_chars": _message_chars(router_preview.get("router_messages"), "system"),
                "router_user_chars": _message_chars(router_preview.get("router_messages"), "user"),
                "selector_chars": sum(
                    len(str(item.get("content") or ""))
                    for item in router_preview.get("selector_messages") or []
                    if isinstance(item, dict)
                ),
                "reply_system_chars": len(reply_messages[0]["content"]),
                "reply_user_chars": len(reply_messages[1]["content"]),
                "reply_total_chars": sum(len(item["content"]) for item in reply_messages),
                "reply_section_chars": _section_char_counts(reply_messages[1]["content"]),
                "reply_prompt_tokens": _usage_token(reply_usage, "prompt_tokens"),
                "reply_completion_tokens": _usage_token(reply_usage, "completion_tokens"),
                "sequence_candidates": len((route_result.get("knowledge_evidence") or {}).get("sequence_candidates") or []),
                "script_candidates": len((route_result.get("knowledge_evidence") or {}).get("candidates") or []),
                "content_candidates": len(candidates),
            },
            "semantic_route": route_result.get("semantic_route") or {},
            "knowledge_evidence": route_result.get("knowledge_evidence") or {},
            "router_messages": router_preview.get("router_messages") or [],
            "selector_messages": router_preview.get("selector_messages") or [],
            "reply_messages": reply_messages,
            "reply_output": reply_output,
            "reply_error": reply_error,
            "reply_validation": reply_validation,
            "router_model_usage": router_client.last_usage or {},
            "reply_model_usage": reply_usage,
        }
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = output_dir / f"v3_prompt_context_{args.scenario}_{stamp}.json"
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        output_path.with_suffix(".md").write_text(_markdown(report), encoding="utf-8")
        return output_path
    finally:
        await router_client.aclose()
        await reply_client.aclose()
        await knowledge.aclose()


def _shared_context(scenario: dict[str, Any]) -> dict[str, Any]:
    conversation = [
        {
            "message_ref": f"conv_{index:03d}",
            "role": role,
            "content": content,
            "sent_at": f"2026-08-22 14:{index:02d}:00",
        }
        for index, (role, content) in enumerate(scenario["history"], start=1)
    ]
    return {
        "schema_version": "shared_context_v2",
        "current_time": {"iso": "2026-08-22T14:10:00+08:00", "timezone": "Asia/Shanghai"},
        "current_message": {
            "message_ref": "current_message",
            "content": scenario["current"],
            "message_type": "text",
            "sent_at": "2026-08-22 14:10:00",
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
    customer_refs = [
        "current_message",
        *[
            item["message_ref"]
            for item in shared.get("conversation") or []
            if item.get("role") == "customer"
        ],
    ]
    content_ids = [
        str(item.get("content_id") or "")
        for item in candidates
        if str(item.get("content_id") or "")
    ]
    evidence = {
        "schema_version": "reply_chain_evidence_join_v1",
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
        "valid_customer_message_refs": customer_refs,
        "valid_deposit_evidence_refs": customer_refs,
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


def _message_chars(messages: Any, role: str) -> int:
    return sum(
        len(str(item.get("content") or ""))
        for item in messages or []
        if isinstance(item, dict) and item.get("role") == role
    )


def _usage_token(usage: dict[str, Any], key: str) -> int:
    nested = usage.get("usage") if isinstance(usage.get("usage"), dict) else {}
    return int(nested.get(key) or usage.get(key) or 0)


def _section_char_counts(content: str) -> dict[str, int]:
    matches = list(re.finditer(r"^【([^】]+)】\n", content, flags=re.MULTILINE))
    output: dict[str, int] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        output[match.group(1)] = len(content[match.end() : end].strip())
    return output


def _markdown(report: dict[str, Any]) -> str:
    counts = report["counts"]
    route = report.get("semantic_route") or {}
    reply_messages = report.get("reply_messages") or [{"content": ""}, {"content": ""}]
    lines = [
        "# V3 Reply 完整提示词上下文审核",
        "",
        "## 测试声明",
        "",
        f"- 场景：`{report['scenario']}`",
        f"- 路由模型：`{report['providers']['router_model']}`（DeepSeek 官方接口，无 GPT fallback）",
        f"- Reply 模型：`{report['providers']['reply_model']}`（DeepSeek 官方接口，无 GPT fallback）",
        "- GPT 调用：`否`",
        f"- 卡点：`{(route.get('checkpoint') or {}).get('primary_code', '')}`",
        f"- 门店查询：`{(route.get('store_query') or {}).get('required', False)}`",
        "",
        "## 体积统计",
        "",
        "| 板块 | 字符数 |",
        "|---|---:|",
        f"| Router system | {counts['router_system_chars']} |",
        f"| Router user | {counts['router_user_chars']} |",
        f"| Selector | {counts['selector_chars']} |",
        f"| Reply system | {counts['reply_system_chars']} |",
        f"| Reply user | {counts['reply_user_chars']} |",
        f"| Reply total | {counts['reply_total_chars']} |",
        f"| Reply prompt tokens（供应商统计） | {counts['reply_prompt_tokens']} |",
        f"| Reply completion tokens（供应商统计） | {counts['reply_completion_tokens']} |",
        "",
        "### Reply 用户上下文分区",
        "",
        "| 分区 | 字符数 |",
        "|---|---:|",
    ]
    for name, count in counts.get("reply_section_chars", {}).items():
        lines.append(f"| {name} | {count} |")
    lines.extend(
        [
            "",
            "## DeepSeek 路由完整输入",
            "",
            _messages_markdown(report.get("router_messages") or []),
            "",
            "## DeepSeek 话术精选完整输入",
            "",
            _messages_markdown(report.get("selector_messages") or []) or "本场景未触发第二次精选。",
            "",
            "## Reply System Prompt（完整）",
            "",
            "```text",
            str(reply_messages[0].get("content") or ""),
            "```",
            "",
            "## Reply User Context（完整）",
            "",
            "```text",
            str(reply_messages[1].get("content") or ""),
            "```",
            "",
            "## DeepSeek Pro 原始输出",
            "",
            "```json",
            json.dumps(report.get("reply_output") or {"error": report.get("reply_error")}, ensure_ascii=False, indent=2),
            "```",
            "",
            "## 结构校验后客户可见消息",
            "",
            "```json",
            json.dumps(report.get("reply_validation") or {}, ensure_ascii=False, indent=2),
            "```",
            "",
            "## 知识命中",
            "",
            f"- 序列候选：`{counts['sequence_candidates']}`",
            f"- 话术候选：`{counts['script_candidates']}`",
            f"- 全部内容候选：`{counts['content_candidates']}`",
        ]
    )
    return "\n".join(lines) + "\n"


def _messages_markdown(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        lines.extend(
            [
                f"### {str(item.get('role') or 'unknown').title()}",
                "",
                "```text",
                str(item.get("content") or ""),
                "```",
                "",
            ]
        )
    return "\n".join(lines).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview V3 Router and Reply prompts using DeepSeek only.")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="effect")
    parser.add_argument("--router-model", default="deepseek-v4-flash")
    parser.add_argument("--reply-model", default="deepseek-v4-pro")
    parser.add_argument("--router-timeout", type=float, default=20.0)
    parser.add_argument("--reply-timeout", type=float, default=90.0)
    parser.add_argument("--router-max-tokens", type=int, default=1200)
    parser.add_argument("--reply-max-tokens", type=int, default=3000)
    parser.add_argument("--skip-reply", action="store_true")
    parser.add_argument("--output-dir", default=".tmp_runtime/v3_reply_prompt_audit")
    args = parser.parse_args()
    print(asyncio.run(_run(args)))


def _assert_deepseek_models(*models: str) -> None:
    invalid = [str(model or "") for model in models if not str(model or "").lower().startswith("deepseek-")]
    if invalid:
        raise RuntimeError(f"DeepSeek-only preview rejected model(s): {', '.join(invalid)}")


if __name__ == "__main__":
    main()
