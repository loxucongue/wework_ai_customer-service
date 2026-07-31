from __future__ import annotations

import asyncio
import json
import math
import os
import re
from pathlib import Path
from typing import Any, Callable

from app.graph.nodes.action_module_outputs import build_planner_fact_output
from app.graph.nodes.action_task_results import ActionToolTask, merge_action_task_results
from app.graph.nodes.appointment_time_utils import normalize_time_text
from app.graph.nodes.sent_message_summary import latest_single_store_card_anchor_id
from app.graph.planner.runtime_plan import (
    planner_primary_task,
    planner_required_tools,
    planner_secondary_tasks,
)
from app.graph.state import AgentState
from app.policies.constants import KNOWN_STORE_FACTS
from app.services.coze_client import CozeClient
from app.services.platform_agent_client import PlatformAgentClient
from app.services.customer_payment_state import is_paid_deposit_state, normalize_prepay_facts
from app.services.customer_order_context import order_status_text
from app.services.store_fact_integrity import (
    filter_valid_store_facts,
    store_fact_is_valid,
)
from app.services.store_service import StoreService
from app.services.trace_logger import TraceLogger


_STORE_SNAPSHOT_CACHE: dict[str, Any] | None = None
_STORE_SNAPSHOT_CACHE_KEY: tuple[str, int] | None = None
_ACTION_TOOL_TIMEOUT_SECONDS = 12.0


def create_execute_actions_node(
    *,
    coze_client: CozeClient,
    trace_logger: TraceLogger,
    store_service: StoreService | None,
    appointment_query_from_state: Callable[[str, dict[str, Any], AgentState], dict[str, Any]],
    platform_agent_client: PlatformAgentClient | None = None,
) -> Callable[[AgentState], Any]:
    async def execute_actions(state: AgentState) -> dict[str, Any]:
        required_tools = planner_required_tools(state)
        with trace_logger.node(
            state,
            "execute_actions",
            {
                "primary_task": planner_primary_task(state),
                "secondary_tasks": planner_secondary_tasks(state),
                "required_tools": required_tools,
            },
        ) as span:
            content = state.get("normalized_content") or ""
            tool_results: dict[str, Any] = {}
            tool_calls: list[dict[str, Any]] = []
            tool_tasks: list[ActionToolTask] = []
            planned_tools = state.get("planner_tool_calls") if isinstance(state.get("planner_tool_calls"), list) else []
            if planned_tools:
                required_tools = [tool for tool in planned_tools if isinstance(tool, dict)]
            required_tools = _dedupe_planned_tools(required_tools)
            required_tools = _filter_invalid_planned_tools(required_tools, state, tool_results, tool_calls)

            for tool in required_tools:
                _queue_planned_tool_tasks(
                    tool=tool,
                    state=state,
                    coze_client=coze_client,
                    tool_results=tool_results,
                    tool_calls=tool_calls,
                    tool_tasks=tool_tasks,
                )

            if _needs_customer_store_lookup(required_tools):
                lookup_tool = _planned_tool(required_tools, "customer_store_lookup")
                try:
                    result = await asyncio.wait_for(
                        _customer_store_lookup(lookup_tool, state, coze_client),
                        timeout=_ACTION_TOOL_TIMEOUT_SECONDS,
                    )
                except Exception as exc:
                    result = _tool_execution_error_result(
                        tool_name="customer_store_lookup",
                        tool=lookup_tool,
                        state=state,
                        exc=exc,
                    )
                tool_results["customer_store_lookup"] = result
                tool_calls.append({"name": "customer_store_lookup", "input": lookup_tool, "output": result})
                if not _needs_distance_calculate(required_tools) and _lookup_result_needs_distance_enrichment(result):
                    distance_tool = {
                        "name": "distance_calculate",
                        "origin": str(result.get("query") or lookup_tool.get("query") or content or "").strip(),
                        "candidate_source": "customer_store_lookup",
                        "purpose": "auto_rank_cross_region_store_candidates",
                        "lookup_scope": _store_lookup_scope_fields(result),
                    }
                    try:
                        result = await asyncio.wait_for(
                            _distance_calculate(distance_tool, state, coze_client, tool_results),
                            timeout=_ACTION_TOOL_TIMEOUT_SECONDS,
                        )
                    except Exception as exc:
                        result = _tool_execution_error_result(
                            tool_name="distance_calculate",
                            tool=distance_tool,
                            state=state,
                            exc=exc,
                        )
                    tool_results["distance_calculate"] = result
                    tool_calls.append({"name": "distance_calculate", "input": distance_tool, "output": result, "auto_enriched": True})

            if _needs_distance_calculate(required_tools):
                distance_tool = _planned_tool(required_tools, "distance_calculate")
                try:
                    result = await asyncio.wait_for(
                        _distance_calculate(distance_tool, state, coze_client, tool_results),
                        timeout=_ACTION_TOOL_TIMEOUT_SECONDS,
                    )
                except Exception as exc:
                    result = _tool_execution_error_result(
                        tool_name="distance_calculate",
                        tool=distance_tool,
                        state=state,
                        exc=exc,
                    )
                tool_results["distance_calculate"] = result
                tool_calls.append({"name": "distance_calculate", "input": distance_tool, "output": result})

            if _needs_appointment_record_query(required_tools):
                appointment = state.get("appointment_cache") if isinstance(state.get("appointment_cache"), dict) else {}
                tool_results["appointment_record_query"] = {"handled_by_cache": True, **appointment}
                tool_calls.append(
                    {
                        "name": "appointment_record_query",
                        "input": {"query": content, "planned": True},
                        "output": tool_results["appointment_record_query"],
                    }
                )

            if platform_agent_client:
                platform_tool_results: dict[str, Any] = dict(tool_results)
                platform_tool_calls: list[dict[str, Any]] = []
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(
                            _execute_platform_order_tools,
                            required_tools=required_tools,
                            state=state,
                            platform_client=platform_agent_client,
                            tool_results=platform_tool_results,
                            tool_calls=platform_tool_calls,
                        ),
                        timeout=_ACTION_TOOL_TIMEOUT_SECONDS,
                    )
                    tool_results.update(platform_tool_results)
                    tool_calls.extend(platform_tool_calls)
                except asyncio.TimeoutError:
                    tool_calls.append(
                        {
                            "name": "platform_order_tools",
                            "input": {"planned_tools": [str(item.get("name") or "") for item in required_tools]},
                            "error": "TimeoutError: platform order tools deadline exceeded",
                        }
                    )

            if _needs_appointment_lookup(required_tools) and store_service:
                try:
                    appointment_query = _appointment_query_from_planner(required_tools, state)
                    if _needs_available_time(required_tools):
                        if appointment_query.get("store_id") and appointment_query.get("date"):
                            available = await asyncio.wait_for(
                                asyncio.to_thread(
                                    store_service.available_time,
                                    store_id=str(appointment_query["store_id"]),
                                    date=str(appointment_query["date"]),
                                    customer_context=state.get("customer_context") or {},
                                ),
                                timeout=_ACTION_TOOL_TIMEOUT_SECONDS,
                            )
                            available["store_name"] = appointment_query.get("store_name", "")
                            available["date"] = appointment_query.get("date", "")
                            available["target_time"] = appointment_query.get("target_time", "")
                            tool_results["available_time"] = available
                            tool_calls.append(
                                {
                                    "name": "available_time",
                                    "input": appointment_query,
                                    "output": available,
                                }
                            )
                        else:
                            tool_results["available_time"] = {"slots": {}, "missing": appointment_query.get("missing", [])}
                except Exception as exc:
                    tool_results["available_time"] = {"slots": {}, "error": f"{type(exc).__name__}: {exc}"}
                    tool_calls.append(
                        {
                            "name": "available_time",
                            "input": {"query": content},
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )

            if _needs_professional_assist(required_tools):
                assist = _professional_assist_result(state)
                tool_results["professional_assist"] = assist
                tool_calls.append({"name": "professional_assist", "input": _planned_tool(required_tools, "professional_assist"), "output": assist})

            if tool_tasks:
                results = await asyncio.gather(*(task for _, _, task in tool_tasks), return_exceptions=True)
                merge_action_task_results(
                    tool_tasks=tool_tasks,
                    results=results,
                    tool_results=tool_results,
                    tool_calls=tool_calls,
                )
                _filter_case_studies_by_sent_documents(tool_results, state, tool_calls)

            planner_fact_output = build_planner_fact_output(tool_results, state)
            fact_envelope = dict(planner_fact_output.get("fact_envelope") or {})

            span["entry"]["tool_calls"] = tool_calls
            output = {
                "tool_results": tool_results,
                "fact_envelope": fact_envelope,
                "trace": state.get("trace", []),
            }
            span["output_snapshot"] = output
            return output

    return execute_actions


def _execute_platform_order_tools(
    *,
    required_tools: list[dict[str, Any]],
    state: AgentState,
    platform_client: PlatformAgentClient,
    tool_results: dict[str, Any],
    tool_calls: list[dict[str, Any]],
) -> None:
    for name in ("create_work_order", "add_customer_mobile", "create_order_plan"):
        tool = _explicit_planned_tool(required_tools, name)
        if not tool:
            continue
        try:
            if name == "create_work_order":
                result = _create_or_reuse_work_order(tool, state, platform_client, tool_results)
                logged_input = {key: value for key, value in tool.items() if key != "mobile"}
            elif name == "add_customer_mobile":
                result = _sync_customer_mobile(tool, state, platform_client)
                logged_input = {**tool, "mobile": _mask_mobile(str(tool.get("mobile") or ""))}
            else:
                result = _create_or_reuse_order_plan(tool, state, platform_client)
                logged_input = dict(tool)
        except Exception as exc:
            result = _tool_execution_error_result(tool_name=name, tool=tool, state=state, exc=exc)
            logged_input = {key: value for key, value in tool.items() if key != "mobile"}
            if name == "add_customer_mobile":
                logged_input["mobile"] = _mask_mobile(str(tool.get("mobile") or ""))
        tool_results[name] = result
        tool_calls.append({"name": name, "input": logged_input, "output": result})


def _create_or_reuse_work_order(
    tool: dict[str, Any],
    state: AgentState,
    platform_client: PlatformAgentClient,
    tool_results: dict[str, Any],
) -> dict[str, Any]:
    store_id = str(tool.get("store_id") or "").strip()
    amount = _coerce_deposit_amount(tool.get("prepay") or tool.get("amount"))
    category_id = _order_category_id(state, tool)
    confirmation_source = str(tool.get("store_confirmation_source") or "").strip()
    if not store_id or not amount:
        return {"status": "invalid_arguments", "missing": [key for key, value in (("store_id", store_id), ("prepay", amount)) if not value]}
    if store_id not in _known_store_ids(state, tool_results):
        return {"status": "rejected", "error": "store_id_not_backed_by_store_fact", "store_id": store_id}
    if confirmation_source not in {
        "request_confirmed",
        "current_message",
        "recent_explicit_choice",
        "single_store_card_anchor",
        "appointment_context",
    }:
        return {"status": "rejected", "error": "store_confirmation_required_before_work_order", "store_id": store_id}
    if confirmation_source == "single_store_card_anchor" and latest_single_store_card_anchor_id(state) != store_id:
        return {
            "status": "rejected",
            "error": "single_store_card_anchor_not_authoritative",
            "store_id": store_id,
        }

    fresh_orders = platform_client.list_orders(
        customer_id=_platform_customer_id(state),
        page=1,
        limit=10,
        request_context=_request_context(state),
    )
    existing = _reusable_raw_order(fresh_orders, store_id=store_id, category_id=category_id)
    if not existing:
        existing = _reusable_order(state, store_id=store_id, category_id=category_id)
    if existing:
        existing_amount = _numeric_amount(existing.get("prepay_required"))
        if existing.get("deposit_state") == "paid_by_order":
            return {
                "status": "reused",
                "source": "platform_agent.order_index",
                "store_confirmation_source": confirmation_source,
                **existing,
            }
        if existing_amount != amount:
            user_id = _request_context(state).get("user_id") or state.get("user_id")
            modified = platform_client.modify_work_order(
                order_id=existing.get("order_id"),
                store_id=store_id,
                user_id=user_id,
                amount=amount,
                category_id=category_id or None,
                request_context=_request_context(state),
            )
            if _platform_explicitly_rejected(modified):
                return {
                    "status": "rejected",
                    "error": "existing_work_order_amount_update_failed",
                    "order_id": existing.get("order_id"),
                    "result": _compact_platform_result(modified),
                }
            existing["prepay_required"] = amount
            existing["amount_updated"] = True
        return {
            "status": "reused",
            "source": "platform_agent.order_index",
            "store_confirmation_source": confirmation_source,
            **existing,
        }

    bindable_paid_order = _bindable_paid_order(fresh_orders, amount=amount)
    if bindable_paid_order:
        user_id = _request_context(state).get("user_id") or state.get("user_id")
        modified = platform_client.modify_work_order(
            order_id=bindable_paid_order.get("order_id"),
            store_id=store_id,
            user_id=user_id,
            amount=amount,
            category_id=category_id or None,
            request_context=_request_context(state),
        )
        if _platform_explicitly_rejected(modified):
            return {
                "status": "rejected",
                "error": "existing_paid_work_order_binding_failed",
                "order_id": bindable_paid_order.get("order_id"),
                "result": _compact_platform_result(modified),
            }
        return {
            "status": "reused",
            "source": "platform_agent.order.modify",
            **bindable_paid_order,
            "store_id": store_id,
            "category_id": category_id,
            "prepay_required": amount,
            "deposit_state": "paid_by_order",
            "order_binding_state": "bound",
            "order_binding_repaired": True,
            "store_confirmation_source": confirmation_source,
        }

    customer_id = _platform_customer_id(state)
    customer_add_wechat_id = _customer_add_wechat_id(state)
    user_id = _request_context(state).get("user_id") or state.get("user_id")
    customer_kind = _customer_fact(state, "kind") or _request_context(state).get("kind")
    enrichment_error = ""
    if not customer_add_wechat_id or customer_kind in (None, "") or not category_id or user_id in (None, ""):
        request_context = _request_context(state)
        try:
            customer_info = platform_client.get_customer_info(
                user_id=user_id,
                corp_id=request_context.get("corp_id"),
                wechat=request_context.get("wechat"),
                external_userid=request_context.get("external_userid"),
            )
        except Exception as exc:
            customer_info = {}
            enrichment_error = f"{type(exc).__name__}: {exc}"
        customer_add_wechat_id = str(customer_add_wechat_id or customer_info.get("customer_add_wechat_id") or "").strip()
        user_id = user_id or customer_info.get("user_id") or customer_info.get("assignee_id")
        customer_kind = customer_kind if customer_kind not in (None, "") else customer_info.get("kind")
        info_category = str(customer_info.get("category_id") or "").strip()
        if _is_platform_category_id(info_category) and not _is_platform_category_id(category_id):
            category_id = info_category
        else:
            category_id = str(category_id or info_category or "").strip()
    hard_missing = [
        key
        for key, value in (
            ("customer_id", customer_id),
        )
        if value in (None, "")
    ]
    if hard_missing:
        return {"status": "invalid_arguments", "missing": hard_missing}
    missing_optional_fields = [
        key
        for key, value in (
            ("customer_add_wechat_id", customer_add_wechat_id),
            ("user_id", user_id),
            ("kind", customer_kind),
            ("category_id", category_id),
        )
        if value in (None, "")
    ]

    check_status = "skipped_missing_optional_kind"
    if customer_kind not in (None, ""):
        check = platform_client.check_customer(
            customer_id=customer_id,
            kind=customer_kind,
            request_context=_request_context(state),
        )
        check_status = "accepted"
        if _check_customer_rejected(check):
            return {
                "status": "rejected",
                "source": "platform_agent.order.check_customer",
                "check_customer": _compact_platform_result(check),
                "missing_optional_fields": missing_optional_fields,
            }

    created = platform_client.create_work_order(
        customer_id=customer_id,
        store_id=store_id,
        user_id=user_id,
        prepay=amount,
        customer_add_wechat_id=customer_add_wechat_id,
        category_id=category_id or None,
        remark="AI客服预约金开单",
        request_context=_request_context(state),
    )
    order_id = _result_identifier(created, "order_id", "id")
    if not order_id:
        return {
            "status": "error",
            "error": "create_work_order_missing_order_id",
            "source": "platform_agent.order.create_work",
            "result": _compact_platform_result(created),
            "missing_optional_fields": missing_optional_fields,
            "enrichment_error": enrichment_error,
        }
    return {
        "status": "created",
        "source": "platform_agent.order.create_work",
        "order_id": order_id,
        "store_id": store_id,
        "category_id": category_id,
        "prepay_required": amount,
        "deposit_state": "required_unpaid",
        "store_confirmation_source": confirmation_source,
        "creation_mode": "partial" if missing_optional_fields else "complete",
        "missing_optional_fields": missing_optional_fields,
        "customer_check_status": check_status,
        "enrichment_error": enrichment_error,
    }


def _sync_customer_mobile(tool: dict[str, Any], state: AgentState, platform_client: PlatformAgentClient) -> dict[str, Any]:
    mobile = re.sub(r"\D", "", str(tool.get("mobile") or ""))
    customer_id = _platform_customer_id(state)
    if len(mobile) != 11 or not customer_id:
        return {"status": "invalid_arguments", "missing": ["mobile" if len(mobile) != 11 else "customer_id"]}
    result = platform_client.add_customer_mobile(
        customer_id=customer_id,
        mobile=mobile,
        request_context=_request_context(state),
    )
    return {
        "status": "synced" if not _platform_explicitly_rejected(result) else "rejected",
        "source": "platform_agent.customer.add_mobile",
        "mobile": _mask_mobile(mobile),
        "result": _compact_platform_result(result),
    }


def _create_or_reuse_order_plan(
    tool: dict[str, Any],
    state: AgentState,
    platform_client: PlatformAgentClient,
) -> dict[str, Any]:
    order_id = str(tool.get("order_id") or "").strip()
    store_id = str(tool.get("store_id") or "").strip()
    date = str(tool.get("date") or tool.get("appointment_time") or "").strip()
    user_id = tool.get("user_id") or _request_context(state).get("user_id") or state.get("user_id")
    customer_name, mobile = _registration_values(state, tool)
    if not order_id:
        paid_order = _latest_paid_order(state)
        order_id = str(paid_order.get("order_id") or paid_order.get("id") or "")
        store_id = store_id or str(paid_order.get("store_id") or "")
    missing = [
        key
        for key, value in (
            ("order_id", order_id),
            ("store_id", store_id),
            ("date", date),
            ("user_id", user_id),
            ("customer_name", customer_name),
            ("mobile", mobile if len(mobile) == 11 else ""),
        )
        if value in (None, "")
    ]
    if missing:
        return {"status": "invalid_arguments", "missing": missing}
    if not _state_has_paid_deposit(state):
        return {"status": "rejected", "error": "paid_deposit_required_before_order_plan", "order_id": order_id}
    existing = _existing_plan(state, order_id=order_id, date=date)
    if existing:
        return {"status": "reused", "source": "platform_agent.order_index", **existing}
    result = platform_client.create_order_plan(
        store_id=store_id,
        date=date,
        order_id=order_id,
        user_id=user_id,
        teacher_id=tool.get("teacher_id"),
        seat_check=tool.get("seat_check"),
        note="AI客服确认到店排期",
        request_context=_request_context(state),
    )
    if _platform_explicitly_rejected(result):
        return {"status": "rejected", "source": "platform_agent.order.schedule.order_plan", "result": _compact_platform_result(result)}
    return {
        "status": "created",
        "source": "platform_agent.order.schedule.order_plan",
        "order_id": order_id,
        "store_id": store_id,
        "store_name": _store_name_for_id(state, store_id),
        "appointment_time": date,
        "result": _compact_platform_result(result),
    }


def _request_context(state: AgentState) -> dict[str, Any]:
    return state.get("request_context") if isinstance(state.get("request_context"), dict) else {}


def _platform_customer_id(state: AgentState) -> str:
    context = state.get("customer_context") if isinstance(state.get("customer_context"), dict) else {}
    return str(context.get("platform_customer_id") or context.get("customer_id") or state.get("customer_id") or "").strip()


def _customer_add_wechat_id(state: AgentState) -> str:
    context = state.get("customer_context") if isinstance(state.get("customer_context"), dict) else {}
    identity = context.get("identity") if isinstance(context.get("identity"), dict) else {}
    return str(
        state.get("customer_add_wechat_id")
        or context.get("customer_add_wechat_id")
        or identity.get("customer_add_wechat_id")
        or ""
    ).strip()


def _customer_fact(state: AgentState, key: str) -> Any:
    context = state.get("customer_context") if isinstance(state.get("customer_context"), dict) else {}
    customer = context.get("customer") if isinstance(context.get("customer"), dict) else {}
    return customer.get(key)


def _order_category_id(state: AgentState, tool: dict[str, Any]) -> str:
    tool_category = str(tool.get("category_id") or "").strip()
    customer_category = str(_customer_fact(state, "category_id") or "").strip()
    request_category = str(_request_context(state).get("category_id") or "").strip()
    for value in (tool_category, customer_category, request_category):
        if _is_platform_category_id(value):
            return value
    return tool_category or customer_category or request_category


def _is_platform_category_id(value: Any) -> bool:
    return str(value or "").strip().isdigit()


def _context_orders(state: AgentState) -> list[dict[str, Any]]:
    context = state.get("customer_context") if isinstance(state.get("customer_context"), dict) else {}
    return [item for item in context.get("orders") or [] if isinstance(item, dict)]


def _reusable_order(state: AgentState, *, store_id: str, category_id: str) -> dict[str, Any]:
    for order in _context_orders(state):
        if str(order.get("status") or "") not in {"pending", "waiting_schedule", "scheduled"}:
            continue
        if str(order.get("store_id") or "") != store_id:
            continue
        order_category = str(order.get("category_id") or "")
        if not _categories_compatible(category_id, order_category):
            continue
        return {
            "order_id": str(order.get("id") or order.get("order_id") or ""),
            "order_no": str(order.get("order_no") or ""),
            "store_id": store_id,
            "category_id": order_category,
            "prepay_required": order.get("prepay_required"),
            "prepay_paid": order.get("prepay_paid"),
            "deposit_state": order.get("deposit_state") or "unknown",
        }
    return {}


def _reusable_raw_order(orders: list[dict[str, Any]], *, store_id: str, category_id: str) -> dict[str, Any]:
    for order in orders:
        if order_status_text(order.get("status")) not in {"pending", "waiting_schedule", "scheduled"}:
            continue
        if str(order.get("store_id") or "") != store_id:
            continue
        order_category = str(order.get("category_id") or "")
        if not _categories_compatible(category_id, order_category):
            continue
        payment = normalize_prepay_facts(order)
        return {
            "order_id": str(order.get("id") or order.get("order_id") or ""),
            "order_no": str(order.get("order_no") or ""),
            "store_id": store_id,
            "category_id": order_category,
            "prepay_required": payment.get("prepay_required"),
            "prepay_paid": payment.get("prepay_paid"),
            "deposit_state": payment.get("deposit_state") or "unknown",
        }
    return {}


def _bindable_paid_order(orders: list[dict[str, Any]], *, amount: int) -> dict[str, Any]:
    for order in orders:
        if order_status_text(order.get("status")) not in {"pending", "waiting_schedule", "scheduled"}:
            continue
        payment = normalize_prepay_facts(order)
        if payment.get("deposit_state") != "paid_by_order":
            continue
        paid_amount = _numeric_amount(payment.get("prepay_paid") or order.get("fee_paid"))
        if paid_amount and paid_amount != amount:
            continue
        if payment.get("order_binding_state") != "needs_binding":
            continue
        order_id = str(order.get("id") or order.get("order_id") or "")
        if not order_id:
            continue
        return {
            "order_id": order_id,
            "order_no": str(order.get("order_no") or ""),
            "prepay_paid": payment.get("prepay_paid"),
            "previous_store_id": str(order.get("store_id") or ""),
            "previous_category_id": str(order.get("category_id") or ""),
            "previous_prepay_required": payment.get("prepay_required"),
        }
    return {}


def _latest_paid_order(state: AgentState) -> dict[str, Any]:
    for order in _context_orders(state):
        if str(order.get("deposit_state") or "") == "paid_by_order":
            return order
    image_info = state.get("image_info") if isinstance(state.get("image_info"), dict) else {}
    if image_info.get("image_type") == "payment_proof" and image_info.get("payment_result") == "success":
        for order in _context_orders(state):
            if str(order.get("status") or "") in {"pending", "waiting_schedule", "scheduled"}:
                return order
    basic = state.get("customer_basic_info") if isinstance(state.get("customer_basic_info"), dict) else {}
    deposit = basic.get("deposit_state") if isinstance(basic.get("deposit_state"), dict) else {}
    order_state = basic.get("order_state") if isinstance(basic.get("order_state"), dict) else {}
    if is_paid_deposit_state(deposit.get("status") or deposit.get("deposit_state")):
        return {
            "order_id": deposit.get("order_id") or order_state.get("order_id"),
            "order_no": deposit.get("order_no") or order_state.get("order_no"),
            "store_id": order_state.get("store_id"),
            "deposit_state": deposit.get("status") or deposit.get("deposit_state"),
        }
    return {}


def _state_has_paid_deposit(state: AgentState) -> bool:
    image_info = state.get("image_info") if isinstance(state.get("image_info"), dict) else {}
    if image_info.get("image_type") == "payment_proof" and image_info.get("payment_result") == "success":
        return True
    for order in _context_orders(state):
        if is_paid_deposit_state(order.get("deposit_state")):
            return True
    basic = state.get("customer_basic_info") if isinstance(state.get("customer_basic_info"), dict) else {}
    stored = basic.get("deposit_state")
    if isinstance(stored, dict):
        return is_paid_deposit_state(stored.get("status") or stored.get("deposit_state"))
    return is_paid_deposit_state(stored)


def _registration_values(state: AgentState, tool: dict[str, Any]) -> tuple[str, str]:
    basic = state.get("customer_basic_info") if isinstance(state.get("customer_basic_info"), dict) else {}
    name = str(tool.get("customer_name") or basic.get("customer_name") or "").strip()
    mobile = re.sub(r"\D", "", str(tool.get("mobile") or basic.get("phone") or ""))
    return name, mobile


def _existing_plan(state: AgentState, *, order_id: str, date: str) -> dict[str, Any]:
    for order in _context_orders(state):
        if str(order.get("id") or order.get("order_id") or "") != order_id:
            continue
        appointment_time = str(order.get("appointment_time") or "")
        if str(order.get("status") or "") == "scheduled" and appointment_time and appointment_time == date:
            return {"order_id": order_id, "store_id": str(order.get("store_id") or ""), "appointment_time": appointment_time}
    return {}


def _known_store_ids(state: AgentState, tool_results: dict[str, Any]) -> set[str]:
    ids = {
        str(state.get("confirmed_store_id") or "").strip(),
        str(state.get("store_id") or "").strip(),
    }
    context = state.get("customer_context") if isinstance(state.get("customer_context"), dict) else {}
    appointment = context.get("appointment") if isinstance(context.get("appointment"), dict) else {}
    ids.add(str(appointment.get("store_id") or "").strip())
    current_known_store = state.get("current_known_store") if isinstance(state.get("current_known_store"), dict) else {}
    ids.add(str(current_known_store.get("store_id") or current_known_store.get("id") or "").strip())
    lookup = tool_results.get("customer_store_lookup") if isinstance(tool_results.get("customer_store_lookup"), dict) else {}
    for store in lookup.get("stores") or []:
        if isinstance(store, dict):
            ids.add(str(store.get("store_id") or store.get("id") or "").strip())
    knowledge = state.get("customer_store_knowledge") if isinstance(state.get("customer_store_knowledge"), dict) else {}
    for store in knowledge.get("stores") or []:
        if isinstance(store, dict):
            ids.add(str(store.get("store_id") or store.get("id") or "").strip())
    return {item for item in ids if item}


def _store_name_for_id(state: AgentState, store_id: str) -> str:
    current_known_store = state.get("current_known_store") if isinstance(state.get("current_known_store"), dict) else {}
    if str(current_known_store.get("store_id") or current_known_store.get("id") or "") == str(store_id):
        name = str(current_known_store.get("store_name") or current_known_store.get("name") or "").strip()
        if name:
            return name
    context = state.get("customer_context") if isinstance(state.get("customer_context"), dict) else {}
    for source in (
        context.get("orders") or [],
        (state.get("customer_store_knowledge") or {}).get("stores") or [],
    ):
        for store in source if isinstance(source, list) else []:
            if not isinstance(store, dict):
                continue
            if str(store.get("store_id") or store.get("id") or "") == str(store_id):
                name = str(store.get("store_name") or store.get("name") or "").strip()
                if name:
                    return name
    return ""


def _coerce_deposit_amount(value: Any) -> int | None:
    try:
        amount = int(float(value))
    except (TypeError, ValueError):
        return None
    return amount if amount in {10, 20, 30, 40} else None


def _numeric_amount(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _platform_explicitly_rejected(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if value.get("success") is False or value.get("allowed") is False or value.get("can_create") is False:
        return True
    status = str(value.get("status") or value.get("code") or "").strip().lower()
    return status in {"failed", "failure", "error", "rejected", "forbidden", "false"}


def _check_customer_rejected(value: Any) -> bool:
    if _platform_explicitly_rejected(value):
        return True
    if not isinstance(value, dict):
        return False
    return value.get("result") in {0, "0", False}


def _categories_compatible(expected: str, actual: str) -> bool:
    unspecified = {"", "0", "none", "null"}
    expected_value = str(expected or "").strip().lower()
    actual_value = str(actual or "").strip().lower()
    return expected_value in unspecified or actual_value in unspecified or expected_value == actual_value


def _result_identifier(value: Any, *keys: str) -> str:
    pending: list[tuple[Any, int]] = [(value, 0)]
    while pending:
        current, depth = pending.pop(0)
        if not isinstance(current, dict):
            continue
        for key in keys:
            if current.get(key) not in (None, ""):
                return str(current[key])
        if depth >= 3:
            continue
        for nested in current.values():
            if isinstance(nested, dict):
                pending.append((nested, depth + 1))
    return ""


def _compact_platform_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"value": str(value)[:160]}
    allowed = ("success", "status", "code", "message", "msg", "result", "allowed", "can_create", "id", "order_id")
    return {key: value.get(key) for key in allowed if value.get(key) not in (None, "")}


def _mask_mobile(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    return f"{digits[:3]}****{digits[-4:]}" if len(digits) == 11 else "***"


def _filter_invalid_planned_tools(
    required_tools: list[dict[str, Any]],
    state: AgentState,
    tool_results: dict[str, Any],
    tool_calls: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    invalid_by_name = _invalid_tool_policy_by_name(state)
    if not invalid_by_name:
        return required_tools
    filtered: list[dict[str, Any]] = []
    for tool in required_tools:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "").strip()
        violation = invalid_by_name.get(name)
        if not violation:
            filtered.append(tool)
            continue
        missing = str(violation.get("missing") or "tool_policy_violation")
        error = f"planner_tool_policy_violation: {missing}"
        if name == "available_time":
            tool_results[name] = {"slots": {}, "missing": ["store_id"], "error": error}
        else:
            tool_results[name] = {"error": error}
        tool_calls.append({"name": name, "input": tool, "error": error, "skipped": True})
    return filtered


def _dedupe_planned_tools(required_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for tool in required_tools:
        if not isinstance(tool, dict):
            continue
        key = _planned_tool_dedupe_key(tool)
        if key in seen:
            continue
        seen.add(key)
        output.append(tool)
    return output


def _planned_tool_dedupe_key(tool: dict[str, Any]) -> tuple[Any, ...]:
    name = str(tool.get("name") or "").strip()
    arguments = {
        key: value
        for key, value in tool.items()
        if key not in {"name", "purpose", "order"} and value not in (None, "", [], {})
    }
    serialized = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return name, serialized


def _invalid_tool_policy_by_name(state: AgentState) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    violations = state.get("tool_policy_violations") if isinstance(state.get("tool_policy_violations"), list) else []
    for item in violations:
        if not isinstance(item, dict):
            continue
        subtype = str(item.get("subtype") or "").strip()
        missing = str(item.get("missing") or "").strip()
        if subtype == "available_time" and missing.startswith("available_time_"):
            output["available_time"] = item
        if subtype == "customer_store_lookup" and missing in {
            "location_query_missing_city_or_region",
            "store_lookup_query_over_anchors_history",
            "store_lookup_query_over_ambiguous_reference",
        }:
            output["customer_store_lookup"] = item
        if subtype == "professional_assist" and missing == "professional_assist_from_advisory_health_context":
            output["professional_assist"] = item
        if subtype in {"create_work_order", "add_customer_mobile", "create_order_plan"}:
            output[subtype] = item
    return output


def _queue_planned_tool_tasks(
    *,
    tool: dict[str, Any],
    state: AgentState,
    coze_client: CozeClient,
    tool_results: dict[str, Any],
    tool_calls: list[dict[str, Any]],
    tool_tasks: list[ActionToolTask],
) -> None:
    name = str(tool.get("name") or "").strip()
    if name == "no_tool":
        return
    if name == "kb_search":
        kb_name = str(tool.get("kb_name") or "").strip()
        if kb_name and kb_name != "case_studies":
            tool_results[kb_name] = {
                "kb_name": kb_name,
                "items": [],
                "error": "planner_tool_rejected",
                "rejected_reason": "Only kb_search(case_studies) is selectable. sales_talk_qa is currently disabled.",
            }
            tool_calls.append(
                {
                    "name": "planner_tool_rejected",
                    "input": {"name": "kb_search", "kb_name": kb_name, "planned": True},
                    "error": "unsupported_kb",
                }
            )
            return
        if not kb_name:
            _record_tool_argument_error(
                tool_results=tool_results,
                tool_calls=tool_calls,
                key="kb_search",
                error="missing_planner_kb_name",
                tool_input={"planned": True, "purpose": str(tool.get("purpose") or "").strip()},
            )
            return
        query = str(tool.get("query") or "").strip()
        if not query:
            _record_tool_argument_error(
                tool_results=tool_results,
                tool_calls=tool_calls,
                key=kb_name,
                error="missing_planner_query",
                tool_input={
                    "kb_name": kb_name,
                    "query": "",
                    "planned": True,
                    "purpose": str(tool.get("purpose") or "").strip(),
                },
            )
            return
        call = {
            "name": "coze_kb_search",
            "input": {
                "kb_name": kb_name,
                "query": query,
                "planned": True,
                "purpose": str(tool.get("purpose") or "").strip(),
            },
        }
        tool_tasks.append(
            (
                kb_name,
                call,
                asyncio.create_task(
                    asyncio.wait_for(coze_client.search_kb(kb_name, query), timeout=_ACTION_TOOL_TIMEOUT_SECONDS)
                ),
            )
        )
        return


def _filter_case_studies_by_sent_documents(
    tool_results: dict[str, Any],
    state: AgentState,
    tool_calls: list[dict[str, Any]],
) -> None:
    result = tool_results.get("case_studies")
    if not isinstance(result, dict):
        return
    items = result.get("items") if isinstance(result.get("items"), list) else []
    sent_ids = _sent_case_document_ids(state)
    raw_ids = [_document_id(item) for item in items if isinstance(item, dict)]
    visible_items = [
        item
        for item in items
        if not isinstance(item, dict) or _document_id(item) not in sent_ids
    ]
    filtered_ids = [doc_id for doc_id in raw_ids if doc_id and doc_id in sent_ids]
    result["items"] = visible_items
    result["case_studies_filter"] = {
        "raw_count": len(items),
        "filtered_count": len(filtered_ids),
        "filtered_document_ids": filtered_ids,
        "visible_document_ids": [_document_id(item) for item in visible_items if isinstance(item, dict) and _document_id(item)],
    }
    if items and not visible_items:
        result["no_visible_items_reason"] = "all_case_studies_already_sent_to_customer"
    tool_calls.append(
        {
            "name": "case_studies_document_filter",
            "input": {"sent_document_ids": sorted(sent_ids)},
            "output": result["case_studies_filter"],
        }
    )


def _sent_case_document_ids(state: AgentState) -> set[str]:
    profile = state.get("customer_profile") if isinstance(state.get("customer_profile"), dict) else {}
    raw = profile.get("sent_case_document_ids") if isinstance(profile.get("sent_case_document_ids"), list) else []
    return {str(item).strip() for item in raw if str(item).strip()}


def _document_id(item: dict[str, Any]) -> str:
    return str(item.get("document_id") or item.get("documentId") or "").strip()


def _record_tool_argument_error(
    *,
    tool_results: dict[str, Any],
    tool_calls: list[dict[str, Any]],
    key: str,
    error: str,
    tool_input: dict[str, Any],
) -> None:
    tool_results[key] = {
        "items": [],
        "error": error,
        "missing": _missing_fields_for_error(error),
    }
    tool_calls.append(
        {
            "name": str(tool_input.get("name") or "coze_kb_search"),
            "input": tool_input,
            "error": error,
        }
    )


def _missing_fields_for_error(error: str) -> list[str]:
    if error == "missing_planner_query":
        return ["query"]
    if error == "missing_planner_kb_name":
        return ["kb_name"]
    return []


def _tool_execution_error_result(
    *,
    tool_name: str,
    tool: dict[str, Any],
    state: AgentState,
    exc: Exception,
) -> dict[str, Any]:
    query = str(
        tool.get("query")
        or tool.get("origin")
        or tool.get("address")
        or state.get("normalized_content")
        or state.get("content")
        or ""
    ).strip()
    result = {
        "status": "tool_error",
        "query": query,
        "purpose": str(tool.get("purpose") or "").strip(),
        "stores": [],
        "candidate_stores": [],
        "candidate_store_count": 0,
        "ranked_stores": [],
        "error": f"{type(exc).__name__}: {exc}",
        "tool": tool_name,
    }
    if tool_name == "create_work_order":
        result.update(
            {
                "store_id": str(tool.get("store_id") or "").strip(),
                "prepay_required": _coerce_deposit_amount(tool.get("prepay") or tool.get("amount")),
                "source": "platform_agent.order.create_work",
            }
        )
    return result


def _needs_distance_calculate(required_tools: list[dict[str, Any]]) -> bool:
    return any(str(item.get("name") or "") == "distance_calculate" for item in required_tools if isinstance(item, dict))


def _lookup_result_needs_distance_enrichment(result: dict[str, Any]) -> bool:
    """Add ranking facts when lookup falls back outside the customer's exact area."""
    if not isinstance(result, dict) or str(result.get("status") or "") != "ok":
        return False
    geocode = result.get("geocode") if isinstance(result.get("geocode"), dict) else {}
    if not str(geocode.get("location") or "").strip():
        return False
    if not (str(geocode.get("city") or "").strip() or str(geocode.get("district") or "").strip()):
        return False
    candidates = result.get("candidate_stores") if isinstance(result.get("candidate_stores"), list) else []
    if len(candidates) < 2:
        return False
    if result.get("exact_scope_has_store") is False:
        return True
    cities = {
        str(item.get("city") or "").strip()
        for item in candidates
        if isinstance(item, dict) and str(item.get("city") or "").strip()
    }
    if len(cities) <= 1:
        return False
    geocode_city = str(geocode.get("city") or "").strip()
    if geocode_city and geocode_city in cities:
        return False
    return True


def _needs_customer_store_lookup(required_tools: list[dict[str, Any]]) -> bool:
    return any(str(item.get("name") or "") == "customer_store_lookup" for item in required_tools if isinstance(item, dict))


def _needs_appointment_record_query(required_tools: list[dict[str, Any]]) -> bool:
    return any(
        str(item.get("name") or "") == "appointment_record_query" for item in required_tools if isinstance(item, dict)
    )


def _needs_appointment_lookup(required_tools: list[dict[str, Any]]) -> bool:
    names = {str(item.get("name") or "") for item in required_tools if isinstance(item, dict)}
    return bool(names & {"available_time"})


def _needs_available_time(required_tools: list[dict[str, Any]]) -> bool:
    return any(str(item.get("name") or "") == "available_time" for item in required_tools if isinstance(item, dict))


def _needs_professional_assist(required_tools: list[dict[str, Any]]) -> bool:
    return any(str(item.get("name") or "") == "professional_assist" for item in required_tools if isinstance(item, dict))


def _planned_tool(required_tools: list[dict[str, Any]], tool_name: str) -> dict[str, Any]:
    for item in required_tools:
        if isinstance(item, dict) and str(item.get("name") or "").strip() == tool_name:
            return item
    return {"name": tool_name}


def _explicit_planned_tool(required_tools: list[dict[str, Any]], tool_name: str) -> dict[str, Any]:
    for item in required_tools:
        if isinstance(item, dict) and str(item.get("name") or "").strip() == tool_name:
            return item
    return {}


def _appointment_query_from_planner(required_tools: list[dict[str, Any]], state: AgentState) -> dict[str, Any]:
    tool = _planned_tool(required_tools, "available_time")
    store_id = str(tool.get("store_id") or state.get("confirmed_store_id") or state.get("store_id") or "").strip()
    date = str(tool.get("date") or "").strip()
    store_name = str(tool.get("store_name") or state.get("confirmed_store_name") or state.get("store_name") or "").strip()
    target_time = str(tool.get("target_time") or tool.get("time") or tool.get("appointment_time") or "").strip()
    if target_time:
        target_time = normalize_time_text(target_time) or target_time
    if not target_time:
        target_time = normalize_time_text(str(state.get("normalized_content") or state.get("content") or ""))
    missing = []
    if not store_id:
        missing.append("store_id")
    if not date:
        missing.append("date")
    return {"store_id": store_id, "store_name": store_name, "date": date, "target_time": target_time, "missing": missing}


async def _customer_store_lookup(tool: dict[str, Any], state: AgentState, coze_client: CozeClient) -> dict[str, Any]:
    raw_query = str(tool.get("query") or tool.get("origin") or tool.get("address") or state.get("normalized_content") or "").strip()
    query = _clean_store_lookup_query(raw_query)
    purpose = str(tool.get("purpose") or "").strip()
    raw_scope_stores = _customer_scope_stores(state)
    stores, invalid_scope_stores = filter_valid_store_facts(
        raw_scope_stores,
        known_stores=[*_snapshot_store_values(), *raw_scope_stores],
    )
    scope_unavailable = _customer_store_scope_unavailable(state) or bool(raw_scope_stores and not stores)
    if not query:
        return {
            "status": "missing_query",
            "raw_query": raw_query,
            "query": "",
            "purpose": purpose,
            "stores": [],
            "candidate_stores": [],
            "candidate_store_count": 0,
            "error": "missing_query",
        }

    workflow_id = str(getattr(coze_client.settings, "geocode_workflow_id", "") or "").strip()
    geocode: dict[str, Any] = {}
    if workflow_id:
        geocode = await _geocode_address(coze_client, workflow_id, query)

    ambiguous_location = _geocode_location_is_ambiguous(
        raw_query=raw_query,
        query=query,
        geocode=geocode,
        stores=stores,
    )
    if ambiguous_location:
        return {
            "status": "ambiguous_location",
            "raw_query": raw_query,
            "query": query,
            "purpose": purpose,
            "source": "poi_to_geocode_ambiguous",
            "geocode": {
                key: geocode.get(key)
                for key in ("formatted_address", "province", "city", "district", "location")
                if geocode.get(key)
            },
            "geocode_candidate_count": int(geocode.get("candidate_count") or 0),
            "geocode_candidate_regions": list(geocode.get("candidate_regions") or [])[:6],
            "stores": [],
            "candidate_stores": [],
            "candidate_store_count": 0,
            "missing": ["confirmed_city_or_district"],
        }

    geocode_conflict = _geocode_conflicts_with_query_scope(query, geocode, stores)
    candidates = [] if geocode_conflict else _stores_for_geocode(geocode, stores, purpose)
    source = "customer_scope_geocode_conflict_ignored" if geocode_conflict else "customer_scope_geocode"
    if not candidates:
        candidates = _stores_for_text_query(query, stores, purpose)
        source = "customer_scope_text_match"
    if not candidates and scope_unavailable:
        candidates = _snapshot_stores_for_exact_query(query)
        source = "store_snapshot_exact_name"
    if not candidates and scope_unavailable and _snapshot_region_fallback_allowed(query, geocode):
        candidates = _snapshot_stores_for_region_query(query, geocode, purpose)
        source = "store_snapshot_region_fallback"
    candidates, invalid_candidates = filter_valid_store_facts(
        candidates,
        known_stores=[*_snapshot_store_values(), *stores, *candidates],
    )
    filtered_invalid_stores = [*invalid_scope_stores, *invalid_candidates]
    pre_scope_fields = _store_lookup_scope_fields(
        {
            "query": query,
            "geocode": geocode,
            "candidate_stores": [_store_lookup_item(store) for store in candidates[:60]],
        }
    )

    normalized = [_store_lookup_item(store) for store in candidates[:60]]
    scope_fields = _store_lookup_scope_fields(
        {
            "query": query,
            "geocode": geocode,
            "candidate_stores": normalized,
        }
    )
    status = "ok" if normalized else "no_match"
    return {
        "status": status,
        "raw_query": raw_query,
        "query": query,
        "purpose": purpose,
        "source": source,
        "geocode_conflict_ignored": geocode_conflict,
        "geocode": {key: geocode.get(key) for key in ("formatted_address", "province", "city", "district", "township", "location") if geocode.get(key)},
        **scope_fields,
        "stores": normalized[:12],
        "candidate_stores": normalized,
        "candidate_store_count": len(normalized),
        "filtered_invalid_stores": filtered_invalid_stores,
        "tool_errors": [
            {
                "type": "invalid_store_fact",
                "store_id": item.get("store_id", ""),
                "violations": item.get("violations", []),
            }
            for item in filtered_invalid_stores
        ],
        "missing": [] if normalized else (["store_scope_unavailable"] if scope_unavailable else ["matched_customer_scope_store"]),
    }


async def _distance_calculate(
    tool: dict[str, Any],
    state: AgentState,
    coze_client: CozeClient,
    tool_results: dict[str, Any] | None = None,
) -> dict[str, Any]:
    origin = str(tool.get("origin") or tool.get("address") or tool.get("query") or state.get("normalized_content") or "").strip()
    geocode_origin = _normalize_distance_origin_from_store_regions(_normalize_known_landmark_origin(origin), state)
    raw_candidates = _distance_candidate_stores(tool, state, tool_results or {})
    all_candidates, invalid_candidates = filter_valid_store_facts(
        raw_candidates,
        known_stores=[*_snapshot_store_values(), *raw_candidates],
    )
    if not origin:
        return {
            "status": "missing_origin",
            "candidate_stores": all_candidates,
            "filtered_invalid_stores": invalid_candidates,
            "error": "missing_origin",
        }
    if not all_candidates:
        return {
            "origin": origin,
            "status": "no_candidate_stores",
            "candidate_stores": [],
            "filtered_invalid_stores": invalid_candidates,
            "error": "no_candidate_stores",
        }
    geocode_workflow_id = str(getattr(coze_client.settings, "geocode_workflow_id", "") or "").strip()
    distance_workflow_id = str(getattr(coze_client.settings, "distance_workflow_id", "") or "").strip()
    if not geocode_workflow_id:
        return {
            "origin": origin,
            "candidate_stores": all_candidates,
            "status": "distance_tool_unavailable",
            "error": "geocode_workflow_id_not_configured",
        }
    try:
        admin_candidate = _administrative_area_origin_candidate(origin, state)
        origin_geo = await _geocode_address(coze_client, geocode_workflow_id, geocode_origin)
        if admin_candidate and not _geocode_matches_area(origin_geo, admin_candidate["area"]):
            admin_geo = await _geocode_address(coze_client, geocode_workflow_id, admin_candidate["origin"])
            if _geocode_matches_area(admin_geo, admin_candidate["area"]) or _geocode_has_unconflicted_location(admin_geo):
                origin_geo = admin_geo
                geocode_origin = admin_candidate["origin"]
        if not origin_geo.get("location"):
            return {"origin": origin, "candidate_stores": all_candidates, "status": "origin_geocode_failed", "error": "origin_geocode_failed"}
        origin_point = _parse_lng_lat(str(origin_geo.get("location") or ""))
        if not origin_point:
            return {"origin": origin, "candidate_stores": all_candidates, "status": "origin_geocode_failed", "error": "invalid_origin_location"}
        candidates = _preselect_distance_candidates(
            all_candidates,
            origin_point,
            limit=12,
        )

        async def rank_store(store: dict[str, Any]) -> dict[str, Any]:
            address = str(store.get("store_address") or "").strip()
            cached_location = str(store.get("location") or "").strip()
            geo: dict[str, Any] = {}
            point = _parse_lng_lat(cached_location) if cached_location else None
            if point:
                geo = {
                    key: store.get(key)
                    for key in ("geocode_formatted_address", "province", "city", "district", "location")
                    if store.get(key)
                }
                if store.get("geocode_formatted_address"):
                    geo["formatted_address"] = store.get("geocode_formatted_address")
                geo["location"] = cached_location
            else:
                geo = await _geocode_address(coze_client, geocode_workflow_id, address)
                point = _parse_lng_lat(str(geo.get("location") or ""))
            ranked = dict(store)
            ranked["geocode"] = {key: geo.get(key) for key in ("formatted_address", "province", "city", "district", "location")}
            if point:
                destination = str(geo.get("location") or cached_location or "").strip()
                distance = await _distance_between_points(coze_client, distance_workflow_id, str(origin_geo.get("location") or ""), destination)
                if distance.get("distance_km") is not None:
                    ranked["distance_km"] = distance["distance_km"]
                    ranked["distance_meters"] = distance.get("distance_meters")
                    ranked["duration_seconds"] = distance.get("duration_seconds")
                    ranked["distance_source"] = distance.get("source")
                else:
                    ranked["distance_km"] = round(_haversine_km(origin_point, point), 2)
                    ranked["distance_source"] = "haversine_fallback"
                    ranked["distance_error"] = distance.get("error") or "distance_workflow_failed"
            else:
                ranked["distance_error"] = "store_geocode_failed"
            return ranked

        ranked = await asyncio.gather(*(rank_store(store) for store in candidates[:12]), return_exceptions=True)
        ranked_stores, invalid_ranked_stores = filter_valid_store_facts(
            [item for item in ranked if isinstance(item, dict)],
            known_stores=[*_snapshot_store_values(), *candidates],
        )
        invalid_candidates.extend(invalid_ranked_stores)
        ranked_stores.sort(
            key=lambda item: (
                float(item.get("distance_km") if item.get("distance_km") is not None else 999999),
                _store_id_sort_key(item),
            )
        )
        return {
            "origin": origin,
            "geocode_origin": geocode_origin,
            "origin_geocode": {key: origin_geo.get(key) for key in ("formatted_address", "province", "city", "district", "location")},
            "distance_workflow_id": distance_workflow_id,
            "status": "ok" if ranked_stores else "no_candidate_stores",
            "ranked_stores": ranked_stores,
            "candidate_store_count": len(all_candidates),
            "ranked_candidate_count": len(candidates),
            "filtered_invalid_stores": invalid_candidates,
            **_distance_lookup_scope_fields(tool, tool_results or {}),
        }
    except Exception as exc:
        return {
            "origin": origin,
            "candidate_stores": all_candidates[:12],
            "status": "distance_tool_error",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _distance_candidate_stores(tool: dict[str, Any], state: AgentState, tool_results: dict[str, Any]) -> list[dict[str, Any]]:
    if str(tool.get("candidate_source") or "").strip() == "customer_store_lookup":
        lookup = tool_results.get("customer_store_lookup") if isinstance(tool_results, dict) else {}
        lookup_candidates = lookup.get("candidate_stores") if isinstance(lookup, dict) and isinstance(lookup.get("candidate_stores"), list) else []
        return [_store_lookup_candidate_for_distance(item) for item in lookup_candidates[:200] if isinstance(item, dict)]
    candidate_ids = tool.get("candidate_store_ids") if isinstance(tool.get("candidate_store_ids"), list) else []
    stores = []
    knowledge = state.get("customer_store_knowledge") if isinstance(state.get("customer_store_knowledge"), dict) else {}
    allowed_ids = {str(item) for item in candidate_ids}
    for store in knowledge.get("stores", []) if isinstance(knowledge.get("stores"), list) else []:
        if not isinstance(store, dict):
            continue
        if allowed_ids and str(store.get("store_id") or "") not in allowed_ids:
            continue
        stores.append(store)
    return stores[:12]


def _preselect_distance_candidates(
    candidates: list[dict[str, Any]],
    origin_point: tuple[float, float],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    if len(candidates) <= limit:
        return candidates
    located: list[tuple[float, int, dict[str, Any]]] = []
    missing_location: list[dict[str, Any]] = []
    for index, store in enumerate(candidates):
        point = _parse_lng_lat(str(store.get("location") or "").strip())
        if not point:
            missing_location.append(store)
            continue
        located.append((_haversine_km(origin_point, point), index, store))
    located.sort(key=lambda item: (item[0], item[1]))
    reserve_for_missing = min(3, len(missing_location), limit)
    located_limit = max(0, limit - reserve_for_missing)
    selected = [
        store
        for _, _, store in located[:located_limit]
    ]
    selected.extend(missing_location[:reserve_for_missing])
    if len(selected) < limit:
        selected.extend(
            store
            for _, _, store in located[located_limit:limit]
        )
    if len(selected) < limit:
        selected.extend(
            missing_location[
                reserve_for_missing : reserve_for_missing + limit - len(selected)
            ]
        )
    return selected[:limit]


def _stores_for_geocode(geocode: dict[str, Any], stores: list[dict[str, Any]], purpose: str) -> list[dict[str, Any]]:
    if not isinstance(geocode, dict):
        return []
    province = str(geocode.get("province") or "").strip()
    city = str(geocode.get("city") or "").strip()
    district = str(geocode.get("district") or "").strip()
    if not any((province, city, district)):
        return []
    district_matches = [
        store
        for store in stores
        if (not province or _region_equal(store.get("province"), province))
        and (not city or _region_equal(store.get("city"), city))
        and district
        and _region_equal(store.get("district"), district)
    ]
    if district_matches:
        return district_matches
    city_matches = [
        store
        for store in stores
        if (not province or _region_equal(store.get("province"), province))
        and city
        and _region_equal(store.get("city"), city)
    ]
    if city_matches:
        return city_matches
    if province:
        return [store for store in stores if _region_equal(store.get("province"), province)]
    return []


def _stores_for_text_query(query: str, stores: list[dict[str, Any]], purpose: str) -> list[dict[str, Any]]:
    text = _compact_text(query)
    if not text:
        return []
    scored: list[tuple[int, dict[str, Any]]] = []
    for store in stores:
        score = _store_text_match_score(text, store)
        if score > 0:
            scored.append((score, store))
    if not scored:
        return []
    scored.sort(key=lambda item: (-item[0], str(item[1].get("store_id") or "")))
    if purpose == "nearby_candidates":
        top_city = str(scored[0][1].get("city") or "").strip()
        if top_city:
            city_stores = [store for store in stores if _region_equal(store.get("city"), top_city)]
            if city_stores:
                return city_stores
    return [store for _, store in scored]


def _clean_store_lookup_query(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    # Data-cleaning only: strip labels from platform/operator structured notes.
    match = re.match(r"^\s*[\u4e00-\u9fffA-Za-z0-9_ /-]{2,16}\s*[:：]\s*(.+?)\s*$", text)
    if match:
        label = match.group(0).split(":", 1)[0].split("：", 1)[0]
        label_compact = _compact_text(label)
        if label_compact and any(marker in label_compact for marker in ("门店", "位置", "地址", "定位", "区域", "商圈", "地标")):
            text = match.group(1).strip()
    return text


def _geocode_location_is_ambiguous(
    *,
    raw_query: str,
    query: str,
    geocode: dict[str, Any],
    stores: list[dict[str, Any]],
) -> bool:
    """Keep cross-city geocode ambiguity as a fact instead of selecting a city for the model."""

    if not bool(geocode.get("ambiguous_regions")):
        return False
    if _has_structured_location_label(raw_query):
        return False
    return not bool(_stores_for_text_query(query, stores, ""))


def _has_structured_location_label(value: str) -> bool:
    text = str(value or "").strip()
    match = re.match(r"^\s*([\u4e00-\u9fffA-Za-z0-9_ /-]{2,16})\s*[:：]\s*.+$", text)
    if not match:
        return False
    label = _compact_text(match.group(1))
    return bool(label and any(marker in label for marker in ("门店", "位置", "地址", "定位", "区域", "商圈", "地标")))


def _geocode_conflicts_with_query_scope(query: str, geocode: dict[str, Any], stores: list[dict[str, Any]]) -> bool:
    if not isinstance(geocode, dict) or not geocode.get("location"):
        return False
    if not stores:
        return False
    text = _compact_text(query)
    if not text:
        return False
    explicit_matches = _stores_for_text_query(query, stores, "")
    if not explicit_matches:
        return False
    geocode_matches = _stores_for_geocode(geocode, stores, "")
    if not geocode_matches:
        return True
    explicit_ids = {str(store.get("store_id") or store.get("id") or "").strip() for store in explicit_matches}
    geocode_ids = {str(store.get("store_id") or store.get("id") or "").strip() for store in geocode_matches}
    return bool(explicit_ids and geocode_ids and explicit_ids.isdisjoint(geocode_ids))


def _store_text_match_score(text: str, store: dict[str, Any]) -> int:
    score = 0
    for key, weight in (
        ("store_name", 8),
        ("city", 6),
        ("district", 5),
        ("province", 4),
        ("store_address", 3),
        ("parking_name", 2),
        ("parking_address", 2),
    ):
        value = _compact_text(store.get(key))
        if value and (value in text or text in value):
            score += weight
            continue
        for token in _region_tokens(str(store.get(key) or "")):
            compact_token = _compact_text(token)
            if len(compact_token) < 2:
                continue
            if compact_token and compact_token in text:
                score += weight
                break
    return score


def _store_lookup_item(store: dict[str, Any]) -> dict[str, Any]:
    parking = str(store.get("parking_name") or store.get("parking_address") or "").strip()
    return {
        "id": str(store.get("store_id") or "").strip(),
        "store_id": str(store.get("store_id") or "").strip(),
        "name": str(store.get("store_name") or "").strip(),
        "store_name": str(store.get("store_name") or "").strip(),
        "province": str(store.get("province") or "").strip(),
        "city": str(store.get("city") or "").strip(),
        "district": str(store.get("district") or "").strip(),
        "address": str(store.get("store_address") or "").strip(),
        "store_address": str(store.get("store_address") or "").strip(),
        "business_hours": str(store.get("business_hours") or "").strip(),
        "parking": parking,
        "parking_name": str(store.get("parking_name") or "").strip(),
        "parking_address": str(store.get("parking_address") or "").strip(),
        "map_url": str(store.get("map_url") or "").strip(),
        "location": str(store.get("location") or "").strip(),
        "geocode_formatted_address": str(store.get("geocode_formatted_address") or "").strip(),
        "store_fact_integrity": str(store.get("store_fact_integrity") or "valid").strip(),
        "store_fact_integrity_violations": list(store.get("store_fact_integrity_violations") or []),
        "store_fact_integrity_warnings": list(store.get("store_fact_integrity_warnings") or []),
    }


def _store_lookup_candidate_for_distance(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "store_id": str(item.get("store_id") or item.get("id") or "").strip(),
        "store_name": str(item.get("store_name") or item.get("name") or "").strip(),
        "province": str(item.get("province") or "").strip(),
        "city": str(item.get("city") or "").strip(),
        "district": str(item.get("district") or "").strip(),
        "store_address": str(item.get("store_address") or item.get("address") or "").strip(),
        "business_hours": str(item.get("business_hours") or "").strip(),
        "parking_name": str(item.get("parking_name") or item.get("parking") or "").strip(),
        "parking_address": str(item.get("parking_address") or "").strip(),
        "map_url": str(item.get("map_url") or "").strip(),
        "location": str(item.get("location") or "").strip(),
        "geocode_formatted_address": str(item.get("geocode_formatted_address") or "").strip(),
        "store_fact_integrity": str(item.get("store_fact_integrity") or "valid").strip(),
        "store_fact_integrity_violations": list(item.get("store_fact_integrity_violations") or []),
        "store_fact_integrity_warnings": list(item.get("store_fact_integrity_warnings") or []),
    }


def _store_id_sort_key(item: dict[str, Any]) -> int:
    try:
        return int(str(item.get("store_id") or item.get("id") or "").strip())
    except ValueError:
        return 999999


def _region_equal(left: Any, right: Any) -> bool:
    left_tokens = {_compact_text(token) for token in _region_tokens(str(left or "")) if _compact_text(token)}
    right_tokens = {_compact_text(token) for token in _region_tokens(str(right or "")) if _compact_text(token)}
    return bool(left_tokens & right_tokens)


def _compact_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip()).lower()


def _normalize_distance_origin_from_store_regions(origin: str, state: AgentState) -> str:
    text = str(origin or "").strip()
    if not text:
        return ""
    stores = _customer_scope_stores(state)
    matches: list[tuple[int, str]] = []
    for store in stores:
        province = str(store.get("province") or "").strip()
        city = str(store.get("city") or "").strip()
        district = str(store.get("district") or "").strip()
        if not city or not district:
            continue
        city_tokens = _region_tokens(city)
        district_tokens = _region_tokens(district)
        has_city = any(token and token in text for token in city_tokens)
        has_district = any(token and token in text for token in district_tokens)
        if not has_district:
            continue
        score = 2 if has_city else 1
        full_region = _join_region(province=province, city=city, district=district)
        matches.append((score, full_region))
    if not matches:
        return text
    matches.sort(key=lambda item: (-item[0], len(item[1])))
    top_score = matches[0][0]
    top_regions = sorted({region for score, region in matches if score == top_score})
    return top_regions[0] if len(top_regions) == 1 else text


def _administrative_area_origin_candidate(origin: str, state: AgentState) -> dict[str, str]:
    text = str(origin or "").strip()
    if not text:
        return {}
    stores = _customer_scope_stores(state)
    city_names = sorted({str(store.get("city") or "").strip() for store in stores if store.get("city")}, key=len, reverse=True)
    for city in city_names:
        for city_token in _region_tokens(city):
            if not city_token or city_token not in text:
                continue
            area = text.split(city_token, 1)[1]
            area = _clean_area_candidate(area)
            if not _looks_like_admin_area_candidate(area):
                continue
            return {"origin": f"{city}{area}区", "area": area}
    return {}


def _clean_area_candidate(value: str) -> str:
    text = re.sub(r"[，,。？?！!\s]", "", str(value or "").strip())
    text = re.sub(r"(附近|周边|哪家|哪个|最近|更近|比较近|近点|近一点|近|门店|店|地址|路线|导航|停车|营业时间|有|吗|呢|呀|的|在|离)", "", text)
    return text.strip()


def _looks_like_admin_area_candidate(value: str) -> bool:
    text = str(value or "").strip()
    if len(text) < 2 or len(text) > 5:
        return False
    if text.endswith(("区", "县", "市", "镇", "街道", "机场", "车站", "火车站", "高铁站", "商场", "广场", "大厦", "医院", "学校")):
        return False
    return bool(re.fullmatch(r"[\u4e00-\u9fff]+", text))


def _geocode_matches_area(geo: dict[str, Any], area: str) -> bool:
    text = str(area or "").strip()
    if not text or not isinstance(geo, dict):
        return False
    district = str(geo.get("district") or "").strip()
    return bool(district and text in district)


def _geocode_has_unconflicted_location(geo: dict[str, Any]) -> bool:
    if not isinstance(geo, dict):
        return False
    if not str(geo.get("location") or "").strip():
        return False
    return not str(geo.get("district") or "").strip()


def _customer_scope_stores(state: AgentState) -> list[dict[str, Any]]:
    knowledge = state.get("customer_store_knowledge") if isinstance(state.get("customer_store_knowledge"), dict) else {}
    stores = knowledge.get("stores") if isinstance(knowledge.get("stores"), list) else []
    return [store for store in stores if isinstance(store, dict)]


def _customer_store_scope_unavailable(state: AgentState) -> bool:
    knowledge = state.get("customer_store_knowledge") if isinstance(state.get("customer_store_knowledge"), dict) else {}
    if not isinstance(knowledge, dict):
        return True
    stores = knowledge.get("stores") if isinstance(knowledge.get("stores"), list) else []
    if stores:
        return False
    source = str(knowledge.get("source") or "").strip()
    if source in {
        "missing_customer_store_scope",
        "platform_agent_unavailable",
        "platform_agent.store_index_error",
        "customer_store_knowledge_error",
    }:
        return True
    if knowledge.get("error") or knowledge.get("store_scope_error"):
        return True
    cache = knowledge.get("cache") if isinstance(knowledge.get("cache"), dict) else {}
    return str(cache.get("store_scope_status") or "") in {"miss", "error"}


def _snapshot_stores_for_exact_query(query: str) -> list[dict[str, Any]]:
    text = _compact_text(query)
    if not text:
        return []
    stores = _usable_snapshot_store_values()
    matched: list[dict[str, Any]] = []
    for store in stores:
        name = _compact_text(store.get("store_name") or store.get("name"))
        if not name:
            continue
        if _snapshot_store_name_matches_query(name, text):
            matched.append(store)
    return _without_subsumed_snapshot_stores(_dedupe_snapshot_stores(matched))[:5]


def _snapshot_stores_for_region_query(query: str, geocode: dict[str, Any], purpose: str) -> list[dict[str, Any]]:
    stores = _usable_snapshot_store_values()
    candidates = _stores_for_text_query(query, stores, purpose)
    if not candidates:
        candidates = _stores_for_geocode(geocode, stores, purpose)
    return _dedupe_snapshot_stores(candidates)[:60]


def _augment_with_snapshot_region_candidates(
    candidates: list[dict[str, Any]],
    geocode: dict[str, Any],
    purpose: str,
) -> tuple[list[dict[str, Any]], bool]:
    city = str(geocode.get("city") or "").strip() if isinstance(geocode, dict) else ""
    province = str(geocode.get("province") or "").strip() if isinstance(geocode, dict) else ""
    if not province and not city:
        return candidates, False
    same_city_candidates = [
        store
        for store in _usable_snapshot_store_values()
        if (not province or _region_equal(store.get("province"), province))
        and city
        and _region_equal(store.get("city"), city)
    ]
    snapshot_candidates = same_city_candidates
    if not snapshot_candidates and province:
        snapshot_candidates = [
            store
            for store in _usable_snapshot_store_values()
            if _region_equal(store.get("province"), province)
        ]
    merged = _dedupe_snapshot_stores([*candidates, *snapshot_candidates])
    return merged[:60], len(merged) > len(_dedupe_snapshot_stores(candidates))


def _store_lookup_scope_fields(result: dict[str, Any]) -> dict[str, Any]:
    geocode = result.get("geocode") if isinstance(result.get("geocode"), dict) else {}
    candidates = result.get("candidate_stores") if isinstance(result.get("candidate_stores"), list) else []
    resolved_level = _geocode_resolved_admin_level(str(result.get("query") or ""), geocode)
    exact_has_store = _geocode_exact_scope_has_store(geocode, candidates, resolved_level)
    return {
        "province": str(geocode.get("province") or "").strip(),
        "city": str(geocode.get("city") or "").strip(),
        "district": str(geocode.get("district") or "").strip(),
        "township": str(geocode.get("township") or "").strip(),
        "resolved_admin_level": resolved_level,
        "scope_match_level": _geocode_scope_match_level(geocode, candidates, resolved_level, exact_has_store),
        "exact_scope_has_store": exact_has_store,
    }


def _distance_lookup_scope_fields(tool: dict[str, Any], tool_results: dict[str, Any]) -> dict[str, Any]:
    explicit = tool.get("lookup_scope") if isinstance(tool.get("lookup_scope"), dict) else {}
    if explicit:
        return {key: explicit.get(key) for key in _STORE_LOOKUP_SCOPE_FIELD_NAMES if key in explicit}
    lookup = tool_results.get("customer_store_lookup") if isinstance(tool_results.get("customer_store_lookup"), dict) else {}
    return {key: lookup.get(key) for key in _STORE_LOOKUP_SCOPE_FIELD_NAMES if key in lookup}


_STORE_LOOKUP_SCOPE_FIELD_NAMES = (
    "province",
    "city",
    "district",
    "township",
    "resolved_admin_level",
    "scope_match_level",
    "exact_scope_has_store",
)


def _geocode_resolved_admin_level(query: str, geocode: dict[str, Any]) -> str:
    text = _compact_text(query)
    if str(geocode.get("township") or "").strip() or text.endswith(("镇", "乡", "村", "街道", "社区")):
        return "township"
    if str(geocode.get("district") or "").strip():
        return "district"
    if str(geocode.get("city") or "").strip():
        return "city"
    if str(geocode.get("province") or "").strip():
        return "province"
    return "unknown"


def _geocode_exact_scope_has_store(geocode: dict[str, Any], candidates: list[Any], resolved_level: str) -> bool | None:
    if not candidates:
        return False
    province = str(geocode.get("province") or "").strip()
    city = str(geocode.get("city") or "").strip()
    district = str(geocode.get("district") or "").strip()
    township = str(geocode.get("township") or "").strip()
    if resolved_level == "township":
        if township:
            return any(
                isinstance(store, dict)
                and _region_equal(store.get("province"), province)
                and _region_equal(store.get("city"), city)
                and _region_equal(store.get("district"), district)
                and _store_contains_region_text(store, township)
                for store in candidates
            )
        return any(
            isinstance(store, dict)
            and _region_equal(store.get("province"), province)
            and _region_equal(store.get("city"), city)
            and _region_equal(store.get("district"), district)
            for store in candidates
        )
    if resolved_level == "district":
        return any(
            isinstance(store, dict)
            and _region_equal(store.get("province"), province)
            and _region_equal(store.get("city"), city)
            and _region_equal(store.get("district"), district)
            for store in candidates
        )
    if resolved_level == "city":
        return any(
            isinstance(store, dict)
            and _region_equal(store.get("province"), province)
            and _region_equal(store.get("city"), city)
            for store in candidates
        )
    if resolved_level == "province":
        return any(isinstance(store, dict) and _region_equal(store.get("province"), province) for store in candidates)
    return None


def _geocode_scope_match_level(
    geocode: dict[str, Any],
    candidates: list[Any],
    resolved_level: str,
    exact_has_store: bool | None,
) -> str:
    if exact_has_store:
        return resolved_level
    if not candidates:
        return "none"
    province = str(geocode.get("province") or "").strip()
    city = str(geocode.get("city") or "").strip()
    if city and any(isinstance(store, dict) and _region_equal(store.get("city"), city) for store in candidates):
        return "city_fallback"
    if province and any(isinstance(store, dict) and _region_equal(store.get("province"), province) for store in candidates):
        return "province_fallback"
    return "unknown"


def _store_contains_region_text(store: dict[str, Any], region: str) -> bool:
    text = _compact_text(
        " ".join(
            str(store.get(key) or "")
            for key in ("store_name", "store_address", "address", "parking_name", "parking_address")
        )
    )
    return bool(_compact_text(region) and _compact_text(region) in text)


def _snapshot_region_fallback_allowed(query: str, geocode: dict[str, Any]) -> bool:
    text = _compact_text(query)
    if not text or text in {"门店", "门店在哪里", "你们门店在哪里", "地址", "位置", "附近"}:
        return False
    if isinstance(geocode, dict) and (geocode.get("city") or geocode.get("district")):
        return True
    return _query_matches_snapshot_region(query)


def _query_matches_snapshot_region(query: str) -> bool:
    text = _compact_text(query)
    if not text or len(text) < 2:
        return False
    for store in _usable_snapshot_store_values():
        for key in ("city", "district", "province", "store_address"):
            value = str(store.get(key) or "").strip()
            for token in _region_tokens(value):
                compact_token = _compact_text(token)
                if compact_token and (compact_token in text or (len(text) >= 2 and text in compact_token)):
                    return True
    return False


def _usable_snapshot_store_values() -> list[dict[str, Any]]:
    rows = [store for store in _snapshot_store_values() if _snapshot_store_is_usable(store)]
    valid, _ = filter_valid_store_facts(rows)
    return valid


def _snapshot_store_is_usable(store: dict[str, Any]) -> bool:
    name = str(store.get("store_name") or store.get("name") or "").strip()
    address = str(store.get("store_address") or store.get("address") or "").strip()
    store_id = str(store.get("store_id") or store.get("id") or "").strip()
    if not (store_id and name and address):
        return False
    return "停业" not in name and store_fact_is_valid(store)


def _snapshot_store_name_matches_query(name: str, text: str) -> bool:
    if name in text or (len(text) >= 4 and text in name):
        return True
    normalized_name = _normalize_store_name_for_match(name)
    normalized_text = _normalize_store_name_for_match(text)
    return bool(
        normalized_name
        and normalized_text
        and (normalized_name in normalized_text or (len(normalized_text) >= 4 and normalized_text in normalized_name))
    )


def _normalize_store_name_for_match(value: str) -> str:
    return str(value or "").replace("市", "").replace("百星", "").strip()


def _snapshot_store_values() -> list[dict[str, Any]]:
    global _STORE_SNAPSHOT_CACHE, _STORE_SNAPSHOT_CACHE_KEY
    snapshot: dict[str, Any] = {}
    selected_key: tuple[str, int] | None = None
    selected_path: Path | None = None
    for path in _snapshot_store_candidate_paths():
        try:
            stat = path.stat()
        except OSError:
            continue
        selected_path = path
        selected_key = (str(path.resolve()), stat.st_mtime_ns)
        break
    if selected_key != _STORE_SNAPSHOT_CACHE_KEY:
        if selected_path is not None:
            try:
                loaded = json.loads(selected_path.read_text(encoding="utf-8"))
                snapshot = loaded if isinstance(loaded, dict) else {}
            except (OSError, json.JSONDecodeError):
                snapshot = {}
        _STORE_SNAPSHOT_CACHE = snapshot
        _STORE_SNAPSHOT_CACHE_KEY = selected_key
    stores_by_id = _STORE_SNAPSHOT_CACHE.get("stores_by_id") if isinstance(_STORE_SNAPSHOT_CACHE, dict) else {}
    if isinstance(stores_by_id, dict) and stores_by_id:
        return [store for store in stores_by_id.values() if isinstance(store, dict)]
    return [dict(item) for item in KNOWN_STORE_FACTS]


def _snapshot_store_candidate_paths() -> list[Path]:
    paths: list[Path] = []
    env_path = str(os.getenv("STORE_SNAPSHOT_PATH") or "").strip()
    if env_path:
        paths.append(Path(env_path))
    paths.extend(
        [
            Path("data/store_snapshot.json"),
            Path(__file__).resolve().parents[3] / "data" / "store_snapshot.json",
            Path("/opt/ai-paths/data/store_snapshot.json"),
        ]
    )
    output: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        if key in seen:
            continue
        seen.add(key)
        output.append(path)
    return output


def _dedupe_snapshot_stores(stores: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for store in stores:
        key = (
            str(store.get("store_id") or store.get("id") or "").strip(),
            str(store.get("store_name") or store.get("name") or "").strip(),
        )
        if not key[1] or key in seen:
            continue
        seen.add(key)
        output.append(store)
    return output


def _without_subsumed_snapshot_stores(stores: list[dict[str, Any]]) -> list[dict[str, Any]]:
    names = [str(store.get("store_name") or store.get("name") or "").strip() for store in stores]
    output: list[dict[str, Any]] = []
    for store, name in zip(stores, names):
        compact_name = _compact_text(name)
        if any(compact_name != _compact_text(other) and compact_name in _compact_text(other) for other in names):
            continue
        output.append(store)
    return output


def _region_tokens(value: str) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    tokens = {text}
    for suffix in ("省", "市", "区", "县", "旗", "自治州", "自治县", "新区"):
        if text.endswith(suffix) and len(text) > len(suffix):
            tokens.add(text[: -len(suffix)])
    return sorted(tokens, key=len, reverse=True)


def _normalize_known_landmark_origin(origin: str) -> str:
    text = str(origin or "").strip()
    compact = _compact_text(text)
    if "厦门" in compact and "机场" in compact and "高崎" not in compact:
        return "厦门高崎国际机场"
    return text


def _join_region(*, province: str, city: str, district: str) -> str:
    parts: list[str] = []
    for value in (province, city, district):
        text = str(value or "").strip()
        if text and text not in parts:
            parts.append(text)
    return "".join(parts)


async def _geocode_address(coze_client: CozeClient, workflow_id: str, address: str) -> dict[str, Any]:
    if not address:
        return {}
    raw = await coze_client.run_workflow(workflow_id, {"address": address})
    data = raw.get("data")
    if isinstance(data, list):
        return _first_geocode_candidate(data)
    if isinstance(data, str) and data:
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            parsed = {}
        if isinstance(parsed, list):
            return _first_geocode_candidate(parsed)
    elif isinstance(data, dict):
        parsed = data
    else:
        parsed = raw
    output = parsed.get("output") if isinstance(parsed, dict) else None
    if isinstance(output, list) and output and isinstance(output[0], dict):
        return _first_geocode_candidate(output)
    if isinstance(output, dict):
        return output
    if isinstance(parsed, dict) and isinstance(parsed.get("output"), str):
        try:
            nested = json.loads(str(parsed.get("output") or ""))
            if isinstance(nested, list) and nested and isinstance(nested[0], dict):
                return _first_geocode_candidate(nested)
        except json.JSONDecodeError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _first_geocode_candidate(items: list[Any]) -> dict[str, Any]:
    if not items or not isinstance(items[0], dict):
        return {}
    first = dict(items[0])
    first_region = {
        key: str(first.get(key) or "").strip()
        for key in ("province", "city", "district")
        if str(first.get(key) or "").strip()
    }
    first["candidate_count"] = len(items)
    first["candidate_regions"] = [first_region] if first_region else []
    first["ambiguous_regions"] = False
    return first


async def _distance_between_points(coze_client: CozeClient, workflow_id: str, origin: str, destination: str) -> dict[str, Any]:
    if not workflow_id:
        return {"source": "distance_workflow", "error": "distance_workflow_id_not_configured"}
    if not origin or not destination:
        return {"source": "distance_workflow", "error": "missing_origin_or_destination"}
    try:
        raw = await coze_client.run_workflow(workflow_id, {"origin": origin, "destination": destination})
    except Exception as exc:
        return {"source": "distance_workflow", "error": f"{type(exc).__name__}: {exc}"}
    parsed = _parse_workflow_data(raw)
    output = parsed.get("output") if isinstance(parsed, dict) else None
    if isinstance(output, str) and output:
        try:
            output = json.loads(output)
        except json.JSONDecodeError:
            output = {}
    if not isinstance(output, dict):
        output = parsed if isinstance(parsed, dict) else {}
    meters = _to_float(output.get("distance"))
    duration = _to_float(output.get("duration"))
    if meters is None:
        return {"source": "distance_workflow", "raw": output, "error": "distance_missing"}
    result: dict[str, Any] = {
        "source": "distance_workflow",
        "distance_meters": int(meters),
        "distance_km": round(meters / 1000, 2),
    }
    if duration is not None:
        result["duration_seconds"] = int(duration)
    return result


def _parse_workflow_data(raw: dict[str, Any]) -> dict[str, Any]:
    data = raw.get("data") if isinstance(raw, dict) else None
    if isinstance(data, str) and data:
        try:
            parsed = json.loads(data)
            return parsed if isinstance(parsed, dict) else {"output": parsed}
        except json.JSONDecodeError:
            return {"output": data}
    if isinstance(data, dict):
        return data
    return raw if isinstance(raw, dict) else {}


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_lng_lat(value: str) -> tuple[float, float] | None:
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 2:
        return None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lng1, lat1 = a
    lng2, lat2 = b
    radius = 6371.0088
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lng2 - lng1)
    h = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(h))


def _professional_assist_result(state: AgentState) -> dict[str, Any]:
    handoff = state.get("handoff") if isinstance(state.get("handoff"), dict) else {}
    primary = state.get("primary_task") if isinstance(state.get("primary_task"), dict) else {}
    return {
        "status": "requested",
        "reason": str(handoff.get("reason") or primary.get("customer_need") or "").strip(),
        "task_type": str(primary.get("type") or "").strip(),
        "subtype": str(primary.get("subtype") or "").strip(),
        "policy_hint": str(primary.get("policy_hint") or "").strip(),
        "required_internal_action": "professional_colleague_follow_up",
    }
