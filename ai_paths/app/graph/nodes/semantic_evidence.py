from __future__ import annotations

import copy
import time
from typing import Any, Callable

from app.graph.state import AgentState
from app.services.coze_client import CozeClient
from app.services.model_client import ModelClient
from app.services.material_fingerprint import diversify_material_candidates
from app.services.sales_strategy_service import SalesStrategyService
from app.services.trace_logger import TraceLogger
from app.services.v3_semantic_router_service import V3SemanticRouterService, script_content_candidates
from app.services.v3_sop_execution_service import SopExecutionService
from app.graph.nodes.reply_contract import (
    _branch_trace_output,
    _dedupe_content_candidates,
    _dict_list,
    _merge_tool_calls,
    _protocol_required_read_only_tools,
    _semantic_route_observability,
    _store_resolution_fact_for_post_route,
)


def _sent_case_image_urls(state: AgentState) -> list[str]:
    shared = state.get("shared_context") if isinstance(state.get("shared_context"), dict) else {}
    facts = shared.get("authoritative_facts") if isinstance(shared.get("authoritative_facts"), dict) else {}
    sent = facts.get("sent_messages") if isinstance(facts.get("sent_messages"), dict) else {}
    delivery = sent.get("case_image_delivery") if isinstance(sent.get("case_image_delivery"), dict) else {}
    return list(
        dict.fromkeys(
            str(item or "").strip()
            for item in delivery.get("sent_image_urls") or []
            if str(item or "").strip()
        )
    )


def _recent_material_context(state: AgentState) -> tuple[list[str], list[str]]:
    shared = state.get("shared_context") if isinstance(state.get("shared_context"), dict) else {}
    observations = (
        shared.get("derived_observations")
        if isinstance(shared.get("derived_observations"), dict)
        else {}
    )
    material_ids: list[str] = []
    for delivery in observations.get("recent_asset_deliveries") or []:
        if not isinstance(delivery, dict):
            continue
        facts = (
            delivery.get("last_delivery_facts")
            if isinstance(delivery.get("last_delivery_facts"), dict)
            else {}
        )
        for value in (
            delivery.get("asset_identity"),
            facts.get("sop_pack_id"),
            facts.get("document_id"),
        ):
            normalized = str(value or "").strip()
            if normalized and normalized not in material_ids:
                material_ids.append(normalized)
    latest_usage = (
        observations.get("latest_follow_knowledge_usage")
        if isinstance(observations.get("latest_follow_knowledge_usage"), dict)
        else {}
    )
    for script_id in latest_usage.get("selected_script_ids") or []:
        normalized = str(script_id or "").strip()
        if normalized and normalized not in material_ids:
            material_ids.append(normalized)
    facts = shared.get("authoritative_facts") if isinstance(shared.get("authoritative_facts"), dict) else {}
    sent = facts.get("sent_messages") if isinstance(facts.get("sent_messages"), dict) else {}
    case_delivery = sent.get("case_image_delivery") if isinstance(sent.get("case_image_delivery"), dict) else {}
    for document_id in case_delivery.get("recent_document_ids") or []:
        normalized = str(document_id or "").strip()
        if normalized and normalized not in material_ids:
            material_ids.append(normalized)
    recent_texts = [
        str(item.get("content") or "").strip()
        for item in observations.get("recent_assistant_messages") or []
        if isinstance(item, dict) and str(item.get("content") or "").strip()
    ]
    return material_ids[:12], recent_texts[:4]


async def _diverse_content_candidates(
    state: AgentState,
    candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    recent_material_ids, recent_texts = _recent_material_context(state)
    result = await diversify_material_candidates(
        _dedupe_content_candidates(candidates),
        sent_image_urls=_sent_case_image_urls(state),
        recent_material_ids=recent_material_ids,
        recent_assistant_texts=recent_texts,
    )
    return _dict_list(result.get("candidates")), copy.deepcopy(result.get("audit") or {})


def create_post_fact_semantic_evidence_node(
    *,
    trace_logger: TraceLogger,
    semantic_router_service: V3SemanticRouterService | None = None,
    sales_strategy_service: SalesStrategyService | None = None,
) -> Callable[[AgentState], Any]:
    async def post_store_semantic_evidence(state: AgentState) -> dict[str, Any]:
        pre_route = state.get("store_pre_route") if isinstance(state.get("store_pre_route"), dict) else {}
        if str(pre_route.get("phase") or "") != "pre_store_pending":
            return {"trace": state.get("trace", [])}

        started = time.perf_counter()
        store_fact = _store_resolution_fact_for_post_route(state)
        with trace_logger.node(
            state,
            "v3_post_store_retrieval_after_facts",
            {
                "pre_route_phase": pre_route.get("phase"),
                "store_resolution_status": store_fact.get("status"),
            },
        ) as span:
            if semantic_router_service is None:
                semantic_output = {
                    "status": "degraded",
                    "semantic_route": {
                        "schema_version": "v3_semantic_route_v1",
                        "status": "disabled",
                        "phase": "post_store_final",
                        "reason": "semantic_router_not_configured",
                        "checkpoint": {},
                        "sequence_match": {},
                        "script_queries": [],
                        "store_query": {"required": False},
                    },
                    "knowledge_evidence": {
                        "status": "disabled",
                        "candidates": [],
                        "sequence_candidates": [],
                    },
                }
            else:
                try:
                    semantic_output = await semantic_router_service.complete_after_store(
                        shared_context=copy.deepcopy(state.get("shared_context") or {}),
                        pre_route=copy.deepcopy(pre_route),
                        store_resolution_fact=copy.deepcopy(store_fact),
                        sequence_result=copy.deepcopy(state.get("follow_sequence_index") or {}),
                        taxonomy_result=copy.deepcopy(state.get("follow_checkpoint_taxonomy") or {}),
                        closing_catalog_result=copy.deepcopy(state.get("closing_catalog") or {}),
                    )
                except Exception as exc:
                    semantic_output = {
                        "status": "degraded",
                        "semantic_route": {
                            "schema_version": "v3_semantic_route_v1",
                            "status": "error",
                            "phase": "post_store_final",
                            "reason": f"{type(exc).__name__}: {exc}"[:500],
                            "checkpoint": {},
                            "sequence_match": {},
                            "script_queries": [],
                            "store_query": {"required": False},
                        },
                        "knowledge_evidence": {
                            "status": "error",
                            "candidates": [],
                            "sequence_candidates": [],
                        },
                    }

            semantic_route = copy.deepcopy(semantic_output.get("semantic_route") or {})
            sales_recall = copy.deepcopy(semantic_output.get("knowledge_evidence") or {})
            existing_gate = copy.deepcopy(state.get("content_gate_result") or {})
            existing_candidates = _dict_list(existing_gate.get("content_candidates"))
            recalled_candidates = script_content_candidates(
                sales_recall,
                sent_image_urls=_sent_case_image_urls(state),
            )
            content_candidates, material_audit = await _diverse_content_candidates(
                state,
                [
                    *existing_candidates,
                    *recalled_candidates,
                ]
            )
            gate_result = {
                **existing_gate,
                "status": "completed",
                "content_candidate_ids": [item["content_id"] for item in content_candidates],
                "content_candidates": content_candidates,
                "reason": "approved_assets_plus_post_store_semantic_knowledge",
            }
            metrics = copy.deepcopy(state.get("parallel_branch_metrics") or {})
            post_duration_ms = int((time.perf_counter() - started) * 1000)
            metrics.update(
                {
                    "post_store_semantic_router_duration_ms": 0,
                    "post_store_retrieval_duration_ms": int(semantic_output.get("duration_ms") or 0),
                    "post_store_evidence_elapsed_ms": post_duration_ms,
                    "pre_reply_evidence_elapsed_ms": int(metrics.get("pre_reply_evidence_elapsed_ms") or 0)
                    + post_duration_ms,
                    "semantic_route_summary": _semantic_route_observability(semantic_route),
                    "material_selection": material_audit,
                }
            )
            span["entry"]["tool_calls"] = [
                {"name": "deterministic_post_store_retrieval", "output": _branch_trace_output(semantic_route)},
                {"name": "follow_knowledge_api", "output": _branch_trace_output(sales_recall)},
            ]
            span["output_snapshot"] = {
                "checkpoint": (semantic_route.get("checkpoint") or {}).get("primary_code"),
                "store_resolution_status": store_fact.get("status"),
                "sales_recall_status": sales_recall.get("status"),
                "sales_recall_candidates": sales_recall.get("candidate_count"),
                "selected_content_ids": gate_result.get("content_candidate_ids") or [],
                "metrics": metrics,
                "material_selection": material_audit,
            }
            return {
                "content_gate_result": gate_result,
                "sales_recall": sales_recall,
                "cardpoint_candidates": [],
                "followup_strategy_candidates": [],
                "sales_strategy_retrieval_audit": {
                    "runtime_mode": "off",
                    "candidate_count": 0,
                    "filtered": [],
                    "catalog": {"source": "configured_closing_catalog"},
                    "reply_effect": False,
                },
                "semantic_route": semantic_route,
                "knowledge_evidence": sales_recall,
                "parallel_branch_metrics": metrics,
                "trace": state.get("trace", []),
            }

    return post_store_semantic_evidence


def create_semantic_evidence_node(
    *,
    trace_logger: TraceLogger,
    model_client: ModelClient | None,
    sop_execution_service: SopExecutionService | None,
    coze_client: CozeClient | None = None,
    semantic_router_service: V3SemanticRouterService | None = None,
    sales_strategy_service: SalesStrategyService | None = None,
) -> Callable[[AgentState], Any]:
    async def parallel_evidence(state: AgentState) -> dict[str, Any]:
        started = time.perf_counter()
        with trace_logger.node(
            state,
            "v3_semantic_route_and_knowledge",
            {"shared_context_schema": (state.get("shared_context") or {}).get("schema_version")},
        ) as span:
            protocol_required = _protocol_required_read_only_tools(state)
            force_store_required = any(
                str(item.get("name") or "").strip() == "resolve_customer_store"
                for item in protocol_required
                if isinstance(item, dict)
            )
            if semantic_router_service is None:
                semantic_output = {
                    "status": "degraded",
                    "semantic_route": {"status": "disabled", "reason": "semantic_router_not_configured"},
                    "knowledge_evidence": {"status": "disabled", "candidates": [], "sequence_candidates": []},
                    "tool_plan": {
                        "status": "completed",
                        "decision": "facts_sufficient",
                        "tool_calls": [],
                        "missing_facts": [],
                        "evidence_refs": [],
                    },
                }
            else:
                try:
                    semantic_output = await semantic_router_service.route(
                        shared_context=copy.deepcopy(state.get("shared_context") or {}),
                        sequence_result=copy.deepcopy(state.get("follow_sequence_index") or {}),
                        taxonomy_result=copy.deepcopy(state.get("follow_checkpoint_taxonomy") or {}),
                        closing_catalog_result=copy.deepcopy(state.get("closing_catalog") or {}),
                        force_store_required=force_store_required,
                    )
                except Exception as exc:
                    semantic_output = {
                        "status": "degraded",
                        "semantic_route": {"status": "error", "reason": f"{type(exc).__name__}: {exc}"[:500]},
                        "knowledge_evidence": {"status": "error", "candidates": [], "sequence_candidates": []},
                        "tool_plan": {
                            "status": "completed",
                            "decision": "facts_sufficient",
                            "tool_calls": [],
                            "missing_facts": [],
                            "evidence_refs": [],
                        },
                    }
            semantic_route = copy.deepcopy(semantic_output.get("semantic_route") or {})
            sales_recall = copy.deepcopy(semantic_output.get("knowledge_evidence") or {})
            tool_plan = copy.deepcopy(semantic_output.get("tool_plan") or {})
            tool_plan["tool_calls"] = _merge_tool_calls(
                _dict_list(tool_plan.get("tool_calls")),
                protocol_required,
            )
            if tool_plan["tool_calls"]:
                tool_plan["decision"] = "use_tools"
            assets = _dict_list((state.get("shared_context") or {}).get("available_assets"))
            recalled_candidates = script_content_candidates(
                sales_recall,
                sent_image_urls=_sent_case_image_urls(state),
            )
            content_candidates, material_audit = await _diverse_content_candidates(
                state,
                [
                    *assets,
                    *recalled_candidates,
                ]
            )
            gate_result = {
                "schema_version": "v3_asset_catalog_result_v1",
                "status": "completed",
                "content_candidate_ids": [item["content_id"] for item in content_candidates],
                "content_candidates": content_candidates,
                "reason": "approved_assets_plus_semantic_knowledge",
            }
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            metrics = {
                "elapsed_ms": elapsed_ms,
                "semantic_router_duration_ms": int(semantic_output.get("duration_ms") or 0),
                "sales_recall_duration_ms": int(sales_recall.get("duration_ms") or 0),
                "pre_reply_evidence_elapsed_ms": elapsed_ms,
                "semantic_route_summary": _semantic_route_observability(semantic_route),
                "material_selection": material_audit,
            }
            output = {
                "content_gate_result": gate_result,
                "tool_plan": tool_plan,
                "sales_recall": sales_recall,
                "cardpoint_candidates": [],
                "followup_strategy_candidates": [],
                "sales_strategy_retrieval_audit": {
                    "runtime_mode": "off",
                    "candidate_count": 0,
                    "filtered": [],
                    "catalog": {"source": "configured_closing_catalog"},
                    "reply_effect": False,
                },
                "semantic_route": semantic_route,
                "store_pre_route": (
                    copy.deepcopy(semantic_route)
                    if str(semantic_route.get("phase") or "") == "pre_store_pending"
                    else {}
                ),
                "knowledge_evidence": sales_recall,
                "parallel_branch_metrics": metrics,
                # Compatibility input for the existing read-only executor.
                "planner_tool_calls": list(tool_plan.get("tool_calls") or []),
                "required_tools": list(tool_plan.get("tool_calls") or []),
                "planner_source": "v3_semantic_router_store_only",
                "trace": state.get("trace", []),
            }
            span["entry"]["tool_calls"] = [
                {"name": "deepseek_semantic_router", "output": _branch_trace_output(semantic_route)},
                {"name": "follow_knowledge_api", "output": _branch_trace_output(sales_recall)},
                {"name": "v3_store_tool_plan", "output": _branch_trace_output(tool_plan)},
            ]
            span["output_snapshot"] = {
                "checkpoint": (semantic_route.get("checkpoint") or {}).get("primary_code"),
                "selected_content_ids": gate_result.get("content_candidate_ids") or [],
                "tool_names": [item.get("name") for item in tool_plan.get("tool_calls") or []],
                "tool_plan_status": tool_plan.get("status"),
                "tool_plan_reason": tool_plan.get("reason"),
                "tool_plan_violations": tool_plan.get("violations") or [],
                "tool_plan_initial_violations": tool_plan.get("initial_violations") or [],
                "tool_plan_decision": tool_plan.get("decision"),
                "sales_recall_status": sales_recall.get("status"),
                "sales_recall_candidates": sales_recall.get("candidate_count"),
                "sales_strategy_candidate_count": 0,
                "sales_strategy_runtime_mode": "off",
                "follow_sequence_candidates": sales_recall.get("selected_sequence_count"),
                "follow_knowledge_selector_status": (
                    (sales_recall.get("selector") or {}).get("status")
                    if isinstance(sales_recall.get("selector"), dict)
                    else ""
                ),
                "missing_fact_count": len(tool_plan.get("missing_facts") or []),
                "metrics": metrics,
                "material_selection": material_audit,
            }
            return output

    return parallel_evidence
