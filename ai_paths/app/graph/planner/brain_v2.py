from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.graph.nodes.common import model_usage_snapshot
from app.graph.nodes.contextual_short_message import short_message_context_for_model
from app.graph.nodes.current_turn_context import (
    build_current_turn_context,
    can_use_contextual_store_for_message,
    current_store_anchor_from_state,
)
from app.graph.nodes.sent_message_summary import sent_message_summary_for_model
from app.graph.planner.planner_contract import ALLOWED_TOOLS
from app.graph.planner.brain_v2_prompts import PLANNER_REPAIR_PROMPT, PLANNER_RISK_PATCH_PROMPT, PLANNER_SYSTEM_PROMPT
from app.graph.planner.brain_v2_normalizer import build_planner_plan_v2, safety_fallback_plan
from app.graph.state import AgentState
from app.policies.business_rules import planner_business_rules_prompt_section
from app.policies.constants import KNOWN_STORE_NAMES
from app.services.model_client import ModelClient
from app.services.risk_hold import HEALTH_RISK_TERMS, health_risk_hold

def planner_v2_model_tier(state: AgentState) -> str:
    return "planner"


def planner_v2_messages_for_model(state: AgentState) -> list[dict[str, Any]]:
    payload = _planner_payload_for_model(state)
    return [
        {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
        {"role": "system", "content": PLANNER_RISK_PATCH_PROMPT},
        {"role": "system", "content": "# Planner Rule Packs\n" + planner_business_rules_prompt_section()},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
    ]


def planner_v2_repair_messages_for_model(
    state: AgentState,
    *,
    original_plan: dict[str, Any],
    violations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    payload = {
        **_planner_payload_for_model(state),
        "original_plan": _compact_plan_for_repair(original_plan),
        "tool_policy_violations": _compact_violations_for_repair(violations),
    }
    return [
        {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
        {"role": "system", "content": PLANNER_RISK_PATCH_PROMPT},
        {"role": "system", "content": "# Planner Rule Packs\n" + planner_business_rules_prompt_section()},
        {"role": "system", "content": PLANNER_REPAIR_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
    ]


async def run_planner_brain_v2(
    state: AgentState,
    model_client: ModelClient,
) -> tuple[dict[str, Any], dict[str, Any]]:
    tier = planner_v2_model_tier(state)
    payload = await model_client.chat_json(planner_v2_messages_for_model(state), tier=tier, temperature=0.1)
    plan = build_planner_plan_v2(state, payload)
    initial_usage = model_usage_snapshot(model_client)
    nested_calls: list[dict[str, Any]] = []
    for repair_attempt in range(1, 3):
        violations = list(plan.get("tool_policy_violations", []))
        if not violations:
            break
        repair_call: dict[str, Any] = {
            "name": "planner_brain_repair",
            "input": {"tier": tier, "attempt": repair_attempt, "violations": violations},
        }
        try:
            repaired_payload = await model_client.chat_json(
                planner_v2_repair_messages_for_model(
                    state,
                    original_plan=plan,
                    violations=violations,
                ),
                tier=tier,
                temperature=0.0,
            )
            repaired_plan = build_planner_plan_v2(state, repaired_payload)
            plan = repaired_plan
            repair_call["output"] = {
                "decision": plan.get("planner_decision", ""),
                "stage": plan.get("planner_stage", ""),
                "sub_rule_id": plan.get("planner_sub_rule_id", ""),
                "conversion_stage": plan.get("conversion_stage", ""),
                "customer_type": plan.get("customer_type", ""),
                "main_blocker": plan.get("main_blocker", ""),
                "next_step": plan.get("next_step", ""),
                "payment_action": plan.get("payment_action", ""),
                "payment_decision": plan.get("payment_decision", {}),
                "tool_calls": len(plan.get("planner_tool_calls", [])),
                "tool_policy_violations": len(plan.get("tool_policy_violations", [])),
            }
            repair_call["usage"] = model_usage_snapshot(model_client)
        except Exception as exc:
            repair_call["error"] = f"{type(exc).__name__}: {exc}"
            repair_call["usage"] = model_usage_snapshot(model_client)
            nested_calls.append(repair_call)
            break
        nested_calls.append(repair_call)
    model_call = {
        "name": "planner_brain_v2",
        "input": {"tier": tier},
        "output": {
            "decision": plan.get("planner_decision", ""),
            "stage": plan.get("planner_stage", ""),
            "sub_rule_id": plan.get("planner_sub_rule_id", ""),
            "conversion_stage": plan.get("conversion_stage", ""),
            "customer_type": plan.get("customer_type", ""),
            "main_blocker": plan.get("main_blocker", ""),
            "next_step": plan.get("next_step", ""),
            "payment_action": plan.get("payment_action", ""),
            "payment_decision": plan.get("payment_decision", {}),
            "reply_messages": len(plan.get("planner_reply_messages", [])),
            "tool_calls": len(plan.get("planner_tool_calls", [])),
            "tool_policy_violations": len(plan.get("tool_policy_violations", [])),
        },
        "usage": initial_usage,
    }
    if nested_calls:
        model_call["nested_calls"] = nested_calls
    return plan, model_call


def _planner_payload_for_model(state: AgentState) -> dict[str, Any]:
    suppress_memory = False
    sent_message_summary = {} if suppress_memory else sent_message_summary_for_model(state)
    current_known_store = _current_known_store_for_planner(state)
    current_turn_context = {} if suppress_memory else build_current_turn_context(
        state,
        current_known_store=current_known_store,
        sent_message_summary=sent_message_summary,
    )
    risk_hold = {} if suppress_memory else health_risk_hold(state)
    payload = {
        "current_date": _current_date_iso(),
        "timezone": "Asia/Shanghai",
        "current_message": state.get("normalized_content") or "",
        "conversation_history": [] if suppress_memory else (state.get("conversation_history") or [])[-20:],
        "short_message_context": {} if suppress_memory else short_message_context_for_model(
            content=str(state.get("normalized_content") or state.get("content") or ""),
            conversation_history=state.get("conversation_history") if isinstance(state.get("conversation_history"), list) else [],
            sent_message_summary=sent_message_summary,
        ),
        "image_info": _compact_image_info(state.get("image_info") or {}),
        "category_id": str(((state.get("request_context") or {}).get("category_id") or "")).strip(),
        "customer_profile": {} if suppress_memory else _compact_customer_profile_for_planner(state.get("customer_profile") or {}),
        "history_events": [] if suppress_memory else (state.get("history_events") or [])[-8:],
        "customer_context": {} if suppress_memory else _compact_customer_context(state.get("customer_context") or {}),
        "current_known_store": current_known_store,
        "current_turn_context": current_turn_context,
        "turn_evidence": current_turn_context.get("turn_evidence") if isinstance(current_turn_context, dict) else {},
        "risk_hold": risk_hold,
        "store_scope_summary": _store_scope_summary(state.get("customer_store_knowledge") or {}),
        "sent_message_summary": sent_message_summary,
        "available_tools": [tool for tool in ALLOWED_TOOLS if tool != "no_tool"],
    }
    return _drop_empty(payload)


def _current_date_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()


def _compact_customer_profile_for_planner(profile: Any) -> dict[str, Any]:
    if not isinstance(profile, dict):
        return {}
    allowed_keys = (
        "decision_stage",
        "conversion_stage",
        "customer_stage",
        "deposit_state",
        "deposit_status",
        "payment_status",
        "intent_level",
        "trust_level",
        "main_objection",
        "main_concern",
        "risk_tags",
        "customer_type_tags",
        "tags",
        "preferred_project",
        "preferred_store",
        "preferred_store_name",
        "intent_date",
        "intent_time",
    )
    compact: dict[str, Any] = {}
    for key in allowed_keys:
        value = profile.get(key)
        if value not in (None, "", [], {}):
            if key in {"main_objection", "main_concern"} and _mentions_health_risk(value):
                continue
            if key in {"risk_tags", "customer_type_tags", "tags"} and isinstance(value, list):
                filtered = [item for item in value if not _mentions_health_risk(item)]
                if filtered:
                    compact[key] = filtered
                continue
            compact[key] = value
    return compact


def _mentions_health_risk(value: Any) -> bool:
    text = str(value or "")
    return any(term in text for term in HEALTH_RISK_TERMS) or "健康风险" in text


def _current_known_store_for_planner(state: AgentState) -> dict[str, Any]:
    store_id = str(state.get("confirmed_store_id") or state.get("store_id") or "").strip()
    store_name = str(state.get("confirmed_store_name") or state.get("store_name") or "").strip()
    if store_id or store_name:
        return _drop_empty({"store_id": store_id, "store_name": store_name, "source": "request"})

    current_message_store = _store_from_current_message(state)
    if current_message_store:
        return current_message_store

    content = str(state.get("normalized_content") or state.get("content") or "").strip()
    if can_use_contextual_store_for_message(content, state):
        explicit_store = _recent_explicit_store_for_planner(state)
        if explicit_store:
            return explicit_store
        contextual_store = current_store_anchor_from_state(
            state,
            current_known_store=None,
            allow_profile=False,
            prefer_recent=True,
        )
        if contextual_store:
            return contextual_store

    customer_context = state.get("customer_context") if isinstance(state.get("customer_context"), dict) else {}
    appointment = customer_context.get("appointment") if isinstance(customer_context.get("appointment"), dict) else {}
    if _current_turn_can_use_appointment_store(state):
        store_id = str(appointment.get("store_id") or "").strip()
        store_name = str(appointment.get("store_name") or "").strip()
        return _drop_empty({"store_id": store_id, "store_name": store_name, "source": "appointment_context"})

    basic_info = state.get("customer_basic_info") if isinstance(state.get("customer_basic_info"), dict) else {}
    preferred = _store_from_basic_info(basic_info)
    if preferred:
        return preferred
    return {}


def _recent_explicit_store_for_planner(state: AgentState) -> dict[str, Any]:
    event_store = _store_from_recent_events(state)
    if event_store:
        return event_store

    return _store_from_recent_conversation(state)


def _store_from_current_message(state: AgentState) -> dict[str, Any]:
    text = str(state.get("normalized_content") or state.get("content") or "").strip()
    matched = _stores_matching_text_for_planner(state, text)
    if len(matched) == 1:
        return {**_compact_store_for_planner(matched[0]), "source": "current_message"}
    if len(matched) > 1:
        return {
            "ambiguous": True,
            "matched_store_names": [_store_name_for_planner(store) for store in matched[:5]],
            "source": "current_message",
        }
    return {}


def _stores_matching_text_for_planner(state: AgentState, text: str) -> list[dict[str, Any]]:
    if not text:
        return []
    matched: list[dict[str, Any]] = []
    for store in _known_store_candidates_for_planner(state):
        name = _store_name_for_planner(store)
        if _store_name_matches_text_for_planner(name, text):
            matched.append(store)
    return _without_subsumed_store_matches(_dedupe_store_matches(matched))


def _known_store_candidates_for_planner(state: AgentState) -> list[dict[str, Any]]:
    candidates = list(_customer_scope_stores_for_planner(state))
    seen_names = {_store_name_for_planner(store) for store in candidates if _store_name_for_planner(store)}
    for name in KNOWN_STORE_NAMES:
        if name and name not in seen_names:
            candidates.append({"store_name": name})
            seen_names.add(name)
    return candidates


def _dedupe_store_matches(stores: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for store in stores:
        store_id = str(store.get("store_id") or store.get("id") or "").strip()
        name = _store_name_for_planner(store)
        key = (store_id, name)
        if not name or key in seen:
            continue
        seen.add(key)
        output.append(store)
    return output


def _without_subsumed_store_matches(stores: list[dict[str, Any]]) -> list[dict[str, Any]]:
    names = [_store_name_for_planner(store) for store in stores]
    output: list[dict[str, Any]] = []
    for store, name in zip(stores, names):
        if any(name != other and name in other for other in names):
            continue
        output.append(store)
    return output


def _store_from_basic_info(raw: dict[str, Any]) -> dict[str, Any]:
    store_id = str(raw.get("preferred_store_id") or "").strip()
    store_name = str(raw.get("preferred_store_name") or "").strip()
    city = str(raw.get("city") or "").strip()
    if not (store_id or store_name):
        return {}
    return _drop_empty({"store_id": store_id, "store_name": store_name, "city": city, "source": "customer_profile"})


def _store_from_recent_events(state: AgentState) -> dict[str, Any]:
    events = state.get("history_events") if isinstance(state.get("history_events"), list) else []
    for event in reversed(events[-20:]):
        if not isinstance(event, dict):
            continue
        if str(event.get("event_type") or "") not in {"store_matched", "store_address_sent"}:
            continue
        facts = event.get("facts") if isinstance(event.get("facts"), dict) else {}
        store_id = str(facts.get("store_id") or facts.get("id") or "").strip()
        store_name = str(facts.get("store_name") or facts.get("name") or "").strip()
        if store_id or store_name:
            return _drop_empty(
                {
                    "store_id": store_id,
                    "store_name": store_name,
                    "city": str(facts.get("city") or "").strip(),
                    "district": str(facts.get("district") or "").strip(),
                    "source": "history_event",
                }
            )
    return {}


def _store_from_recent_conversation(state: AgentState) -> dict[str, Any]:
    stores = _customer_scope_stores_for_planner(state)
    if not stores:
        return {}
    history = state.get("conversation_history") if isinstance(state.get("conversation_history"), list) else []
    chunks: list[str] = []
    for item in history[-6:]:
        text = _conversation_item_text(item)
        if text:
            chunks.append(text)
    store_by_id = {
        str(store.get("store_id") or store.get("id") or "").strip(): store
        for store in stores
        if str(store.get("store_id") or store.get("id") or "").strip()
    }
    for chunk in reversed(chunks):
        match = re.search(r"(?:store_id|门店ID)\s*[=:：]\s*(\d+)", chunk, flags=re.IGNORECASE)
        if match and match.group(1) in store_by_id:
            return {**_compact_store_for_planner(store_by_id[match.group(1)]), "source": "recent_store_address_message"}
    matched_overall: list[dict[str, Any]] = []
    for chunk in chunks:
        matched_overall.extend(store for store in stores if _store_name_matches_text_for_planner(_store_name_for_planner(store), chunk))
    matched_overall = _without_subsumed_store_matches(_dedupe_store_matches(matched_overall))
    if len(matched_overall) == 1:
        return {**_compact_store_for_planner(matched_overall[0]), "source": "recent_conversation"}
    if len(matched_overall) > 1:
        return {
            "ambiguous": True,
            "matched_store_names": [_store_name_for_planner(store) for store in matched_overall[:5]],
            "source": "recent_conversation",
        }
    return {}


def _customer_scope_stores_for_planner(state: AgentState) -> list[dict[str, Any]]:
    knowledge = state.get("customer_store_knowledge") if isinstance(state.get("customer_store_knowledge"), dict) else {}
    stores = knowledge.get("stores") if isinstance(knowledge.get("stores"), list) else []
    return [store for store in stores if isinstance(store, dict)]


def _store_name_for_planner(store: dict[str, Any]) -> str:
    return str(store.get("store_name") or store.get("name") or "").strip()


def _store_name_matches_text_for_planner(name: str, text: str) -> bool:
    raw_name = str(name or "").strip()
    raw_text = str(text or "").strip()
    if raw_name and raw_text and (raw_name in raw_text or (len(raw_text) >= 4 and raw_text in raw_name)):
        return True
    normalized_name = _normalize_store_name_for_planner_match(raw_name)
    normalized_text = _normalize_store_name_for_planner_match(raw_text)
    return bool(
        normalized_name
        and normalized_text
        and (normalized_name in normalized_text or (len(normalized_text) >= 4 and normalized_text in normalized_name))
    )


def _normalize_store_name_for_planner_match(value: str) -> str:
    return re.sub(r"[，,。？?！!\s]", "", str(value or "")).replace("市", "").replace("百星", "")


def _compact_store_for_planner(store: dict[str, Any]) -> dict[str, Any]:
    return _drop_empty(
        {
            "store_id": str(store.get("store_id") or store.get("id") or "").strip(),
            "store_name": _store_name_for_planner(store),
            "city": str(store.get("city") or "").strip(),
            "district": str(store.get("district") or "").strip(),
        }
    )


def _conversation_item_text(item: Any) -> str:
    if isinstance(item, dict):
        content = item.get("content")
        if isinstance(content, dict):
            return str(content.get("text") or content.get("url") or "").strip()
        return str(content or "").strip()
    return str(item or "").strip()


def _current_turn_can_use_appointment_store(state: AgentState) -> bool:
    content = "".join(str(state.get("normalized_content") or state.get("content") or "").split())
    if not content:
        return False
    if _recent_explicit_store_for_planner(state):
        return False
    return any(
        term in content
        for term in (
            "预约",
            "改约",
            "取消",
            "档期",
            "已约",
            "约过",
            "预约记录",
        )
    )


def _should_suppress_planner_memory(state: AgentState) -> bool:
    return False


def _compact_plan_for_repair(plan: dict[str, Any]) -> dict[str, Any]:
    return _drop_empty(
        {
            "decision": plan.get("planner_decision", ""),
            "stage": plan.get("planner_stage", ""),
            "sub_rule_id": plan.get("planner_sub_rule_id", ""),
            "conversion_stage": plan.get("conversion_stage", ""),
            "customer_type": plan.get("customer_type", ""),
            "main_blocker": plan.get("main_blocker", ""),
            "next_step": plan.get("next_step", ""),
            "payment_state": plan.get("payment_state", ""),
            "payment_action": plan.get("payment_action", ""),
            "reply_messages": plan.get("planner_reply_messages", []),
            "tool_calls": plan.get("planner_tool_calls", []),
            "handoff": plan.get("handoff", {}),
        }
    )


def _compact_violations_for_repair(violations: list[dict[str, Any]]) -> list[dict[str, str]]:
    compact: list[dict[str, str]] = []
    for item in violations[:8]:
        if not isinstance(item, dict):
            continue
        compact.append(
            {
                "missing": str(item.get("missing") or ""),
                "note": str(item.get("note") or "")[:240],
            }
        )
    return [item for item in compact if item.get("missing") or item.get("note")]


def _compact_customer_context(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    keys = (
        "city",
        "confirmed_store_id",
        "confirmed_store_name",
        "detected_city",
        "appointment_info",
        "has_upcoming_appointment",
        "latest_store_candidates",
    )
    return {key: raw.get(key) for key in keys if raw.get(key) not in (None, "", [], {})}


def _compact_image_info(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    has_image = bool(raw.get("has_image"))
    has_signal = any(
        raw.get(key) not in (None, "", [], {})
        for key in ("image_desc", "visible_concerns", "risk_signals", "extracted_text", "text_clues")
    )
    if not has_image and not has_signal:
        return {}
    keys = (
        "has_image",
        "image_type",
        "image_intent",
        "body_part",
        "visible_concerns",
        "risk_signals",
        "extracted_text",
        "text_clues",
        "image_desc",
        "confidence",
    )
    return {key: raw.get(key) for key in keys if raw.get(key) not in (None, "", [], {})}


def _store_scope_summary(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict) or not raw:
        return {}
    stores = raw.get("stores") if isinstance(raw.get("stores"), list) else []
    return {
        "source": raw.get("source"),
        "store_count": raw.get("store_count", len(stores)),
        "snapshot_generated_at": raw.get("snapshot_generated_at"),
        "store_scope_error": raw.get("store_scope_error") or raw.get("error") or "",
        "cache": raw.get("cache") if isinstance(raw.get("cache"), dict) else {},
        "missing_snapshot_store_ids": raw.get("missing_snapshot_store_ids", []),
        "province_counts": _province_counts(stores),
    }


def _province_counts(stores: list[Any]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for store in stores:
        if not isinstance(store, dict):
            continue
        province = str(store.get("province") or "").strip() or "未识别省份"
        counts[province] = counts.get(province, 0) + 1
    return [
        {"province": province, "store_count": count}
        for province, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _drop_empty(value: Any) -> Any:
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in value.items():
            compact_item = _drop_empty(item)
            if compact_item in (None, "", [], {}):
                continue
            output[key] = compact_item
        return output
    if isinstance(value, list):
        output_list = [_drop_empty(item) for item in value]
        return [item for item in output_list if item not in (None, "", [], {})]
    return value
