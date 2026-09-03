from __future__ import annotations

import copy
from typing import Any, Callable

from app.graph.state import AgentState
from app.services.trace_logger import TraceLogger
from app.services.v3_sop_execution_service import SopExecutionService
from app.graph.nodes.reply_contract import (
    _message_type,
    _request_from_state,
    _shared_context,
    _v3_available_assets_for_turn,
)


def create_shared_context_node(
    *,
    trace_logger: TraceLogger,
    sop_execution_service: SopExecutionService | None = None,
) -> Callable[[AgentState], Any]:
    async def build_shared_context(state: AgentState) -> dict[str, Any]:
        with trace_logger.node(
            state,
            "shared_context",
            {"request_id": state.get("request_id"), "message_type": _message_type(state)},
        ) as span:
            content_catalog = (
                sop_execution_service.reply_chain_content_catalog()
                if sop_execution_service is not None
                else {"schema_version": "reply_chain_content_index_v2", "sop_packs": []}
            )
            if sop_execution_service is None:
                sop_progress = {
                    "status": "unavailable",
                    "source": "sop_execution_service_unavailable",
                    "completed_pack_ids": [],
                    "completed_categories": [],
                    "unfinished_sops": [],
                }
            else:
                try:
                    sop_progress = sop_execution_service.reply_chain_sop_progress(
                        _request_from_state(state),
                        request_context=dict(state.get("request_context") or {}),
                    )
                except Exception as exc:
                    sop_progress = {
                        "status": "error",
                        "source": "scoped_sop_send_records",
                        "error": f"{type(exc).__name__}: {exc}",
                        "completed_pack_ids": [],
                        "completed_categories": [],
                        "unfinished_sops": [],
                    }
            shared = _shared_context(
                state,
                content_catalog=content_catalog,
                sop_progress=sop_progress,
            )
            available_assets = getattr(sop_execution_service, "reply_chain_available_assets", None)
            approved_assets = available_assets() if callable(available_assets) else []
            shared["available_assets"] = _v3_available_assets_for_turn(
                state,
                approved_assets,
                sent_summary=shared.get("authoritative_facts", {}).get("sent_messages", {}),
                sop_progress=shared.get("authoritative_facts", {}).get("sop_progress", {}),
            )
            for key in ("ai_sales_policy", "sales_strategy_catalog"):
                value = state.get(key)
                if isinstance(value, dict) and str(value.get("runtime_mode") or "off") != "off":
                    shared[key] = copy.deepcopy(value)
            previous_policy_state = state.get("previous_policy_state")
            if isinstance(previous_policy_state, dict) and previous_policy_state:
                shared["previous_policy_state"] = copy.deepcopy(previous_policy_state)
            facts = shared.get("authoritative_facts") if isinstance(shared.get("authoritative_facts"), dict) else {}
            output = {
                "shared_context": shared,
                "store_scope_summary": copy.deepcopy(facts.get("visible_store_scope") or {}),
                "sent_message_summary": copy.deepcopy(facts.get("sent_messages") or {}),
                "trace": state.get("trace", []),
            }
            span["output_snapshot"] = {
                "schema_version": shared.get("schema_version"),
                "conversation_count": len(shared.get("conversation") or []),
                "fact_sections": sorted((shared.get("authoritative_facts") or {}).keys()),
                "excluded_semantic_fields": shared.get("excluded_semantic_fields") or [],
            }
            return output

    return build_shared_context
