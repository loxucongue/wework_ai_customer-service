from __future__ import annotations

import html
import re
from typing import Any

from app.graph.nodes.appointment_time_utils import target_time_status
from app.graph.nodes.common import clean_model_text
from app.graph.state import AgentState
from app.policies.s10_offer import attach_s10_offer_facts


def build_planner_fact_output(tool_results: dict[str, Any], state: AgentState) -> dict[str, Any]:
    """Provide factual evidence to the final reply model without customer-facing wording."""
    facts: list[str] = []
    missing_slots: list[str] = []
    risk_flags = list((state.get("guardrail_result") or {}).get("terms") or [])
    structured_facts: dict[str, Any] = {
        "store_facts": [],
        "store_lookup_status": {},
        "recommended_store": {},
        "distance_facts": [],
        "price_facts": [],
        "case_facts": [],
        "knowledge_facts": [],
        "sales_talk_scripts": [],
        "appointment_facts": [],
        "customer_profile_facts": [],
        "customer_order_facts": [],
        "professional_assist": {},
        "tool_errors": [],
    }
    unsupported_claims: list[str] = []

    for key, value in tool_results.items():
        if not isinstance(value, dict):
            continue
        if value.get("error"):
            facts.append(f"{key}: tool_error={value.get('error')}")
            structured_facts["tool_errors"].append({"tool": key, "error": str(value.get("error"))[:240]})
            unsupported_claims.append(f"{key} unavailable")

        if key == "store_lookup":
            stores = value.get("stores") or []
            missing = [str(item) for item in (value.get("missing") or [])[:4]]
            platform_error = str(value.get("platform_error") or "").strip()
            distance_lookup = tool_results.get("distance_lookup")
            if not isinstance(distance_lookup, dict):
                distance_lookup = {}
            structured_facts["store_lookup_status"] = {
                "query": str(value.get("query") or "")[:160],
                "city": str(value.get("city") or ""),
                "requested_store": str(value.get("requested_store") or ""),
                "area_or_landmark": str(value.get("area_or_landmark") or ""),
                "location_granularity": str(value.get("location_granularity") or ""),
                "location_preference": str(value.get("location_preference") or ""),
                "source": str(value.get("source") or ""),
                "data_authority": str(value.get("data_authority") or ""),
                "distance_origin": str(value.get("distance_origin") or ""),
                "planned_distance_origin": str(value.get("planned_distance_origin") or ""),
                "final_distance_origin": str(value.get("distance_origin") or ""),
                "distance_lookup_required": bool(value.get("distance_lookup_required")),
                "distance_lookup_status": str(distance_lookup.get("status") or ""),
                "distance_lookup_reason": str(distance_lookup.get("reason") or distance_lookup.get("error") or "")[:240],
                "missing": missing,
                "platform_error": platform_error[:240],
                "has_store_facts": bool(stores),
                "city_store_count": value.get("city_store_count") or 0,
                "has_city_store_candidates": bool(value.get("has_city_store_candidates")),
                "needs_area_or_landmark": bool(value.get("needs_area_or_landmark")),
                "no_store_match_confirmed": bool(not stores and not missing and not platform_error),
                "detail_lookup_required": bool(stores),
            }
            if stores:
                structured_facts["store_facts"] = [
                    {
                        "id": str(item.get("id") or item.get("store_id") or ""),
                        "name": str(item.get("name") or ""),
                        "address": str(item.get("address") or ""),
                        "business_hours": str(item.get("business_hours") or item.get("business_hours_text") or ""),
                        "map_url": str(item.get("map_url") or ""),
                        "parking_name": str(item.get("parking_name") or ""),
                        "parking_address": str(item.get("parking_address") or ""),
                        "parking_link": str(item.get("parking_link") or ""),
                        "parking": str(item.get("parking") or item.get("parking_info") or item.get("parking_address") or ""),
                        "detail_source": str(item.get("detail_source") or ""),
                        "detail_error": str(item.get("detail_error") or "")[:180],
                        "has_detail": bool(item.get("has_detail")),
                        "address_source": str(item.get("address_source") or ""),
                        "status_code": item.get("status_code"),
                        "shore_show_code": item.get("shore_show_code"),
                        "schedule_status": item.get("schedule_status"),
                        "plan_status": item.get("plan_status"),
                        "is_pause": item.get("is_pause"),
                        "pause_start": str(item.get("pause_start") or ""),
                        "pause_end": str(item.get("pause_end") or ""),
                        "is_public": bool(item.get("is_public")),
                        "status_summary": str(item.get("status_summary") or ""),
                    }
                    for item in stores[:5]
                    if isinstance(item, dict)
                ]
                names = [item["name"] for item in structured_facts["store_facts"][:3] if item.get("name")]
                if names:
                    facts.append(f"store_lookup: matched_stores={', '.join(names)}")
            recommended = value.get("recommended_store") or {}
            if isinstance(recommended, dict) and recommended:
                store_facts = structured_facts.get("store_facts") if isinstance(structured_facts.get("store_facts"), list) else []
                matched_store = next(
                    (
                        item
                        for item in store_facts
                        if isinstance(item, dict)
                        and (
                            str(item.get("id") or "") == str(recommended.get("id") or recommended.get("store_id") or "")
                            or str(item.get("name") or "") == str(recommended.get("name") or "")
                        )
                    ),
                    {},
                )
                structured_facts["recommended_store"] = {
                    "id": str(recommended.get("id") or recommended.get("store_id") or ""),
                    "name": str(recommended.get("name") or ""),
                    "address": str(recommended.get("address") or ""),
                    "business_hours": str(recommended.get("business_hours") or matched_store.get("business_hours") or ""),
                    "map_url": str(recommended.get("map_url") or matched_store.get("map_url") or ""),
                    "parking_name": str(recommended.get("parking_name") or matched_store.get("parking_name") or ""),
                    "parking_address": str(recommended.get("parking_address") or matched_store.get("parking_address") or ""),
                    "parking_link": str(recommended.get("parking_link") or matched_store.get("parking_link") or ""),
                    "detail_source": str(recommended.get("detail_source") or matched_store.get("detail_source") or ""),
                    "has_detail": bool(recommended.get("has_detail") or matched_store.get("has_detail")),
                    "status_code": recommended.get("status_code", matched_store.get("status_code")),
                    "shore_show_code": recommended.get("shore_show_code", matched_store.get("shore_show_code")),
                    "schedule_status": recommended.get("schedule_status", matched_store.get("schedule_status")),
                    "plan_status": recommended.get("plan_status", matched_store.get("plan_status")),
                    "is_pause": recommended.get("is_pause", matched_store.get("is_pause")),
                    "pause_start": str(recommended.get("pause_start") or matched_store.get("pause_start") or ""),
                    "pause_end": str(recommended.get("pause_end") or matched_store.get("pause_end") or ""),
                    "is_public": bool(recommended.get("is_public") if "is_public" in recommended else matched_store.get("is_public")),
                    "status_summary": str(recommended.get("status_summary") or matched_store.get("status_summary") or ""),
                    "reason": str(recommended.get("reason") or value.get("recommend_reason") or ""),
                }
                facts.append(
                    "store_lookup: recommended_store="
                    f"{recommended.get('name') or ''}; address={recommended.get('address') or ''}"
                )
            if not stores and not missing and not platform_error:
                facts.append(
                    "store_lookup: no_matched_stores; "
                    f"city={value.get('city') or ''}; query={value.get('query') or ''}"
                )
            if platform_error and not stores:
                unsupported_claims.append("store_lookup incomplete")
            if stores and not any(item.get("has_detail") for item in stores if isinstance(item, dict)):
                unsupported_claims.append("store details unavailable")
            missing_slots.extend(missing)
            continue

        if key == "distance_lookup":
            distances = value.get("distances") or []
            if isinstance(distances, list):
                structured_facts["distance_facts"] = [
                    {
                        "store_id": str(item.get("id") or ""),
                        "name": str(item.get("name") or ""),
                        "address": str(item.get("address") or ""),
                        "distance_text": str(item.get("distance_text") or ""),
                    }
                    for item in distances[:5]
                    if isinstance(item, dict)
                ]
                usable = [item for item in structured_facts["distance_facts"] if item.get("distance_text")]
                if usable:
                    facts.append(
                        "distance_lookup: "
                        + "; ".join(f"{item.get('name')}: {item.get('distance_text')}" for item in usable[:3])
                    )
            if value.get("error"):
                unsupported_claims.append("distance_lookup unavailable")
            if not structured_facts["distance_facts"] and not value.get("error"):
                unsupported_claims.append("distance_lookup unavailable")
            continue

        if key == "available_time":
            slots = value.get("slots") if isinstance(value.get("slots"), dict) else {}
            time_status = target_time_status(
                slots,
                str(value.get("target_time") or ""),
                str(value.get("query") or ""),
            )
            available_times = value.get("available_times") or time_status.get("available_times") or []
            recommended_time = str(value.get("recommended_time") or "")
            if not recommended_time and available_times:
                recommended_time = str(available_times[0] or "")
            appointment_fact = {
                "type": "available_time",
                "status": value.get("status") or ("ok" if slots else "missing_info"),
                "store_id": value.get("store_id") or "",
                "store_name": value.get("store_name") or "",
                "store": value.get("store_name") or value.get("store_id") or "",
                "date": value.get("date") or "",
                "time_preference": value.get("time_preference") or "",
                "slots": slots,
                "missing": value.get("missing") or [],
                "error": value.get("error") or "",
                "target_time": time_status.get("target_time") or "",
                "target_time_available": time_status.get("target_time_available"),
                "available_times": available_times,
                "recommended_time": recommended_time,
                "nearby_times": time_status.get("nearby_times") or [],
            }
            structured_facts["appointment_facts"].append(appointment_fact)
            facts.append(
                f"available_time: store={appointment_fact['store']}; "
                f"date={appointment_fact['date']}; status={appointment_fact['status']}; "
                f"recommended_time={appointment_fact['recommended_time']}; slots={appointment_fact['slots']}"
            )
            if appointment_fact["status"] in {"error", "missing_info", "no_slots"} or appointment_fact["error"]:
                unsupported_claims.append("available_time unavailable")
            missing_slots.extend(str(item) for item in appointment_fact["missing"][:4])
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

        if key == "appointment_opening":
            appointment_fact = {
                "type": "appointment_opening",
                "status": value.get("status") or "",
                "order_id": value.get("order_id") or "",
                "missing": value.get("missing") or [],
                "error": value.get("error") or "",
            }
            structured_facts["appointment_facts"].append(appointment_fact)
            facts.append(
                f"appointment_opening: status={appointment_fact['status']}; "
                f"order_id={appointment_fact['order_id']}; missing={appointment_fact['missing']}"
            )
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
        if items:
            target = "case_facts" if key == "case_studies" else "knowledge_facts"
            normalized_items: list[dict[str, str]] = []
            sales_talk_scripts: list[dict[str, str]] = []
            for item in items[:5]:
                if not isinstance(item, dict):
                    continue
                raw_content = clean_model_text(str(item.get("content") or item.get("output") or item), max_chars=1500)
                content = clean_model_text(raw_content, max_chars=500)
                if not content:
                    continue
                fact = {
                    "source": key,
                    "title": clean_model_text(str(item.get("title") or item.get("documentId") or ""), max_chars=120),
                    "content": content,
                }
                if key == "sales_talk_qa":
                    script = _sales_talk_script_from_content(raw_content)
                    if script:
                        fact.update(script)
                        sales_talk_scripts.append(script)
                image_url = _image_url_from_item(item) or _image_url_from_content(raw_content)
                if image_url:
                    fact["image_url"] = image_url
                normalized_items.append(fact)
            structured_facts[target].extend(normalized_items)
            if sales_talk_scripts:
                structured_facts["sales_talk_scripts"].extend(sales_talk_scripts[:3])
            facts.append(f"{key}: kb_items={len(items)}")

    profile_facts = _customer_profile_facts_from_state(state)
    if profile_facts:
        structured_facts["customer_profile_facts"] = profile_facts
        profile = profile_facts[0]
        facts.append(
            "customer_profile: "
            f"kind={profile.get('kind') or ''}; kind_text={profile.get('kind_text') or ''}; "
            f"source={profile.get('source') or ''}"
        )

    order_facts = _customer_order_facts_from_state(state)
    if order_facts:
        structured_facts["customer_order_facts"] = order_facts
        latest = order_facts[0]
        facts.append(
            "customer_order: latest_order="
            f"id={latest.get('id') or ''}; amount={latest.get('amount_for_quote') or ''}"
        )

    fact_envelope = attach_s10_offer_facts(
        {
            "usable_facts": facts[:8],
            "missing_facts": list(dict.fromkeys(missing_slots))[:6],
            "risky_facts": risk_flags[:6],
            "unsupported_claims": list(dict.fromkeys(unsupported_claims))[:6],
            "structured_facts": structured_facts,
        }
    )
    return {
        "intent": "facts_only",
        "facts": fact_envelope.get("usable_facts", [])[:8],
        "structured_facts": fact_envelope.get("structured_facts", {}),
        "fact_envelope": fact_envelope,
        "reply_points": [],
        "missing_slots": list(dict.fromkeys(missing_slots))[:6],
        "risk_flags": risk_flags[:6],
        "suggested_next_step": "",
        "confidence": 0.9,
    }


def _customer_profile_facts_from_state(state: AgentState) -> list[dict[str, Any]]:
    customer_context = state.get("customer_context") if isinstance(state, dict) else {}
    if not isinstance(customer_context, dict):
        return [_default_new_customer_profile("missing_customer_context")]

    customer = customer_context.get("customer") if isinstance(customer_context.get("customer"), dict) else {}
    raw_kind = customer.get("kind") if isinstance(customer, dict) else None
    kind = _normalize_customer_kind(raw_kind)
    source = str(customer_context.get("source") or "")
    error = str(customer_context.get("error") or "")

    if kind not in (1, 2):
        reason = "customer_info_error" if error else "kind_missing_or_unknown"
        return [_default_new_customer_profile(reason, source=source, error=error)]

    return [
        {
            "kind": str(kind),
            "kind_text": "old_customer" if kind == 2 else "new_customer",
            "is_old_customer": kind == 2,
            "source": source or "platform_agent.customer_info",
            "raw_kind": str(raw_kind),
            "fallback_reason": "",
            "error": error[:240],
            "pricing_note": (
                "Use old-customer quote only with real previous order amount."
                if kind == 2
                else "Use current public anniversary campaign price."
            ),
        }
    ]


def _default_new_customer_profile(reason: str, *, source: str = "", error: str = "") -> dict[str, Any]:
    return {
        "kind": "1",
        "kind_text": "new_customer",
        "is_old_customer": False,
        "source": source or "default_new_on_missing_customer_info",
        "raw_kind": "",
        "fallback_reason": reason,
        "error": error[:240],
        "pricing_note": "Customer info missing/error/unknown kind: use current public anniversary campaign price.",
    }


def _normalize_customer_kind(value: Any) -> int | None:
    try:
        kind = int(value)
    except (TypeError, ValueError):
        return None
    return kind if kind in (1, 2) else None


def _customer_order_facts_from_state(state: AgentState) -> list[dict[str, Any]]:
    customer_context = state.get("customer_context") if isinstance(state, dict) else {}
    if not isinstance(customer_context, dict):
        return []
    orders = customer_context.get("orders") or []
    if not isinstance(orders, list):
        return []
    facts: list[dict[str, Any]] = []
    for order in orders[:5]:
        if not isinstance(order, dict):
            continue
        amount = _first_amount(order, "fee_paid_total", "fee_paid", "fee_required")
        facts.append(
            {
                "id": str(order.get("id") or ""),
                "order_no": str(order.get("order_no") or ""),
                "status": str(order.get("status") or ""),
                "store_name": str(order.get("store_name") or ""),
                "appointment_time": str(order.get("appointment_time") or ""),
                "store_at": str(order.get("store_at") or ""),
                "projects": order.get("projects") if isinstance(order.get("projects"), list) else [],
                "fee_origin": str(order.get("fee_origin") or ""),
                "fee_required": str(order.get("fee_required") or ""),
                "fee_paid": str(order.get("fee_paid") or ""),
                "fee_paid_total": str(order.get("fee_paid_total") or ""),
                "amount_for_quote": amount,
            }
        )
    return facts


def _sales_talk_script_from_content(content: str) -> dict[str, str]:
    question = _extract_labeled_field(content, "用户问题")
    business_logic = _extract_labeled_field(content, "业务应答逻辑")
    sales_script = _extract_labeled_field(content, "销冠话术", "回答建议", "参考话术")
    if not (question or business_logic or sales_script):
        return {}
    return {
        "source": "sales_talk_qa",
        "matched_question": question[:180],
        "business_logic": business_logic[:260],
        "sales_script": sales_script[:260],
    }


def _extract_labeled_field(content: str, *field_names: str) -> str:
    text = str(content or "")
    if not text or not field_names:
        return ""
    fields = "|".join(re.escape(name) for name in field_names)
    known_labels = (
        "切片ID|客户阶段|场景类型|用户问题|业务应答逻辑|销冠话术|回答建议|参考话术|"
        "适用条件|禁用表达|下一步动作"
    )
    pattern = re.compile(rf"(?:{fields})\s*[：:]\s*(.*?)(?=\s+(?:{known_labels})[：:]|$)", re.S)
    match = pattern.search(text)
    if not match:
        return ""
    return clean_model_text(" ".join(match.group(1).split()), max_chars=300)


def _first_amount(order: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = order.get(key)
        if value in (None, ""):
            continue
        text = str(value).replace(",", "").strip()
        match = re.search(r"\d+(?:\.\d+)?", text)
        if match:
            return match.group(0)
    return ""


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


def _image_url_from_item(item: dict[str, Any]) -> str:
    for key in ("image_url", "imageUrl", "url", "file_url", "fileUrl", "cover_url", "coverUrl"):
        value = str(item.get(key) or "").strip()
        if value:
            image_url = _image_url_from_content(value)
            if image_url:
                return image_url
    for key in ("content", "output", "text", "markdown"):
        value = item.get(key)
        if isinstance(value, str):
            image_url = _image_url_from_content(value)
            if image_url:
                return image_url
    return ""
