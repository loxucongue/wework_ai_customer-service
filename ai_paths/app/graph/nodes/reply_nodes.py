from __future__ import annotations

from typing import Any, Callable

from app.graph.nodes.common import model_usage_snapshot
from app.graph.planner.runtime_plan import planner_handoff
from app.graph.state import AgentState
from app.services.model_client import ModelClient
from app.services.trace_logger import TraceLogger


def create_synthesize_reply_node(
    *,
    trace_logger: TraceLogger,
    model_client: ModelClient | None,
    debug_message_contents: Callable[[list[dict[str, Any]]], list[str]],
    model_reply_unsafe: Callable[[AgentState, list[dict[str, Any]]], bool],
    postprocess_reply_messages: Callable[[AgentState, list[dict[str, Any]]], list[dict[str, Any]]],
    reply_messages_for_model: Callable[[AgentState], list[dict[str, Any]]],
    reply_model_tier: Callable[[AgentState], str],
    reply_repair_messages_for_model: Callable[[AgentState, list[dict[str, Any]]], list[dict[str, Any]]],
    reply_text_rescue_messages_for_model: Callable[[AgentState], list[dict[str, Any]]],
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
            repair_attempted = False
            text_rescue_attempted = False

            try:
                if not (model_client and model_client.available and should_use_model_reply(state)):
                    raise RuntimeError("reply_synthesizer_model_required")

                tier = reply_model_tier(state)
                model_call = {"name": "reply_synthesizer_model", "input": {"tier": tier, "required": True}}

                payload = await model_client.chat_json(reply_messages_for_model(state), tier=tier, temperature=0.0)
                model_call["usage"] = model_usage_snapshot(model_client)
                messages = validated_model_messages(payload)
                model_call["draft_messages"] = debug_message_contents(messages)
                if messages:
                    messages = postprocess_reply_messages(state, messages)
                    model_call["postprocessed_messages"] = debug_message_contents(messages)

                if not messages or model_reply_unsafe(state, messages):
                    messages, repair_call, repaired = await _try_repair_reply(
                        state=state,
                        draft_messages=messages,
                        tier=tier,
                        model_client=model_client,
                        model_reply_unsafe=model_reply_unsafe,
                        postprocess_reply_messages=postprocess_reply_messages,
                        reply_repair_messages_for_model=reply_repair_messages_for_model,
                        validated_model_messages=validated_model_messages,
                        debug_message_contents=debug_message_contents,
                        reason="initial_quality_gate",
                    )
                    repair_attempted = True
                    model_call.setdefault("nested_calls", []).append(repair_call)
                    if repaired:
                        model_call["fallback"] = "repaired_model_reply"
                        reply_source = "repair_model"
                    else:
                        model_call["fallback"] = "handoff_after_repair_failure"

                model_call["output"] = {"messages": len(messages)}
            except Exception as exc:
                model_call = model_call or {"name": "reply_synthesizer_model", "input": {}}
                primary_error = f"{type(exc).__name__}: {exc}"
                model_call["error"] = primary_error
                errors.append(
                    {
                        "node": "synthesize_reply",
                        "message": "final_reply_model_failed",
                        "detail": primary_error,
                    }
                )
                messages = []

            if messages and model_reply_unsafe(state, messages):
                repaired = False
                if model_call and model_client and model_client.available:
                    tier = str((model_call.get("input") or {}).get("tier") or reply_model_tier(state))
                    messages, repair_call, repaired = await _try_repair_reply(
                        state=state,
                        draft_messages=messages,
                        tier=tier,
                        model_client=model_client,
                        model_reply_unsafe=model_reply_unsafe,
                        postprocess_reply_messages=postprocess_reply_messages,
                        reply_repair_messages_for_model=reply_repair_messages_for_model,
                        validated_model_messages=validated_model_messages,
                        debug_message_contents=debug_message_contents,
                        reason="final_quality_gate",
                    )
                    repair_attempted = True
                    model_call.setdefault("nested_calls", []).append(repair_call)
                    if repaired:
                        reply_source = "repair_model"
                        model_call["fallback"] = "repaired_after_final_quality_gate"
                if not repaired:
                    messages = []
                    errors.append({"node": "synthesize_reply", "message": "final_reply_failed_quality_gate"})

            if not messages:
                if model_call and model_client and model_client.available and not repair_attempted:
                    tier = str((model_call.get("input") or {}).get("tier") or reply_model_tier(state))
                    messages, repair_call, repaired = await _try_repair_reply(
                        state=state,
                        draft_messages=[],
                        tier=tier,
                        model_client=model_client,
                        model_reply_unsafe=model_reply_unsafe,
                        postprocess_reply_messages=postprocess_reply_messages,
                        reply_repair_messages_for_model=reply_repair_messages_for_model,
                        validated_model_messages=validated_model_messages,
                        debug_message_contents=debug_message_contents,
                        reason="empty_reply_contract",
                    )
                    repair_attempted = True
                    model_call.setdefault("nested_calls", []).append(repair_call)
                    if repaired:
                        reply_source = "repair_model"
                        model_call["fallback"] = "repaired_empty_reply_contract"

            if not messages and model_call and model_client and model_client.available and not text_rescue_attempted:
                tier = str((model_call.get("input") or {}).get("tier") or reply_model_tier(state))
                messages, rescue_call, rescued = await _try_text_rescue_reply(
                    state=state,
                    tier=tier,
                    model_client=model_client,
                    model_reply_unsafe=model_reply_unsafe,
                    postprocess_reply_messages=postprocess_reply_messages,
                    reply_text_rescue_messages_for_model=reply_text_rescue_messages_for_model,
                    debug_message_contents=debug_message_contents,
                    reason=str(model_call.get("fallback") or model_call.get("error") or "empty_reply"),
                )
                text_rescue_attempted = True
                model_call.setdefault("nested_calls", []).append(rescue_call)
                if rescued:
                    reply_source = "text_rescue_model"
                    model_call["fallback"] = "text_rescue_model_reply"

            if not _has_customer_visible_text(messages):
                errors.append({"node": "synthesize_reply", "message": "customer_visible_reply_unavailable"})
                messages, reply_source = _safe_visible_fallback_messages(state)

            if model_call:
                span["entry"]["tool_calls"] = [model_call]
            output = {
                "reply_messages": messages,
                "reply_source": reply_source,
                "postprocess_changed": bool(state.get("postprocess_changed")),
                "postprocess_reasons": state.get("postprocess_reasons", []),
                "errors": errors,
                "trace": state.get("trace", []),
            }
            span["output_snapshot"] = output
            return output

    return synthesize_reply


async def _try_repair_reply(
    *,
    state: AgentState,
    draft_messages: list[dict[str, Any]],
    tier: str,
    model_client: ModelClient,
    model_reply_unsafe: Callable[[AgentState, list[dict[str, Any]]], bool],
    postprocess_reply_messages: Callable[[AgentState, list[dict[str, Any]]], list[dict[str, Any]]],
    reply_repair_messages_for_model: Callable[[AgentState, list[dict[str, Any]]], list[dict[str, Any]]],
    validated_model_messages: Callable[[dict[str, Any]], list[dict[str, Any]]],
    debug_message_contents: Callable[[list[dict[str, Any]]], list[str]],
    reason: str,
) -> tuple[list[dict[str, Any]], dict[str, Any], bool]:
    repair_call: dict[str, Any] = {"name": "reply_repair_model", "input": {"tier": tier, "required": True, "reason": reason}}
    try:
        repair_payload = await model_client.chat_json(
            reply_repair_messages_for_model(state, draft_messages),
            tier=tier,
            temperature=0.05,
        )
        repair_call["usage"] = model_usage_snapshot(model_client)
        repaired_messages = validated_model_messages(repair_payload)
        repair_call["draft_messages"] = debug_message_contents(repaired_messages)
        if repaired_messages:
            repaired_messages = postprocess_reply_messages(state, repaired_messages)
            repair_call["postprocessed_messages"] = debug_message_contents(repaired_messages)
        if repaired_messages and not model_reply_unsafe(state, repaired_messages):
            return repaired_messages, repair_call, True
        repair_call["error"] = "repaired_reply_still_unsafe"
    except Exception as repair_exc:
        repair_call["error"] = f"{type(repair_exc).__name__}: {repair_exc}"
    return [], repair_call, False


async def _try_text_rescue_reply(
    *,
    state: AgentState,
    tier: str,
    model_client: ModelClient,
    model_reply_unsafe: Callable[[AgentState, list[dict[str, Any]]], bool],
    postprocess_reply_messages: Callable[[AgentState, list[dict[str, Any]]], list[dict[str, Any]]],
    reply_text_rescue_messages_for_model: Callable[[AgentState], list[dict[str, Any]]],
    debug_message_contents: Callable[[list[dict[str, Any]]], list[str]],
    reason: str,
) -> tuple[list[dict[str, Any]], dict[str, Any], bool]:
    rescue_call: dict[str, Any] = {
        "name": "reply_text_rescue_model",
        "input": {"tier": tier, "required": True, "reason": reason},
    }
    try:
        text = await model_client.chat_text(
            reply_text_rescue_messages_for_model(state),
            tier=tier,
            temperature=0.05,
        )
        rescue_call["usage"] = model_usage_snapshot(model_client)
        text = _clean_rescue_text(text)
        rescue_call["draft_text"] = text[:240]
        if not text:
            rescue_call["error"] = "empty_text_rescue"
            return [], rescue_call, False

        rescued_messages = [{"type": "text", "order": 1, "content": {"text": text}}]
        rescued_messages = postprocess_reply_messages(state, rescued_messages)
        rescue_call["postprocessed_messages"] = debug_message_contents(rescued_messages)
        if rescued_messages and not model_reply_unsafe(state, rescued_messages):
            return rescued_messages, rescue_call, True
        rescue_call["error"] = "text_rescue_still_unsafe"
    except Exception as rescue_exc:
        rescue_call["error"] = f"{type(rescue_exc).__name__}: {rescue_exc}"
    return [], rescue_call, False


def _clean_rescue_text(text: str) -> str:
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("text"):
            cleaned = cleaned[4:].strip()
    cleaned = cleaned.strip().strip('"').strip("'").strip()
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if lines:
        cleaned = " ".join(lines)
    return cleaned.strip()


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
        canonical = str(item.get("canonical_sales_reply") or "").strip()
        copy_strength = str(item.get("copy_strength") or "").strip().lower()
        if canonical and copy_strength == "high":
            return canonical
    return ""


def _store_fact_text_fallback(state: AgentState) -> str:
    fact_envelope = state.get("fact_envelope") if isinstance(state, dict) else {}
    if not isinstance(fact_envelope, dict):
        return ""
    structured = fact_envelope.get("structured_facts")
    if not isinstance(structured, dict):
        return ""
    status = structured.get("store_lookup_status")
    status = status if isinstance(status, dict) else {}
    recommended = structured.get("recommended_store")
    store: dict[str, object] = recommended if isinstance(recommended, dict) and recommended else {}
    stores = structured.get("store_facts")
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
