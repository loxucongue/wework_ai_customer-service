from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.config import Settings
from app.graph.nodes.reply_context import reply_user_payload_for_model
from app.graph.nodes.reply_nodes import _reply_retry_messages
from app.graph.nodes.reply_validation import validate_reply_consistency, validated_model_messages
from app.graph.planner.brain_v2 import run_planner_brain_v2
from app.policies.sales_flow import precision_qa_index_for_gate, sales_mainline_for_model
from app.prompts.reply_synthesizer import build_reply_messages
from app.prompts.sop_chat_gate import build_sop_chat_gate_messages, build_sop_chat_gate_repair_messages
from app.services.model_client import ModelClient
from app.services.runtime_budget import build_runtime_budget
from app.services.sop_execution_service import _chat_gate_output_violations, _sop_summary
from app.services.sop_reply_pack_service import SopReplyPackService


SCORE_KEYS = (
    "current_question",
    "history_continuity",
    "business_accuracy",
    "sales_progression",
    "human_tone",
    "fact_safety",
)

TRANSIENT_MODEL_ERROR_MARKERS = (
    "http 429",
    "http 500",
    "http 502",
    "http 503",
    "http 504",
    "timeout",
    "timed out",
    "connecterror",
    "connection reset",
    "connection error",
    "networkerror",
)

def _is_transient_model_error(exc: Exception) -> bool:
    detail = f"{type(exc).__name__}:{exc}".lower()
    return any(marker in detail for marker in TRANSIENT_MODEL_ERROR_MARKERS)


async def _call_with_transient_retry(
    factory: Any,
    *,
    attempts: int,
    timeout_seconds: float,
    retry_delay_seconds: float = 1.5,
) -> Any:
    last_error: Exception | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return await asyncio.wait_for(factory(), timeout=max(0.001, float(timeout_seconds)))
        except Exception as exc:  # noqa: BLE001 - model providers return heterogeneous errors
            last_error = exc
            if attempt >= attempts or not _is_transient_model_error(exc):
                raise
            await asyncio.sleep(min(6.0, max(0.0, retry_delay_seconds) * attempt))
    raise RuntimeError(f"model call failed without an exception: {last_error}")


def _is_infrastructure_error(value: str) -> bool:
    lowered = str(value or "").lower()
    return (
        value.startswith(("planner.error:", "reply.error:", "gate.error:", "review.error:"))
        and any(marker in lowered for marker in TRANSIENT_MODEL_ERROR_MARKERS)
    )


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    output = deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(output.get(key), dict):
            output[key] = _deep_merge(output[key], value)
        else:
            output[key] = deepcopy(value)
    return output


def _percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def _message_types(messages: list[dict[str, Any]]) -> list[str]:
    return [str(item.get("type") or "") for item in messages if isinstance(item, dict)]


def _visible_text(messages: list[dict[str, Any]]) -> str:
    values: list[str] = []
    for item in messages:
        if not isinstance(item, dict) or str(item.get("type") or "") != "text":
            continue
        content = item.get("content")
        if isinstance(content, dict):
            content = content.get("text") or content.get("content") or ""
        values.append(str(content or ""))
    return "\n".join(values)


def _store_ids(messages: list[dict[str, Any]]) -> list[str]:
    output: list[str] = []
    for item in messages:
        if not isinstance(item, dict) or str(item.get("type") or "") != "store_address":
            continue
        content = item.get("content") if isinstance(item.get("content"), dict) else {}
        store_id = str(content.get("store_id") or "").strip()
        if store_id:
            output.append(store_id)
    return output


def _payment_amounts(messages: list[dict[str, Any]]) -> list[int]:
    output: list[int] = []
    for item in messages:
        if not isinstance(item, dict) or str(item.get("type") or "") != "payment_collection":
            continue
        content = item.get("content") if isinstance(item.get("content"), dict) else {}
        try:
            output.append(int(content.get("amount") or 0))
        except (TypeError, ValueError):
            output.append(0)
    return output


def _planner_checks(plan: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    decision = str(plan.get("planner_decision") or "")
    decisions = [str(item) for item in expected.get("decisions") or []]
    if decisions and decision not in decisions:
        errors.append(f"planner.decision expected={decisions} actual={decision}")
    tool_names = [str(item.get("name") or "") for item in plan.get("planner_tool_calls") or [] if isinstance(item, dict)]
    for name in expected.get("required_tools") or []:
        if str(name) not in tool_names:
            errors.append(f"planner.missing_tool:{name}")
    for name in expected.get("forbidden_tools") or []:
        if str(name) in tool_names:
            errors.append(f"planner.forbidden_tool:{name}")
    types = _message_types(plan.get("planner_reply_messages") or [])
    for value in expected.get("required_message_types") or []:
        if str(value) not in types:
            errors.append(f"planner.missing_message_type:{value}")
    for value in expected.get("forbidden_message_types") or []:
        if str(value) in types:
            errors.append(f"planner.forbidden_message_type:{value}")
    return errors


def _reply_checks(messages: list[dict[str, Any]], expected: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    types = _message_types(messages)
    text = _visible_text(messages)
    if not messages:
        errors.append("reply.empty")
    for value in expected.get("required_types") or []:
        if str(value) not in types:
            errors.append(f"reply.missing_type:{value}")
    for value in expected.get("forbidden_types") or []:
        if str(value) in types:
            errors.append(f"reply.forbidden_type:{value}")
    for phrase in expected.get("forbidden_phrases") or []:
        if str(phrase) and str(phrase) in text:
            errors.append(f"reply.forbidden_phrase:{phrase}")
    expected_ids = [str(value) for value in expected.get("required_store_ids") or []]
    actual_ids = _store_ids(messages)
    for store_id in expected_ids:
        if store_id not in actual_ids:
            errors.append(f"reply.missing_store_id:{store_id}")
    if expected.get("payment_amount") is not None:
        expected_amount = int(expected["payment_amount"])
        amounts = _payment_amounts(messages)
        if amounts != [expected_amount]:
            errors.append(f"reply.payment_amount expected={[expected_amount]} actual={amounts}")
    return errors


def _base_state(settings: Settings, message: str, history: list[str]) -> dict[str, Any]:
    return {
        "content": message,
        "normalized_content": message,
        "conversation_history": list(history),
        "request_context": {
            "category_id": "S10N",
            "memory_persist_allowed": False,
            "test_isolated": True,
            "corp_id": "test-corp",
            "wechat": "TEST001",
            "external_userid": "test-external",
            "customer_id": "test-customer",
        },
        "customer_id": "test-customer",
        "external_userid": "test-external",
        "corp_id": "test-corp",
        "wechat": "TEST001",
        "runtime_budget": build_runtime_budget(settings),
    }


def _normalize_state_patch(patch: dict[str, Any]) -> dict[str, Any]:
    output = deepcopy(patch)
    summary = output.pop("sent_message_summary", None)
    if isinstance(summary, dict) and isinstance(summary.get("case_image_delivery"), dict):
        delivery = summary["case_image_delivery"]
        output.setdefault("history_events", []).append(
            {
                "event_id": "fixture-case-image",
                "event_type": "case_image_sent",
                "created_at": delivery.get("last_sent_at") or "2026-07-22T10:00:00+08:00",
                "facts": {"image_urls": ["https://test.by4dev.4ba.cn/cases/spot-01.jpg"]},
            }
        )
    return output


def _reply_case_for_message(case: dict[str, Any], message: Any) -> tuple[dict[str, Any], str]:
    if not isinstance(message, dict):
        return case, str(message)
    content = str(message.get("content") or "")
    overrides = {key: deepcopy(value) for key, value in message.items() if key != "content"}
    resolved = _deep_merge(deepcopy(case), overrides)
    return resolved, content


async def _run_reply_case(
    *,
    case: dict[str, Any],
    message: str,
    run_id: str,
    settings: Settings,
    client: ModelClient,
    semaphore: asyncio.Semaphore,
    attempts: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    async with semaphore:
        state = _base_state(settings, message, case.get("history") or [])
        state = _deep_merge(state, _normalize_state_patch(case.get("state_patch") or {}))
        plan: dict[str, Any] = {}
        planner_call: dict[str, Any] = {}
        planner_ms = 0
        reply_ms = 0
        errors: list[str] = []
        raw_reply: dict[str, Any] = {}
        reply_attempts: list[dict[str, Any]] = []
        reply_messages: list[dict[str, Any]] = []
        reply_payload: dict[str, Any] = {}
        started = time.perf_counter()
        try:
            plan, planner_call = await _call_with_transient_retry(
                lambda: _run_planner_for_matrix(state, client),
                attempts=attempts,
                timeout_seconds=timeout_seconds,
            )
            planner_ms = int((time.perf_counter() - started) * 1000)
            errors.extend(_planner_checks(plan, case.get("expected_planner") or {}))
        except Exception as exc:  # noqa: BLE001 - report model/runtime failures verbatim
            planner_ms = int((time.perf_counter() - started) * 1000)
            errors.append(f"planner.error:{type(exc).__name__}:{exc}")
        if plan:
            reply_state = {**state, **plan}
            if _planner_requested_tools(plan) and isinstance(case.get("reply_fact_envelope"), dict):
                reply_state["fact_envelope"] = deepcopy(case["reply_fact_envelope"])
            reply_payload = reply_user_payload_for_model(reply_state)
            started = time.perf_counter()
            try:
                model_messages = build_reply_messages(
                    reply_payload,
                    json_dumps=lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":")),
                )
                raw_reply = await _call_with_transient_retry(
                    lambda: client.chat_json(
                        model_messages,
                        tier="reply",
                        temperature=0.45,
                    ),
                    attempts=attempts,
                    timeout_seconds=timeout_seconds,
                )
                try:
                    reply_messages = validated_model_messages(raw_reply, state=reply_state)
                    validate_reply_consistency(reply_messages, reply_state)
                    reply_attempts.append({"raw_json_output": raw_reply, "status": "accepted"})
                except Exception as validation_exc:  # noqa: BLE001 - mirror production reply repair
                    reply_attempts.append(
                        {
                            "raw_json_output": raw_reply,
                            "status": "validation_failed",
                            "error": f"{type(validation_exc).__name__}: {validation_exc}",
                        }
                    )
                    retry_messages = _reply_retry_messages(model_messages, validation_exc)
                    raw_reply = await _call_with_transient_retry(
                        lambda: client.chat_json(
                            retry_messages,
                            tier="reply",
                            temperature=0.45,
                        ),
                        attempts=attempts,
                        timeout_seconds=timeout_seconds,
                    )
                    reply_messages = validated_model_messages(raw_reply, state=reply_state)
                    validate_reply_consistency(reply_messages, reply_state)
                    reply_attempts.append({"raw_json_output": raw_reply, "status": "repair_accepted"})
                reply_ms = int((time.perf_counter() - started) * 1000)
                errors.extend(_reply_checks(reply_messages, case.get("expected_reply") or {}))
            except Exception as exc:  # noqa: BLE001
                reply_ms = int((time.perf_counter() - started) * 1000)
                errors.append(f"reply.error:{type(exc).__name__}:{exc}")
        return {
            "run_id": run_id,
            "group_id": case["id"],
            "category": case.get("category") or "",
            "current_message": message,
            "conversation_history": case.get("history") or [],
            "semantic_goal": case.get("semantic_goal") or "",
            "planner_plan": plan,
            "planner_call": planner_call,
            "reply_payload": reply_payload,
            "raw_reply": raw_reply,
            "reply_attempts": reply_attempts,
            "reply_messages": reply_messages,
            "planner_ms": planner_ms,
            "reply_ms": reply_ms,
            "hard_errors": errors,
        }


def _planner_requested_tools(plan: dict[str, Any]) -> bool:
    if str(plan.get("planner_decision") or "") != "need_tools":
        return False
    return any(
        isinstance(item, dict) and str(item.get("name") or "").strip()
        for item in plan.get("planner_tool_calls") or []
    )


def _planner_result_has_transient_recovery_failure(result: tuple[dict[str, Any], dict[str, Any]]) -> bool:
    plan, model_call = result
    details = " ".join(
        str(value or "")
        for value in (
            model_call.get("initial_error"),
            model_call.get("error"),
            *[
                call.get("error")
                for call in model_call.get("nested_calls") or []
                if isinstance(call, dict)
            ],
        )
    ).lower()
    has_transient_error = any(marker in details for marker in TRANSIENT_MODEL_ERROR_MARKERS)
    if not has_transient_error:
        return False
    if str(plan.get("planner_sub_rule_id") or "") == "PLANNER_SYSTEM_UNAVAILABLE":
        return True
    violations = plan.get("tool_policy_violations") or []
    if any(
        isinstance(item, dict)
        and str(item.get("missing") or "") in {"need_tools_requires_executable_tool"}
        for item in violations
    ):
        return True
    # Match production behavior: a recovered Planner plan with policy
    # violations is still passed to Reply, where hard facts and customer-visible
    # text are validated or repaired. The matrix should fail only when Planner
    # cannot produce a usable plan at all, not when it produces a plan plus
    # repair guidance.
    return False


async def _run_planner_for_matrix(state: dict[str, Any], client: ModelClient) -> tuple[dict[str, Any], dict[str, Any]]:
    result = await run_planner_brain_v2(state, client)
    if _planner_result_has_transient_recovery_failure(result):
        raise RuntimeError("planner transient timeout/502 recovery left structural violations")
    return result


def _gate_selector_input(
    *,
    case: dict[str, Any],
    message: str,
    packs_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    candidates = [packs_by_id[pack_id] for pack_id in case.get("candidates") or [] if pack_id in packs_by_id]
    return {
        "current_message": message,
        "recent_conversation": case.get("history") or [],
        "mainline": sales_mainline_for_model(),
        "mainline_progress": {
            "completed_pack_ids": case.get("completed_ids") or [],
            "completed_categories": case.get("completed_categories") or [],
        },
        "precision_qa_index": precision_qa_index_for_gate(),
        "unfinished_sops": [_sop_summary(pack, customer_memory={}, customer_context={}) for pack in candidates],
    }


def _gate_checks(output: dict[str, Any], case: dict[str, Any], selector_input: dict[str, Any]) -> list[str]:
    errors = list(_chat_gate_output_violations(output, selector_input))
    routes = [str(item) for item in case.get("expected_routes") or []]
    route = str(output.get("route") or "")
    if routes and route not in routes:
        errors.append(f"gate.route expected={routes} actual={route}")
    expected_pack = str(case.get("expected_pack") or "")
    if expected_pack and str(output.get("sop_pack_id") or "") != expected_pack:
        errors.append(f"gate.pack expected={expected_pack} actual={output.get('sop_pack_id')}")
    expected_priority = str(case.get("expected_priority") or "")
    if expected_priority and str(output.get("priority_question_id") or "") != expected_priority:
        errors.append(
            f"gate.priority expected={expected_priority} actual={output.get('priority_question_id')}"
        )
    return errors


async def _run_gate_case(
    *,
    case: dict[str, Any],
    message: str,
    run_id: str,
    client: ModelClient,
    packs_by_id: dict[str, dict[str, Any]],
    semaphore: asyncio.Semaphore,
    attempts: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    async with semaphore:
        selector_input = _gate_selector_input(case=case, message=message, packs_by_id=packs_by_id)
        started = time.perf_counter()
        output: dict[str, Any] = {}
        errors: list[str] = []
        repaired = False
        try:
            raw = await _call_with_transient_retry(
                lambda: client.chat_json(
                    build_sop_chat_gate_messages(selector_input), tier="reply", temperature=0
                ),
                attempts=attempts,
                timeout_seconds=timeout_seconds,
            )
            output = raw if isinstance(raw, dict) else {}
            violations = _chat_gate_output_violations(output, selector_input)
            if violations:
                repaired_raw = await _call_with_transient_retry(
                    lambda: client.chat_json(
                        build_sop_chat_gate_repair_messages(selector_input, output, violations),
                        tier="reply",
                        temperature=0,
                    ),
                    attempts=attempts,
                    timeout_seconds=timeout_seconds,
                )
                output = repaired_raw if isinstance(repaired_raw, dict) else {}
                repaired = True
            errors.extend(_gate_checks(output, case, selector_input))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"gate.error:{type(exc).__name__}:{exc}")
        return {
            "run_id": run_id,
            "group_id": case["id"],
            "current_message": message,
            "conversation_history": case.get("history") or [],
            "semantic_goal": case.get("semantic_goal") or "",
            "selector_input": selector_input,
            "gate_output": output,
            "repaired": repaired,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "hard_errors": errors,
        }


def _review_messages(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    items = [
        {
            "run_id": row["run_id"],
            "current_message": row["current_message"],
            "conversation_history": row["conversation_history"],
            "semantic_goal": row["semantic_goal"],
            "planner_decision": {
                "decision": row.get("planner_plan", {}).get("planner_decision"),
                "payment_decision": row.get("planner_plan", {}).get("payment_decision"),
                "sales_progression": row.get("planner_plan", {}).get("sales_progression"),
                "precision_qa_decision": row.get("planner_plan", {}).get("precision_qa_decision"),
            },
            "reply_messages": row.get("reply_messages") or [],
            "tool_facts": (row.get("reply_payload") or {}).get("tool_facts") or {},
            "transaction_facts": (row.get("reply_payload") or {}).get("transaction_facts") or {},
            "business_rules": (row.get("reply_payload") or {}).get("business_rules") or {},
            "precision_qa_playbook": (row.get("reply_payload") or {}).get("precision_qa_playbook") or {},
            "hard_errors": row.get("hard_errors") or [],
        }
        for row in rows
    ]
    system = """
你是独立的中文微信销售回复质检模型。按每个 case 的 semantic_goal 和完整历史评估最终回复，不做关键词命中评分。
事实依据包括 tool_facts、transaction_facts、当前 business_rules，以及 precision_qa_playbook.selected_question 中的精准回复业务边界；这些字段中的当前价格、项目范围、年龄边界、预约金与退款政策是已提供事实，不能因为聊天历史未逐字出现就判成编造。
reply_messages 是最终结构化消息数组；如果数组里有 {"type":"payment_collection"}，就代表本轮确实发了收款卡，不要再说“文本提到卡片但没有可验证卡片”。如果数组里有 {"type":"store_address"}，就代表本轮确实发了真实门店卡，但仍要检查文本是否承诺了未经工具证明的“最近/更方便/本地有店”。
客户口头说“付款成功/付好了”不是权威已付事实；回复不能说到账、核实成功或支付已确认。但如果回复只是顺着客户声明继续收姓名电话/到店意向，同时没有重复发卡、没有说已正式排期，可以按事实安全通过，最多扣表达分。
痘印、痘坑属于当前线上淡斑活动改善范围，不能判为不支持项目。不支持线上预约的项目（除皱、祛眼袋、黑眼圈、水光等）必须先明确拒绝该项目预约，不能发 payment_collection；若同时用封闭式问题询问客户是否也有斑点需求，可视为合适的轻主线承接，不应因没有强成交闭环而直接判失败。
门店场景不要强迫销售话术主动说“这不代表最近/距离未知”这类负面免责声明；只要回复没有承诺最近、更近、方便、车程、公里或分钟，并且真实发出 store_address，就不能因未声明负面边界扣到 3 分以下。若评论判断“整体可上线/基本合格”，各项分数应至少为 4。
每项1到5分：current_question、history_continuity、business_accuracy、sales_progression、human_tone、fact_safety。
4分表示可以上线，5分表示优秀。任何事实编造、金额/卡片错误、空回复、答非所问、违反明确禁止项，都必须 overall_pass=false。
只输出 JSON：{"evaluations":[{"run_id":"...","current_question":1,"history_continuity":1,"business_accuracy":1,"sales_progression":1,"human_tone":1,"fact_safety":1,"overall_pass":false,"issues":["..."],"comment":"..."}]}
""".strip()
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(items, ensure_ascii=False, separators=(",", ":"))},
    ]


async def _review_reply_rows(
    rows: list[dict[str, Any]],
    client: ModelClient,
    semaphore: asyncio.Semaphore,
    attempts: int,
    timeout_seconds: float,
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}

    async def review_batch(batch: list[dict[str, Any]]) -> None:
        async with semaphore:
            try:
                raw = await _call_with_transient_retry(
                    lambda: client.chat_json(
                        _review_messages(batch), tier="balanced", temperature=0
                    ),
                    attempts=attempts,
                    timeout_seconds=timeout_seconds,
                )
                for item in raw.get("evaluations") or []:
                    if isinstance(item, dict) and str(item.get("run_id") or ""):
                        item["review_status"] = "completed"
                        output[str(item["run_id"])] = item
            except Exception as exc:  # noqa: BLE001
                for row in batch:
                    output[row["run_id"]] = {
                        "review_status": "unavailable",
                        "overall_pass": None,
                        "issues": [f"review_unavailable:{type(exc).__name__}:{exc}"],
                    }

    await asyncio.gather(*(review_batch(rows[offset : offset + 2]) for offset in range(0, len(rows), 2)))
    return output


async def _run(args: argparse.Namespace) -> int:
    fixture = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    selected_groups = {str(value) for value in args.group or [] if str(value).strip()}
    settings = Settings(_env_file=args.env_file)
    if args.test_model:
        settings = settings.model_copy(
            update={
                "model_fast": args.test_model,
                "model_fast_fallbacks": "",
                "model_planner": args.test_model,
                "model_planner_fallbacks": "",
                "model_balanced": args.test_model,
                "model_balanced_fallbacks": "",
                "model_reply": args.test_model,
                "model_reply_fallbacks": "",
            }
        )
    client = ModelClient(settings)
    reviewer_settings = settings.model_copy(
        update={"model_balanced": args.reviewer_model, "model_balanced_fallbacks": "gpt-5.4-mini"}
    )
    reviewer = ModelClient(reviewer_settings)
    effective_concurrency = max(1, min(20, args.concurrency))
    effective_attempts = max(1, min(3, args.attempts))
    semaphore = asyncio.Semaphore(effective_concurrency)
    reply_jobs = []
    for case in fixture.get("reply_cases") or []:
        if selected_groups and str(case.get("id") or "") not in selected_groups:
            continue
        for index, message in enumerate(case.get("messages") or [], start=1):
            resolved_case, current_message = _reply_case_for_message(case, message)
            run_id = f"reply:{case['id']}:{index}"
            reply_jobs.append(
                _run_reply_case(
                    case=resolved_case,
                    message=current_message,
                    run_id=run_id,
                    settings=settings,
                    client=client,
                    semaphore=semaphore,
                    attempts=effective_attempts,
                    timeout_seconds=args.call_timeout_seconds,
                )
            )
            if args.limit and len(reply_jobs) >= args.limit:
                break
        if args.limit and len(reply_jobs) >= args.limit:
            break
    reply_rows = list(await asyncio.gather(*reply_jobs))

    pack_config = SopReplyPackService(settings).load()
    packs_by_id = {
        str(pack.get("id") or ""): pack
        for pack in pack_config.get("packs") or []
        if isinstance(pack, dict) and str(pack.get("id") or "")
    }
    gate_jobs = []
    for case in fixture.get("gate_cases") or []:
        if selected_groups and str(case.get("id") or "") not in selected_groups:
            continue
        for index, message in enumerate(case.get("messages") or [], start=1):
            run_id = f"gate:{case['id']}:{index}"
            gate_jobs.append(
                _run_gate_case(
                    case=case,
                    message=str(message),
                    run_id=run_id,
                    client=client,
                    packs_by_id=packs_by_id,
                    semaphore=semaphore,
                    attempts=effective_attempts,
                    timeout_seconds=args.call_timeout_seconds,
                )
            )
            if args.limit and len(gate_jobs) >= args.limit:
                break
        if args.limit and len(gate_jobs) >= args.limit:
            break
    gate_rows = list(await asyncio.gather(*gate_jobs))

    review_semaphore = asyncio.Semaphore(effective_concurrency)
    reviewable_rows = [
        row
        for row in reply_rows
        if not any(_is_infrastructure_error(error) for error in row.get("hard_errors") or [])
    ]
    reviews = await _review_reply_rows(
        reviewable_rows,
        reviewer,
        review_semaphore,
        effective_attempts,
        args.call_timeout_seconds,
    )
    reply_passed = 0
    reply_hard_passed = 0
    reply_semantic_reviewed = 0
    reply_review_unavailable = 0
    for row in reply_rows:
        review = reviews.get(row["run_id"], {})
        row["semantic_review"] = review
        hard_pass = not row["hard_errors"]
        row["hard_pass"] = hard_pass
        reply_hard_passed += int(hard_pass)
        review_status = str(review.get("review_status") or "unavailable")
        scores = [int(review.get(key) or 0) for key in SCORE_KEYS]
        if review_status == "completed":
            reply_semantic_reviewed += 1
            semantic_pass = bool(scores) and min(scores) >= 4 and bool(review.get("overall_pass"))
            row["semantic_status"] = "passed" if semantic_pass else "failed"
            row["passed"] = hard_pass and semantic_pass
            reply_passed += int(row["passed"])
        else:
            reply_review_unavailable += 1
            row["semantic_status"] = "review_unavailable"
            row["passed"] = None
    gate_passed = 0
    for row in gate_rows:
        row["passed"] = not row["hard_errors"]
        gate_passed += int(row["passed"])

    reply_infrastructure_failures = sum(
        1
        for row in reply_rows
        if any(_is_infrastructure_error(error) for error in row.get("hard_errors") or [])
    )
    gate_infrastructure_failures = sum(
        1
        for row in gate_rows
        if any(_is_infrastructure_error(error) for error in row.get("hard_errors") or [])
    )
    reply_evaluable = reply_semantic_reviewed
    gate_evaluable = max(0, len(gate_rows) - gate_infrastructure_failures)

    planner_times = [int(row.get("planner_ms") or 0) for row in reply_rows]
    reply_times = [int(row.get("reply_ms") or 0) for row in reply_rows if int(row.get("reply_ms") or 0) > 0]
    gate_times = [int(row.get("duration_ms") or 0) for row in gate_rows]
    summary = {
        "fixture_version": fixture.get("version"),
        "reply_runs": len(reply_rows),
        "reply_passed": reply_passed,
        "reply_pass_rate": round(reply_passed / reply_evaluable, 4) if reply_evaluable else None,
        "reply_hard_passed": reply_hard_passed,
        "reply_hard_pass_rate": round(reply_hard_passed / len(reply_rows), 4) if reply_rows else 0,
        "reply_semantic_reviewed": reply_semantic_reviewed,
        "reply_review_unavailable": reply_review_unavailable,
        "reply_infrastructure_failures": reply_infrastructure_failures,
        "reply_evaluable_runs": reply_evaluable,
        "reply_evaluable_pass_rate": round(reply_passed / reply_evaluable, 4) if reply_evaluable else None,
        "gate_runs": len(gate_rows),
        "gate_passed": gate_passed,
        "gate_pass_rate": round(gate_passed / len(gate_rows), 4) if gate_rows else 0,
        "gate_infrastructure_failures": gate_infrastructure_failures,
        "gate_evaluable_runs": gate_evaluable,
        "gate_evaluable_pass_rate": round(gate_passed / gate_evaluable, 4) if gate_evaluable else 0,
        "planner_p50_ms": int(statistics.median(planner_times)) if planner_times else 0,
        "planner_p90_ms": _percentile(planner_times, 0.9),
        "reply_p50_ms": int(statistics.median(reply_times)) if reply_times else 0,
        "reply_p90_ms": _percentile(reply_times, 0.9),
        "gate_p50_ms": int(statistics.median(gate_times)) if gate_times else 0,
        "gate_p90_ms": _percentile(gate_times, 0.9),
        "reviewer_model": args.reviewer_model,
        "test_model_override": args.test_model,
        "requested_concurrency": args.concurrency,
        "effective_concurrency": effective_concurrency,
        "effective_attempts": effective_attempts,
        "call_timeout_seconds": args.call_timeout_seconds,
        "same_model_review": bool(args.test_model and args.test_model == args.reviewer_model),
        "acceptance_ready": bool(
            reply_rows
            and reply_semantic_reviewed == len(reply_rows)
            and reply_passed == len(reply_rows)
            and gate_passed == len(gate_rows)
        ),
    }
    Path(args.report).write_text(
        json.dumps({"summary": summary, "reply_rows": reply_rows, "gate_rows": gate_rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    hard_failed = reply_hard_passed != len(reply_rows) or gate_passed != len(gate_rows)
    semantic_failed = reply_semantic_reviewed > 0 and reply_passed != reply_semantic_reviewed
    return 1 if hard_failed or semantic_failed else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--report", required=True)
    parser.add_argument("--reviewer-model", default="gpt-5.4")
    parser.add_argument("--test-model", default="")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--call-timeout-seconds", type=float, default=55.0)
    parser.add_argument("--group", action="append", default=[])
    parser.add_argument("--limit", type=int, default=0)
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
