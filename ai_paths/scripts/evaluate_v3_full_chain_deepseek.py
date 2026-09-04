from __future__ import annotations

"""Run a write-free, two-phase DeepSeek evaluation of the V3 reply graph.

The runtime phase must finish before the judge phase starts.  This prevents a
large DeepSeek-Reasoner judging request from competing with the Router/Reply
requests under test.  Only redacted outputs remain after ``--mode all``.
"""

import argparse
import asyncio
import csv
import hashlib
import json
import os
import random
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from uuid import uuid4


ROOT = Path(os.environ.get("EVAL_CANDIDATE_ROOT", Path(__file__).resolve().parents[2]))
AI_PATHS_ROOT = ROOT / "ai_paths"
if str(AI_PATHS_ROOT) not in sys.path:
    sys.path.insert(0, str(AI_PATHS_ROOT))

from app.config import Settings  # noqa: E402
from app.graph.graph_builder import build_reply_graphs  # noqa: E402
from app.services.ai_sales_policy_service import AiSalesPolicyService  # noqa: E402
from app.services.coze_client import CozeClient  # noqa: E402
from app.services.customer_context import CustomerContextService  # noqa: E402
from app.services.customer_scope import customer_scope_from_state  # noqa: E402
from app.services.customer_store_knowledge import CustomerStoreKnowledgeService  # noqa: E402
from app.services.deepseek_semantic_client import DeepSeekSemanticClient  # noqa: E402
from app.services.follow_knowledge_client import FollowKnowledgeClient  # noqa: E402
from app.services.model_client import ModelClient  # noqa: E402
from app.services.platform_agent_client import PlatformAgentClient  # noqa: E402
from app.services.runtime_budget import build_runtime_budget  # noqa: E402
from app.services.sales_strategy_service import SalesStrategyService  # noqa: E402
from app.services.store_service import StoreService  # noqa: E402
from app.services.store_snapshot_service import StoreSnapshotService  # noqa: E402
from app.services.trace_logger import TraceLogger  # noqa: E402
from app.services.v3_semantic_router_service import V3SemanticRouterService  # noqa: E402


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
        ("store", ("门店", "地址", "附近", "离我", "多远", "哪个店", "怎么走", "定位", "地铁")),
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
        content = _text(raw.get("content"))
        if not content or content in AUTO_MESSAGES or content.startswith("你已添加了"):
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
                "source_reply_source": _text(raw.get("reply_source")), "content": content,
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
    return rows


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
        current = 0
        for row in groups.get(bucket, []):
            if row["source_path"] in used or per_identity[row["identity_hash"]] >= 3:
                continue
            selected.append(row)
            used.add(row["source_path"])
            per_identity[row["identity_hash"]] += 1
            current += 1
            if current >= count:
                return

    scale = min(1.0, limit / 400.0)
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


def build_settings(output_dir: Path) -> Settings:
    return Settings().model_copy(
        update={
            "service_role": "reply", "background_workers_enabled": False,
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


def _block_write(name: str, audit: dict[str, Any]):
    def blocked(*_args: Any, **_kwargs: Any) -> Any:
        audit["blocked_attempts"].append(name)
        raise WriteBlockedError(f"production write blocked: {name}")

    return blocked


def build_runtime(settings: Settings, audit: dict[str, Any]) -> dict[str, Any]:
    trace_logger = TraceLogger(settings)
    coze_client = CozeClient(settings)
    model_client = ModelClient(settings)
    platform_client = PlatformAgentClient(settings)
    for name in ("create_work_order", "create_order_plan", "add_customer_mobile"):
        setattr(platform_client, name, _block_write(f"platform_agent.{name}", audit))
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
    graph = build_reply_graphs(
        coze_client, trace_logger, model_client, memory_store=None,
        customer_context_service=customer_context,
        customer_store_knowledge_service=store_knowledge,
        store_service=StoreService(platform_client), outreach_send_client=None,
        platform_agent_client=platform_client, sop_execution_service=None,
        semantic_router_service=semantic_router, sales_strategy_service=SalesStrategyService(settings),
    ).full_graph
    return {
        "graph": graph, "model_client": model_client, "semantic_client": semantic_client,
        "follow_client": follow_client, "coze_client": coze_client,
        "platform_client": platform_client, "policy": policy,
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
    recall = state.get("sales_recall") if isinstance(state.get("sales_recall"), dict) else {}
    sequences = recall.get("sequence_candidates") or recall.get("sequences") or []
    scripts = recall.get("script_candidates") or recall.get("scripts") or recall.get("items") or []
    knowledge = state.get("reply_knowledge_use") if isinstance(state.get("reply_knowledge_use"), dict) else {}
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
        "adopted_sequence_id": _text(knowledge.get("sequence_id")),
        "adopted_script_id": _text(knowledge.get("script_id")),
        "store_status": _text(store.get("status") or store.get("match_status")),
        "decision_status": _text(state.get("decision_status")),
        "reply_source": _text(state.get("reply_source")),
        "failure_category": _text(failure.get("category")), "failure_code": _text(failure.get("code")),
    }


def compact_facts(state: dict[str, Any]) -> dict[str, Any]:
    shared = state.get("shared_context") if isinstance(state.get("shared_context"), dict) else {}
    facts = shared.get("authoritative_facts") if isinstance(shared.get("authoritative_facts"), dict) else {}
    return {key: facts[key] for key in ("customer", "orders", "payment", "visible_store_scope", "appointment") if key in facts}


def judge_messages(private: dict[str, Any]) -> list[dict[str, str]]:
    payload = {
        "客户最近历史": private["history"][-10:], "客户本轮消息": private["content"],
        "权威事实": private["facts"], "系统决策": private["summary"],
        "系统回复": private["reply"], "评测场景桶": private["bucket"],
    }
    rubric = """你是销售客服质量评审员。只根据给定客户原话、历史和权威事实评估，不补充业务事实。
输出严格 JSON：{"expected_intent":"7类之一","expected_emotion":"8类之一","pressure_direction_ok":true,"reply_accuracy_score":0,"reply_naturalness_score":0,"mainline_progress_score":0,"follow_sequence_fit_score":null,"closing_fit_score":null,"store_next_step_ok":null,"unsupported_fact":false,"safety_ok":true,"passed":true,"reasons":["简短中文原因"]}
意图只能是 fact_inquiry、blocker_expression、transaction_progress、information_submission、defer、explicit_exit、normal_exchange。情绪只能是 neutral、curious、enthusiastic、hesitant、cold、defensive、impatient、angry。粗口本身不是愤怒；只有明确针对品牌、服务或接待人员的强烈敌意才是 angry。投诉不等于退订。有未解决卡点时应先解卡并暂停逼单；明确退订必须停止营销。门店、预约、付款、效果不得脱离权威事实。passed 需要回复准确且自然、压力合适、安全合规，关键事实无虚构。"""
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
        "unsupported_fact": bool(raw.get("unsupported_fact")), "safety_ok": bool(raw.get("safety_ok")),
        "passed": bool(raw.get("passed")), "reasons": [redact(item, 160) for item in (raw.get("reasons") or [])[:5]],
    }


async def close_runtime(runtime: dict[str, Any]) -> None:
    await runtime["model_client"].aclose()
    await runtime["semantic_client"].aclose()
    await runtime["follow_client"].aclose()
    await runtime["coze_client"].aclose()
    runtime["platform_client"].close()


async def runtime_phase(args: argparse.Namespace, private_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    settings = build_settings(args.output)
    audit: dict[str, Any] = {"blocked_attempts": [], "write_methods_installed": 3}
    runtime = build_runtime(settings, audit)
    samples, distribution = choose_samples(load_candidates(args.days), args.limit)
    if len(samples) < args.limit:
        raise RuntimeError(f"eligible real samples insufficient: {len(samples)} < {args.limit}")
    semaphore = asyncio.Semaphore(max(1, args.concurrency))
    rows: list[dict[str, Any] | None] = [None] * len(samples)
    private_rows: list[dict[str, Any] | None] = [None] * len(samples)
    fatal = asyncio.Event()

    async def one(index: int, sample: dict[str, Any]) -> None:
        if fatal.is_set():
            return
        state = build_state(sample, settings, runtime["policy"])
        started = time.perf_counter()
        try:
            async with semaphore:
                final = await asyncio.wait_for(runtime["graph"].ainvoke(state), timeout=155.0)
                await asyncio.sleep(max(0.0, args.runtime_gap_seconds))
            models = set(model_names(final))
            for client_name in ("model_client", "semantic_client"):
                usage = getattr(runtime[client_name], "last_usage", None)
                if isinstance(usage, dict) and _text(usage.get("model")):
                    models.add(_text(usage.get("model")))
            models = sorted(models)
            non_deepseek = [name for name in models if not name.lower().startswith("deepseek-")]
            if non_deepseek:
                fatal.set()
                raise RuntimeError("non-DeepSeek model observed: " + ",".join(non_deepseek))
            summary = decision_summary(final)
            rows[index] = {
                "case_id": f"C{index + 1:04d}", "identity_hash": sample["identity_hash"],
                "bucket": sample["bucket"], "customer_excerpt": redact(sample["content"], 100),
                "reply_excerpt": redact(reply_text(final), 180), **summary, "model_names": models,
                "duration_ms": int((time.perf_counter() - started) * 1000), "judge": {}, "runtime_error": "",
            }
            private_rows[index] = {
                "case_id": f"C{index + 1:04d}", "bucket": sample["bucket"], "content": sample["content"],
                "history": sample["conversation_history"], "facts": compact_facts(final),
                "summary": summary, "reply": reply_text(final),
            }
        except WriteBlockedError:
            fatal.set()
            audit["blocked_attempts"].append("graph_runtime")
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            if "non-DeepSeek" in message:
                fatal.set()
            rows[index] = {
                "case_id": f"C{index + 1:04d}", "identity_hash": sample["identity_hash"],
                "bucket": sample["bucket"], "customer_excerpt": redact(sample["content"], 100),
                "reply_excerpt": "", "duration_ms": int((time.perf_counter() - started) * 1000),
                "runtime_error": redact(message, 300), "judge": {},
            }
        print(json.dumps({"phase": "runtime", "done": index + 1, "total": len(samples)}, ensure_ascii=False), flush=True)

    await asyncio.gather(*(one(index, sample) for index, sample in enumerate(samples)))
    await close_runtime(runtime)
    clean_rows = [row for row in rows if isinstance(row, dict)]
    if fatal.is_set() or audit["blocked_attempts"]:
        raise RuntimeError("evaluation aborted: non-DeepSeek model or production write attempt")
    with private_path.open("w", encoding="utf-8") as handle:
        for row in private_rows:
            if isinstance(row, dict):
                handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    return clean_rows, {"distribution": distribution, "audit": audit}


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
            raw = await judge.chat_json(judge_messages(private))
            row["judge"] = normalize_judge(raw)
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
    return ordered[min(len(ordered) - 1, max(0, int(len(ordered) * fraction) - 1))]


def build_metrics(rows: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any]:
    completed = [row for row in rows if not row.get("runtime_error")]
    judged = [row for row in completed if row.get("judge")]
    policy = [row for row in completed if row.get("intent") and row.get("emotion") and row.get("closing_action")]
    valid = [row for row in completed if row.get("reply_source") in VALID_REPLY_SOURCES]
    eligible = [row for row in valid if row.get("intent") != "explicit_exit" and (row.get("sequence_candidates") or row.get("script_candidates"))]
    durations = [int(row.get("duration_ms") or 0) for row in completed]
    policy_case_ids = {row.get("case_id") for row in policy}
    judged_policy = [row for row in judged if row.get("case_id") in policy_case_ids]
    return {
        "requested_count": len(rows), "completed_count": len(completed),
        "runtime_error_count": len(rows) - len(completed), "valid_model_reply_count": len(valid),
        "policy_core_coverage": round(len(policy) / len(completed), 4) if completed else 0,
        "degraded_count": sum(row.get("decision_status") == "degraded" for row in completed),
        "failure_codes": dict(Counter(row.get("failure_code") or "none" for row in completed)),
        "reply_sources": dict(Counter(row.get("reply_source") or "exception" for row in rows)),
        "sequence_candidate_count": sum(bool(row.get("sequence_candidates")) for row in completed),
        "script_candidate_count": sum(bool(row.get("script_candidates")) for row in completed),
        "adoption_eligible_count": len(eligible),
        "sequence_adopted_count": sum(bool(row.get("adopted_sequence_id")) for row in eligible),
        "script_adopted_count": sum(bool(row.get("adopted_script_id")) for row in eligible),
        "closing_enter_advance_count": sum(row.get("closing_action") in {"enter", "advance"} for row in valid),
        "judge_count": len(judged), "judge_pass_rate": round(sum(bool(row["judge"].get("passed")) for row in judged) / len(judged), 4) if judged else 0,
        "intent_accuracy_valid_policy": round(sum(row.get("intent") == row["judge"].get("expected_intent") for row in judged_policy) / max(1, len(judged_policy)), 4),
        "emotion_accuracy_valid_policy": round(sum(row.get("emotion") == row["judge"].get("expected_emotion") for row in judged_policy) / max(1, len(judged_policy)), 4),
        "unsupported_fact_count": sum(bool(row["judge"].get("unsupported_fact")) for row in judged),
        "safety_failure_count": sum(not bool(row["judge"].get("safety_ok")) for row in judged),
        "p50_ms": int(statistics.median(durations)) if durations else 0, "p95_ms": percentile(durations, 0.95),
        "sample_distribution": context.get("distribution") or {},
        "model_names": sorted({name for row in completed for name in row.get("model_names") or []}),
        "isolation": {"commit_graph_constructed": False, "public_reply_endpoint_called": False,
                      "production_repository_constructed": False,
                      "blocked_write_attempts": list((context.get("audit") or {}).get("blocked_attempts") or [])},
    }


CSV_FIELDS = [
    "case_id", "identity_hash", "bucket", "customer_excerpt", "reply_excerpt", "reply_source",
    "primary_task", "intent", "emotion", "flow_action", "checkpoint_code", "cardpoint_state",
    "sequence_candidates", "script_candidates", "adopted_sequence_id", "adopted_script_id",
    "closing_action", "closing_sequence_key", "closing_node_key", "customer_state", "store_status",
    "decision_status", "failure_category", "failure_code", "duration_ms", "runtime_error",
    "judge_expected_intent", "judge_expected_emotion", "judge_passed", "judge_reply_accuracy",
    "judge_naturalness", "judge_mainline_progress", "judge_follow_sequence_fit", "judge_closing_fit",
    "judge_store_next_step_ok", "judge_unsupported_fact", "judge_safety_ok", "judge_reasons",
]


def csv_row(row: dict[str, Any]) -> dict[str, Any]:
    judge = row.get("judge") if isinstance(row.get("judge"), dict) else {}
    flat = dict(row)
    flat.update(
        {
            "sequence_candidates": "；".join(row.get("sequence_candidates") or []),
            "script_candidates": "；".join(row.get("script_candidates") or []),
            "judge_expected_intent": judge.get("expected_intent", ""),
            "judge_expected_emotion": judge.get("expected_emotion", ""), "judge_passed": judge.get("passed", ""),
            "judge_reply_accuracy": judge.get("reply_accuracy_score", ""),
            "judge_naturalness": judge.get("reply_naturalness_score", ""),
            "judge_mainline_progress": judge.get("mainline_progress_score", ""),
            "judge_follow_sequence_fit": judge.get("follow_sequence_fit_score", ""),
            "judge_closing_fit": judge.get("closing_fit_score", ""),
            "judge_store_next_step_ok": judge.get("store_next_step_ok", ""),
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
    failures = [row for row in rows if row.get("runtime_error") or not bool((row.get("judge") or {}).get("passed"))]
    lines = ["# 失败与人工复核案例", "", f"共 {len(failures)} 条。", ""]
    for row in failures:
        reason = "；".join((row.get("judge") or {}).get("reasons") or []) or row.get("failure_code") or row.get("runtime_error") or "AI 初评未通过"
        lines += [f"## {row.get('case_id')}｜{row.get('bucket')}", "", f"- 客户消息摘要：{row.get('customer_excerpt', '')}",
                  f"- 回复摘要：{row.get('reply_excerpt', '')}",
                  f"- 系统决策：意图 {row.get('intent', '')}；情绪 {row.get('emotion', '')}；B 单 {row.get('closing_action', '')}",
                  f"- 复核原因：{reason}", ""]
    (output / "failures.md").write_text("\n".join(lines), encoding="utf-8")
    report = [
        "# V3 全链路 DeepSeek 两阶段隔离评测", "",
        "> 运行与评审已分阶段执行；这是 DeepSeek AI 初评，不是业务确认金标。", "",
        f"- 样本：{metrics['requested_count']}；运行异常：{metrics['runtime_error_count']}",
        f"- 主模型/单次修复有效回复：{metrics['valid_model_reply_count']}",
        f"- 完整意图+情绪+B 单覆盖率：{metrics['policy_core_coverage']:.1%}",
        f"- AI 初评通过率：{metrics['judge_pass_rate']:.1%}",
        f"- 有效策略行意图一致率：{metrics['intent_accuracy_valid_policy']:.1%}",
        f"- 有效策略行情绪一致率：{metrics['emotion_accuracy_valid_policy']:.1%}",
        f"- 序列候选/话术候选：{metrics['sequence_candidate_count']}/{metrics['script_candidate_count']}",
        f"- 条件可采用样本：{metrics['adoption_eligible_count']}；采用序列/话术：{metrics['sequence_adopted_count']}/{metrics['script_adopted_count']}",
        f"- B 单 enter/advance：{metrics['closing_enter_advance_count']}",
        f"- P50/P95：{metrics['p50_ms']}/{metrics['p95_ms']} ms", "",
        f"- 回复来源：{json.dumps(metrics['reply_sources'], ensure_ascii=False)}",
        f"- 失败分类：{json.dumps(metrics['failure_codes'], ensure_ascii=False)}",
        f"- 安全失败/无依据事实：{metrics['safety_failure_count']}/{metrics['unsupported_fact_count']}",
        f"- 生产写入尝试：{len(metrics['isolation']['blocked_write_attempts'])}",
        f"- 观测模型：{', '.join(metrics['model_names']) or 'trace 未记录名称'}", "",
        "逐条数据见 `report.csv`，需复核案例见 `failures.md`。",
    ]
    (output / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    (output / "isolation_audit.md").write_text(
        "# 隔离审计\n\n- 未调用公网回复接口。\n- 未构建 commit graph。\n- 未构建生产 Repository。\n"
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
