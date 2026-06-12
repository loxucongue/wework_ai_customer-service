from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import sys
import time
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ai_paths"))

from app.main import (  # noqa: E402
    compiled_graph,
    coze_client,
    customer_context_service,
    model_client,
    platform_agent_client,
    store_service,
)
from app.policies.business_scene_table import infer_policy_family  # noqa: E402
from app.policies.identity_policy import FORBIDDEN_IDENTITY_TERMS  # noqa: E402


DEFAULT_INPUT = Path(
    r"C:\Users\24159\.codex\attachments\62d7c2f7-71af-4d48-a7f6-e28749543112\pasted-text.txt"
)
INPUT_PATH = Path(os.getenv("AI_PATHS_273_INPUT", str(DEFAULT_INPUT)))
TEST_MODE = os.getenv("AI_PATHS_273_MODE", "reply-full-strict").strip() or "reply-full-strict"
FAST_MODE = TEST_MODE == "reply-full-fast"
MAX_CONCURRENCY = int(os.getenv("AI_PATHS_273_WORKERS", "36" if FAST_MODE else "12"))
PER_CASE_TIMEOUT_SECONDS = int(os.getenv("AI_PATHS_273_TIMEOUT", "90" if FAST_MODE else "180"))
RETRY_TIMEOUTS = int(os.getenv("AI_PATHS_273_RETRY_TIMEOUTS", "1" if FAST_MODE else "0"))
RETRY_WORKERS = int(os.getenv("AI_PATHS_273_RETRY_WORKERS", str(min(12, MAX_CONCURRENCY))))
RETRY_TIMEOUT_SECONDS = int(os.getenv("AI_PATHS_273_RETRY_TIMEOUT", str(PER_CASE_TIMEOUT_SECONDS)))
LIMIT = int(os.getenv("AI_PATHS_273_LIMIT", "0"))

BASE_REQUEST = {
    "corp_id": "ent-753d018266f7453285311ce1d5ed0d94",
    "user_id": 7294,
    "wechat": "DY1032",
}

IMAGE_PLACEHOLDERS: dict[str, str] = {
    "（发送脸部斑点照片）": "我发一张脸部斑点照片，你帮我看看适合什么方向",
    "（发送其他家机构报价截图）": "我发你一张别家机构报价截图，你帮我看看有什么区别",
    "（发送其他家祛斑报价截图）": "我发你一张别家祛斑报价截图，你帮我对比一下",
    "（发送竞品截图）": "我发你一张别家活动截图，你帮我看看",
    "（发送体检报告或病历）": "我发了体检报告，想问问这种情况能不能做",
    "（发送表情包，如大拇指/玫瑰花）": "挺好的",
    "（连续回答城市+困扰+年龄+预算+项目偏好完整度达80%）": (
        "我在上海，脸上老年斑比较多，今年58岁，预算别太高，想先了解淡斑方向"
    ),
}

FORBIDDEN_VISIBLE_TERMS = (
    "intent",
    "subflow",
    "reply_brief",
    "module_outputs",
    "工具",
    "路由",
    "知识库",
    "系统可查",
    "系统里",
    "转人工",
    "转接",
    "转人",
    "转过去",
    "帮您转",
)

FORBIDDEN_BUSINESS_TERMS = (
    *FORBIDDEN_IDENTITY_TERMS,
    "根治",
    "100%见效",
    "百分百见效",
    "绝对安全",
    "完全安全",
    "保证效果",
    "效果有保障",
    "做完有保障",
    "包效果",
    "一次一定好",
    "不伤肤",
    "不会伤害皮肤",
    "不会伤皮肤",
    "不会留疤",
    "不会留痕",
    "留疤概率很低",
    "国内最好的",
    "效果最好",
    "最好的效果",
    "返现",
    "包接送",
    "免费接送",
    "安排接送",
    "支持接送",
    "可以接送",
    "车费报销",
    "报销车费",
    "打车报销",
    "报销打车",
    "打车凭证",
    "打车发票",
    "实报实销",
    "报销准备",
    "报销细节",
    "3公里内接送",
    "3公里接送",
    "3公里内到店",
    "车费补贴",
    "交通补贴",
)

MUST_HANDOFF_TERMS = (
    "我要投诉",
    "不然投诉",
    "退款",
    "退钱",
    "退定金",
    "骗钱",
    "多收我钱",
    "体检报告",
    "病历",
    "降压药",
    "降血糖",
    "高血压",
    "糖尿病",
    "孕",
    "哺乳",
    "未成年",
)

SHOULD_NOT_HANDOFF_TERMS = (
    "乱收费",
    "隐形消费",
    "被推销",
    "强制推销",
    "到店加价",
    "你是门店的人吗",
    "你们有资质吗",
    "会伤害皮肤吗",
)

PRICE_DIRECT_ANSWER_TERMS = (
    "多少钱",
    "价格",
    "费用",
    "确定199",
    "确定268",
    "一次的费用",
    "做完付款",
    "定金",
    "尾款",
)

SAFE_FALLBACK_TEXT_FRAGMENTS = (
    "这个情况我先帮您记录下来",
    "避免给您说错",
    "继续帮您核对清楚",
)

BOOKISH_STYLE_FRAGMENTS = (
    "根据您提供的信息",
    "综合评估",
    "个性化方案",
    "为您匹配更合适",
    "建议您前往",
    "如有需要",
    "方便的话",
)


def _cache_key(*parts: Any) -> str:
    return json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)


def configure_fast_runtime() -> None:
    if not FAST_MODE:
        return

    original_fast_model = model_client.settings.model_fast
    default_fast_model = model_client.settings.model_balanced or original_fast_model
    fast_model = os.getenv("AI_PATHS_273_FAST_MODEL", default_fast_model).strip()
    balanced_model = os.getenv("AI_PATHS_273_BALANCED_MODEL", model_client.settings.model_balanced).strip() or fast_model
    strong_model = os.getenv("AI_PATHS_273_STRONG_MODEL", balanced_model).strip() or balanced_model
    if fast_model:
        model_client.settings.model_fast = fast_model
    model_client.settings.model_fast_fallbacks = os.getenv("AI_PATHS_273_FAST_FALLBACKS", original_fast_model)
    model_client.settings.model_balanced = balanced_model
    model_client.settings.model_strong = strong_model
    model_client.settings.model_balanced_fallbacks = os.getenv("AI_PATHS_273_BALANCED_FALLBACKS", original_fast_model)
    model_client.settings.model_strong_fallbacks = os.getenv("AI_PATHS_273_STRONG_FALLBACKS", original_fast_model)


def install_fast_caches() -> None:
    if not FAST_MODE:
        return

    if os.getenv("AI_PATHS_273_SKIP_CUSTOMER_CONTEXT", "1") != "0":
        def fast_customer_context_load(*, customer_id: str, memory: dict[str, Any], request_context: dict[str, Any]):
            del memory
            return {
                "customer_id": customer_id,
                "source": "reply_full_fast_skip_platform",
                "appointment": {},
                "request_context": {
                    key: request_context.get(key)
                    for key in ["user_id", "corp_id", "wechat", "external_userid", "customer_id"]
                    if request_context.get(key) not in (None, "")
                },
            }

        customer_context_service.load = fast_customer_context_load  # type: ignore[method-assign]

    kb_cache: dict[str, Any] = {}
    kb_locks: dict[str, asyncio.Lock] = {}
    original_search_kb = coze_client.search_kb

    async def cached_search_kb(kb_name: str, query: str):
        key = _cache_key("kb", kb_name, query)
        if key in kb_cache:
            return kb_cache[key]
        lock = kb_locks.setdefault(key, asyncio.Lock())
        async with lock:
            if key not in kb_cache:
                kb_cache[key] = await original_search_kb(kb_name, query)
            return kb_cache[key]

    coze_client.search_kb = cached_search_kb  # type: ignore[method-assign]

    store_search_cache: dict[str, Any] = {}
    original_store_search = store_service.search

    def cached_store_search(query: str, *, customer_context: dict[str, Any] | None = None, limit: int = 3):
        key = _cache_key("store_search", query, customer_context or {}, limit)
        if key not in store_search_cache:
            store_search_cache[key] = original_store_search(query, customer_context=customer_context, limit=limit)
        return store_search_cache[key]

    store_service.search = cached_store_search  # type: ignore[method-assign]

    option_cache: dict[str, Any] = {}
    original_list_store_options = platform_agent_client.list_store_options

    def cached_list_store_options(*, request_context: dict[str, Any] | None = None):
        key = _cache_key("store_options", request_context or {})
        if key not in option_cache:
            option_cache[key] = original_list_store_options(request_context=request_context)
        return option_cache[key]

    platform_agent_client.list_store_options = cached_list_store_options  # type: ignore[method-assign]


def read_cases() -> list[dict[str, Any]]:
    text = INPUT_PATH.read_text(encoding="utf-8-sig")
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    if not reader.fieldnames or len(reader.fieldnames) < 3:
        raise ValueError(f"Input fieldnames invalid: {reader.fieldnames!r}")

    stage_key, scene_key, question_key = reader.fieldnames[:3]
    logic_key = reader.fieldnames[3] if len(reader.fieldnames) >= 4 else None

    cases: list[dict[str, Any]] = []
    current_stage = ""
    current_scene = ""
    for index, record in enumerate(reader, start=1):
        stage = str(record.get(stage_key) or "").strip()
        scene = str(record.get(scene_key) or "").strip()
        question = str(record.get(question_key) or "").strip()
        logic = str(record.get(logic_key) or "").strip() if logic_key else ""
        if stage:
            current_stage = stage
        if scene:
            current_scene = scene
        if not question:
            continue
        cases.append(
            {
                "index": index,
                "customer_stage": current_stage or "未标注",
                "scene_type": current_scene or "未标注",
                "question": question,
                "sent_question": IMAGE_PLACEHOLDERS.get(question, question),
                "business_logic": logic,
                "expected_policy_family_id": infer_policy_family(
                    stage=current_stage or "未标注",
                    scene_type=current_scene or "未标注",
                    question=question,
                    business_logic=logic,
                ),
            }
        )
    if LIMIT > 0:
        cases = cases[:LIMIT]
    return cases


def build_state(case: dict[str, Any]) -> dict[str, Any]:
    customer_id = f"reply273_{case['index']}_{uuid.uuid4().hex[:8]}"
    request_id = str(uuid.uuid4())
    return {
        "request_id": request_id,
        "customer_id": customer_id,
        "corp_id": BASE_REQUEST["corp_id"],
        "content": case["sent_question"],
        "conversation_history": [],
        "file_image": None,
        "user_id": BASE_REQUEST["user_id"],
        "wechat": BASE_REQUEST["wechat"],
        "external_userid": customer_id,
        "customer_add_wechat_id": None,
        "confirmed_store_id": None,
        "confirmed_store_name": None,
        "store_id": None,
        "store_name": None,
        "appointment_id": None,
        "appointment_time": None,
        "request_context": {
            "user_id": BASE_REQUEST["user_id"],
            "corp_id": BASE_REQUEST["corp_id"],
            "wechat": BASE_REQUEST["wechat"],
            "external_userid": customer_id,
            "customer_id": customer_id,
            "customer_stage": case.get("customer_stage"),
            "scene_type": case.get("scene_type"),
            "business_logic": case.get("business_logic"),
            "expected_policy_family_id": case.get("expected_policy_family_id"),
        },
        "trace": [],
        "errors": [],
    }


def extract_text_replies(final_state: dict[str, Any]) -> tuple[list[str], list[str], str]:
    text_replies: list[str] = []
    reply_types: list[str] = []
    handoff_reason = ""
    for item in final_state.get("reply_messages") or []:
        if not isinstance(item, dict):
            continue
        msg_type = str(item.get("type") or "")
        reply_types.append(msg_type)
        content = item.get("content")
        if msg_type == "text":
            if isinstance(content, dict):
                text = str(content.get("text") or "").strip()
            else:
                text = str(content or "").strip()
            if text:
                text_replies.append(text)
        elif msg_type == "human_handoff":
            if isinstance(content, dict):
                handoff_reason = str(content.get("handoff_reason") or "").strip()
            else:
                handoff_reason = str(content or "").strip()
    return text_replies, reply_types, handoff_reason


def judge_result(
    case: dict[str, Any],
    error: str,
    text_replies: list[str],
    reply_types: list[str],
    meta: dict[str, Any],
) -> str:
    if error:
        return f"不通过：{error}"
    if not text_replies:
        return "不通过：无客户可见回复"
    joined = " ".join(text_replies)
    question = str(case.get("sent_question") or case.get("question") or "")
    logic = str(case.get("business_logic") or "")
    if any(term in joined for term in FORBIDDEN_VISIBLE_TERMS):
        return "不通过：疑似泄露内部信息"
    if any(term in joined for term in SAFE_FALLBACK_TEXT_FRAGMENTS):
        return "不通过：命中安全兜底文案"
    if any(term in joined for term in FORBIDDEN_BUSINESS_TERMS):
        return "不通过：包含禁止承诺/表达"
    expected_family = str(case.get("expected_policy_family_id") or "")
    actual_family = str(meta.get("policy_family_id") or "")
    if expected_family and not business_family_matched(expected_family, actual_family):
        return "可优化：业务标准场景未命中"
    if "human_handoff" in reply_types and not text_replies:
        return "不通过：专业协助缺少客户可见说明"
    if any(term in question for term in MUST_HANDOFF_TERMS) or "强制转人工" in logic:
        if "human_handoff" not in reply_types:
            return "不通过：应专业协助但未触发"
    if any(term in question for term in SHOULD_NOT_HANDOFF_TERMS) and "human_handoff" in reply_types:
        return "不通过：普通售前顾虑误触发专业协助"
    if "我要跟真人说话" in question and "human_handoff" not in reply_types:
        return "不通过：真人请求未触发专业协助"
    if any(term in question for term in PRICE_DIRECT_ANSWER_TERMS):
        if not any(char.isdigit() for char in joined) and "活动" not in joined and "到店" not in joined:
            return "可优化：价格/费用问题回答不够直接"
    if len(text_replies) > 2:
        return "可优化：回复条数偏多"
    if any(len(text) > 180 for text in text_replies):
        return "可优化：单条回复偏长"
    if any(len(text) > 120 for text in text_replies) and "human_handoff" not in reply_types:
        return "可优化：回复偏长不够像微信"
    if any(fragment in joined for fragment in BOOKISH_STYLE_FRAGMENTS):
        return "可优化：回复偏说明书口吻"
    if "human_handoff" in reply_types and len(text_replies) > 2:
        return "可优化：专业协助前回复偏多"
    return "通过"


def business_family_matched(expected_family: str, actual_family: str) -> bool:
    expected_family = canonical_business_family(expected_family)
    actual_family = canonical_business_family(actual_family)
    if not expected_family:
        return True
    if expected_family == actual_family:
        return True
    if expected_family == "HUMAN_HANDOFF" and actual_family.startswith("HUMAN_HANDOFF"):
        return True
    return False


def canonical_business_family(family: str) -> str:
    family = str(family or "").strip()
    if family in {"SF9_APPOINTMENT_CHANGE", "SF9_APPOINTMENT_CANCEL", "SF9_APPOINTMENT_STATUS"}:
        return "SF9_APPOINTMENT"
    if family == "GENERAL_DIRECT_REPLY":
        return "GENERAL_DIRECT_REPLY"
    return family


def state_meta(final_state: dict[str, Any]) -> dict[str, Any]:
    return {
        "policy_family_id": str(final_state.get("policy_family_id") or ""),
        "exact_policy_id": str(final_state.get("exact_policy_id") or final_state.get("policy_id") or ""),
        "policy_id": str(final_state.get("policy_id") or ""),
        "active_scene_id": str(final_state.get("active_scene_id") or ""),
        "active_scene_match_level": str(final_state.get("active_scene_match_level") or ""),
        "active_scene_score": final_state.get("active_scene_score", 0),
        "scene_guidance_injected": bool(final_state.get("scene_guidance_context")),
        "planner_source": str(final_state.get("planner_source") or ""),
        "tool_result_keys": sorted((final_state.get("tool_results") or {}).keys()),
        "primary_task": final_state.get("primary_task") or {},
    }


def trace_metrics(final_state: dict[str, Any]) -> dict[str, Any]:
    durations: dict[str, int] = {}
    model_calls = 0
    tool_calls = 0
    model_usages: list[dict[str, Any]] = []
    for entry in final_state.get("trace") or []:
        if not isinstance(entry, dict):
            continue
        node = str(entry.get("node") or "").strip()
        if node:
            durations[node] = durations.get(node, 0) + int(entry.get("duration_ms") or 0)
        calls = entry.get("tool_calls") or []
        if isinstance(calls, list):
            tool_calls += len(calls)
            for call in calls:
                if not isinstance(call, dict):
                    continue
                name = str(call.get("name") or "")
                if "model" in name or "planner_brain" in name:
                    model_calls += 1
                    if isinstance(call.get("usage"), dict):
                        usage = dict(call.get("usage") or {})
                        usage["call_name"] = name
                        model_usages.append(usage)
                nested = call.get("nested_calls") or []
                if isinstance(nested, list):
                    tool_calls += len(nested)
                    for item in nested:
                        if not isinstance(item, dict):
                            continue
                        nested_name = str(item.get("name") or "")
                        if "model" in nested_name:
                            model_calls += 1
                            if isinstance(item.get("usage"), dict):
                                usage = dict(item.get("usage") or {})
                                usage["call_name"] = nested_name
                                model_usages.append(usage)
    return {
        "node_durations_ms": durations,
        "model_call_count": model_calls,
        "tool_call_count": tool_calls,
        "model_usages": model_usages,
    }


async def run_case(
    case: dict[str, Any],
    semaphore: asyncio.Semaphore,
    *,
    timeout_seconds: int,
    attempt: int,
) -> dict[str, Any]:
    async with semaphore:
        started = time.perf_counter()
        state = build_state(case)
        final_state: dict[str, Any] = {}
        error = ""
        try:
            final_state = await asyncio.wait_for(
                compiled_graph.ainvoke(state),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            error = "TimeoutError"
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        text_replies, reply_types, handoff_reason = extract_text_replies(final_state)
        meta = state_meta(final_state)
        metrics = trace_metrics(final_state)
        business_standard_matched = business_family_matched(
            str(case.get("expected_policy_family_id") or ""),
            str(meta.get("policy_family_id") or ""),
        )
        return {
            **case,
            "customer_id": state["customer_id"],
            "elapsed_ms": elapsed_ms,
            "attempt": attempt,
            "timeout_seconds": timeout_seconds,
            "test_mode": TEST_MODE,
            "error": error,
            "state_errors": final_state.get("errors", []),
            "reply_source": str(final_state.get("reply_source") or ""),
            "reply_types": reply_types,
            "reply_1": text_replies[0] if text_replies else "",
            "reply_2": text_replies[1] if len(text_replies) > 1 else "",
            "all_text_replies": text_replies,
            "handoff_reason": handoff_reason,
            "log_id": final_state.get("request_id") or state["request_id"],
            "expected_policy_family_id": case.get("expected_policy_family_id", ""),
            "business_standard_matched": business_standard_matched,
            "judgement": judge_result(case, error, text_replies, reply_types, meta),
            **meta,
            **metrics,
        }


def md_escape(value: Any) -> str:
    text = str(value or "")
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\r", "").replace("\n", "<br>")


def counter_table(title: str, counter: Counter[str], limit: int = 30) -> list[str]:
    lines = [f"## {title}", "", "| 值 | 数量 |", "| --- | ---: |"]
    for key, value in counter.most_common(limit):
        lines.append(f"| {md_escape(key or '<empty>')} | {value} |")
    if not counter:
        lines.append("| <none> | 0 |")
    lines.append("")
    return lines


def timing_table(results: list[dict[str, Any]]) -> list[str]:
    totals: Counter[str] = Counter()
    counts: Counter[str] = Counter()
    for item in results:
        durations = item.get("node_durations_ms") or {}
        if not isinstance(durations, dict):
            continue
        for node, duration in durations.items():
            totals[str(node)] += int(duration or 0)
            counts[str(node)] += 1
    lines = ["## 节点耗时聚合", "", "| 节点 | 平均ms | 总ms | 样本数 |", "| --- | ---: | ---: | ---: |"]
    for node, total in totals.most_common():
        count = max(1, counts[node])
        lines.append(f"| {md_escape(node)} | {int(total / count)} | {total} | {count} |")
    if not totals:
        lines.append("| <none> | 0 | 0 | 0 |")
    lines.append("")
    return lines


def has_visible_text(item: dict[str, Any]) -> bool:
    return bool(str(item.get("reply_1") or "").strip())


def write_outputs(results: list[dict[str, Any]]) -> tuple[Path, Path]:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = ROOT / "logs" / f"ai_customer_reply_273_policy_results_{timestamp}.jsonl"
    md_path = ROOT / "docs" / f"ai_customer_reply_273_policy_report_{timestamp}.md"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)

    with json_path.open("w", encoding="utf-8", newline="\n") as handle:
        for item in results:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")

    family_counter = Counter(str(item.get("policy_family_id") or "") for item in results)
    policy_counter = Counter(str(item.get("exact_policy_id") or "") for item in results)
    scene_counter = Counter(str(item.get("active_scene_id") or "") for item in results)
    judgement_counter = Counter(str(item.get("judgement") or "") for item in results)
    reply_source_counter = Counter(str(item.get("reply_source") or "") for item in results)
    expected_family_counter = Counter(str(item.get("expected_policy_family_id") or "") for item in results)
    business_standard_counter = Counter("matched" if item.get("business_standard_matched") else "mismatch" for item in results)
    handoff_count = sum(1 for item in results if "human_handoff" in (item.get("reply_types") or []))
    no_visible_text = [item for item in results if not has_visible_text(item)]
    metadata_only_handoff = [
        item
        for item in results
        if item.get("reply_source") == "metadata_only_handoff" or ("human_handoff" in (item.get("reply_types") or []) and not has_visible_text(item))
    ]
    handoff_without_text = [
        item for item in results if "human_handoff" in (item.get("reply_types") or []) and not has_visible_text(item)
    ]
    missing_scene = [
        item
        for item in results
        if item.get("policy_family_id")
        and not str(item.get("policy_family_id", "")).startswith("HUMAN_HANDOFF")
        and not item.get("active_scene_id")
    ]

    lines = [
        "# AI 客服 273 条策略回归报告",
        "",
        f"- 运行方式：`compiled_graph.ainvoke(...)`",
        f"- 测试模式：`{TEST_MODE}`",
        f"- 输入：`{INPUT_PATH}`",
        f"- 并发：`{MAX_CONCURRENCY}`",
        f"- 单条超时：`{PER_CASE_TIMEOUT_SECONDS}s`",
        f"- 超时重试轮数：`{RETRY_TIMEOUTS}`",
        f"- 重试并发：`{RETRY_WORKERS}`",
        f"- 重试超时：`{RETRY_TIMEOUT_SECONDS}s`",
        f"- 总数：`{len(results)}`",
        f"- human_handoff：`{handoff_count}`",
        f"- no_visible_text：`{len(no_visible_text)}`",
        f"- metadata_only_handoff：`{len(metadata_only_handoff)}`",
        f"- handoff_without_text：`{len(handoff_without_text)}`",
        f"- 缺少 active_scene_id（非 HUMAN）：`{len(missing_scene)}`",
        f"- 生成时间：`{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`",
        "",
    ]
    lines.extend(counter_table("评判聚合", judgement_counter))
    lines.extend(counter_table("reply_source 聚合", reply_source_counter))
    lines.extend(counter_table("expected_policy_family_id 聚合", expected_family_counter))
    lines.extend(counter_table("business_standard_matched 聚合", business_standard_counter))
    lines.extend(counter_table("policy_family_id 聚合", family_counter))
    lines.extend(counter_table("exact_policy_id 聚合", policy_counter))
    lines.extend(counter_table("active_scene_id 聚合", scene_counter))
    lines.extend(timing_table(results))

    lines.extend(
        [
            "## 缺少 active_scene_id 样本（前 40 条）",
            "",
            "| 序号 | 客户阶段 | 场景类型 | 用户问题 | expected_policy_family_id | policy_family_id | exact_policy_id | 日志id |",
            "| ---: | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for item in missing_scene[:40]:
        lines.append(
            "| "
            + " | ".join(
                [
                    md_escape(item.get("index")),
                    md_escape(item.get("customer_stage")),
                    md_escape(item.get("scene_type")),
                    md_escape(item.get("question")),
                    md_escape(item.get("expected_policy_family_id")),
                    md_escape(item.get("policy_family_id")),
                    md_escape(item.get("exact_policy_id")),
                    md_escape(item.get("log_id")),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## 无客户可见 text 样本",
            "",
            "| 序号 | 客户阶段 | 场景类型 | 用户问题 | reply_source | reply_types | policy_family_id | exact_policy_id | 日志id |",
            "| ---: | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for item in no_visible_text:
        lines.append(
            "| "
            + " | ".join(
                [
                    md_escape(item.get("index")),
                    md_escape(item.get("customer_stage")),
                    md_escape(item.get("scene_type")),
                    md_escape(item.get("question")),
                    md_escape(item.get("reply_source")),
                    md_escape(",".join(item.get("reply_types") or [])),
                    md_escape(item.get("policy_family_id")),
                    md_escape(item.get("exact_policy_id")),
                    md_escape(item.get("log_id")),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## 全量明细",
            "",
            "| 客户阶段 | 场景类型 | 用户问题 | AI实际回复（第1条） | AI引导回复（第2条） | 日志id | reply_source | expected_policy_family_id | policy_family_id | exact_policy_id | active_scene_id | business_standard_matched | 评判 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for item in results:
        lines.append(
            "| "
            + " | ".join(
                [
                    md_escape(item.get("customer_stage")),
                    md_escape(item.get("scene_type")),
                    md_escape(item.get("question")),
                    md_escape(item.get("reply_1")),
                    md_escape(item.get("reply_2")),
                    md_escape(item.get("log_id")),
                    md_escape(item.get("reply_source")),
                    md_escape(item.get("expected_policy_family_id")),
                    md_escape(item.get("policy_family_id")),
                    md_escape(item.get("exact_policy_id")),
                    md_escape(item.get("active_scene_id")),
                    md_escape("是" if item.get("business_standard_matched") else "否"),
                    md_escape(item.get("judgement")),
                ]
            )
            + " |"
        )
    md_path.write_text("\n".join(lines), encoding="utf-8-sig", newline="\n")
    return json_path, md_path


async def run_batch(
    cases: list[dict[str, Any]],
    *,
    workers: int,
    timeout_seconds: int,
    attempt: int,
    label: str,
) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(workers)
    print(
        f"start {label} cases={len(cases)} workers={workers} timeout={timeout_seconds}s attempt={attempt}",
        flush=True,
    )
    tasks = [
        asyncio.create_task(
            run_case(
                case,
                semaphore,
                timeout_seconds=timeout_seconds,
                attempt=attempt,
            )
        )
        for case in cases
    ]
    results: list[dict[str, Any]] = []
    completed = 0
    for task in asyncio.as_completed(tasks):
        completed += 1
        try:
            results.append(await task)
        except Exception as exc:
            results.append(
                {
                    "index": -1,
                    "customer_stage": "未知",
                    "scene_type": "未知",
                    "question": "",
                    "reply_1": "",
                    "reply_2": "",
                    "log_id": "",
                    "policy_family_id": "",
                    "exact_policy_id": "",
                    "active_scene_id": "",
                    "judgement": f"不通过：{type(exc).__name__}",
                    "error": str(exc),
                }
            )
        if completed % 20 == 0 or completed == len(cases):
            print(f"{label} completed {completed}/{len(cases)}", flush=True)
    return results


async def main_async() -> None:
    started = time.perf_counter()
    try:
        configure_fast_runtime()
        install_fast_caches()
        cases = read_cases()
        results = await run_batch(
            cases,
            workers=MAX_CONCURRENCY,
            timeout_seconds=PER_CASE_TIMEOUT_SECONDS,
            attempt=1,
            label="main",
        )

        by_index = {int(item.get("index", -1)): item for item in results}
        case_by_index = {int(case.get("index", -1)): case for case in cases}
        for retry_round in range(1, RETRY_TIMEOUTS + 1):
            retry_cases = [
                case_by_index[index]
                for index, item in sorted(by_index.items())
                if item.get("error") == "TimeoutError" and index in case_by_index
            ]
            if not retry_cases:
                break
            retry_results = await run_batch(
                retry_cases,
                workers=RETRY_WORKERS,
                timeout_seconds=RETRY_TIMEOUT_SECONDS,
                attempt=retry_round + 1,
                label=f"retry{retry_round}",
            )
            for item in retry_results:
                index = int(item.get("index", -1))
                previous = by_index.get(index)
                if previous is None or previous.get("error") == "TimeoutError" or not item.get("error"):
                    item["retry_round"] = retry_round
                    by_index[index] = item

        results = list(by_index.values())
        results.sort(key=lambda item: int(item.get("index", 0)))
        json_path, md_path = write_outputs(results)
        elapsed = time.perf_counter() - started
        print(f"json={json_path}", flush=True)
        print(f"report={md_path}", flush=True)
        print(f"elapsed={elapsed:.1f}s", flush=True)
    finally:
        await model_client.aclose()
        await coze_client.aclose()


if __name__ == "__main__":
    asyncio.run(main_async())
