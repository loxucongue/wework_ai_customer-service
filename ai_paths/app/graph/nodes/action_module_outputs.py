from __future__ import annotations

import html
import re
from typing import Any

from app.graph.state import AgentState


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
            structured_facts["store_lookup_status"] = {
                "query": str(value.get("query") or ""),
                "city": str(value.get("city") or ""),
                "requested_store": str(value.get("requested_store") or ""),
                "location_preference": str(value.get("location_preference") or ""),
                "distance_origin": str(value.get("distance_origin") or ""),
                "distance_lookup_required": bool(value.get("distance_lookup_required")),
                "recommendation_status": str(value.get("recommendation_status") or ""),
                "source": str(value.get("source") or ""),
                "candidate_count": len(stores) if isinstance(stores, list) else 0,
            }
            if value.get("distance_lookup_required"):
                facts.append(
                    "store_lookup: distance_lookup_required="
                    f"{value.get('distance_origin') or value.get('location_preference') or ''}"
                )
            if stores:
                structured_facts["store_facts"] = [
                    {
                        "id": str(item.get("id") or item.get("store_id") or ""),
                        "name": str(item.get("name") or ""),
                        "address": str(item.get("address") or ""),
                        "business_hours": str(item.get("business_hours") or item.get("business_hours_text") or ""),
                        "parking": str(item.get("parking") or item.get("parking_info") or ""),
                    }
                    for item in stores[:5]
                    if isinstance(item, dict)
                ]
                names = [item["name"] for item in structured_facts["store_facts"][:3] if item.get("name")]
                if names:
                    facts.append(f"store_lookup: matched_stores={', '.join(names)}")
            recommended = value.get("recommended_store") or {}
            if isinstance(recommended, dict) and recommended:
                structured_facts["recommended_store"] = {
                    "id": str(recommended.get("id") or recommended.get("store_id") or ""),
                    "name": str(recommended.get("name") or ""),
                    "address": str(recommended.get("address") or ""),
                    "reason": str(recommended.get("reason") or value.get("recommend_reason") or ""),
                }
                facts.append(
                    "store_lookup: recommended_store="
                    f"{recommended.get('name') or ''}; address={recommended.get('address') or ''}"
                )
            missing_slots.extend(str(item) for item in (value.get("missing") or [])[:4])
            continue

        if key == "pricing_rules":
            rows = value.get("rows") or []
            if rows:
                structured_facts["price_facts"].extend(
                    {
                        "rule_id": str(item.get("rule_id") or ""),
                        "project_name": str(item.get("project_name") or item.get("name") or ""),
                        "project_code": str(item.get("project_code") or ""),
                        "category": str(item.get("category") or ""),
                        "quote_type": str(item.get("quote_type") or ""),
                        "body_scope": str(item.get("body_scope") or ""),
                        "customer_segment": str(item.get("customer_segment") or ""),
                        "prepay_amount": str(item.get("prepay_amount") or ""),
                        "tail_amount": str(item.get("tail_amount") or ""),
                        "total_price": str(item.get("total_price") or ""),
                        "display_price": str(item.get("display_price") or ""),
                        "original_price": str(item.get("original_price") or ""),
                        "min_quote": str(item.get("min_quote") or ""),
                        "conditions": str(item.get("conditions") or ""),
                        "rule_note": str(item.get("rule_note") or item.get("description") or ""),
                    }
                    for item in rows[:5]
                    if isinstance(item, dict)
                )
                names = [item["project_name"] for item in structured_facts["price_facts"][:3] if item.get("project_name")]
                facts.append(f"{key}: rows={len(rows)}; projects={', '.join(names)}")
            continue

        if key == "available_time":
            appointment_fact = {
                "type": "available_time",
                "store": value.get("store_name") or value.get("store_id") or "",
                "date": value.get("date") or "",
                "slots": value.get("slots") or {},
                "missing": value.get("missing") or [],
            }
            structured_facts["appointment_facts"].append(appointment_fact)
            facts.append(
                f"available_time: store={appointment_fact['store']}; "
                f"date={appointment_fact['date']}; slots={appointment_fact['slots']}"
            )
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
            for item in items[:5]:
                if not isinstance(item, dict):
                    continue
                content = str(item.get("content") or item.get("output") or item)[:500]
                fact = {
                    "source": key,
                    "title": str(item.get("title") or item.get("documentId") or "")[:120],
                    "content": content,
                }
                image_url = _image_url_from_content(content)
                if image_url:
                    fact["image_url"] = image_url
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
