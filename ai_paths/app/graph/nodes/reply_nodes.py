from __future__ import annotations

from typing import Any, Callable

from app.graph.nodes.common import model_usage_snapshot
from app.graph.planner.runtime_plan import planner_handoff
from app.graph.state import AgentState
from app.services.model_client import ModelClient
from app.services.trace_logger import TraceLogger


FINAL_REPLY_MODEL_NAMES = ["deepseek-v4-flash"]
FINAL_REPLY_JSON_FORMAT = {"type": "json_object"}
FINAL_REPLY_TEMPERATURE = 0.25


def create_synthesize_reply_node(
    *,
    trace_logger: TraceLogger,
    model_client: ModelClient | None,
    debug_message_contents: Callable[[list[dict[str, Any]]], list[str]],
    model_reply_unsafe: Callable[[AgentState, list[dict[str, Any]]], bool],
    postprocess_reply_messages: Callable[[AgentState, list[dict[str, Any]]], list[dict[str, Any]]],
    reply_messages_for_model: Callable[[AgentState], list[dict[str, Any]]],
    reply_model_tier: Callable[[AgentState], str],
    should_use_model_reply: Callable[[AgentState], bool],
    validated_model_messages: Callable[[dict[str, Any]], list[dict[str, Any]]],
):
    async def synthesize_reply(state: AgentState) -> dict[str, Any]:
        with trace_logger.node(
            state,
            "synthesize_reply",
            {"fact_envelope": state.get("fact_envelope"), "required_tools": state.get("required_tools")},
        ) as span:
            model_call: dict[str, Any] | None = None
            errors = list(state.get("errors", []))
            messages: list[dict[str, Any]] = []
            reply_source = "main_model"
            pending_model_errors: list[dict[str, Any]] = []

            try:
                if not (model_client and model_client.available and should_use_model_reply(state)):
                    raise RuntimeError("reply_synthesizer_model_required")

                tier = reply_model_tier(state)
                model_call = {"name": "reply_synthesizer_model", "input": {"tier": tier, "required": True}}

                payload = await model_client.chat_json(
                    reply_messages_for_model(state),
                    tier=tier,
                    temperature=FINAL_REPLY_TEMPERATURE,
                    model_names=FINAL_REPLY_MODEL_NAMES,
                    response_format=FINAL_REPLY_JSON_FORMAT,
                )
                model_call["usage"] = model_usage_snapshot(model_client)
                messages = validated_model_messages(payload)
                model_call["draft_messages"] = debug_message_contents(messages)
                if messages:
                    messages = postprocess_reply_messages(state, messages)
                    model_call["postprocessed_messages"] = debug_message_contents(messages)

                model_call["output"] = {"messages": len(messages)}
            except Exception as exc:
                model_call = model_call or {"name": "reply_synthesizer_model", "input": {}}
                primary_error = f"{type(exc).__name__}: {exc}"
                model_call["error"] = primary_error
                pending_model_errors.append(
                    {
                        "node": "synthesize_reply",
                        "message": "final_reply_model_failed",
                        "detail": primary_error,
                    }
                )
                messages = []

            if messages and model_reply_unsafe(state, messages):
                errors.append({"node": "synthesize_reply", "message": "final_reply_failed_quality_gate"})

            if not _has_customer_visible_text(messages):
                errors.extend(pending_model_errors)
                errors.append({"node": "synthesize_reply", "message": "customer_visible_reply_unavailable"})
                messages, reply_source = _safe_visible_fallback_messages(state)

            if model_call:
                span["entry"]["tool_calls"] = [model_call]
            recovered_errors = []
            if pending_model_errors and reply_source in {"safe_text_fallback", "safe_handoff_fallback"}:
                errors.extend(error for error in pending_model_errors if error not in errors)
            output = {
                "reply_messages": messages,
                "reply_source": reply_source,
                "postprocess_changed": bool(state.get("postprocess_changed")),
                "postprocess_reasons": state.get("postprocess_reasons", []),
                "errors": errors,
                "recovered_errors": recovered_errors,
                "trace": state.get("trace", []),
            }
            span["output_snapshot"] = output
            return output

    return synthesize_reply


def _has_customer_visible_text(messages: list[dict[str, Any]]) -> bool:
    for message in messages:
        if not isinstance(message, dict) or message.get("type") != "text":
            continue
        content = message.get("content")
        if isinstance(content, dict):
            text = str(content.get("text") or "").strip()
        else:
            text = str(content or "").strip()
        if text:
            return True
    return False


def _safe_visible_fallback_messages(state: AgentState) -> tuple[list[dict[str, Any]], str]:
    handoff = planner_handoff(state)
    reason = str(handoff.get("reason") or "").strip() or "最终回复生成失败，需要专业同事核对"
    if _fallback_needs_handoff(state, handoff):
        text = _canonical_scene_reply(state) or "我先帮您对接专业同事继续跟您说，您稍等一下。"
        return (
            [
                {"type": "text", "order": 1, "content": {"text": text}},
                {"type": "human_handoff", "order": 2, "content": {"handoff_reason": reason}},
            ],
            "safe_handoff_fallback",
        )

    text = _safe_text_fallback(state)
    return ([{"type": "text", "order": 1, "content": {"text": text}}], "safe_text_fallback")


def _fallback_needs_handoff(state: AgentState, handoff: dict[str, Any]) -> bool:
    if bool(handoff.get("needed")):
        return True
    policy_family = str(state.get("policy_family_id") or "")
    if policy_family.startswith("HUMAN_HANDOFF"):
        return True
    primary_task = state.get("primary_task") if isinstance(state.get("primary_task"), dict) else {}
    policy_hint = str(primary_task.get("policy_hint") or "").upper()
    return policy_hint.startswith("HUMAN_HANDOFF") or policy_hint.startswith("HUMAN_REQUEST")


def _safe_text_fallback(state: AgentState) -> str:
    canonical = _canonical_scene_reply(state)
    if canonical:
        return canonical
    policy_family = str(state.get("policy_family_id") or "")
    exact_policy = str(state.get("exact_policy_id") or "")
    sop_stage = str(state.get("sop_stage") or "")
    if exact_policy == "SF7_OLD_CUSTOMER_PRICE" and _customer_profile_kind(state) != "2":
        return "现在按周年庆活动价268元，线上交10元预约金，到店抵扣后做付258元。"
    if exact_policy == "SF7_PRICE_ONCE_FEE":
        return "是的，这次周年庆活动是一次费用，线上交10元预约金，到店抵扣后做付258元。"
    if exact_policy == "SF7_PRICE_AD_58":
        return "亲，不是58的哈，您应该是看错了，我们现在是周年庆活动268元操作的哦。"
    if exact_policy == "SF7_PRICE_DIFFERENCE":
        return "您看到的可能是之前活动或不同内容，现在能参加的是周年庆活动价268元。"
    if exact_policy == "SF7_DEPOSIT_EXPLAIN":
        return "10元是线上预约金，用来锁周年庆活动名额，到店直接抵扣。"
    if exact_policy == "SF7_PAYMENT_TIMING":
        return "线上先交10元预约金锁名额，到店检测认可再做，做付258元。"
    if policy_family == "SF7_PRICE_ACTIVITY":
        return "费用会按活动规则和到店检测后的方案提前说清楚，认可再做。"
    if policy_family == "SF5_COMPETITOR_COMPARE":
        return "您对比价格很正常，重点看包含内容、部位次数和费用是否透明，认可后再决定。"
    if policy_family == "SF10_TRUST_BUILD":
        return "理解您的顾虑，资质、费用和效果参考到店都能实地核对，认可再安排。"
    if policy_family.startswith("SF9_APPOINTMENT"):
        return "我先按您的到店意向继续确认，具体门店和时间以真实档期为准。"
    if policy_family == "SF6_STORE_INQUIRY":
        store_reply = _store_fact_text_fallback(state)
        if store_reply:
            return store_reply
        return "门店位置和营业时间我会按真实信息确认清楚，再帮您选更方便的一家。"
    if sop_stage == "S2_STORE_ADDRESS":
        store_reply = _store_fact_text_fallback(state)
        if store_reply:
            return store_reply
        return "您在哪个区或附近什么地标呀？我给您匹配近一点的门店。"
    if policy_family == "SF12_AFTER_SALES":
        return "理解您这次体验不太理想，我先帮您把门店、时间和具体情况问清楚再处理。"
    return "我先按您的问题继续帮您确认，涉及价格、门店或时间都会以真实信息为准。"


def _canonical_scene_reply(state: AgentState) -> str:
    contexts = state.get("scene_guidance_context")
    if not isinstance(contexts, list):
        return ""
    for item in contexts:
        if not isinstance(item, dict):
            continue
        if str(item.get("scene_id") or "") == "SF7_OLD_CUSTOMER_PRICE" and _customer_profile_kind(state) != "2":
            continue
        canonical = str(item.get("canonical_sales_reply") or "").strip()
        copy_strength = str(item.get("copy_strength") or "").strip().lower()
        if canonical and copy_strength == "high":
            return canonical
    return ""


def _customer_profile_kind(state: AgentState) -> str:
    fact_envelope = state.get("fact_envelope") if isinstance(state, dict) else {}
    if not isinstance(fact_envelope, dict):
        return ""
    structured = fact_envelope.get("structured_facts")
    if not isinstance(structured, dict):
        return ""
    profile_facts = structured.get("customer_profile_facts")
    if not isinstance(profile_facts, list) or not profile_facts:
        return ""
    first = profile_facts[0]
    if not isinstance(first, dict):
        return ""
    return str(first.get("kind") or "")


def _store_fact_text_fallback(state: AgentState) -> str:
    fact_envelope = state.get("fact_envelope") if isinstance(state, dict) else {}
    if not isinstance(fact_envelope, dict):
        return ""
    structured = fact_envelope.get("structured_facts")
    if not isinstance(structured, dict):
        return ""
    status = structured.get("store_lookup_status")
    status = status if isinstance(status, dict) else {}
    if bool(status.get("needs_area_or_landmark")):
        city = str(status.get("city") or "").strip()
        if city:
            return f"{city}这边可以帮您查，您在{city}哪个区或附近什么地标呀？我给您匹配近一点的门店。"
        return "您在哪个城市或附近什么地标呀？我给您匹配近一点的门店。"
    recommended = structured.get("recommended_store")
    store: dict[str, object] = recommended if isinstance(recommended, dict) and recommended else {}
    stores = structured.get("store_facts")
    if _current_query_asks_nearest_store(state) and not _state_has_distance_facts(state):
        city = str(status.get("city") or "").strip()
        names: list[str] = []
        if isinstance(stores, list):
            names = [str(item.get("name") or "").strip() for item in stores if isinstance(item, dict) and item.get("name")]
        if names:
            prefix = f"{city}这边" if city else "这边"
            return f"{prefix}我查到{ '、'.join(names[:3]) }，具体哪家离您更近我还需要继续核对距离。"
        return "我正在帮您核对近一点的门店，具体距离以真实查询和导航为准。"
    if not store and isinstance(stores, list):
        for item in stores:
            if isinstance(item, dict) and (item.get("name") or item.get("address")):
                store = item
                break
    if store:
        name = str(store.get("name") or "").strip()
        address = str(store.get("address") or "").strip()
        hours = str(store.get("business_hours") or "").strip()
        location_preference = str(status.get("location_preference") or "").strip()
        if location_preference:
            prefix = f"按您说的{location_preference}，"
        else:
            prefix = ""
        parts = []
        if name and address:
            parts.append(f"{prefix}我查到{name}，地址在{address}")
        elif name:
            parts.append(f"{prefix}我查到{name}")
        elif address:
            parts.append(f"{prefix}我查到门店地址在{address}")
        if hours:
            parts.append(f"营业时间{hours}")
        text = "，".join(parts).strip("，")
        if text:
            return f"{text}，具体距离以导航为准。"
    if bool(status.get("no_store_match_confirmed")):
        city = str(status.get("city") or "").strip()
        if city:
            return f"{city}这边我查了暂时没匹配到门店，我再帮您看近一点的可到门店。"
        return "这边我查了暂时没匹配到门店，我再帮您看近一点的可到门店。"
    return ""


def _current_query_asks_nearest_store(state: AgentState) -> bool:
    query = str(state.get("normalized_content") or "")
    return any(term in query for term in ("哪家近", "离我近", "近一点", "最近", "附近哪家"))


def _state_has_distance_facts(state: AgentState) -> bool:
    fact_envelope = state.get("fact_envelope") if isinstance(state, dict) else {}
    if not isinstance(fact_envelope, dict):
        return False
    structured = fact_envelope.get("structured_facts")
    if not isinstance(structured, dict):
        return False
    for item in structured.get("distance_facts") or []:
        if isinstance(item, dict) and str(item.get("distance_text") or "").strip():
            return True
    recommended = structured.get("recommended_store")
    return isinstance(recommended, dict) and bool(str(recommended.get("distance") or "").strip())
