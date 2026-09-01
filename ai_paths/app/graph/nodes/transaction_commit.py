from __future__ import annotations

import copy
from typing import Any, Callable

from app.graph.state import AgentState
from app.services.trace_logger import TraceLogger
from app.services.v3_sop_execution_service import SopExecutionService
from app.graph.nodes.reply_contract import (
    DEFERRED_COMMIT_TOOL_NAMES,
    _commit_action_violations,
    _string_list,
)


def create_prepare_commit_node(*, trace_logger: TraceLogger) -> Callable[[AgentState], Any]:
    async def prepare_commit(state: AgentState) -> dict[str, Any]:
        with trace_logger.node(state, "prepare_commit", {}) as span:
            normalized: list[dict[str, Any]] = []
            violations: list[str] = []
            for item in state.get("commit_actions") or []:
                if not isinstance(item, dict):
                    violations.append("commit_action_not_object")
                    continue
                name = str(item.get("name") or "").strip()
                if name not in DEFERRED_COMMIT_TOOL_NAMES:
                    violations.append(f"commit_action_not_allowed:{name or 'missing'}")
                    continue
                arguments = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
                evidence_refs = _string_list(item.get("evidence_refs"))
                action_violations = _commit_action_violations(
                    name,
                    arguments,
                    state,
                    evidence_refs=evidence_refs,
                )
                if action_violations:
                    violations.extend(action_violations)
                    continue
                normalized.append(
                    {
                        "name": name,
                        **copy.deepcopy(arguments),
                        "evidence_refs": evidence_refs,
                    }
                )
            output = {
                "planner_tool_calls": normalized,
                "required_tools": normalized,
                "commit_result": {
                    "status": "prepared" if normalized else "no_actions",
                    "violations": violations,
                },
                "trace": state.get("trace", []),
            }
            span["output_snapshot"] = {
                "commit_tool_names": [item.get("name") for item in normalized],
                "violations": violations,
            }
            return output

    return prepare_commit


def create_commit_coordinator_node(
    *,
    trace_logger: TraceLogger,
    sop_execution_service: SopExecutionService | None,
) -> Callable[[AgentState], Any]:
    async def commit(state: AgentState) -> dict[str, Any]:
        with trace_logger.node(state, "commit_coordinator", {}) as span:
            result = dict(state.get("commit_result") or {})
            result["write_results"] = copy.deepcopy(state.get("commit_tool_results") or {})
            if sop_execution_service is not None:
                try:
                    # A Reply may use only part of a Gate candidate. Preserve
                    # that customer-visible answer, but record SOP completion
                    # only when all required structured payloads were sent.
                    from app.graph.nodes.reply_validation import (
                        completed_parallel_selected_content_ids,
                    )

                    commit_state = dict(state)
                    commit_state["selected_content_ids"] = completed_parallel_selected_content_ids(
                        list(state.get("reply_messages") or []),
                        state,
                        list(state.get("selected_content_ids") or []),
                    )
                    result["sop"] = sop_execution_service.commit_reply_selected_chat_gate_candidate(
                        state=commit_state,
                        reply_messages=list(state.get("reply_messages") or []),
                    )
                except Exception as exc:
                    result["sop"] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
            else:
                result["sop"] = {"status": "skipped", "reason": "sop_execution_service_missing"}
            output = {"commit_result": result, "trace": state.get("trace", [])}
            span["output_snapshot"] = result
            return output

    return commit
