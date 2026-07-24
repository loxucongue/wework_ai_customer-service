from __future__ import annotations

import html
import re
from typing import Any

from app.graph.nodes.appointment_time_utils import summarize_available_slots, target_time_status
from app.graph.nodes.store_scope_summary import store_scope_ids
from app.graph.state import AgentState
from app.policies.business_rules import load_business_rules
from app.services.customer_payment_state import resolved_payment_fact


def build_planner_fact_output(tool_results: dict[str, Any], state: AgentState) -> dict[str, Any]:
    """Provide factual evidence to the final reply model without customer-facing wording."""
    facts: list[str] = []
    missing_slots: list[str] = []
    risk_flags = list((state.get("guardrail_result") or {}).get("terms") or [])
    structured_facts: dict[str, Any] = {
        "store_lookup_status": {},
        "store_facts": [],
        "recommended_store": {},
        "price_facts": [],
        "case_facts": [],
        "knowledge_facts": [],
        "appointment_facts": [],
        "order_facts": [],
        "payment_facts": [],
        "registration_facts": [],
        "professional_assist": {},
        "tool_errors": [],
    }
    unsupported_claims: list[str] = []

    payment_fact = _payment_fact_from_state(state)
    if payment_fact:
        structured_facts["payment_facts"].append(payment_fact)
        facts.append(
            f"payment: state={payment_fact.get('deposit_state') or ''}; source={payment_fact.get('source') or ''}; "
            f"order_id={payment_fact.get('order_id') or ''}"
        )

    for key, value in tool_results.items():
        if not isinstance(value, dict):
            continue
        if value.get("error"):
            facts.append(f"{key}: tool_error={value.get('error')}")
            structured_facts["tool_errors"].append({"tool": key, "error": str(value.get("error"))[:240]})
            unsupported_claims.append(f"{key} unavailable")

        if key in {"store_lookup", "customer_store_lookup"}:
            stores = value.get("stores") or []
            structured_facts["store_lookup_status"] = {
                "query": str(value.get("query") or ""),
                "province": str(value.get("province") or (value.get("geocode") or {}).get("province") or ""),
                "city": str(value.get("city") or (value.get("geocode") or {}).get("city") or ""),
                "district": str(value.get("district") or (value.get("geocode") or {}).get("district") or ""),
                "township": str(value.get("township") or (value.get("geocode") or {}).get("township") or ""),
                "purpose": str(value.get("purpose") or ""),
                "requested_store": str(value.get("requested_store") or ""),
                "location_preference": str(value.get("location_preference") or ""),
                "distance_origin": str(value.get("distance_origin") or ""),
                "distance_lookup_required": bool(value.get("distance_lookup_required")),
                "recommendation_status": str(value.get("recommendation_status") or ""),
                "resolved_admin_level": str(value.get("resolved_admin_level") or ""),
                "scope_match_level": str(value.get("scope_match_level") or ""),
                "exact_scope_has_store": value.get("exact_scope_has_store"),
                "source": str(value.get("source") or ""),
                "status": str(value.get("status") or ""),
                "candidate_count": int(value.get("candidate_store_count") or (len(stores) if isinstance(stores, list) else 0)),
            }
            if value.get("distance_lookup_required"):
                facts.append(
                    "store_lookup: distance_lookup_required="
                    f"{value.get('distance_origin') or value.get('location_preference') or ''}"
                )
            if stores:
                authorized_stores = _authorized_customer_scope_store_items(stores, state)
                structured_facts["store_facts"] = [
                    _store_fact_from_lookup_item(item, state=state)
                    for item in authorized_stores[:5]
                    if isinstance(item, dict)
                ]
                names = [item["name"] for item in structured_facts["store_facts"][:3] if item.get("name")]
                if names:
                    facts.append(f"{key}: matched_stores={', '.join(names)}")
            recommended = value.get("recommended_store") or {}
            if isinstance(recommended, dict) and recommended and _store_item_is_customer_scope_authorized(recommended, state):
                structured_facts["recommended_store"] = {
                    **_store_fact_from_lookup_item(recommended, state=state),
                    "reason": str(recommended.get("reason") or value.get("recommend_reason") or ""),
                }
                facts.append(
                    "store_lookup: recommended_store="
                    f"{recommended.get('name') or ''}; address={recommended.get('address') or ''}"
                )
            missing_slots.extend(str(item) for item in (value.get("missing") or [])[:4])
            continue

        if key == "distance_calculate":
            candidate_stores = value.get("ranked_stores") if isinstance(value.get("ranked_stores"), list) else []
            if not candidate_stores:
                candidate_stores = value.get("candidate_stores") if isinstance(value.get("candidate_stores"), list) else []
            comparable_stores = [
                item
                for item in candidate_stores
                if isinstance(item, dict)
                and item.get("distance_km") is not None
                and not str(item.get("distance_error") or "").strip()
            ]
            has_real_ranking = len(comparable_stores) >= 2
            structured_facts["store_lookup_status"] = {
                "query": str(value.get("origin") or ""),
                "province": str(value.get("province") or ""),
                "city": str(value.get("city") or ""),
                "district": str(value.get("district") or ""),
                "township": str(value.get("township") or ""),
                "location_preference": str(value.get("origin") or ""),
                "distance_origin": str(value.get("origin") or ""),
                "distance_lookup_required": bool(value.get("status") == "distance_tool_unavailable"),
                "recommendation_status": (
                    str(value.get("status") or "") if has_real_ranking else "insufficient_comparable_candidates"
                ),
                "resolved_admin_level": str(value.get("resolved_admin_level") or ""),
                "scope_match_level": str(value.get("scope_match_level") or ""),
                "exact_scope_has_store": value.get("exact_scope_has_store"),
                "source": "distance_calculate",
                "candidate_count": int(value.get("candidate_store_count") or len(value.get("ranked_stores") or value.get("candidate_stores") or [])),
                "comparable_candidate_count": len(comparable_stores),
            }
            authorized_candidate_stores = _authorized_customer_scope_store_items(candidate_stores, state)
            structured_facts["store_facts"] = [
                {
                    **_store_fact_from_lookup_item(item, state=state),
                    "distance_source": str(item.get("distance_source") or ""),
                    "distance_error": str(item.get("distance_error") or ""),
                }
                for item in authorized_candidate_stores[:5]
                if isinstance(item, dict)
            ]
            authorized_comparable_stores = _authorized_customer_scope_store_items(comparable_stores, state)
            if has_real_ranking and authorized_comparable_stores:
                top_store = _store_fact_from_lookup_item(authorized_comparable_stores[0], state=state)
                structured_facts["recommended_store"] = {
                    **top_store,
                    "distance_source": str(authorized_comparable_stores[0].get("distance_source") or ""),
                    "distance_error": "",
                    "reason": "distance_calculate_rank_1",
                }
            facts.append(
                "distance_calculate: "
                f"origin={value.get('origin') or ''}; status={value.get('status') or ''}; candidates={len(candidate_stores)}; "
                f"source={candidate_stores[0].get('distance_source') if candidate_stores and isinstance(candidate_stores[0], dict) else ''}"
            )
            if value.get("error"):
                unsupported_claims.append("distance calculate unavailable")
            continue

        if key == "available_time":
            slot_summary = summarize_available_slots(
                value.get("slots") if isinstance(value.get("slots"), dict) else {},
                str(state.get("normalized_content") or state.get("content") or ""),
                target_time=str(value.get("target_time") or ""),
            )
            status = target_time_status(
                value.get("slots") if isinstance(value.get("slots"), dict) else {},
                str(value.get("target_time") or ""),
                str(state.get("normalized_content") or state.get("content") or ""),
            )
            appointment_fact = {
                "type": "available_time",
                "store": value.get("store_name") or value.get("store_id") or "",
                "date": value.get("date") or "",
                "recommended_slot": slot_summary.get("recommended_slot") or "",
                "backup_slots": slot_summary.get("backup_slots") or [],
                "slot_count": slot_summary.get("slot_count") or 0,
                "preference": slot_summary.get("preference") or "",
                "missing": value.get("missing") or [],
                "target_time": status.get("target_time") or "",
                "target_time_available": status.get("target_time_available"),
                "nearby_times": slot_summary.get("nearby_times") or [],
            }
            structured_facts["appointment_facts"].append(appointment_fact)
            target_note = ""
            if appointment_fact["target_time"]:
                target_note = (
                    f"; target={appointment_fact['target_time']}; "
                    f"target_available={appointment_fact['target_time_available']}"
                )
            facts.append(
                f"available_time: store={appointment_fact['store']}; "
                f"date={appointment_fact['date']}; recommended={appointment_fact['recommended_slot']}; "
                f"backup={appointment_fact['backup_slots']}; slot_count={appointment_fact['slot_count']}{target_note}"
            )
            missing_slots.extend(str(item) for item in appointment_fact["missing"][:4])
            continue

        if key == "create_work_order":
            order_fact = {
                "type": "work_order",
                "status": str(value.get("status") or ""),
                "order_id": str(value.get("order_id") or ""),
                "order_no": str(value.get("order_no") or ""),
                "store_id": str(value.get("store_id") or ""),
                "category_id": str(value.get("category_id") or ""),
                "prepay_required": value.get("prepay_required"),
                "prepay_paid": value.get("prepay_paid"),
                "deposit_state": str(value.get("deposit_state") or ""),
                "order_binding_state": str(value.get("order_binding_state") or ""),
                "order_binding_repaired": bool(value.get("order_binding_repaired")),
                "store_confirmation_source": str(value.get("store_confirmation_source") or ""),
                "creation_mode": str(value.get("creation_mode") or ""),
                "missing": [str(item) for item in value.get("missing") or [] if str(item or "").strip()],
                "missing_optional_fields": [
                    str(item) for item in value.get("missing_optional_fields") or [] if str(item or "").strip()
                ],
                "error": str(value.get("error") or "")[:240],
                "source": str(value.get("source") or ""),
            }
            structured_facts["order_facts"].append(order_fact)
            facts.append(
                f"create_work_order: status={order_fact['status']}; order_id={order_fact['order_id']}; "
                f"store_id={order_fact['store_id']}; prepay={order_fact['prepay_required']}"
            )
            continue

        if key == "add_customer_mobile":
            registration_fact = {
                "type": "customer_mobile_sync",
                "status": str(value.get("status") or ""),
                "mobile": str(value.get("mobile") or ""),
                "source": str(value.get("source") or ""),
            }
            structured_facts["registration_facts"].append(registration_fact)
            facts.append(f"add_customer_mobile: status={registration_fact['status']}")
            continue

        if key == "create_order_plan":
            appointment_fact = {
                "type": "appointment_created" if value.get("status") in {"created", "reused"} else "appointment_create_failed",
                "status": str(value.get("status") or ""),
                "appointment_id": str(value.get("appointment_id") or value.get("order_id") or ""),
                "order_id": str(value.get("order_id") or ""),
                "store_id": str(value.get("store_id") or ""),
                "store_name": str(value.get("store_name") or ""),
                "appointment_time": str(value.get("appointment_time") or ""),
                "source": str(value.get("source") or ""),
            }
            structured_facts["appointment_facts"].append(appointment_fact)
            facts.append(
                f"create_order_plan: status={appointment_fact['status']}; order_id={appointment_fact['order_id']}; "
                f"appointment_time={appointment_fact['appointment_time']}"
            )
            continue

        if key == "appointment_record_query":
            appointment_fact = {
                "type": "appointment_record_query",
                "status": value.get("status") or "",
                "store": value.get("store_name") or value.get("store_id") or "",
                "date": value.get("date") or "",
                "missing": value.get("missing") or [],
                "error": value.get("error") or "",
            }
            structured_facts["appointment_facts"].append(appointment_fact)
            facts.append(
                f"appointment_record_query: status={appointment_fact['status']}; "
                f"store={appointment_fact['store']}; date={appointment_fact['date']}"
            )
            if appointment_fact["error"]:
                unsupported_claims.append("appointment record unavailable")
            missing_slots.extend(str(item) for item in appointment_fact["missing"][:4])
            continue

        if key == "professional_assist":
            assist_fact = {
                "status": str(value.get("status") or ""),
                "reason": str(value.get("reason") or "")[:240],
                "task_type": str(value.get("task_type") or ""),
                "subtype": str(value.get("subtype") or ""),
                "policy_hint": str(value.get("policy_hint") or ""),
                "guardrail_terms": [str(item) for item in (value.get("guardrail_terms") or [])[:8]],
                "required_internal_action": str(value.get("required_internal_action") or ""),
            }
            structured_facts["professional_assist"] = assist_fact
            facts.append(
                "professional_assist: "
                f"status={assist_fact['status']}; task_type={assist_fact['task_type']}; policy={assist_fact['policy_hint']}"
            )
            continue

        items = value.get("items") or []
        if key == "case_studies" and not items and isinstance(value.get("case_studies_filter"), dict):
            fallback_fact = _configured_case_image_fallback_fact(state)
            if fallback_fact:
                structured_facts["case_facts"].append(fallback_fact)
                facts.append("case_studies: fallback_case_image=configured_case_image_pool")
            else:
                structured_facts["case_facts"].append(
                    {
                        "source": key,
                        "status": "no_new_case_image",
                        "filtered_document_ids": value["case_studies_filter"].get("filtered_document_ids", []),
                    }
                )
                facts.append("case_studies: no_new_case_image")
            continue
        if items:
            target = "case_facts" if key == "case_studies" else "knowledge_facts"
            normalized_items: list[dict[str, Any]] = []
            for item in items[:5]:
                if not isinstance(item, dict):
                    continue
                content = str(item.get("content") or item.get("output") or item)[:500]
                document_id = str(item.get("document_id") or item.get("documentId") or "").strip()
                fact = {
                    "source": key,
                    "document_id": document_id,
                    "title": str(item.get("title") or document_id or "")[:120],
                    "content": content,
                    "raw_content": content,
                }
                image_url = _image_url_from_content(content)
                if image_url:
                    fact["image_url"] = image_url
                if key == "case_studies":
                    fact["description"] = _description_from_case_content(content)
                normalized_items.append(fact)
            structured_facts[target].extend(normalized_items)
            facts.append(f"{key}: kb_items={len(items)}")

    return {
        "intent": "facts_only",
        "facts": facts[:8],
        "structured_facts": structured_facts,
        "fact_envelope": {
            "usable_facts": facts[:8],
            "missing_facts": list(dict.fromkeys(missing_slots))[:6],
            "risky_facts": risk_flags[:6],
            "unsupported_claims": list(dict.fromkeys(unsupported_claims))[:6],
            "structured_facts": structured_facts,
        },
        "reply_points": [],
        "missing_slots": list(dict.fromkeys(missing_slots))[:6],
        "risk_flags": risk_flags[:6],
        "suggested_next_step": "",
        "confidence": 0.9,
    }


def _payment_fact_from_state(state: AgentState) -> dict[str, Any]:
    customer_context = state.get("customer_context") if isinstance(state.get("customer_context"), dict) else {}
    orders = customer_context.get("orders") if isinstance(customer_context.get("orders"), list) else []
    basic_info = state.get("customer_basic_info") if isinstance(state.get("customer_basic_info"), dict) else {}
    stored = basic_info.get("deposit_state")
    if isinstance(stored, dict):
        existing_state = str(stored.get("status") or stored.get("deposit_state") or "")
        existing_source = str(stored.get("source") or "")
    else:
        existing_state = str(stored or "")
        existing_source = "customer_memory" if existing_state else ""
    return resolved_payment_fact(
        orders=orders,
        image_info=state.get("image_info"),
        existing_state=existing_state,
        existing_source=existing_source,
        existing_fact=stored,
    )


def _store_fact_from_lookup_item(item: dict[str, Any], *, state: AgentState | dict[str, Any] | None = None) -> dict[str, Any]:
    parking_name = str(item.get("parking_name") or "").strip()
    parking_address = str(item.get("parking_address") or "").strip()
    parking = str(item.get("parking") or item.get("parking_info") or parking_name or parking_address or "").strip()
    scope_authorized = _store_item_is_customer_scope_authorized(item, state or {})
    return {
        "id": str(item.get("id") or item.get("store_id") or "").strip(),
        "store_id": str(item.get("store_id") or item.get("id") or "").strip(),
        "name": str(item.get("name") or item.get("store_name") or "").strip(),
        "store_name": str(item.get("store_name") or item.get("name") or "").strip(),
        "province": str(item.get("province") or "").strip(),
        "city": str(item.get("city") or "").strip(),
        "district": str(item.get("district") or "").strip(),
        "address": str(item.get("address") or item.get("store_address") or "").strip(),
        "store_address": str(item.get("store_address") or item.get("address") or "").strip(),
        "business_hours": str(item.get("business_hours") or item.get("business_hours_text") or "").strip(),
        "parking": parking,
        "parking_name": parking_name,
        "parking_address": parking_address,
        "parking_url": str(item.get("parking_url") or "").strip(),
        "map_url": str(item.get("map_url") or "").strip(),
        "location": str(item.get("location") or "").strip(),
        "geocode_formatted_address": str(item.get("geocode_formatted_address") or "").strip(),
        "scope_authorized": scope_authorized,
    }


def _authorized_customer_scope_store_items(items: list[Any], state: AgentState) -> list[dict[str, Any]]:
    return [item for item in items if isinstance(item, dict) and _store_item_is_customer_scope_authorized(item, state)]


def _store_item_is_customer_scope_authorized(item: dict[str, Any], state: AgentState | dict[str, Any]) -> bool:
    store_id = str(item.get("store_id") or item.get("id") or "").strip()
    if not store_id:
        return False
    knowledge = state.get("customer_store_knowledge") if isinstance(state.get("customer_store_knowledge"), dict) else {}
    scope_ids = store_scope_ids(knowledge)
    if scope_ids:
        return store_id in scope_ids
    source = str(knowledge.get("source") or "").strip() if isinstance(knowledge, dict) else ""
    # No customer scope loaded in synthetic/unit contexts: keep facts usable.
    # Real customer scope with zero allowed IDs must not authorize arbitrary snapshot stores.
    return not source


def _image_url_from_content(content: str) -> str:
    if not content:
        return ""
    match = re.search(r'<img\s+[^>]*src=["\']([^"\']+)["\']', content, flags=re.IGNORECASE)
    if match:
        return html.unescape(match.group(1)).strip()

    stripped = content.strip()
    if stripped.startswith("http://") or stripped.startswith("https://"):
        return html.unescape(stripped.split()[0]).strip()

    match = re.search(r"https?://[^\s<>'\")]+", content)
    if match:
        return html.unescape(match.group(0)).strip()
    return ""


def _description_from_case_content(content: str) -> str:
    if not content:
        return ""
    text = re.sub(r"<img\s+[^>]*>", "", content, flags=re.IGNORECASE).strip()
    text = re.sub(r"\s+", " ", text).strip()
    if text.lower().startswith("description:"):
        return text.split(":", 1)[1].strip()
    return text[:300]


def _configured_case_image_fallback_fact(state: AgentState) -> dict[str, Any]:
    offer = load_business_rules().get("offer")
    urls = offer.get("case_image_fallback_urls") if isinstance(offer, dict) else []
    if not isinstance(urls, list):
        return {}

    sent_ids = _sent_case_document_ids(state)
    for index, raw_url in enumerate(urls, start=1):
        url = str(raw_url or "").strip()
        if not url:
            continue
        document_id = f"configured_case_image_{index}"
        if document_id in sent_ids:
            continue
        return {
            "source": "configured_case_image_pool",
            "status": "fallback_case_image",
            "document_id": document_id,
            "title": "configured case image",
            "image_url": url,
            "description": "同类改善参考图",
        }
    return {}


def _sent_case_document_ids(state: AgentState) -> set[str]:
    profile = state.get("customer_profile") if isinstance(state.get("customer_profile"), dict) else {}
    raw = profile.get("sent_case_document_ids") if isinstance(profile.get("sent_case_document_ids"), list) else []
    return {str(item).strip() for item in raw if str(item).strip()}
