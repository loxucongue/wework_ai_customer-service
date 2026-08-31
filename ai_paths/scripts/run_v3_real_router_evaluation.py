from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import sqlite3
import statistics
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.config import Settings, get_settings
from app.services.deepseek_semantic_client import DeepSeekSemanticClient
from app.services.follow_knowledge_client import FollowKnowledgeClient
from app.services.outreach_send_client import OutreachSendClient
from app.services.v3_semantic_router_service import V3SemanticRouterService


TZ = ZoneInfo("Asia/Shanghai")
_INTERNAL_MARKERS = ("sim_", "smoke", "codex", "internal_test", "测试客户")
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_TOKEN_RE = re.compile(r"(?i)(token|signature|sig|accesskeyid)=([^&\s]+)")
_LONG_ID_RE = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{24,}(?![A-Za-z0-9_-])")
_NON_BUSINESS_CURRENT_MESSAGES = {
    "[OK]",
    "[emotion消息]",
    "[图片消息]",
    "[语音消息]",
    "[视频消息]",
    "[音视频通话消息]",
    "[定位消息]",
    "[文件消息]",
    "[消息已撤回]",
    "我已经添加了你，现在我们可以开始聊天了。",
}


def _json_loads(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        decoded = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))[:2000]


def _masked_case_id(identity: dict[str, str], index: int) -> str:
    raw = "|".join(identity.get(key, "") for key in ("corp_id", "wechat", "external_userid", "customer_id"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
    return f"sim_router_{index:03d}_{digest}"


def _redact(value: str, identity_values: list[str]) -> str:
    result = _URL_RE.sub("[URL已脱敏]", str(value or ""))
    result = _TOKEN_RE.sub(r"\1=[已脱敏]", result)
    result = _PHONE_RE.sub("[手机号已脱敏]", result)
    for raw in sorted({item for item in identity_values if len(item) >= 4}, key=len, reverse=True):
        result = result.replace(raw, "[身份已脱敏]")
    return _LONG_ID_RE.sub("[长标识已脱敏]", result)


def _is_internal(identity: dict[str, str]) -> bool:
    combined = " ".join(identity.values()).lower()
    return any(marker in combined for marker in _INTERNAL_MARKERS)


def _recent_identities(db_path: Path, *, hours: int, pool_size: int) -> list[dict[str, str]]:
    cutoff = (datetime.now(TZ) - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT input_snapshot, MAX(created_at) AS latest_at, COUNT(*) AS run_count
            FROM runs
            WHERE datetime(created_at) >= datetime(?)
            GROUP BY
              json_extract(input_snapshot, '$.corp_id'),
              lower(json_extract(input_snapshot, '$.wechat')),
              coalesce(json_extract(input_snapshot, '$.external_userid'), json_extract(input_snapshot, '$.customer_id'))
            ORDER BY latest_at DESC
            LIMIT ?
            """,
            (cutoff, max(pool_size, 30)),
        ).fetchall()
    finally:
        connection.close()
    identities: list[dict[str, str]] = []
    for row in rows:
        snapshot = _json_loads(row["input_snapshot"])
        context = snapshot.get("request_context") if isinstance(snapshot.get("request_context"), dict) else {}
        identity = {
            "corp_id": _text(snapshot.get("corp_id") or context.get("corp_id")),
            "wechat": _text(snapshot.get("wechat") or context.get("wechat")),
            "external_userid": _text(snapshot.get("external_userid") or context.get("external_userid")),
            "customer_id": _text(snapshot.get("customer_id") or context.get("customer_id")),
            "user_id": _text(snapshot.get("user_id") or context.get("user_id")),
            "latest_at": _text(row["latest_at"]),
            "run_count": _text(row["run_count"]),
        }
        if all(identity.get(key) for key in ("corp_id", "wechat", "external_userid", "customer_id", "user_id")) and not _is_internal(identity):
            identities.append(identity)
    return identities


def _message_role(message: dict[str, Any]) -> str:
    for key in ("role", "from", "direction", "sender_type", "message_role", "from_type"):
        value = _text(message.get(key)).lower()
        if value in {"user", "customer", "inbound", "external", "client"}:
            return "customer"
        if value in {"assistant", "staff", "outbound", "agent", "ai", "employee", "service"}:
            return "assistant"
    if message.get("is_customer") is True:
        return "customer"
    if message.get("is_self") is True:
        return "assistant"
    return "unknown"


def _message_content(message: dict[str, Any]) -> str:
    for key in ("content", "text", "message", "body", "msg_content"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            for nested_key in ("content", "text", "title", "address", "name"):
                nested = _text(value.get(nested_key))
                if nested:
                    return nested
    msg_type = _text(message.get("msgtype") or message.get("type") or message.get("message_type")).lower()
    return {
        "image": "[图片消息]",
        "voice": "[语音消息]",
        "location": "[定位消息]",
        "video": "[视频消息]",
        "file": "[文件消息]",
    }.get(msg_type, "")


def _message_time(message: dict[str, Any], fallback_index: int) -> str:
    for key in ("sent_at", "created_at", "msgtime", "timestamp", "time", "send_time"):
        value = message.get(key)
        if value in (None, ""):
            continue
        if isinstance(value, (int, float)) or str(value).isdigit():
            number = float(value)
            if number > 10_000_000_000:
                number /= 1000
            try:
                return datetime.fromtimestamp(number, TZ).isoformat()
            except (OverflowError, OSError, ValueError):
                pass
        return _text(value)
    return f"order:{fallback_index:03d}"


def _normalized_conversation(
    messages: list[dict[str, Any]],
    *,
    identity_values: list[str],
) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for index, message in enumerate(messages, start=1):
        role = _message_role(message)
        content = _redact(_message_content(message), identity_values)
        if role not in {"customer", "assistant"} or not content:
            continue
        normalized.append(
            {
                "message_ref": f"conv_{len(normalized) + 1:03d}",
                "role": role,
                "content": content[:4000],
                "sent_at": _message_time(message, index),
                "message_type": _text(message.get("msgtype") or message.get("type") or "text") or "text",
            }
        )
    return normalized


def _conversation_until_last_customer(conversation: list[dict[str, str]]) -> tuple[list[dict[str, str]], dict[str, str]] | None:
    customer_indexes = [
        index
        for index, item in enumerate(conversation)
        if item.get("role") == "customer"
        and str(item.get("content") or "").strip() not in _NON_BUSINESS_CURRENT_MESSAGES
    ]
    if not customer_indexes:
        return None
    current_index = customer_indexes[-1]
    current = dict(conversation[current_index])
    history = [dict(item) for item in conversation[:current_index]]
    current["message_ref"] = "current_message"
    return history, current


def _shared_context(case_id: str, history: list[dict[str, str]], current: dict[str, str]) -> dict[str, Any]:
    return {
        "schema_version": "shared_context_v2",
        "simulation_mode": True,
        "customer_id": case_id,
        "current_time": {"iso": datetime.now(TZ).isoformat(), "timezone": "Asia/Shanghai"},
        "current_message": current,
        "conversation": history,
        "authoritative_facts": {
            "orders_and_payment": {"summary": "本专项不判断订单成交，仅测试语义路由。"},
            "registration_facts": {},
            "sent_messages": {},
        },
        "derived_observations": {},
        "rules": [],
    }


def _selected_sequence_details(route_result: dict[str, Any]) -> list[dict[str, Any]]:
    knowledge = route_result.get("knowledge_evidence") if isinstance(route_result.get("knowledge_evidence"), dict) else {}
    return [item for item in knowledge.get("sequence_candidates") or [] if isinstance(item, dict)]


def _script_details(route_result: dict[str, Any]) -> list[dict[str, Any]]:
    knowledge = route_result.get("knowledge_evidence") if isinstance(route_result.get("knowledge_evidence"), dict) else {}
    details = []
    for item in knowledge.get("candidates") or []:
        if not isinstance(item, dict):
            continue
        details.append(
            {
                "script_id": item.get("source_id") or item.get("script_code"),
                "name": item.get("name") or item.get("script_name"),
                "checkpoint_code": item.get("checkpoint_code"),
                "action_code": item.get("action_code"),
                "text_summary": _text(item.get("body_text") or item.get("text") or item.get("summary"))[:240],
                "sequence_links": item.get("sequence_links") or [],
            }
        )
    return details


def _structural_issues(route_result: dict[str, Any], valid_refs: set[str]) -> list[str]:
    issues: list[str] = []
    route = route_result.get("semantic_route") if isinstance(route_result.get("semantic_route"), dict) else {}
    checkpoint = route.get("checkpoint") if isinstance(route.get("checkpoint"), dict) else {}
    current_intent = route.get("current_intent") if isinstance(route.get("current_intent"), dict) else {}
    current_friction = route.get("current_friction") if isinstance(route.get("current_friction"), dict) else {}
    historical = (
        route.get("historical_unresolved_friction")
        if isinstance(route.get("historical_unresolved_friction"), dict)
        else {}
    )
    store_query = route.get("store_query") if isinstance(route.get("store_query"), dict) else {}
    refs = [
        *checkpoint.get("evidence_refs", []),
        *current_intent.get("evidence_refs", []),
        *current_friction.get("evidence_refs", []),
        *historical.get("evidence_refs", []),
        *store_query.get("location_evidence_refs", []),
    ]
    invalid_refs = sorted({_text(ref) for ref in refs if _text(ref) and _text(ref) not in valid_refs})
    if invalid_refs:
        issues.append(f"invalid_message_refs:{','.join(invalid_refs)}")
    if _text(current_intent.get("summary")) and not current_intent.get("evidence_refs"):
        issues.append("missing_current_intent_refs")
    if _text(current_friction.get("summary")) and not current_friction.get("evidence_refs"):
        issues.append("missing_current_friction_refs")
    if _text(historical.get("summary")) and not historical.get("evidence_refs"):
        issues.append("missing_historical_friction_refs")
    if route_result.get("status") != "ok":
        issues.append(f"route_status:{route_result.get('status')}")
    if route.get("status") not in {"ok", "empty"}:
        issues.append(f"semantic_status:{route.get('status')}")
    return issues


async def _fetch_cases(
    outreach: OutreachSendClient,
    identities: list[dict[str, str]],
    *,
    max_customers: int,
    conversation_limit: int,
    concurrency: int,
) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def fetch(identity: dict[str, str]) -> tuple[dict[str, str], dict[str, Any]]:
        async with semaphore:
            result = await outreach.fetch_conversation(
                corp_id=identity["corp_id"],
                customer_id=identity["customer_id"],
                external_userid=identity["external_userid"],
                user_id=identity["user_id"],
                wechat=identity["wechat"],
                limit=conversation_limit,
            )
            return identity, result

    fetched = await asyncio.gather(*(fetch(item) for item in identities))
    eligible: list[dict[str, Any]] = []
    for identity, result in fetched:
        if result.get("status") != "ok":
            continue
        identity_values = [value for value in identity.values() if isinstance(value, str)]
        conversation = _normalized_conversation(result.get("messages") or [], identity_values=identity_values)
        split = _conversation_until_last_customer(conversation)
        if split is None or len(conversation) < 3:
            continue
        history, current = split
        if not current.get("content") or current.get("content", "").startswith("[身份已脱敏]"):
            continue
        eligible.append({"identity": identity, "history": history, "current": current, "message_count": len(conversation)})
    eligible.sort(key=lambda item: (item["message_count"], item["identity"].get("latest_at", "")), reverse=True)
    return eligible[:max_customers]


def _snapshot_cases(path: Path, *, max_customers: int) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("cases") if isinstance(payload, dict) else []
    output: list[dict[str, Any]] = []
    for raw in rows if isinstance(rows, list) else []:
        if not isinstance(raw, dict):
            continue
        case_id = _text(raw.get("case_id"))
        history = [dict(item) for item in raw.get("conversation") or [] if isinstance(item, dict)]
        current = dict(raw.get("current_message") or {})
        if not case_id.startswith("sim_") or not history or not current.get("content"):
            continue
        source = raw.get("source") if isinstance(raw.get("source"), dict) else {}
        output.append(
            {
                "identity": {
                    "corp_id": "snapshot",
                    "wechat": "snapshot",
                    "external_userid": case_id,
                    "customer_id": case_id,
                    "latest_at": _text(source.get("latest_request_at")),
                },
                "history": history,
                "current": current,
                "message_count": int(source.get("message_count") or len(history) + 1),
                "expected_annotation": dict(raw.get("expected_annotation") or {}),
            }
        )
        if len(output) >= max_customers:
            break
    if not output:
        raise RuntimeError("No anonymized cases were found in the snapshot")
    return output


async def _evaluate(args: argparse.Namespace) -> Path:
    _assert_deepseek_model(args.router_model)
    settings = _router_test_settings(get_settings(), router_model=args.router_model)
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    outreach = OutreachSendClient(settings)
    knowledge = FollowKnowledgeClient(settings)
    deepseek = DeepSeekSemanticClient(settings, None)
    router = V3SemanticRouterService(
        semantic_client=deepseek,
        knowledge_client=knowledge,
        script_threshold=settings.deepseek_semantic_script_threshold,
        max_scripts=settings.deepseek_semantic_max_scripts,
    )
    snapshot_path = Path(args.snapshot).resolve() if args.snapshot else None
    if snapshot_path is None and not outreach.available:
        raise RuntimeError("Outreach conversation read-only API is not configured")
    if not knowledge.available:
        raise RuntimeError("Follow knowledge API is not configured")
    if not deepseek.available:
        raise RuntimeError("DeepSeek official provider is not configured")
    try:
        if snapshot_path is not None:
            cases = _snapshot_cases(snapshot_path, max_customers=args.max_customers)
        else:
            identities = _recent_identities(
                Path(args.db_path or settings.db_path),
                hours=args.hours,
                pool_size=max(args.max_customers * 4, 100),
            )
            cases = await _fetch_cases(
                outreach,
                identities,
                max_customers=args.max_customers,
                conversation_limit=args.conversation_limit,
                concurrency=args.fetch_concurrency,
            )
        wanted_case_ids = {item.strip() for item in str(args.case_id or "").split(",") if item.strip()}
        if wanted_case_ids:
            def replay_case_id(item: dict[str, Any]) -> str:
                identity = item.get("identity") if isinstance(item.get("identity"), dict) else {}
                return str(
                    item.get("case_id")
                    or identity.get("external_userid")
                    or identity.get("customer_id")
                    or ""
                )

            available_case_ids = {replay_case_id(item) for item in cases}
            missing_case_ids = sorted(wanted_case_ids - available_case_ids)
            if missing_case_ids:
                raise ValueError("snapshot is missing requested case IDs: " + ",".join(missing_case_ids))
            cases = [item for item in cases if replay_case_id(item) in wanted_case_ids]
        if not cases:
            raise RuntimeError("No eligible real conversations were found")
        sequence_index = await knowledge.query_all_sequences()
        if sequence_index.get("status") != "ok":
            raise RuntimeError(f"Sequence API failed: {sequence_index.get('reason')}")

        semaphore = asyncio.Semaphore(max(1, args.model_concurrency))

        async def run_case(index: int, case: dict[str, Any]) -> dict[str, Any]:
            case_id = _masked_case_id(case["identity"], index)
            shared = _shared_context(case_id, case["history"], case["current"])
            queued_at = time.perf_counter()
            async with semaphore:
                execution_started = time.perf_counter()
                queue_wait_ms = int((execution_started - queued_at) * 1000)
                result = await router.route(shared_context=shared, sequence_result=sequence_index)
                execution_ms = int((time.perf_counter() - execution_started) * 1000)
            duration_ms = int(result.get("duration_ms") or execution_ms)
            route = result.get("semantic_route") if isinstance(result.get("semantic_route"), dict) else {}
            checkpoint = route.get("checkpoint") if isinstance(route.get("checkpoint"), dict) else {}
            sequence_match = route.get("sequence_match") if isinstance(route.get("sequence_match"), dict) else {}
            store_query = route.get("store_query") if isinstance(route.get("store_query"), dict) else {}
            valid_refs = {"current_message", *[item["message_ref"] for item in case["history"]]}
            return {
                "case_id": case_id,
                "source": {
                    "kind": "real_conversation_read_only_anonymized",
                    "message_count": case["message_count"],
                    "latest_request_at": case["identity"].get("latest_at"),
                },
                "conversation": case["history"],
                "current_message": case["current"],
                "expected_annotation": _expected_annotation(case.get("expected_annotation")),
                "actual": {
                    "classification_status": route.get("classification_status"),
                    "primary_checkpoint": checkpoint.get("primary_code"),
                    "secondary_checkpoint": checkpoint.get("secondary_code"),
                    "evidence_refs": checkpoint.get("evidence_refs") or [],
                    "sequence_ids": sequence_match.get("sequence_ids") or [],
                    "alternative_sequence_ids": sequence_match.get("alternative_sequence_ids") or [],
                    "excluded_sequence_ids": sequence_match.get("excluded_sequence_ids") or [],
                    "relevant_step_ids": sequence_match.get("relevant_step_ids") or [],
                    "script_queries": route.get("script_queries") or [],
                    "store_query": store_query,
                    "sequences": _selected_sequence_details(result),
                    "scripts": _script_details(result),
                    "raw_normalized_route": route,
                },
                "runtime": {
                    "status": result.get("status"),
                    "duration_ms": duration_ms,
                    "execution_wall_ms": execution_ms,
                    "queue_wait_ms": queue_wait_ms,
                    "timings": result.get("timings") or {},
                    "route_duration_ms": route.get("duration_ms"),
                    "model_usage": route.get("model_usage") or {},
                    "selector_used": ((result.get("knowledge_evidence") or {}).get("selector") or {}).get("status") == "ok",
                    "script_option_count": (result.get("knowledge_evidence") or {}).get("script_option_count", 0),
                    "script_candidate_count": (result.get("knowledge_evidence") or {}).get("candidate_count", 0),
                    "structural_issues": _structural_issues(result, valid_refs),
                },
            }

        results = await asyncio.gather(*(run_case(index, case) for index, case in enumerate(cases, start=1)))
        for item in results:
            item["evaluation"] = _evaluate_expected_annotation(item)
        durations = sorted(item["runtime"]["duration_ms"] for item in results)
        queue_waits = sorted(item["runtime"]["queue_wait_ms"] for item in results)
        failures = [item for item in results if item["runtime"]["status"] != "ok"]
        structural = [item for item in results if item["runtime"]["structural_issues"]]
        selector_count = sum(1 for item in results if item["runtime"]["selector_used"])
        scored = [item for item in results if item["evaluation"].get("scored")]
        def rate(field: str, predicate: Any = None) -> float | None:
            eligible = [item for item in scored if predicate is None or predicate(item)]
            values = [bool(item["evaluation"].get(field)) for item in eligible]
            return round(sum(values) / len(values), 4) if values else None

        sequence_scored = [item for item in scored if item["expected_annotation"].get("sequence_required")]
        step_action_scored = [
            item
            for item in scored
            if item["expected_annotation"].get("acceptable_step_ids")
            or item["expected_annotation"].get("acceptable_action_codes")
        ]
        no_sequence_scored = [item for item in scored if item["expected_annotation"].get("forbid_sequence")]
        report = {
            "schema_version": "v3_deepseek_real_router_evaluation_v1",
            "created_at": datetime.now(TZ).isoformat(),
            "git_commit": _git_commit(),
            "evaluation_status": "technical_review" if scored else "preliminary_pending_business_labels",
            "scope": {
                "hours": args.hours,
                "requested_customers": args.max_customers,
                "evaluated_customers": len(results),
                "conversation_limit": args.conversation_limit,
                "deepseek_only": True,
                "reply_executed": False,
                "customer_state_written": False,
                "messages_sent": False,
                "snapshot_replay": snapshot_path is not None,
            },
            "knowledge": {
                "sequence_total": sequence_index.get("total", 0),
                "source": sequence_index.get("source"),
            },
            "summary": {
                "supplier_failures": len(failures),
                "structural_issue_cases": len(structural),
                "invalid_or_fabricated_ids": sum(
                    1 for item in results for issue in item["runtime"]["structural_issues"] if "invalid_" in issue
                ),
                "selector_runs": selector_count,
                "selector_rate": round(selector_count / len(results), 4),
                "p50_ms": round(statistics.median(durations), 1),
                "p90_ms": durations[min(len(durations) - 1, max(0, math.ceil(len(durations) * 0.9) - 1))],
                "queue_wait_p50_ms": round(statistics.median(queue_waits), 1),
                "queue_wait_p90_ms": queue_waits[
                    min(len(queue_waits) - 1, max(0, math.ceil(len(queue_waits) * 0.9) - 1))
                ],
                "scored_cases": len(scored),
                "checkpoint_accuracy": rate("checkpoint_pass"),
                "sequence_scored_cases": len(sequence_scored),
                "sequence_top3_recall": rate(
                    "sequence_pass",
                    lambda item: item["expected_annotation"].get("sequence_required"),
                ),
                "step_action_scored_cases": len(step_action_scored),
                "step_action_accuracy": rate(
                    "step_action_pass",
                    lambda item: bool(
                        item["expected_annotation"].get("acceptable_step_ids")
                        or item["expected_annotation"].get("acceptable_action_codes")
                    ),
                ),
                "no_sequence_scored_cases": len(no_sequence_scored),
                "false_nomination_pass_rate": rate(
                    "sequence_pass",
                    lambda item: item["expected_annotation"].get("forbid_sequence"),
                ),
                "store_route_accuracy": rate("store_pass"),
                "forbidden_candidate_pass_rate": rate("forbidden_pass"),
                "live_step_timing_pass_rate": rate("live_timing_pass"),
                "overall_route_pass_rate": rate("overall_pass"),
                "accuracy_note": (
                    "Technical labels are assistant-reviewed and still require business approval."
                    if scored
                    else "Expected labels are pending business review; accuracy metrics are intentionally not claimed."
                ),
            },
            "cases": results,
        }
        actual_models = sorted(
            {
                str((item["runtime"].get("model_usage") or {}).get("model") or "").strip()
                for item in results
                if str((item["runtime"].get("model_usage") or {}).get("model") or "").strip()
            }
        )
        for model in actual_models:
            _assert_deepseek_model(model)
        if args.router_model not in actual_models:
            raise RuntimeError(f"Router model usage was not recorded: {args.router_model}")
        report["model_audit"] = {
            "required_model": args.router_model,
            "actual_models": actual_models,
            "fallback_client_configured": False,
            "passed": True,
        }
        (output_dir / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "report.md").write_text(_markdown(report), encoding="utf-8")
        return output_dir
    finally:
        await outreach.aclose()
        await knowledge.aclose()
        await deepseek.aclose()


def _markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]

    def ratio(value: Any) -> str:
        return "无适用样本" if value is None else f"{float(value):.1%}"

    lines = [
        "# V3 DeepSeek 真实聊天路由专项",
        "",
        "> 本报告使用真实聊天的脱敏只读回放。技术预期标签由 AI 初审，业务确认前不作为最终准确率。",
        "",
        "## 范围与结果",
        "",
        f"- 评估客户：{report['scope']['evaluated_customers']} / {report['scope']['requested_customers']}",
        f"- 真实序列：{report['knowledge']['sequence_total']} 条",
        f"- DeepSeek 供应商失败：{summary['supplier_failures']}",
        f"- 结构问题样本：{summary['structural_issue_cases']}",
        f"- 第二次话术精选：{summary['selector_runs']} 次（{summary['selector_rate']:.1%}）",
        f"- P50 / P90：{summary['p50_ms'] / 1000:.2f}s / {summary['p90_ms'] / 1000:.2f}s",
        "",
        "## 逐条预览",
        "",
        "| Case | 当前消息 | 主/次卡点 | 序列 | 步骤/动作 | 话术 | 门店工具 | 状态 |",
        "|---|---|---|---|---|---:|---|---|",
    ]
    if summary.get("scored_cases"):
        lines[lines.index("## 逐条预览") : lines.index("## 逐条预览")] = [
            f"- 已标注样本：{summary['scored_cases']}",
            f"- 卡点准确率：{ratio(summary['checkpoint_accuracy'])}",
            f"- Sequence Top-3 Recall：{ratio(summary['sequence_top3_recall'])}（{summary['sequence_scored_cases']} 条应召回样本）",
            f"- Step/Action 命中率：{ratio(summary['step_action_accuracy'])}（{summary['step_action_scored_cases']} 条动作样本）",
            f"- 无需序列时零误提名率：{ratio(summary['false_nomination_pass_rate'])}（{summary['no_sequence_scored_cases']} 条）",
            f"- 门店路由准确率：{ratio(summary['store_route_accuracy'])}",
            f"- 禁止候选通过率：{ratio(summary['forbidden_candidate_pass_rate'])}",
            f"- 实时步骤时机通过率：{ratio(summary['live_step_timing_pass_rate'])}",
            f"- 综合路由通过率：{ratio(summary['overall_route_pass_rate'])}",
            "",
        ]
    for item in report["cases"]:
        actual = item["actual"]
        scripts = actual["scripts"]
        actions = [f"{row.get('step_id')}:{row.get('action_code')}" for row in actual["script_queries"]]
        current = item["current_message"]["content"].replace("|", "\\|").replace("\n", " ")[:80]
        checkpoint = "/".join(filter(None, [actual["primary_checkpoint"], actual["secondary_checkpoint"]])) or "none"
        lines.append(
            f"| {item['case_id']} | {current} | {checkpoint} | {','.join(actual['sequence_ids']) or '-'} | "
            f"{','.join(actions) or '-'} | {len(scripts)} | {actual['store_query'].get('required', False)} | "
            f"{item['runtime']['status']} |"
        )
    lines.extend(
        [
            "",
            "## 审核说明",
            "",
            "`result.json` 已预留主/次卡点、可接受序列、步骤、动作和门店工具预期字段。完成业务审核后，才能计算正式准确率。",
        ]
    )
    return "\n".join(lines) + "\n"


def _expected_annotation(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    return {
        "status": _text(raw.get("status")) or "pending_business_review",
        "primary_checkpoint": _text(raw.get("primary_checkpoint")),
        "acceptable_primary_checkpoints": [
            _text(item) for item in raw.get("acceptable_primary_checkpoints") or [] if _text(item)
        ],
        "acceptable_sequence_ids": [
            _text(item) for item in raw.get("acceptable_sequence_ids") or [] if _text(item)
        ],
        "acceptable_step_ids": [
            _text(item) for item in raw.get("acceptable_step_ids") or [] if _text(item)
        ],
        "acceptable_action_codes": [
            _text(item) for item in raw.get("acceptable_action_codes") or [] if _text(item)
        ],
        "forbidden_sequence_ids": [
            _text(item) for item in raw.get("forbidden_sequence_ids") or [] if _text(item)
        ],
        "sequence_required": bool(raw.get("sequence_required")),
        "forbid_sequence": bool(raw.get("forbid_sequence")),
        "store_query_required": raw.get("store_query_required"),
        "review_notes": _text(raw.get("review_notes")),
    }


def _evaluate_expected_annotation(item: dict[str, Any]) -> dict[str, Any]:
    expected = item.get("expected_annotation") if isinstance(item.get("expected_annotation"), dict) else {}
    if expected.get("status") not in {"technical_reviewed", "approved"}:
        return {"scored": False}
    actual = item.get("actual") if isinstance(item.get("actual"), dict) else {}
    acceptable_checkpoints = set(expected.get("acceptable_primary_checkpoints") or [])
    acceptable_checkpoints.add(str(expected.get("primary_checkpoint") or ""))
    actual_checkpoint = str(actual.get("primary_checkpoint") or "")
    checkpoint_pass = actual_checkpoint in acceptable_checkpoints

    actual_sequences = {str(value) for value in actual.get("sequence_ids") or []}
    acceptable_sequences = {str(value) for value in expected.get("acceptable_sequence_ids") or []}
    forbidden_sequences = {str(value) for value in expected.get("forbidden_sequence_ids") or []}
    if expected.get("forbid_sequence"):
        sequence_pass = not actual_sequences
    elif expected.get("sequence_required"):
        sequence_pass = bool(actual_sequences & acceptable_sequences)
    else:
        sequence_pass = True
    forbidden_pass = not bool(actual_sequences & forbidden_sequences)

    actual_steps = {str(value) for value in actual.get("relevant_step_ids") or []}
    actual_actions = {
        str(value.get("action_code") or "")
        for value in actual.get("script_queries") or []
        if isinstance(value, dict) and str(value.get("action_code") or "")
    }
    acceptable_steps = {str(value) for value in expected.get("acceptable_step_ids") or []}
    acceptable_actions = {str(value) for value in expected.get("acceptable_action_codes") or []}
    step_action_checks = []
    if acceptable_steps:
        step_action_checks.append(bool(actual_steps & acceptable_steps))
    if acceptable_actions:
        step_action_checks.append(bool(actual_actions & acceptable_actions))
    step_action_pass = all(step_action_checks) if step_action_checks else True

    expected_store = expected.get("store_query_required")
    store_pass = (
        True
        if expected_store is None
        else bool((actual.get("store_query") or {}).get("required")) is bool(expected_store)
    )
    future_steps = [
        str(step.get("step_id") or "")
        for sequence in actual.get("sequences") or []
        if isinstance(sequence, dict)
        for step in sequence.get("steps") or []
        if isinstance(step, dict)
        and (
            (str(step.get("trigger_base") or "") == "last_reply" and int(step.get("relative_value") or 0) > 0)
            or str(step.get("trigger_base") or "") == "add_wecom_day"
        )
    ]
    live_timing_pass = not future_steps
    structural_pass = not bool((item.get("runtime") or {}).get("structural_issues"))
    overall = all(
        [
            checkpoint_pass,
            sequence_pass,
            step_action_pass,
            store_pass,
            forbidden_pass,
            live_timing_pass,
            structural_pass,
        ]
    )
    return {
        "scored": True,
        "checkpoint_pass": checkpoint_pass,
        "sequence_pass": sequence_pass,
        "step_action_pass": step_action_pass,
        "store_pass": store_pass,
        "forbidden_pass": forbidden_pass,
        "live_timing_pass": live_timing_pass,
        "structural_pass": structural_pass,
        "future_silence_step_ids": future_steps,
        "overall_pass": overall,
    }


def _git_commit() -> str:
    configured = str(os.getenv("AI_PATHS_BUILD_GIT_COMMIT") or "").strip()
    if configured:
        return configured
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _router_test_settings(settings: Settings, *, router_model: str) -> Settings:
    _assert_deepseek_model(router_model)
    return settings.model_copy(
        update={
            "deepseek_semantic_model": router_model,
            "deepseek_semantic_timeout_seconds": 20.0,
            "service_rule_data_enabled": False,
        }
    )


def _assert_deepseek_model(model: str) -> None:
    if not str(model or "").strip().lower().startswith("deepseek-"):
        raise RuntimeError(f"DeepSeek-only router test rejected model: {model}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the V3 DeepSeek router on anonymized real conversations.")
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--max-customers", type=int, default=30)
    parser.add_argument("--conversation-limit", type=int, default=50)
    parser.add_argument("--fetch-concurrency", type=int, default=4)
    parser.add_argument("--model-concurrency", type=int, default=2)
    parser.add_argument("--router-model", default="deepseek-v4-flash")
    parser.add_argument("--db-path", default="")
    parser.add_argument("--snapshot", default="", help="Existing anonymized result.json to replay.")
    parser.add_argument("--case-id", default="", help="Comma-separated anonymized case IDs to replay.")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    print(asyncio.run(_evaluate(args)))


if __name__ == "__main__":
    main()
