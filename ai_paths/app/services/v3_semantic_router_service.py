from __future__ import annotations

import asyncio
import copy
import json
import re
import time
from typing import Any
from urllib.parse import urlparse

from app.prompts.v3_semantic_router import (
    build_v3_checkpoint_router_messages,
    build_v3_script_prefilter_messages,
    build_v3_script_selector_messages,
    build_v3_sequence_selector_messages,
)
from app.policies.business_rules import v3_fact_topic_catalog_for_model
from app.services.deepseek_semantic_client import DeepSeekSemanticClient
from app.services.follow_knowledge_client import ACTION_CODES, FollowKnowledgeClient


MAX_SEQUENCE_CANDIDATES = 3
MAX_SEQUENCE_STEPS_TOTAL = 4
MAX_STEPS_PER_SEQUENCE = 4
MAX_PARAGRAPH_GROUPS = 6


class V3SemanticRouterService:
    """Select retrieval evidence and store-tool need without making a sales decision."""

    def __init__(
        self,
        *,
        semantic_client: DeepSeekSemanticClient,
        knowledge_client: FollowKnowledgeClient | None,
        script_threshold: int = 12,
        max_scripts: int = 6,
    ) -> None:
        self.semantic_client = semantic_client
        self.knowledge_client = knowledge_client
        self.script_threshold = max(1, int(script_threshold or 12))
        self.max_scripts = max(1, min(int(max_scripts or 6), 12))

    @property
    def available(self) -> bool:
        return self.semantic_client.available

    async def load_sequence_index(self) -> dict[str, Any]:
        return await self._sequence_index()

    async def load_checkpoint_taxonomy(self) -> dict[str, Any]:
        if self.knowledge_client is None or not self.knowledge_client.available:
            return {"status": "disabled", "types": []}
        loader = getattr(self.knowledge_client, "query_script_taxonomy", None)
        if loader is None:
            return {"status": "unavailable", "types": []}
        return await loader()

    async def load_closing_catalog(self) -> dict[str, Any]:
        if self.knowledge_client is None or not bool(
            getattr(self.knowledge_client, "closing_catalog_available", self.knowledge_client.available)
        ):
            return {
                "schema_version": "closing_catalog_v1",
                "status": "disabled",
                "reason": "follow_knowledge_not_configured",
                "rules": {},
                "sequences": [],
            }
        loader = getattr(self.knowledge_client, "query_closing_catalog", None)
        if loader is None:
            return {
                "schema_version": "closing_catalog_v1",
                "status": "unavailable",
                "reason": "closing_catalog_client_unavailable",
                "rules": {},
                "sequences": [],
            }
        return await loader()

    async def route(
        self,
        *,
        shared_context: dict[str, Any],
        sequence_result: dict[str, Any] | None = None,
        taxonomy_result: dict[str, Any] | None = None,
        closing_catalog_result: dict[str, Any] | None = None,
        force_store_required: bool = False,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        if not isinstance(sequence_result, dict) and not isinstance(taxonomy_result, dict):
            sequence_result, taxonomy_result = await asyncio.gather(
                self._sequence_index(),
                self.load_checkpoint_taxonomy(),
            )
        else:
            sequence_result = sequence_result if isinstance(sequence_result, dict) else await self._sequence_index()
            taxonomy_result = (
                taxonomy_result
                if isinstance(taxonomy_result, dict)
                else await self.load_checkpoint_taxonomy()
            )
        sequences = [item for item in sequence_result.get("items") or [] if isinstance(item, dict)]
        taxonomy = _taxonomy_types(taxonomy_result, sequences=sequences)
        fact_topics = v3_fact_topic_catalog_for_model()
        closing_catalog = _normalized_closing_catalog(closing_catalog_result)
        router_messages = build_v3_checkpoint_router_messages(
            shared_context=shared_context,
            checkpoint_taxonomy=taxonomy,
            sequence_index=[],
            fact_topic_catalog=fact_topics,
            closing_catalog=closing_catalog,
        )
        router_started = time.perf_counter()
        try:
            raw_route = await self.semantic_client.chat_json(router_messages)
            route_error = ""
        except Exception as exc:
            raw_route = {}
            route_error = f"{type(exc).__name__}: {exc}"[:500]
        semantic_route = _normalize_semantic_route(
            raw_route,
            shared_context=shared_context,
            sequences=sequences,
            checkpoint_taxonomy=taxonomy,
            fact_topic_catalog=fact_topics,
            closing_catalog=closing_catalog,
        )
        contract_issues = _semantic_route_contract_issues(semantic_route)
        if force_store_required:
            semantic_route.setdefault("store_query", {})["required"] = True
            semantic_route["store_query"]["purpose"] = str(
                semantic_route["store_query"].get("purpose") or "store_resolution"
            )
        if route_error:
            semantic_route.update({"status": "error", "reason": route_error})
        semantic_route["duration_ms"] = int((time.perf_counter() - router_started) * 1000)
        semantic_route["model_usage"] = copy.deepcopy(self.semantic_client.last_usage or {})
        semantic_route["contract_repair_used"] = False
        semantic_route["contract_issues"] = _semantic_route_contract_issues(semantic_route)
        if contract_issues and not route_error:
            semantic_route.update(
                {
                    "status": "degraded",
                    "reason": "semantic_contract_missing_references",
                }
            )
        semantic_route["closing_catalog_evidence"] = _closing_catalog_evidence(
            closing_catalog,
            semantic_route.get("closing_catalog_match"),
        )
        checkpoint_router_ms = int(semantic_route["duration_ms"])

        if bool((semantic_route.get("store_query") or {}).get("required")):
            pre_route = _deferred_store_pre_route(semantic_route)
            total_ms = int((time.perf_counter() - started) * 1000)
            return {
                "schema_version": "v3_semantic_evidence_v2",
                "status": "ok" if semantic_route.get("status") == "ok" else "degraded",
                "semantic_route": pre_route,
                "knowledge_evidence": _deferred_store_knowledge(sequence_result),
                "tool_plan": _store_tool_plan(pre_route),
                "duration_ms": total_ms,
                "timings": {
                    "checkpoint_router_ms": checkpoint_router_ms,
                    "sequence_selector_ms": 0,
                    "knowledge_ms": 0,
                    "total_ms": total_ms,
                },
                "prompt_preview": {
                    "router_messages": router_messages,
                    "sequence_selector_messages": [],
                    "selector_messages": [],
                },
            }

        semantic_route = _apply_deterministic_sequence_top_k(
            semantic_route,
            shared_context=shared_context,
            sequences=sequences,
        )
        semantic_route["closing_catalog_evidence"] = _closing_catalog_evidence(
            closing_catalog,
            semantic_route.get("closing_catalog_match"),
        )
        semantic_route["phase"] = "non_store_final"
        knowledge, selector_messages = await self._knowledge_for_route(
            shared_context=shared_context,
            semantic_route=semantic_route,
            sequence_result=sequence_result,
            sequences=sequences,
            checkpoint_taxonomy=taxonomy,
        )
        total_ms = int((time.perf_counter() - started) * 1000)
        return {
            "schema_version": "v3_semantic_evidence_v2",
            "status": "ok" if semantic_route.get("status") == "ok" else "degraded",
            "semantic_route": semantic_route,
            "knowledge_evidence": knowledge,
            "tool_plan": _store_tool_plan(semantic_route),
            "duration_ms": total_ms,
            "timings": {
                "checkpoint_router_ms": checkpoint_router_ms,
                "sequence_selector_ms": 0,
                "knowledge_ms": int(knowledge.get("duration_ms") or 0),
                "total_ms": total_ms,
            },
            "prompt_preview": {
                "router_messages": router_messages,
                "sequence_selector_messages": [],
                "selector_messages": [],
            },
        }

    async def complete_after_store(
        self,
        *,
        shared_context: dict[str, Any],
        pre_route: dict[str, Any],
        store_resolution_fact: dict[str, Any],
        sequence_result: dict[str, Any] | None = None,
        taxonomy_result: dict[str, Any] | None = None,
        closing_catalog_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        sequence_result = sequence_result if isinstance(sequence_result, dict) else await self._sequence_index()
        sequences = [item for item in sequence_result.get("items") or [] if isinstance(item, dict)]
        provisional = (
            pre_route.get("provisional_checkpoint")
            if isinstance(pre_route.get("provisional_checkpoint"), dict)
            else pre_route.get("checkpoint")
        )
        checkpoint_route = {
            "schema_version": "v3_semantic_route_v1",
            "status": "ok",
            "classification_status": str(pre_route.get("classification_status") or "none"),
            "checkpoint": copy.deepcopy(provisional or {}),
            "current_intent": copy.deepcopy(pre_route.get("current_intent") or {}),
            "current_friction": copy.deepcopy(
                pre_route.get("provisional_current_friction")
                or pre_route.get("current_friction")
                or {}
            ),
            "historical_unresolved_friction": copy.deepcopy(
                pre_route.get("historical_unresolved_friction") or {}
            ),
            "relevant_fact_topic_ids": list(pre_route.get("relevant_fact_topic_ids") or []),
            "store_query": copy.deepcopy(pre_route.get("store_query") or {}),
            "sequence_match": _empty_sequence_match(),
            "script_queries": [],
            "closing_catalog_match": copy.deepcopy(pre_route.get("closing_catalog_match") or {}),
        }
        taxonomy_result = (
            taxonomy_result
            if isinstance(taxonomy_result, dict)
            else await self.load_checkpoint_taxonomy()
        )
        taxonomy = _taxonomy_types(taxonomy_result, sequences=sequences)
        closing_catalog = _normalized_closing_catalog(closing_catalog_result)
        semantic_route = _apply_deterministic_sequence_top_k(
            checkpoint_route,
            shared_context=shared_context,
            sequences=sequences,
        )
        semantic_route["phase"] = "post_store_final"
        semantic_route["provisional_checkpoint"] = copy.deepcopy(
            pre_route.get("provisional_checkpoint") or pre_route.get("checkpoint") or {}
        )
        semantic_route["store_query"] = {
            "required": False,
            "purpose": "store_query_already_completed",
            "location_evidence_refs": list(
                (pre_route.get("store_query") or {}).get("location_evidence_refs") or []
            ),
            "destination_hint": str(
                (pre_route.get("store_query") or {}).get("destination_hint") or ""
            ),
        }
        semantic_route["closing_catalog_evidence"] = _closing_catalog_evidence(
            closing_catalog,
            semantic_route.get("closing_catalog_match"),
        )
        semantic_route["store_result_interpretation"] = {
            "status": str(store_resolution_fact.get("status") or "unknown"),
            "resolved_by_reply": True,
            "reason": "authoritative_store_fact_deferred_to_final_reply",
        }
        knowledge, selector_messages = await self._knowledge_for_route(
            shared_context=shared_context,
            semantic_route=semantic_route,
            sequence_result=sequence_result,
            sequences=sequences,
            checkpoint_taxonomy=taxonomy,
        )
        total_ms = int((time.perf_counter() - started) * 1000)
        return {
            "schema_version": "v3_semantic_evidence_v2",
            "status": "ok" if semantic_route.get("status") == "ok" else "degraded",
            "semantic_route": semantic_route,
            "knowledge_evidence": knowledge,
            "tool_plan": _store_tool_plan(semantic_route),
            "duration_ms": total_ms,
            "timings": {
                "checkpoint_router_ms": 0,
                "sequence_selector_ms": 0,
                "knowledge_ms": int(knowledge.get("duration_ms") or 0),
                "total_ms": total_ms,
            },
            "prompt_preview": {
                "router_messages": [],
                "sequence_selector_messages": [],
                "selector_messages": [],
            },
        }

    async def _select_sequence_route(
        self,
        *,
        shared_context: dict[str, Any],
        checkpoint_route: dict[str, Any],
        sequences: list[dict[str, Any]],
        store_resolution_fact: dict[str, Any] | None = None,
        checkpoint_taxonomy: list[dict[str, Any]] | None = None,
        fact_topic_catalog: list[dict[str, Any]] | None = None,
        closing_catalog: dict[str, Any] | None = None,
        allow_closing_rematch: bool = False,
    ) -> tuple[dict[str, Any], list[dict[str, str]]]:
        """Ask the model to select only among metadata-filtered real sequences."""

        candidates = _sequences_for_checkpoint(sequences, checkpoint_route)
        if not candidates and not isinstance(store_resolution_fact, dict):
            output = copy.deepcopy(checkpoint_route)
            output["sequence_match"] = _empty_sequence_match()
            output["script_queries"] = []
            output["duration_ms"] = 0
            output["model_usage"] = {}
            return output, []

        messages = build_v3_sequence_selector_messages(
            shared_context=shared_context,
            checkpoint_route=checkpoint_route,
            sequence_candidates=candidates,
            checkpoint_taxonomy=checkpoint_taxonomy or [],
            fact_topic_catalog=fact_topic_catalog or [],
            store_resolution_fact=store_resolution_fact,
            closing_catalog=closing_catalog if allow_closing_rematch else None,
        )
        started = time.perf_counter()
        try:
            raw_selection = await self.semantic_client.chat_json(messages)
            error = ""
        except Exception as exc:
            raw_selection = {}
            error = f"{type(exc).__name__}: {exc}"[:500]
        payload = copy.deepcopy(raw_selection) if isinstance(raw_selection, dict) else {}
        # The first pass owns intent and friction extraction. The selector may
        # choose evidence candidates, but it must not become a second semantic
        # router or erase the first pass's message references.
        for key in (
            "classification_status",
            "current_intent",
            "current_friction",
            "historical_unresolved_friction",
            "knowledge_focus",
            "checkpoint",
            "store_query",
        ):
            payload[key] = copy.deepcopy(checkpoint_route.get(key))
        if not allow_closing_rematch:
            payload["closing_catalog_match"] = copy.deepcopy(
                checkpoint_route.get("closing_catalog_match")
            )
        # The selector may add fact topics made relevant by the store result,
        # but it must not erase topics already selected for the customer's
        # current question by the first model pass.
        selected_fact_topics = [
            str(item).strip()
            for item in payload.get("relevant_fact_topic_ids") or []
            if str(item or "").strip()
        ]
        payload["relevant_fact_topic_ids"] = list(
            dict.fromkeys(
                [
                    *(
                        str(item).strip()
                        for item in checkpoint_route.get("relevant_fact_topic_ids") or []
                        if str(item or "").strip()
                    ),
                    *selected_fact_topics,
                ]
            )
        )[:3]
        payload["script_queries"] = []
        output = _normalize_semantic_route(
            payload,
            shared_context=shared_context,
            sequences=candidates,
            checkpoint_taxonomy=checkpoint_taxonomy,
            fact_topic_catalog=fact_topic_catalog,
            closing_catalog=closing_catalog if allow_closing_rematch else None,
        )
        if not allow_closing_rematch:
            output["closing_catalog_match"] = copy.deepcopy(
                checkpoint_route.get("closing_catalog_match") or {}
            )
        output["duration_ms"] = int((time.perf_counter() - started) * 1000)
        output["model_usage"] = copy.deepcopy(self.semantic_client.last_usage or {})
        if error:
            output.update({"status": "error", "reason": error})
        if isinstance(store_resolution_fact, dict):
            output["store_result_interpretation"] = _normalize_store_result_interpretation(
                raw_selection,
                shared_context=shared_context,
            )
        return _expand_sequence_action_queries(output, sequences=candidates), messages

    async def _knowledge_for_route(
        self,
        *,
        shared_context: dict[str, Any],
        semantic_route: dict[str, Any],
        sequence_result: dict[str, Any],
        sequences: list[dict[str, Any]],
        checkpoint_taxonomy: list[dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, str]]]:
        started = time.perf_counter()
        sequence_candidates = _selected_sequences(sequences, semantic_route)
        script_result = await self._script_candidates(
            semantic_route,
            checkpoint_taxonomy=checkpoint_taxonomy or [],
        )
        script_candidates = [item for item in script_result.get("items") or [] if isinstance(item, dict)]
        raw_paragraph_group_count = _paragraph_group_count(script_candidates)
        script_candidates = _rank_script_groups(
            script_candidates,
            query_text=_semantic_retrieval_text(shared_context, semantic_route),
            max_groups=MAX_PARAGRAPH_GROUPS,
        )
        retrieval_mode = (
            "same_type_action_relaxed"
            if any(
                bool(item.get("fallback_used"))
                for item in script_result.get("query_results") or []
                if isinstance(item, dict)
            )
            else "deterministic_top_k"
        )
        selector_for_runtime = {
            "status": "not_needed",
            "reason": "deterministic_top_k",
            "retrieval_mode": retrieval_mode,
            "candidate_count": len(script_candidates),
            "paragraph_candidate_count": _paragraph_group_count(script_candidates),
            "duration_ms": 0,
        }
        knowledge = {
            "schema_version": "v3_knowledge_evidence_v1",
            "status": "ok" if sequence_candidates or script_candidates else "empty",
            "source": "follow_knowledge_api",
            "sequence_index_status": sequence_result.get("status"),
            "sequence_index_total": int(sequence_result.get("total") or 0),
            "sequence_candidates": sequence_candidates,
            "script_query_results": script_result.get("query_results") or [],
            "support_level": script_result.get("support_level") or (
                "sequence_only" if sequence_candidates else "none"
            ),
            "script_option_count": int(script_result.get("option_count") or 0),
            "paragraph_option_count": raw_paragraph_group_count,
            "paragraph_candidate_count": _paragraph_group_count(script_candidates),
            "candidate_count": len(script_candidates),
            "candidates": [_script_reference(item) for item in script_candidates],
            "selector": selector_for_runtime,
            "retrieval_mode": retrieval_mode,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
        return knowledge, []

    async def _sequence_index(self) -> dict[str, Any]:
        if self.knowledge_client is None or not self.knowledge_client.available:
            return {"status": "disabled", "reason": "follow_knowledge_not_configured", "total": 0, "items": []}
        return await self.knowledge_client.query_all_sequences()

    async def _script_candidates(
        self,
        semantic_route: dict[str, Any],
        *,
        checkpoint_taxonomy: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        route_queries = (
            semantic_route.get("script_queries")
            if isinstance(semantic_route.get("script_queries"), list)
            else []
        )
        queries = [
            *route_queries,
            *_closing_script_queries(semantic_route, existing_queries=route_queries),
        ]
        if self.knowledge_client is None or not queries:
            return {"status": "empty", "option_count": 0, "items": [], "query_results": []}
        tasks = [
            self._query_scripts_for_route(item)
            for item in queries
        ]
        results = await asyncio.gather(*tasks)
        fallback_tasks: list[Any] = []
        fallback_indexes: list[int] = []
        has_any_ordinary_exact = any(
            str(query.get("query_source") or "") != "closing_catalog_node"
            and str(result.get("status") or "") == "ok"
            and int(result.get("total") or 0) > 0
            for query, result in zip(queries, results)
        )
        for index, (query, result) in enumerate(zip(queries, results)):
            if (
                not has_any_ordinary_exact
                and not fallback_tasks
                and str(query.get("query_source") or "") != "closing_catalog_node"
                and str(result.get("status") or "") == "ok"
                and int(result.get("total") or 0) == 0
                and int(query.get("checkpoint_type_id") or 0) > 0
                and int(query.get("checkpoint_tag_id") or 0) > 0
                and str(query.get("action_code") or "").strip()
            ):
                fallback_indexes.append(index)
                fallback_tasks.append(
                    self.knowledge_client.query_all_scripts(
                        checkpoint_type_id=int(query.get("checkpoint_type_id") or 0),
                        checkpoint_tag_id=None,
                        checkpoint_code="",
                        action_code=str(query.get("action_code") or ""),
                    )
                )
        fallback_results = await asyncio.gather(*fallback_tasks) if fallback_tasks else []
        fallbacks_by_index = dict(zip(fallback_indexes, fallback_results))
        by_code: dict[str, dict[str, Any]] = {}
        query_results: list[dict[str, Any]] = []
        exact_candidate_count = 0
        broad_candidate_count = 0
        for index, (query, exact_result) in enumerate(zip(queries, results)):
            fallback_result = fallbacks_by_index.get(index)
            result = fallback_result if isinstance(fallback_result, dict) else exact_result
            fallback_used = isinstance(fallback_result, dict)
            requested_tag_id = int(query.get("checkpoint_tag_id") or 0)
            match_scope = (
                "closing_script_type"
                if str(query.get("query_source") or "") == "closing_catalog_node"
                else "checkpoint_type_tag_action"
                if requested_tag_id > 0 and not fallback_used
                else "checkpoint_type_action"
            )
            query_audit = {
                    "checkpoint_code": query.get("checkpoint_code"),
                    "checkpoint_type_id": query.get("checkpoint_type_id"),
                    "checkpoint_tag_id": query.get("checkpoint_tag_id"),
                    "action_code": query.get("action_code"),
                    "sequence_id": query.get("sequence_id"),
                    "step_id": query.get("step_id"),
                    "query_source": query.get("query_source"),
                    "sequence_links": copy.deepcopy(query.get("sequence_links") or []),
                    "status": result.get("status"),
                    "total": int(result.get("total") or 0),
                    "reason": result.get("reason", ""),
                    "source": result.get("source", ""),
                    "duration_ms": int(result.get("duration_ms") or 0),
                    "cache_hit_pages": int(result.get("cache_hit_pages") or 0),
                    "match_scope": match_scope,
                    "fallback_used": fallback_used,
                    "exact_total": int(exact_result.get("total") or 0),
                    "fallback_total": int(fallback_result.get("total") or 0)
                    if isinstance(fallback_result, dict)
                    else 0,
                    "rejected_type_mismatch_count": 0,
                    "rejected_tag_mismatch_count": 0,
                    "rejected_action_mismatch_count": 0,
                }
            query_results.append(query_audit)
            for raw in result.get("items") or []:
                if not isinstance(raw, dict):
                    continue
                returned_type = (
                    raw.get("checkpoint_type")
                    if isinstance(raw.get("checkpoint_type"), dict)
                    else {}
                )
                requested_type_id = int(query.get("checkpoint_type_id") or 0)
                if requested_type_id > 0 and int(returned_type.get("id") or 0) != requested_type_id:
                    query_audit["rejected_type_mismatch_count"] += 1
                    continue
                if str(query.get("query_source") or "") != "closing_catalog_node":
                    returned_action = str(raw.get("action_code") or "").strip().lower()
                    requested_action = str(query.get("action_code") or "").strip().lower()
                    if requested_action and returned_action != requested_action:
                        query_audit["rejected_action_mismatch_count"] += 1
                        continue
                    returned_tag = (
                        raw.get("checkpoint_tag")
                        if isinstance(raw.get("checkpoint_tag"), dict)
                        else {}
                    )
                    if (
                        requested_tag_id > 0
                        and not fallback_used
                        and int(returned_tag.get("id") or 0) != requested_tag_id
                    ):
                        query_audit["rejected_tag_mismatch_count"] += 1
                        continue
                code = str(raw.get("script_code") or "").strip()
                if not code:
                    continue
                item = by_code.setdefault(code, copy.deepcopy(raw))
                existing_scope = str(item.get("retrieval_match_scope") or "")
                if not existing_scope or match_scope == "checkpoint_type_tag_action":
                    item["retrieval_match_scope"] = match_scope
                links = item.setdefault("sequence_links", [])
                query_links = query.get("sequence_links")
                if not isinstance(query_links, list) or not query_links:
                    query_links = [
                        {
                            "sequence_id": str(query.get("sequence_id") or ""),
                            "step_id": str(query.get("step_id") or ""),
                            "action_code": str(query.get("action_code") or ""),
                            "query_source": str(query.get("query_source") or ""),
                        }
                    ]
                for raw_link in query_links:
                    if not isinstance(raw_link, dict):
                        continue
                    link = {
                        **copy.deepcopy(raw_link),
                        "sequence_id": str(raw_link.get("sequence_id") or ""),
                        "step_id": str(raw_link.get("step_id") or ""),
                        "action_code": str(raw_link.get("action_code") or ""),
                        "match_scope": match_scope,
                        "query_source": str(
                            raw_link.get("query_source") or query.get("query_source") or ""
                        ),
                    }
                    if link not in links:
                        links.append(link)
                if match_scope == "checkpoint_type_action":
                    broad_candidate_count += 1
                else:
                    exact_candidate_count += 1

        support_level = (
            "script_exact"
            if exact_candidate_count and not broad_candidate_count
            else "script_mixed"
            if exact_candidate_count and broad_candidate_count
            else "script_broad"
            if broad_candidate_count
            else "sequence_only"
        )
        return {
            "status": "ok" if by_code else "empty",
            "support_level": support_level,
            "option_count": len(by_code),
            "items": list(by_code.values()),
            "query_results": query_results,
        }

    async def _query_scripts_for_route(self, query: dict[str, Any]) -> dict[str, Any]:
        if (
            str(query.get("query_source") or "") == "closing_catalog_node"
            and hasattr(self.knowledge_client, "query_closing_scripts")
        ):
            return await self.knowledge_client.query_closing_scripts(
                checkpoint_type_id=int(query.get("checkpoint_type_id") or 0),
                catalog_source=str(query.get("catalog_source") or ""),
            )
        return await self.knowledge_client.query_all_scripts(
            checkpoint_type_id=int(query.get("checkpoint_type_id") or 0) or None,
            checkpoint_tag_id=int(query.get("checkpoint_tag_id") or 0) or None,
            checkpoint_code=str(query.get("checkpoint_code") or ""),
            action_code=str(query.get("action_code") or ""),
        )

    async def _narrow_scripts(
        self,
        *,
        shared_context: dict[str, Any],
        semantic_route: dict[str, Any],
        candidates: list[dict[str, Any]],
        max_scripts: int | None = None,
        max_paragraph_groups: int = MAX_PARAGRAPH_GROUPS,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        limit = max(1, int(max_scripts or self.max_scripts))
        messages = build_v3_script_selector_messages(
            shared_context=shared_context,
            semantic_route=semantic_route,
            candidates=candidates,
            max_scripts=limit,
            max_paragraph_groups=max_paragraph_groups,
        )
        started = time.perf_counter()
        structure_retry_used = False
        try:
            try:
                raw = await self.semantic_client.chat_json(messages)
            except Exception:
                structure_retry_used = True
                retry_messages = [
                    *messages,
                    {
                        "role": "user",
                        "content": (
                            "上一次输出不是合法 JSON。不要重新判断业务语义，只修正结构："
                            "输出单行紧凑 JSON，只包含 group_audits、selected_script_ids、reason；"
                            "每个 reason_code 必须来自枚举，reason 不超过 20 个汉字。"
                        ),
                    },
                ]
                raw = await self.semantic_client.chat_json(retry_messages)
                messages = retry_messages
            valid_ids = {str(item.get("script_code") or "") for item in candidates}
            paragraph_script_ids = {
                str(item.get("script_code") or "")
                for item in candidates
                if any(isinstance(value, dict) for value in item.get("paragraphs") or [])
            }
            valid_paragraph_pairs = {
                (str(item.get("script_code") or ""), int(paragraph.get("paragraph_no") or 0))
                for item in candidates
                for paragraph in item.get("paragraphs") or []
                if isinstance(paragraph, dict) and int(paragraph.get("paragraph_no") or 0) > 0
            }
            selected = []
            for value in raw.get("selected_script_ids") or []:
                script_id = str(value or "").strip()
                # Paragraph-backed knowledge must be selected at paragraph
                # granularity. Whole-script IDs only support legacy rows that
                # genuinely have no paragraph structure.
                if (
                    script_id in valid_ids
                    and script_id not in paragraph_script_ids
                    and script_id not in selected
                ):
                    selected.append(script_id)
                if len(selected) >= limit:
                    break
            selected_groups: list[tuple[str, int]] = []
            selected_group_audit: list[dict[str, Any]] = []
            valid_message_refs = _valid_message_refs(shared_context)
            group_audits: list[dict[str, Any]] = []
            audit_by_pair: dict[tuple[str, int], dict[str, Any]] = {}
            for item in raw.get("group_audits") or []:
                if not isinstance(item, dict):
                    continue
                script_id = str(item.get("script_id") or "").strip()
                paragraph_no = int(item.get("paragraph_no") or 0)
                pair = (script_id, paragraph_no)
                if pair not in valid_paragraph_pairs or pair in audit_by_pair:
                    continue
                decision = str(item.get("decision") or "").strip()
                reason_code = str(item.get("reason_code") or "").strip()
                authority_status = str(item.get("authority_status") or "").strip()
                action_fit = str(item.get("action_fit") or "").strip()
                evidence_refs = [
                    str(value).strip()
                    for value in item.get("evidence_refs") or []
                    if str(value).strip() in valid_message_refs
                ]
                if decision not in {"select", "exclude"}:
                    continue
                if reason_code not in {
                    "hard_fact_conflict",
                    "action_not_supported",
                    "irrelevant",
                    "duplicate",
                    "selected",
                }:
                    continue
                audit = {
                    "script_id": script_id,
                    "paragraph_no": paragraph_no,
                    "decision": decision,
                    "reason_code": reason_code,
                    "evidence_refs": list(dict.fromkeys(evidence_refs)),
                    "authority_status": authority_status,
                    "action_fit": action_fit,
                    "reason": str(item.get("reason") or "")[:300],
                }
                audit_by_pair[pair] = audit
                group_audits.append(audit)

                # The compact selector contract records each paragraph once.
                # A fully auditable select decision is sufficient; this is
                # lossless schema normalization, not a code-side semantic choice.
                if (
                    decision == "select"
                    and reason_code == "selected"
                    and evidence_refs
                    and authority_status == "pass"
                    and action_fit in {"direct", "supporting"}
                    and pair not in selected_groups
                    and len(selected_groups) < max_paragraph_groups
                ):
                    selected_groups.append(pair)
                    selected_group_audit.append(
                        {
                            "script_id": script_id,
                            "paragraph_no": paragraph_no,
                            "evidence_refs": list(dict.fromkeys(evidence_refs)),
                            "authority_status": authority_status,
                            "action_fit": action_fit,
                        }
                    )
            for item in raw.get("selected_groups") or []:
                if not isinstance(item, dict):
                    continue
                script_id = str(item.get("script_id") or "").strip()
                paragraph_no = int(item.get("paragraph_no") or 0)
                if script_id not in valid_ids or paragraph_no <= 0:
                    continue
                evidence_refs = [
                    str(value).strip()
                    for value in item.get("evidence_refs") or []
                    if str(value).strip() in valid_message_refs
                ]
                authority_status = str(item.get("authority_status") or "").strip()
                action_fit = str(item.get("action_fit") or "").strip()
                audit = audit_by_pair.get((script_id, paragraph_no)) or {}
                if not evidence_refs or authority_status != "pass" or action_fit not in {
                    "direct",
                    "supporting",
                } or audit.get("decision") != "select" or audit.get("authority_status") != "pass" or audit.get(
                    "action_fit"
                ) not in {"direct", "supporting"}:
                    continue
                pair = (script_id, paragraph_no)
                if pair not in selected_groups:
                    selected_groups.append(pair)
                    selected_group_audit.append(
                        {
                            "script_id": script_id,
                            "paragraph_no": paragraph_no,
                            "evidence_refs": list(dict.fromkeys(evidence_refs)),
                            "authority_status": authority_status,
                            "action_fit": action_fit,
                        }
                    )
                if len(selected_groups) >= max_paragraph_groups:
                    break
            excluded_groups: list[dict[str, Any]] = []
            for audit in group_audits:
                if audit.get("decision") != "exclude":
                    continue
                excluded_groups.append(
                    {
                        "script_id": audit["script_id"],
                        "paragraph_no": audit["paragraph_no"],
                        "reason_code": audit["reason_code"],
                    }
                )
            for item in raw.get("excluded_groups") or []:
                if not isinstance(item, dict):
                    continue
                script_id = str(item.get("script_id") or "").strip()
                paragraph_no = int(item.get("paragraph_no") or 0)
                reason_code = str(item.get("reason_code") or "").strip()
                if script_id not in valid_ids or paragraph_no <= 0:
                    continue
                if reason_code not in {"hard_fact_conflict", "action_not_supported", "irrelevant", "duplicate"}:
                    reason_code = "irrelevant"
                excluded = {
                    "script_id": script_id,
                    "paragraph_no": paragraph_no,
                    "reason_code": reason_code,
                }
                if excluded not in excluded_groups:
                    excluded_groups.append(excluded)
            narrowed = _filter_script_groups(
                candidates,
                selected_groups=selected_groups,
                selected_script_ids=selected,
                max_groups=max_paragraph_groups,
            )
            return (
                {
                    "status": "ok" if narrowed else "empty",
                    "reason": str(raw.get("reason") or "")[:500],
                    "selected_script_ids": [str(item.get("script_code") or "") for item in narrowed],
                    "selected_groups": selected_group_audit,
                    "group_audits": group_audits,
                    "excluded_groups": excluded_groups,
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                    "model_usage": copy.deepcopy(self.semantic_client.last_usage or {}),
                    "structure_retry_used": structure_retry_used,
                    "messages": messages,
                },
                narrowed,
            )
        except Exception as exc:
            return (
                {
                    "status": "error",
                    "reason": f"{type(exc).__name__}: {exc}"[:500],
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                    "structure_retry_used": structure_retry_used,
                    "messages": messages,
                },
                [],
            )

    async def _prefilter_scripts(
        self,
        *,
        shared_context: dict[str, Any],
        semantic_route: dict[str, Any],
        candidates: list[dict[str, Any]],
        max_paragraph_groups: int,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        limit = max(1, int(max_paragraph_groups or self.script_threshold))
        messages = build_v3_script_prefilter_messages(
            shared_context=shared_context,
            semantic_route=semantic_route,
            candidates=candidates,
            max_paragraph_groups=limit,
        )
        started = time.perf_counter()
        try:
            raw = await self.semantic_client.chat_json(messages)
            valid_message_refs = _valid_message_refs(shared_context)
            paragraph_pairs: set[tuple[str, int]] = set()
            legacy_ids: set[str] = set()
            for item in candidates:
                script_id = str(item.get("script_code") or "").strip()
                paragraphs = [
                    value
                    for value in item.get("paragraphs") or []
                    if isinstance(value, dict)
                ]
                if paragraphs:
                    paragraph_pairs.update(
                        (script_id, int(value.get("paragraph_no") or 0))
                        for value in paragraphs
                        if script_id and int(value.get("paragraph_no") or 0) > 0
                    )
                elif script_id:
                    legacy_ids.add(script_id)

            selected_pairs: list[tuple[str, int]] = []
            selected_legacy_ids: list[str] = []
            selected_audit: list[dict[str, Any]] = []
            for item in raw.get("selected_groups") or []:
                if not isinstance(item, dict):
                    continue
                script_id = str(item.get("script_id") or "").strip()
                paragraph_no = int(item.get("paragraph_no") or 0)
                refs = [
                    str(value).strip()
                    for value in item.get("evidence_refs") or []
                    if str(value).strip() in valid_message_refs
                ]
                if not refs:
                    continue
                if (script_id, paragraph_no) in paragraph_pairs:
                    pair = (script_id, paragraph_no)
                    if pair in selected_pairs:
                        continue
                    selected_pairs.append(pair)
                elif script_id in legacy_ids and paragraph_no == 1:
                    if script_id in selected_legacy_ids:
                        continue
                    selected_legacy_ids.append(script_id)
                else:
                    continue
                selected_audit.append(
                    {
                        "script_id": script_id,
                        "paragraph_no": paragraph_no,
                        "evidence_refs": list(dict.fromkeys(refs)),
                        "reason": str(item.get("reason") or "")[:120],
                    }
                )
                if len(selected_audit) >= limit:
                    break

            narrowed = _filter_script_groups(
                candidates,
                selected_groups=selected_pairs,
                selected_script_ids=selected_legacy_ids,
                max_groups=limit,
            )
            return (
                {
                    "status": "ok" if narrowed else "empty",
                    "reason": str(raw.get("reason") or "")[:300],
                    "selected_groups": selected_audit,
                    "candidate_count": len(candidates),
                    "paragraph_candidate_count": _paragraph_group_count(candidates),
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                    "model_usage": copy.deepcopy(self.semantic_client.last_usage or {}),
                    "messages": messages,
                },
                narrowed,
            )
        except Exception as exc:
            return (
                {
                    "status": "error",
                    "reason": f"{type(exc).__name__}: {exc}"[:500],
                    "candidate_count": len(candidates),
                    "paragraph_candidate_count": _paragraph_group_count(candidates),
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                    "messages": messages,
                },
                [],
            )

    async def _narrow_scripts_by_action(
        self,
        *,
        shared_context: dict[str, Any],
        semantic_route: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        groups: dict[str, list[dict[str, Any]]] = {}
        for item in candidates:
            action = str(item.get("action_code") or "unknown").strip().lower() or "unknown"
            groups.setdefault(action, []).append(item)
        if len(groups) <= 1:
            return await self._narrow_scripts(
                shared_context=shared_context,
                semantic_route=semantic_route,
                candidates=candidates,
            )

        per_group = max(1, self.max_scripts // len(groups))
        tasks = [
            self._narrow_scripts(
                shared_context=shared_context,
                semantic_route=semantic_route,
                candidates=items,
                max_scripts=per_group,
            )
            for items in groups.values()
        ]
        results = await asyncio.gather(*tasks)
        selected: list[dict[str, Any]] = []
        group_results: list[dict[str, Any]] = []
        for action, (selector, items) in zip(groups, results):
            group_results.append(
                {
                    "action_code": action,
                    "candidate_count": len(groups[action]),
                    "status": selector.get("status"),
                    "reason": selector.get("reason", ""),
                    "selected_script_ids": selector.get("selected_script_ids") or [],
                    "duration_ms": selector.get("duration_ms", 0),
                }
            )
            selected.extend(items)
        selected = selected[: self.max_scripts]
        return (
            {
                "status": "ok" if selected else "empty",
                "reason": "selected_per_action_group",
                "selected_script_ids": [str(item.get("script_code") or "") for item in selected],
                "groups": group_results,
                "duration_ms": max((int(item.get("duration_ms") or 0) for item in group_results), default=0),
            },
            selected,
        )


def _taxonomy_types(
    result: dict[str, Any] | None,
    *,
    sequences: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    items = [
        copy.deepcopy(item)
        for item in (result or {}).get("types") or []
        if isinstance(item, dict) and (int(item.get("id") or 0) > 0 or str(item.get("code") or "").strip())
    ]
    known_codes = {str(item.get("code") or "").strip().lower() for item in items}
    for sequence in sequences:
        code = str(sequence.get("checkpoint_code") or "").strip().lower()
        if not code or code == "all" or code in known_codes:
            continue
        items.append({"id": 0, "code": code, "name": str(sequence.get("checkpoint_name") or code), "tags": []})
        known_codes.add(code)
    return items


def _empty_checkpoint_fact() -> dict[str, Any]:
    return {"type_id": 0, "code": "", "type_name": "", "tag_id": 0, "tag_name": ""}


def _normalize_checkpoint_fact(
    raw: dict[str, Any],
    *,
    prefix: str,
    taxonomy: list[dict[str, Any]],
) -> dict[str, Any]:
    type_id = int(raw.get(f"{prefix}_type_id") or 0)
    code = str(raw.get(f"{prefix}_code") or "").strip().lower()
    matched = next(
        (
            item
            for item in taxonomy
            if (type_id and int(item.get("id") or 0) == type_id)
            or (code and str(item.get("code") or "").strip().lower() == code)
        ),
        None,
    )
    if matched is None:
        return _empty_checkpoint_fact()
    type_id = int(matched.get("id") or 0)
    code = str(matched.get("code") or "").strip().lower()
    tag_id = int(raw.get(f"{prefix}_tag_id") or 0)
    tag = next(
        (item for item in matched.get("tags") or [] if int(item.get("id") or 0) == tag_id),
        None,
    )
    return {
        "type_id": type_id,
        "code": code,
        "type_name": str(matched.get("name") or code),
        "tag_id": int((tag or {}).get("id") or 0),
        "tag_name": str((tag or {}).get("name") or ""),
    }


def _normalize_semantic_route(
    raw: Any,
    *,
    shared_context: dict[str, Any],
    sequences: list[dict[str, Any]],
    checkpoint_taxonomy: list[dict[str, Any]] | None = None,
    fact_topic_catalog: list[dict[str, Any]] | None = None,
    closing_catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = raw if isinstance(raw, dict) else {}
    valid_refs = {"current_message"}
    valid_customer_refs = {"current_message"}
    for item in shared_context.get("conversation") or []:
        if not isinstance(item, dict):
            continue
        message_ref = str(item.get("message_ref") or "").strip()
        if not message_ref:
            continue
        valid_refs.add(message_ref)
        if str(item.get("role") or "").strip().lower() in {"customer", "user", "external"}:
            valid_customer_refs.add(message_ref)
    by_id = {str(item.get("id") or "").strip(): item for item in sequences if str(item.get("id") or "").strip()}
    taxonomy = _taxonomy_types(
        {"types": checkpoint_taxonomy or []},
        sequences=sequences,
    )
    checkpoint_raw = payload.get("checkpoint") if isinstance(payload.get("checkpoint"), dict) else {}
    current_friction_raw = (
        payload.get("current_friction")
        if isinstance(payload.get("current_friction"), dict)
        else {}
    )
    friction_checkpoint = (
        {
            "primary_type_id": current_friction_raw.get("checkpoint_type_id"),
            "primary_code": current_friction_raw.get("checkpoint_code"),
            "primary_tag_id": current_friction_raw.get("checkpoint_tag_id"),
        }
        if current_friction_raw
        else checkpoint_raw
    )
    primary_fact = _normalize_checkpoint_fact(friction_checkpoint, prefix="primary", taxonomy=taxonomy)
    secondary_fact = _normalize_checkpoint_fact(checkpoint_raw, prefix="secondary", taxonomy=taxonomy)
    primary = str(primary_fact.get("code") or "")
    secondary = str(secondary_fact.get("code") or "")
    if secondary and secondary == primary:
        secondary_fact = _empty_checkpoint_fact()
        secondary = ""
    friction_status = str(current_friction_raw.get("status") or "").strip().lower()
    if friction_status not in {"explicit", "inferred", "none"}:
        friction_status = "explicit" if primary else "none"
    if friction_status == "none":
        primary_fact = _empty_checkpoint_fact()
        primary = ""
        secondary_fact = _empty_checkpoint_fact()
        secondary = ""
    classification_status = str(payload.get("classification_status") or "").strip().lower()
    if classification_status not in {"clear", "ambiguous", "none"}:
        classification_status = "none" if not primary else "clear"
    if not primary:
        classification_status = "none"

    current_intent_raw = (
        payload.get("current_intent") if isinstance(payload.get("current_intent"), dict) else {}
    )
    current_intent_summary = str(current_intent_raw.get("summary") or "")[:300]
    current_intent_refs = _valid_refs(current_intent_raw.get("evidence_refs"), valid_customer_refs)
    if current_intent_summary and "current_message" not in current_intent_refs:
        current_intent_refs.insert(0, "current_message")
    current_intent = {
        "summary": current_intent_summary,
        "evidence_refs": current_intent_refs,
    }
    current_friction_refs = _valid_refs(
        current_friction_raw.get("evidence_refs") or checkpoint_raw.get("evidence_refs"),
        valid_customer_refs,
    )
    if primary and friction_status == "explicit" and "current_message" not in current_friction_refs:
        current_friction_refs.insert(0, "current_message")
    historical_raw = (
        payload.get("historical_unresolved_friction")
        if isinstance(payload.get("historical_unresolved_friction"), dict)
        else {}
    )
    taxonomy_codes = {str(item.get("code") or "").strip().lower() for item in taxonomy}
    historical_code = str(historical_raw.get("checkpoint_code") or "").strip().lower()
    if historical_code not in taxonomy_codes or historical_code == primary:
        historical_code = ""
    historical_friction = {
        "checkpoint_code": historical_code,
        "summary": str(historical_raw.get("summary") or "")[:300] if historical_code else "",
        "evidence_refs": _valid_refs(historical_raw.get("evidence_refs"), valid_customer_refs)
        if historical_code
        else [],
    }
    knowledge_focus_raw = (
        payload.get("knowledge_focus")
        if isinstance(payload.get("knowledge_focus"), dict)
        else {}
    )
    knowledge_focus_fact = _normalize_checkpoint_fact(
        {
            "focus_type_id": knowledge_focus_raw.get("checkpoint_type_id"),
            "focus_code": knowledge_focus_raw.get("checkpoint_code"),
            "focus_tag_id": knowledge_focus_raw.get("checkpoint_tag_id"),
        },
        prefix="focus",
        taxonomy=taxonomy,
    )
    knowledge_focus_source = str(knowledge_focus_raw.get("source") or "none").strip().lower()
    if knowledge_focus_source not in {"current_intent", "current_friction", "none"}:
        knowledge_focus_source = "none"
    if knowledge_focus_source == "current_friction" and not primary:
        knowledge_focus_source = "none"
    knowledge_focus_action = str(knowledge_focus_raw.get("action_code") or "").strip().lower()
    if not _taxonomy_allows_action(
        taxonomy,
        fact=knowledge_focus_fact,
        action_code=knowledge_focus_action,
    ):
        knowledge_focus_action = ""
    if (
        knowledge_focus_source == "none"
        or int(knowledge_focus_fact.get("type_id") or 0) <= 0
        or not knowledge_focus_action
    ):
        knowledge_focus_fact = _empty_checkpoint_fact()
        knowledge_focus_source = "none"
        knowledge_focus_action = ""
    knowledge_focus_refs = _valid_refs(
        knowledge_focus_raw.get("evidence_refs"),
        valid_customer_refs,
    )
    if knowledge_focus_source == "current_intent" and "current_message" not in knowledge_focus_refs:
        knowledge_focus_refs.insert(0, "current_message")
    if knowledge_focus_source == "current_friction" and primary:
        knowledge_focus_refs = list(current_friction_refs)
    valid_topic_ids = {
        str(item.get("id") or "").strip()
        for item in fact_topic_catalog or []
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    # A model-selected knowledge focus and its supporting authoritative fact
    # topic are one configured evidence package. Joining them here does not
    # infer customer intent or force Reply to use either one; it prevents a
    # valid script lookup from arriving without the facts needed to assess it.
    relevant_fact_topic_ids: list[str] = []
    knowledge_focus_code = str(knowledge_focus_fact.get("code") or "").strip().lower()
    if knowledge_focus_source != "none" and knowledge_focus_code:
        for topic in fact_topic_catalog or []:
            if not isinstance(topic, dict):
                continue
            linked_codes = {
                str(item or "").strip().lower()
                for item in topic.get("knowledge_checkpoint_codes") or []
                if str(item or "").strip()
            }
            topic_id = str(topic.get("id") or "").strip()
            if knowledge_focus_code in linked_codes and topic_id in valid_topic_ids:
                relevant_fact_topic_ids.append(topic_id)
    for value in payload.get("relevant_fact_topic_ids") or []:
        topic_id = str(value or "").strip()
        if topic_id in valid_topic_ids and topic_id not in relevant_fact_topic_ids:
            relevant_fact_topic_ids.append(topic_id)
        if len(relevant_fact_topic_ids) >= 3:
            break
    sequence_raw = payload.get("sequence_match") if isinstance(payload.get("sequence_match"), dict) else {}
    sequence_ids = []
    for value in sequence_raw.get("sequence_ids") or []:
        sequence_id = str(value or "").strip()
        if sequence_id in by_id and sequence_id not in sequence_ids:
            sequence_ids.append(sequence_id)
        if len(sequence_ids) >= 2:
            break
    if not primary:
        sequence_ids = []
    alternative_sequence_ids = []
    for value in sequence_raw.get("alternative_sequence_ids") or []:
        sequence_id = str(value or "").strip()
        if (
            sequence_id in sequence_ids[1:]
            and sequence_id not in alternative_sequence_ids
        ):
            alternative_sequence_ids.append(sequence_id)
    excluded_sequence_ids = []
    for value in sequence_raw.get("excluded_sequence_ids") or []:
        sequence_id = str(value or "").strip()
        if sequence_id in by_id and sequence_id not in sequence_ids and sequence_id not in excluded_sequence_ids:
            excluded_sequence_ids.append(sequence_id)
        if len(excluded_sequence_ids) >= 5:
            break
    raw_exclusion_reasons = (
        sequence_raw.get("exclusion_reasons")
        if isinstance(sequence_raw.get("exclusion_reasons"), dict)
        else {}
    )
    exclusion_reasons = {
        sequence_id: str(raw_exclusion_reasons.get(sequence_id) or "")[:300]
        for sequence_id in excluded_sequence_ids
    }
    valid_steps = {
        str(step.get("id") or "").strip(): (sequence_id, step)
        for sequence_id in sequence_ids
        for step in (by_id.get(sequence_id) or {}).get("steps") or []
        if isinstance(step, dict) and str(step.get("id") or "").strip()
    }
    step_ids = []
    step_counts_by_sequence: dict[str, int] = {}
    for value in sequence_raw.get("relevant_step_ids") or []:
        step_id = str(value or "").strip()
        sequence_id = str((valid_steps.get(step_id) or ("", {}))[0])
        if (
            step_id in valid_steps
            and step_id not in step_ids
            and step_counts_by_sequence.get(sequence_id, 0) < 2
        ):
            step_ids.append(step_id)
            step_counts_by_sequence[sequence_id] = step_counts_by_sequence.get(sequence_id, 0) + 1
        if len(step_ids) >= 4:
            break
    script_queries = []
    seen_queries: set[tuple[int, int, str, str, str, str]] = set()
    for item in payload.get("script_queries") or []:
        if not isinstance(item, dict):
            continue
        sequence_id = str(item.get("sequence_id") or "").strip()
        step_id = str(item.get("step_id") or "").strip()
        step_link = valid_steps.get(step_id)
        checkpoint_type_id = int(item.get("checkpoint_type_id") or primary_fact.get("type_id") or 0)
        checkpoint_tag_id = int(item.get("checkpoint_tag_id") or primary_fact.get("tag_id") or 0)
        checkpoint = str(item.get("checkpoint_code") or primary).strip().lower()
        action = str(item.get("action_code") or "").strip().lower()
        if not step_link or step_link[0] != sequence_id:
            continue
        step = step_link[1]
        if action != str(step.get("action_code") or "").strip().lower():
            continue
        if checkpoint != primary or action not in ACTION_CODES:
            continue
        if checkpoint_type_id != int(primary_fact.get("type_id") or 0):
            continue
        if checkpoint_tag_id and checkpoint_tag_id != int(primary_fact.get("tag_id") or 0):
            continue
        key = (checkpoint_type_id, checkpoint_tag_id, checkpoint, action, sequence_id, step_id)
        if key in seen_queries:
            continue
        seen_queries.add(key)
        script_queries.append(
            {
                "checkpoint_type_id": checkpoint_type_id,
                "checkpoint_tag_id": checkpoint_tag_id,
                "checkpoint_code": checkpoint,
                "action_code": action,
                "sequence_id": sequence_id,
                "step_id": step_id,
            }
        )
    store_raw = payload.get("store_query") if isinstance(payload.get("store_query"), dict) else {}
    location_refs = _valid_refs(store_raw.get("location_evidence_refs"), valid_customer_refs)
    destination_hint = _sourced_destination_hint(
        store_raw.get("destination_hint"),
        refs=location_refs,
        shared_context=shared_context,
    )
    store_required = bool(store_raw.get("required"))
    store_purpose = str(store_raw.get("purpose") or "none")[:100]
    if not store_required:
        structured_location_hint = _structured_current_location_hint(shared_context)
        if structured_location_hint:
            store_required = True
            store_purpose = "store_search"
            location_refs = _valid_refs(["current_message"], valid_customer_refs)
            destination_hint = structured_location_hint
    closing_catalog_match = _normalize_closing_catalog_match(
        payload.get("closing_catalog_match"),
        catalog=_normalized_closing_catalog(closing_catalog),
        valid_customer_refs=valid_customer_refs,
    )
    return {
        "schema_version": "v3_semantic_route_v2",
        "status": "ok",
        "classification_status": classification_status,
        "current_intent": current_intent,
        "current_friction": {
            "checkpoint_type_id": int(primary_fact.get("type_id") or 0),
            "checkpoint_code": primary,
            "checkpoint_type_name": str(primary_fact.get("type_name") or ""),
            "checkpoint_tag_id": int(primary_fact.get("tag_id") or 0),
            "checkpoint_tag_name": str(primary_fact.get("tag_name") or ""),
            "summary": str(current_friction_raw.get("summary") or checkpoint_raw.get("reason") or "")[:300]
            if primary
            else "",
            "evidence_refs": current_friction_refs if primary else [],
            "status": friction_status if primary else "none",
        },
        "historical_unresolved_friction": historical_friction,
        "knowledge_focus": {
            "checkpoint_type_id": int(knowledge_focus_fact.get("type_id") or 0),
            "checkpoint_code": str(knowledge_focus_fact.get("code") or ""),
            "checkpoint_type_name": str(knowledge_focus_fact.get("type_name") or ""),
            "checkpoint_tag_id": int(knowledge_focus_fact.get("tag_id") or 0),
            "checkpoint_tag_name": str(knowledge_focus_fact.get("tag_name") or ""),
            "action_code": knowledge_focus_action,
            "source": knowledge_focus_source,
            "evidence_refs": knowledge_focus_refs if knowledge_focus_source != "none" else [],
            "reason": str(knowledge_focus_raw.get("reason") or "")[:300]
            if knowledge_focus_source != "none"
            else "",
        },
        "relevant_fact_topic_ids": relevant_fact_topic_ids,
        "checkpoint": {
            "primary_code": primary,
            "primary_type_id": int(primary_fact.get("type_id") or 0),
            "primary_type_name": str(primary_fact.get("type_name") or ""),
            "primary_tag_id": int(primary_fact.get("tag_id") or 0),
            "primary_tag_name": str(primary_fact.get("tag_name") or ""),
            "secondary_code": secondary,
            "secondary_type_id": int(secondary_fact.get("type_id") or 0),
            "secondary_type_name": str(secondary_fact.get("type_name") or ""),
            "secondary_tag_id": int(secondary_fact.get("tag_id") or 0),
            "secondary_tag_name": str(secondary_fact.get("tag_name") or ""),
            "evidence_refs": current_friction_refs if primary else [],
            "reason": str(current_friction_raw.get("summary") or checkpoint_raw.get("reason") or "")[:500]
            if primary
            else "",
        },
        "sequence_match": {
            "sequence_ids": sequence_ids,
            "alternative_sequence_ids": alternative_sequence_ids,
            "relevant_step_ids": step_ids,
            "excluded_sequence_ids": excluded_sequence_ids,
            "exclusion_reasons": exclusion_reasons,
            "reason": str(sequence_raw.get("reason") or "")[:500],
        },
        "store_query": {
            "required": store_required,
            "purpose": store_purpose,
            "location_evidence_refs": location_refs,
            "destination_hint": destination_hint,
        },
        "script_queries": script_queries,
        "closing_catalog_match": closing_catalog_match,
    }


def _normalized_closing_catalog(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    status = str(raw.get("status") or "unavailable").strip().lower()
    if status != "ok":
        return {
            "schema_version": "closing_catalog_v1",
            "status": status if status in {"disabled", "error", "unavailable"} else "unavailable",
            "freshness_status": "unavailable",
            "source": str(raw.get("source") or ""),
            "reason": str(raw.get("reason") or "closing_catalog_unavailable")[:500],
            "checksum": "",
            "eligibility_status": "unavailable",
            "rules": {},
            "sequences": [],
            "quality_flags": list(raw.get("quality_flags") or [])[:20],
        }
    rules = raw.get("rules") if isinstance(raw.get("rules"), dict) else {}
    triggers = [item for item in rules.get("triggers") or [] if isinstance(item, dict)]
    sequences = [item for item in raw.get("sequences") or [] if isinstance(item, dict)]
    return {
        "schema_version": "closing_catalog_v1",
        "status": "ok",
        "freshness_status": str(raw.get("freshness_status") or "fresh")[:32],
        "source": str(raw.get("source") or "follow_knowledge_api"),
        "fallback_used": bool(raw.get("fallback_used")),
        "fallback_reason": str(raw.get("fallback_reason") or "")[:500],
        "checksum": str(raw.get("checksum") or "")[:128],
        "eligibility_status": "configured" if triggers else "catalog_empty",
        "rules": {
            "triggers": triggers,
            "ai_confirm": copy.deepcopy(rules.get("ai_confirm") or {}),
            "constraints": copy.deepcopy(rules.get("constraints") or {}),
        },
        "sequences": sequences,
        "quality_flags": [str(item)[:200] for item in raw.get("quality_flags") or []][:20],
    }


def _normalize_closing_catalog_match(
    value: Any,
    *,
    catalog: dict[str, Any],
    valid_customer_refs: set[str],
) -> dict[str, Any]:
    if str(catalog.get("status") or "") != "ok":
        return {
            "status": "catalog_unavailable",
            "selected_rule_ids": [],
            "sequence_candidate_ids": [],
            "evidence_refs": [],
            "reason": str(catalog.get("reason") or "closing catalog unavailable")[:300],
        }
    triggers = {
        str(item.get("rule_key") or ""): item
        for item in (catalog.get("rules") or {}).get("triggers") or []
        if isinstance(item, dict) and str(item.get("rule_key") or "")
    }
    if not triggers:
        return {
            "status": "catalog_empty",
            "selected_rule_ids": [],
            "sequence_candidate_ids": [],
            "evidence_refs": [],
            "reason": "tenant has no enabled closing rule",
        }
    raw = value if isinstance(value, dict) else {}
    selected_rule_ids = [
        item
        for item in dict.fromkeys(str(entry or "").strip() for entry in raw.get("selected_rule_ids") or [])
        if item in triggers
    ][:3]
    unsupported = [
        rule_id
        for rule_id in selected_rule_ids
        if not bool((triggers.get(rule_id) or {}).get("grouping_supported", True))
    ]
    sequence_map = {
        str(item.get("sequence_key") or ""): item
        for item in catalog.get("sequences") or []
        if isinstance(item, dict) and str(item.get("sequence_key") or "")
    }
    sequence_candidate_ids = [
        item
        for item in dict.fromkeys(
            str(entry or "").strip() for entry in raw.get("sequence_candidate_ids") or []
        )
        if item in sequence_map
    ][:3]
    if not selected_rule_ids or unsupported:
        sequence_candidate_ids = []
    evidence_refs = _valid_refs(raw.get("evidence_refs"), valid_customer_refs)
    missing_evidence = bool(selected_rule_ids) and not evidence_refs
    if missing_evidence:
        selected_rule_ids = []
        sequence_candidate_ids = []
    status = "matched" if selected_rule_ids and sequence_candidate_ids else (
        "blocked" if unsupported else "rule_only" if selected_rule_ids else "none"
    )
    return {
        "status": status,
        "selected_rule_ids": selected_rule_ids,
        "sequence_candidate_ids": sequence_candidate_ids,
        "evidence_refs": evidence_refs[:6],
        "reason": (
            "combined trigger grouping is not defined by upstream contract"
            if unsupported
            else "closing rule match requires customer evidence"
            if missing_evidence
            else str(raw.get("reason") or "")[:300]
        ),
    }


def _closing_catalog_evidence(value: Any, match_value: Any) -> dict[str, Any]:
    catalog = _normalized_closing_catalog(value)
    match = match_value if isinstance(match_value, dict) else {}
    rule_ids = {str(item) for item in match.get("selected_rule_ids") or []}
    sequence_ids = {str(item) for item in match.get("sequence_candidate_ids") or []}
    rules = catalog.get("rules") if isinstance(catalog.get("rules"), dict) else {}
    return {
        "schema_version": "closing_catalog_evidence_v1",
        "status": str(catalog.get("status") or "unavailable"),
        "freshness_status": str(catalog.get("freshness_status") or "unavailable"),
        "source": str(catalog.get("source") or ""),
        "fallback_used": bool(catalog.get("fallback_used")),
        "fallback_reason": str(catalog.get("fallback_reason") or "")[:500],
        "eligibility_status": str(catalog.get("eligibility_status") or "unavailable"),
        "match_status": str(match.get("status") or "none"),
        "checksum": str(catalog.get("checksum") or ""),
        "selected_rules": [
            copy.deepcopy(item)
            for item in rules.get("triggers") or []
            if isinstance(item, dict) and str(item.get("rule_key") or "") in rule_ids
        ],
        "candidate_sequences": [
            copy.deepcopy(item)
            for item in catalog.get("sequences") or []
            if isinstance(item, dict) and str(item.get("sequence_key") or "") in sequence_ids
        ],
        "ai_confirm": copy.deepcopy(rules.get("ai_confirm") or {}),
        "constraints": copy.deepcopy(rules.get("constraints") or {}),
        "quality_flags": list(catalog.get("quality_flags") or []),
        "reason": str(match.get("reason") or catalog.get("reason") or "")[:300],
    }


def _empty_sequence_match() -> dict[str, Any]:
    return {
        "sequence_ids": [],
        "alternative_sequence_ids": [],
        "relevant_step_ids": [],
        "excluded_sequence_ids": [],
        "exclusion_reasons": {},
        "reason": "",
    }


def _taxonomy_allows_action(
    taxonomy: list[dict[str, Any]],
    *,
    fact: dict[str, Any],
    action_code: str,
) -> bool:
    """Validate a model-selected retrieval action against tenant metadata."""

    action = str(action_code or "").strip().lower()
    type_id = int(fact.get("type_id") or 0)
    if not action or action not in ACTION_CODES or type_id <= 0:
        return False
    checkpoint_type = next(
        (item for item in taxonomy if int(item.get("id") or 0) == type_id),
        None,
    )
    if not isinstance(checkpoint_type, dict):
        return False
    tag_id = int(fact.get("tag_id") or 0)
    if tag_id:
        tag = next(
            (
                item
                for item in checkpoint_type.get("tags") or []
                if isinstance(item, dict) and int(item.get("id") or 0) == tag_id
            ),
            None,
        )
        counts = tag.get("action_counts") if isinstance(tag, dict) else {}
    else:
        counts = checkpoint_type.get("action_counts")
    return isinstance(counts, dict) and int(counts.get(action) or 0) > 0


def _taxonomy_action_fallback_queries(
    route: dict[str, Any],
    *,
    taxonomy: list[dict[str, Any]],
    existing_signatures: set[tuple[int, int, str, str]],
    enabled: bool,
    max_actions: int = 4,
) -> list[dict[str, Any]]:
    """Add retrieval-only queries for published actions covered by taxonomy.

    This does not decide sales semantics.  It only prevents a model-selected
    sequence step with an uncovered action_code from starving the downstream
    script selector when the tenant taxonomy says the same checkpoint has
    published scripts under nearby actions.
    """

    if not enabled:
        return []
    current_friction = route.get("current_friction") if isinstance(route.get("current_friction"), dict) else {}
    if str(current_friction.get("status") or "none") == "none":
        return []
    checkpoint = route.get("checkpoint") if isinstance(route.get("checkpoint"), dict) else {}
    checkpoint_type_id = int(checkpoint.get("primary_type_id") or 0)
    checkpoint_tag_id = int(checkpoint.get("primary_tag_id") or 0)
    checkpoint_code = str(checkpoint.get("primary_code") or "").strip().lower()
    if checkpoint_type_id <= 0 or not checkpoint_code or checkpoint_code == "all":
        return []
    checkpoint_type = next(
        (
            item
            for item in taxonomy
            if isinstance(item, dict) and int(item.get("id") or 0) == checkpoint_type_id
        ),
        None,
    )
    if not isinstance(checkpoint_type, dict):
        return []

    ordered_actions: list[tuple[str, int, int]] = []
    if checkpoint_tag_id:
        tag = next(
            (
                item
                for item in checkpoint_type.get("tags") or []
                if isinstance(item, dict) and int(item.get("id") or 0) == checkpoint_tag_id
            ),
            None,
        )
        if isinstance(tag, dict):
            ordered_actions.extend(_ordered_action_counts(tag.get("action_counts"), scope=1))
    ordered_actions.extend(_ordered_action_counts(checkpoint_type.get("action_counts"), scope=0))

    output: list[dict[str, Any]] = []
    seen_actions: set[str] = set()
    for action_code, _count, scope in ordered_actions:
        if action_code in seen_actions or action_code not in ACTION_CODES:
            continue
        seen_actions.add(action_code)
        query_tag_id = checkpoint_tag_id if scope == 1 else 0
        signature = (checkpoint_type_id, query_tag_id, checkpoint_code, action_code)
        if signature in existing_signatures:
            continue
        output.append(
            {
                "checkpoint_type_id": checkpoint_type_id,
                "checkpoint_tag_id": query_tag_id,
                "checkpoint_code": checkpoint_code,
                "action_code": action_code,
                "sequence_id": "",
                "step_id": "",
                "query_source": "taxonomy_action_coverage_fallback",
            }
        )
        existing_signatures.add(signature)
        if len(output) >= max_actions:
            break
    return output


def _closing_script_queries(
    semantic_route: dict[str, Any],
    *,
    existing_queries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Translate matched closing node types into the existing script retrieval batch.

    The mapping is contractual rather than semantic: the node script type is the
    foreign key into the script collection from the same catalog source. Reply
    still decides whether a sequence, node, or returned expression is appropriate.
    """

    evidence = (
        semantic_route.get("closing_catalog_evidence")
        if isinstance(semantic_route.get("closing_catalog_evidence"), dict)
        else {}
    )
    if (
        str(evidence.get("status") or "") != "ok"
        or str(evidence.get("match_status") or "") != "matched"
    ):
        return []
    existing_signatures = {
        (
            int(item.get("checkpoint_type_id") or 0),
            int(item.get("checkpoint_tag_id") or 0),
            str(item.get("checkpoint_code") or "").strip().lower(),
            str(item.get("action_code") or "").strip().lower(),
        )
        for item in existing_queries
        if isinstance(item, dict)
    }
    by_script_type: dict[int, dict[str, Any]] = {}
    for sequence in evidence.get("candidate_sequences") or []:
        if not isinstance(sequence, dict):
            continue
        sequence_id = str(sequence.get("sequence_key") or "").strip()
        if not sequence_id:
            continue
        for node in sequence.get("nodes") or []:
            if not isinstance(node, dict):
                continue
            script_type = node.get("script_type") if isinstance(node.get("script_type"), dict) else {}
            action_type = node.get("action_type") if isinstance(node.get("action_type"), dict) else {}
            script_type_id = int(script_type.get("id") or 0)
            node_id = str(node.get("node_key") or "").strip()
            if script_type_id <= 0 or not node_id:
                continue
            query = by_script_type.setdefault(
                script_type_id,
                {
                    "checkpoint_type_id": script_type_id,
                    "checkpoint_tag_id": 0,
                    "checkpoint_code": "",
                    "action_code": "",
                    "sequence_id": "",
                    "step_id": "",
                    "query_source": "closing_catalog_node",
                    "catalog_source": str(evidence.get("source") or ""),
                    "sequence_links": [],
                },
            )
            link = {
                "sequence_id": sequence_id,
                "step_id": node_id,
                "action_code": "",
                "query_source": "closing_catalog_node",
                "closing_action_type_id": int(action_type.get("id") or 0),
                "closing_action_type_name": str(action_type.get("name") or "")[:120],
                "closing_script_type_id": script_type_id,
                "closing_script_type_name": str(script_type.get("name") or "")[:120],
            }
            if link not in query["sequence_links"]:
                query["sequence_links"].append(link)
    output: list[dict[str, Any]] = []
    for script_type_id, query in by_script_type.items():
        signature = (script_type_id, 0, "", "")
        if signature in existing_signatures:
            continue
        output.append(query)
        if len(output) >= 8:
            break
    return output


def _ordered_action_counts(value: Any, *, scope: int) -> list[tuple[str, int, int]]:
    if not isinstance(value, dict):
        return []
    items = [
        (str(action or "").strip().lower(), int(count or 0), scope)
        for action, count in value.items()
        if str(action or "").strip().lower() and int(count or 0) > 0
    ]
    return sorted(items, key=lambda item: (-item[1], item[0]))


def _semantic_route_contract_issues(route: dict[str, Any]) -> list[str]:
    """Return schema/provenance defects without interpreting business meaning."""

    issues: list[str] = []
    for field in ("current_intent", "current_friction", "historical_unresolved_friction"):
        value = route.get(field) if isinstance(route.get(field), dict) else {}
        if str(value.get("summary") or "").strip() and not value.get("evidence_refs"):
            issues.append(f"{field}_missing_evidence_refs")
    return issues


def _apply_semantic_reference_repair(raw_route: Any, repaired_refs: Any) -> dict[str, Any]:
    """Merge model-selected references without changing any semantic field."""

    output = copy.deepcopy(raw_route) if isinstance(raw_route, dict) else {}
    refs = repaired_refs if isinstance(repaired_refs, dict) else {}
    mappings = (
        ("current_intent", "current_intent_refs"),
        ("current_friction", "current_friction_refs"),
        ("historical_unresolved_friction", "historical_unresolved_friction_refs"),
    )
    for field, refs_field in mappings:
        value = output.get(field) if isinstance(output.get(field), dict) else {}
        full_route_value = refs.get(field) if isinstance(refs.get(field), dict) else {}
        selected_refs = refs.get(refs_field) or full_route_value.get("evidence_refs") or []
        if str(value.get("summary") or "").strip() and selected_refs:
            value["evidence_refs"] = list(selected_refs)
            output[field] = value
    friction = output.get("current_friction") if isinstance(output.get("current_friction"), dict) else {}
    checkpoint = output.get("checkpoint") if isinstance(output.get("checkpoint"), dict) else {}
    if friction.get("evidence_refs") and not checkpoint.get("evidence_refs"):
        checkpoint["evidence_refs"] = list(friction["evidence_refs"])
        output["checkpoint"] = checkpoint
    return output


def _semantic_retrieval_text(
    shared_context: dict[str, Any],
    route: dict[str, Any],
) -> str:
    """Build a retrieval-only query from model output and the current message.

    The value is used only to rank already-published metadata. It does not
    infer a new intent or authorize a sales action.
    """

    current = (
        shared_context.get("current_message")
        if isinstance(shared_context.get("current_message"), dict)
        else {}
    )
    fragments: list[Any] = [current.get("content") or current.get("raw_content")]
    for field in ("current_intent", "current_friction", "knowledge_focus", "checkpoint"):
        value = route.get(field) if isinstance(route.get(field), dict) else {}
        fragments.extend(
            value.get(key)
            for key in (
                "summary",
                "reason",
                "checkpoint_code",
                "checkpoint_type_name",
                "checkpoint_tag_name",
                "primary_code",
                "primary_name",
                "primary_tag_name",
                "action_code",
            )
        )
    return " ".join(str(value).strip() for value in fragments if str(value or "").strip())


def _retrieval_terms(value: Any) -> set[str]:
    text = str(value or "").strip().lower()
    if not text:
        return set()
    terms = {
        token
        for token in re.findall(r"[a-z0-9_\-]{2,}", text)
        if token
    }
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(run) <= 8:
            terms.add(run)
        for size in (2, 3):
            terms.update(run[index : index + size] for index in range(max(0, len(run) - size + 1)))
    return terms


def _retrieval_overlap(query_terms: set[str], value: Any) -> int:
    if not query_terms:
        return 0
    return len(query_terms.intersection(_retrieval_terms(value)))


def _current_step_candidates(sequence: dict[str, Any], *, query_terms: set[str]) -> list[dict[str, Any]]:
    candidates: list[tuple[int, int, str, dict[str, Any]]] = []
    for step in sequence.get("steps") or []:
        if not isinstance(step, dict):
            continue
        step_id = str(step.get("id") or "").strip()
        action_code = str(step.get("action_code") or "").strip().lower()
        if not step_id or action_code not in ACTION_CODES:
            continue
        trigger_base = str(step.get("trigger_base") or "").strip().lower()
        relative_value = max(0, int(step.get("relative_value") or 0))
        if trigger_base not in {
            "",
            "customer_reply",
            "last_reply",
            "current_message",
            "immediate",
            "now",
        } or relative_value > 0:
            continue
        searchable = " ".join(
            str(step.get(key) or "")
            for key in ("action_code", "action_name", "trigger_base_name", "remark")
        )
        score = _retrieval_overlap(query_terms, searchable)
        candidates.append(
            (
                -score,
                int(step.get("sort_order") or 0),
                step_id,
                step,
            )
        )
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    return [item[3] for item in candidates]


def _apply_deterministic_sequence_top_k(
    route: dict[str, Any],
    *,
    shared_context: dict[str, Any],
    sequences: list[dict[str, Any]],
) -> dict[str, Any]:
    """Rank real external sequences and current nodes without a selector model."""

    output = copy.deepcopy(route)
    output["script_queries"] = []
    friction = output.get("current_friction") if isinstance(output.get("current_friction"), dict) else {}
    if str(friction.get("status") or "none") == "none":
        output["sequence_match"] = _empty_sequence_match()
        return _expand_sequence_action_queries(output, sequences=[])

    checkpoint = output.get("checkpoint") if isinstance(output.get("checkpoint"), dict) else {}
    checkpoint_code = str(checkpoint.get("primary_code") or "").strip().lower()
    query_text = _semantic_retrieval_text(shared_context, output)
    query_terms = _retrieval_terms(query_text)
    ranked: list[tuple[int, str, dict[str, Any], list[dict[str, Any]]]] = []
    for sequence in sequences:
        if not isinstance(sequence, dict):
            continue
        sequence_id = str(sequence.get("id") or "").strip()
        if not sequence_id:
            continue
        sequence_code = str(sequence.get("checkpoint_code") or "").strip().lower()
        searchable = " ".join(
            str(sequence.get(key) or "")
            for key in ("checkpoint_code", "checkpoint_name", "sequence_name", "description")
        )
        searchable += " " + " ".join(
            " ".join(
                str(step.get(key) or "")
                for key in ("action_code", "action_name", "remark")
            )
            for step in sequence.get("steps") or []
            if isinstance(step, dict)
        )
        score = 5 * _retrieval_overlap(query_terms, searchable)
        if checkpoint_code and sequence_code == checkpoint_code:
            score += 100
        elif sequence_code == "all":
            score += 10
        current_steps = _current_step_candidates(sequence, query_terms=query_terms)
        if score <= 0 or not current_steps:
            continue
        ranked.append((-score, sequence_id, sequence, current_steps))
    ranked.sort(key=lambda item: (item[0], item[1]))
    ranked = ranked[:MAX_SEQUENCE_CANDIDATES]

    selected_ids = [item[1] for item in ranked]
    selected_step_ids: list[str] = []
    # Give each selected sequence one usable node before filling the remaining slots.
    for _, _, _, steps in ranked:
        if steps and len(selected_step_ids) < MAX_SEQUENCE_STEPS_TOTAL:
            selected_step_ids.append(str(steps[0].get("id") or ""))
    depth = 1
    while len(selected_step_ids) < MAX_SEQUENCE_STEPS_TOTAL:
        added = False
        for _, _, _, steps in ranked:
            if depth < len(steps):
                selected_step_ids.append(str(steps[depth].get("id") or ""))
                added = True
                if len(selected_step_ids) >= MAX_SEQUENCE_STEPS_TOTAL:
                    break
        if not added:
            break
        depth += 1

    output["sequence_match"] = {
        "sequence_ids": selected_ids,
        "alternative_sequence_ids": selected_ids[1:],
        "relevant_step_ids": [value for value in selected_step_ids if value],
        "excluded_sequence_ids": [],
        "exclusion_reasons": {},
        "reason": "deterministic_top_k",
    }
    return _expand_sequence_action_queries(output, sequences=[item[2] for item in ranked])


def _rank_script_groups(
    candidates: list[dict[str, Any]],
    *,
    query_text: str,
    max_groups: int,
) -> list[dict[str, Any]]:
    """Return at most ``max_groups`` published paragraph groups in stable order."""

    query_terms = _retrieval_terms(query_text)
    ranked: list[tuple[int, str, int, bool]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for raw in candidates:
        if not isinstance(raw, dict):
            continue
        script_id = str(raw.get("script_code") or "").strip()
        if not script_id:
            continue
        by_id[script_id] = raw
        checkpoint_type = raw.get("checkpoint_type") if isinstance(raw.get("checkpoint_type"), dict) else {}
        checkpoint_tag = raw.get("checkpoint_tag") if isinstance(raw.get("checkpoint_tag"), dict) else {}
        base = " ".join(
            str(value or "")
            for value in (
                raw.get("script_name"),
                raw.get("checkpoint_code"),
                checkpoint_type.get("name"),
                checkpoint_tag.get("name"),
                raw.get("action_code"),
                raw.get("action_name"),
            )
        )
        paragraphs = [item for item in raw.get("paragraphs") or [] if isinstance(item, dict)]
        if not paragraphs:
            searchable = base + " " + str(raw.get("body_text") or "")
            ranked.append((-_retrieval_overlap(query_terms, searchable), script_id, 0, False))
            continue
        for paragraph in paragraphs:
            paragraph_no = max(1, int(paragraph.get("paragraph_no") or 1))
            body = " ".join(
                str(message.get("content") or "")
                for message in paragraph.get("messages") or []
                if isinstance(message, dict)
            )
            ranked.append(
                (-_retrieval_overlap(query_terms, base + " " + body), script_id, paragraph_no, True)
            )
    ranked.sort(key=lambda item: (item[0], item[1], item[2]))
    selected = ranked[: max(1, int(max_groups or 1))]
    selected_groups = [(script_id, paragraph_no) for _, script_id, paragraph_no, has_group in selected if has_group]
    selected_scripts = [script_id for _, script_id, _, has_group in selected if not has_group]
    return _filter_script_groups(
        list(by_id.values()),
        selected_groups=selected_groups,
        selected_script_ids=selected_scripts,
        max_groups=max_groups,
    )


def _sequences_for_checkpoint(
    sequences: list[dict[str, Any]],
    route: dict[str, Any],
) -> list[dict[str, Any]]:
    """Order sequence candidates without treating cross-API codes as canonical.

    Script taxonomy and follow-sequence APIs are tenant-owned and can expose
    different codes for the same business concept (for example a generated
    ``cpN`` code versus a legacy semantic code).  Code therefore puts exact and
    generic metadata matches first, but leaves the semantic choice to DeepSeek
    instead of hiding the remaining real sequences before the model can see
    them.
    """

    checkpoint = str((route.get("checkpoint") or {}).get("primary_code") or "").strip().lower()
    if not checkpoint:
        return []
    exact = [
        item
        for item in sequences
        if str(item.get("checkpoint_code") or "").strip().lower() == checkpoint
    ]
    generic = [
        item
        for item in sequences
        if str(item.get("checkpoint_code") or "").strip().lower() == "all"
    ]
    remaining = [item for item in sequences if item not in exact and item not in generic]
    return [*exact, *generic, *remaining]


def _deferred_store_pre_route(route: dict[str, Any]) -> dict[str, Any]:
    """Keep only store routing evidence until authoritative store facts exist."""

    output = copy.deepcopy(route)
    output["phase"] = "pre_store_pending"
    output["provisional_checkpoint"] = copy.deepcopy(output.get("checkpoint") or {})
    output["provisional_current_friction"] = copy.deepcopy(output.get("current_friction") or {})
    output["provisional_knowledge_focus"] = copy.deepcopy(output.get("knowledge_focus") or {})
    output["checkpoint"] = {
        "primary_code": "",
        "secondary_code": "",
        "evidence_refs": [],
        "reason": "deferred_until_store_resolution",
    }
    output["current_friction"] = {
        "checkpoint_type_id": 0,
        "checkpoint_code": "",
        "checkpoint_type_name": "",
        "checkpoint_tag_id": 0,
        "checkpoint_tag_name": "",
        "summary": "",
        "evidence_refs": [],
        "status": "none",
    }
    output["relevant_fact_topic_ids"] = []
    output["knowledge_focus"] = {
        "checkpoint_type_id": 0,
        "checkpoint_code": "",
        "checkpoint_type_name": "",
        "checkpoint_tag_id": 0,
        "checkpoint_tag_name": "",
        "action_code": "",
        "source": "none",
        "evidence_refs": [],
        "reason": "deferred_until_store_resolution",
    }
    output["sequence_match"] = {
        "sequence_ids": [],
        "alternative_sequence_ids": [],
        "relevant_step_ids": [],
        "excluded_sequence_ids": [],
        "exclusion_reasons": {},
        "reason": "deferred_until_store_resolution",
    }
    output["script_queries"] = []
    return output


def _deferred_store_knowledge(sequence_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "v3_knowledge_evidence_v1",
        "status": "deferred_until_store_resolution",
        "source": "follow_knowledge_api",
        "sequence_index_status": sequence_result.get("status"),
        "sequence_index_total": int(sequence_result.get("total") or 0),
        "sequence_candidates": [],
        "script_query_results": [],
        "script_option_count": 0,
        "candidate_count": 0,
        "candidates": [],
        "selector": {"status": "deferred", "reason": "store_facts_required_first"},
    }


def _normalize_store_result_interpretation(
    raw: Any,
    *,
    shared_context: dict[str, Any],
) -> dict[str, Any]:
    payload = raw if isinstance(raw, dict) else {}
    value = (
        payload.get("store_result_interpretation")
        if isinstance(payload.get("store_result_interpretation"), dict)
        else {}
    )
    valid_refs = _valid_message_refs(shared_context)
    return {
        "resolved_current_request": bool(value.get("resolved_current_request")),
        "remaining_customer_concern_refs": _valid_refs(
            value.get("remaining_customer_concern_refs"),
            valid_refs,
        ),
        "reason": str(value.get("reason") or "")[:500],
    }


def _valid_message_refs(shared_context: dict[str, Any]) -> set[str]:
    refs = {"current_message"}
    refs.update(
        str(item.get("message_ref") or "").strip()
        for item in shared_context.get("conversation") or []
        if isinstance(item, dict) and str(item.get("message_ref") or "").strip()
    )
    return refs


def _selected_sequences(sequences: list[dict[str, Any]], route: dict[str, Any]) -> list[dict[str, Any]]:
    selected_ids = [
        str(item or "").strip()
        for item in (route.get("sequence_match") or {}).get("sequence_ids") or []
        if str(item or "").strip()
    ][:MAX_SEQUENCE_CANDIDATES]
    relevant_steps = set((route.get("sequence_match") or {}).get("relevant_step_ids") or [])
    selection_reason = str((route.get("sequence_match") or {}).get("reason") or "")[:500]
    by_id = {str(item.get("id") or "").strip(): item for item in sequences if isinstance(item, dict)}
    output = []
    for sequence_id in selected_ids:
        item = by_id.get(sequence_id)
        if not isinstance(item, dict):
            continue
        selected_steps = [
            step
            for step in item.get("steps") or []
            if isinstance(step, dict) and str(step.get("id") or "") in relevant_steps
        ][:MAX_STEPS_PER_SEQUENCE]
        output.append(
            {
                "sequence_id": sequence_id,
                "sequence_name": _single_line(item.get("sequence_name"), 200),
                "checkpoint_code": str(item.get("checkpoint_code") or ""),
                "checkpoint_name": _single_line(item.get("checkpoint_name"), 200),
                # Sequence descriptions and step remarks explain why an action
                # exists. They are strategy references, not authoritative facts
                # or finished customer copy, and Reply remains free to ignore them.
                "description": _single_line(item.get("description"), 300),
                "selection_reason": selection_reason,
                "steps": [
                    {
                        "step_id": str(step.get("id") or ""),
                        "sort_order": int(step.get("sort_order") or 0),
                        "action_code": str(step.get("action_code") or ""),
                        "action_name": str(step.get("action_name") or ""),
                        "trigger_base": str(step.get("trigger_base") or ""),
                        "relative_value": max(0, int(step.get("relative_value") or 0)),
                        "relative_unit": str(step.get("relative_unit") or ""),
                        "fixed_time": str(step.get("fixed_time") or ""),
                        "remark": _single_line(step.get("remark"), 300),
                        "relevant": str(step.get("id") or "") in relevant_steps,
                    }
                    for step in selected_steps
                ],
                "authority": "business_strategy_reference_not_mandatory_state",
            }
        )
    return output


def _expand_sequence_action_queries(
    route: dict[str, Any],
    *,
    sequences: list[dict[str, Any]],
) -> dict[str, Any]:
    """Query scripts only for the model-selected real sequence steps."""
    output = copy.deepcopy(route)
    sequence_match = output.get("sequence_match") or {}
    selected_ids = set(sequence_match.get("sequence_ids") or [])
    relevant_step_ids = set(sequence_match.get("relevant_step_ids") or [])
    checkpoint = str((output.get("checkpoint") or {}).get("primary_code") or "").strip().lower()
    checkpoint_type_id = int((output.get("checkpoint") or {}).get("primary_type_id") or 0)
    checkpoint_tag_id = int((output.get("checkpoint") or {}).get("primary_tag_id") or 0)
    queries = output.setdefault("script_queries", [])
    seen_actions = {
        (str(item.get("sequence_id") or ""), str(item.get("action_code") or ""))
        for item in queries
        if isinstance(item, dict)
    }
    query_by_signature = {
        (
            int(item.get("checkpoint_type_id") or 0),
            int(item.get("checkpoint_tag_id") or 0),
            str(item.get("checkpoint_code") or "").strip().lower(),
            str(item.get("action_code") or "").strip().lower(),
        ): item
        for item in queries
        if isinstance(item, dict)
    }
    if selected_ids and relevant_step_ids and checkpoint and checkpoint != "all":
        for sequence in sequences:
            sequence_id = str(sequence.get("id") or "").strip()
            if sequence_id not in selected_ids:
                continue
            for step in sequence.get("steps") or []:
                if not isinstance(step, dict):
                    continue
                action = str(step.get("action_code") or "").strip().lower()
                step_id = str(step.get("id") or "").strip()
                if step_id not in relevant_step_ids:
                    continue
                key = (sequence_id, action)
                query_signature = (
                    checkpoint_type_id,
                    checkpoint_tag_id,
                    checkpoint,
                    action,
                )
                if (
                    not step_id
                    or action not in ACTION_CODES
                    or key in seen_actions
                ):
                    continue
                link = {
                    "sequence_id": sequence_id,
                    "step_id": step_id,
                    "action_code": action,
                    "query_source": "deterministic_top_k_step",
                }
                existing_query = query_by_signature.get(query_signature)
                if isinstance(existing_query, dict):
                    links = existing_query.setdefault("sequence_links", [])
                    if link not in links:
                        links.append(link)
                    seen_actions.add(key)
                    continue
                queries.append(
                    {
                        "checkpoint_type_id": checkpoint_type_id,
                        "checkpoint_tag_id": checkpoint_tag_id,
                        "checkpoint_code": checkpoint,
                        "action_code": action,
                        "sequence_id": sequence_id,
                        "step_id": step_id,
                        "query_source": "deterministic_top_k_step",
                        "sequence_links": [link],
                    }
                )
                seen_actions.add(key)
                query_by_signature[query_signature] = queries[-1]

    focus = output.get("knowledge_focus") if isinstance(output.get("knowledge_focus"), dict) else {}
    focus_type_id = int(focus.get("checkpoint_type_id") or 0)
    focus_tag_id = int(focus.get("checkpoint_tag_id") or 0)
    focus_code = str(focus.get("checkpoint_code") or "").strip().lower()
    focus_action = str(focus.get("action_code") or "").strip().lower()
    focus_key = ("", focus_action)
    focus_signature = (focus_type_id, focus_tag_id, focus_code, focus_action)
    if (
        focus_type_id > 0
        and focus_code
        and focus_action in ACTION_CODES
        and str(focus.get("source") or "none") != "none"
        and focus_key not in seen_actions
        and focus_signature not in query_by_signature
    ):
        queries.append(
            {
                "checkpoint_type_id": focus_type_id,
                "checkpoint_tag_id": focus_tag_id,
                "checkpoint_code": focus_code,
                "action_code": focus_action,
                "sequence_id": "",
                "step_id": "",
                "query_source": "model_selected_knowledge_focus",
            }
        )
    return output


def _paragraph_group_count(items: list[dict[str, Any]]) -> int:
    count = 0
    for item in items:
        paragraphs = [value for value in item.get("paragraphs") or [] if isinstance(value, dict)]
        count += len(paragraphs) if paragraphs else 1
    return count


def _filter_script_groups(
    candidates: list[dict[str, Any]],
    *,
    selected_groups: list[tuple[str, int]],
    selected_script_ids: list[str],
    max_groups: int,
) -> list[dict[str, Any]]:
    """Keep selected real paragraph groups and preserve source message order."""

    group_set = set(selected_groups)
    script_set = set(selected_script_ids)
    output: list[dict[str, Any]] = []
    remaining = max(1, int(max_groups or 1))
    for raw in candidates:
        if remaining <= 0:
            break
        script_id = str(raw.get("script_code") or "").strip()
        paragraphs = [item for item in raw.get("paragraphs") or [] if isinstance(item, dict)]
        if paragraphs:
            selected_paragraphs = [
                copy.deepcopy(item)
                for item in paragraphs
                if (script_id, int(item.get("paragraph_no") or 0)) in group_set
            ][:remaining]
            if not selected_paragraphs:
                continue
            item = copy.deepcopy(raw)
            item["paragraphs"] = selected_paragraphs
            output.append(item)
            remaining -= len(selected_paragraphs)
            continue
        if script_id not in script_set:
            continue
        item = copy.deepcopy(raw)
        remaining -= 1
        output.append(item)
    return output


def _first_script_groups(
    candidates: list[dict[str, Any]],
    *,
    max_groups: int,
) -> list[dict[str, Any]]:
    """Bound exact-type closing expressions without adding a selector model call."""

    output: list[dict[str, Any]] = []
    remaining = max(1, int(max_groups or 1))
    for raw in candidates:
        if remaining <= 0:
            break
        item = copy.deepcopy(raw)
        paragraphs = [value for value in item.get("paragraphs") or [] if isinstance(value, dict)]
        if paragraphs:
            item["paragraphs"] = paragraphs[:remaining]
            remaining -= len(item["paragraphs"])
        else:
            remaining -= 1
        output.append(item)
    return output


def _script_reference(item: dict[str, Any]) -> dict[str, Any]:
    media = item.get("media") if isinstance(item.get("media"), dict) else {}
    return {
        "script_id": str(item.get("id") or ""),
        "source_id": str(item.get("script_code") or ""),
        "source_ref": str(item.get("source_ref") or ""),
        "script_name": str(item.get("script_name") or ""),
        "checkpoint_code": str(item.get("checkpoint_code") or ""),
        "checkpoint_name": str(item.get("checkpoint_name") or ""),
        "checkpoint_type": copy.deepcopy(item.get("checkpoint_type") or {}),
        "checkpoint_tag": copy.deepcopy(item.get("checkpoint_tag") or {}),
        "action_code": str(item.get("action_code") or ""),
        "action_name": str(item.get("action_name") or ""),
        "reference_text": str(item.get("body_text") or ""),
        "content_type": str(item.get("content_type") or "text"),
        "media": copy.deepcopy(media),
        "paragraphs": copy.deepcopy(item.get("paragraphs") or []),
        "sequence_links": copy.deepcopy(item.get("sequence_links") or []),
        "retrieval_match_scope": str(item.get("retrieval_match_scope") or ""),
        "authority_scope": str(item.get("authority_scope") or "approved_sales_expression"),
        "hard_fact_authority": bool(item.get("hard_fact_authority")),
        "data_quality_flags": copy.deepcopy(item.get("data_quality_flags") or []),
    }


def script_content_candidates(knowledge: dict[str, Any]) -> list[dict[str, Any]]:
    output = []
    for item in knowledge.get("candidates") or []:
        if not isinstance(item, dict):
            continue
        source_id = str(item.get("source_id") or "").strip()
        if not source_id:
            continue
        paragraphs = [value for value in item.get("paragraphs") or [] if isinstance(value, dict)]
        if not paragraphs:
            paragraphs = [
                {
                    "paragraph_no": 1,
                    "messages": [{"type": "text", "content": item.get("reference_text") or ""}],
                }
            ]
        for paragraph in paragraphs:
            paragraph_no = max(1, int(paragraph.get("paragraph_no") or 1))
            script_source_ref = str(
                item.get("source_ref")
                or f"follow_script:{item.get('script_id') or source_id}"
            )
            paragraph_source_ref = str(
                paragraph.get("source_ref") or f"{script_source_ref}:p{paragraph_no}"
            )
            reference_lines: list[str] = []
            structured_media: list[dict[str, str]] = []
            ordered_reference_messages: list[dict[str, Any]] = []
            for message in paragraph.get("messages") or []:
                if not isinstance(message, dict):
                    continue
                message_type = str(message.get("type") or "").strip().lower()
                if message_type == "text":
                    content = str(message.get("content") or "").strip()
                    if content:
                        reference_lines.append(content)
                        ordered_reference_messages.append({"type": "text", "content": content})
                    continue
                url = str(message.get("url") or "").strip()
                if message_type in {"image", "video"} and _is_http_url(url):
                    structured = {"type": message_type, "content": url}
                    structured_media.append(structured)
                    ordered_reference_messages.append(
                        {
                            **structured,
                            "file_id": int(message.get("file_id") or 0),
                            "title": str(message.get("title") or ""),
                            "remark": str(message.get("remark") or ""),
                        }
                    )
            output.append(
                {
                    "content_id": f"follow_script:{source_id}:p{paragraph_no}",
                    "source_script_id": str(item.get("script_id") or ""),
                    "source_script_code": source_id,
                    "source_ref": paragraph_source_ref,
                    "paragraph_no": paragraph_no,
                    "content_type": "follow_script_reference_group",
                    "name": f"{item.get('script_name') or source_id} / 第{paragraph_no}组",
                    "purpose": f"{item.get('checkpoint_name') or item.get('checkpoint_code')} / {item.get('action_name') or item.get('action_code')}",
                    "asset_role": "sales_reference",
                    "checkpoint_type": copy.deepcopy(item.get("checkpoint_type") or {}),
                    "checkpoint_tag": copy.deepcopy(item.get("checkpoint_tag") or {}),
                    "reference_text": "\n".join(reference_lines),
                    "reference_messages": ordered_reference_messages,
                    "messages": structured_media,
                    "required_structured_media": structured_media,
                    "selection_constraints": {
                        "authority_scope": "approved_sales_expression",
                        "hard_fact_authority": False,
                        "complete_reference_group": True,
                        "authoritative_facts_override": True,
                        "retrieval_match_scope": str(item.get("retrieval_match_scope") or ""),
                    },
                    "sequence_links": copy.deepcopy(item.get("sequence_links") or []),
                    "retrieval_match_scope": str(item.get("retrieval_match_scope") or ""),
                }
            )
    return output


def _store_tool_plan(route: dict[str, Any]) -> dict[str, Any]:
    store = route.get("store_query") if isinstance(route.get("store_query"), dict) else {}
    if not store.get("required"):
        return {
            "schema_version": "v3_store_tool_plan_v1",
            "status": "completed",
            "decision": "facts_sufficient",
            "tool_calls": [],
            "missing_facts": [],
            "evidence_refs": list(store.get("location_evidence_refs") or []),
            "reason": "semantic_router_did_not_require_store_lookup",
        }
    arguments = {
        "purpose": str(store.get("purpose") or "store_resolution"),
        # These flags are set by the V3 orchestrator, not inferred from customer
        # text. They let the shared store workflow consume the destination
        # resolver's structured administrative fact without changing V1/V2.
        "use_resolver_admin_fallback": True,
        "allow_broad_scope_delivery": True,
    }
    if str(store.get("destination_hint") or "").strip():
        arguments["destination_hint"] = str(store.get("destination_hint") or "").strip()
    return {
        "schema_version": "v3_store_tool_plan_v1",
        "status": "completed",
        "decision": "use_tools",
        "tool_calls": [
            {
                "name": "resolve_customer_store",
                "arguments": arguments,
                "purpose": str(store.get("purpose") or "store_resolution"),
                "evidence_refs": list(store.get("location_evidence_refs") or []),
            }
        ],
        "missing_facts": [],
        "evidence_refs": list(store.get("location_evidence_refs") or []),
        "reason": "semantic_router_requires_store_lookup",
    }


def _valid_refs(raw: Any, valid: set[str]) -> list[str]:
    return list(dict.fromkeys(str(item).strip() for item in raw or [] if str(item).strip() in valid))


def _structured_current_location_hint(shared_context: dict[str, Any]) -> str:
    """Recognize explicit structured location input for tool routing only."""

    current = (
        shared_context.get("current_message")
        if isinstance(shared_context.get("current_message"), dict)
        else {}
    )
    content = str(current.get("content") or current.get("raw_content") or "").strip()
    if not content:
        return ""
    match = re.search(r"(?:^|[\s，。；;])(?:门店位置|当前位置|位置|地址|定位)\s*[:：]\s*(.{2,120})", content)
    if not match:
        return ""
    hint = " ".join(match.group(1).split())
    if not hint:
        return ""
    if not re.search(r"(?:省|市|区|县|镇|街|路|号|广场|中心|大厦|商场|写字楼|定位)", hint):
        return ""
    return hint[:120]


def _sourced_destination_hint(
    raw_hint: Any,
    *,
    refs: list[str],
    shared_context: dict[str, Any],
) -> str:
    """Keep only a literal location hint backed by the model-cited messages.

    This is provenance validation, not location interpretation. The store
    resolver still owns normalization and administrative resolution.
    """

    hint = " ".join(str(raw_hint or "").split())[:300]
    if not hint or not refs:
        return ""
    messages: dict[str, str] = {}
    for item in shared_context.get("conversation") or []:
        if not isinstance(item, dict):
            continue
        ref = str(item.get("message_ref") or "").strip()
        if ref:
            messages[ref] = str(item.get("content") or item.get("text") or "")
    current = shared_context.get("current_message") if isinstance(shared_context.get("current_message"), dict) else {}
    messages["current_message"] = str(current.get("content") or current.get("raw_content") or "")
    cited_text = "\n".join(messages.get(ref, "") for ref in refs)
    return hint if hint in cited_text else ""


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _single_line(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]
