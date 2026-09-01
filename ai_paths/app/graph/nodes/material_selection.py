from __future__ import annotations

import copy
from typing import Any, Callable

from app.graph.state import AgentState
from app.services.trace_logger import TraceLogger
from app.graph.nodes.reply_contract import (
    _authority_conflicts,
    _current_turn_structural_constraints,
    _drop_keys,
    _normalized_tool_fact_envelope,
    _payment_channel_availability,
    _registration_fact_status,
    _store_fact_status,
    _structured_delivery_options,
    _valid_commit_evidence,
)


def create_evidence_join_node(*, trace_logger: TraceLogger) -> Callable[[AgentState], Any]:
    async def evidence_join(state: AgentState) -> dict[str, Any]:
        with trace_logger.node(state, "deterministic_evidence_join", {}) as span:
            gate = copy.deepcopy(state.get("content_gate_result") or {})
            tool_plan = copy.deepcopy(state.get("tool_plan") or {})
            tool_results = copy.deepcopy(state.get("tool_results") or {})
            fact_envelope = _normalized_tool_fact_envelope(state.get("fact_envelope"))
            joined = {
                "schema_version": "reply_chain_evidence_join_v1",
                "shared_context": copy.deepcopy(state.get("shared_context") or {}),
                "content_candidates": copy.deepcopy(gate.get("content_candidates") or []),
                "sales_recall": copy.deepcopy(state.get("sales_recall") or {}),
                "semantic_route": copy.deepcopy(state.get("semantic_route") or {}),
                "knowledge_evidence": copy.deepcopy(state.get("knowledge_evidence") or {}),
                "gate_evidence": _drop_keys(
                    gate,
                    {
                        "content_candidates",
                        "selector_input",
                        "selector_output",
                        "model_usage",
                        # This only describes whether Gate recalled content. It
                        # must not narrow the final Reply to a tools-only answer.
                        "route_advice",
                    },
                ),
                "tool_plan": _drop_keys(tool_plan, {"model_usage"}),
                "tool_facts": tool_results,
                # The executor already normalizes raw tool payloads into
                # authority-scoped facts. Reply needs those facts to emit
                # valid structured messages, while Join remains agnostic
                # about whether a sales action should be taken.
                "normalized_tool_facts": fact_envelope,
                "missing_facts": copy.deepcopy(tool_plan.get("missing_facts") or []),
                "authority_conflicts": _authority_conflicts(state, tool_results),
                "join_policy": "evidence_only_no_customer_copy_no_sales_decision",
            }
            output = {
                "evidence_join": joined,
                "reply_mode": "parallel_evidence_reply",
                "trace": state.get("trace", []),
            }
            span["output_snapshot"] = {
                "content_candidate_count": len(joined["content_candidates"]),
                "sales_recall_status": (joined.get("sales_recall") or {}).get("status"),
                "sales_recall_candidates": (joined.get("sales_recall") or {}).get("candidate_count"),
                "tool_fact_names": sorted(tool_results),
                "missing_fact_count": len(joined["missing_facts"]),
                "conflict_count": len(joined["authority_conflicts"]),
            }
            return output

    return evidence_join


def parallel_reply_payload(state: AgentState) -> dict[str, Any]:
    joined = copy.deepcopy(state.get("evidence_join") or {})
    shared = joined.get("shared_context") if isinstance(joined.get("shared_context"), dict) else {}
    valid_customer_message_refs = ["current_message"]
    valid_message_refs = ["current_message"]
    for item in shared.get("conversation") or []:
        if not isinstance(item, dict):
            continue
        ref = str(item.get("message_ref") or "").strip()
        if ref and ref not in valid_message_refs:
            valid_message_refs.append(ref)
        if (
            ref
            and str(item.get("role") or "").strip().lower() in {"customer", "user"}
            and ref not in valid_customer_message_refs
        ):
            valid_customer_message_refs.append(ref)
    facts = shared.get("authoritative_facts") if isinstance(shared.get("authoritative_facts"), dict) else {}
    sop_progress = facts.get("sop_progress") if isinstance(facts.get("sop_progress"), dict) else {}
    content_indexes = shared.get("content_indexes") if isinstance(shared.get("content_indexes"), dict) else {}
    content_catalog = (
        content_indexes.get("available_sop") if isinstance(content_indexes.get("available_sop"), dict) else {}
    )
    catalog_by_id = {
        str(item.get("content_id") or "").strip(): item
        for item in content_catalog.get("sop_packs") or []
        if isinstance(item, dict) and str(item.get("content_id") or "").strip()
    }
    structured_delivered_assets = [
        {
            "ref": f"sop_completed:{content_id}",
            "content_id": content_id,
            "asset_role": str((catalog_by_id.get(content_id) or {}).get("asset_role") or "").strip(),
        }
        for raw_content_id in sop_progress.get("completed_pack_ids") or []
        if (content_id := str(raw_content_id).strip()) in catalog_by_id
    ]
    sent_messages = facts.get("sent_messages") if isinstance(facts.get("sent_messages"), dict) else {}
    store_delivery = (
        sent_messages.get("store_address_delivery")
        if isinstance(sent_messages.get("store_address_delivery"), dict)
        else {}
    )
    store_delivery_request_id = str(store_delivery.get("request_id") or "").strip()
    store_delivery_ids = [
        str(item).strip() for item in store_delivery.get("latest_batch_store_ids") or [] if str(item).strip()
    ]
    if (
        str(store_delivery.get("batch_confidence") or "").strip() == "high"
        and store_delivery_request_id
        and store_delivery_ids
    ):
        structured_delivered_assets.append(
            {
                "ref": f"store_delivery:{store_delivery_request_id}",
                "content_id": "",
                "asset_role": "address_evidence",
                "store_ids": list(dict.fromkeys(store_delivery_ids)),
                "delivered_at": str(store_delivery.get("last_sent_at") or "").strip(),
            }
        )
    structured_delivery_refs = [item["ref"] for item in structured_delivered_assets]
    structured_deposit_refs = [
        item["ref"] for item in structured_delivered_assets if item.get("asset_role") == "activity_offer"
    ]
    structured_supporting_refs = [
        item["ref"]
        for item in structured_delivered_assets
        if item.get("asset_role")
        in {
            "address_evidence",
            "effect_evidence",
            "objection_support",
        }
    ]
    prior_assistant_refs = [
        str(item.get("message_ref") or "").strip()
        for item in shared.get("conversation") or []
        if isinstance(item, dict)
        and str(item.get("role") or "").strip().lower() in {"assistant", "staff", "ai"}
        and str(item.get("message_ref") or "").strip()
    ]
    prior_assistant_message_refs = list(dict.fromkeys(prior_assistant_refs))
    prior_message_and_delivery_refs = list(dict.fromkeys([*valid_message_refs, *structured_delivery_refs]))
    allowed_selected_content_ids = [
        str(item.get("content_id") or item.get("id") or "").strip()
        for item in joined.get("content_candidates") or []
        if isinstance(item, dict)
        and str(item.get("content_id") or item.get("id") or "").strip()
        and str(item.get("delivery_status") or "").strip() != "completed"
    ]
    content_candidate_reference_options = [
        {
            "content_id": content_id,
            "used_fact_ref": f"content_asset:{content_id}",
        }
        for content_id in dict.fromkeys(allowed_selected_content_ids)
    ]
    sales_recall = joined.get("sales_recall") if isinstance(joined.get("sales_recall"), dict) else {}
    sales_recall_reference_options = [
        {
            "ref": f"sales_recall:{source_id}",
            "source_id": source_id,
            "authority": "reference_only_not_business_fact",
        }
        for item in sales_recall.get("candidates") or []
        if isinstance(item, dict) and (source_id := str(item.get("source_id") or "").strip())
    ]
    follow_sequence_reference_options = [
        {
            "sequence_id": sequence_id,
            "sequence_name": str(item.get("sequence_name") or "").strip(),
            "valid_step_ids": [
                str(step.get("step_id") or "").strip()
                for step in item.get("steps") or []
                if isinstance(step, dict) and str(step.get("step_id") or "").strip()
            ],
            "authority": "business_strategy_reference_not_mandatory_state",
        }
        for item in sales_recall.get("sequence_candidates") or []
        if isinstance(item, dict) and (sequence_id := str(item.get("sequence_id") or "").strip())
    ]
    follow_script_reference_options = [
        {
            "script_code": str(item.get("source_script_code") or "").strip(),
            "content_id": str(item.get("content_id") or "").strip(),
            "paragraph_no": int(item.get("paragraph_no") or 0),
            "sequence_links": copy.deepcopy(item.get("sequence_links") or []),
        }
        for item in joined.get("content_candidates") or []
        if isinstance(item, dict) and str(item.get("content_id") or "").startswith("follow_script:")
    ]
    tool_facts = joined.get("tool_facts") if isinstance(joined.get("tool_facts"), dict) else {}
    tool_fact_reference_options = [
        {
            "ref": f"tool_fact:{tool_name}",
            "tool_name": tool_name,
        }
        for raw_tool_name, tool_fact in tool_facts.items()
        if (tool_name := str(raw_tool_name or "").strip()) and isinstance(tool_fact, dict)
    ]
    valid_commit_evidence = _valid_commit_evidence(state, shared)
    authoritative_fact_reference_options = [
        {
            "ref": str(item.get("ref") or "").strip(),
            "kind": str(item.get("kind") or "").strip(),
        }
        for item in valid_commit_evidence
        if isinstance(item, dict)
        and str(item.get("ref") or "").strip()
        and str(item.get("kind") or "").strip() != "customer_message"
    ]
    registration_fact_status = _registration_fact_status(state, shared)
    store_fact_status = _store_fact_status(joined)
    structured_delivery_options = _structured_delivery_options(joined, state=state)
    current_message = shared.get("current_message") if isinstance(shared.get("current_message"), dict) else {}
    protocol_events = (
        current_message.get("protocol_events") if isinstance(current_message.get("protocol_events"), list) else []
    )
    payment_channel_availability = _payment_channel_availability(
        structured_delivery_options=structured_delivery_options,
        authoritative_paid=bool(registration_fact_status.get("authoritative_paid")),
        protocol_events=protocol_events,
    )
    reply_evidence = copy.deepcopy(joined)
    reply_shared = (
        reply_evidence.get("shared_context") if isinstance(reply_evidence.get("shared_context"), dict) else {}
    )
    reply_authoritative = (
        reply_shared.get("authoritative_facts") if isinstance(reply_shared.get("authoritative_facts"), dict) else {}
    )
    # The complete permission inventory is needed by the Tool Planner, but the
    # final Reply only needs the compact scope plus current-turn matched facts.
    # Removing this duplicate bulk does not remove any unique business fact.
    reply_authoritative.pop("raw_visible_store_records", None)
    return {
        "schema_version": "parallel_reply_input_v2",
        # Put the current turn's compact tool contract before the larger evidence
        # envelope. This changes no business decision; it prevents authoritative
        # tool results from being buried behind pre-tool content candidates.
        "structured_delivery_options": structured_delivery_options,
        "payment_channel_availability": payment_channel_availability,
        "tool_fact_reference_options": tool_fact_reference_options,
        "authoritative_fact_reference_options": authoritative_fact_reference_options,
        "registration_fact_status": registration_fact_status,
        "store_fact_status": store_fact_status,
        "current_turn_structural_constraints": _current_turn_structural_constraints(
            store_fact_status=store_fact_status,
            structured_delivery_options=structured_delivery_options,
        ),
        "evidence": reply_evidence,
        "ai_sales_policy": copy.deepcopy(shared.get("ai_sales_policy") or {}),
        "sales_strategy_catalog": copy.deepcopy(shared.get("sales_strategy_catalog") or {}),
        "valid_message_refs": valid_message_refs,
        "valid_customer_message_refs": valid_customer_message_refs,
        "structured_delivered_assets": structured_delivered_assets,
        "valid_deposit_evidence_refs": list(
            dict.fromkeys(
                [
                    *prior_assistant_message_refs,
                    *structured_deposit_refs,
                    *structured_supporting_refs,
                    "current_message",
                ]
            )
        ),
        "structured_prior_activity_refs": structured_deposit_refs,
        "structured_prior_supporting_refs": structured_supporting_refs,
        # Neutral provenance pools. Their names intentionally do not label any
        # message as an activity offer or a supporting sales key.
        "prior_assistant_message_refs": prior_assistant_message_refs,
        "prior_message_and_delivery_refs": prior_message_and_delivery_refs,
        "allowed_selected_content_ids": list(dict.fromkeys(allowed_selected_content_ids)),
        # Exact schema references only; Reply still decides whether to adopt an asset.
        "content_candidate_reference_options": content_candidate_reference_options,
        "sales_recall_reference_options": sales_recall_reference_options,
        "follow_sequence_reference_options": follow_sequence_reference_options,
        "follow_script_reference_options": follow_script_reference_options,
        # Neutral provenance identifiers for this turn's actual tool outputs.
        # They do not describe what the facts mean or whether Reply should use them.
        "valid_commit_evidence": valid_commit_evidence,
        "output_contract": {
            "reply_messages": "required customer-visible message list",
            "selected_content_ids": "optional; candidate IDs actually adopted and fully delivered",
            "sales_judgment": (
                "required compact current-turn judgment: primary_objective, customer_friction_observation and posture; "
                "only the first two observations may be replayed as low-authority evidence"
            ),
            "knowledge_use": (
                "optional; actual sequence, step or one primary script adopted this turn, never Router-only nominations; "
                "script_id may record a text-only script that materially shaped the reply"
            ),
            "payment_assessment": "optional; include only for a current payment context with exact evidence refs",
            "deposit_evidence": "optional; required only when payment_collection is actually emitted",
            "safety_assessment": "optional; include only for a current non-none risk using exact customer refs",
            "party_size_assessment": "optional; include only for explicit party-size payment evidence",
            "commit_actions": (
                "optional validated deferred writes after authoritative payment; add_customer_mobile arguments={mobile}; "
                "create_work_order arguments={customer_name,mobile,store_id}"
            ),
        },
    }
