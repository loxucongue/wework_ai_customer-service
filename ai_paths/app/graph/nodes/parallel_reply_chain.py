from __future__ import annotations

import asyncio
import copy
import re
import time
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

from app.graph.nodes.common import json_dumps, model_usage_snapshot
from app.graph.nodes.sent_message_summary import sent_message_summary_for_model
from app.graph.nodes.store_scope_summary import build_store_scope_summary
from app.graph.nodes.v2_derived_observations import build_v2_derived_observations
from app.graph.state import AgentState
from app.policies.business_rules import parallel_reply_business_rules_for_model
from app.schemas import ChatRequest
from app.services.coze_client import CozeClient
from app.services.model_client import ModelClient
from app.services.customer_payment_state import is_paid_deposit_state, resolved_payment_fact
from app.services.payment_collection import payment_collection_content
from app.services.sop_execution_service import SopExecutionService
from app.services.trace_logger import TraceLogger
from app.services.v2_sales_recall_service import V2SalesRecallService


BEIJING_TZ = ZoneInfo("Asia/Shanghai")
READ_ONLY_TOOL_NAMES = {
    "resolve_customer_store",
    "kb_search",
    "appointment_record_query",
}
DEFERRED_COMMIT_TOOL_NAMES = {"create_work_order", "add_customer_mobile"}


TOOL_PLANNER_SYSTEM_PROMPT = """# 对话连续性优先
- 决定事实是否充足前，必须阅读完整的带时间对话，不能只看当前消息的字面。
- 极短确认、仅标点追问或疑惑表达可能是在继续紧邻的未完成事实任务，不能把它当成脱离历史的新问题。
- 若近期客户原话已经提供可用地点，而上一轮仍未交付客户索要的门店结果，应继续该门店任务并规划 `resolve_customer_store`，同时引用相关客户消息的 message_ref。
- 不得因为当前消息很短，就要求客户重复近期已经给出的城市、区域、地标或定位。

你是 V3 回复链路的只读 Tool Planner。你不是客服，也不是销售策略模型。

你的唯一任务：根据 shared_context 判断最终 Reply 在回答当前消息前是否缺少实时事实，并规划最少的只读工具调用。

请只输出严格 json 对象。

# 边界
- 不输出客户可见话术。
- 不判断客户心理、意向等级、成交阶段、是否回主线或是否发预约金卡。
- 不选择 SOP、精准话术、销冠召回或成交理由。
- 不规划写操作、发送动作、开单、手机号同步或排客。
- 已在 authoritative_facts 中存在且没有冲突的事实，不重复查询。但 visible_store_scope 只证明客户权限范围内的区域覆盖和数量，不是本轮地名匹配结果，也不提供具体门店名、地址、排序或最近推荐；客户索要具体门店或地址时仍需查询。
- 工具参数只能来自当前消息、完整聊天或结构事实，并在 evidence_refs 引用真实 message_ref，例如 current_message、conv_001。
- 当前消息是本轮绑定任务。历史已完成的其他城市、门店或销售任务，不能替代客户当前提出的新事实请求。

# 允许工具
1. resolve_customer_store：所有门店、地址、定位、区县、县级市、乡镇、村、地标、远近、营业信息和门店详情场景统一调用。不要自行拼接最终地址；完整聊天和当前消息会交给工具内的地点解析模型。参数只需 purpose，可选 destination_hint。
2. kb_search：查询真实案例等素材。案例使用 kb_name=case_studies，query 来自客户原话或聊天原文。
3. appointment_record_query：只读查询已有预约记录；普通登记流程不得规划档期查询或开单。

# 案例查询
- 客户当前直接问效果、改善程度、一次效果、案例或效果图，且近期没有真实案例图片发送证据时，规划 kb_search(kb_name=case_studies)。
- 客户质疑“真的、靠谱吗、可信吗”时，先判断紧邻话题被质疑对象；若对象是效果或案例真实性，且没有近期真实案例图，规划案例查询。
- 上一轮刚发过真实案例图且客户只是评价/追问该素材时，不重复查询；客户明确要新的或更多案例时仍可查询。

# 门店查询
- 当前消息问某地门店、索要地址、远近、导航、停车、营业时间，或消息本身是省、市、区县、县级市、乡镇、村、道路、地标、定位卡时，除非紧邻历史已经真实发送对应门店卡且客户只是在确认该卡，否则规划 resolve_customer_store。
- 客户补充下级地名、改口到新地点或反复比较区域时，不在 Tool Planner 里重建最终目的地；完整历史交给 resolve_customer_store 内的地点解析模型处理。
- 客户正在回答上一轮的城市、区县、商圈或定位追问时，当前即使只有“汉口”“番禺”“武平”这类短地点，也代表新的门店查询条件，必须规划 resolve_customer_store。历史门店文字、上一轮候选列表或普通助手回复不能替代本轮结构化门店工具事实。
- 客户每次补充、纠正或切换地点，都可能改变可见候选和距离结果；除非紧邻历史已经真实发送唯一对应门店卡且客户只是确认该卡，否则必须重新查询，不能因为当前消息没有重复说“门店/地址”而跳过。
- 客户明确要求重发地址、位置、导航或门店卡时，若本轮 authoritative_facts 没有可直接重放的真实 store_id 与结构消息，必须重新规划 resolve_customer_store。客户只说“这家、收到、可以”则不重查。
- 客户问“更近/最近”但历史与当前消息没有可解析的位置原点时，仍调用 resolve_customer_store，由工具返回缺失事实，不自行挑门店。
- `missing_facts` 只记录回答客户当前明确请求不可缺少的事实，不能记录可选销售机会。完整历史已经说明门店是按客户位置匹配或相对合适，并已交付门店结果时，当前窗口没有再次携带原始位置不等于该事实从未收集；客户只是评价远近、没有主动要求重新匹配、没有提供新位置也没有指出原位置错误时，不得把位置写成缺失事实。

输出格式：
{
  "decision": "use_tools | facts_sufficient",
  "tool_calls": [{"name":"resolve_customer_store","arguments":{},"purpose":"","evidence_refs":[]}],
  "missing_facts": [{"field":"","reason":"","evidence_refs":[]}],
  "evidence_refs": [],
  "reason": ""
}
"""


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


def create_parallel_evidence_node(
    *,
    trace_logger: TraceLogger,
    model_client: ModelClient | None,
    sop_execution_service: SopExecutionService | None,
    coze_client: CozeClient | None = None,
) -> Callable[[AgentState], Any]:
    async def parallel_evidence(state: AgentState) -> dict[str, Any]:
        started = time.perf_counter()
        with trace_logger.node(
            state,
            "parallel_gate_tool_planner",
            {"shared_context_schema": (state.get("shared_context") or {}).get("schema_version")},
        ) as span:
            gate_task = asyncio.create_task(_run_content_gate(state, sop_execution_service))
            planner_task = asyncio.create_task(_run_tool_planner(state, model_client))
            recall_task = asyncio.create_task(_run_sales_recall(state, coze_client))
            raw_gate, raw_tool_plan = await asyncio.gather(
                gate_task,
                planner_task,
                return_exceptions=True,
            )
            raw_recall = await _finish_sales_recall(
                recall_task,
                coze_client=coze_client,
                started=started,
            )
            gate_result = _completed_parallel_branch(
                raw_gate,
                schema_version="content_gate_result_v4",
                fallback={
                    "route_advice": "tools_only",
                    "content_candidate_ids": [],
                    "content_candidates": [],
                },
            )
            tool_plan = _completed_parallel_branch(
                raw_tool_plan,
                schema_version="tool_plan_v1",
                fallback={
                    "tool_calls": [],
                    "missing_facts": [],
                    "evidence_refs": [],
                },
            )
            sales_recall = _completed_sales_recall(raw_recall)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            metrics = {
                "elapsed_ms": elapsed_ms,
                "gate_duration_ms": int(gate_result.get("duration_ms") or 0),
                "tool_planner_duration_ms": int(tool_plan.get("duration_ms") or 0),
                "sales_recall_duration_ms": int(sales_recall.get("duration_ms") or 0),
                "parallel_expected_elapsed_ms": max(
                    int(gate_result.get("duration_ms") or 0),
                    int(tool_plan.get("duration_ms") or 0),
                    int(sales_recall.get("duration_ms") or 0),
                ),
            }
            output = {
                "content_gate_result": gate_result,
                "tool_plan": tool_plan,
                "sales_recall": sales_recall,
                "parallel_branch_metrics": metrics,
                # Compatibility input for the existing read-only executor.
                "planner_tool_calls": list(tool_plan.get("tool_calls") or []),
                "required_tools": list(tool_plan.get("tool_calls") or []),
                "planner_source": "parallel_tool_planner",
                "trace": state.get("trace", []),
            }
            span["entry"]["tool_calls"] = [
                {"name": "content_gate_model", "output": _branch_trace_output(gate_result)},
                {"name": "tool_planner_model", "output": _branch_trace_output(tool_plan)},
                {"name": "v2_sales_recall", "output": _branch_trace_output(sales_recall)},
            ]
            span["output_snapshot"] = {
                "gate_route_advice": gate_result.get("route_advice"),
                "selected_content_ids": gate_result.get("content_candidate_ids") or [],
                "tool_names": [item.get("name") for item in tool_plan.get("tool_calls") or []],
                "tool_plan_status": tool_plan.get("status"),
                "tool_plan_reason": tool_plan.get("reason"),
                "tool_plan_violations": tool_plan.get("violations") or [],
                "tool_plan_initial_violations": tool_plan.get("initial_violations") or [],
                "tool_plan_decision": tool_plan.get("decision"),
                "sales_recall_status": sales_recall.get("status"),
                "sales_recall_candidates": sales_recall.get("candidate_count"),
                "missing_fact_count": len(tool_plan.get("missing_facts") or []),
                "metrics": metrics,
            }
            return output

    return parallel_evidence


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
                "gate_evidence": _drop_keys(
                    gate,
                    {"content_candidates", "selector_input", "selector_output", "model_usage"},
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


def _normalized_tool_fact_envelope(value: Any) -> dict[str, Any]:
    """Expose only executor-owned facts, never legacy sales semantics."""

    if not isinstance(value, dict):
        return {}
    allowed_fields = (
        "usable_facts",
        "missing_facts",
        "risky_facts",
        "unsupported_claims",
        "structured_facts",
    )
    return {
        field: copy.deepcopy(value[field])
        for field in allowed_fields
        if field in value
    }


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


async def _run_content_gate(
    state: AgentState,
    service: SopExecutionService | None,
) -> dict[str, Any]:
    started = time.perf_counter()
    if service is None:
        return {
            "schema_version": "content_gate_result_v4",
            "status": "unavailable",
            "route_advice": "tools_only",
            "content_candidate_ids": [],
            "content_candidates": [],
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
    try:
        result = await service.evaluate_chat_gate(
            _request_from_state(state),
            request_id=str(state.get("request_id") or ""),
            request_context=dict(state.get("request_context") or {}),
            record_task=False,
            shared_state={"shared_context": _content_gate_shared_context(state)},
        )
        content_candidates = _dict_list(result.get("candidate_packs"))
        candidate_ids = [
            str(item.get("content_id") or "").strip()
            for item in content_candidates
            if str(item.get("content_id") or "").strip()
        ]
        return {
            "schema_version": "content_gate_result_v4",
            "status": "completed",
            "route_advice": _gate_route_advice(result),
            "content_candidate_ids": list(dict.fromkeys(candidate_ids)),
            "content_candidates": content_candidates,
            "reason": str(result.get("reason") or ""),
            "sop_progress_evidence": copy.deepcopy(result.get("sop_progress_evidence") or {}),
            "candidate_commit": {
                "sop_pack_ids": [
                    str(item.get("content_id") or "").strip()
                    for item in content_candidates
                    if item.get("content_type") == "sop" and str(item.get("content_id") or "").strip()
                ]
            },
            "model_usage": copy.deepcopy(result.get("model_usage") or {}),
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
    except Exception as exc:
        return {
            "schema_version": "content_gate_result_v4",
            "status": "error",
            "route_advice": "tools_only",
            "content_candidate_ids": [],
            "content_candidates": [],
            "error": f"{type(exc).__name__}: {exc}",
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }


def _content_gate_shared_context(state: AgentState) -> dict[str, Any]:
    """Give Gate delivery facts, never prior model sales observations."""

    shared = copy.deepcopy(state.get("shared_context") or {})
    observations = (
        shared.get("derived_observations")
        if isinstance(shared.get("derived_observations"), dict)
        else {}
    )
    if observations:
        observations.pop("prior_model_observations", None)
        shared["derived_observations"] = observations
    return shared


async def _run_sales_recall(state: AgentState, coze_client: CozeClient | None) -> dict[str, Any]:
    return await V2SalesRecallService(coze_client).recall(copy.deepcopy(state.get("shared_context") or {}))


async def _finish_sales_recall(
    task: asyncio.Task,
    *,
    coze_client: CozeClient | None,
    started: float,
) -> Any:
    settings = getattr(coze_client, "settings", None) if coze_client is not None else None
    wait_seconds = float(getattr(settings, "v2_sales_recall_wait_seconds", 2.5) or 0)
    if task.done():
        return await task
    remaining_wait = wait_seconds - max(0.0, time.perf_counter() - started)
    if remaining_wait <= 0:
        task.cancel()
        return _sales_recall_timeout(started, "kb_recall_not_ready")
    try:
        return await asyncio.wait_for(task, timeout=remaining_wait)
    except asyncio.TimeoutError:
        task.cancel()
        return _sales_recall_timeout(started, "kb_recall_timeout")


def _sales_recall_timeout(started: float, reason: str) -> dict[str, Any]:
    return {
        "schema_version": "v2_sales_recall_v1",
        "status": "timeout",
        "source": "coze_workflow",
        "reason": reason,
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "candidate_count": 0,
        "candidates": [],
    }


def _completed_sales_recall(value: Any) -> dict[str, Any]:
    if isinstance(value, Exception):
        return {
            "schema_version": "v2_sales_recall_v1",
            "status": "error",
            "source": "coze_workflow",
            "reason": f"{type(value).__name__}: {value}",
            "candidate_count": 0,
            "candidates": [],
        }
    if isinstance(value, dict):
        value.setdefault("schema_version", "v2_sales_recall_v1")
        value.setdefault("candidates", [])
        value["candidate_count"] = len(value.get("candidates") or [])
        return value
    return {
        "schema_version": "v2_sales_recall_v1",
        "status": "error",
        "source": "coze_workflow",
        "reason": "invalid_recall_result",
        "candidate_count": 0,
        "candidates": [],
    }


async def _run_tool_planner(state: AgentState, model_client: ModelClient | None) -> dict[str, Any]:
    started = time.perf_counter()
    if model_client is None or not model_client.available:
        protocol_calls = _protocol_required_read_only_tools(state)
        return {
            "schema_version": "tool_plan_v1",
            "status": "protocol_recovered" if protocol_calls else "unavailable",
            "tool_calls": protocol_calls,
            "missing_facts": [],
            "evidence_refs": [],
            "protocol_recovery": bool(protocol_calls),
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
    try:
        messages = [
            {"role": "system", "content": TOOL_PLANNER_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json_dumps(
                    {
                        "current_request_focus": copy.deepcopy(
                            (state.get("shared_context") or {}).get("current_message") or {}
                        ),
                        "shared_context": _tool_planner_shared_context(state),
                    }
                ),
            },
        ]
        payload = await model_client.chat_json(
            messages,
            tier="planner",
            temperature=0,
        )
        tool_calls, violations = _normalize_read_only_tool_calls(
            payload.get("tool_calls"),
            valid_evidence_refs=_shared_context_evidence_refs(state),
        )
        violations.extend(_protocol_tool_plan_violations(state, tool_calls))
        violations.extend(_tool_plan_decision_violations(payload, tool_calls))
        initial_violations = list(violations)
        repair_attempted = False
        repair_error = ""
        if violations:
            repair_attempted = True
            try:
                repaired_payload = await model_client.chat_json(
                    [
                        *messages,
                        {
                            "role": "user",
                            "content": json_dumps(
                                {
                                    "schema_violations": violations,
                                    "instruction": (
                                        "只修正工具计划的 schema、必填参数和 evidence_refs。"
                                        "不要新增客户话术、业务判断或写操作；返回完整 json 对象。"
                                    ),
                                }
                            ),
                        },
                    ],
                    tier="planner",
                    temperature=0,
                )
                repaired_calls, repaired_violations = _normalize_read_only_tool_calls(
                    repaired_payload.get("tool_calls"),
                    valid_evidence_refs=_shared_context_evidence_refs(state),
                )
                repaired_violations.extend(
                    _protocol_tool_plan_violations(state, repaired_calls)
                )
                repaired_violations.extend(
                    _tool_plan_decision_violations(repaired_payload, repaired_calls)
                )
                payload = repaired_payload
                tool_calls = repaired_calls
                violations = repaired_violations
            except Exception as exc:
                repair_error = f"{type(exc).__name__}: {exc}"
        protocol_recovery_calls = _protocol_required_read_only_tools(state)
        protocol_recovery = bool(
            protocol_recovery_calls
            and any(item.startswith("protocol_required_tool_missing:") for item in violations)
        )
        if protocol_recovery_calls:
            # A protocol-required tool may already be present but carry a
            # model-shortened title instead of the location card's full
            # address/coordinates. Always apply protocol-owned arguments;
            # `protocol_recovery` remains reserved for a missing tool name.
            tool_calls = _merge_tool_calls(tool_calls, protocol_recovery_calls)
        if protocol_recovery:
            violations = [
                item
                for item in violations
                if not item.startswith("protocol_required_tool_missing:")
            ]
        return {
            "schema_version": "tool_plan_v1",
            "status": (
                "protocol_recovered"
                if protocol_recovery and not violations
                else "completed" if not violations else "completed_with_violations"
            ),
            "tool_calls": tool_calls,
            "decision": str(payload.get("decision") or ""),
            "missing_facts": _dict_list(payload.get("missing_facts")),
            "evidence_refs": _string_list(payload.get("evidence_refs")),
            "reason": str(payload.get("reason") or ""),
            "violations": violations,
            "initial_violations": initial_violations,
            "repair_attempted": repair_attempted,
            "repair_error": repair_error,
            "protocol_recovery": protocol_recovery,
            "model_usage": model_usage_snapshot(model_client),
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
    except Exception as exc:
        protocol_calls = _protocol_required_read_only_tools(state)
        return {
            "schema_version": "tool_plan_v1",
            "status": "protocol_recovered" if protocol_calls else "error",
            "tool_calls": protocol_calls,
            "missing_facts": [],
            "evidence_refs": [],
            "error": f"{type(exc).__name__}: {exc}",
            "protocol_recovery": bool(protocol_calls),
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }


def _protocol_required_read_only_tools(state: AgentState) -> list[dict[str, Any]]:
    """Recover only tool calls mandated by an inbound message protocol."""

    shared = state.get("shared_context") if isinstance(state.get("shared_context"), dict) else {}
    current = shared.get("current_message") if isinstance(shared.get("current_message"), dict) else {}
    facts = shared.get("authoritative_facts") if isinstance(shared.get("authoritative_facts"), dict) else {}
    location_card = facts.get("location_card") if isinstance(facts.get("location_card"), dict) else {}
    if str(current.get("message_type") or "").strip().lower() != "location":
        return []
    address = str(location_card.get("address") or location_card.get("location_address") or "").strip()
    title = str(location_card.get("title") or location_card.get("location_title") or "").strip()
    coordinates = str(location_card.get("coordinates") or location_card.get("location") or "").strip()
    query = " ".join(dict.fromkeys(item for item in (address, title) if item)).strip()
    if not query:
        query = coordinates
    if not query:
        return []
    return [
        {
            "name": "resolve_customer_store",
            "destination_hint": query,
            "purpose": "protocol_location_card_resolution",
            "evidence_refs": ["current_message"],
        }
    ]


def _protocol_tool_plan_violations(
    state: AgentState,
    tool_calls: list[dict[str, Any]],
) -> list[str]:
    required_names = {
        str(item.get("name") or "").strip()
        for item in _protocol_required_read_only_tools(state)
        if str(item.get("name") or "").strip()
    }
    planned_names = {
        str(item.get("name") or "").strip()
        for item in tool_calls
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    }
    return [
        f"protocol_required_tool_missing:{name}"
        for name in sorted(required_names - planned_names)
    ]


def _tool_plan_decision_violations(
    payload: dict[str, Any],
    tool_calls: list[dict[str, Any]],
) -> list[str]:
    """Validate an explicit tool decision without deciding its business meaning."""

    decision = str(payload.get("decision") or "").strip()
    planned_tool_calls = _dict_list(payload.get("tool_calls"))
    if decision not in {"use_tools", "facts_sufficient"}:
        return ["tool_plan_decision_missing_or_invalid"]
    if decision == "use_tools" and not planned_tool_calls:
        return ["tool_plan_decision_requires_tool_calls"]
    if decision == "facts_sufficient" and (planned_tool_calls or tool_calls):
        return ["tool_plan_facts_sufficient_with_tool_calls"]
    if decision == "facts_sufficient" and not _string_list(payload.get("evidence_refs")):
        return ["tool_plan_facts_sufficient_missing_evidence_refs"]
    return []


def _merge_tool_calls(
    planned: list[dict[str, Any]],
    required: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    required_by_name = {
        str(item.get("name") or "").strip(): item
        for item in required
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    }
    output: list[dict[str, Any]] = []
    present: set[str] = set()
    for raw in planned:
        if not isinstance(raw, dict):
            continue
        item = copy.deepcopy(raw)
        name = str(item.get("name") or "").strip()
        if name in required_by_name:
            # Protocol facts such as a location card's address and coordinates
            # are authoritative input data. They may replace model-generated
            # arguments for the same read-only tool without choosing a sales
            # action or interpreting customer intent.
            item.update(copy.deepcopy(required_by_name[name]))
        output.append(item)
        if name:
            present.add(name)
    output.extend(
        copy.deepcopy(item)
        for name, item in required_by_name.items()
        if name not in present
    )
    return output


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
        content_indexes.get("available_sop")
        if isinstance(content_indexes.get("available_sop"), dict)
        else {}
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
    sent_messages = (
        facts.get("sent_messages") if isinstance(facts.get("sent_messages"), dict) else {}
    )
    store_delivery = (
        sent_messages.get("store_address_delivery")
        if isinstance(sent_messages.get("store_address_delivery"), dict)
        else {}
    )
    store_delivery_request_id = str(store_delivery.get("request_id") or "").strip()
    store_delivery_ids = [
        str(item).strip()
        for item in store_delivery.get("latest_batch_store_ids") or []
        if str(item).strip()
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
        item["ref"]
        for item in structured_delivered_assets
        if item.get("asset_role") == "activity_offer"
    ]
    structured_supporting_refs = [
        item["ref"]
        for item in structured_delivered_assets
        if item.get("asset_role") in {
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
    prior_message_and_delivery_refs = list(
        dict.fromkeys([*valid_message_refs, *structured_delivery_refs])
    )
    allowed_selected_content_ids = [
        str(item.get("content_id") or item.get("id") or "").strip()
        for item in joined.get("content_candidates") or []
        if isinstance(item, dict)
        and str(item.get("content_id") or item.get("id") or "").strip()
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
        if isinstance(item, dict)
        and (source_id := str(item.get("source_id") or "").strip())
    ]
    tool_facts = joined.get("tool_facts") if isinstance(joined.get("tool_facts"), dict) else {}
    tool_fact_reference_options = [
        {
            "ref": f"tool_fact:{tool_name}",
            "tool_name": tool_name,
        }
        for raw_tool_name, tool_fact in tool_facts.items()
        if (tool_name := str(raw_tool_name or "").strip())
        and isinstance(tool_fact, dict)
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
    reply_evidence = copy.deepcopy(joined)
    reply_shared = (
        reply_evidence.get("shared_context")
        if isinstance(reply_evidence.get("shared_context"), dict)
        else {}
    )
    reply_authoritative = (
        reply_shared.get("authoritative_facts")
        if isinstance(reply_shared.get("authoritative_facts"), dict)
        else {}
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
        "tool_fact_reference_options": tool_fact_reference_options,
        "authoritative_fact_reference_options": authoritative_fact_reference_options,
        "registration_fact_status": registration_fact_status,
        "store_fact_status": store_fact_status,
        "current_turn_structural_constraints": _current_turn_structural_constraints(
            store_fact_status=store_fact_status,
            structured_delivery_options=structured_delivery_options,
        ),
        "evidence": reply_evidence,
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
        # Neutral provenance identifiers for this turn's actual tool outputs.
        # They do not describe what the facts mean or whether Reply should use them.
        "valid_commit_evidence": valid_commit_evidence,
        "output_contract": {
            "reply_messages": "required customer-visible message list",
            "used_fact_refs": "fact references actually used",
            "selected_content_ids": "Gate candidates actually adopted",
            "content_decisions": "model-owned adopt/skip audit for direct Gate candidates; never controls delivery",
            "action": "none | ask | offer | payment | registration",
            "action_reason": "brief model reasoning for audit; never customer-visible",
            "sales_judgment": (
                "model-owned current-turn judgment: customer_goal, primary_objective, explicit friction observation, "
                "posture and reason; only the last two observations may be replayed as low-authority evidence"
            ),
            "payment_assessment": "ephemeral Reply-owned payment context with evidence refs; never persisted",
            "deposit_evidence": "required evidence references only when a payment card is sent",
            "safety_assessment": "model conclusion using exact refs from valid_customer_message_refs",
            "party_size_assessment": "model conclusion using exact refs from valid_customer_message_refs",
            "commit_actions": "optional validated deferred writes; never execute before reply validation",
        },
    }


def _current_turn_structural_constraints(
    *,
    store_fact_status: dict[str, Any],
    structured_delivery_options: dict[str, Any],
) -> list[dict[str, str]]:
    """Expose executor-owned delivery boundaries without choosing sales behavior."""

    constraints: list[dict[str, str]] = []
    status = str(store_fact_status.get("status") or "").strip()
    if status in {"need_location", "need_location_confirmation", "ambiguous_location"}:
        constraints.append(
            {
                "code": "store_location_clarification_required",
                "instruction": (
                    "本轮只能说明工具已经确认的范围，并询问一个会改变查询结果的必要位置；"
                    "不得发送 store_address，不得编造系统更新、同步或维护原因，也不得推导活动未覆盖。"
                ),
            }
        )
    elif status == "no_valid_candidate" and not bool(
        store_fact_status.get("candidate_search_complete")
    ):
        constraints.append(
            {
                "code": "store_scope_incomplete",
                "instruction": (
                    "本轮门店查询范围不完整；不得断言当地没有门店或活动，"
                    "不得编造系统更新、同步或维护原因。"
                ),
            }
        )
    elif status in {"send_single", "send_multiple"}:
        store_delivery = (
            structured_delivery_options.get("store_address")
            if isinstance(structured_delivery_options.get("store_address"), dict)
            else {}
        )
        available_ids = [
            str(item).strip()
            for item in store_delivery.get("available_store_ids") or []
            if str(item).strip()
        ]
        constraints.append(
            {
                "code": "store_delivery_available",
                "instruction": (
                    "本轮门店工具已经给出可交付结果；回答当前门店问题时应实际交付 "
                    f"structured_delivery_options 中 store_id 属于 {available_ids} 的 store_address，"
                    "不能只列名称或再次索要已经足够的位置。"
                ),
            }
        )
    return constraints


def _shared_context(
    state: AgentState,
    *,
    content_catalog: dict[str, Any],
    sop_progress: dict[str, Any],
) -> dict[str, Any]:
    request_context = state.get("request_context") if isinstance(state.get("request_context"), dict) else {}
    store_knowledge = state.get("customer_store_knowledge") if isinstance(state.get("customer_store_knowledge"), dict) else {}
    location_hints = [
        state.get("normalized_content"),
        (state.get("location_card") or {}).get("title") if isinstance(state.get("location_card"), dict) else "",
        (state.get("location_card") or {}).get("address") if isinstance(state.get("location_card"), dict) else "",
    ]
    conversation = _conversation(state)
    current_message = {
        "content": str(state.get("normalized_content") or state.get("content") or ""),
        "raw_content": str(state.get("content") or ""),
        "message_type": _message_type(state),
        "msgid": str(request_context.get("msgid") or ""),
        "sent_at": request_context.get("msgtime") or request_context.get("created_at"),
    }
    authoritative_facts = {
        "orders_and_payment": _authoritative_order_payment_facts(state),
        # This is a permission/coverage summary, not a current-turn lookup
        # result. Store names, IDs and addresses stay out of model context so
        # neither Tool Planner nor Reply can mistake the visibility inventory
        # for a resolved customer location. The executor and validators keep
        # using customer_store_knowledge directly for permission checks.
        "visible_store_scope": _store_scope_for_models(
            build_store_scope_summary(store_knowledge, location_hints=location_hints)
        ),
        "sop_progress": copy.deepcopy(sop_progress),
        "sent_messages": sent_message_summary_for_model(state),
        "image_or_transfer_fact": copy.deepcopy(state.get("image_info") or {}),
        "location_card": copy.deepcopy(state.get("location_card") or {}),
        "request_store_facts": {
            "confirmed_store_id": state.get("confirmed_store_id"),
            "confirmed_store_name": state.get("confirmed_store_name"),
            "store_id": state.get("store_id"),
            "store_name": state.get("store_name"),
        },
        "registration_facts": _authoritative_registration_facts(state),
        "fact_source_status": copy.deepcopy(state.get("background_fact_views") or {}),
    }
    return {
        "schema_version": "shared_context_v2",
        "current_time": {
            "iso": datetime.now(BEIJING_TZ).isoformat(),
            "timezone": "Asia/Shanghai",
        },
        "current_message": current_message,
        "derived_observations": build_v2_derived_observations(
            conversation=conversation,
            history_events=list(state.get("history_events") or []),
            current_message=current_message,
            interface_version=str(request_context.get("interface_version") or "v2"),
        ),
        "conversation": conversation,
        "customer_scope": copy.deepcopy(state.get("customer_scope") or {}),
        "authoritative_facts": authoritative_facts,
        "content_indexes": {
            "available_sop": _content_index_with_delivery_status(content_catalog, sop_progress),
        },
        "sales_guidance": {
            "source": "v2_distilled_objection_playbook",
            "principles": copy.deepcopy(content_catalog.get("sales_principles") or []),
            "raw_source_replies_included": False,
        },
        "rules": parallel_reply_business_rules_for_model(),
        "fact_priority": [
            "current_customer_message",
            "current_turn_tool_facts",
            "recent_real_conversation",
            "structured_delivery_records",
            "non_authoritative_background",
        ],
        "excluded_semantic_fields": [
            "signup_state",
            "next_slot",
            "deposit_ready_candidate",
            "customer_profile.next_sales_strategy",
            "customer_type",
            "main_blocker",
            "conversion_stage",
            "automatic_store_confirmation",
        ],
    }


def _tool_planner_shared_context(state: AgentState) -> dict[str, Any]:
    """Remove sales/content guidance from the read-only tool planning boundary."""

    shared = copy.deepcopy(state.get("shared_context") or {})
    shared.pop("content_indexes", None)
    shared.pop("sales_guidance", None)
    shared.pop("derived_observations", None)
    facts = (
        shared.get("authoritative_facts")
        if isinstance(shared.get("authoritative_facts"), dict)
        else {}
    )
    facts.pop("raw_visible_store_records", None)
    rules = shared.get("rules") if isinstance(shared.get("rules"), dict) else {}
    shared["rules"] = {
        key: copy.deepcopy(rules.get(key))
        for key in ("MUST FOLLOW", "AUTHORITATIVE FACTS", "TOOL FACT BOUNDARIES")
        if rules.get(key) not in (None, "", [], {})
    }
    return shared


def _store_scope_for_models(value: dict[str, Any]) -> dict[str, Any]:
    """Keep coverage evidence while withholding store answers from models."""

    if not isinstance(value, dict):
        return {}
    output = {
        key: copy.deepcopy(value.get(key))
        for key in (
            "source",
            "store_count",
            "snapshot_generated_at",
            "store_scope_error",
            "cache",
            "missing_snapshot_store_ids",
            "province_counts",
            "city_counts",
            "district_counts",
        )
        if value.get(key) not in (None, "", [], {})
    }
    regions: list[dict[str, Any]] = []
    for raw in value.get("relevant_regions") or []:
        if not isinstance(raw, dict):
            continue
        region = {
            key: copy.deepcopy(raw.get(key))
            for key in (
                "province",
                "city",
                "store_count",
                "district_counts",
                "requested_areas",
                "exact_area_store_count",
            )
            if raw.get(key) not in (None, "", [], {})
        }
        if region:
            regions.append(region)
    if regions:
        output["relevant_regions"] = regions
    return output


def _content_index_with_delivery_status(
    content_catalog: dict[str, Any],
    sop_progress: dict[str, Any],
) -> dict[str, Any]:
    """Expose asset metadata and delivery state without leaking configured bodies."""

    completed_ids = {
        str(item).strip()
        for item in sop_progress.get("completed_pack_ids") or []
        if str(item).strip()
    }
    items: list[dict[str, Any]] = []
    for raw in content_catalog.get("sop_packs") or []:
        if not isinstance(raw, dict):
            continue
        content_id = str(raw.get("content_id") or "").strip()
        if not content_id:
            continue
        items.append(
            {
                "content_id": content_id,
                "content_type": str(raw.get("content_type") or "sop"),
                "name": str(raw.get("name") or ""),
                "purpose": str(raw.get("purpose") or ""),
                "asset_role": str(raw.get("asset_role") or "supporting_content"),
                "requires_prior_asset_roles": [
                    str(item).strip()
                    for item in raw.get("requires_prior_asset_roles") or []
                    if str(item).strip()
                ],
                "selection_constraints": copy.deepcopy(raw.get("selection_constraints") or {}),
                "category": str(raw.get("category") or ""),
                "delivery_status": "completed" if content_id in completed_ids else "available",
            }
        )
    return {
        "schema_version": "content_asset_index_v2",
        "sop_packs": items,
    }


def _structured_delivery_options(joined: dict[str, Any], *, state: AgentState) -> dict[str, Any]:
    """Surface current-turn structured options without deciding to use them."""

    options: dict[str, Any] = {}
    tool_facts = joined.get("tool_facts") if isinstance(joined.get("tool_facts"), dict) else {}
    normalized_tool_facts = (
        joined.get("normalized_tool_facts")
        if isinstance(joined.get("normalized_tool_facts"), dict)
        else {}
    )
    pending: list[Any] = [tool_facts, normalized_tool_facts]
    resolutions: list[dict[str, Any]] = []
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            resolution = value.get("store_resolution_fact")
            if isinstance(resolution, dict):
                resolutions.append(resolution)
            pending.extend(item for item in value.values() if isinstance(item, (dict, list)))
        elif isinstance(value, list):
            pending.extend(item for item in value if isinstance(item, (dict, list)))
    if resolutions:
        resolution = next(
            (
                item
                for item in resolutions
                if item.get("delivery_store_ids") or item.get("visible_candidate_ids")
            ),
            resolutions[0],
        )
        store_ids = [
            str(item).strip()
            for item in resolution.get("delivery_store_ids") or []
            if str(item).strip()
        ]
        # A lookup result can be useful without containing a deliverable card. For
        # example, an ambiguous place needs one clarification and a city with many
        # candidates needs narrower location evidence. Keep those results in the
        # tool fact reference catalog, but do not advertise an empty structured
        # delivery contract that the Reply could only satisfy incorrectly.
        if store_ids:
            options["store_address"] = {
                "fact_ref": "tool_fact:customer_store_lookup",
                "status": str(resolution.get("status") or ""),
                "available_store_ids": list(dict.fromkeys(store_ids)),
                "message_payloads": [
                    {"type": "store_address", "content": {"store_id": store_id}}
                    for store_id in dict.fromkeys(store_ids)
                ],
                "candidate_search_complete": resolution.get("candidate_search_complete"),
                "ranking_method": str(resolution.get("ranking_method") or ""),
                "source": "current_turn_tool_fact",
            }
    if _payment_collection_delivery_available(state):
        options["payment_collection"] = {
            "fact_ref": "authoritative_fact:payment_collection_option",
            "status": "available",
            "message_payloads": [
                {
                    "type": "payment_collection",
                    "content": payment_collection_content({}, state=state),
                }
            ],
            "source": "system_payment_collection_contract",
            "constraints": [
                "reply_must_choose_action_payment",
                "reply_must_provide_deposit_evidence",
                "same_turn_max_one_payment_collection",
            ],
        }
    return options


def _payment_collection_delivery_available(state: AgentState) -> bool:
    """Expose the payment card as material only after structural quote evidence.

    This does not decide that the customer wants to pay. It only tells Reply
    that a real card can be delivered if its own sales judgment and deposit
    evidence satisfy the payment contract.
    """

    joined = state.get("evidence_join") if isinstance(state.get("evidence_join"), dict) else {}
    shared = joined.get("shared_context") if isinstance(joined.get("shared_context"), dict) else {}
    authoritative = (
        shared.get("authoritative_facts")
        if isinstance(shared.get("authoritative_facts"), dict)
        else {}
    )
    sop_progress = (
        authoritative.get("sop_progress")
        if isinstance(authoritative.get("sop_progress"), dict)
        else {}
    )
    if not _v2_activity_offer_delivered(
        sop_progress=sop_progress,
        sent_messages=(
            authoritative.get("sent_messages")
            if isinstance(authoritative.get("sent_messages"), dict)
            else {}
        ),
        history_events=state.get("history_events") or [],
    ):
        return False
    if is_paid_deposit_state(state.get("payment_state")):
        return False
    context = state.get("customer_context") if isinstance(state.get("customer_context"), dict) else {}
    existing_state, existing_source, existing_fact = _authoritative_existing_payment(state)
    resolved = resolved_payment_fact(
        orders=context.get("orders") or [],
        image_info=state.get("image_info"),
        existing_state=existing_state,
        existing_source=existing_source,
        existing_fact=existing_fact,
    )
    if is_paid_deposit_state(resolved.get("deposit_state")):
        return False
    return True


def _v2_activity_offer_delivered(
    *,
    sop_progress: dict[str, Any],
    sent_messages: dict[str, Any],
    history_events: list[Any],
) -> bool:
    """Use append-only delivery facts, never visible-text interpretation.

    This is an execution prerequisite for exposing the payment card as an
    available structure. It does not decide whether Reply should use the card.
    """

    completed_ids = {
        str(item).strip()
        for item in sop_progress.get("completed_pack_ids") or []
        if str(item).strip()
    }
    completed_categories = {
        str(item).strip()
        for item in sop_progress.get("completed_categories") or []
        if str(item).strip()
    }
    if "s10_activity_intro" in completed_ids or completed_categories.intersection(
        {"s10_activity_intro", "activity_intro", "price_quote"}
    ):
        return True
    try:
        payment_collection_count = int(sent_messages.get("payment_collection_count") or 0)
    except (TypeError, ValueError):
        payment_collection_count = 0
    if (
        sent_messages.get("activity_intro_image_sent")
        or sent_messages.get("payment_collection_sent")
        or payment_collection_count > 0
    ):
        return True
    for raw_event in history_events:
        if not isinstance(raw_event, dict):
            continue
        event_type = str(raw_event.get("event_type") or "").strip().lower()
        pack_id = str(
            raw_event.get("pack_id")
            or raw_event.get("sop_pack_id")
            or raw_event.get("send_once_key")
            or ""
        ).strip().lower()
        category = str(
            raw_event.get("sop_category") or raw_event.get("category") or ""
        ).strip().lower()
        if event_type in {
            "activity_intro_image_sent",
            "offer_explained",
            "payment_collection_sent",
        }:
            return True
        if pack_id == "s10_activity_intro" or category in {
            "activity_intro",
            "s10_activity_intro",
            "price_quote",
        }:
            return True
    return False


def _store_fact_status(joined: dict[str, Any]) -> dict[str, Any]:
    """Copy compact location facts without selecting a clarification or store."""

    normalized = (
        joined.get("normalized_tool_facts")
        if isinstance(joined.get("normalized_tool_facts"), dict)
        else {}
    )
    structured = (
        normalized.get("structured_facts")
        if isinstance(normalized.get("structured_facts"), dict)
        else {}
    )
    lookup = (
        structured.get("store_lookup_status")
        if isinstance(structured.get("store_lookup_status"), dict)
        else {}
    )
    resolution = (
        structured.get("store_resolution_fact")
        if isinstance(structured.get("store_resolution_fact"), dict)
        else {}
    )
    location = (
        resolution.get("location_evidence")
        if isinstance(resolution.get("location_evidence"), dict)
        else {}
    )
    store_candidate_regions: list[dict[str, str]] = []
    seen_regions: set[tuple[str, str, str]] = set()
    for item in structured.get("store_facts") or []:
        if not isinstance(item, dict):
            continue
        region = (
            str(item.get("province") or "").strip(),
            str(item.get("city") or "").strip(),
            str(item.get("district") or "").strip(),
        )
        if not any(region) or region in seen_regions:
            continue
        seen_regions.add(region)
        store_candidate_regions.append(
            {"province": region[0], "city": region[1], "district": region[2]}
        )
    return {
        "status": str(resolution.get("status") or lookup.get("status") or ""),
        "raw_place": str(
            resolution.get("raw_place")
            or lookup.get("raw_query")
            or location.get("raw_text")
            or ""
        ),
        "missing_facts": copy.deepcopy(normalized.get("missing_facts") or []),
        "resolved_admin": {
            "province": str(location.get("province") or lookup.get("province") or ""),
            "city": str(location.get("city") or lookup.get("city") or ""),
            "district": str(location.get("district") or lookup.get("district") or ""),
        },
        "candidate_regions": copy.deepcopy(location.get("geocode_candidate_regions") or []),
        "store_candidate_regions": store_candidate_regions,
        "candidate_store_count": int(resolution.get("visible_candidate_count") or 0),
        "delivery_store_ids": copy.deepcopy(resolution.get("delivery_store_ids") or []),
        "ranking_method": str(resolution.get("ranking_method") or ""),
        "source": "normalized_tool_facts",
    }


def _conversation(state: AgentState) -> list[dict[str, Any]]:
    """Return complete prior history; the current turn lives in current_message."""

    turns = state.get("conversation_turns") if isinstance(state.get("conversation_turns"), list) else []
    if turns:
        output = []
        for index, item in enumerate(turns, start=1):
            if not isinstance(item, dict):
                continue
            turn = copy.deepcopy(item)
            turn.setdefault("message_ref", f"history_{index}")
            output.append(turn)
    else:
        output = []
        for index, raw in enumerate(state.get("conversation_history") or [], start=1):
            text = str(raw or "").strip()
            role = "unknown"
            for prefix, value in (("用户:", "customer"), ("客户:", "customer"), ("小贝:", "assistant"), ("AI:", "assistant"), ("员工:", "assistant")):
                if text.startswith(prefix):
                    role = value
                    text = text[len(prefix) :].strip()
                    break
            output.append({"message_ref": f"history_{index}", "role": role, "content": text})
    current = str(state.get("normalized_content") or state.get("content") or "").strip()
    if current and _conversation_ends_with(output, current):
        last_role = str(output[-1].get("role") or "").strip().lower()
        if last_role in {"customer", "user"}:
            output.pop()
    return output


def _authoritative_order_payment_facts(state: AgentState) -> dict[str, Any]:
    """Expose platform facts without old-memory ordering or sales-state labels."""

    context = state.get("customer_context") if isinstance(state.get("customer_context"), dict) else {}
    orders: list[dict[str, Any]] = []
    for raw in context.get("orders") or []:
        if not isinstance(raw, dict):
            continue
        order = copy.deepcopy(raw)
        order.pop("is_current_order", None)
        orders.append(order)
    existing_state, existing_source, existing_fact = _authoritative_existing_payment(state)
    output = {
        "source": str(context.get("source") or ""),
        "customer_id": context.get("customer_id"),
        "platform_customer_id": context.get("platform_customer_id"),
        "customer_add_wechat_id": context.get("customer_add_wechat_id"),
        "customer": copy.deepcopy(context.get("customer") or {}),
        "orders": orders,
        "orders_error": str(context.get("orders_error") or ""),
        "customer_info_error": str(context.get("customer_info_error") or ""),
        "resolved_payment": resolved_payment_fact(
            orders=orders,
            image_info=state.get("image_info"),
            existing_state=existing_state,
            existing_source=existing_source,
            existing_fact=existing_fact,
        ),
    }
    if str(context.get("source") or "") == "platform_agent":
        output["appointment"] = copy.deepcopy(context.get("appointment") or {})
    return {key: value for key, value in output.items() if value not in (None, "", [], {})}


def _authoritative_existing_payment(state: AgentState) -> tuple[str, str, Any]:
    source = str(state.get("payment_source") or "").strip()
    payment_state = str(state.get("payment_state") or "").strip()
    if source in {"vision.payment_proof", "platform.unknown_message_transfer"}:
        return payment_state, source, state.get("payment_fact")
    if payment_state == "paid_by_platform_transfer_event":
        return payment_state, "platform.unknown_message_transfer", state.get("payment_fact")
    basic = state.get("customer_basic_info") if isinstance(state.get("customer_basic_info"), dict) else {}
    stored = basic.get("deposit_state") if isinstance(basic.get("deposit_state"), dict) else {}
    stored_state = str(stored.get("status") or stored.get("deposit_state") or "").strip()
    stored_source = str(stored.get("source") or "").strip()
    if (
        stored_state in {"paid_by_platform_transfer_event", "paid_by_screenshot"}
        and stored_source in {"platform.unknown_message_transfer", "vision.payment_proof"}
    ):
        return stored_state, stored_source, stored
    return "", "", None


def _commit_action_violations(
    name: str,
    arguments: dict[str, Any],
    state: AgentState,
    *,
    evidence_refs: list[str] | None = None,
) -> list[str]:
    """Validate deferred-write provenance without deciding sales semantics."""

    refs = [str(item).strip() for item in evidence_refs or [] if str(item).strip()]
    violations: list[str] = []
    scope = _commit_customer_scope(state)
    if not scope.get("persistence_allowed") or not scope.get("wechat"):
        violations.append(f"commit_action_requires_current_sales_contact_scope:{name}")
    if not state.get("memory_persist_allowed", True):
        violations.append(f"commit_action_persistence_disabled:{name}")

    valid_evidence = {
        str(item.get("ref") or "").strip(): item
        for item in _valid_commit_evidence(state)
        if isinstance(item, dict) and str(item.get("ref") or "").strip()
    }
    if not refs:
        violations.append(f"commit_action_requires_evidence_refs:{name}")
    else:
        unknown_refs = sorted({ref for ref in refs if ref not in valid_evidence})
        if unknown_refs:
            violations.append(
                f"commit_action_unknown_evidence_refs:{name}:{','.join(unknown_refs)}"
            )

    if not _parallel_payment_is_paid(state):
        violations.append(f"commit_action_requires_paid_deposit:{name}")
    elif "payment_fact:authoritative_paid" not in refs:
        violations.append(f"commit_action_requires_paid_evidence_ref:{name}")

    mobile = re.sub(r"\D", "", str(arguments.get("mobile") or ""))
    if name == "add_customer_mobile":
        if len(mobile) != 11:
            violations.append("commit_action_invalid_mobile:add_customer_mobile")
        elif not _commit_value_has_evidence(
            mobile,
            refs,
            valid_evidence,
            kinds={"customer_message", "registration_mobile"},
            digits_only=True,
        ):
            violations.append("commit_action_mobile_missing_source:add_customer_mobile")
        return violations
    if name == "create_work_order":
        customer_name = str(arguments.get("customer_name") or "").strip()
        store_id = str(arguments.get("store_id") or "").strip()
        if not customer_name:
            violations.append("commit_action_missing_customer_name:create_work_order")
        elif not _commit_value_has_evidence(
            customer_name,
            refs,
            valid_evidence,
            kinds={"customer_message", "registration_name"},
        ):
            violations.append("commit_action_customer_name_missing_source:create_work_order")
        if len(mobile) != 11:
            violations.append("commit_action_invalid_mobile:create_work_order")
        elif not _commit_value_has_evidence(
            mobile,
            refs,
            valid_evidence,
            kinds={"customer_message", "registration_mobile"},
            digits_only=True,
        ):
            violations.append("commit_action_mobile_missing_source:create_work_order")
        if not store_id:
            violations.append("commit_action_missing_store_id:create_work_order")
        else:
            if store_id not in _visible_store_ids(state):
                violations.append("commit_action_store_not_customer_visible:create_work_order")
            store_ref = next(
                (
                    ref
                    for ref in refs
                    if valid_evidence.get(ref, {}).get("kind") == "store_anchor"
                    and str(valid_evidence.get(ref, {}).get("value") or "") == store_id
                ),
                "",
            )
            if not store_ref:
                violations.append("commit_action_store_missing_anchor:create_work_order")
        return violations
    return violations


def _authoritative_registration_facts(state: AgentState) -> dict[str, Any]:
    """Expose only structured registration values and their source."""

    context = state.get("customer_context") if isinstance(state.get("customer_context"), dict) else {}
    customer = context.get("customer") if isinstance(context.get("customer"), dict) else {}
    basic = state.get("customer_basic_info") if isinstance(state.get("customer_basic_info"), dict) else {}
    name = str(
        customer.get("customer_name")
        or basic.get("customer_name")
        or ""
    ).strip()
    mobile = re.sub(
        r"\D",
        "",
        str(
            customer.get("mobile")
            or customer.get("phone")
            or basic.get("phone")
            or ""
        ),
    )
    output: dict[str, Any] = {"source": "structured_registration"}
    if name:
        output["customer_name"] = name
    if len(mobile) == 11:
        output["mobile"] = mobile
    return output if len(output) > 1 else {}


def _registration_fact_status(
    state: AgentState,
    shared_context: dict[str, Any],
) -> dict[str, Any]:
    """Summarize authoritative field presence without choosing a reply action."""

    facts = (
        shared_context.get("authoritative_facts")
        if isinstance(shared_context.get("authoritative_facts"), dict)
        else {}
    )
    registration = (
        facts.get("registration_facts")
        if isinstance(facts.get("registration_facts"), dict)
        else {}
    )
    collected_fields: list[str] = []
    if str(registration.get("customer_name") or "").strip():
        collected_fields.append("customer_name")
    mobile = re.sub(r"\D", "", str(registration.get("mobile") or ""))
    if len(mobile) == 11:
        collected_fields.append("customer_mobile")
    expected_fields = ["customer_name", "customer_mobile"]
    return {
        "authoritative_paid": _parallel_payment_is_paid(
            {**state, "shared_context": shared_context}
        ),
        "collected_fields": collected_fields,
        "missing_fields": [field for field in expected_fields if field not in collected_fields],
        "source": "authoritative_facts.registration_facts",
    }


def _valid_commit_evidence(
    state: AgentState,
    shared_context: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    shared = shared_context if isinstance(shared_context, dict) else (
        state.get("shared_context") if isinstance(state.get("shared_context"), dict) else {}
    )
    facts = shared.get("authoritative_facts") if isinstance(shared.get("authoritative_facts"), dict) else {}
    output: list[dict[str, str]] = []

    current = shared.get("current_message") if isinstance(shared.get("current_message"), dict) else {}
    current_text = str(current.get("content") or current.get("raw_content") or "").strip()
    if current_text:
        output.append({"ref": "current_message", "kind": "customer_message", "value": current_text})
    for item in shared.get("conversation") or []:
        if not isinstance(item, dict) or str(item.get("role") or "").strip().lower() not in {"customer", "user"}:
            continue
        ref = str(item.get("message_ref") or "").strip()
        value = str(item.get("content") or "").strip()
        if ref and value:
            output.append({"ref": ref, "kind": "customer_message", "value": value})

    if _parallel_payment_is_paid({**state, "shared_context": shared}):
        output.append(
            {
                "ref": "payment_fact:authoritative_paid",
                "kind": "paid_deposit",
                "value": "paid",
            }
        )

    registration = facts.get("registration_facts") if isinstance(facts.get("registration_facts"), dict) else {}
    name = str(registration.get("customer_name") or "").strip()
    mobile = re.sub(r"\D", "", str(registration.get("mobile") or ""))
    if name:
        output.append({"ref": "registration_fact:customer_name", "kind": "registration_name", "value": name})
    if len(mobile) == 11:
        output.append({"ref": "registration_fact:customer_mobile", "kind": "registration_mobile", "value": mobile})

    request_store = facts.get("request_store_facts") if isinstance(facts.get("request_store_facts"), dict) else {}
    for field in ("confirmed_store_id", "store_id"):
        store_id = str(request_store.get(field) or "").strip()
        if store_id:
            output.append({"ref": f"request_store:{store_id}", "kind": "store_anchor", "value": store_id})

    sent = facts.get("sent_messages") if isinstance(facts.get("sent_messages"), dict) else {}
    anchor = sent.get("store_anchor_fact") if isinstance(sent.get("store_anchor_fact"), dict) else {}
    if str(anchor.get("status") or "") == "eligible":
        store_id = str(anchor.get("store_id") or "").strip()
        if store_id:
            output.append({"ref": f"sent_store_anchor:{store_id}", "kind": "store_anchor", "value": store_id})

    for store_id in sorted(_tool_store_anchor_ids(state)):
        output.append({"ref": f"tool_store:{store_id}", "kind": "store_anchor", "value": store_id})

    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in output:
        ref = item["ref"]
        if ref in seen:
            continue
        seen.add(ref)
        deduped.append(item)
    return deduped


def _commit_value_has_evidence(
    value: str,
    refs: list[str],
    valid_evidence: dict[str, dict[str, str]],
    *,
    kinds: set[str],
    digits_only: bool = False,
) -> bool:
    expected = re.sub(r"\D", "", value) if digits_only else str(value).strip()
    if not expected:
        return False
    for ref in refs:
        evidence = valid_evidence.get(ref) or {}
        if evidence.get("kind") not in kinds:
            continue
        actual = str(evidence.get("value") or "")
        actual = re.sub(r"\D", "", actual) if digits_only else actual
        if expected in actual:
            return True
    return False


def _visible_store_ids(state: AgentState) -> set[str]:
    store_knowledge = (
        state.get("customer_store_knowledge")
        if isinstance(state.get("customer_store_knowledge"), dict)
        else {}
    )
    stores = (
        store_knowledge.get("stores")
        if isinstance(store_knowledge.get("stores"), list)
        else []
    )
    if stores:
        return {
            str(item.get("store_id") or item.get("id") or "").strip()
            for item in stores
            if isinstance(item, dict)
            and str(item.get("store_id") or item.get("id") or "").strip()
        }
    shared = state.get("shared_context") if isinstance(state.get("shared_context"), dict) else {}
    facts = shared.get("authoritative_facts") if isinstance(shared.get("authoritative_facts"), dict) else {}
    stores = facts.get("raw_visible_store_records") if isinstance(facts.get("raw_visible_store_records"), list) else []
    return {
        str(item.get("store_id") or item.get("id") or "").strip()
        for item in stores
        if isinstance(item, dict) and str(item.get("store_id") or item.get("id") or "").strip()
    }


def _tool_store_anchor_ids(state: AgentState) -> set[str]:
    results = state.get("tool_results") if isinstance(state.get("tool_results"), dict) else {}
    output: set[str] = set()
    pending: list[Any] = [results]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            for key in ("delivery_store_ids", "store_ids"):
                raw_ids = value.get(key)
                if isinstance(raw_ids, list):
                    output.update(str(item).strip() for item in raw_ids if str(item).strip())
            fact = value.get("store_resolution_fact")
            if isinstance(fact, dict):
                pending.append(fact)
            pending.extend(item for item in value.values() if isinstance(item, (dict, list)))
        elif isinstance(value, list):
            pending.extend(item for item in value if isinstance(item, (dict, list)))
    return output


def _commit_customer_scope(state: AgentState) -> dict[str, Any]:
    shared = state.get("shared_context") if isinstance(state.get("shared_context"), dict) else {}
    scope = shared.get("customer_scope") if isinstance(shared.get("customer_scope"), dict) else {}
    request_context = state.get("request_context") if isinstance(state.get("request_context"), dict) else {}
    state_wechat = str(request_context.get("wechat") or state.get("wechat") or "").strip()
    scope_wechat = str(scope.get("wechat") or "").strip()
    if state_wechat and scope_wechat and state_wechat.lower() != scope_wechat.lower():
        return {**scope, "persistence_allowed": False, "scope_conflict": "wechat"}
    return scope


def _parallel_payment_is_paid(state: AgentState) -> bool:
    shared = state.get("shared_context") if isinstance(state.get("shared_context"), dict) else {}
    facts = shared.get("authoritative_facts") if isinstance(shared.get("authoritative_facts"), dict) else {}
    order_payment = facts.get("orders_and_payment") if isinstance(facts.get("orders_and_payment"), dict) else {}
    resolved = order_payment.get("resolved_payment") if isinstance(order_payment.get("resolved_payment"), dict) else {}
    return is_paid_deposit_state(resolved.get("deposit_state"))


def _normalize_read_only_tool_calls(
    raw: Any,
    *,
    valid_evidence_refs: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    calls: list[dict[str, Any]] = []
    violations: list[str] = []
    seen: set[tuple[str, str]] = set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            violations.append("tool_call_not_object")
            continue
        name = str(item.get("name") or "").strip()
        if name not in READ_ONLY_TOOL_NAMES:
            violations.append(f"tool_not_read_only:{name or 'missing'}")
            continue
        arguments = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
        argument_violations = _read_only_tool_argument_violations(name, arguments)
        if argument_violations:
            violations.extend(argument_violations)
            continue
        evidence_refs = [
            _canonical_tool_evidence_ref(ref)
            for ref in _string_list(item.get("evidence_refs"))
        ]
        evidence_refs = [ref for ref in dict.fromkeys(evidence_refs) if ref]
        if valid_evidence_refs is not None:
            invalid_refs = [ref for ref in evidence_refs if ref not in valid_evidence_refs]
            if invalid_refs:
                violations.append(f"tool_call_invalid_evidence_ref:{name}")
                evidence_refs = [ref for ref in evidence_refs if ref in valid_evidence_refs]
            if not evidence_refs:
                violations.append(f"tool_call_missing_evidence_ref:{name}")
                continue
            # A malformed extra ref remains observable, but it must not erase
            # an otherwise supported read-only query.
        normalized = {
            "name": name,
            **copy.deepcopy(arguments),
            "purpose": str(item.get("purpose") or ""),
            "evidence_refs": evidence_refs,
        }
        key = (name, json_dumps(arguments))
        if key in seen:
            continue
        seen.add(key)
        calls.append(normalized)
    return calls, violations


def _read_only_tool_argument_violations(name: str, arguments: dict[str, Any]) -> list[str]:
    """Validate tool contracts without inventing business meaning or argument values."""

    if name == "kb_search":
        missing = [key for key in ("kb_name", "query") if not str(arguments.get(key) or "").strip()]
        return [f"tool_call_missing_argument:{name}:{key}" for key in missing]
    if name == "customer_store_lookup":
        if not any(str(arguments.get(key) or "").strip() for key in ("query", "origin", "address")):
            return [f"tool_call_missing_location_argument:{name}"]
    return []


def _canonical_tool_evidence_ref(value: str) -> str:
    """Normalize field-path spelling without inferring or replacing evidence."""

    ref = str(value or "").strip()
    if ref.startswith("shared_context."):
        ref = ref[len("shared_context.") :]
    if ref in {"current_message.content", "current_message.raw_content"}:
        return "current_message"
    match = re.fullmatch(r"conversation\.([A-Za-z0-9_-]+)(?:\.content)?", ref)
    if match:
        return match.group(1)
    return ref


def _shared_context_evidence_refs(state: AgentState) -> set[str]:
    shared = state.get("shared_context") if isinstance(state.get("shared_context"), dict) else {}
    refs = {"current_message"}
    for item in shared.get("conversation") or []:
        if not isinstance(item, dict):
            continue
        ref = str(item.get("message_ref") or "").strip()
        if ref:
            refs.add(ref)
    return refs


def _request_from_state(state: AgentState) -> ChatRequest:
    return ChatRequest(
        content=str(state.get("content") or ""),
        customer_id=str(state.get("customer_id") or ""),
        corp_id=str(state.get("corp_id") or ""),
        conversation_history=_gate_conversation_history(state),
        file_image=state.get("file_image"),
        user_id=state.get("user_id"),
        wechat=state.get("wechat"),
        external_userid=state.get("external_userid"),
        customer_add_wechat_id=state.get("customer_add_wechat_id"),
        confirmed_store_id=state.get("confirmed_store_id"),
        confirmed_store_name=state.get("confirmed_store_name"),
        store_id=state.get("store_id"),
        store_name=state.get("store_name"),
        appointment_id=state.get("appointment_id"),
        appointment_time=state.get("appointment_time"),
        request_context=dict(state.get("request_context") or {}),
    )


def _gate_conversation_history(state: AgentState) -> list[str]:
    """Give Gate the same full, timestamped conversation that Reply receives."""

    shared = state.get("shared_context") if isinstance(state.get("shared_context"), dict) else {}
    conversation = shared.get("conversation") if isinstance(shared.get("conversation"), list) else []
    output: list[str] = []
    role_labels = {"customer": "用户", "user": "用户", "assistant": "小贝", "staff": "员工"}
    for item in conversation:
        if not isinstance(item, dict) or str(item.get("message_ref") or "") == "current_message":
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        role = role_labels.get(str(item.get("role") or "").lower(), "消息")
        sent_at = str(item.get("sent_at") or item.get("timestamp") or "").strip()
        prefix = f"[{sent_at}] " if sent_at else ""
        output.append(f"{prefix}{role}: {content}")
    if output:
        return output
    return [str(item) for item in state.get("conversation_history") or []]


def _message_type(state: AgentState) -> str:
    context = state.get("request_context") if isinstance(state.get("request_context"), dict) else {}
    return str(context.get("msgtype") or ("image" if state.get("file_image") else "text"))


def _gate_route_advice(result: dict[str, Any]) -> str:
    # Gate and Tool Planner always run in parallel. This field only describes
    # whether Gate produced candidate evidence; it must not inherit a legacy
    # direct-reply route or treat legacy reply_messages as a valid candidate.
    return "content_only" if result.get("candidate_packs") else "tools_only"


def _authority_conflicts(state: AgentState, tool_results: dict[str, Any]) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    background = state.get("background_fact_views") if isinstance(state.get("background_fact_views"), dict) else {}
    for section, fact in background.items():
        if isinstance(fact, dict) and fact.get("missing"):
            conflicts.append({"section": section, "type": "source_incomplete", "detail": fact.get("missing")})
    for tool_name, result in tool_results.items():
        if isinstance(result, dict) and result.get("error"):
            conflicts.append({"section": tool_name, "type": "tool_error", "detail": result.get("error")})
    return conflicts


def _conversation_ends_with(messages: list[dict[str, Any]], content: str) -> bool:
    if not messages:
        return False
    return str(messages[-1].get("content") or "").strip() == content


def _branch_trace_output(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": result.get("status"),
        "duration_ms": result.get("duration_ms"),
        "error": result.get("error"),
    }


def _completed_parallel_branch(
    result: dict[str, Any] | BaseException,
    *,
    schema_version: str,
    fallback: dict[str, Any],
) -> dict[str, Any]:
    """Turn one failed sibling into explicit missing evidence without cancelling the other."""

    if isinstance(result, dict):
        return result
    output = copy.deepcopy(fallback)
    output.update(
        {
            "schema_version": schema_version,
            "status": "error",
            "error": f"{type(result).__name__}: {result}",
            "duration_ms": 0,
        }
    )
    return output


def _dict_list(value: Any) -> list[dict[str, Any]]:
    return [copy.deepcopy(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _string_list(value: Any) -> list[str]:
    return [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else []


def _drop_keys(value: dict[str, Any], keys: set[str]) -> dict[str, Any]:
    return {key: copy.deepcopy(item) for key, item in value.items() if key not in keys}
