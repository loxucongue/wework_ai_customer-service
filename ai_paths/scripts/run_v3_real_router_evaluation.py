from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sqlite3
import statistics
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.config import get_settings
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
    for key in ("role", "direction", "sender_type", "message_role", "from_type"):
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
    customer_indexes = [index for index, item in enumerate(conversation) if item.get("role") == "customer"]
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
    store_query = route.get("store_query") if isinstance(route.get("store_query"), dict) else {}
    refs = [*checkpoint.get("evidence_refs", []), *store_query.get("location_evidence_refs", [])]
    invalid_refs = sorted({_text(ref) for ref in refs if _text(ref) and _text(ref) not in valid_refs})
    if invalid_refs:
        issues.append(f"invalid_message_refs:{','.join(invalid_refs)}")
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


async def _evaluate(args: argparse.Namespace) -> Path:
    settings = get_settings()
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
    if not outreach.available:
        raise RuntimeError("Outreach conversation read-only API is not configured")
    if not knowledge.available:
        raise RuntimeError("Follow knowledge API is not configured")
    if not deepseek.available:
        raise RuntimeError("DeepSeek official provider is not configured")
    try:
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
        if not cases:
            raise RuntimeError("No eligible real conversations were found")
        sequence_index = await knowledge.query_all_sequences()
        if sequence_index.get("status") != "ok":
            raise RuntimeError(f"Sequence API failed: {sequence_index.get('reason')}")

        semaphore = asyncio.Semaphore(max(1, args.model_concurrency))

        async def run_case(index: int, case: dict[str, Any]) -> dict[str, Any]:
            case_id = _masked_case_id(case["identity"], index)
            shared = _shared_context(case_id, case["history"], case["current"])
            started = time.perf_counter()
            async with semaphore:
                result = await router.route(shared_context=shared, sequence_result=sequence_index)
            duration_ms = int((time.perf_counter() - started) * 1000)
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
                "expected_annotation": {
                    "status": "pending_business_review",
                    "primary_checkpoint": "",
                    "secondary_checkpoint": "",
                    "acceptable_sequence_ids": [],
                    "acceptable_step_ids": [],
                    "acceptable_action_codes": [],
                    "forbidden_sequence_ids": [],
                    "store_query_required": None,
                    "review_notes": "",
                },
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
                    "route_duration_ms": route.get("duration_ms"),
                    "model_usage": route.get("model_usage") or {},
                    "selector_used": ((result.get("knowledge_evidence") or {}).get("selector") or {}).get("status") == "ok",
                    "script_option_count": (result.get("knowledge_evidence") or {}).get("script_option_count", 0),
                    "script_candidate_count": (result.get("knowledge_evidence") or {}).get("candidate_count", 0),
                    "structural_issues": _structural_issues(result, valid_refs),
                },
            }

        results = await asyncio.gather(*(run_case(index, case) for index, case in enumerate(cases, start=1)))
        durations = sorted(item["runtime"]["duration_ms"] for item in results)
        failures = [item for item in results if item["runtime"]["status"] != "ok"]
        structural = [item for item in results if item["runtime"]["structural_issues"]]
        selector_count = sum(1 for item in results if item["runtime"]["selector_used"])
        report = {
            "schema_version": "v3_deepseek_real_router_evaluation_v1",
            "created_at": datetime.now(TZ).isoformat(),
            "git_commit": _git_commit(),
            "evaluation_status": "preliminary_pending_business_labels",
            "scope": {
                "hours": args.hours,
                "requested_customers": args.max_customers,
                "evaluated_customers": len(results),
                "conversation_limit": args.conversation_limit,
                "deepseek_only": True,
                "reply_executed": False,
                "customer_state_written": False,
                "messages_sent": False,
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
                "p90_ms": durations[min(len(durations) - 1, max(0, int(len(durations) * 0.9) - 1))],
                "accuracy_note": "Expected labels are pending business review; accuracy metrics are intentionally not claimed.",
            },
            "cases": results,
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
    lines = [
        "# V3 DeepSeek 真实聊天路由专项",
        "",
        "> 本报告使用真实聊天的脱敏只读回放。人工预期标签尚未审核，因此不把当前结果称为最终准确率。",
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


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the V3 DeepSeek router on anonymized real conversations.")
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--max-customers", type=int, default=30)
    parser.add_argument("--conversation-limit", type=int, default=50)
    parser.add_argument("--fetch-concurrency", type=int, default=4)
    parser.add_argument("--model-concurrency", type=int, default=2)
    parser.add_argument("--db-path", default="")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    print(asyncio.run(_evaluate(args)))


if __name__ == "__main__":
    main()
