"""Run a write-free, two-phase DeepSeek evaluation of the V3 reply graph.

The runtime phase must finish before the judge phase starts.  This prevents a
large DeepSeek-Reasoner judging request from competing with the Router/Reply
requests under test.  Only redacted outputs remain after ``--mode all``.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import math
import os
import random
import re
import shutil
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


ROOT = Path(os.environ.get("EVAL_CANDIDATE_ROOT", Path(__file__).resolve().parents[2]))
AI_PATHS_ROOT = ROOT / "ai_paths"
if str(AI_PATHS_ROOT) not in sys.path:
    sys.path.insert(0, str(AI_PATHS_ROOT))

from app.config import Settings  # noqa: E402
from app.chat_runtime import ChatRuntime  # noqa: E402
from app.graph.graph_builder import build_reply_graphs  # noqa: E402
from app.schemas import ChatRequest  # noqa: E402
from app.services.ai_sales_policy_service import AiSalesPolicyService  # noqa: E402
from app.services.coze_client import CozeClient  # noqa: E402
from app.services.customer_context import CustomerContextService  # noqa: E402
from app.services.customer_scope import customer_scope_from_state  # noqa: E402
from app.services.customer_store_knowledge import CustomerStoreKnowledgeService  # noqa: E402
from app.services.deepseek_semantic_client import DeepSeekSemanticClient  # noqa: E402
from app.services.follow_knowledge_client import FollowKnowledgeClient  # noqa: E402
from app.services.model_client import ModelClient  # noqa: E402
from app.services.memory_store import CustomerMemoryStore  # noqa: E402
from app.services.outreach_system_client import OutreachSystemClient  # noqa: E402
from app.services.platform_agent_client import PlatformAgentClient  # noqa: E402
from app.services.runtime_budget import build_runtime_budget  # noqa: E402
from app.services.sales_strategy_service import SalesStrategyService  # noqa: E402
from app.services.store_service import StoreService  # noqa: E402
from app.services.store_snapshot_service import StoreSnapshotService  # noqa: E402
from app.services.trace_logger import TraceLogger  # noqa: E402
from app.services.v3_semantic_router_service import V3SemanticRouterService  # noqa: E402
from app.services.v3_sop_execution_service import is_platform_auto_opening_message  # noqa: E402
from app.services.storage import AppRepository, SQLiteStore, build_store  # noqa: E402
from app.services.storage.serialization import loads_dict  # noqa: E402
from app.services.workflow_compat import workflow_response_from_chat  # noqa: E402
from scripts.v3_lifecycle_eval.protocol import (  # noqa: E402
    customer_visible_text,
    expected_contract,
    hard_assertions,
    merge_ai_judge,
    normalize_visible_messages,
    prior_delivery_events,
    prior_structured_summary,
    render_visible_messages,
)


RUNS_ROOT = Path(os.environ.get("EVAL_RUNS_ROOT", "/opt/ai-paths/logs/runs"))
SEED = 20260904
INTENTS = {
    "fact_inquiry", "blocker_expression", "transaction_progress",
    "information_submission", "defer", "explicit_exit", "normal_exchange",
}
EMOTIONS = {
    "neutral", "curious", "enthusiastic", "hesitant",
    "cold", "defensive", "impatient", "angry",
}
AUTO_MESSAGES = {"[消息已撤回]", "【消息已撤回】", "你已添加了我，现在可以开始聊天了。"}
BUCKET_QUOTAS = {
    "explicit_exit": 15, "complaint": 15, "profanity": 15, "defer": 15,
    "store": 60, "transaction": 50, "price": 30, "trust_effect": 30,
    "time_family": 30, "health": 30, "general": 110,
}
VALID_REPLY_SOURCES = {"main_model", "single_targeted_repair_model", "single_full_task_retry_model"}
NON_MODEL_TERMINAL_SOURCES = {
    "human_takeover_guard",
    "ignored_platform_auto_message",
    "platform_recalled_message",
    "platform_superseded",
}


def customer_reply_generated(row: dict[str, Any], reply: Any = None) -> bool:
    source = _text(row.get("reply_source"))
    if reply is None and "reply_excerpt" not in row:
        visible = source in VALID_REPLY_SOURCES
    else:
        visible = bool(_text(reply if reply is not None else row.get("reply_excerpt")))
    return bool(visible and source not in {"reply_failed", "exception"})


class WriteBlockedError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("runtime", "judge", "all"), default="all")
    parser.add_argument("--limit", type=int, default=120)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--runtime-gap-seconds", type=float, default=1.0)
    parser.add_argument("--judge-gap-seconds", type=float, default=1.0)
    parser.add_argument("--cooldown-seconds", type=float, default=30.0)
    parser.add_argument(
        "--request-id",
        action="append",
        default=[],
        help="Only replay these source request ids. May be repeated.",
    )
    parser.add_argument(
        "--source-memory-mode",
        choices=("runs-only", "production-readonly"),
        default="runs-only",
        help="Optionally read point-in-time history events from the configured source database.",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def _text(value: Any) -> str:
    return str(value or "").strip()


def redact(value: Any, limit: int = 120) -> str:
    text = _text(value).replace("\r", " ").replace("\n", " ")
    text = re.sub(r"https?://\S+", "[链接]", text)
    text = re.sub(r"(?<!\d)1\d{10}(?!\d)", "[手机号]", text)
    text = re.sub(r"\b\d{15,20}\b", "[长编号]", text)
    return re.sub(r"\s+", " ", text)[:limit]


def identity_hash(row: dict[str, Any]) -> str:
    raw = "|".join(_text(row.get(key)) for key in ("corp_id", "wechat", "external_userid", "customer_id"))
    return hashlib.sha256(("v3-deepseek-eval|" + raw).encode()).hexdigest()[:12]


def sample_bucket(content: str) -> str:
    """Stratify evaluation only; this function is never used by Reply."""

    compact = re.sub(r"\s+", "", content)
    groups = (
        ("explicit_exit", ("别联系", "不要联系", "别发了", "不要再发", "取消接收", "不再打扰")),
        ("complaint", ("投诉", "负责人", "骗", "垃圾服务", "态度太差", "答非所问", "坑人")),
        ("profanity", ("他妈", "卧槽", "我靠", "傻逼", "滚", "妈的", "牛逼")),
        ("defer", ("晚点", "改天", "考虑一下", "以后再说", "暂时不", "现在忙", "上班", "开车", "没空")),
        (
            "store",
            (
                "门店", "地址", "附近", "离我", "多远", "哪个店", "怎么走", "定位", "地铁",
                "停车", "停车场", "营业时间", "几点开门", "几点关门", "几楼", "楼层", "导航",
            ),
        ),
        ("transaction", ("预约金", "怎么付", "付款", "支付", "报名", "预约", "下单", "转账", "缴费")),
        ("price", ("多少钱", "价格", "太贵", "便宜", "费用", "收费", "优惠")),
        ("trust_effect", ("效果", "没用", "不信", "假的", "骗人", "案例", "反弹", "靠谱吗")),
        ("time_family", ("没时间", "加班", "家里", "家人", "老公", "老婆", "父母", "商量", "再等等")),
        ("health", ("过敏", "敏感", "孕", "哺乳", "皮炎", "伤口", "疼", "副作用", "医院", "医生")),
    )
    for name, markers in groups:
        if any(marker in compact for marker in markers):
            return name
    return "general"


def load_candidates(days: int) -> list[dict[str, Any]]:
    cutoff = time.time() - max(1, days) * 86400
    files = sorted(
        (path for path in RUNS_ROOT.glob("*.json") if path.stat().st_mtime >= cutoff),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in files:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        reply_control = raw.get("reply_control") if isinstance(raw.get("reply_control"), dict) else {}
        merged_customer_messages = [
            _text(item)
            for item in reply_control.get("merged_customer_messages") or []
            if _text(item)
        ]
        content = (
            "\n".join(merged_customer_messages)
            if str(reply_control.get("mode") or "") == "merged_latest" and merged_customer_messages
            else _text(raw.get("content"))
        )
        if (
            not content
            or content in AUTO_MESSAGES
            or content.startswith("你已添加了")
            or is_platform_auto_opening_message(content)
        ):
            continue
        if raw.get("file_image") or raw.get("image_urls"):
            continue
        if any(not _text(raw.get(key)) for key in ("corp_id", "wechat", "external_userid", "customer_id")):
            continue
        context = raw.get("request_context") if isinstance(raw.get("request_context"), dict) else {}
        dedupe = _text(context.get("msgid")) or hashlib.sha256((identity_hash(raw) + content).encode()).hexdigest()
        if dedupe in seen:
            continue
        seen.add(dedupe)
        rows.append(
            {
                "source_path": str(path), "source_mtime": path.stat().st_mtime,
                "source_request_id": _text(raw.get("request_id") or path.stem),
                "source_reply_source": _text(raw.get("reply_source")), "content": content,
                "source_reply_messages": normalize_visible_messages(raw.get("reply_messages") or []),
                "conversation_history": [_text(item) for item in raw.get("conversation_history") or [] if _text(item)][-20:],
                "corp_id": raw.get("corp_id"), "wechat": raw.get("wechat"),
                "external_userid": raw.get("external_userid"), "customer_id": raw.get("customer_id"),
                "user_id": raw.get("user_id"), "customer_add_wechat_id": raw.get("customer_add_wechat_id"),
                "confirmed_store_id": raw.get("confirmed_store_id"), "confirmed_store_name": raw.get("confirmed_store_name"),
                "store_id": raw.get("store_id"), "store_name": raw.get("store_name"),
                "appointment_id": raw.get("appointment_id"), "appointment_time": raw.get("appointment_time"),
                "request_context": context, "bucket": sample_bucket(content), "identity_hash": identity_hash(raw),
            }
        )
    timelines: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        timelines[row["identity_hash"]].append(row)
    for timeline in timelines.values():
        timeline.sort(key=lambda item: float(item.get("source_mtime") or 0))
        delivered: list[dict[str, Any]] = []
        for row in timeline:
            row["prior_deliveries"] = list(delivered[-20:])
            if row.get("source_reply_source") in VALID_REPLY_SOURCES and row.get("source_reply_messages"):
                delivered.append(
                    {
                        "request_id": row.get("source_request_id"),
                        "occurred_at": datetime.fromtimestamp(
                            float(row.get("source_mtime") or 0), timezone.utc
                        ).isoformat(),
                        "reply_messages": row.get("source_reply_messages"),
                    }
                )
    refresh_state_tags(rows)
    return rows


def refresh_state_tags(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        prior_deliveries = _prior_deliveries_with_source_memory(row)
        contract = expected_contract(
            sample=row,
            facts={},
            prior_deliveries=prior_deliveries,
        )
        tags: list[str] = []
        if contract.get("prior_store_card_ids"):
            tags.append("prior_store_card")
        if contract.get("current_is_store_detail_question"):
            tags.append("store_detail")
        if contract.get("current_requests_address_or_navigation"):
            tags.append("store_address_request")
        if contract.get("prior_store_card_ids") and contract.get("current_requests_address_or_navigation"):
            tags.append("store_address_after_card")
        if contract.get("prior_store_card_ids") and contract.get("current_is_store_detail_question"):
            tags.append("store_detail_after_card")
        if row.get("appointment_id") or row.get("appointment_time"):
            tags.append("appointment_present")
        row["state_tags"] = tags


def choose_samples(candidates: list[dict[str, Any]], limit: int) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rng = random.Random(SEED)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in candidates:
        groups[row["bucket"]].append(row)
    for rows in groups.values():
        rng.shuffle(rows)
    selected: list[dict[str, Any]] = []
    used: set[str] = set()
    per_identity: Counter[str] = Counter()

    def take(bucket: str, count: int) -> None:
        current = sum(row.get("bucket") == bucket for row in selected)
        if current >= count:
            return
        for row in groups.get(bucket, []):
            if row["source_path"] in used or per_identity[row["identity_hash"]] >= 3:
                continue
            selected.append(row)
            used.add(row["source_path"])
            per_identity[row["identity_hash"]] += 1
            current += 1
            if current >= count:
                return

    def take_state(tag: str, count: int) -> None:
        current = 0
        tagged = [row for row in candidates if tag in (row.get("state_tags") or [])]
        rng.shuffle(tagged)
        for row in tagged:
            if row["source_path"] in used or per_identity[row["identity_hash"]] >= 3:
                continue
            selected.append(row)
            used.add(row["source_path"])
            per_identity[row["identity_hash"]] += 1
            current += 1
            if current >= count:
                return

    scale = min(1.0, limit / 400.0)
    for tag, quota in (
        ("store_detail_after_card", 24),
        ("store_address_after_card", 16),
        ("appointment_present", 12),
    ):
        take_state(tag, max(1, round(quota * scale)))
    for bucket, quota in BUCKET_QUOTAS.items():
        take(bucket, max(1, round(quota * scale)))
    remainder = [row for row in candidates if row["source_path"] not in used]
    rng.shuffle(remainder)
    for row in remainder:
        if len(selected) >= limit:
            break
        if per_identity[row["identity_hash"]] >= 3:
            continue
        selected.append(row)
        per_identity[row["identity_hash"]] += 1
    selected = selected[:limit]
    # Interleave strata. Candidate-rich sales cases must not always run after
    # long reasoner-heavy or store-heavy stretches.
    rng.shuffle(selected)
    return selected, dict(Counter(row["bucket"] for row in selected))


def load_source_history_events(
    samples: list[dict[str, Any]],
    audit: dict[str, Any],
) -> None:
    """Read point-in-time memory events without ever initializing or mutating the source."""

    source_settings = Settings()
    source_store = build_store(source_settings)
    keys = list(
        dict.fromkeys(
            customer_scope_from_state(sample).sales_contact_key
            for sample in samples
            if customer_scope_from_state(sample).sales_contact_key
        )
    )
    by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    try:
        with source_store.connect() as conn:
            for offset in range(0, len(keys), 200):
                chunk = keys[offset : offset + 200]
                placeholders = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    f"""
                    SELECT id, customer_id, event_type, stage, summary, facts,
                           impact, confidence, created_at
                    FROM history_events
                    WHERE customer_id IN ({placeholders})
                    ORDER BY customer_id, created_at DESC
                    """,
                    chunk,
                ).fetchall()
                audit["source_read_queries"] += 1
                for row in rows:
                    key = str(row["customer_id"] or "")
                    if len(by_key[key]) >= 200:
                        continue
                    by_key[key].append(
                        {
                            "event_id": str(row["id"] or ""),
                            "event_type": str(row["event_type"] or ""),
                            "stage": str(row["stage"] or ""),
                            "summary": str(row["summary"] or ""),
                            "facts": loads_dict(row["facts"]),
                            "impact": str(row["impact"] or ""),
                            "confidence": float(row["confidence"] or 0),
                            "event_time": str(row["created_at"] or ""),
                        }
                    )
    finally:
        source_store.close()
    for sample in samples:
        key = customer_scope_from_state(sample).sales_contact_key
        source_request_id = str(sample.get("source_request_id") or "")
        cutoff = float(sample.get("source_mtime") or 0)
        eligible: list[dict[str, Any]] = []
        for event in reversed(by_key.get(key, [])):
            facts = event.get("facts") if isinstance(event.get("facts"), dict) else {}
            if source_request_id and str(facts.get("request_id") or "") == source_request_id:
                continue
            event_time = str(event.get("event_time") or "")
            try:
                timestamp = datetime.fromisoformat(event_time.replace("Z", "+00:00")).timestamp()
            except ValueError:
                timestamp = 0
            if timestamp and cutoff and timestamp > cutoff:
                continue
            eligible.append(event)
        sample["source_history_events"] = eligible[-100:]
    audit["source_memory_mode"] = "production-readonly"


def verify_source_absence(request_ids: list[str], audit: dict[str, Any]) -> None:
    """Prove evaluation request ids never reached production persistence."""

    clean_ids = list(dict.fromkeys(str(item or "").strip() for item in request_ids if str(item or "").strip()))
    counts = {"runs": 0, "v3_strategy_usage_events": 0, "message_dispatches": 0, "strategy_data_outbox": 0}
    if not clean_ids:
        audit["production_absence"] = counts
        return
    source_store = build_store(Settings())

    def count_value(row: Any) -> int:
        if isinstance(row, dict):
            return int(next(iter(row.values()), 0) or 0)
        try:
            return int(row[0] or 0)
        except (IndexError, KeyError, TypeError):
            return 0

    try:
        with source_store.connect() as conn:
            for offset in range(0, len(clean_ids), 100):
                chunk = clean_ids[offset : offset + 100]
                placeholders = ",".join("?" for _ in chunk)
                counts["runs"] += count_value(
                    conn.execute(
                        f"SELECT COUNT(*) FROM runs WHERE request_id IN ({placeholders})",
                        chunk,
                    ).fetchone()
                )
                counts["v3_strategy_usage_events"] += count_value(
                    conn.execute(
                        f"SELECT COUNT(*) FROM v3_strategy_usage_events WHERE request_id IN ({placeholders})",
                        chunk,
                    ).fetchone()
                )
                counts["message_dispatches"] += count_value(
                    conn.execute(
                        f"SELECT COUNT(*) FROM message_dispatches WHERE source_request_id IN ({placeholders})",
                        chunk,
                    ).fetchone()
                )
                like_clause = " OR ".join("payload_json LIKE ?" for _ in chunk)
                counts["strategy_data_outbox"] += count_value(
                    conn.execute(
                        f"SELECT COUNT(*) FROM strategy_data_outbox WHERE {like_clause}",
                        [f"%{item}%" for item in chunk],
                    ).fetchone()
                )
                audit["source_read_queries"] += 4
    finally:
        source_store.close()
    audit["production_absence"] = counts
    if any(counts.values()):
        raise RuntimeError("evaluation request ids unexpectedly found in production: " + json.dumps(counts))


def _prior_deliveries_with_source_memory(sample: dict[str, Any]) -> list[dict[str, Any]]:
    deliveries = list(sample.get("prior_deliveries") or [])
    known_store_ids = {
        store_id
        for delivery in deliveries
        for store_id in prior_structured_summary([delivery]).get("store_card_ids") or []
    }
    for event in sample.get("source_history_events") or []:
        if not isinstance(event, dict) or str(event.get("event_type") or "") != "store_address_sent":
            continue
        facts = event.get("facts") if isinstance(event.get("facts"), dict) else {}
        store_id = str(facts.get("store_id") or "").strip()
        if not store_id or store_id in known_store_ids:
            continue
        known_store_ids.add(store_id)
        deliveries.append(
            {
                "request_id": facts.get("request_id") or event.get("event_id"),
                "occurred_at": event.get("event_time"),
                "reply_messages": [
                    {"type": "store_address", "order": 1, "content": {"store_id": store_id}}
                ],
            }
        )
    return deliveries[-20:]


def build_settings(output_dir: Path) -> Settings:
    return Settings().model_copy(
        update={
            "service_role": "reply", "background_workers_enabled": False,
            "trace_log_dir": output_dir / "trace",
            "sop_platform_pull_enabled": False, "service_rule_data_enabled": False,
            "aics_storage_backend": "sqlite", "db_path": output_dir / "ephemeral_state.db",
            "memory_dir": output_dir / "ephemeral_memory",
            "store_snapshot_path": output_dir / "ephemeral_store_snapshot.json",
            "ai_sales_policy_enabled": True, "closing_catalog_source": "external_then_local",
            "model_provider": "relay", "model_fast": "deepseek-chat", "model_planner": "deepseek-chat",
            "model_balanced": "deepseek-chat", "model_strong": "deepseek-chat",
            "model_reply": "deepseek-chat", "model_store_destination": "deepseek-chat",
            "model_fast_fallbacks": "", "model_planner_fallbacks": "", "model_balanced_fallbacks": "",
            "model_strong_fallbacks": "", "model_reply_fallbacks": "",
            "model_store_destination_fallbacks": "", "model_emergency_fallbacks": "",
            "model_secondary_provider": "", "model_secondary": "", "model_hedge_max_parallel": 1,
            "model_request_retry_attempts": 2, "model_timeout_seconds": 70,
            "model_reply_total_timeout_seconds": 70.0, "model_store_destination_total_timeout_seconds": 45.0,
            "model_round_timeout_seconds": 150.0, "deepseek_semantic_model": "deepseek-v4-flash",
            "deepseek_semantic_timeout_seconds": 30.0, "deepseek_semantic_max_tokens": 1200,
        }
    )


def validate_evaluation_settings(settings: Settings) -> None:
    """Fail closed when a supposedly full-chain run lacks its real catalogs."""

    missing: list[str] = []
    if not settings.model_relay_api_key:
        missing.append("MODEL_RELAY_API_KEY")
    if not settings.deepseek_api_key:
        missing.append("DEEPSEEK_API_KEY")
    if settings.follow_knowledge_enabled and not settings.follow_knowledge_token:
        missing.append("FOLLOW_KNOWLEDGE_TOKEN")
    if missing:
        raise RuntimeError("evaluation required configuration missing: " + ",".join(missing))


def _block_write(name: str, audit: dict[str, Any]):
    def blocked(*_args: Any, **_kwargs: Any) -> Any:
        audit["blocked_attempts"].append(name)
        raise WriteBlockedError(f"production write blocked: {name}")

    return blocked


def build_runtime(settings: Settings, audit: dict[str, Any]) -> dict[str, Any]:
    """Build shared read/model clients; per-case graphs receive isolated stores."""

    coze_client = CozeClient(settings)
    model_client = ModelClient(settings)
    platform_client = PlatformAgentClient(settings)
    outreach_system_client = OutreachSystemClient(settings)
    blocked_platform_methods = (
        "prepay_order",
        "create_work_order",
        "modify_work_order",
        "create_order_plan",
        "change_plan_time",
        "cancel_plan",
        "add_customer_mobile",
    )
    installed = 0
    for name in blocked_platform_methods:
        if not hasattr(platform_client, name):
            continue
        setattr(platform_client, name, _block_write(f"platform_agent.{name}", audit))
        installed += 1
    audit["write_methods_installed"] = installed
    outreach_system_client.send = _block_write("outreach_system.send", audit)  # type: ignore[method-assign]
    audit["write_methods_installed"] += 1
    customer_context = CustomerContextService(platform_client)
    snapshot = StoreSnapshotService(settings, platform_client)
    store_knowledge = CustomerStoreKnowledgeService(platform_client, snapshot)
    follow_client = FollowKnowledgeClient(settings)
    semantic_client = DeepSeekSemanticClient(settings, None)
    semantic_router = V3SemanticRouterService(
        semantic_client=semantic_client, knowledge_client=follow_client,
        script_threshold=settings.deepseek_semantic_script_threshold,
        max_scripts=settings.deepseek_semantic_max_scripts,
    )
    policy = AiSalesPolicyService(settings)
    return {
        "model_client": model_client, "semantic_client": semantic_client,
        "follow_client": follow_client, "coze_client": coze_client,
        "platform_client": platform_client, "policy": policy,
        "outreach_system_client": outreach_system_client,
        "customer_context": customer_context,
        "store_knowledge": store_knowledge,
        "store_service": StoreService(platform_client),
        "semantic_router": semantic_router,
        "sales_strategy": SalesStrategyService(settings),
    }


class TimedGraph:
    """Capture the full graph state and timing without changing production code."""

    def __init__(self, graph: Any):
        self.graph = graph
        self.final_by_request: dict[str, dict[str, Any]] = {}
        self.timing_by_request: dict[str, dict[str, int]] = {}

    async def ainvoke(self, state: dict[str, Any]) -> dict[str, Any]:
        request_id = str(state.get("request_id") or "")
        started = time.perf_counter()
        final = await self.graph.ainvoke(state)
        finished = time.perf_counter()
        self.final_by_request[request_id] = final
        self.timing_by_request[request_id] = {
            "graph_started_ns": int(started * 1_000_000_000),
            "graph_finished_ns": int(finished * 1_000_000_000),
            "graph_duration_ms": int((finished - started) * 1000),
        }
        return final


def _case_settings(settings: Settings, case_dir: Path) -> Settings:
    return settings.model_copy(
        update={
            "trace_log_dir": case_dir / "trace",
            "db_path": case_dir / "state.db",
            "memory_dir": case_dir / "memory",
            "store_snapshot_path": case_dir / "store_snapshot.json",
        }
    )


def _seed_case_memory(
    memory_store: CustomerMemoryStore,
    sample: dict[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    scope = customer_scope_from_state(sample)
    # Reconstructed structured deliveries come first; point-in-time source
    # events come last so the newest 100-event memory window matches production.
    # The opposite order evicted recent case/activity facts for noisy accounts.
    events = list(prior_delivery_events(sample.get("prior_deliveries") or []))
    seen = {str(item.get("event_id") or "") for item in events if isinstance(item, dict)}
    for event in sample.get("source_history_events") or []:
        event_id = str(event.get("event_id") or "")
        if event_id and event_id in seen:
            continue
        events.append(event)
        if event_id:
            seen.add(event_id)
    events = events[-100:]
    if events:
        memory_store.save_update(
            scope.sales_contact_key,
            profile_update={},
            event_updates=events,
        )
    return scope.sales_contact_key, events


def build_case_runtime(
    *,
    settings: Settings,
    shared: dict[str, Any],
    sample: dict[str, Any],
    case_dir: Path,
) -> dict[str, Any]:
    case_settings = _case_settings(settings, case_dir)
    store = SQLiteStore(case_settings)
    store.initialize()
    repository = AppRepository(store)
    memory_store = CustomerMemoryStore(case_settings, repository)
    sales_contact_key, seed_events = _seed_case_memory(memory_store, sample)
    trace_logger = TraceLogger(case_settings)
    graph = build_reply_graphs(
        shared["coze_client"], trace_logger, shared["model_client"], memory_store=memory_store,
        customer_context_service=shared["customer_context"],
        customer_store_knowledge_service=shared["store_knowledge"],
        store_service=shared["store_service"], outreach_send_client=None,
        platform_agent_client=shared["platform_client"], sop_execution_service=None,
        semantic_router_service=shared["semantic_router"], sales_strategy_service=shared["sales_strategy"],
    ).full_graph
    timed_graph = TimedGraph(graph)
    runtime = ChatRuntime(
        full_graph=timed_graph,
        commit_graph=None,
        trace_logger=trace_logger,
        repository=repository,
        memory_store=memory_store,
        outreach_system_client=shared["outreach_system_client"],
        ai_sales_policy_service=shared["policy"],
        sales_strategy_service=shared["sales_strategy"],
        settings=case_settings,
    )
    return {
        "runtime": runtime,
        "graph": timed_graph,
        "repository": repository,
        "store": store,
        "sales_contact_key": sales_contact_key,
        "seed_event_count": len(seed_events),
        "settings": case_settings,
    }


def build_request(sample: dict[str, Any]) -> ChatRequest:
    context = {
        key: value for key, value in dict(sample.get("request_context") or {}).items()
        if key not in {"raw_workflow_payload", "test_isolated", "memory_persist_allowed"}
    }
    context.update(
        {
            "interface_version": "v3",
            "api_version": "v3",
            "reply_chain_mode": "model_led_sales_brain_v3",
            "v3_sidecar": True,
            "source_protocol": "real_identity_ephemeral_lifecycle_evaluation",
            "evaluation_source_request_id": sample.get("source_request_id"),
        }
    )
    return ChatRequest(
        content=str(sample.get("content") or ""),
        customer_id=str(sample.get("customer_id") or ""),
        corp_id=str(sample.get("corp_id") or ""),
        conversation_history=list(sample.get("conversation_history") or []),
        user_id=sample.get("user_id"),
        wechat=str(sample.get("wechat") or "") or None,
        external_userid=str(sample.get("external_userid") or "") or None,
        customer_add_wechat_id=sample.get("customer_add_wechat_id"),
        confirmed_store_id=sample.get("confirmed_store_id"),
        confirmed_store_name=sample.get("confirmed_store_name"),
        store_id=sample.get("store_id"),
        store_name=sample.get("store_name"),
        appointment_id=sample.get("appointment_id"),
        appointment_time=sample.get("appointment_time"),
        request_context=context,
    )


def ephemeral_counts(store: SQLiteStore) -> dict[str, int]:
    tables = ("runs", "messages", "history_events", "v3_strategy_usage_events", "message_dispatches", "strategy_data_outbox")
    with store.connect() as conn:
        return {
            table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in tables
        }


def build_state(sample: dict[str, Any], settings: Settings, policy: AiSalesPolicyService) -> dict[str, Any]:
    context = {
        key: value for key, value in dict(sample.get("request_context") or {}).items()
        if key not in {"raw_workflow_payload", "test_isolated", "memory_persist_allowed"}
    }
    context.update(
        {
            "interface_version": "v3", "api_version": "v3",
            "reply_chain_mode": "model_led_sales_brain_v3", "v3_sidecar": True,
            "test_isolated": True, "memory_persist_allowed": False,
            "source_protocol": "real_identity_write_free_evaluation",
        }
    )
    state: dict[str, Any] = {
        "request_id": f"eval-{uuid4()}", "customer_id": sample["customer_id"],
        "corp_id": sample["corp_id"], "content": sample["content"],
        "conversation_history": sample["conversation_history"], "file_image": None, "image_urls": [],
        "user_id": sample.get("user_id"), "wechat": sample.get("wechat"),
        "external_userid": sample.get("external_userid"),
        "customer_add_wechat_id": sample.get("customer_add_wechat_id"),
        "confirmed_store_id": sample.get("confirmed_store_id"),
        "confirmed_store_name": sample.get("confirmed_store_name"),
        "store_id": sample.get("store_id"), "store_name": sample.get("store_name"),
        "appointment_id": sample.get("appointment_id"), "appointment_time": sample.get("appointment_time"),
        "request_context": context, "test_isolated": True, "memory_persist_allowed": False,
        "runtime_budget": build_runtime_budget(settings), "trace": [], "errors": [],
        "previous_policy_state": {}, "ai_sales_policy": policy.runtime_snapshot(),
    }
    scope = customer_scope_from_state(state)
    state.update(
        {"sales_contact_key": scope.sales_contact_key, "global_customer_key": scope.global_customer_key,
         "customer_scope": scope.as_dict()}
    )
    return state


def find_first(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for child in value.values():
            found = find_first(child, key)
            if found not in (None, {}, []):
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_first(child, key)
            if found not in (None, {}, []):
                return found
    return None


def reply_text(state: dict[str, Any]) -> str:
    return " ".join(
        _text(item.get("content")) for item in state.get("reply_messages") or []
        if isinstance(item, dict) and item.get("type") == "text"
    )


def model_names(state: dict[str, Any]) -> list[str]:
    names: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"model", "selected_model", "semantic_model"} and isinstance(child, str) and child.strip():
                    names.add(child.strip())
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(state.get("trace") or [])
    return sorted(names)


def decision_summary(state: dict[str, Any]) -> dict[str, Any]:
    decision = state.get("policy_decision") if isinstance(state.get("policy_decision"), dict) else {}
    intent = decision.get("realtime_intent") if isinstance(decision.get("realtime_intent"), dict) else {}
    emotion = decision.get("emotion_decision") if isinstance(decision.get("emotion_decision"), dict) else {}
    closing = decision.get("closing_decision") if isinstance(decision.get("closing_decision"), dict) else {}
    cardpoint = decision.get("cardpoint_decision") if isinstance(decision.get("cardpoint_decision"), dict) else {}
    route = state.get("semantic_route") if isinstance(state.get("semantic_route"), dict) else {}
    friction = route.get("current_friction") if isinstance(route.get("current_friction"), dict) else {}
    closing_evidence = (
        route.get("closing_catalog_evidence")
        if isinstance(route.get("closing_catalog_evidence"), dict)
        else {}
    )
    closing_rules = [
        item for item in closing_evidence.get("selected_rules") or []
        if isinstance(item, dict)
    ]
    closing_sequences = [
        item for item in closing_evidence.get("candidate_sequences") or []
        if isinstance(item, dict)
    ]
    closing_sequence_keys = {
        _text(item.get("sequence_key")) for item in closing_sequences
        if _text(item.get("sequence_key"))
    }
    recall = state.get("sales_recall") if isinstance(state.get("sales_recall"), dict) else {}
    sequences = recall.get("sequence_candidates") or recall.get("sequences") or []
    scripts = recall.get("script_candidates") or recall.get("scripts") or recall.get("items") or recall.get("candidates") or []
    closing_scripts = [
        item for item in scripts
        if isinstance(item, dict)
        and any(
            isinstance(link, dict)
            and _text(link.get("query_source")) == "closing_catalog_node"
            for link in item.get("sequence_links") or []
        )
    ]
    closing_script_ids = {
        _text(item.get(key))
        for item in closing_scripts
        for key in ("script_id", "id", "source_id", "script_code")
        if _text(item.get(key))
    }
    knowledge = state.get("reply_knowledge_use") if isinstance(state.get("reply_knowledge_use"), dict) else {}
    selected_script_ids = [
        _text(item)
        for item in knowledge.get("selected_script_ids") or []
        if _text(item)
    ]
    tool_results = state.get("tool_results") if isinstance(state.get("tool_results"), dict) else {}
    store = tool_results.get("resolve_customer_store") or tool_results.get("customer_store_lookup") or {}
    failure = state.get("reply_failure") if isinstance(state.get("reply_failure"), dict) else {}
    return {
        "primary_task": _text((decision.get("primary_task") or {}).get("type")),
        "intent": _text(intent.get("type")), "emotion": _text(emotion.get("label")),
        "flow_action": _text(emotion.get("flow_action")), "closing_action": _text(closing.get("action")),
        "closing_sequence_key": _text(closing.get("sequence_key")),
        "closing_node_key": _text(closing.get("node_key")), "customer_state": _text(closing.get("customer_state")),
        "cardpoint_state": _text(cardpoint.get("state")),
        "checkpoint_code": _text(friction.get("checkpoint_code") or friction.get("code")),
        "sequence_candidates": [_text(item.get("name") or item.get("sequence_name") or item.get("sequence_key") or item.get("id")) for item in sequences[:3] if isinstance(item, dict)],
        "script_candidates": [_text(item.get("script_name") or item.get("name") or item.get("script_id") or item.get("id")) for item in scripts[:6] if isinstance(item, dict)],
        "closing_catalog_source": _text(closing_evidence.get("source")),
        "closing_catalog_status": _text(closing_evidence.get("status")),
        "closing_rule_match_status": _text(closing_evidence.get("match_status")),
        "closing_rule_candidates": [
            _text(item.get("type_name") or item.get("rule_key"))
            for item in closing_rules[:3]
        ],
        "closing_strategy_candidates": [
            _text(item.get("name") or item.get("sequence_key"))
            for item in closing_sequences[:3]
        ],
        "closing_script_candidates": [
            _text(item.get("script_name") or item.get("name") or item.get("script_id") or item.get("id"))
            for item in closing_scripts[:6]
        ],
        "adopted_sequence_id": _text(knowledge.get("sequence_id")),
        "adopted_script_id": ";".join(selected_script_ids),
        "closing_strategy_adopted": _text(knowledge.get("sequence_id")) in closing_sequence_keys,
        "closing_script_adopted": bool(set(selected_script_ids) & closing_script_ids),
        "store_status": _text(store.get("status") or store.get("match_status")),
        "decision_status": _text(state.get("decision_status")),
        "decision_reasons": [_text(item) for item in state.get("decision_reasons") or [] if _text(item)],
        "reply_source": _text(state.get("reply_source")),
        "failure_category": _text(failure.get("category")), "failure_code": _text(failure.get("code")),
        "failure_reason": redact(
            "；".join(
                item
                for item in (
                    _text(failure.get("reason") or failure.get("message") or state.get("recovery_reason")),
                    "primary_keys=" + ",".join(failure.get("primary_output_keys") or [])
                    if failure.get("primary_output_keys")
                    else "",
                    "repair_keys=" + ",".join(failure.get("repair_output_keys") or [])
                    if failure.get("repair_output_keys")
                    else "",
                )
                if item
            ),
            500,
        ),
    }


def compact_facts(state: dict[str, Any]) -> dict[str, Any]:
    shared = state.get("shared_context") if isinstance(state.get("shared_context"), dict) else {}
    facts = shared.get("authoritative_facts") if isinstance(shared.get("authoritative_facts"), dict) else {}
    rules = shared.get("rules") if isinstance(shared.get("rules"), dict) else {}
    joined = state.get("evidence_join") if isinstance(state.get("evidence_join"), dict) else {}
    normalized_tools = (
        joined.get("normalized_tool_facts")
        if isinstance(joined.get("normalized_tool_facts"), dict)
        else {}
    )
    return {
        "authoritative_facts": {
            key: facts[key]
            for key in (
                "orders_and_payment", "visible_store_scope", "request_store_facts",
                "registration_facts", "fact_source_status",
            )
            if key in facts
        },
        "business_authority": rules.get("AUTHORITATIVE FACTS") or {},
        "normalized_tool_facts": normalized_tools,
        "request_state": {
            key: state.get(key)
            for key in (
                "confirmed_store_id", "confirmed_store_name", "store_id", "store_name",
                "appointment_id", "appointment_time",
            )
            if state.get(key) not in (None, "")
        },
    }


def judge_messages(private: dict[str, Any]) -> list[dict[str, str]]:
    payload = {
        "客户最近历史": private["history"][-10:], "客户本轮消息": private["content"],
        "权威事实": private["facts"], "系统决策": private["summary"],
        "此前已真实交付的结构化消息": private.get("prior_structured_messages") or {},
        "系统本轮完整客户可见回复": private.get("rendered_reply_messages") or private["reply"],
        "确定性生命周期要求": (private.get("hard_assertions") or {}).get("contract") or {},
        "确定性断言结果": private.get("hard_assertions") or {},
        "评测场景桶": private["bucket"],
    }
    rubric = """你是销售客服质量评审员。只根据给定客户原话、历史和权威事实评估，不补充业务事实。
输出严格 JSON：{"expected_intent":"7类之一","expected_emotion":"8类之一","pressure_direction_ok":true,"reply_accuracy_score":0,"reply_naturalness_score":0,"mainline_progress_score":0,"follow_sequence_fit_score":null,"closing_fit_score":null,"store_next_step_ok":null,"stale_topic_revival":false,"unsupported_fact":false,"safety_ok":true,"passed":true,"reasons":["简短中文原因"]}
意图只能是 fact_inquiry、blocker_expression、transaction_progress、information_submission、defer、explicit_exit、normal_exchange。情绪只能是 neutral、curious、enthusiastic、hesitant、cold、defensive、impatient、angry。粗口本身不是愤怒；只有明确针对品牌、服务或接待人员的强烈敌意才是 angry。投诉不等于退订。有未解决卡点时应先解卡并暂停逼单；明确退订必须停止营销。回复必须紧扣本轮话题：若客户本轮没有提地点、门店、路线、到店或预约，却主动续接历史中的具体门店、地区、路线或预约，stale_topic_revival=true、passed=false，且自然度不得高于 5。客户询问某条广告或某个具体案例是否真实时，除非权威事实里有对应案例记录，否则确认“那个案例是真的”、声称有该人的原相机记录或用该案例承诺效果，都属于 unsupported_fact。门店、预约、付款、效果不得脱离权威事实。门店判断以本轮 normalized_tool_facts 中的 store_resolution_fact 为最高优先级；历史订单里出现的门店只说明旧订单关联，不等于客户当前位置、当前确认门店或本轮已有可发送门店，不得用它推翻本轮 no_valid_candidate/clarify_location 结果。必须同时阅读文本、门店卡、付款卡、图片和视频，不能只评文本。门店卡已经送达且客户本轮只问停车或营业时间时，不能重复同一门店卡；若确定性生命周期要求表明效果、活动和门店主线均已真实交付、客户尚未预约且没有卡点、安全风险或暂缓，回答后必须明确说明下一步是预约/登记/保留名额，仅问“工作日还是周末”但不说明预约目的，mainline_progress_score不得高于5且passed=false。若效果或活动尚未交付，则应推进最缺的价值环节，不能机械邀约。passed 需要回复准确且自然、压力合适、安全合规，关键事实无虚构。"""
    return [{"role": "system", "content": rubric}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)}]


def normalize_judge(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    intent, emotion = _text(raw.get("expected_intent")), _text(raw.get("expected_emotion"))
    return {
        "expected_intent": intent if intent in INTENTS else "",
        "expected_emotion": emotion if emotion in EMOTIONS else "",
        "pressure_direction_ok": bool(raw.get("pressure_direction_ok")),
        "reply_accuracy_score": int(raw.get("reply_accuracy_score") or 0),
        "reply_naturalness_score": int(raw.get("reply_naturalness_score") or 0),
        "mainline_progress_score": int(raw.get("mainline_progress_score") or 0),
        "follow_sequence_fit_score": raw.get("follow_sequence_fit_score"),
        "closing_fit_score": raw.get("closing_fit_score"),
        "store_next_step_ok": raw.get("store_next_step_ok"),
        "stale_topic_revival": bool(raw.get("stale_topic_revival")),
        "unsupported_fact": bool(raw.get("unsupported_fact")), "safety_ok": bool(raw.get("safety_ok")),
        "passed": bool(raw.get("passed")), "reasons": [redact(item, 160) for item in (raw.get("reasons") or [])[:5]],
    }


async def close_runtime(runtime: dict[str, Any]) -> None:
    await runtime["model_client"].aclose()
    await runtime["semantic_client"].aclose()
    await runtime["follow_client"].aclose()
    await runtime["coze_client"].aclose()
    await runtime["outreach_system_client"].aclose()
    runtime["platform_client"].close()


async def runtime_phase(args: argparse.Namespace, private_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    settings = build_settings(args.output)
    validate_evaluation_settings(settings)
    audit: dict[str, Any] = {
        "blocked_attempts": [],
        "write_methods_installed": 0,
        "ephemeral_repository_cases": 0,
        "ephemeral_rows": Counter(),
        "evaluation_request_ids": [],
        "source_read_queries": 0,
    }
    candidates = load_candidates(args.days)
    if args.source_memory_mode == "production-readonly":
        # Load historical state for store/appointment candidates before
        # sampling, otherwise the state matrix would again be selected only by
        # current-message wording.
        state_candidates = [
            item
            for item in candidates
            if item.get("bucket") == "store"
            or item.get("appointment_id")
            or item.get("appointment_time")
        ]
        load_source_history_events(state_candidates, audit)
        refresh_state_tags(state_candidates)
    if args.request_id:
        requested = list(dict.fromkeys(str(item).strip() for item in args.request_id if str(item).strip()))
        by_request = {str(item.get("source_request_id") or ""): item for item in candidates}
        missing = [item for item in requested if item not in by_request]
        if missing:
            raise RuntimeError("source request ids not found: " + ",".join(missing))
        samples = [by_request[item] for item in requested]
        distribution = dict(Counter(row["bucket"] for row in samples))
    else:
        samples, distribution = choose_samples(candidates, args.limit)
        if len(samples) < args.limit:
            raise RuntimeError(f"eligible real samples insufficient: {len(samples)} < {args.limit}")
    if args.source_memory_mode == "production-readonly":
        load_source_history_events(samples, audit)
        refresh_state_tags(samples)
        audit["source_history_event_count"] = sum(
            len(sample.get("source_history_events") or []) for sample in samples
        )
    else:
        audit["source_memory_mode"] = "runs-only"
        audit["source_read_queries"] = 0
        audit["source_history_event_count"] = 0
    shared = build_runtime(settings, audit)
    semaphore = asyncio.Semaphore(max(1, args.concurrency))
    rows: list[dict[str, Any] | None] = [None] * len(samples)
    private_rows: list[dict[str, Any] | None] = [None] * len(samples)
    fatal = asyncio.Event()
    ephemeral_root = args.output / ".ephemeral"
    ephemeral_root.mkdir(parents=True, exist_ok=True)

    async def one(index: int, sample: dict[str, Any]) -> None:
        if fatal.is_set():
            return
        state: dict[str, Any] = {}
        case_id = f"C{index + 1:04d}"
        case_dir = ephemeral_root / f"{case_id}-{uuid4().hex[:8]}"
        case_runtime: dict[str, Any] | None = None
        lifecycle_started = time.perf_counter()
        try:
            async with semaphore:
                # Build a real ChatRuntime lifecycle around a disposable local
                # repository. The production source remains read-only and all
                # external write methods are fail-closed blockers.
                lifecycle_started = time.perf_counter()
                case_runtime = build_case_runtime(
                    settings=settings,
                    shared=shared,
                    sample=sample,
                    case_dir=case_dir,
                )
                request = build_request(sample)
                response = await asyncio.wait_for(
                    case_runtime["runtime"].run_platform_reply(request),
                    timeout=175.0,
                )
                response_returned = time.perf_counter()
                request_id = str(response.request_id or "")
                state = case_runtime["graph"].final_by_request.get(request_id) or {}
                response_messages = normalize_visible_messages(response.reply_messages)
                if not state:
                    state = {
                        "request_id": request_id,
                        "reply_messages": response_messages,
                        "reply_source": (response.meta or {}).get("reply_source", ""),
                        "trace": [],
                        "errors": [],
                    }
                else:
                    state["reply_messages"] = response_messages
                public_body = workflow_response_from_chat(response)
                serialize_started = time.perf_counter()
                case_runtime["repository"].update_run_http_response(
                    request_id=request_id,
                    response_body=public_body,
                )
                json.dumps(public_body, ensure_ascii=False, default=str)
                lifecycle_finished = time.perf_counter()
                timing = case_runtime["graph"].timing_by_request.get(request_id) or {}
                graph_started = int(timing.get("graph_started_ns") or 0) / 1_000_000_000
                graph_finished = int(timing.get("graph_finished_ns") or 0) / 1_000_000_000
                timings = {
                    "pre_graph_ms": int(max(0.0, graph_started - lifecycle_started) * 1000) if graph_started else 0,
                    "graph_duration_ms": int(timing.get("graph_duration_ms") or 0),
                    "post_graph_ms": int(max(0.0, response_returned - graph_finished) * 1000) if graph_finished else 0,
                    "response_serialize_ms": int(max(0.0, lifecycle_finished - serialize_started) * 1000),
                    "lifecycle_duration_ms": int(max(0.0, lifecycle_finished - lifecycle_started) * 1000),
                }
                counts = ephemeral_counts(case_runtime["store"])
                expected_message_count = 2 if response_messages else 1
                if counts["runs"] < 1 or counts["messages"] < expected_message_count:
                    raise RuntimeError(f"ephemeral lifecycle persistence incomplete: {counts}")
                audit["ephemeral_repository_cases"] += 1
                audit["evaluation_request_ids"].append(request_id)
                for table, count in counts.items():
                    audit["ephemeral_rows"][table] += count
                await asyncio.sleep(max(0.0, args.runtime_gap_seconds))
            models = set(model_names(state))
            for client_name in ("model_client", "semantic_client"):
                usage = getattr(shared[client_name], "last_usage", None)
                if isinstance(usage, dict) and _text(usage.get("model")):
                    models.add(_text(usage.get("model")))
            models = sorted(models)
            non_deepseek = [name for name in models if not name.lower().startswith("deepseek-")]
            if non_deepseek:
                fatal.set()
                raise RuntimeError("non-DeepSeek model observed: " + ",".join(non_deepseek))
            summary = decision_summary(state)
            facts = compact_facts(state)
            prior_deliveries = _prior_deliveries_with_source_memory(sample)
            hard = hard_assertions(
                sample=sample,
                facts=facts,
                prior_deliveries=prior_deliveries,
                reply_messages=response_messages,
            )
            prior_summary = prior_structured_summary(prior_deliveries)
            rows[index] = {
                "case_id": case_id, "identity_hash": sample["identity_hash"],
                "source_request_hash": hashlib.sha256(str(sample.get("source_request_id") or "").encode()).hexdigest()[:12],
                "bucket": sample["bucket"], "customer_excerpt": redact(sample["content"], 100),
                "reply_excerpt": redact(customer_visible_text(response_messages), 180), **summary,
                "prior_structured_count": prior_summary.get("message_count", 0),
                "prior_store_card_count": len(prior_summary.get("store_card_ids") or []),
                "reply_message_types": [item.get("type") for item in response_messages],
                "hard_assertion_passed": hard.get("passed"),
                "hard_failure_codes": hard.get("failure_codes") or [],
                "model_names": models, **timings,
                "duration_ms": timings["lifecycle_duration_ms"], "judge": {}, "runtime_error": "",
            }
            private_rows[index] = {
                "case_id": case_id, "bucket": sample["bucket"], "content": sample["content"],
                "history": sample["conversation_history"], "facts": facts,
                "summary": summary, "reply": customer_visible_text(response_messages),
                "reply_messages": response_messages,
                "rendered_reply_messages": render_visible_messages(response_messages),
                "prior_structured_messages": prior_summary,
                "hard_assertions": hard,
            }
        except WriteBlockedError:
            fatal.set()
            if "graph_runtime" not in audit["blocked_attempts"]:
                audit["blocked_attempts"].append("graph_runtime")
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            if "non-DeepSeek" in message:
                fatal.set()
            rows[index] = {
                "case_id": case_id, "identity_hash": sample["identity_hash"],
                "bucket": sample["bucket"], "customer_excerpt": redact(sample["content"], 100),
                "reply_excerpt": "", "duration_ms": int((time.perf_counter() - lifecycle_started) * 1000),
                "runtime_error": redact(message, 300), "judge": {},
            }
        finally:
            if case_runtime is not None:
                case_runtime["store"].close()
            shutil.rmtree(case_dir, ignore_errors=True)
        print(json.dumps({"phase": "runtime", "done": index + 1, "total": len(samples)}, ensure_ascii=False), flush=True)

    try:
        await asyncio.gather(*(one(index, sample) for index, sample in enumerate(samples)))
        if args.source_memory_mode == "production-readonly":
            verify_source_absence(list(audit.get("evaluation_request_ids") or []), audit)
    finally:
        await close_runtime(shared)
        shutil.rmtree(ephemeral_root, ignore_errors=True)
    clean_rows = [row for row in rows if isinstance(row, dict)]
    if fatal.is_set() or audit["blocked_attempts"]:
        raise RuntimeError("evaluation aborted: non-DeepSeek model or production write attempt")
    with private_path.open("w", encoding="utf-8") as handle:
        for row in private_rows:
            if isinstance(row, dict):
                handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    audit["ephemeral_rows"] = dict(audit["ephemeral_rows"])
    return clean_rows, {
        "distribution": distribution,
        "state_distribution": dict(Counter(tag for sample in samples for tag in sample.get("state_tags") or [])),
        "audit": audit,
    }


async def judge_phase(args: argparse.Namespace, private_path: Path, rows: list[dict[str, Any]]) -> None:
    if not private_path.exists():
        raise RuntimeError("private runtime checkpoint is missing")
    private_rows = [json.loads(line) for line in private_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    by_case = {row["case_id"]: row for row in rows}
    settings = build_settings(args.output).model_copy(
        update={"deepseek_semantic_model": "deepseek-reasoner", "deepseek_semantic_timeout_seconds": 90.0,
                "deepseek_semantic_max_tokens": 1800}
    )
    judge = DeepSeekSemanticClient(settings, None)
    try:
        for index, private in enumerate(private_rows):
            row = by_case.get(private["case_id"])
            if row is None or row.get("runtime_error"):
                continue
            source = _text(row.get("reply_source"))
            if source in NON_MODEL_TERMINAL_SOURCES:
                row["judge"] = {
                    "skipped": True,
                    "skip_reason": source,
                }
                continue
            if not customer_reply_generated(row, private.get("reply")):
                row["judge"] = {
                    "expected_intent": "",
                    "expected_emotion": "",
                    "pressure_direction_ok": False,
                    "reply_accuracy_score": 0,
                    "reply_naturalness_score": 0,
                    "mainline_progress_score": 0,
                    "follow_sequence_fit_score": None,
                    "closing_fit_score": None,
                    "store_next_step_ok": None,
                    "stale_topic_revival": False,
                    "unsupported_fact": False,
                    "safety_ok": True,
                    "ai_passed": False,
                    "passed": False,
                    "hard_failure_codes": list(row.get("hard_failure_codes") or []),
                    "reasons": ["运行阶段未生成有效客户回复"],
                }
                continue
            raw = await judge.chat_json(judge_messages(private))
            row["judge"] = merge_ai_judge(
                normalize_judge(raw),
                private.get("hard_assertions") or {},
            )
            row["judge_model"] = _text((judge.last_usage or {}).get("model"))
            if row["judge_model"] and not row["judge_model"].startswith("deepseek-"):
                raise RuntimeError("non-DeepSeek judge observed")
            print(json.dumps({"phase": "judge", "done": index + 1, "total": len(private_rows)}, ensure_ascii=False), flush=True)
            await asyncio.sleep(max(0.0, args.judge_gap_seconds))
    finally:
        await judge.aclose()


def percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(len(ordered) * fraction) - 1))]


def build_metrics(rows: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any]:
    completed = [row for row in rows if not row.get("runtime_error")]
    evaluable = [
        row
        for row in completed
        if _text(row.get("reply_source")) not in NON_MODEL_TERMINAL_SOURCES
    ]
    judged = [
        row
        for row in evaluable
        if row.get("judge") and not bool((row.get("judge") or {}).get("skipped"))
    ]
    policy = [row for row in evaluable if row.get("intent") and row.get("emotion") and row.get("closing_action")]
    valid = [row for row in evaluable if customer_reply_generated(row)]
    valid_model = [row for row in evaluable if row.get("reply_source") in VALID_REPLY_SOURCES]
    eligible = [row for row in valid if row.get("intent") != "explicit_exit" and (row.get("sequence_candidates") or row.get("script_candidates"))]
    durations = [int(row.get("duration_ms") or 0) for row in evaluable]
    graph_durations = [int(row.get("graph_duration_ms") or 0) for row in evaluable]
    post_graph_durations = [int(row.get("post_graph_ms") or 0) for row in evaluable]
    hard_failures = [code for row in evaluable for code in row.get("hard_failure_codes") or []]
    policy_case_ids = {row.get("case_id") for row in policy}
    judged_policy = [row for row in judged if row.get("case_id") in policy_case_ids]
    return {
        "requested_count": len(rows), "completed_count": len(completed),
        "evaluable_count": len(evaluable),
        "non_model_terminal_count": len(completed) - len(evaluable),
        "runtime_error_count": len(rows) - len(completed),
        "valid_customer_reply_count": len(valid), "valid_model_reply_count": len(valid_model),
        "policy_core_coverage": round(len(policy) / len(evaluable), 4) if evaluable else 0,
        "degraded_count": sum(row.get("decision_status") == "degraded" for row in evaluable),
        "decision_reasons": dict(Counter(reason for row in evaluable for reason in row.get("decision_reasons") or [])),
        "failure_codes": dict(Counter(row.get("failure_code") or "none" for row in evaluable)),
        "reply_sources": dict(Counter(row.get("reply_source") or "exception" for row in rows)),
        "sequence_candidate_count": sum(bool(row.get("sequence_candidates")) for row in evaluable),
        "script_candidate_count": sum(bool(row.get("script_candidates")) for row in evaluable),
        "closing_rule_candidate_count": sum(bool(row.get("closing_rule_candidates")) for row in evaluable),
        "closing_strategy_candidate_count": sum(bool(row.get("closing_strategy_candidates")) for row in evaluable),
        "closing_script_candidate_count": sum(bool(row.get("closing_script_candidates")) for row in evaluable),
        "adoption_eligible_count": len(eligible),
        "sequence_adopted_count": sum(bool(row.get("adopted_sequence_id")) for row in eligible),
        "script_adopted_count": sum(bool(row.get("adopted_script_id")) for row in eligible),
        "closing_strategy_adopted_count": sum(bool(row.get("closing_strategy_adopted")) for row in evaluable),
        "closing_script_adopted_count": sum(bool(row.get("closing_script_adopted")) for row in evaluable),
        "closing_enter_advance_count": sum(row.get("closing_action") in {"enter", "advance"} for row in valid),
        "judge_count": len(judged),
        "ai_judge_pass_rate": round(sum(bool(row["judge"].get("ai_passed", row["judge"].get("passed"))) for row in judged) / len(judged), 4) if judged else 0,
        "judge_pass_rate": round(sum(bool(row["judge"].get("passed")) for row in judged) / len(judged), 4) if judged else 0,
        "hard_failure_count": len(hard_failures),
        "hard_failures": dict(Counter(hard_failures)),
        "structured_history_case_count": sum(int(row.get("prior_structured_count") or 0) > 0 for row in evaluable),
        "prior_store_card_case_count": sum(int(row.get("prior_store_card_count") or 0) > 0 for row in evaluable),
        "human_expression_pass_rate": round(sum(int(row["judge"].get("reply_naturalness_score") or 0) >= 7 and not bool(row["judge"].get("stale_topic_revival")) for row in judged) / len(judged), 4) if judged else 0,
        "stale_topic_revival_count": sum(bool(row["judge"].get("stale_topic_revival")) for row in judged),
        "intent_accuracy_valid_policy": round(sum(row.get("intent") == row["judge"].get("expected_intent") for row in judged_policy) / max(1, len(judged_policy)), 4),
        "emotion_accuracy_valid_policy": round(sum(row.get("emotion") == row["judge"].get("expected_emotion") for row in judged_policy) / max(1, len(judged_policy)), 4),
        "unsupported_fact_count": sum(bool(row["judge"].get("unsupported_fact")) for row in judged),
        "safety_failure_count": sum(not bool(row["judge"].get("safety_ok")) for row in judged),
        "p50_ms": int(statistics.median(durations)) if durations else 0, "p95_ms": percentile(durations, 0.95),
        "graph_p50_ms": int(statistics.median(graph_durations)) if graph_durations else 0,
        "graph_p95_ms": percentile(graph_durations, 0.95),
        "post_graph_p50_ms": int(statistics.median(post_graph_durations)) if post_graph_durations else 0,
        "post_graph_p95_ms": percentile(post_graph_durations, 0.95),
        "sample_distribution": context.get("distribution") or {},
        "sample_state_distribution": context.get("state_distribution") or {},
        "model_names": sorted({name for row in evaluable for name in row.get("model_names") or []}),
        "isolation": {
            "commit_graph_constructed": False,
            "public_reply_endpoint_called": False,
            "production_repository_passed_to_runtime": False,
            "ephemeral_repository_cases": int((context.get("audit") or {}).get("ephemeral_repository_cases") or 0),
            "ephemeral_rows": dict((context.get("audit") or {}).get("ephemeral_rows") or {}),
            "external_write_blockers_installed": int((context.get("audit") or {}).get("write_methods_installed") or 0),
            "source_memory_mode": str((context.get("audit") or {}).get("source_memory_mode") or "runs-only"),
            "source_read_queries": int((context.get("audit") or {}).get("source_read_queries") or 0),
            "source_history_event_count": int((context.get("audit") or {}).get("source_history_event_count") or 0),
            "production_absence": dict((context.get("audit") or {}).get("production_absence") or {}),
            "blocked_write_attempts": list((context.get("audit") or {}).get("blocked_attempts") or []),
        },
    }


CSV_FIELDS = [
    "case_id", "identity_hash", "source_request_hash", "bucket", "customer_excerpt", "reply_excerpt", "reply_source",
    "prior_structured_count", "prior_store_card_count", "reply_message_types",
    "hard_assertion_passed", "hard_failure_codes",
    "primary_task", "intent", "emotion", "flow_action", "checkpoint_code", "cardpoint_state",
    "sequence_candidates", "script_candidates", "adopted_sequence_id", "adopted_script_id",
    "closing_catalog_source", "closing_catalog_status", "closing_rule_match_status",
    "closing_rule_candidates", "closing_strategy_candidates", "closing_script_candidates",
    "closing_strategy_adopted", "closing_script_adopted",
    "closing_action", "closing_sequence_key", "closing_node_key", "customer_state", "store_status",
    "decision_status", "decision_reasons", "failure_category", "failure_code", "failure_reason",
    "pre_graph_ms", "graph_duration_ms", "post_graph_ms", "response_serialize_ms", "lifecycle_duration_ms", "duration_ms", "runtime_error",
    "judge_expected_intent", "judge_expected_emotion", "judge_ai_passed", "judge_passed", "judge_reply_accuracy",
    "judge_naturalness", "judge_mainline_progress", "judge_follow_sequence_fit", "judge_closing_fit",
    "judge_store_next_step_ok", "judge_stale_topic_revival", "judge_unsupported_fact", "judge_safety_ok", "judge_reasons",
]


def csv_row(row: dict[str, Any]) -> dict[str, Any]:
    judge = row.get("judge") if isinstance(row.get("judge"), dict) else {}
    flat = dict(row)
    flat.update(
        {
            "sequence_candidates": "；".join(row.get("sequence_candidates") or []),
            "script_candidates": "；".join(row.get("script_candidates") or []),
            "closing_rule_candidates": "；".join(row.get("closing_rule_candidates") or []),
            "closing_strategy_candidates": "；".join(row.get("closing_strategy_candidates") or []),
            "closing_script_candidates": "；".join(row.get("closing_script_candidates") or []),
            "decision_reasons": "；".join(row.get("decision_reasons") or []),
            "reply_message_types": "；".join(row.get("reply_message_types") or []),
            "hard_failure_codes": "；".join(row.get("hard_failure_codes") or []),
            "judge_expected_intent": judge.get("expected_intent", ""),
            "judge_expected_emotion": judge.get("expected_emotion", ""),
            "judge_ai_passed": judge.get("ai_passed", ""),
            "judge_passed": judge.get("passed", ""),
            "judge_reply_accuracy": judge.get("reply_accuracy_score", ""),
            "judge_naturalness": judge.get("reply_naturalness_score", ""),
            "judge_mainline_progress": judge.get("mainline_progress_score", ""),
            "judge_follow_sequence_fit": judge.get("follow_sequence_fit_score", ""),
            "judge_closing_fit": judge.get("closing_fit_score", ""),
            "judge_store_next_step_ok": judge.get("store_next_step_ok", ""),
            "judge_stale_topic_revival": judge.get("stale_topic_revival", ""),
            "judge_unsupported_fact": judge.get("unsupported_fact", ""),
            "judge_safety_ok": judge.get("safety_ok", ""), "judge_reasons": "；".join(judge.get("reasons") or []),
        }
    )
    return {field: flat.get(field, "") for field in CSV_FIELDS}


def write_outputs(output: Path, rows: list[dict[str, Any]], metrics: dict[str, Any]) -> None:
    (output / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    with (output / "report.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(csv_row(row) for row in rows)
    failures = [
        row
        for row in rows
        if row.get("runtime_error")
        or (
            _text(row.get("reply_source")) not in NON_MODEL_TERMINAL_SOURCES
            and not bool((row.get("judge") or {}).get("passed"))
        )
    ]
    lines = ["# 失败与人工复核案例", "", f"共 {len(failures)} 条。", ""]
    for row in failures:
        hard_reason = "；".join(row.get("hard_failure_codes") or [])
        reason = "；".join((row.get("judge") or {}).get("reasons") or []) or hard_reason or row.get("failure_reason") or row.get("failure_code") or row.get("runtime_error") or "AI 初评未通过"
        lines += [f"## {row.get('case_id')}｜{row.get('bucket')}", "", f"- 客户消息摘要：{row.get('customer_excerpt', '')}",
                  f"- 回复摘要：{row.get('reply_excerpt', '')}",
                  f"- 系统决策：意图 {row.get('intent', '')}；情绪 {row.get('emotion', '')}；B 单 {row.get('closing_action', '')}",
                  f"- 复核原因：{reason}", ""]
    (output / "failures.md").write_text("\n".join(lines), encoding="utf-8")
    report = [
        "# V3 全链路 DeepSeek 两阶段隔离评测", "",
        "> 运行与评审已分阶段执行；这是 DeepSeek AI 初评，不是业务确认金标。", "",
        f"- 样本：{metrics['requested_count']}；可评业务请求：{metrics['evaluable_count']}；人工接管/协议终态：{metrics['non_model_terminal_count']}；运行异常：{metrics['runtime_error_count']}",
        f"- 有效客户回复：{metrics['valid_customer_reply_count']}；其中主模型/单次修复：{metrics['valid_model_reply_count']}",
        f"- 完整意图+情绪+B 单覆盖率：{metrics['policy_core_coverage']:.1%}",
        f"- AI 原始初评通过率：{metrics['ai_judge_pass_rate']:.1%}",
        f"- 合并硬断言后的最终通过率：{metrics['judge_pass_rate']:.1%}",
        f"- 生命周期硬失败：{metrics['hard_failure_count']}；{json.dumps(metrics['hard_failures'], ensure_ascii=False)}",
        f"- 含结构化历史/已发门店卡样本：{metrics['structured_history_case_count']}/{metrics['prior_store_card_case_count']}",
        f"- 真人表达通过率：{metrics['human_expression_pass_rate']:.1%}",
        f"- 旧话题误续接：{metrics['stale_topic_revival_count']}",
        f"- 有效策略行意图一致率：{metrics['intent_accuracy_valid_policy']:.1%}",
        f"- 有效策略行情绪一致率：{metrics['emotion_accuracy_valid_policy']:.1%}",
        f"- 序列候选/话术候选：{metrics['sequence_candidate_count']}/{metrics['script_candidate_count']}",
        f"- B 单规则/策略/话术候选：{metrics['closing_rule_candidate_count']}/{metrics['closing_strategy_candidate_count']}/{metrics['closing_script_candidate_count']}",
        f"- 条件可采用样本：{metrics['adoption_eligible_count']}；采用序列/话术：{metrics['sequence_adopted_count']}/{metrics['script_adopted_count']}",
        f"- B 单策略/话术实际采用：{metrics['closing_strategy_adopted_count']}/{metrics['closing_script_adopted_count']}",
        f"- B 单 enter/advance：{metrics['closing_enter_advance_count']}",
        f"- 完整生命周期 P50/P95：{metrics['p50_ms']}/{metrics['p95_ms']} ms",
        f"- 模型图 P50/P95：{metrics['graph_p50_ms']}/{metrics['graph_p95_ms']} ms",
        f"- 图后持久化与返回 P50/P95：{metrics['post_graph_p50_ms']}/{metrics['post_graph_p95_ms']} ms", "",
        f"- 回复来源：{json.dumps(metrics['reply_sources'], ensure_ascii=False)}",
        f"- 失败分类：{json.dumps(metrics['failure_codes'], ensure_ascii=False)}",
        f"- 决策降级原因：{json.dumps(metrics['decision_reasons'], ensure_ascii=False)}",
        f"- 安全失败/无依据事实：{metrics['safety_failure_count']}/{metrics['unsupported_fact_count']}",
        f"- 生产写入尝试：{len(metrics['isolation']['blocked_write_attempts'])}",
        f"- 观测模型：{', '.join(metrics['model_names']) or 'trace 未记录名称'}", "",
        f"- 消息场景分布：{json.dumps(metrics['sample_distribution'], ensure_ascii=False)}",
        f"- 生命周期状态覆盖：{json.dumps(metrics['sample_state_distribution'], ensure_ascii=False)}", "",
        "逐条数据见 `report.csv`，需复核案例见 `failures.md`。",
    ]
    (output / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    (output / "isolation_audit.md").write_text(
        "# 隔离审计\n\n- 未调用公网回复接口。\n- 未构建 commit graph。\n- 未把生产 Repository 传入运行时。\n"
        f"- 临时 Repository 完整生命周期样本：{metrics['isolation']['ephemeral_repository_cases']}。\n"
        f"- 临时持久化行数：{json.dumps(metrics['isolation']['ephemeral_rows'], ensure_ascii=False)}。\n"
        f"- 外部写接口阻断器：{metrics['isolation']['external_write_blockers_installed']} 个。\n"
        f"- 生产源只读查询：{metrics['isolation']['source_read_queries']} 次；读取历史事件：{metrics['isolation']['source_history_event_count']} 条。\n"
        f"- 生产库反查命中：{json.dumps(metrics['isolation']['production_absence'], ensure_ascii=False)}。\n"
        f"- 写接口触发尝试：{len(metrics['isolation']['blocked_write_attempts'])}。\n",
        encoding="utf-8",
    )


async def run(args: argparse.Namespace) -> int:
    args.output.mkdir(parents=True, exist_ok=True)
    private_path = args.output / ".private_runtime.jsonl"
    rows_path = args.output / ".runtime_rows.json"
    context_path = args.output / ".runtime_context.json"
    rows: list[dict[str, Any]]
    context: dict[str, Any]
    if args.mode in {"runtime", "all"}:
        rows, context = await runtime_phase(args, private_path)
        rows_path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        context_path.write_text(json.dumps(context, ensure_ascii=False), encoding="utf-8")
        if args.mode == "all":
            await asyncio.sleep(max(0.0, args.cooldown_seconds))
    else:
        rows = json.loads(rows_path.read_text(encoding="utf-8"))
        context = json.loads(context_path.read_text(encoding="utf-8"))
    if args.mode in {"judge", "all"}:
        await judge_phase(args, private_path, rows)
    metrics = build_metrics(rows, context)
    write_outputs(args.output, rows, metrics)
    if args.mode in {"judge", "all"}:
        private_path.unlink(missing_ok=True)
        rows_path.unlink(missing_ok=True)
        context_path.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
