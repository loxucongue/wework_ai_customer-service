from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.graph import reply_filters, task_state
from app.graph.nodes.common import model_usage_snapshot
from app.graph.nodes.price_question_frames import detect_price_question_frame
from app.graph.nodes.reply_postprocess import OPENING_GUIDANCE_TEXT
from app.graph.state import AgentState
from app.prompts.appointment_reply_synthesizer import build_appointment_reply_messages
from app.prompts.reply_synthesizer import build_forced_reply_messages
from app.prompts.store_reply_synthesizer import build_store_reply_messages
from app.services.appointment_opening_service import book_order_message
from app.services.model_client import ModelClient
from app.services.trace_logger import TraceLogger


@dataclass(frozen=True)
class ReplyCallbacks:
    appointment_reply_payload_for_model: Callable[[AgentState], dict[str, Any]]
    debug_message_contents: Callable[[list[dict[str, Any]]], list[str]]
    forced_reply_satisfies_hard_instruction: Callable[[list[dict[str, Any]], dict[str, Any]], bool]
    json_dumps: Callable[[Any], str]
    model_reply_unsafe: Callable[[AgentState, list[dict[str, Any]]], bool]
    postprocess_reply_messages: Callable[[AgentState, list[dict[str, Any]]], list[dict[str, Any]]]
    reply_forced_payload_for_model: Callable[[AgentState], dict[str, Any]]
    reply_messages_for_model: Callable[[AgentState], list[dict[str, Any]]]
    reply_model_tier: Callable[[AgentState], str]
    reply_repair_messages_for_model: Callable[[AgentState, list[dict[str, Any]]], list[dict[str, Any]]]
    should_use_appointment_fact_reply: Callable[[AgentState], bool]
    should_use_store_fact_reply: Callable[[AgentState], bool]
    should_use_model_reply: Callable[[AgentState], bool]
    store_reply_payload_for_model: Callable[[AgentState], dict[str, Any]]
    validated_model_messages: Callable[[dict[str, Any]], list[dict[str, Any]]]


def create_synthesize_reply_node(
    *,
    trace_logger: TraceLogger,
    model_client: ModelClient | None,
    callbacks: ReplyCallbacks,
):
    async def synthesize_reply(state: AgentState) -> dict[str, Any]:
        with trace_logger.node(
            state,
            "synthesize_reply",
            {"action_plan": state.get("action_plan"), "module_outputs": state.get("module_outputs")},
        ) as span:
            model_call: dict[str, Any] | None = None
            errors = list(state.get("errors", []))
            messages: list[dict[str, Any]] = []

            def _message_text(message: dict[str, Any]) -> str:
                content = message.get("content")
                if isinstance(content, dict):
                    text = content.get("text") or content.get("handoff_reason") or content.get("url") or ""
                    return str(text).strip()
                return str(content or "").strip()

            def _with_repaired_appointment_commitment(input_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
                if not callbacks.should_use_appointment_fact_reply(state):
                    return input_messages
                opening = state.get("tool_results", {}).get("appointment_opening") if isinstance(state.get("tool_results"), dict) else {}
                if isinstance(opening, dict) and opening.get("status") in {"created", "dry_run_created"}:
                    return input_messages
                repaired: list[dict[str, Any]] = []
                for item in input_messages:
                    if not isinstance(item, dict) or item.get("type") != "text":
                        repaired.append(item)
                        continue
                    text = reply_filters.repair_appointment_commitment(_message_text(item))
                    repaired.append({**item, "content": text})
                return repaired

            def _with_passive_opening_rewrite(input_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
                content = "".join(str(state.get("normalized_content") or "").split())
                if not any(term in content for term in ["我已经添加了你", "已经添加了你", "现在我们可以开始聊天了", "可以开始聊天了", "开始聊天"]):
                    return input_messages
                if len(input_messages) != 1 or not isinstance(input_messages[0], dict) or input_messages[0].get("type") != "text":
                    return input_messages
                text = _message_text(input_messages[0])
                compact = "".join(text.split()).strip("。！？!?~～")
                needs_rewrite = (
                    compact in {"您好", "你好", "你好呀", "小贝在的", "你好呀小贝在的"}
                    or len(compact) <= 8
                    or (
                        any(term in text for term in ["城市", "附近区域", "最近的门店", "最近门店"])
                        and not any(term in text for term in ["皮肤改善", "活动价格", "活动", "价格"])
                    )
                    or not any(term in text for term in ["皮肤改善", "活动价格", "附近门店", "门店安排", "想了解", "想先了解"])
                )
                if not needs_rewrite:
                    return input_messages
                return [
                    {"type": "text", "order": 1, "content": {"text": OPENING_GUIDANCE_TEXT}}
                ]

            def _appointment_missing_prompt(payload: dict[str, Any]) -> str:
                opening = payload.get("appointment_opening") if isinstance(payload.get("appointment_opening"), dict) else {}
                missing = opening.get("missing") if isinstance(opening.get("missing"), list) else []
                if not missing:
                    return "你把姓名发我，我继续帮你确认。"
                first_missing = str(missing[0] or "").strip()
                if first_missing == "姓名":
                    return "你把姓名发我，我继续帮你确认。"
                if first_missing == "电话":
                    return "你把电话发我，我继续帮你确认。"
                if first_missing == "门店":
                    return "你告诉我想去哪家店，我继续帮你确认。"
                if first_missing == "到店日期":
                    return "你告诉我想哪天过去，我继续帮你确认。"
                if first_missing == "到店时间":
                    return "你告诉我想几点过去，我继续帮你确认。"
                return f"你把{first_missing}发我，我继续帮你确认。"

            def _appointment_deterministic_text(payload: dict[str, Any]) -> str:
                action = payload.get("appointment_action") if isinstance(payload.get("appointment_action"), dict) else {}
                action_status = str(action.get("status") or "").strip()
                action_operation = str(action.get("operation") or "").strip()
                action_time = str(action.get("appointment_time") or "").strip()
                if action_status in {"scheduled", "dry_run_scheduled"}:
                    time_part = f"{action_time}的" if action_time else ""
                    return f"好的，这边已经继续按{time_part}预约安排往下处理，后续门店会再跟你确认具体到店细节。"
                if action_status in {"changed", "dry_run_changed"}:
                    time_part = f"到{action_time}" if action_time else ""
                    return f"好的，已经帮你把预约时间调整{time_part}，后续以门店确认信息为准。"
                if action_status in {"cancelled", "dry_run_cancelled"} or action_operation == "cancel":
                    time_part = f"{action_time}的" if action_time else ""
                    return f"好的，{time_part}预约我这边已经帮你取消处理了，后面想再过来可以随时跟我说。"
                store_name = str(payload.get("store_name") or "").strip()
                date_label = str(payload.get("visit_date_label") or "").strip()
                preferred_time = str(payload.get("preferred_time") or "").strip()
                preferred_time_available = payload.get("preferred_time_available")
                slots = payload.get("available_time_slots") if isinstance(payload.get("available_time_slots"), list) else []
                error = str(payload.get("available_time_error") or "").strip()
                direct_arrival = bool(payload.get("direct_arrival_question"))
                location_text = f"{store_name}" if store_name else "这家门店"
                date_text = date_label or "这个时间"
                time_text = preferred_time or ""
                when_text = f"{date_text}{time_text}" if time_text else date_text
                if error and not slots:
                    if direct_arrival:
                        return f"{location_text}{date_text}这次还没拿到实时排班结果，先别直接过去，我让门店同事帮你核一下。"
                    return f"{location_text}{date_text}这次还没拿到实时排班结果，我先让门店同事帮你核一下。"
                if preferred_time and preferred_time_available is False:
                    slot_text = "、".join(str(item) for item in slots[:6])
                    if direct_arrival:
                        return f"{when_text}这个时间暂时没看到可约，先别直接过去。现在可选时间有{slot_text}，你看哪个时间方便？"
                    return f"{when_text}这个时间暂时没看到可约。现在可选时间有{slot_text}，你看哪个时间方便？"
                if preferred_time and preferred_time_available is True:
                    return f"{when_text}这个时间目前有空位。{_appointment_missing_prompt(payload)}"
                if slots:
                    slot_text = "、".join(str(item) for item in slots[:6])
                    if direct_arrival:
                        return f"{location_text}{date_text}有空位，可选时间有{slot_text}。先别直接过去，你看哪个时间方便？"
                    return f"{location_text}{date_text}有空位，可约时间段有{slot_text}。你方便哪个时间过来呢？"
                return ""

            async def try_deterministic_appointment_reply(reason: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
                payload = callbacks.appointment_reply_payload_for_model(state)
                fallback_call: dict[str, Any] = {
                    "name": "appointment_deterministic_fallback",
                    "input": {
                        "reason": reason,
                        "preferred_time": payload.get("preferred_time"),
                        "preferred_time_available": payload.get("preferred_time_available"),
                    },
                }
                text = _appointment_deterministic_text(payload)
                if not text:
                    fallback_call["error"] = "deterministic_appointment_text_empty"
                    return [], fallback_call
                candidate = [{"type": "text", "order": 1, "content": {"text": text}}]
                unsafe = callbacks.model_reply_unsafe(state, candidate)
                fallback_call["draft_messages"] = callbacks.debug_message_contents(candidate)
                fallback_call["unsafe"] = unsafe
                if unsafe:
                    fallback_call["quality_rejection"] = state.get("_last_quality_rejection")
                    safer_text = reply_filters.repair_appointment_commitment(text)
                    if safer_text != text:
                        candidate = [{"type": "text", "order": 1, "content": {"text": safer_text}}]
                        unsafe = callbacks.model_reply_unsafe(state, candidate)
                        fallback_call["retry_draft_messages"] = callbacks.debug_message_contents(candidate)
                        fallback_call["retry_unsafe"] = unsafe
                        if unsafe:
                            fallback_call["retry_quality_rejection"] = state.get("_last_quality_rejection")
                if unsafe:
                    fallback_call["error"] = "deterministic_appointment_reply_unsafe"
                    return [], fallback_call
                fallback_call["output"] = {"messages": len(candidate)}
                return candidate, fallback_call

            async def try_forced_reply(tier: str, reason: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
                forced_user_payload = callbacks.reply_forced_payload_for_model(state)
                forced_facts = forced_user_payload.get("fact_brief", {}).get("available_facts", {})
                forced_call: dict[str, Any] = {
                    "name": "reply_forced_fact_model",
                    "input": {
                        "tier": tier,
                        "required": True,
                        "reason": reason,
                        "code_revision": "forced_hard_v2",
                        "hard_instruction": forced_user_payload.get("hard_instruction", ""),
                        "preferred_time_available": forced_facts.get("preferred_time_available")
                        if isinstance(forced_facts, dict)
                        else None,
                    },
                }
                try:
                    if not model_client:
                        raise RuntimeError("model_client_unavailable")
                    forced_payload = await model_client.chat_json(
                        build_forced_reply_messages(forced_user_payload, json_dumps=callbacks.json_dumps),
                        tier=tier,
                    )
                    forced_call["usage"] = model_usage_snapshot(model_client)
                    forced_messages = _with_repaired_appointment_commitment(callbacks.validated_model_messages(forced_payload))
                    forced_call["draft_messages"] = callbacks.debug_message_contents(forced_messages)
                    forced_call["draft_messages_full"] = callbacks.debug_message_contents(forced_messages)
                    forced_unsafe = callbacks.model_reply_unsafe(state, forced_messages) if forced_messages else True
                    forced_call["unsafe"] = forced_unsafe
                    if forced_unsafe:
                        forced_call["quality_rejection"] = state.get("_last_quality_rejection")
                    if forced_messages and (
                        not forced_unsafe
                        or callbacks.forced_reply_satisfies_hard_instruction(forced_messages, forced_user_payload)
                    ):
                        forced_call["output"] = {"messages": len(forced_messages)}
                        return forced_messages, forced_call
                    forced_call["error"] = "forced_reply_still_unsafe_or_empty"
                except Exception as forced_exc:
                    forced_call["error"] = f"{type(forced_exc).__name__}: {forced_exc}"
                return [], forced_call

            async def try_appointment_fact_reply(tier: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
                appointment_payload = callbacks.appointment_reply_payload_for_model(state)
                appointment_call: dict[str, Any] = {
                    "name": "appointment_fact_reply_model",
                    "input": {
                        "tier": tier,
                        "required": True,
                        "preferred_time": appointment_payload.get("preferred_time"),
                        "preferred_time_available": appointment_payload.get("preferred_time_available"),
                        "direct_arrival_question": appointment_payload.get("direct_arrival_question"),
                    },
                }
                try:
                    if not model_client:
                        raise RuntimeError("model_client_unavailable")
                    appointment_model_payload = await model_client.chat_json(
                        build_appointment_reply_messages(appointment_payload, json_dumps=callbacks.json_dumps),
                        tier=tier,
                    )
                    appointment_call["usage"] = model_usage_snapshot(model_client)
                    appointment_messages = _with_repaired_appointment_commitment(callbacks.validated_model_messages(appointment_model_payload))
                    appointment_call["draft_messages"] = callbacks.debug_message_contents(appointment_messages)
                    appointment_unsafe = callbacks.model_reply_unsafe(state, appointment_messages) if appointment_messages else True
                    appointment_call["unsafe"] = appointment_unsafe
                    if appointment_unsafe:
                        appointment_call["quality_rejection"] = state.get("_last_quality_rejection")
                    if appointment_messages and not appointment_unsafe:
                        appointment_call["output"] = {"messages": len(appointment_messages)}
                        return appointment_messages, appointment_call
                    appointment_call["error"] = "appointment_reply_unsafe_or_empty"
                except Exception as appointment_exc:
                    appointment_call["error"] = f"{type(appointment_exc).__name__}: {appointment_exc}"
                return [], appointment_call

            async def try_store_fact_reply(tier: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
                store_payload = callbacks.store_reply_payload_for_model(state)
                store_call: dict[str, Any] = {
                    "name": "store_fact_reply_model",
                    "input": {
                        "tier": tier,
                        "required": True,
                        "city": store_payload.get("store_lookup", {}).get("city", "") if isinstance(store_payload.get("store_lookup"), dict) else "",
                        "stores": len(store_payload.get("stores") or []) if isinstance(store_payload.get("stores"), list) else 0,
                        "recommended_store": bool(store_payload.get("recommended_store")),
                    },
                }
                try:
                    if not model_client:
                        raise RuntimeError("model_client_unavailable")
                    store_model_payload = await model_client.chat_json(
                        build_store_reply_messages(store_payload, json_dumps=callbacks.json_dumps),
                        tier=tier,
                    )
                    store_call["usage"] = model_usage_snapshot(model_client)
                    store_messages = _with_repaired_appointment_commitment(callbacks.validated_model_messages(store_model_payload))
                    store_call["draft_messages"] = callbacks.debug_message_contents(store_messages)
                    store_unsafe = callbacks.model_reply_unsafe(state, store_messages) if store_messages else True
                    store_call["unsafe"] = store_unsafe
                    if store_unsafe:
                        store_call["quality_rejection"] = state.get("_last_quality_rejection")
                    if store_messages and not store_unsafe:
                        store_call["output"] = {"messages": len(store_messages)}
                        return store_messages, store_call
                    store_call["error"] = "store_reply_unsafe_or_empty"
                except Exception as store_exc:
                    store_call["error"] = f"{type(store_exc).__name__}: {store_exc}"
                return [], store_call

            try:
                if not (model_client and model_client.available and callbacks.should_use_model_reply(state)):
                    raise RuntimeError("reply_synthesizer_model_required")
                tier = callbacks.reply_model_tier(state)
                model_call = {"name": "reply_synthesizer_model", "input": {"tier": tier, "required": True}}
                if callbacks.should_use_appointment_fact_reply(state):
                    messages, appointment_call = await try_appointment_fact_reply(tier)
                    model_call.setdefault("nested_calls", []).append(appointment_call)
                    if messages:
                        model_call["fallback"] = "appointment_fact_model_reply"
                if not messages and callbacks.should_use_store_fact_reply(state):
                    messages, store_call = await try_store_fact_reply(tier)
                    model_call.setdefault("nested_calls", []).append(store_call)
                    if messages:
                        model_call["fallback"] = "store_fact_model_reply"
                if not messages:
                    payload = await model_client.chat_json(callbacks.reply_messages_for_model(state), tier=tier)
                    model_call["usage"] = model_usage_snapshot(model_client)
                    messages = _with_repaired_appointment_commitment(callbacks.validated_model_messages(payload))
                    model_call["draft_messages"] = callbacks.debug_message_contents(messages)
                primary_unsafe = callbacks.model_reply_unsafe(state, messages) if messages else True
                if primary_unsafe:
                    model_call["unsafe"] = primary_unsafe
                    model_call["quality_rejection"] = state.get("_last_quality_rejection")
                if not messages or primary_unsafe:
                    repair_call: dict[str, Any] = {"name": "reply_repair_model", "input": {"tier": tier, "required": True}}
                    try:
                        repair_payload = await model_client.chat_json(callbacks.reply_repair_messages_for_model(state, messages), tier=tier)
                        repair_call["usage"] = model_usage_snapshot(model_client)
                        repaired_messages = _with_repaired_appointment_commitment(callbacks.validated_model_messages(repair_payload))
                        repair_call["draft_messages"] = callbacks.debug_message_contents(repaired_messages)
                        repaired_unsafe = callbacks.model_reply_unsafe(state, repaired_messages) if repaired_messages else True
                        repair_call["unsafe"] = repaired_unsafe
                        if repaired_unsafe:
                            repair_call["quality_rejection"] = state.get("_last_quality_rejection")
                        if repaired_messages and not repaired_unsafe:
                            messages = repaired_messages
                            model_call["fallback"] = "repaired_model_reply"
                        else:
                            messages = []
                            repair_call["error"] = "repaired_reply_still_unsafe"
                    except Exception as repair_exc:
                        messages = []
                        repair_call["error"] = f"{type(repair_exc).__name__}: {repair_exc}"
                    model_call.setdefault("nested_calls", []).append(repair_call)
                    if not messages:
                        forced_messages, forced_call = await try_forced_reply(tier, "repair_failed_or_unsafe")
                        model_call.setdefault("nested_calls", []).append(forced_call)
                        if not forced_messages and tier != "balanced":
                            forced_messages, forced_call = await try_forced_reply("balanced", "strong_forced_reply_failed")
                            model_call.setdefault("nested_calls", []).append(forced_call)
                        if not forced_messages and tier != "fast":
                            forced_messages, forced_call = await try_forced_reply("fast", "balanced_forced_reply_failed")
                            model_call.setdefault("nested_calls", []).append(forced_call)
                        if forced_messages:
                            messages = forced_messages
                            model_call["fallback"] = "forced_fact_model_reply"
                        else:
                            model_call["fallback"] = "blocked_without_template_fallback"
                if not messages and callbacks.should_use_appointment_fact_reply(state):
                    deterministic_messages, deterministic_call = await try_deterministic_appointment_reply("model_chain_blocked")
                    model_call.setdefault("nested_calls", []).append(deterministic_call)
                    if deterministic_messages:
                        messages = deterministic_messages
                        model_call["fallback"] = "deterministic_appointment_reply"
                model_call["output"] = {"messages": len(messages)}
            except Exception as exc:
                model_call = model_call or {"name": "reply_synthesizer_model", "input": {}}
                primary_error = f"{type(exc).__name__}: {exc}"
                model_call["primary_error"] = primary_error
                forced_messages, forced_call = await try_forced_reply("balanced", "primary_reply_model_exception")
                model_call.setdefault("nested_calls", []).append(forced_call)
                if not forced_messages:
                    forced_messages, forced_call = await try_forced_reply("fast", "balanced_forced_after_exception_failed")
                    model_call.setdefault("nested_calls", []).append(forced_call)
                if forced_messages:
                    messages = forced_messages
                    model_call["fallback"] = "forced_fact_model_after_exception"
                    model_call["output"] = {"messages": len(messages)}
                else:
                    model_call["error"] = primary_error
                    errors.append(
                        {"node": "synthesize_reply", "message": "final_reply_model_failed", "detail": primary_error}
                    )
                    messages = []
            if messages:
                messages = callbacks.postprocess_reply_messages(state, messages)
            if not messages:
                deterministic_messages: list[dict[str, Any]] = []
                deterministic_call: dict[str, Any] | None = None
                if callbacks.should_use_appointment_fact_reply(state):
                    deterministic_messages, deterministic_call = await try_deterministic_appointment_reply("postprocess_removed_all_messages")
                    if model_call and deterministic_call:
                        model_call.setdefault("nested_calls", []).append(deterministic_call)
                    if deterministic_messages:
                        messages = deterministic_messages
                        if model_call:
                            model_call["fallback"] = "deterministic_appointment_after_postprocess"
                if not messages and model_client and model_client.available:
                    forced_messages, forced_call = await try_forced_reply("balanced", "postprocess_removed_all_messages")
                    if model_call:
                        model_call.setdefault("nested_calls", []).append(forced_call)
                    if forced_messages:
                        messages = forced_messages
                        if model_call:
                            model_call["fallback"] = "forced_fact_model_after_postprocess"
            if messages and callbacks.model_reply_unsafe(state, messages):
                messages = []
                errors.append({"node": "synthesize_reply", "message": "final_reply_failed_quality_gate"})
            if not messages:
                if callbacks.should_use_appointment_fact_reply(state):
                    deterministic_messages, deterministic_call = await try_deterministic_appointment_reply("final_quality_gate_removed_all_messages")
                    if model_call:
                        model_call.setdefault("nested_calls", []).append(deterministic_call)
                    if deterministic_messages:
                        messages = deterministic_messages
                        if model_call:
                            model_call["fallback"] = "deterministic_appointment_after_quality_gate"
                if not messages and model_client and model_client.available:
                    forced_messages, forced_call = await try_forced_reply("balanced", "final_quality_gate_removed_all_messages")
                    if model_call:
                        model_call.setdefault("nested_calls", []).append(forced_call)
                    if forced_messages:
                        messages = forced_messages
                        if model_call:
                            model_call["fallback"] = "forced_fact_model_after_quality_gate"
            if not messages:
                deterministic_general = _deterministic_general_text(state)
                if deterministic_general:
                    messages = [{"type": "text", "order": 1, "content": {"text": deterministic_general}}]
                    if model_call:
                        model_call.setdefault("nested_calls", []).append(
                            {
                                "name": "deterministic_general_fallback",
                                "input": {"reason": "model_chain_empty", "content": state.get("normalized_content")},
                                "output": {"messages": 1},
                            }
                        )
            if messages:
                messages = callbacks.postprocess_reply_messages(state, messages)
            if not messages:
                deterministic_general = _deterministic_general_text(state)
                if deterministic_general:
                    messages = [{"type": "text", "order": 1, "content": {"text": deterministic_general}}]
                    if model_call:
                        model_call.setdefault("nested_calls", []).append(
                            {
                                "name": "deterministic_general_fallback",
                                "input": {"reason": "final_postprocess_removed_all_messages", "content": state.get("normalized_content")},
                                "output": {"messages": 1},
                            }
                        )
            if messages:
                messages = _with_passive_opening_rewrite(messages)
            if messages:
                push_messages = [
                    book_order_message(state.get("tool_results", {}) or {}),
                ]
                for extra_message in push_messages:
                    if not extra_message:
                        continue
                    extra_message = dict(extra_message)
                    extra_message["order"] = len(messages) + 1
                    messages.append(extra_message)
            if model_call:
                span["entry"]["tool_calls"] = [model_call]
            output = {"reply_messages": messages, "errors": errors, "trace": state.get("trace", [])}
            span["output_snapshot"] = output
            return output

    return synthesize_reply


def _deterministic_general_text(state: AgentState) -> str:
    content = str(state.get("normalized_content") or "").strip()
    intents = {str(item.get("intent") or "") for item in state.get("intents", []) if isinstance(item, dict)}
    compact_content = "".join(content.split())
    price_frame = detect_price_question_frame(content)
    lookup = state.get("tool_results", {}).get("store_lookup") if isinstance(state.get("tool_results"), dict) else {}
    store = {}
    if isinstance(lookup, dict):
        recommended = lookup.get("recommended_store")
        if isinstance(recommended, dict):
            store = recommended
        stores = lookup.get("stores")
        if not store and isinstance(stores, list):
            for item in stores:
                if isinstance(item, dict):
                    store = item
                    break
    if intents & {"project_inquiry", "case_request", "image_inquiry", "trust_issue"} and any(
        term in content for term in ["能看到变化", "有效果", "能不能做", "可以吗", "黑色素", "淡斑", "祛斑", "色沉", "暗沉"]
    ):
        return "可以的，这类改善方向大多数都能做，也能看到阶段性变化；我先给你按同类改善参考和实际情况继续帮你判断，不会乱给你承诺。"
    if any(term in content for term in ["报名", "先报名", "帮我登记", "登记一下", "留个名额", "留名额", "保留优惠", "先保留"]):
        city = str(state.get("detected_city") or "").strip()
        store_name = str(state.get("confirmed_store_name") or state.get("store_name") or "").strip()
        if store_name:
            return f"可以的，我先帮你把这个活动名额登记上。你要是就按{store_name}这家来，我这边继续帮你确认到店时间。"
        if city:
            return f"可以的，我先帮你登记这个活动名额。你是在{city}这边对吧？我按最近门店继续帮你安排。"
        return "可以的，我先帮你登记这个活动名额。你是在什么城市或哪一片方便过去？我按最近门店继续帮你安排。"
    if any(term in content for term in ["几点上班", "几点开门", "营业时间", "几点开", "几点关"]):
        name = str(store.get("name") or "").strip() if isinstance(store, dict) else ""
        hours = str(store.get("business_hours") or "").strip() if isinstance(store, dict) else ""
        if hours:
            return f"{name}营业时间一般是{hours}，你要是想过去，我再按这家帮你接着安排。".strip("，")
        return "我们门店一般是早上9点开始接待，具体哪家店定下来后，我再把那家营业时间一起发你。"
    if any(term in content for term in ["地址发我", "发我地址", "把地址发我", "门店地址发我"]):
        name = str(store.get("name") or "").strip() if isinstance(store, dict) else ""
        address = str(store.get("address") or "").strip() if isinstance(store, dict) else ""
        if address:
            return f"{name}地址是{address}。"
    if any(term in content for term in ["有没有停车", "有停车吗", "停车方便吗", "停车场在哪", "停车怎么停"]):
        name = str(store.get("name") or "").strip() if isinstance(store, dict) else ""
        parking = str((store or {}).get("parking") or (store or {}).get("parking_name") or (store or {}).get("parking_address") or "").strip()
        if parking:
            return f"{name}停车这块是方便的，{parking}。".strip("，")
        if store:
            return f"{name}这边停车一般是方便的，你到了按这家门店指引过去就行。"
    if "project_process" in intents:
        return "这类项目一般会先做皮肤状态确认，再做清洁和项目操作，结束后会交代护理重点；到店整体通常40到60分钟左右，项目操作本身多在20到30分钟。"
    if "case_request" in intents:
        return "可以的，我先按你现在这个方向给你发同类改善参考。案例主要是看同类变化趋势，具体变化快慢和次数还是会因人不同。"
    if "trust_issue" in intents and any(term in content for term in ["会不会反弹", "怕反弹", "维持多久", "保持多久", "能维持多久", "能保持多久"]):
        return "不会说刚做完很快就回到原来那样，基础改善和后续跟进这块是有的；后面防晒、护理和生活习惯，会影响维持时间和稳定度。"
    if price_frame == "price_conflict":
        return "你看到的价格和前面说的不一样，多半是活动口径、项目范围、是否含预约金/尾款或适用门店不同；我先按你看到的那条活动口径帮你核清楚。"
    if price_frame == "course_payment":
        return "一般是按当次确认的项目、范围和活动规则来付费，不是一上来就强制把完整安排一次性说死。"
    if price_frame == "single_fee":
        return "这个一般先按单次或单个明确范围来理解，不是整个疗程费用；具体是一边、双侧还是全脸，要看活动图上的范围。"
    if price_frame == "hidden_fee_concern":
        return "这个你放心，确认好的项目、价格和包含项不会临时再加别的费用；如果现场你自己还想加做别的项目，也会提前跟你说清楚，由你自己决定。"
    if price_frame == "deposit_question":
        return "这10元主要是预约登记或活动参与资格确认，不代表项目效果或最终费用已经确定。"
    if compact_content in {"你好", "您好"} and task_state.is_active_appointment_task(state):
        active_task = state.get("active_task") or {}
        store_name = str(active_task.get("known_slots", {}).get("store_name") or state.get("confirmed_store_name") or "").strip() if isinstance(active_task, dict) else ""
        if store_name:
            return f"你好呀，前面你这边的预约安排我还接着跟进着。现在要是继续按{store_name}这家推进，我帮你把时间和登记信息接着确认。"
        return "你好呀，前面你的预约安排我这边还接着跟进着。你现在是想继续确认时间，还是先把门店给你定下来？"
    if intents & {"price_inquiry", "ad_price_check", "campaign_inquiry"} and any(
        term in content for term in ["多少钱", "什么价格", "价格", "费用", "要多少"]
    ):
        history = "\n".join(str(item) for item in state.get("conversation_history", [])[-6:])
        has_context = any(term in history for term in ["我在", "机场", "附近", "门店", "黑色素", "斑", "淡斑", "祛斑"])
        if has_context:
            return "这个方向我先不乱报数字，避免把活动口径和项目范围说混；你看今天还是明天方便到店，我帮你按实际项目和活动价核清楚。"
        return "这个价格我先不乱报数字，得按具体项目、活动口径和包含项核清楚，避免把局部价或体验价说混。"
    if "store_inquiry" in intents and any(term in content for term in ["门店名字", "门店名称", "门店叫什么", "叫什么"]):
        return "可以的，你告诉我在哪个城市或哪个区域，我就按当地门店给你发准确名称和地址。"
    return ""
