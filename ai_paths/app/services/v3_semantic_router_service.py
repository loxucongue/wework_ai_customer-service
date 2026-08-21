from __future__ import annotations

import asyncio
import copy
import time
from typing import Any
from urllib.parse import urlparse

from app.prompts.v3_semantic_router import (
    build_v3_checkpoint_router_messages,
    build_v3_script_selector_messages,
    build_v3_sequence_selector_messages,
)
from app.services.deepseek_semantic_client import DeepSeekSemanticClient
from app.services.follow_knowledge_client import ACTION_CODES, CHECKPOINT_CODES, FollowKnowledgeClient


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

    async def route(
        self,
        *,
        shared_context: dict[str, Any],
        sequence_result: dict[str, Any] | None = None,
        force_store_required: bool = False,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        sequence_result = sequence_result if isinstance(sequence_result, dict) else await self._sequence_index()
        sequences = [item for item in sequence_result.get("items") or [] if isinstance(item, dict)]
        router_messages = build_v3_checkpoint_router_messages(shared_context=shared_context)
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
            sequences=[],
        )
        if force_store_required:
            semantic_route.setdefault("store_query", {})["required"] = True
            semantic_route["store_query"]["purpose"] = str(
                semantic_route["store_query"].get("purpose") or "store_resolution"
            )
        if route_error:
            semantic_route.update({"status": "error", "reason": route_error})
        semantic_route["duration_ms"] = int((time.perf_counter() - router_started) * 1000)
        semantic_route["model_usage"] = copy.deepcopy(self.semantic_client.last_usage or {})
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

        semantic_route, sequence_selector_messages = await self._select_sequence_route(
            shared_context=shared_context,
            checkpoint_route=semantic_route,
            sequences=sequences,
        )
        sequence_selector_ms = int(semantic_route.get("duration_ms") or 0)
        semantic_route["phase"] = "non_store_final"
        knowledge, selector_messages = await self._knowledge_for_route(
            shared_context=shared_context,
            semantic_route=semantic_route,
            sequence_result=sequence_result,
            sequences=sequences,
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
                "sequence_selector_ms": sequence_selector_ms,
                "knowledge_ms": int(knowledge.get("duration_ms") or 0),
                "total_ms": total_ms,
            },
            "prompt_preview": {
                "router_messages": router_messages,
                "sequence_selector_messages": sequence_selector_messages,
                "selector_messages": selector_messages,
            },
        }

    async def route_after_store(
        self,
        *,
        shared_context: dict[str, Any],
        pre_route: dict[str, Any],
        store_resolution_fact: dict[str, Any],
        sequence_result: dict[str, Any] | None = None,
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
            "store_query": copy.deepcopy(pre_route.get("store_query") or {}),
            "sequence_match": _empty_sequence_match(),
            "script_queries": [],
        }
        semantic_route, router_messages = await self._select_sequence_route(
            shared_context=shared_context,
            checkpoint_route=checkpoint_route,
            sequences=sequences,
            store_resolution_fact=store_resolution_fact,
        )
        sequence_selector_ms = int(semantic_route.get("duration_ms") or 0)
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
        knowledge, selector_messages = await self._knowledge_for_route(
            shared_context=shared_context,
            semantic_route=semantic_route,
            sequence_result=sequence_result,
            sequences=sequences,
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
                "sequence_selector_ms": sequence_selector_ms,
                "knowledge_ms": int(knowledge.get("duration_ms") or 0),
                "total_ms": total_ms,
            },
            "prompt_preview": {
                "router_messages": [],
                "sequence_selector_messages": router_messages,
                "selector_messages": selector_messages,
            },
        }

    async def _select_sequence_route(
        self,
        *,
        shared_context: dict[str, Any],
        checkpoint_route: dict[str, Any],
        sequences: list[dict[str, Any]],
        store_resolution_fact: dict[str, Any] | None = None,
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
            store_resolution_fact=store_resolution_fact,
        )
        started = time.perf_counter()
        try:
            raw_selection = await self.semantic_client.chat_json(messages)
            error = ""
        except Exception as exc:
            raw_selection = {}
            error = f"{type(exc).__name__}: {exc}"[:500]
        payload = {
            "classification_status": checkpoint_route.get("classification_status"),
            "checkpoint": copy.deepcopy(checkpoint_route.get("checkpoint") or {}),
            "store_query": copy.deepcopy(checkpoint_route.get("store_query") or {}),
            "sequence_match": copy.deepcopy(raw_selection.get("sequence_match") or {}),
            "script_queries": [],
        }
        output = _normalize_semantic_route(
            payload,
            shared_context=shared_context,
            sequences=candidates,
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
    ) -> tuple[dict[str, Any], list[dict[str, str]]]:
        started = time.perf_counter()
        sequence_candidates = _selected_sequences(sequences, semantic_route)
        script_result = await self._script_candidates(semantic_route)
        script_candidates = [item for item in script_result.get("items") or [] if isinstance(item, dict)]
        selector: dict[str, Any] = {"status": "not_needed", "reason": "candidate_count_within_threshold"}
        if len(script_candidates) > self.script_threshold:
            selector, script_candidates = await self._narrow_scripts_by_action(
                shared_context=shared_context,
                semantic_route=semantic_route,
                candidates=script_candidates,
            )

        selector_messages = copy.deepcopy(selector.get("messages") or [])
        selector_for_runtime = {
            key: copy.deepcopy(value)
            for key, value in selector.items()
            if key != "messages"
        }
        knowledge = {
            "schema_version": "v3_knowledge_evidence_v1",
            "status": "ok" if sequence_candidates or script_candidates else "empty",
            "source": "follow_knowledge_api",
            "sequence_index_status": sequence_result.get("status"),
            "sequence_index_total": int(sequence_result.get("total") or 0),
            "sequence_candidates": sequence_candidates,
            "script_query_results": script_result.get("query_results") or [],
            "script_option_count": int(script_result.get("option_count") or 0),
            "candidate_count": len(script_candidates),
            "candidates": [_script_reference(item) for item in script_candidates],
            "selector": selector_for_runtime,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
        return knowledge, selector_messages

    async def _sequence_index(self) -> dict[str, Any]:
        if self.knowledge_client is None or not self.knowledge_client.available:
            return {"status": "disabled", "reason": "follow_knowledge_not_configured", "total": 0, "items": []}
        return await self.knowledge_client.query_all_sequences()

    async def _script_candidates(self, semantic_route: dict[str, Any]) -> dict[str, Any]:
        queries = semantic_route.get("script_queries") if isinstance(semantic_route.get("script_queries"), list) else []
        if self.knowledge_client is None or not self.knowledge_client.available or not queries:
            return {"status": "empty", "option_count": 0, "items": [], "query_results": []}
        tasks = [
            self.knowledge_client.query_all_scripts(
                checkpoint_code=str(item.get("checkpoint_code") or ""),
                action_code=str(item.get("action_code") or ""),
            )
            for item in queries
        ]
        results = await asyncio.gather(*tasks)
        by_code: dict[str, dict[str, Any]] = {}
        query_results: list[dict[str, Any]] = []
        for query, result in zip(queries, results):
            query_results.append(
                {
                    "checkpoint_code": query.get("checkpoint_code"),
                    "action_code": query.get("action_code"),
                    "sequence_id": query.get("sequence_id"),
                    "step_id": query.get("step_id"),
                    "status": result.get("status"),
                    "total": int(result.get("total") or 0),
                    "reason": result.get("reason", ""),
                    "duration_ms": int(result.get("duration_ms") or 0),
                    "cache_hit_pages": int(result.get("cache_hit_pages") or 0),
                }
            )
            for raw in result.get("items") or []:
                if not isinstance(raw, dict):
                    continue
                code = str(raw.get("script_code") or "").strip()
                if not code:
                    continue
                item = by_code.setdefault(code, copy.deepcopy(raw))
                links = item.setdefault("sequence_links", [])
                link = {
                    "sequence_id": str(query.get("sequence_id") or ""),
                    "step_id": str(query.get("step_id") or ""),
                    "action_code": str(query.get("action_code") or ""),
                }
                if link not in links:
                    links.append(link)
        return {
            "status": "ok" if by_code else "empty",
            "option_count": len(by_code),
            "items": list(by_code.values()),
            "query_results": query_results,
        }

    async def _narrow_scripts(
        self,
        *,
        shared_context: dict[str, Any],
        semantic_route: dict[str, Any],
        candidates: list[dict[str, Any]],
        max_scripts: int | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        limit = max(1, int(max_scripts or self.max_scripts))
        messages = build_v3_script_selector_messages(
            shared_context=shared_context,
            semantic_route=semantic_route,
            candidates=candidates,
            max_scripts=limit,
        )
        started = time.perf_counter()
        try:
            raw = await self.semantic_client.chat_json(messages)
            valid_ids = {str(item.get("script_code") or "") for item in candidates}
            selected = []
            for value in raw.get("selected_script_ids") or []:
                script_id = str(value or "").strip()
                if script_id in valid_ids and script_id not in selected:
                    selected.append(script_id)
                if len(selected) >= limit:
                    break
            selected_set = set(selected)
            return (
                {
                    "status": "ok" if selected else "empty",
                    "reason": str(raw.get("reason") or "")[:500],
                    "selected_script_ids": selected,
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                    "model_usage": copy.deepcopy(self.semantic_client.last_usage or {}),
                    "messages": messages,
                },
                [item for item in candidates if str(item.get("script_code") or "") in selected_set],
            )
        except Exception as exc:
            return (
                {
                    "status": "error",
                    "reason": f"{type(exc).__name__}: {exc}"[:500],
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


def _normalize_semantic_route(
    raw: Any,
    *,
    shared_context: dict[str, Any],
    sequences: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = raw if isinstance(raw, dict) else {}
    valid_refs = {"current_message"}
    valid_refs.update(
        str(item.get("message_ref") or "").strip()
        for item in shared_context.get("conversation") or []
        if isinstance(item, dict) and str(item.get("message_ref") or "").strip()
    )
    by_id = {str(item.get("id") or "").strip(): item for item in sequences if str(item.get("id") or "").strip()}
    checkpoint_raw = payload.get("checkpoint") if isinstance(payload.get("checkpoint"), dict) else {}
    primary = str(checkpoint_raw.get("primary_code") or "").strip().lower()
    secondary = str(checkpoint_raw.get("secondary_code") or "").strip().lower()
    if primary not in CHECKPOINT_CODES - {"all"}:
        primary = ""
    if secondary not in CHECKPOINT_CODES - {"all"} or secondary == primary:
        secondary = ""
    classification_status = str(payload.get("classification_status") or "").strip().lower()
    if classification_status not in {"clear", "ambiguous", "none"}:
        classification_status = "none" if not primary else "clear"
    sequence_raw = payload.get("sequence_match") if isinstance(payload.get("sequence_match"), dict) else {}
    sequence_ids = []
    for value in sequence_raw.get("sequence_ids") or []:
        sequence_id = str(value or "").strip()
        if sequence_id in by_id and sequence_id not in sequence_ids:
            sequence_ids.append(sequence_id)
        if len(sequence_ids) >= 3:
            break
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
    for value in sequence_raw.get("relevant_step_ids") or []:
        step_id = str(value or "").strip()
        if step_id in valid_steps and step_id not in step_ids:
            step_ids.append(step_id)
        if len(step_ids) >= 4:
            break
    script_queries = []
    seen_queries: set[tuple[str, str, str, str]] = set()
    for item in payload.get("script_queries") or []:
        if not isinstance(item, dict):
            continue
        sequence_id = str(item.get("sequence_id") or "").strip()
        step_id = str(item.get("step_id") or "").strip()
        step_link = valid_steps.get(step_id)
        checkpoint = str(item.get("checkpoint_code") or primary).strip().lower()
        action = str(item.get("action_code") or "").strip().lower()
        if not step_link or step_link[0] != sequence_id:
            continue
        step = step_link[1]
        if action != str(step.get("action_code") or "").strip().lower():
            continue
        if checkpoint not in CHECKPOINT_CODES - {"all"} or action not in ACTION_CODES:
            continue
        key = (checkpoint, action, sequence_id, step_id)
        if key in seen_queries:
            continue
        seen_queries.add(key)
        script_queries.append(
            {"checkpoint_code": checkpoint, "action_code": action, "sequence_id": sequence_id, "step_id": step_id}
        )
    store_raw = payload.get("store_query") if isinstance(payload.get("store_query"), dict) else {}
    location_refs = _valid_refs(store_raw.get("location_evidence_refs"), valid_refs)
    destination_hint = _sourced_destination_hint(
        store_raw.get("destination_hint"),
        refs=location_refs,
        shared_context=shared_context,
    )
    return {
        "schema_version": "v3_semantic_route_v1",
        "status": "ok",
        "classification_status": classification_status,
        "checkpoint": {
            "primary_code": primary,
            "secondary_code": secondary,
            "evidence_refs": _valid_refs(checkpoint_raw.get("evidence_refs"), valid_refs),
            "reason": str(checkpoint_raw.get("reason") or "")[:500],
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
            "required": bool(store_raw.get("required")),
            "purpose": str(store_raw.get("purpose") or "none")[:100],
            "location_evidence_refs": location_refs,
            "destination_hint": destination_hint,
        },
        "script_queries": script_queries,
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


def _sequences_for_checkpoint(
    sequences: list[dict[str, Any]],
    route: dict[str, Any],
) -> list[dict[str, Any]]:
    """Reduce retrieval candidates using only upstream taxonomy metadata.

    This does not infer the customer's objection. DeepSeek already selected the
    checkpoint; code only applies the API's declared checkpoint labels. Exact
    checkpoint sequences take precedence. Generic `all` sequences are exposed
    only when the knowledge base has no exact sequence for that checkpoint.
    """

    checkpoint = str((route.get("checkpoint") or {}).get("primary_code") or "").strip().lower()
    if checkpoint not in CHECKPOINT_CODES - {"all"}:
        return []
    exact = [
        item
        for item in sequences
        if str(item.get("checkpoint_code") or "").strip().lower() == checkpoint
    ]
    if exact:
        return exact
    return [
        item
        for item in sequences
        if str(item.get("checkpoint_code") or "").strip().lower() == "all"
    ]


def _deferred_store_pre_route(route: dict[str, Any]) -> dict[str, Any]:
    """Keep only store routing evidence until authoritative store facts exist."""

    output = copy.deepcopy(route)
    output["phase"] = "pre_store_pending"
    output["provisional_checkpoint"] = copy.deepcopy(output.get("checkpoint") or {})
    output["checkpoint"] = {
        "primary_code": "",
        "secondary_code": "",
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
    selected_ids = set((route.get("sequence_match") or {}).get("sequence_ids") or [])
    relevant_steps = set((route.get("sequence_match") or {}).get("relevant_step_ids") or [])
    selection_reason = str((route.get("sequence_match") or {}).get("reason") or "")[:500]
    output = []
    for item in sequences:
        sequence_id = str(item.get("id") or "").strip()
        if sequence_id not in selected_ids:
            continue
        selected_steps = [
            step
            for step in item.get("steps") or []
            if isinstance(step, dict) and str(step.get("id") or "") in relevant_steps
        ]
        output.append(
            {
                "sequence_id": sequence_id,
                "sequence_name": _single_line(item.get("sequence_name"), 200),
                "checkpoint_code": str(item.get("checkpoint_code") or ""),
                # The upstream description/remark fields may contain finished
                # customer copy and stale business claims. DeepSeek sees only
                # the compact index and selects the relevant action; Reply gets
                # that selection evidence, while finished scripts remain a
                # separate, explicitly non-authoritative reference channel.
                "selection_reason": selection_reason,
                "steps": [
                    {
                        "step_id": str(step.get("id") or ""),
                        "sort_order": int(step.get("sort_order") or 0),
                        "action_code": str(step.get("action_code") or ""),
                        "action_name": str(step.get("action_name") or ""),
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
    if not selected_ids or not relevant_step_ids or checkpoint not in CHECKPOINT_CODES - {"all"}:
        return output

    queries = output.setdefault("script_queries", [])
    seen_actions = {
        (str(item.get("sequence_id") or ""), str(item.get("action_code") or ""))
        for item in queries
        if isinstance(item, dict)
    }
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
            if not step_id or action not in ACTION_CODES or key in seen_actions:
                continue
            queries.append(
                {
                    "checkpoint_code": checkpoint,
                    "action_code": action,
                    "sequence_id": sequence_id,
                    "step_id": step_id,
                    "query_source": "model_selected_relevant_step",
                }
            )
            seen_actions.add(key)
    return output


def _script_reference(item: dict[str, Any]) -> dict[str, Any]:
    media = item.get("media") if isinstance(item.get("media"), dict) else {}
    return {
        "script_id": str(item.get("id") or ""),
        "source_id": str(item.get("script_code") or ""),
        "script_name": str(item.get("script_name") or ""),
        "checkpoint_code": str(item.get("checkpoint_code") or ""),
        "checkpoint_name": str(item.get("checkpoint_name") or ""),
        "action_code": str(item.get("action_code") or ""),
        "action_name": str(item.get("action_name") or ""),
        "reference_text": str(item.get("body_text") or ""),
        "content_type": str(item.get("content_type") or "text"),
        "media": copy.deepcopy(media),
        "sequence_links": copy.deepcopy(item.get("sequence_links") or []),
        "authority": "sales_reference_only_authoritative_facts_override",
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
        media = item.get("media") if isinstance(item.get("media"), dict) else {}
        media_url = str(media.get("url") or "").strip()
        structured_media = []
        content_type = str(item.get("content_type") or "text").lower()
        if _is_http_url(media_url) and content_type in {"image", "image_text"}:
            structured_media.append({"type": "image", "content": media_url})
        elif _is_http_url(media_url) and content_type == "video":
            structured_media.append({"type": "video", "content": media_url})
        output.append(
            {
                "content_id": f"follow_script:{source_id}",
                "content_type": "follow_script_reference",
                "name": str(item.get("script_name") or source_id),
                "purpose": f"{item.get('checkpoint_name') or item.get('checkpoint_code')} / {item.get('action_name') or item.get('action_code')}",
                "asset_role": "sales_reference",
                "reference_text": str(item.get("reference_text") or ""),
                "messages": structured_media,
                "required_structured_media": structured_media,
                "selection_constraints": {"reference_only": True, "authoritative_facts_override": True},
                "sequence_links": copy.deepcopy(item.get("sequence_links") or []),
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
