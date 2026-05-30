from __future__ import annotations

import asyncio
import html
import re
import json
from datetime import date, timedelta
from difflib import SequenceMatcher
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.graph import planner_helpers
from app.graph.state import AgentState
from app.policies.constants import (
    ADVANTAGE_KEYWORDS,
    AFTER_SALES_KEYWORDS,
    APPOINTMENT_KEYWORDS,
    CAMPAIGN_KEYWORDS,
    CITY_NAMES,
    COMPLAINT_KEYWORDS,
    COMPETITOR_KEYWORDS,
    EFFECT_DISPUTE_KEYWORDS,
    HUMAN_KEYWORDS,
    KB_BY_SKILL,
    PRICE_KEYWORDS,
    PRICE_OBJECTION_KEYWORDS,
    PRICE_PROJECT_ALIASES,
    PROJECT_KEYWORDS,
    SEVERE_AFTER_SALES_KEYWORDS,
    STORE_KEYWORDS,
    TRUST_KEYWORDS,
)
from app.prompts.reply_synthesizer import build_repair_messages, build_reply_messages
from app.services.coze_client import CozeClient
from app.services.customer_context import CustomerContextService
from app.services.memory_store import CustomerMemoryStore
from app.services.model_client import ModelClient
from app.services.pricing_repository import LocalPricingRepository
from app.services.store_service import StoreService
from app.services.trace_logger import TraceLogger

def build_graph(
    coze_client: CozeClient,
    trace_logger: TraceLogger,
    model_client: ModelClient | None = None,
    memory_store: CustomerMemoryStore | None = None,
    pricing_repository: LocalPricingRepository | None = None,
    customer_context_service: CustomerContextService | None = None,
    store_service: StoreService | None = None,
):
    graph = StateGraph(AgentState)

    async def normalize_input(state: AgentState) -> dict[str, Any]:
        with trace_logger.node(state, "normalize_input", {"content": state.get("content")}) as span:
            normalized = (state.get("content") or "").strip()
            if not normalized and state.get("file_image"):
                normalized = "[图片]"
            errors = list(state.get("errors", []))
            if _looks_bad_text(normalized):
                errors.append({"node": "normalize_input", "message": "输入疑似乱码，已保留原文但后续会降低置信度"})
            output = {"normalized_content": normalized, "errors": errors, "trace": state.get("trace", [])}
            span["output_snapshot"] = output
            return output

    async def image_understanding_placeholder(state: AgentState) -> dict[str, Any]:
        with trace_logger.node(
            state,
            "image_understanding",
            {"file_image": state.get("file_image"), "content": state.get("normalized_content")},
        ) as span:
            has_image = bool(state.get("file_image"))
            model_call: dict[str, Any] | None = None
            if has_image and model_client and model_client.available:
                try:
                    model_call = {"name": "vision_model", "input": {"tier": "vision"}}
                    payload = await model_client.vision_json(
                        prompt=_vision_prompt(state),
                        image_url=str(state.get("file_image")),
                        tier="vision",
                    )
                    image_info = _validated_image_info(payload, has_image=True)
                    model_call["output"] = {
                        "image_type": image_info.get("image_type"),
                        "confidence": image_info.get("confidence"),
                    }
                    model_call["usage"] = _model_usage_snapshot(model_client)
                except Exception as exc:
                    image_info = _fallback_image_info(has_image=True)
                    model_call = model_call or {"name": "vision_model", "input": {"tier": "vision"}}
                    model_call["error"] = f"{type(exc).__name__}: {exc}"
            else:
                image_info = _fallback_image_info(has_image=has_image)
            if model_call:
                span["entry"]["tool_calls"] = [model_call]
            output = {"image_info": image_info, "trace": state.get("trace", [])}
            span["output_snapshot"] = output
            return output

    async def hard_guardrails(state: AgentState) -> dict[str, Any]:
        content = state.get("normalized_content") or ""
        with trace_logger.node(state, "hard_guardrails", {"content": content}) as span:
            hit_terms = [word for word in HUMAN_KEYWORDS if word in content]
            if _is_identity_question(content):
                hit_terms = [term for term in hit_terms if term not in {"真人", "人工", "客服接待"}]
            if _has_minor_signal(content):
                hit_terms.append("未成年")
            hit_terms.extend(_severe_after_sales_terms(content))
            hit_terms.extend(_complaint_terms(content))
            if _has_effect_dispute(content):
                hit_terms.append("效果纠纷")
            if _is_image_following_complaint(state):
                hit_terms.append("效果纠纷")
            result = {
                "blocked": bool(hit_terms),
                "terms": _dedupe_strings(hit_terms),
                "action": "professional_assist" if hit_terms else "",
            }
            output = {"guardrail_result": result, "trace": state.get("trace", [])}
            span["output_snapshot"] = output
            return output

    async def load_memory(state: AgentState) -> dict[str, Any]:
        with trace_logger.node(state, "load_memory", {"customer_id": state.get("customer_id")}) as span:
            memory = memory_store.load(str(state.get("customer_id") or "unknown")) if memory_store else {}
            output = {
                "customer_profile": memory.get("portrait", {}) if isinstance(memory, dict) else {},
                "customer_basic_info": memory.get("basic_info", {}) if isinstance(memory, dict) else {},
                "history_events": memory.get("history_events", []) if isinstance(memory, dict) else [],
                "lifecycle_stage": memory.get("lifecycle_stage", "") if isinstance(memory, dict) else "",
                "saved_memory": memory if isinstance(memory, dict) else {},
                "trace": state.get("trace", []),
            }
            span["output_snapshot"] = output
            return output

    async def load_customer_context(state: AgentState) -> dict[str, Any]:
        with trace_logger.node(
            state,
            "load_customer_context",
            {
                "customer_id": state.get("customer_id"),
                "memory_loaded": bool(state.get("saved_memory")),
                "user_id": state.get("user_id"),
                "wechat": state.get("wechat"),
                "external_userid": state.get("external_userid"),
            },
        ) as span:
            context = {}
            error = None
            if customer_context_service:
                try:
                    context = customer_context_service.load(
                        customer_id=str(state.get("customer_id") or "unknown"),
                        memory=state.get("saved_memory") or {},
                        request_context=_request_context_from_state(state),
                    )
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
            output = {
                "customer_context": context,
                "appointment_cache": context.get("appointment", {}) if isinstance(context, dict) else {},
                "customer_context_error": error,
                "trace": state.get("trace", []),
            }
            span["output_snapshot"] = output
            return output

    async def planner_brain(state: AgentState) -> dict[str, Any]:
        content = state.get("normalized_content") or ""
        with trace_logger.node(
            state,
            "planner_brain",
            {"content": content, "image_info": state.get("image_info"), "guardrail_result": state.get("guardrail_result")},
        ) as span:
            planner_call: dict[str, Any] | None = None
            if state.get("guardrail_result", {}).get("blocked"):
                terms = state.get("guardrail_result", {}).get("terms", [])
                intent = "complaint_refund" if any(term in terms for term in ["投诉", "退款", "维权", "曝光", "骗子", "骗钱", "骗我", "效果纠纷"]) else "human_request"
                intents = [{"intent": intent, "skill": "handoff", "priority": 0, "reason": "命中投诉、纠纷或风险关键词"}]
            else:
                try:
                    rule_intents = planner_helpers.detect_intents(content, state.get("image_info", {}))
                    if model_client and model_client.available and planner_helpers.should_use_model_planner(state):
                        tier = planner_helpers.planner_model_tier(state)
                        planner_call = {"name": "planner_brain_model", "input": {"tier": tier}}
                        payload = await model_client.chat_json(planner_helpers.planner_messages_for_model(state), tier=tier)
                        planner_call["usage"] = _model_usage_snapshot(model_client)
                        intents = planner_helpers.validated_planner_intents(payload)
                        intents = planner_helpers.merge_intents(state, rule_intents, intents)
                        intents = planner_helpers.filter_spurious_intents(state, intents)
                        planner_call["output"] = {"intents": len(intents)}
                    else:
                        intents = rule_intents
                    intents = planner_helpers.filter_spurious_intents(state, intents)
                except Exception as exc:
                    intents = planner_helpers.detect_intents(content, state.get("image_info", {}))
                    intents = planner_helpers.filter_spurious_intents(state, intents)
                    planner_call = planner_call or {"name": "planner_brain_model", "input": {}}
                    planner_call["error"] = f"{type(exc).__name__}: {exc}"
            if planner_call:
                span["entry"]["tool_calls"] = [planner_call]

            actions = []
            for item in intents[:3]:
                actions.append(
                    {
                        "type": "skill",
                        "name": item["skill"],
                        "reason": item["reason"],
                        "priority": item["priority"],
                    }
                )

            primary = intents[0]
            route_result = {
                "scene": _infer_scene(primary["intent"]),
                "intent": primary["intent"],
                "subflow": _subflow_for_skill(primary["skill"]),
                "reason": f"当前消息触发{primary['reason']}，本轮采用轻量规划并最多处理三个主要意图。",
                "confidence": 0.72 if not state.get("errors") else 0.55,
                "need_human": primary["skill"] == "handoff",
            }
            action_plan = {
                "primary_goal": _primary_goal(intents),
                "detected_intents": intents[:3],
                "actions": actions,
                "reply_strategy": {
                    "max_messages": 3,
                    "must_answer": [item["intent"] for item in intents[:3]],
                    "may_guide_to": "项目了解或到店面诊",
                    "must_not": ["编造价格", "承诺效果", "透露工具过程", "生硬暴露AI身份"],
                },
                "confidence": route_result["confidence"],
            }
            output = {
                "intents": intents[:3],
                "route_result": route_result,
                "action_plan": action_plan,
                "trace": state.get("trace", []),
            }
            span["output_snapshot"] = output
            return output

    async def execute_actions(state: AgentState) -> dict[str, Any]:
        with trace_logger.node(state, "execute_actions", {"actions": state.get("action_plan", {}).get("actions", [])}) as span:
            content = state.get("normalized_content") or ""
            tool_results: dict[str, Any] = {}
            module_outputs: list[dict[str, Any]] = []
            tool_calls: list[dict[str, Any]] = []
            actions = state.get("action_plan", {}).get("actions", [])
            tool_tasks: list[tuple[str, dict[str, Any], Any]] = []

            for action in actions:
                skill = action.get("name")
                if skill == "handoff":
                    continue

                kb_name = KB_BY_SKILL.get(str(skill))
                if skill == "price_consult":
                    price_project = _canonical_price_project(_contextual_price_project(state) or _extract_project(content))
                    if not price_project or _is_broad_price_category(price_project):
                        kb_name = ""
                if kb_name:
                    query = _safe_query_from_state(state, skill)
                    call = {"name": "coze_kb_search", "input": {"kb_name": kb_name, "query": query}}
                    tool_tasks.append((kb_name, call, coze_client.search_kb(kb_name, query)))

                if skill == "price_consult":
                    price_project = _canonical_price_project(_contextual_price_project(state) or _extract_project(content))
                    if price_project and not _is_broad_price_category(price_project):
                        sql = _pricing_sql_from_state(state)
                        call = {"name": "coze_pricing_db", "input": {"sql": sql}}
                        tool_tasks.append(("pricing_db", call, coze_client.query_pricing_db(sql)))

                if skill == "store" and store_service:
                    if any(item.get("intent") == "trust_issue" for item in state.get("intents", [])) and not _has_store_inquiry(content):
                        tool_results["store_lookup"] = {"stores": [], "skipped": "trust_issue_without_store_query"}
                        tool_calls.append(
                            {
                                "name": "store_lookup",
                                "input": {"query": content},
                                "output": {"skipped": "trust_issue_without_store_query"},
                            }
                        )
                        continue
                    try:
                        store_query = _store_query_from_state(content, state)
                        result = store_service.search(store_query, customer_context=state.get("customer_context") or {})
                        tool_results["store_lookup"] = result
                        tool_calls.append({"name": "store_lookup", "input": {"query": store_query, "raw_query": content}, "output": result})
                    except Exception as exc:
                        tool_results["store_lookup"] = {"stores": [], "error": f"{type(exc).__name__}: {exc}"}
                        tool_calls.append({"name": "store_lookup", "input": {"query": content}, "error": f"{type(exc).__name__}: {exc}"})

                if skill == "appointment" and store_service:
                    try:
                        if _has_appointment_record_query(content) or _has_appointment_change_or_cancel(content):
                            tool_results["appointment_record_query"] = {"handled_by_cache": True}
                            tool_calls.append({"name": "appointment_record_query", "input": {"query": content}, "output": {"handled_by_cache": True}})
                            continue
                        store_query = _store_query_from_state(content, state)
                        lookup = tool_results.get("store_lookup") or store_service.search(store_query, customer_context=state.get("customer_context") or {})
                        if "store_lookup" not in tool_results:
                            tool_results["store_lookup"] = lookup
                            tool_calls.append({"name": "store_lookup", "input": {"query": store_query, "raw_query": content}, "output": lookup})
                        appointment_query = _appointment_query_from_state(content, lookup, state)
                        if appointment_query.get("store_id") and appointment_query.get("date"):
                            available = store_service.available_time(
                                store_id=str(appointment_query["store_id"]),
                                date=str(appointment_query["date"]),
                                customer_context=state.get("customer_context") or {},
                            )
                            available["store_name"] = appointment_query.get("store_name", "")
                            tool_results["available_time"] = available
                            tool_calls.append({"name": "available_time", "input": appointment_query, "output": available})
                        else:
                            tool_results["available_time"] = {"slots": {}, "missing": appointment_query.get("missing", [])}
                    except Exception as exc:
                        tool_results["available_time"] = {"slots": {}, "error": f"{type(exc).__name__}: {exc}"}
                        tool_calls.append({"name": "available_time", "input": {"query": content}, "error": f"{type(exc).__name__}: {exc}"})

            if tool_tasks:
                results = await asyncio.gather(*(task for _, _, task in tool_tasks), return_exceptions=True)
                for (key, call, _), result in zip(tool_tasks, results):
                    if isinstance(result, Exception):
                        call["error"] = f"{type(result).__name__}: {result}"
                        if key == "pricing_db":
                            tool_results[key] = {"rows": [], "error": call["error"]}
                        else:
                            tool_results[key] = {"kb_name": key, "items": [], "error": call["error"]}
                    elif key == "pricing_db":
                        rows = result if isinstance(result, list) else []
                        tool_results[key] = {"rows": rows[:10]}
                        call["output"] = {"rows": len(rows)}
                    else:
                        dumped = result.model_dump()
                        tool_results[key] = dumped
                        call["output"] = {"items": len(result.items)}
                    tool_calls.append(call)

            if any(action.get("name") == "price_consult" for action in actions):
                db_rows = tool_results.get("pricing_db", {}).get("rows") or []
                price_project = _canonical_price_project(_contextual_price_project(state) or _extract_project(content))
                if not db_rows and pricing_repository and price_project and not _is_broad_price_category(price_project):
                    pricing_query = _canonical_price_project(_contextual_price_project(state)) or content
                    local_call = {"name": "local_pricing_xlsx", "input": {"query": pricing_query}}
                    try:
                        local_rows = pricing_repository.search(pricing_query)
                        tool_results["pricing_local"] = {"rows": local_rows}
                        local_call["output"] = {"rows": len(local_rows)}
                    except Exception as exc:
                        local_call["error"] = f"{type(exc).__name__}: {exc}"
                        tool_results["pricing_local"] = {"rows": [], "error": local_call["error"]}
                    tool_calls.append(local_call)

            for action in actions:
                skill = action.get("name")
                if skill == "handoff":
                    module_outputs.append(
                        {
                            "skill": "handoff",
                            "intent": "human_request",
                            "facts": [],
                            "reply_points": ["这个情况需要专业同事协助确认。"],
                            "missing_slots": [],
                            "risk_flags": state.get("guardrail_result", {}).get("terms", []),
                            "suggested_next_step": "professional_assist",
                            "confidence": 0.9,
                        }
                    )
                    continue
                module_outputs.append(_skill_output(str(skill), content, tool_results, state))

            span["entry"]["tool_calls"] = tool_calls
            output = {"tool_results": tool_results, "module_outputs": module_outputs, "trace": state.get("trace", [])}
            span["output_snapshot"] = output
            return output

    async def synthesize_reply(state: AgentState) -> dict[str, Any]:
        with trace_logger.node(
            state,
            "synthesize_reply",
            {"action_plan": state.get("action_plan"), "module_outputs": state.get("module_outputs")},
        ) as span:
            model_call: dict[str, Any] | None = None
            try:
                if model_client and model_client.available and _should_use_model_reply(state):
                    tier = _reply_model_tier(state)
                    model_call = {"name": "reply_synthesizer_model", "input": {"tier": tier}}
                    payload = await model_client.chat_json(_reply_messages_for_model(state), tier=tier)
                    model_call["usage"] = _model_usage_snapshot(model_client)
                    messages = _validated_model_messages(payload)
                    if _model_reply_unsafe(state, messages):
                        repair_call: dict[str, Any] = {"name": "reply_repair_model", "input": {"tier": tier}}
                        try:
                            repair_payload = await model_client.chat_json(_reply_repair_messages_for_model(state, messages), tier=tier)
                            repair_call["usage"] = _model_usage_snapshot(model_client)
                            repaired_messages = _validated_model_messages(repair_payload)
                            if repaired_messages and not _model_reply_unsafe(state, repaired_messages):
                                messages = repaired_messages
                                model_call["fallback"] = "repaired_model_reply"
                            else:
                                messages = _compose_messages(state)
                                model_call["fallback"] = "unsafe_repair_fallback_template"
                        except Exception as repair_exc:
                            messages = _compose_messages(state)
                            repair_call["error"] = f"{type(repair_exc).__name__}: {repair_exc}"
                            model_call["fallback"] = "repair_error_fallback_template"
                        model_call.setdefault("nested_calls", []).append(repair_call)
                    model_call["output"] = {"messages": len(messages)}
                else:
                    messages = _compose_messages(state)
            except Exception as exc:
                messages = _compose_messages(state)
                model_call = model_call or {"name": "reply_synthesizer_model", "input": {}}
                model_call["error"] = f"{type(exc).__name__}: {exc}"
            messages = _postprocess_reply_messages(state, _attach_asset_images(state, messages))
            messages = _avoid_repeating_recent_reply(state, messages)
            if model_call:
                span["entry"]["tool_calls"] = [model_call]
            output = {"reply_messages": messages[:3], "trace": state.get("trace", [])}
            span["output_snapshot"] = output
            return output

    async def profile_event_extractor(state: AgentState) -> dict[str, Any]:
        with trace_logger.node(
            state,
            "profile_event_extractor",
            {"content": state.get("normalized_content"), "image_info": state.get("image_info"), "intents": state.get("intents")},
        ) as span:
            profile_update = _extract_profile_update(state)
            event_updates = _extract_event_updates(state, profile_update)
            memory_error = None
            saved_memory = {}
            if memory_store:
                try:
                    saved_memory = memory_store.save_update(
                        str(state.get("customer_id") or "unknown"),
                        profile_update=profile_update,
                        event_updates=event_updates,
                    )
                except Exception as exc:
                    memory_error = f"{type(exc).__name__}: {exc}"
            output = {
                "profile_update": profile_update,
                "event_updates": event_updates,
                "saved_memory": _compact_memory(saved_memory),
                "memory_error": memory_error,
                "trace": state.get("trace", []),
            }
            span["output_snapshot"] = output
            return output

    graph.add_node("normalize_input", normalize_input)
    graph.add_node("image_understanding", image_understanding_placeholder)
    graph.add_node("hard_guardrails", hard_guardrails)
    graph.add_node("load_memory", load_memory)
    graph.add_node("load_customer_context", load_customer_context)
    graph.add_node("planner_brain", planner_brain)
    graph.add_node("execute_actions", execute_actions)
    graph.add_node("synthesize_reply", synthesize_reply)
    graph.add_node("profile_event_extractor", profile_event_extractor)

    graph.add_edge(START, "normalize_input")
    graph.add_edge("normalize_input", "image_understanding")
    graph.add_edge("image_understanding", "hard_guardrails")
    graph.add_edge("hard_guardrails", "load_memory")
    graph.add_edge("load_memory", "load_customer_context")
    graph.add_edge("load_customer_context", "planner_brain")
    graph.add_edge("planner_brain", "execute_actions")
    graph.add_edge("execute_actions", "synthesize_reply")
    graph.add_edge("synthesize_reply", "profile_event_extractor")
    graph.add_edge("profile_event_extractor", END)

    return graph.compile()


def _detect_intents(content: str, image_info: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    image_info = image_info or {}
    pre_service_effect_concern = _is_pre_service_effect_concern(content)
    case_request = _has_case_request(content)
    project_process = _has_project_process_question(content)
    ad_price_check = _has_ad_price_check(content)
    if image_info.get("has_image"):
        image_intent = str(image_info.get("image_intent") or "")
        suggested_route = str(image_info.get("suggested_route") or "")
        if image_intent == "after_sales" or suggested_route == "SF12_after_sales":
            items.append({"intent": "after_sales", "skill": "after_sales", "priority": 1, "reason": "图片售后反馈"})
        elif image_intent == "competitor_compare" or suggested_route == "SF5_competitor_response":
            items.append({"intent": "competitor_compare", "skill": "competitor", "priority": 1, "reason": "图片竞品/报价咨询"})
        elif image_intent == "store_inquiry" or suggested_route == "SF6_store_match":
            items.append({"intent": "store_inquiry", "skill": "store", "priority": 1, "reason": "图片门店/地图咨询"})
        elif image_intent == "trust_issue" or suggested_route == "SF10_trust_build":
            items.append({"intent": "trust_issue", "skill": "trust_build", "priority": 1, "reason": "图片资质/产品信任咨询"})
        elif image_intent == "human_request" or suggested_route == "HUMAN_HANDOFF":
            items.append({"intent": "human_request", "skill": "handoff", "priority": 0, "reason": "图片包含高风险或需专业协助内容"})
        else:
            items.append({"intent": "image_inquiry", "skill": "project_consult", "priority": 1, "reason": "图片面诊咨询"})
    if any(word in content for word in TRUST_KEYWORDS):
        items.append({"intent": "trust_issue", "skill": "trust_build", "priority": 1, "reason": "信任或正规性顾虑"})
    if _is_identity_question(content):
        items.append({"intent": "trust_issue", "skill": "trust_build", "priority": 1, "reason": "客户询问身份和服务承接方式"})
    if _has_effect_guarantee_request(content):
        items.append({"intent": "trust_issue", "skill": "trust_build", "priority": 1, "reason": "客户要求效果保证或一次见效承诺"})
    if pre_service_effect_concern:
        items.append({"intent": "trust_issue", "skill": "trust_build", "priority": 1, "reason": "效果或被坑顾虑"})
    if any(word in content for word in COMPETITOR_KEYWORDS):
        items.append({"intent": "competitor_compare", "skill": "competitor", "priority": 2, "reason": "竞品或外部报价对比"})
    if _has_advantage_question(content):
        items.append({"intent": "trust_issue", "skill": "trust_build", "priority": 2, "reason": "询问品牌或服务优势"})
    if _has_price_objection(content):
        items.append({"intent": "price_inquiry", "skill": "price_consult", "priority": 2, "reason": "价格异议或议价"})
    elif ad_price_check:
        items.append({"intent": "ad_price_check", "skill": "price_consult", "priority": 2, "reason": "广告价、预约金或收费口径核对"})
    elif any(word in content for word in CAMPAIGN_KEYWORDS):
        items.append({"intent": "campaign_inquiry", "skill": "price_consult", "priority": 2, "reason": "活动或优惠咨询"})
    elif any(word in content for word in PRICE_KEYWORDS):
        items.append({"intent": "price_inquiry", "skill": "price_consult", "priority": 2, "reason": "价格咨询"})
    if any(word in content for word in AFTER_SALES_KEYWORDS) and not pre_service_effect_concern and not case_request:
        items.append({"intent": "after_sales", "skill": "after_sales", "priority": 2, "reason": "售后或恢复问题"})
    if _has_effect_dispute(content):
        items.append({"intent": "complaint_refund", "skill": "handoff", "priority": 0, "reason": "效果不满或纠纷倾向"})
    if _has_store_inquiry(content):
        items.append({"intent": "store_inquiry", "skill": "store", "priority": 3, "reason": "门店地址或路线咨询"})
    if _has_appointment_record_query(content) and not ad_price_check:
        items.append({"intent": "appointment_confirm", "skill": "appointment", "priority": 3, "reason": "查询已有预约记录"})
    elif any(word in content for word in APPOINTMENT_KEYWORDS) and not ad_price_check:
        items.append({"intent": "appointment_intent", "skill": "appointment", "priority": 3, "reason": "预约或到店意向"})
    if case_request:
        items.append({"intent": "case_request", "skill": "project_consult", "priority": 3, "reason": "案例或效果对比诉求"})
    if project_process:
        items.append({"intent": "project_process", "skill": "project_consult", "priority": 3, "reason": "项目流程或时长咨询"})
    if _has_project_consult_intent(content) or not items:
        items.append({"intent": "project_inquiry", "skill": "project_consult", "priority": 4, "reason": "项目咨询或普通咨询"})
    return _dedupe_intents(items)


def _has_project_consult_intent(content: str) -> bool:
    """Project names alone are not enough; otherwise simple price turns become noisy."""
    if _has_price_objection(content):
        return False
    if not any(word in content for word in PROJECT_KEYWORDS):
        return False
    consult_terms = [
        "适合",
        "效果",
        "原理",
        "恢复",
        "副作用",
        "维持",
        "推荐",
        "方案",
        "怎么弄",
        "怎么做",
        "做什么",
        "能不能做",
        "哪个好",
        "区别",
        "改善",
        "解决",
        "想淡斑",
        "想祛斑",
        "淡化",
        "去掉",
        "去除",
    ]
    return any(term in content for term in consult_terms)


def _has_case_request(content: str) -> bool:
    if not content:
        return False
    case_terms = ["案例", "效果案例", "前后对比", "对比照", "做完效果", "客户做完", "案例效果", "案例展示"]
    return any(term in content for term in case_terms)


def _has_project_process_question(content: str) -> bool:
    if not content:
        return False
    process_terms = ["流程", "操作流程", "怎么操作", "怎么做", "要做多久", "大概要多久", "多久能做完", "时长", "步骤", "过程"]
    return any(term in content for term in process_terms)


def _is_generic_project_intro(content: str) -> bool:
    if not content:
        return False
    if any(term in content for term in ["斑", "痘", "毛孔", "暗沉", "松弛", "抗衰", "价格", "多少钱", "门店", "预约", "案例"]):
        return False
    return any(term in content for term in ["了解一下项目", "了解下项目", "有什么项目", "有哪些项目", "介绍一下项目", "推荐个项目"])


def _is_unclear_need(content: str) -> bool:
    if not content:
        return False
    vague_terms = ["不知道要做啥", "不知道做啥", "不知道做什么", "没明确需求", "脸看着很累", "状态不好", "气色不好"]
    return any(term in content for term in vague_terms) and not any(term in content for term in ["多少钱", "价格", "预约", "门店"])


def _has_ad_price_check(content: str) -> bool:
    if not content:
        return False
    context_terms = ["广告", "直播", "团购", "预约金", "尾款", "隐形收费", "其他收费", "另收费", "包含什么", "包含哪些"]
    price_terms = PRICE_KEYWORDS + ["199", "299", "268", "10元", "定金", "订金"]
    return any(term in content for term in context_terms) and (
        any(term in content for term in price_terms) or bool(re.search(r"\d+\s*元?", content))
    )


def _has_appointment_record_query(content: str) -> bool:
    if not content:
        return False
    terms = ["我有没有预约", "我约的是", "约的是几点", "预约成功", "查一下预约", "查下预约", "是不是约了", "有没有约", "之前是不是约"]
    return any(term in content for term in terms)


def _has_appointment_change_or_cancel(content: str) -> bool:
    if not content:
        return False
    return any(term in content for term in ["取消预约", "帮我取消", "不去了", "明天不去", "改约", "改时间", "换个时间", "改到", "换到"])


def _has_price_objection(content: str) -> bool:
    if not content:
        return False
    if _has_effect_guarantee_request(content):
        return False
    return any(term in content for term in PRICE_OBJECTION_KEYWORDS)


def _is_identity_question(content: str) -> bool:
    if not content:
        return False
    return any(term in content for term in ["你是真人", "是AI", "是 ai", "机器人", "不是人", "客服是真人", "别骗我"]) and any(
        term in content for term in ["真人", "AI", "ai", "机器人", "骗"]
    )


def _has_effect_guarantee_request(content: str) -> bool:
    if not content:
        return False
    return any(term in content for term in ["保证一次有效", "保证有效", "一次有效", "一次见效", "包效果", "不保证就算了"])


def _has_minor_signal(content: str) -> bool:
    if not content:
        return False
    if any(term in content for term in ["未成年", "未满18", "不满18", "未满十八", "不满十八"]):
        return True
    match = re.search(r"(?<!\d)(1[0-7])\s*岁", content)
    if match:
        return True
    return any(term in content for term in ["十七岁", "十六岁", "十五岁", "十四岁", "十三岁", "十二岁"])


def _has_store_inquiry(content: str) -> bool:
    if _has_advantage_question(content):
        return False
    if _has_case_request(content):
        return False
    trust_terms = ["正规", "靠谱", "骗人", "被骗", "资质", "营业执照", "证照", "许可证", "真假", "隐形消费", "被坑", "安全", "售后"]
    if any(term in content for term in trust_terms):
        return False
    hard_store_terms = ["地址", "哪里", "附近", "停车", "导航", "怎么过去", "地铁", "营业", "哪家近", "离我近", "近吗", "近不近", "位置", "路线"]
    if any(term in content for term in hard_store_terms):
        return True
    if not any(term in content for term in ["门店", "店"]):
        return False
    appointment_terms = ["预约", "能约", "能去", "周六", "周日", "明天", "后天", "下午", "上午", "到店"]
    if any(term in content for term in trust_terms + appointment_terms):
        return False
    return True


def _has_advantage_question(content: str) -> bool:
    return any(term in content for term in ADVANTAGE_KEYWORDS)


def _is_store_city_followup(state: AgentState) -> bool:
    content = state.get("normalized_content") or ""
    if not _extract_city(content):
        return False
    if any(term in content for term in PRICE_KEYWORDS + TRUST_KEYWORDS + COMPETITOR_KEYWORDS + AFTER_SALES_KEYWORDS):
        return False
    if any(term in content for term in ["项目", "价格", "多少钱", "适合", "效果", "做什么", "能解决"]):
        return False
    recent = _recent_conversation_text(state, limit=6)
    store_context_terms = [
        "门店",
        "地址",
        "哪里",
        "哪家",
        "更方便",
        "城市",
        "区域",
        "附近",
        "导航",
        "停车",
        "店信息",
    ]
    return any(term in recent for term in store_context_terms)


def _recent_conversation_text(state: AgentState, limit: int = 6) -> str:
    history = state.get("conversation_history") or []
    return "\n".join(str(item) for item in history[-limit:])


def _complaint_terms(content: str) -> list[str]:
    if not content:
        return []
    if _is_identity_question(content):
        return []
    soft_trust_markers = ["是不是", "会不会", "怕", "担心", "感觉", "不靠谱", "靠不靠谱"]
    if any(prefix in content for prefix in ["是不是", "会不会", "怕", "担心"]) and not any(
        hard in content for hard in ["我要投诉", "要求退款", "退钱", "维权", "曝光", "起诉"]
    ):
        return []
    terms = [word for word in COMPLAINT_KEYWORDS if word in content]
    if terms and any(marker in content for marker in soft_trust_markers) and not any(
        hard in content for hard in ["我要投诉", "要求退款", "退钱", "维权", "曝光", "起诉", "骗我钱", "骗钱"]
    ):
        return []
    if "骗人" in content and not any(prefix in content for prefix in soft_trust_markers):
        terms.append("骗人")
    return _dedupe_strings(terms)


def _severe_after_sales_terms(content: str) -> list[str]:
    if not content:
        return []
    return [word for word in SEVERE_AFTER_SALES_KEYWORDS if word in content and not _is_negated_symptom(content, word)]


def _is_negated_symptom(content: str, symptom: str) -> bool:
    negations = ["没有", "没", "无", "不", "未", "并不", "不是"]
    for prefix in negations:
        if f"{prefix}{symptom}" in content:
            return True
    index = content.find(symptom)
    if index < 0:
        return False
    left = content[max(0, index - 4) : index]
    return any(neg in left for neg in negations)


def _denies_severe_after_sales(content: str) -> bool:
    return any(_is_negated_symptom(content, word) for word in SEVERE_AFTER_SALES_KEYWORDS)


def _has_effect_dispute(content: str) -> bool:
    if not content:
        return False
    if any(prefix in content for prefix in ["会不会", "怕", "担心", "有没有可能"]) and any(
        word in content for word in ["没效果", "没用", "被坑"]
    ):
        return False
    past_context = any(word in content for word in ["做了", "做完", "做的", "花了", "丢了", "付了", "买了"])
    if any(word in content for word in ["一点用都没", "没有用", "没用", "白做"]) and past_context:
        return True
    if "没效果" in content and past_context:
        return True
    if any(word in content for word in ["没有淡", "没淡"]) and any(word in content for word in ["斑", "色沉", "痘印"]) and past_context:
        return True
    if any(word in content for word in ["花了", "丢了", "花"]) and any(word in content for word in ["没效果", "没用", "没有淡", "没淡", "一点用都没"]):
        return True
    return False


def _has_recent_complaint_context(state: AgentState) -> bool:
    text = _recent_conversation_text(state)
    if not text:
        return False
    return bool(_complaint_terms(text) or _has_effect_dispute(text))


def _has_recent_competitor_context(state: AgentState) -> bool:
    text = _recent_conversation_text(state, limit=8)
    return any(word in text for word in COMPETITOR_KEYWORDS + ["对比", "报价截图", "别人报价", "竞品"])


def _is_image_following_complaint(state: AgentState) -> bool:
    content = (state.get("normalized_content") or "").strip()
    image_info = state.get("image_info") or {}
    has_known_image = _has_known_image_context(state)
    if not image_info.get("has_image") and content != "[图片]":
        return False
    return _has_recent_complaint_context(state)


def _is_pre_service_effect_concern(content: str) -> bool:
    if not content:
        return False
    soft_terms = [
        "会不会没效果",
        "会不会没有效果",
        "怕没效果",
        "怕没有效果",
        "担心没效果",
        "担心没有效果",
        "有没有效果",
        "怕被坑",
        "担心被坑",
        "会不会被坑",
        "怕乱收费",
        "隐形消费",
    ]
    if not any(term in content for term in soft_terms):
        return False
    past_or_done_terms = ["做完", "术后", "刚做", "已经做", "做了", "花了", "一点用都没", "没有淡", "没淡"]
    return not any(term in content for term in past_or_done_terms)


def _merge_intents(state: AgentState, rule_items: list[dict[str, Any]], model_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Let the model supplement deterministic routing, but never override clear current-message triggers."""
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(item: dict[str, Any]) -> None:
        intent = str(item.get("intent") or "")
        if not intent or intent in seen:
            return
        seen.add(intent)
        merged.append(item)

    for item in rule_items:
        add(item)
    for item in model_items:
        intent = str(item.get("intent") or "")
        if intent in seen or _model_intent_has_current_trigger(state, intent):
            add(item)
    return merged[:3] or _dedupe_intents(rule_items + model_items)


def _model_intent_has_current_trigger(state: AgentState, intent: str) -> bool:
    content = state.get("normalized_content") or ""
    image_info = state.get("image_info") or {}
    if intent == "image_inquiry":
        return bool(image_info.get("has_image"))
    if intent == "appointment_intent" and _has_ad_price_check(content):
        return False
    trigger_map = {
        "trust_issue": TRUST_KEYWORDS + ADVANTAGE_KEYWORDS,
        "competitor_compare": COMPETITOR_KEYWORDS + ADVANTAGE_KEYWORDS,
        "price_inquiry": PRICE_KEYWORDS,
        "ad_price_check": ["广告", "直播", "团购", "预约金", "尾款", "隐形收费", "其他收费", "另收费", "包含"],
        "campaign_inquiry": CAMPAIGN_KEYWORDS,
        "after_sales": AFTER_SALES_KEYWORDS,
        "store_inquiry": STORE_KEYWORDS,
        "appointment_intent": APPOINTMENT_KEYWORDS,
        "appointment_confirm": ["我有没有预约", "我约的是", "约的是几点", "预约成功", "查一下预约", "查下预约", "是不是约了", "有没有约"],
        "project_inquiry": PROJECT_KEYWORDS + ["斑", "点状", "片状", "痘印", "痘坑", "毛孔", "暗沉", "适合", "改善"],
        "case_request": ["案例", "效果案例", "前后对比", "对比照", "做完效果", "客户做完"],
        "project_process": ["流程", "操作流程", "怎么操作", "要做多久", "多久能做完", "时长", "步骤"],
    }
    return any(word in content for word in trigger_map.get(intent, []))


def _vision_prompt(state: AgentState) -> str:
    context = {
        "content": state.get("normalized_content"),
        "conversation_history": state.get("conversation_history", [])[-4:],
    }
    return (
        "你是企业微信医美客服系统中的通用图片理解节点。"
        "你不回复客户，不诊断，不推荐项目，只输出结构化JSON。"
        "请判断图片类型、业务意图、可见表层问题、风险信号和关键文字。"
        "如果是面部皮肤图，可以记录点状斑点、片状色沉、肤色不均、泛红、痘印、痘坑、毛孔明显等可见事实。"
        "不能写黄褐斑、皮炎、感染等诊断词，除非客户文字明确说出。"
        "如果是截图、报价、海报、地图、付款、报告，请提取关键文字，不输出完整手机号、身份证、银行卡号。"
        "最终只输出合法JSON，格式："
        "{\"info\":{\"has_image\":true,\"image_desc\":\"\",\"image_type\":\"face_skin|eye_area|face_shape|body_skin|post_treatment|competitor_quote|chat_screenshot|product_package|payment_proof|store_location|document_report|campaign_poster|qr_code|unrelated|unclear\","
        "\"image_intent\":\"face_consult|after_sales|competitor_compare|price_inquiry|campaign_inquiry|store_inquiry|trust_issue|human_request|general_image|unrelated\","
        "\"body_part\":\"\",\"visible_concerns\":[],\"risk_signals\":[],\"extracted_text\":[],\"text_clues\":[],\"suggested_route\":\"SF4_face_consult|SF5_competitor_response|SF6_store_match|SF7_price_consult|SF8_campaign_push|SF10_trust_build|SF12_after_sales|HUMAN_HANDOFF|DIRECT_REPLY|UNKNOWN\",\"confidence\":0}}。"
        f"客户上下文：{json_dumps(context)}"
    )


def _validated_image_info(payload: dict[str, Any], *, has_image: bool) -> dict[str, Any]:
    info = payload.get("info") if isinstance(payload.get("info"), dict) else payload
    if not isinstance(info, dict):
        raise ValueError("Vision JSON missing info")
    allowed_types = {
        "face_skin",
        "eye_area",
        "face_shape",
        "body_skin",
        "post_treatment",
        "competitor_quote",
        "chat_screenshot",
        "product_package",
        "payment_proof",
        "store_location",
        "document_report",
        "campaign_poster",
        "qr_code",
        "unrelated",
        "unclear",
    }
    allowed_intents = {
        "face_consult",
        "after_sales",
        "competitor_compare",
        "price_inquiry",
        "campaign_inquiry",
        "store_inquiry",
        "trust_issue",
        "human_request",
        "general_image",
        "unrelated",
    }
    image_type = str(info.get("image_type") or "unclear")
    image_intent = str(info.get("image_intent") or "general_image")
    confidence = info.get("confidence", 0.5)
    try:
        confidence_float = float(confidence)
    except (TypeError, ValueError):
        confidence_float = 0.5
    return {
        "has_image": has_image,
        "image_desc": str(info.get("image_desc") or "")[:500],
        "image_type": image_type if image_type in allowed_types else "unclear",
        "image_intent": image_intent if image_intent in allowed_intents else "general_image",
        "body_part": str(info.get("body_part") or "未知"),
        "visible_concerns": _list_of_strings(info.get("visible_concerns")),
        "risk_signals": _list_of_strings(info.get("risk_signals")),
        "extracted_text": _list_of_strings(info.get("extracted_text")),
        "text_clues": _list_of_strings(info.get("text_clues")),
        "suggested_route": str(info.get("suggested_route") or "UNKNOWN"),
        "confidence": max(0.0, min(1.0, confidence_float)),
    }


def _compact_memory(memory: dict[str, Any]) -> dict[str, Any]:
    if not memory:
        return {}
    return {
        "portrait_keys": list((memory.get("portrait") or {}).keys())[:12],
        "basic_info": memory.get("basic_info") or {},
        "history_events_count": len(memory.get("history_events") or []),
        "updated_at": memory.get("updated_at", ""),
    }


def _fallback_image_info(*, has_image: bool) -> dict[str, Any]:
    return {
        "has_image": has_image,
        "image_desc": "客户上传了图片，当前视觉模型未返回可用解析。" if has_image else "",
        "image_type": "unclear",
        "image_intent": "face_consult" if has_image else "unrelated",
        "body_part": "未知" if has_image else "无",
        "visible_concerns": [],
        "risk_signals": [],
        "extracted_text": [],
        "text_clues": [],
        "suggested_route": "SF4_face_consult" if has_image else "UNKNOWN",
        "confidence": 0.25 if has_image else 0,
    }


def _image_concern_terms(image_info: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for key in ["visible_concerns", "text_clues", "extracted_text"]:
        for value in image_info.get(key, []) or []:
            text = str(value).strip()
            if text:
                terms.append(text)
    desc = str(image_info.get("image_desc") or "").strip()
    if desc:
        terms.append(desc)
    return terms


def _has_image_concern(image_info: dict[str, Any], keywords: list[str]) -> bool:
    joined = " ".join(_image_concern_terms(image_info))
    return any(keyword in joined for keyword in keywords)


def _list_of_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item)[:200] for item in value[:10] if str(item).strip()]


def _should_use_model_planner(state: AgentState) -> bool:
    content = state.get("normalized_content") or ""
    if not content and not state.get("file_image"):
        return False
    return True


def _planner_model_tier(state: AgentState) -> str:
    content = state.get("normalized_content") or ""
    if any(word in content for word in AFTER_SALES_KEYWORDS + COMPETITOR_KEYWORDS + TRUST_KEYWORDS):
        return "balanced"
    return "fast"


def _planner_messages_for_model(state: AgentState) -> list[dict[str, Any]]:
    system = (
        "你是企业微信医美客服系统的轻量动作规划节点。"
        "你不回复客户，只判断本轮需要调用哪些业务skill。"
        "最多输出3个意图，按优先级排序。"
        "可选skill只能是：project_consult, price_consult, trust_build, competitor, after_sales, store, appointment。"
        "如果只是普通项目咨询，用project_consult；价格用price_consult；正规/靠谱/怕被骗用trust_build；别家/竞品用competitor。"
        "营业执照、资质、证照、许可证、机构是否正规属于trust_build，不属于store；客户没有问地址/附近/停车/路线时不要调用store。"
        "客户问“你们优势在哪里/为什么选你们/有什么不一样”，属于trust_build；如果上一轮明显在竞品对比，可用competitor。不要因为出现“哪里”误判成门店。"
        "如果上一轮在问门店/地址/哪家方便，客户本轮只补充城市如“我在上海/上海/人在上海”，必须用store。"
        "客户说太贵、贵了、便宜点、能不能优惠、最低价、底价、预算不够时，属于price_consult，不要归到project_consult。"
        "最终只输出合法JSON：{\"intents\":[{\"intent\":\"\",\"skill\":\"\",\"priority\":1,\"reason\":\"\"}]}"
    )
    user = {
        "content": state.get("normalized_content"),
        "conversation_history": state.get("conversation_history", [])[-6:],
        "image_info": state.get("image_info", {}),
        "customer_profile": state.get("customer_profile", {}),
        "history_events": state.get("history_events", [])[-6:],
        "appointment_cache": state.get("appointment_cache", {}),
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json_dumps(user)},
    ]


def _validated_planner_intents(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("intents")
    if not isinstance(raw, list) or not raw:
        raise ValueError("Planner JSON missing intents")
    allowed_skills = {"project_consult", "price_consult", "trust_build", "competitor", "after_sales", "store", "appointment"}
    result: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        skill = str(item.get("skill", "")).strip()
        if skill not in allowed_skills:
            continue
        intent = _intent_for_skill(skill)
        priority_raw = item.get("priority", len(result) + 1)
        try:
            priority = int(priority_raw)
        except (TypeError, ValueError):
            priority = len(result) + 1
        reason = str(item.get("reason") or "模型规划识别").strip()
        result.append({"intent": intent, "skill": skill, "priority": priority, "reason": reason[:80]})
        if len(result) >= 3:
            break
    if not result:
        raise ValueError("Planner JSON has no valid intents")
    return _dedupe_intents(result)


def _filter_spurious_intents(state: AgentState, intents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    image_info = state.get("image_info") or {}
    content = state.get("normalized_content") or ""
    has_current_competitor = any(word in content for word in COMPETITOR_KEYWORDS)
    has_current_trust = any(word in content for word in TRUST_KEYWORDS)
    has_price_objection = _has_price_objection(content)
    pre_service_effect_concern = _is_pre_service_effect_concern(content)
    if _has_effect_guarantee_request(content):
        intents = [item for item in intents if item.get("intent") != "price_inquiry"]
        if not any(item.get("intent") == "trust_issue" for item in intents):
            intents.append({"intent": "trust_issue", "skill": "trust_build", "priority": 1, "reason": "客户要求效果保证或一次见效承诺"})
    if _has_advantage_question(content):
        intents = [item for item in intents if item.get("intent") != "store_inquiry"]
        target_skill = "competitor" if _has_recent_competitor_context(state) else "trust_build"
        target_intent = "competitor_compare" if target_skill == "competitor" else "trust_issue"
        if not any(item.get("intent") == target_intent for item in intents):
            intents.append({"intent": target_intent, "skill": target_skill, "priority": 2, "reason": "客户询问优势或差异点"})
    if has_current_trust and not _has_store_inquiry(content):
        intents = [item for item in intents if item.get("intent") != "store_inquiry"]
        if not any(item.get("intent") == "trust_issue" for item in intents):
            intents.append({"intent": "trust_issue", "skill": "trust_build", "priority": 1, "reason": "客户当前表达资质或正规性顾虑"})
    if _is_store_city_followup(state):
        intents = [item for item in intents if item.get("intent") not in {"project_inquiry", "price_inquiry", "campaign_inquiry"}]
        if not any(item.get("intent") == "store_inquiry" for item in intents):
            intents.append({"intent": "store_inquiry", "skill": "store", "priority": 1, "reason": "承接上一轮门店查询补充城市"})
    if has_price_objection:
        intents = [item for item in intents if item.get("intent") != "project_inquiry"]
        if not any(item.get("intent") == "price_inquiry" for item in intents):
            intents.append({"intent": "price_inquiry", "skill": "price_consult", "priority": 2, "reason": "价格异议或议价"})
    if pre_service_effect_concern:
        intents = [item for item in intents if item.get("intent") != "after_sales"]
    if has_current_competitor and not has_current_trust:
        intents = [item for item in intents if item.get("intent") != "trust_issue"]
    if image_info.get("has_image") and image_info.get("image_intent") == "face_consult":
        allowed = {"image_inquiry", "project_inquiry"}
        if _has_recent_complaint_context(state):
            allowed.add("complaint_refund")
            allowed.add("after_sales")
        if any(word in content for word in PRICE_KEYWORDS):
            allowed.add("price_inquiry")
        if any(word in content for word in CAMPAIGN_KEYWORDS):
            allowed.add("campaign_inquiry")
        if any(word in content for word in APPOINTMENT_KEYWORDS):
            allowed.add("appointment_intent")
        if any(word in content for word in TRUST_KEYWORDS):
            allowed.add("trust_issue")
        if _has_store_inquiry(content):
            allowed.add("store_inquiry")
        if any(word in content for word in AFTER_SALES_KEYWORDS) and not pre_service_effect_concern:
            allowed.add("after_sales")
        filtered = [item for item in intents if item.get("intent") in allowed]
        if filtered:
            return _dedupe_intents(filtered)
    return _dedupe_intents(intents)


def _dedupe_intents(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _, item in sorted(enumerate(items), key=lambda pair: (_intent_rank(str(pair[1]["intent"])), int(pair[1]["priority"]), pair[0])):
        key = str(item["intent"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
        if len(deduped) >= 3:
            break
    return deduped


def _intent_rank(intent: str) -> int:
    return {
        "human_request": 0,
        "complaint_refund": 0,
        "after_sales": 1,
        "trust_issue": 2,
        "competitor_compare": 3,
        "ad_price_check": 4,
        "price_inquiry": 4,
        "campaign_inquiry": 4,
        "store_inquiry": 5,
        "appointment_intent": 6,
        "appointment_confirm": 6,
        "appointment_change": 6,
        "appointment_cancel": 6,
        "image_inquiry": 7,
        "project_inquiry": 8,
        "emotion_chat": 9,
    }.get(intent, 9)


def _skill_output(skill: str, content: str, tool_results: dict[str, Any], state: AgentState) -> dict[str, Any]:
    if skill == "price_consult":
        return _price_skill_output(content, tool_results, state)
    if skill == "trust_build":
        return _trust_skill_output(content, tool_results)
    if skill == "project_consult":
        return _project_skill_output(content, tool_results, state)
    if skill == "competitor":
        return _competitor_skill_output(content, tool_results)
    if skill == "after_sales":
        return _after_sales_skill_output(content, tool_results)
    if skill == "store":
        return _store_skill_output(content, tool_results)
    if skill == "appointment":
        return _basic_skill_output(skill, ["预约相关问题先回答客户当前问的是查预约、改约、取消，还是查某门店某天可约时间；只有客户明确问预约记录或改取消时才提醒已有预约。"], suggested_next_step="按当前预约诉求处理")
    return _basic_skill_output(skill, ["小贝先按客户当前问题做轻量承接。"])


def _after_sales_skill_output(content: str, tool_results: dict[str, Any]) -> dict[str, Any]:
    items = tool_results.get("after_sales_qa", {}).get("items", [])
    parsed = _first_after_sales_slice(items)
    risk_level = parsed.get("risk_level", "")
    say = parsed.get("say", "")
    collect = parsed.get("collect", "")
    next_step = parsed.get("next_step", "")

    reply_points: list[str] = []
    facts: list[str] = []
    missing_slots: list[str] = []
    risk_flags: list[str] = []

    if risk_level:
        facts.append(f"风险等级：{risk_level}")
        if risk_level in {"高", "严重"}:
            risk_flags.append(risk_level)
    if say:
        reply_points.append(_clean_after_sales_text(say))
    else:
        reply_points.append("做完后的反应要结合项目、时间和照片看，小贝先帮你把情况问清楚。")
    if collect:
        missing_slots = _split_collect_items(collect)[:5]
    else:
        missing_slots = ["项目", "做完第几天", "现在主要表现", "是否加重", "照片"]
    if next_step:
        facts.append(f"下一步：{next_step}")

    return {
        "skill": "after_sales",
        "intent": "after_sales",
        "facts": facts,
        "reply_points": reply_points,
        "missing_slots": missing_slots,
        "risk_flags": risk_flags,
        "suggested_next_step": next_step or "补充项目、时间和照片",
        "confidence": 0.78 if parsed else 0.65,
    }


def _competitor_skill_output(content: str, tool_results: dict[str, Any]) -> dict[str, Any]:
    items = tool_results.get("competitor_qa", {}).get("items", [])
    parsed = _first_competitor_slice(items)
    say = parsed.get("say", "")
    collect = parsed.get("collect", "")
    next_step = parsed.get("next_step", "")
    target = parsed.get("target", "")
    forbidden = parsed.get("forbidden", "")
    scenario = _competitor_scenario(content)
    scene_type = parsed.get("scene_type", "")

    project = _extract_project(content)
    price_digits = _extract_price_digits(content)
    reply_points: list[str] = []
    facts: list[str] = []
    missing_slots: list[str] = []

    if target:
        facts.append(f"回复目标：{target}")
    if forbidden:
        facts.append(f"禁用表达：{forbidden}")
    if say and _competitor_slice_matches(scenario, scene_type, say):
        reply_points.append(_clean_competitor_text(say))
    else:
        reply_points.append(_competitor_default_reply(content, project, price_digits, scenario))
    if collect:
        missing_slots = _split_collect_items(collect)[:5]
    elif "截图" in content or "报价" in content:
        missing_slots = ["项目", "产品/剂量", "部位", "次数", "是否含售后"]
    if next_step:
        facts.append(f"下一步：{next_step}")

    return {
        "skill": "competitor",
        "intent": "competitor_compare",
        "facts": facts,
        "reply_points": reply_points,
        "missing_slots": missing_slots,
        "risk_flags": _competitor_risk_terms(content),
        "suggested_next_step": next_step or "拆清对比维度",
        "confidence": 0.78 if parsed else 0.68,
    }


def _first_competitor_slice(items: list[Any]) -> dict[str, str]:
    for item in items:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "")
        if not content:
            continue
        parsed = {
            "scene_type": _extract_label(content, "场景类型"),
            "target": _extract_label(content, "回复目标"),
            "say": _extract_label(content, "可说话术"),
            "collect": _extract_label(content, "需收集信息"),
            "forbidden": _extract_label(content, "禁用表达"),
            "next_step": _extract_label(content, "下一步动作"),
        }
        if any(parsed.values()):
            return parsed
    return {}


def _clean_competitor_text(text: str) -> str:
    text = text.strip()
    text = text.replace("我这边", "小贝这边")
    text = text.replace("我们", "我们这边")
    return text


def _competitor_scenario(content: str) -> str:
    if any(word in content for word in ["一次见效", "一次就", "淡很多", "包效果", "保证", "承诺"]):
        return "effect_claim"
    if any(word in content for word in ["199", "299", "更便宜", "同价", "做到这个价", "低价"]):
        return "price_compare"
    if any(word in content for word in ["报价", "截图", "套餐"]):
        return "quote_compare"
    if any(word in content for word in ["更好", "朋友说", "案例"]):
        return "positive_competitor"
    if any(word in content for word in ["坑", "套路", "隐形消费"]):
        return "fear_trap"
    return "general_compare"


def _competitor_slice_matches(scenario: str, scene_type: str, say: str) -> bool:
    text = f"{scene_type} {say}"
    if scenario == "price_compare":
        return any(word in text for word in ["price", "低价", "便宜", "同价", "价格", "报价"])
    if scenario == "effect_claim":
        return any(word in text for word in ["effect", "效果", "承诺", "保证", "一次见效"])
    if scenario == "quote_compare":
        return any(word in text for word in ["quote", "报价", "截图", "套餐"])
    if scenario == "fear_trap":
        return any(word in text for word in ["trap", "坑", "套路", "隐形消费"])
    if scenario == "positive_competitor":
        return any(word in text for word in ["positive", "更好", "朋友", "案例"])
    return True


def _competitor_default_reply(content: str, project: str, price_digits: list[str], scenario: str) -> str:
    if scenario == "price_compare":
        price_text = price_digits[0] if price_digits else ""
        if project and price_text:
            return f"能理解你会拿{project}{price_text}这个价格来对比，但这类项目不能只看项目名和数字，还要看产品、剂量、部位、次数和售后是不是一致。"
        return "能理解你会对比价格，但医美项目不能只看项目名和数字，还要看产品、剂量、部位、次数和售后是不是一致。"
    if scenario == "effect_claim":
        return "效果这块小贝不建议按“一次见效”或“保证效果”来判断，医美效果和个人基础、方案匹配、操作细节、恢复护理都有关系，先评估清楚会更稳。"
    if scenario == "quote_compare":
        return "报价截图可以看，但小贝建议先拆清楚项目、产品/剂量、部位、次数、操作人员和售后，不能只按一个总价判断划不划算。"
    if scenario == "fear_trap":
        return "担心被坑可以理解，重点看价格是否透明、有没有隐形加项、产品来源是否清楚、售后怎么跟进。"
    return "你多对比一下是对的，小贝不评价别家好坏，主要帮你把项目配置、价格包含项和后续服务拆清楚。"


def _extract_price_digits(content: str) -> list[str]:
    return re.findall(r"\d+(?:\.\d+)?", content or "")


def _competitor_risk_terms(content: str) -> list[str]:
    terms = []
    for word in ["别家", "同价", "更便宜", "最低", "底价", "包效果", "一次见效", "保证有效"]:
        if word in content:
            terms.append(word)
    terms.extend(_extract_price_digits(content)[:2])
    return _dedupe_strings(terms)


def _first_after_sales_slice(items: list[Any]) -> dict[str, str]:
    for item in items:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "")
        if not content:
            continue
        parsed = {
            "risk_level": _extract_label(content, "风险等级"),
            "say": _extract_label(content, "可说话术"),
            "collect": _extract_label(content, "需收集信息"),
            "next_step": _extract_label(content, "下一步动作"),
            "scene_type": _extract_label(content, "场景类型"),
        }
        if any(parsed.values()):
            return parsed
    return {}


def _clean_after_sales_text(text: str) -> str:
    text = text.strip()
    text = text.replace("我这边", "小贝这边")
    text = text.replace("护理老师", "专业同事")
    return text


def _split_collect_items(text: str) -> list[str]:
    normalized = re.sub(r"[，、/；;]", ",", text)
    return [item.strip(" 。") for item in normalized.split(",") if item.strip(" 。")]


def _store_skill_output(content: str, tool_results: dict[str, Any]) -> dict[str, Any]:
    lookup = tool_results.get("store_lookup", {})
    stores = lookup.get("stores", []) if isinstance(lookup, dict) else []
    facts: list[str] = []
    reply_points: list[str] = []
    missing_slots: list[str] = []
    if stores:
        for store in stores[:3]:
            if not isinstance(store, dict):
                continue
            facts.append(f"{store.get('name')}：{store.get('address')}")
        city = lookup.get("city") or _extract_city(content)
        if len(stores) == 1:
            reply_points.append(f"{stores[0].get('name')}在{stores[0].get('address')}。")
        else:
            reply_points.append(f"{city or '这边'}目前匹配到{len(stores)}家门店，可以先发你门店信息。")
    else:
        city = lookup.get("city") or _extract_city(content)
        if city:
            reply_points.append(f"{city}目前没有匹配到可用门店信息，不能拿其他城市门店代替回复。")
        else:
            missing_slots.append("城市或门店")
            reply_points.append("小贝需要先确认你所在城市或想看的门店，才能准确发地址、导航和停车信息。")
    return {
        "skill": "store",
        "intent": "store_inquiry",
        "facts": facts,
        "reply_points": reply_points,
        "missing_slots": missing_slots,
        "risk_flags": [],
        "suggested_next_step": "发送门店地址/导航/停车信息" if stores else "确认城市或门店",
    }


def _price_skill_output(content: str, tool_results: dict[str, Any], state: AgentState | None = None) -> dict[str, Any]:
    kb_rows = _pricing_rows_from_kb(tool_results)
    kb_items = tool_results.get("project_price", {}).get("items", [])
    project = _canonical_price_project(_contextual_price_project(state or {}) or _extract_project(content))
    if _is_broad_price_category(project):
        return {
            "skill": "price_consult",
            "intent": "price_inquiry",
            "facts": ["客户当前询问的是斑点/色沉等大类改善价格，不能拿具体商品价替代。"],
            "reply_points": [
                "斑点/色沉类价格不能只按大类报固定价，要看斑型、范围、深浅、次数和最终配置；可以先按预算范围沟通，但不要引用不相关项目价格。",
            ],
            "missing_slots": ["斑型或照片", "预算范围"],
            "risk_flags": _price_risk_terms(content),
            "suggested_next_step": "先确认斑型范围或预算，再匹配具体配置",
            "confidence": 0.7,
        }
    rows = _filter_pricing_rows_for_project(kb_rows or _pricing_rows(tool_results), project)
    facts: list[str] = []
    reply_points: list[str] = []
    missing_slots: list[str] = []

    if rows:
        row = rows[0]
        name = str(row.get("project_name") or project or "相关项目")
        price_bits = _price_bits(row)
        facts.extend(price_bits)
        source = row.get("_source") or ("local_xlsx" if tool_results.get("pricing_local", {}).get("rows") else "coze_db")
        if _has_price_objection(content):
            reply_points.append(_price_objection_point(name, row, project))
        elif price_bits:
            if source == "project_price_kb":
                reply_points.append(_price_point_from_kb_row(row, project, ""))
            else:
                reply_points.append(_price_point_from_row(name, row, requested_project=project, source=source))
        else:
            reply_points.append(f"{name}已查到项目记录，但当前价格字段不完整，需要按配置确认。")
    elif kb_items:
        facts.append(f"价格知识库命中{len(kb_items)}条结果")
        if _has_price_objection(content) and project:
            reply_points.append(f"能理解你会在意预算。{project}小贝查到了相关配置说明，但没有明确可引用的价格字段，所以不能直接承诺降价或报底价。")
        elif project:
            reply_points.append(f"{project}小贝查到了相关配置说明，但没有明确可引用的价格字段，所以先不乱报数字。")
        else:
            reply_points.append("价格需要先明确具体项目或项目方向，小贝再按对应配置帮你确认。")
    else:
        facts.append("暂未查到明确可引用价格")
        if _has_price_objection(content) and project:
            reply_points.append(f"能理解你觉得预算压力大。小贝这边暂时没查到{project}的明确价格，不能直接承诺降价或拿别的项目价格替代。")
        elif project:
            reply_points.append(f"小贝这边暂时没查到{project}的明确单次价格，不能拿别的项目价格代替报价。")
        else:
            reply_points.append("价格要看具体项目和配置，小贝先不乱报数字。")

    if not project and not rows:
        missing_slots.append("项目名称")

    return {
        "skill": "price_consult",
        "intent": "price_inquiry",
        "facts": facts,
        "reply_points": reply_points,
        "missing_slots": missing_slots,
        "risk_flags": _price_risk_terms(content),
        "suggested_next_step": "预算异议承接" if _has_price_objection(content) else "确认项目配置" if project or rows else "补充项目名称",
        "confidence": 0.78 if rows else 0.62,
    }


def _trust_skill_output(content: str, tool_results: dict[str, Any]) -> dict[str, Any]:
    items = tool_results.get("trust_assets", {}).get("items", [])
    facts = [f"资质/背书资料命中{len(items)}条"] if items else ["暂未命中可直接引用的资质/背书资料"]
    reply_points = [
        "客户担心正规性时先认可谨慎，再从资质、产品来源、服务保障几个维度解释。",
        "如果有可用图片资料，只能发送知识库返回的真实图片链接，不编造资质或案例。",
    ]
    if "AI" in content or "ai" in content or "机器人" in content:
        reply_points.append("客户询问身份时，不要争辩身份，保持小贝服务口吻并说明会同步专业同事确认具体问题。")

    return {
        "skill": "trust_build",
        "intent": "trust_issue",
        "facts": facts,
        "reply_points": reply_points,
        "missing_slots": [],
        "risk_flags": [],
        "suggested_next_step": "提供资质和服务保障说明",
        "confidence": 0.76 if items else 0.58,
    }


def _project_skill_output(content: str, tool_results: dict[str, Any], state: AgentState) -> dict[str, Any]:
    items = tool_results.get("project_qa", {}).get("items", [])
    image_info = state.get("image_info", {})
    facts = [f"项目知识库命中{len(items)}条"] if items else ["暂未命中明确项目知识库结果"]
    if image_info.get("has_image"):
        if image_info.get("visible_concerns"):
            facts.append(f"图片可见问题：{', '.join(map(str, image_info.get('visible_concerns', [])[:5]))}")
        else:
            facts.append("客户本轮包含图片，但视觉模型未返回明确可见问题")
    visible = image_info.get("visible_concerns") or []
    reply_points = ["项目咨询应从客户需求和可见问题切入，不强迫客户先说专业项目名。"]
    if visible:
        reply_points.append(f"必须承接已上传图片：可见{', '.join(map(str, visible[:4]))}，不要再要求客户重复发照片。")
    if _has_image_concern(image_info, ["点状斑", "褐色斑点", "色沉", "肤色不均", "斑点"]):
        reply_points.append("项目方向优先说明：光子嫩肤偏肤色不均、泛红暗沉和浅层色沉；皮秒/祛斑类偏更明确的点状色素，最终看深浅、范围和皮肤耐受。")
    if "点状" in content or "斑" in content:
        reply_points.append("客户提到点状斑或斑点时，可围绕斑型、深浅、范围和是否适合光电类方向继续沟通。")
    return {
        "skill": "project_consult",
        "intent": "project_inquiry",
        "facts": facts,
        "reply_points": reply_points,
        "missing_slots": [],
        "risk_flags": [],
        "suggested_next_step": "补充需求或照片",
        "confidence": 0.7,
    }


def _basic_skill_output(
    skill: str,
    reply_points: list[str],
    *,
    suggested_next_step: str = "",
    facts: list[str] | None = None,
    risk_flags: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "skill": skill,
        "intent": _intent_for_skill(skill),
        "facts": facts or [],
        "reply_points": reply_points,
        "missing_slots": [],
        "risk_flags": risk_flags or [],
        "suggested_next_step": suggested_next_step,
        "confidence": 0.7,
    }


def _compose_messages(state: AgentState) -> list[dict[str, Any]]:
    outputs = state.get("module_outputs", [])
    intents = [item.get("intent") for item in state.get("intents", [])]
    messages: list[dict[str, Any]] = []

    route_result = state.get("route_result") or {}
    if (
        any(output.get("skill") == "handoff" for output in outputs)
        or any(item.get("skill") == "handoff" for item in state.get("intents", []) if isinstance(item, dict))
        or route_result.get("subflow") == "HUMAN_HANDOFF"
        or route_result.get("need_human") is True
    ):
        return [
            {
                "type": "text",
                "order": 1,
                "content": _handoff_message(state),
            }
        ]

    appointment_context = _appointment_context_sentence(state) if _should_show_appointment_context(state) else ""

    if "after_sales" in intents:
        messages.append({"type": "text", "order": len(messages) + 1, "content": _after_sales_message(state)})

    if "trust_issue" in intents and len(messages) < 3:
        messages.append({"type": "text", "order": len(messages) + 1, "content": _trust_message(state)})

    if "competitor_compare" in intents and len(messages) < 3:
        messages.append(
            {
                "type": "text",
                "order": len(messages) + 1,
                "content": _competitor_message(state),
            }
        )

    if "image_inquiry" in intents and len(messages) < 3 and not any(intent in intents for intent in ["trust_issue", "price_inquiry", "campaign_inquiry"]):
        image_info = state.get("image_info") or {}
        image_content = _project_message(state) if image_info.get("visible_concerns") else "图片小贝先收到了。如果你是想看皮肤或项目方向，我会结合照片和你想改善的重点一起看。"
        messages.append(
            {
                "type": "text",
                "order": len(messages) + 1,
                "content": image_content,
            }
        )

    if ("price_inquiry" in intents or "campaign_inquiry" in intents) and len(messages) < 3:
        messages.append({"type": "text", "order": len(messages) + 1, "content": _price_message(state)})

    if "project_inquiry" in intents and _should_add_project_guidance(state) and len(messages) < 3:
        messages.append({"type": "text", "order": len(messages) + 1, "content": _project_message(state)})

    if "store_inquiry" in intents and len(messages) < 3:
        messages.extend(_store_messages(state, start_order=len(messages) + 1))
        if _should_add_project_guidance(state) and len(messages) < 3 and not any(
            intent in intents for intent in ["price_inquiry", "campaign_inquiry", "competitor_compare", "trust_issue", "after_sales"]
        ):
            messages.append({"type": "text", "order": len(messages) + 1, "content": _project_message(state)})

    has_available_time = bool(state.get("tool_results", {}).get("available_time"))
    appointment_intents = {"appointment_intent", "appointment_confirm", "appointment_change", "appointment_cancel"}
    if (set(intents) & appointment_intents) and len(messages) < 3 and (has_available_time or not any(message.get("content") == appointment_context for message in messages)):
        messages.append({"type": "text", "order": len(messages) + 1, "content": _appointment_message(state)})
        if appointment_context and len(messages) < 3 and not any(message.get("content") == appointment_context for message in messages):
            messages.append({"type": "text", "order": len(messages) + 1, "content": appointment_context})

    memory_context = _memory_context_sentence(state)
    if (
        memory_context
        and not appointment_context
        and len(messages) < 3
        and any(intent in intents for intent in ["price_inquiry", "project_inquiry", "image_inquiry"])
    ):
        messages.append({"type": "text", "order": len(messages) + 1, "content": memory_context})

    if "project_inquiry" in intents and not any(
        intent in intents for intent in ["trust_issue", "price_inquiry", "campaign_inquiry", "competitor_compare", "after_sales", "image_inquiry"]
    ):
        messages.append({"type": "text", "order": len(messages) + 1, "content": _project_message(state)})

    if not messages:
        messages.append(
            {
                "type": "text",
                "order": 1,
    "content": "小贝先按你这句来理解，可以继续帮你看项目方向、价格或门店信息。",
            }
        )

    return _postprocess_reply_messages(state, _renumber(messages))


def _postprocess_reply_messages(state: AgentState, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Final guardrail for repeated questions and known bad customer-facing patterns."""
    image_info = state.get("image_info") or {}
    has_known_image = _has_known_image_context(state)
    intents = {item.get("intent") for item in state.get("intents", [])}
    content_text = state.get("normalized_content") or ""
    price_objection = _has_price_objection(content_text)
    has_available_time_result = bool(state.get("tool_results", {}).get("available_time"))
    cleaned: list[dict[str, Any]] = []
    seen_text: set[str] = set()
    for message in messages:
        if not isinstance(message, dict):
            continue
        msg_type = message.get("type") if message.get("type") in {"text", "image"} else "text"
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        if msg_type == "text" and _has_actual_image_context(state) and not intents & {"human_request", "complaint_refund", "after_sales"} and _asks_for_duplicate_photo(content):
            continue
        if msg_type == "text" and "price_inquiry" in intents and _is_vague_price_deferral(content):
            continue
        if msg_type == "text" and price_objection and _is_project_only_after_price_objection(content):
            continue
        if msg_type == "text" and _is_redundant_known_goal_question(state, content):
            continue
        if msg_type == "text" and not intents & {"appointment_intent", "appointment_confirm", "appointment_change", "appointment_cancel"}:
            if any(term in content for term in ["哪天方便到店", "方便到店", "到店面诊", "约个面诊", "约个时间到店", "面诊下皮肤状态"]):
                continue
        if msg_type == "text" and not _should_show_appointment_context(state):
            if any(term in content for term in ["已有预约", "已有预约记录", "预约记录：", "你这边已有预约"]):
                continue
        if msg_type == "text" and has_available_time_result and _looks_like_store_list_message(content):
            if any(re.search(r"\d{1,2}:\d{2}", str(item.get("content") or "")) for item in cleaned):
                continue
        if msg_type == "text":
            content = _repair_appointment_commitment(content)
            normalized = re.sub(r"\s+", "", content)
            if normalized in seen_text:
                continue
            seen_text.add(normalized)
        cleaned.append({"type": msg_type, "order": len(cleaned) + 1, "content": content})
        if msg_type == "text" and price_objection and _has_budget_or_price_answer(content):
            break
        if msg_type == "text" and "price_inquiry" in intents and _asks_daily_single_price(state.get("normalized_content") or "") and re.search(r"\d+\s*元?", content):
            break
        if len(cleaned) >= 3:
            break

    if cleaned:
        cleaned = _ensure_specific_intent_answer(state, cleaned)
        cleaned = _repair_weak_competitor_reply(state, cleaned)
        cleaned = _repair_spot_price_context_reply(state, cleaned)
        cleaned = _ensure_store_answer(state, cleaned)
        cleaned = _ensure_pre_visit_answer(state, cleaned)
        cleaned = _sanitize_sensitive_reply_content(state, cleaned)
        if not cleaned:
            return _renumber(_fallback_messages_after_filter(state))
        if _model_reply_unsafe(state, cleaned):
            return _renumber(_fallback_messages_after_filter(state))
        return _renumber(cleaned)
    return _renumber(_fallback_messages_after_filter(state))


def _fallback_messages_after_filter(state: AgentState) -> list[dict[str, Any]]:
    intents = {item.get("intent") for item in state.get("intents", [])}
    if "ad_price_check" in intents:
        return _ad_price_check_messages(state)
    if "project_process" in intents:
        return _project_process_messages(state)
    if "case_request" in intents:
        return _case_request_messages(state)
    if "competitor_compare" in intents:
        return [{"type": "text", "order": 1, "content": _competitor_message(state)}]
    if "trust_issue" in intents:
        messages = [{"type": "text", "order": 1, "content": _trust_message(state)}]
        if ("price_inquiry" in intents or "campaign_inquiry" in intents) and len(messages) < 3:
            messages.append({"type": "text", "order": 2, "content": _price_message(state)})
        return messages
    if intents & {"appointment_intent", "appointment_confirm", "appointment_change", "appointment_cancel"}:
        return [{"type": "text", "order": 1, "content": _appointment_message(state)}]
    if _has_pre_visit_question(state.get("normalized_content") or ""):
        return [{"type": "text", "order": 1, "content": _pre_visit_message(state)}]
    if "price_inquiry" in intents:
        return [{"type": "text", "order": 1, "content": _price_message(state)}]
    if "project_inquiry" in intents or "image_inquiry" in intents:
        return [{"type": "text", "order": 1, "content": _project_message(state)}]
    return [{"type": "text", "order": 1, "content": "小贝先按你当前问题帮你看，能确认的信息我会直接说清楚。"}]


def _sanitize_sensitive_reply_content(state: AgentState, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    allow_project_names = _allows_specific_project_names(state)
    intents = {item.get("intent") for item in state.get("intents", [])}
    license_doc_request = _is_license_doc_request(state.get("normalized_content") or "")
    for message in messages:
        if (license_doc_request or "trust_issue" in intents) and isinstance(message, dict) and message.get("type") == "image":
            continue
        if not isinstance(message, dict) or message.get("type") != "text":
            sanitized.append(message)
            continue
        content = str(message.get("content") or "")
        content = _sanitize_license_promise(content, strict=license_doc_request or "trust_issue" in intents)
        if not allow_project_names:
            content = _sanitize_unasked_project_names(content)
        if content.strip():
            sanitized.append({**message, "content": content})
    return sanitized


def _is_license_doc_request(content: str) -> bool:
    return any(term in content for term in ["营业执照", "执照", "证照", "许可证", "资质"]) and any(
        term in content for term in ["发", "给我看", "看看", "看一下", "直接"]
    )


def _sanitize_license_promise(text: str, *, strict: bool = False) -> str:
    replacements = {
        "我把营业执照发你": "资质类材料我可以帮你按正规性维度说明，具体证照以门店/官方渠道核验为准",
        "把营业执照发你": "帮你说明资质核验方式",
        "发送营业执照": "说明资质核验方式",
        "发营业执照": "说明资质核验方式",
        "营业执照发你": "资质信息按官方渠道核验",
        "直接发执照": "按官方渠道核验证照",
        "资质材料发你": "资质材料以门店/官方渠道核验为准",
        "发你核对": "通过门店/官方渠道核验",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    if strict:
        text = re.sub(r"https?://\S+", "", text).strip()
        text = text.replace("（📎附图）", "").replace("📎附图", "")
        text = text.replace("已上传", "可通过门店/官方渠道核验")
        text = text.replace("稍后发你样本图", "具体以门店/官方渠道核验为准")
        text = text.replace("马上帮你联系就近门店", "建议按门店/官方渠道")
        text = text.replace("让店长直接", "")
        text = text.replace("所有门店都是持证合规经营的", "门店资质建议以现场或官方渠道核验")
        text = text.replace("正规注册的医美机构", "资质建议以现场或官方渠道核验")
        text = text.replace("产品授权书", "产品来源信息")
        text = text.replace("器械备案信息", "器械备案信息可通过官方渠道核验")
        if any(term in text for term in ["医疗机构执业许可证", "营业执照", "执业许可证"]):
            return "你要核验证照这个诉求小贝理解，但这类材料我这边不直接发图片或截图。可以先帮你按资质核验、产品来源和服务保障这几块说明；具体证照建议以门店现场或官方渠道核验为准。"
    return text


def _allows_specific_project_names(state: AgentState) -> bool:
    content = state.get("normalized_content") or ""
    history = " ".join(str(item) for item in state.get("conversation_history", [])[-6:])
    text = f"{content} {history}"
    specific = ["光子嫩肤", "光子", "皮秒", "水光", "热玛吉", "超声炮", "水杨酸"]
    if any(name in text for name in specific):
        return True
    intents = {item.get("intent") for item in state.get("intents", [])}
    if intents & {"price_inquiry", "campaign_inquiry"} and _contextual_price_project(state):
        return True
    return False


def _sanitize_unasked_project_names(text: str) -> str:
    replacements = [
        ("光子嫩肤", "肤色改善类光电项目"),
        ("光子", "肤色改善类光电方向"),
        ("皮秒/祛斑类", "针对性色素淡化类"),
        ("皮秒或祛斑类", "针对性色素淡化类"),
        ("皮秒", "针对性色素淡化方向"),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    text = re.sub(r"比如\s*(针对性色素淡化方向|肤色改善类光电方向)这类", "比如更偏淡斑的方向", text)
    return text


def _ensure_specific_intent_answer(state: AgentState, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    intents = {item.get("intent") for item in state.get("intents", [])}
    joined = "\n".join(str(item.get("content") or "") for item in messages if item.get("type") == "text")
    if "ad_price_check" in intents and _model_reply_unsafe(state, messages):
        return _ad_price_check_messages(state)
    if "project_process" in intents and _model_reply_unsafe(state, messages):
        return _project_process_messages(state)
    if "case_request" in intents and _model_reply_unsafe(state, messages):
        return _case_request_messages(state)
    if "case_request" in intents and "案例" not in joined and "同类" not in joined:
        return _case_request_messages(state)
    return messages


def _case_request_messages(state: AgentState) -> list[dict[str, Any]]:
    content = state.get("normalized_content") or ""
    project = _extract_project(content) or ("祛斑" if "斑" in content else "")
    subject = f"{project}的" if project else "同类"
    return [
        {
            "type": "text",
            "order": 1,
            "content": f"可以的，小贝先按{subject}效果案例帮你承接。案例更适合看同类问题的改善参考，比如斑点深浅、范围、肤色不均这些变化。",
        },
        {
            "type": "text",
            "order": 2,
            "content": "如果需要真实前后对比图，我这边只发门店可提供的案例素材，不会随便编案例；你也可以告诉我想看点状斑还是片状色沉的参考。",
        },
    ]


def _project_process_messages(state: AgentState) -> list[dict[str, Any]]:
    project = _extract_project(state.get("normalized_content") or "") or "这类项目"
    return [
        {
            "type": "text",
            "order": 1,
            "content": f"{project}一般流程是：到店先做清洁和皮肤检测，再确认方案，之后进行操作，最后会交代修复和防晒注意事项。",
        },
        {
            "type": "text",
            "order": 2,
            "content": "时间上通常按40-60分钟做参考；如果是更精细的点状斑或组合方案，时长会再有一点差异。",
        },
    ]


def _ad_price_check_messages(state: AgentState) -> list[dict[str, Any]]:
    content = state.get("normalized_content") or ""
    digits = _extract_price_digits(content)
    seen_price = f"{digits[0]}元" if digits else "你看到的广告价"
    price_text = _price_message(state)
    return [
        {
            "type": "text",
            "order": 1,
            "content": f"你看到的{seen_price}我先帮你按广告活动价来核对。广告价通常要看对应项目、适用门店、是否首体验，以及是否含预约金/尾款。",
        },
        {
            "type": "text",
            "order": 2,
            "content": price_text if price_text and "没查到" not in price_text else "目前我这边没有直接确认到这条广告价的完整规则，不能直接把它当最终总价说死。",
        },
        {
            "type": "text",
            "order": 3,
            "content": "如果你把广告截图或项目名发我，我可以帮你核对：这笔钱是全款、预约金，还是到店后还需要补尾款。",
        },
    ]


def _looks_like_store_list_message(text: str) -> bool:
    return "匹配到" in text and "门店" in text and ("你看哪家更方便" in text or re.search(r"\n\s*1[.、]", text))


def _is_redundant_known_goal_question(state: AgentState, text: str) -> bool:
    if not text:
        return False
    if not any(term in text for term in ["最想先改善", "想先改善哪", "更想改善哪", "主要想改善哪", "告诉我最想改善"]):
        return False
    if _has_confirmed_spot_goal(state):
        return True
    if _has_known_image_context(state) and _known_visible_concerns_from_state(state):
        return True
    return False


def _has_confirmed_spot_goal(state: AgentState) -> bool:
    content = str(state.get("normalized_content") or "")
    if any(term in content for term in ["就是斑", "主要斑", "祛斑", "淡斑", "斑呀", "斑啊", "斑点"]):
        return True
    profile = state.get("customer_profile") or {}
    if isinstance(profile, dict):
        joined = json_dumps(
            {
                "needs": profile.get("needs", []),
                "pain_points": profile.get("pain_points", []),
                "summary": profile.get("summary", ""),
            }
        )
        if any(term in joined for term in ["斑", "色沉", "肤色不均"]):
            return True
    for event in state.get("history_events", [])[-8:]:
        event_text = json_dumps(event) if isinstance(event, dict) else str(event)
        if any(term in event_text for term in ["点状斑", "斑点", "色沉", "肤色不均", "淡斑", "祛斑"]):
            return True
    return False


def _repair_spot_price_context_reply(state: AgentState, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    intents = {item.get("intent") for item in state.get("intents", [])}
    if "price_inquiry" not in intents:
        return messages
    if not _has_confirmed_spot_goal(state):
        return messages
    joined = "\n".join(str(item.get("content") or "") for item in messages if item.get("type") == "text")
    if not re.search(r"\d+\s*元?", joined):
        return messages
    if any(term in joined for term in ["光子嫩肤", "皮秒", "斑", "色沉"]):
        return messages
    price_text = _price_message(state)
    return [{"type": "text", "order": 1, "content": price_text}]


def _repair_appointment_commitment(text: str) -> str:
    text = text.replace("小贝马上帮你锁位", "小贝再继续帮你确认")
    text = text.replace("马上帮你锁位", "再继续帮你确认")
    text = text.replace("帮你锁位", "帮你继续确认")
    text = text.replace("帮您锁位", "帮您继续确认")
    text = text.replace("锁位", "确认")
    return text


def _repair_weak_competitor_reply(state: AgentState, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    intents = {item.get("intent") for item in state.get("intents", [])}
    if "competitor_compare" not in intents:
        return messages
    joined = "\n".join(str(item.get("content") or "") for item in messages if item.get("type") == "text")
    if _has_strong_competitor_answer(joined):
        return messages
    if "trust_issue" in intents:
        return [
            {"type": "text", "order": 1, "content": _trust_message(state)},
            {"type": "text", "order": 2, "content": _competitor_message(state)},
        ]
    return [{"type": "text", "order": 1, "content": _competitor_message(state)}]


def _has_strong_competitor_answer(text: str) -> bool:
    if not text:
        return False
    dimension_terms = ["产品", "剂量", "部位", "次数", "售后", "配置", "项目是否一样", "不直接", "不跟", "不能只看", "一次见效", "保证效果", "承诺效果", "个人基础", "方案匹配"]
    return sum(1 for term in dimension_terms if term in text) >= 2


def _ensure_store_answer(state: AgentState, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    intents = {item.get("intent") for item in state.get("intents", [])}
    if "store_inquiry" not in intents:
        return messages
    lookup = state.get("tool_results", {}).get("store_lookup") or {}
    stores = lookup.get("stores") if isinstance(lookup, dict) else []
    if not isinstance(stores, list) or not stores:
        return messages
    joined = "\n".join(str(item.get("content") or "") for item in messages if item.get("type") == "text")
    if any(_store_covered_in_text(store, joined) for store in stores if isinstance(store, dict)):
        return messages
    store_messages = _store_messages(state, start_order=len(messages) + 1)
    return (messages[:2] + store_messages)[:3]


def _store_covered_in_text(store: dict[str, Any], text: str) -> bool:
    name = str(store.get("name") or "").strip()
    address = str(store.get("address") or "").strip()
    short_name = name
    for prefix in ["上海", "厦门", "重庆", "南京", "杭州", "北京", "广州", "深圳"]:
        if short_name.startswith(prefix):
            short_name = short_name[len(prefix):]
            break
    if name and name in text:
        return True
    if short_name and len(short_name) >= 3 and short_name in text:
        return True
    if address and address[:8] in text:
        return True
    return False


def _ensure_pre_visit_answer(state: AgentState, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    content = state.get("normalized_content") or ""
    if not _has_pre_visit_question(content):
        return messages
    joined = "\n".join(str(item.get("content") or "") for item in messages if item.get("type") == "text")
    if any(term in joined for term in ["带", "化妆", "空腹", "护肤", "证件", "素颜"]):
        return messages
    repaired = messages[:2]
    repaired.append({"type": "text", "order": len(repaired) + 1, "content": _pre_visit_message(state)})
    return repaired[:3]


def _has_pre_visit_question(content: str) -> bool:
    return any(
        term in content
        for term in ["需要带什么", "要带什么", "带什么", "能不能化妆", "可以化妆", "要不要空腹", "需要空腹", "到店流程", "第一次去注意"]
    )


def _pre_visit_message(state: AgentState) -> str:
    content = state.get("normalized_content") or ""
    if "化妆" in content:
        return "如果是去看皮肤和做光电类项目，建议尽量素颜或淡妆，到店前别叠太多酸类、去角质这类刺激性护肤。"
    if "空腹" in content:
        return "这类皮肤面诊/光电咨询一般不需要空腹，正常作息就行；如果当天要做项目，按门店确认的项目注意事项来。"
    return "如果周六过去，带上手机和基础身份信息就可以；皮肤类建议尽量素颜或淡妆，前一天别刷酸、去角质，也先别暴晒。"


def _avoid_repeating_recent_reply(state: AgentState, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    intents = {item.get("intent") for item in state.get("intents", [])}
    repaired: list[dict[str, Any]] = []
    changed = False
    for message in messages:
        if not isinstance(message, dict) or message.get("type") != "text":
            repaired.append(message)
            continue
        content = str(message.get("content") or "")
        if not _too_similar_to_recent_assistant_reply(state, content):
            repaired.append(message)
            continue
        changed = True
        if "price_inquiry" in intents or "campaign_inquiry" in intents:
            repaired.append({**message, "content": _price_followup_message(state)})
        elif "project_inquiry" in intents or "image_inquiry" in intents:
            repaired.append({**message, "content": _project_followup_message(state)})
        else:
            repaired.append(message)
    return _renumber(repaired) if changed else messages


def _asks_for_duplicate_photo(text: str) -> bool:
    terms = [
        "发照片",
        "发张照片",
        "发一张",
        "照片发",
        "发我照片",
        "正脸自然光",
        "自然光下",
        "正脸照片",
        "拍张照片",
        "再发一张",
        "再发张",
        "看看皮肤状态",
        "帮你看看皮肤",
    ]
    return any(term in text for term in terms)


def _is_vague_price_deferral(text: str) -> bool:
    if "暂时没查到" in text or re.search(r"\d+\s*元?", text):
        return False
    return any(term in text for term in ["具体价格要看", "价格要看", "准确价格", "需要看配置", "结合配置"])


def _is_project_only_after_price_objection(text: str) -> bool:
    if _has_budget_or_price_answer(text):
        return False
    return any(term in text for term in ["斑的深浅", "项目配置", "适合哪个方案", "看斑型", "范围判断", "皮肤耐受"])


def _has_budget_or_price_answer(text: str) -> bool:
    return bool(re.search(r"\d+\s*元?", text)) or any(
        term in text for term in ["预算", "新客体验价", "活动价", "日常单次", "优惠价", "不能直接改价", "不能直接承诺", "不乱降价", "底价"]
    )


def _asks_daily_single_price(content: str) -> bool:
    return any(term in content for term in ["普通一次", "日常单次", "单次多少钱", "一次多少钱", "普通单次"])


def _should_add_project_guidance(state: AgentState) -> bool:
    content = state.get("normalized_content") or ""
    if _has_price_objection(content):
        return False
    guidance_terms = [
        "适合",
        "改善",
        "怎么弄",
        "怎么做",
        "做什么",
        "项目",
        "方向",
        "斑",
        "点状",
        "片状",
        "痘印",
        "痘坑",
        "毛孔",
        "暗沉",
    ]
    return any(term in content for term in guidance_terms)


def _should_use_model_reply(state: AgentState) -> bool:
    intents = state.get("intents", [])
    intent_set = {item.get("intent") for item in intents}
    if not intents:
        return False
    if intent_set & {"human_request", "complaint_refund"}:
        return False
    if intent_set == {"store_inquiry"}:
        return False
    return True


def _attach_asset_images(state: AgentState, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    intents = {item.get("intent") for item in state.get("intents", [])}
    if "trust_issue" not in intents:
        return _renumber(messages)
    if any(message.get("type") == "image" for message in messages):
        return _renumber(messages)
    image_url = _first_asset_image_url(state, "trust_assets")
    if not image_url:
        return _renumber(messages)

    image_message = {"type": "image", "order": 2, "content": image_url}
    if not messages:
        return _renumber([image_message])

    result = [messages[0], image_message, *messages[1:]]
    return _renumber(result[:3])


def _first_asset_image_url(state: AgentState, key: str) -> str:
    items = state.get("tool_results", {}).get(key, {}).get("items") or []
    for item in items:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "")
        match = re.search(r'<img\s+[^>]*src=["\']([^"\']+)["\']', content, flags=re.IGNORECASE)
        if match:
            return html.unescape(match.group(1))
        stripped = content.strip()
        if stripped.startswith("http://") or stripped.startswith("https://"):
            return html.unescape(stripped.split()[0])
    return ""


def _reply_model_tier(state: AgentState) -> str:
    intents = {item.get("intent") for item in state.get("intents", [])}
    if "human_request" in intents or "after_sales" in intents or "competitor_compare" in intents:
        return "strong"
    if len(intents) >= 2 or "trust_issue" in intents:
        return "balanced"
    return "fast"


def _reply_messages_for_model(state: AgentState) -> list[dict[str, Any]]:
    return build_reply_messages(_reply_user_payload_for_model(state), json_dumps=json_dumps)


def _reply_repair_messages_for_model(state: AgentState, draft_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return build_repair_messages(_reply_user_payload_for_model(state), draft_messages, json_dumps=json_dumps)


def _reply_user_payload_for_model(state: AgentState) -> dict[str, Any]:
    return {
        "content": state.get("normalized_content"),
        "conversation_history": state.get("conversation_history", [])[-6:],
        "reply_brief": _reply_brief_for_model(state),
        "image_info": state.get("image_info", {}),
        "customer_profile": state.get("customer_profile", {}),
        "customer_basic_info": state.get("customer_basic_info", {}),
        "history_events": state.get("history_events", [])[-8:],
        "recent_assistant_replies": _recent_assistant_replies(state, limit=4),
        "guardrail_result": state.get("guardrail_result", {}),
        "action_plan": state.get("action_plan", {}),
        "module_outputs": _compact_module_outputs_for_model(state.get("module_outputs", [])),
        "tool_results": _compact_tool_results_for_model(state.get("tool_results", {}), state),
    }

def _reply_brief_for_model(state: AgentState) -> dict[str, Any]:
    """Small factual brief for the final reply model.

    The model is intentionally given this cleaned brief before raw tool/module data
    so it answers like a human instead of echoing internal skill notes.
    """
    content = state.get("normalized_content") or ""
    intent_set = {item.get("intent") for item in state.get("intents", [])}
    tool_results = state.get("tool_results", {}) or {}
    project = _canonical_price_project(_contextual_price_project(state) or _extract_project(content))
    brief: dict[str, Any] = {
        "customer_message": content,
        "intents": sorted(str(intent) for intent in intent_set if intent),
        "must_answer": [],
        "available_facts": {},
        "answer_first": [],
        "known_facts": [],
        "do_not_say": [
            "系统查询到",
            "知识库显示",
            "我是AI",
            "转人工",
            "包效果",
            "一定有效",
            "我把营业执照发你",
            "发送营业执照",
            "营业执照发你",
        ],
        "follow_up": "",
    }

    image_info = state.get("image_info") or {}
    visible = image_info.get("visible_concerns") or _known_visible_concerns_from_state(state)
    if visible:
        visible_text = "、".join(str(item) for item in visible[:4])
        visible_source = "客户图片" if _has_actual_image_context(state) else "客户历史/画像"
        brief["known_facts"].append(f"{visible_source}中可见或提到：{visible_text}")
        brief["available_facts"]["has_actual_image"] = _has_actual_image_context(state)
        brief["must_answer"].append("先承接客户已发图片或已描述的皮肤问题，直接说明可改善方向和限制；只有确实有图片时才说“从图片看”。")
        brief["available_facts"]["visible_concerns"] = list(visible[:6])
        brief["do_not_say"].extend(["再发照片", "发张照片", "正脸自然光照片"])
        if any(term in visible_text for term in ["斑", "色沉", "肤色不均"]):
            brief["known_facts"].append("客户当前已明确关注斑点/色沉/肤色不均，不要再问“最想先改善哪一点”。")
            brief["available_facts"]["project_direction"] = [
                "肤色改善类方向更偏肤色不均、泛红暗沉、浅层色沉改善",
                "针对性色素淡化类方向更偏点状色素问题",
                "具体适合哪类需要结合斑的深浅和范围，不能仅凭照片诊断斑型",
            ]
            brief["do_not_say"].extend(["你最想先改善哪一点", "最想先改善哪一点", "你主要想改善哪一点"])

    if intent_set & {"price_inquiry", "campaign_inquiry"}:
        brief["must_answer"].append("本轮是价格/活动问题，必须优先用已查到的价格事实直接回答；没有查到明确价格时才说明未查到。")
        rows = _filter_pricing_rows_for_project(_pricing_rows_from_kb(tool_results), project) or _filter_pricing_rows_for_project(
            _pricing_rows(tool_results), project
        )
        if rows:
            row = rows[0]
            name = str(row.get("project_name") or project or "相关项目")
            brief["known_facts"].append(f"价格项目：{name}")
            for bit in _price_bits(row)[:5]:
                brief["known_facts"].append(bit)
            brief["available_facts"]["prices"] = [_price_fact_for_brief(row)]
        elif project:
            brief["known_facts"].append(f"本轮想问价格的项目：{project}；未查到明确可引用价格时不能拿其他项目代替。")
            brief["available_facts"]["prices"] = []
        brief["do_not_say"].extend(["价格要看具体配置所以不能说", "到店再说", "门店会有优惠"])
        if _has_confirmed_spot_goal(state):
            brief["known_facts"].append("客户本轮价格问题承接的是淡斑/斑点改善，不要再追问客户想改善什么。")
            brief["do_not_say"].extend(["你最想先改善哪一点", "最想先改善哪一点", "你主要想改善哪一点"])
            brief["follow_up"] = "价格后只需说明按图片/斑点情况先做预算参考，必要时补一句针对性色素淡化类方向需另看，不再问改善重点。"

    if "project_inquiry" in intent_set or "image_inquiry" in intent_set:
        brief["must_answer"].append("本轮是项目/看图咨询，先回答能否改善、适合的大方向和不能直接判断的边界。")

    if "case_request" in intent_set:
        brief["must_answer"].append("客户在要效果案例或前后对比，先承接可以按项目/问题方向看同类改善参考；如果工具没有真实图片链接，不要编造案例图。")
        brief["known_facts"].append("案例诉求不要反复改问客户是要门店、价格还是案例；已有祛斑/淡斑/门店线索时直接围绕同类案例承接。")
        brief["do_not_say"].extend(["您是想了解哪家门店可以看案例", "还是想了解相关项目的案例价格", "案例价格"])
        brief["follow_up"] = "如必须追问，只问一个最必要槽位，例如想看祛斑同类改善还是到店案例。"

    if "project_process" in intent_set:
        brief["must_answer"].append("客户询问项目操作流程或大概要多久，先给通用流程和时长范围；不同项目会有差异时简短说明。")
        brief["known_facts"].append("流程类问题要回答到店评估/清洁/检测/方案确认/操作/术后护理提醒/整体时长，不要只说到店再确认。")
        brief["do_not_say"].extend(["到店再说", "需要到店检测后才能知道流程"])

    if "ad_price_check" in intent_set:
        digits = _extract_price_digits(content)
        if digits:
            brief["known_facts"].append(f"客户看到的广告/活动价格数字：{'、'.join(digits[:3])}")
            brief["available_facts"]["customer_seen_price"] = digits[:3]
        brief["must_answer"].append("客户在核对广告价、预约金、尾款或是否另收费，必须先承接客户看到的价格数字，再解释当前可确认口径；不能直接换成另一个价格不解释。")
        brief["do_not_say"].extend(["绝对没有隐形消费", "肯定没有其他收费", "放心没有其他收费"])

    if "store_inquiry" in intent_set:
        lookup = tool_results.get("store_lookup") or {}
        stores = lookup.get("stores") if isinstance(lookup, dict) else []
        if isinstance(stores, list) and stores:
            store_facts = []
            for store in stores[:3]:
                if not isinstance(store, dict):
                    continue
                bits = [str(store.get("name") or "").strip(), str(store.get("address") or "").strip()]
                parking = _parking_text(store)
                if parking:
                    bits.append(parking)
                brief["known_facts"].append("门店：" + "，".join(bit for bit in bits if bit))
                store_facts.append(
                    {
                        "name": store.get("name"),
                        "address": store.get("address"),
                        "map_url": store.get("map_url"),
                        "parking": parking,
                    }
                )
            brief["available_facts"]["stores"] = store_facts
            brief["must_answer"].append("本轮是门店问题，直接回答匹配到的门店；如果客户指定城市，不能回复其他城市门店。")
        else:
            city = lookup.get("city") if isinstance(lookup, dict) else _extract_city(content)
            brief["known_facts"].append(f"按{city or '客户提供的位置'}暂时没有匹配到可直接发送的门店信息。")
            brief["available_facts"]["stores"] = []

    if intent_set & {"appointment_intent", "appointment_confirm", "appointment_change", "appointment_cancel"}:
        brief["must_answer"].append("本轮是预约相关问题，围绕已有预约、可约时间、改约或取消诉求回答；不要主动制造预约。")
        appointment_context = _appointment_context_sentence(state) if _should_show_appointment_context(state) else ""
        if appointment_context:
            brief["known_facts"].append(appointment_context)
    else:
        brief["do_not_say"].extend(["你已有预约", "已有预约记录", "查可约时间", "约时间"])

    if "trust_issue" in intent_set:
        brief["must_answer"].append("本轮是信任顾虑，先认可客户谨慎，再基于可用资质/背书事实解释；没有资料时不要编造资质。")
    if "competitor_compare" in intent_set:
        brief["must_answer"].append("本轮是竞品/比价，先认可对比，再拆项目、产品、剂量、部位、次数、售后等维度；不要贬低竞品或跟价。")
    if "after_sales" in intent_set:
        brief["must_answer"].append("本轮是售后/术后反馈，先安抚并确认项目、操作时间、症状；有风险信号时建议让专业人士协助确认。")

    memory_context = _memory_context_sentence(state)
    if memory_context:
        brief["known_facts"].append(memory_context)

    brief["must_answer"] = _dedupe_strings([str(item).strip() for item in brief["must_answer"] if str(item).strip()])[:8]
    brief["answer_first"] = _dedupe_strings([str(item).strip() for item in brief["answer_first"] if str(item).strip()])[:3]
    brief["known_facts"] = _dedupe_strings([str(item).strip() for item in brief["known_facts"] if str(item).strip()])[:10]
    if not brief["follow_up"]:
        brief["follow_up"] = _suggested_followup_for_brief(state)
    return brief


def _suggested_followup_for_brief(state: AgentState) -> str:
    content = state.get("normalized_content") or ""
    intent_set = {item.get("intent") for item in state.get("intents", [])}
    if intent_set & {"appointment_intent", "appointment_change", "appointment_cancel"}:
        return "围绕预约诉求确认门店、日期或处理动作。"
    if "store_inquiry" in intent_set:
        return "如有多家门店，让客户选更方便的一家。"
    if "price_inquiry" in intent_set:
        if _has_price_objection(content):
            return "承接预算压力，不承诺降价，给已知价格档位。"
        if _has_confirmed_spot_goal(state):
            return "客户已明确关注斑点/淡斑，回答价格后不要再问最想改善哪一点。"
        return "回答价格后，可问客户更在意效果、恢复期还是预算。"
    if "ad_price_check" in intent_set:
        return "先核对客户看到的广告价/预约金/尾款口径，再说明已知收费项和需要确认的项目配置。"
    if "case_request" in intent_set:
        return "围绕客户要看的案例/效果参考承接，缺少真实案例素材时说明可看同类改善参考。"
    if "project_process" in intent_set:
        return "直接说明项目流程和大致时长，必要时补一句不同项目配置会有差异。"
    if "project_inquiry" in intent_set or "image_inquiry" in intent_set:
        if _has_confirmed_spot_goal(state):
            return "客户已明确关注斑点/淡斑，直接说明淡斑改善方向和限制，不要再问最想改善哪一点。"
        return "给出项目方向后，只问客户最想先改善哪一点。"
    if "trust_issue" in intent_set:
        return "认可谨慎，围绕资质、产品来源和服务保障解释。"
    return "轻量承接客户当前问题。"


def _compact_module_outputs_for_model(outputs: list[Any]) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    for output in outputs[:5]:
        if not isinstance(output, dict):
            continue
        compacted.append(
            {
                "skill": output.get("skill"),
                "intent": output.get("intent"),
                "facts": [str(item)[:160] for item in (output.get("facts") or [])[:4]],
                "reply_points": [],
                "missing_slots": [str(item)[:80] for item in (output.get("missing_slots") or [])[:4]],
                "risk_flags": [str(item)[:80] for item in (output.get("risk_flags") or [])[:4]],
                "suggested_next_step": output.get("suggested_next_step"),
            }
        )
    return compacted


def _compact_tool_results_for_model(tool_results: dict[str, Any], state: AgentState | None = None) -> dict[str, Any]:
    compacted: dict[str, Any] = {}
    project = _canonical_price_project(_contextual_price_project(state or {})) if state else ""
    for key, value in tool_results.items():
        if key == "pricing_db":
            compacted[key] = {
                "rows": [] if _is_broad_price_category(project) else value.get("rows", [])[:3],
                "error": value.get("error"),
            }
            continue
        if key == "pricing_local":
            compacted[key] = {
                "rows": [] if _is_broad_price_category(project) else value.get("rows", [])[:3],
                "error": value.get("error"),
            }
            continue
        if key == "store_lookup" and isinstance(value, dict):
            compacted[key] = {
                "city": value.get("city"),
                "requested_store": value.get("requested_store"),
                "wants_parking": value.get("wants_parking"),
                "wants_route": value.get("wants_route"),
                "stores": [
                    {
                        "id": store.get("id"),
                        "name": store.get("name"),
                        "address": store.get("address"),
                        "map_url": store.get("map_url"),
                        "parking_name": store.get("parking_name"),
                        "parking_address": store.get("parking_address"),
                        "business_hours": store.get("business_hours"),
                    }
                    for store in (value.get("stores") or [])[:3]
                    if isinstance(store, dict)
                ],
                "missing": value.get("missing"),
                "error": value.get("error"),
            }
            continue
        if key == "available_time" and isinstance(value, dict):
            compacted[key] = {
                "store_name": value.get("store_name"),
                "store_id": value.get("store_id"),
                "date": value.get("date"),
                "slots": value.get("slots"),
                "missing": value.get("missing"),
                "error": value.get("error"),
            }
            continue
        if key == "project_price" and _is_broad_price_category(project):
            compacted[key] = {
                "items": [],
                "note": "客户当前只有大类/模糊价格诉求，已隐藏具体商品项，避免拿不相关商品价代替报价。",
                "error": value.get("error") if isinstance(value, dict) else None,
            }
            continue
        items = value.get("items", []) if isinstance(value, dict) else []
        compacted[key] = {
            "items": [
                {
                    "content": str(item.get("content", ""))[:800],
                    "document_id": item.get("document_id", ""),
                }
                for item in items[:3]
                if isinstance(item, dict)
            ],
            "error": value.get("error") if isinstance(value, dict) else None,
        }
    return compacted


def _validated_model_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    messages = payload.get("reply_messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("Model JSON missing reply_messages")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(messages[:3], start=1):
        if not isinstance(item, dict):
            continue
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        msg_type = item.get("type") if item.get("type") in {"text", "image"} else "text"
        if msg_type == "text":
            image_url = _extract_image_url_from_text(content)
            if image_url:
                text_without_url = _strip_image_url_from_text(content, image_url)
                if text_without_url:
                    result.append({"type": "text", "order": len(result) + 1, "content": text_without_url})
                result.append({"type": "image", "order": len(result) + 1, "content": image_url})
                continue
        result.append({"type": msg_type, "order": len(result) + 1, "content": content})
    if not result:
        raise ValueError("Model reply_messages are empty")
    return _renumber(result[:3])


def _recent_assistant_replies(state: AgentState, limit: int = 4) -> list[str]:
    replies: list[str] = []
    for item in reversed(state.get("conversation_history") or []):
        text = str(item).strip()
        if not text:
            continue
        if text.startswith(("小贝：", "小贝:", "助手：", "助手:", "客服：", "客服:")):
            cleaned = re.sub(r"^(小贝|助手|客服)[：:]\s*", "", text).strip()
            if cleaned:
                replies.append(cleaned[:300])
        if len(replies) >= limit:
            break
    return list(reversed(replies))


def _looks_like_image_url(content: str) -> bool:
    return bool(_extract_image_url_from_text(content))


def _extract_image_url_from_text(content: str) -> str:
    text = html.unescape(content.strip())
    img_match = re.search(r'<img\s+[^>]*src=["\']([^"\']+)["\']', text, flags=re.IGNORECASE)
    if img_match:
        return html.unescape(img_match.group(1)).strip()
    markdown_match = re.search(r"!\[[^\]]*\]\((https?://[^)\s]+)\)", text)
    if markdown_match:
        url = html.unescape(markdown_match.group(1)).strip()
        if _is_image_url(url):
            return url
    url_match = re.search(r"https?://[^\s<>'\")]+", text)
    if url_match:
        url = html.unescape(url_match.group(0)).strip()
        if _is_image_url(url):
            return url
    return ""


def _strip_image_url_from_text(content: str, image_url: str) -> str:
    text = html.unescape(content.strip())
    text = re.sub(r"<img\s+[^>]*>", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"!\[[^\]]*\]\(" + re.escape(image_url) + r"\)", "", text).strip()
    text = text.replace(image_url, "").strip()
    text = re.sub(r"\s+", " ", text).strip(" ，,。；;")
    return text


def _is_image_url(text: str) -> bool:
    if not (text.startswith("http://") or text.startswith("https://")):
        return False
    if "\n" in text or " " in text:
        return False
    lower = text.lower()
    return any(marker in lower for marker in [".png", ".jpg", ".jpeg", ".webp", "filebiztype.biz_bot_dataset", "ocean-cloud-tos"])


def _model_reply_unsafe(state: AgentState, messages: list[dict[str, Any]]) -> bool:
    text = "\n".join(str(message.get("content") or "") for message in messages)
    intents = {item.get("intent") for item in state.get("intents", [])}
    content = state.get("normalized_content") or ""
    project = _extract_project(content)
    image_info = state.get("image_info") or {}
    if _has_known_image_context(state):
        repeat_image_terms = ["发照片", "发张照片", "发一张", "照片发", "发我照片", "正脸自然光", "自然光下", "正脸照片", "拍张照片", "再发一张", "再发张", "看看皮肤状态", "帮你看看皮肤"]
        if any(term in text for term in repeat_image_terms):
            return True
    if _is_generic_project_intro(content) and any(term in text for term in ["肉毒", "水光", "热玛吉", "超声炮", "光子", "皮秒"]):
        return True
    if _is_unclear_need(content) and any(term in text for term in ["小气泡", "水杨酸", "肉毒", "水光", "热玛吉", "超声炮", "光子", "皮秒"]):
        return True
    if _is_unclear_need(content) and any(term in text for term in ["PDRN", "三文鱼", "焕肤"]):
        return True
    if "trust_issue" in intents and "store_inquiry" not in intents and any(term in text for term in ["匹配到", "你看哪家更方便", "\n1.", "门店："]):
        return True
    if "ad_price_check" in intents and any(term in text for term in ["列几个预算参考", "几个预算参考", "换成", "其他配置里"]):
        return True
    if "competitor_compare" in intents and any(term in text for term in ["列几个预算参考", "小贝先给你列几个预算", "胶原类项目", "PDRN", "新客体验价", "日常单次价", "活动价"]):
        return True
    if "competitor_compare" in intents:
        digits = _extract_price_digits(content)
        if digits and not any(digit in text for digit in digits[:2]):
            return True
        if any(term in content for term in ["一次见效", "一次就能", "一次就"]) and "一次" not in text:
            return True
    if _is_identity_question(content) and any(term in text for term in ["真人客服", "我是人工", "不是AI", "不是机器人"]):
        return True
    if _has_effect_guarantee_request(content) and any(
        term in text for term in ["多数人", "明显提亮", "3-5次", "3到5次", "一定", "所有项目", "顾问档期", "先面诊", "不收费"]
    ):
        return True
    if "after_sales" in intents and any(term in content for term in ["三天", "3天", "第三天"]) and any(term in content for term in ["泛红", "有点红", "发红", "红"]):
        if not any(term in text for term in ["三天", "保湿", "防晒", "流脓", "发烧"]):
            return True
    if intents & {"price_inquiry", "campaign_inquiry"} and _is_broad_price_category(_contextual_price_project(state)) and any(term in text for term in ["胶原类项目", "PDRN", "喷雾", "洁面", "精华液", "晶钻霜"]):
        return True
    if _has_confirmed_spot_goal(state) and any(term in text for term in ["你最想先改善哪一点", "最想先改善哪一点", "主要想改善哪一点"]):
        return True
    if _has_image_concern(image_info, ["点状斑", "褐色斑点", "色沉", "肤色不均", "斑点"]):
        if any(term in text for term in ["果酸", "焕肤"]) and not _tool_results_contain(state, "果酸"):
            return True
    hard_forbidden = [
        "预留",
        "留名额",
        "锁位",
        "帮你锁位",
        "帮您锁位",
        "马上帮你锁位",
        "帮你留一下",
        "帮您留一下",
        "给你留一下",
        "给您留一下",
        "电话跟您确认",
        "电话联系",
        "电话确认",
        "安排合适的医生",
        "安排医生",
        "持证医生",
        "持证医疗机构",
        "专业医生",
        "官方认证",
        "药监局认证",
        "国家药监局认证",
        "所有门店都持有",
        "所有门店都是正规注册",
        "所有门店都是持证合规",
        "所有项目用的都是",
        "持证合规经营",
        "正规注册的医美机构",
        "卫生许可证",
        "持证皮肤治疗师",
        "持证皮肤管理师",
        "李技师",
        "8年激光",
        "专业仪器",
        "卫健委核发",
        "国家药监局备案",
        "药监局备案",
        "所有操作",
        "专业顾问跟进",
        "安排专业的顾问",
        "到店后直接找我",
        "肯定有效",
        "包效果",
        "一定有效",
        "正规进口设备",
        "认证耗材",
        "产品授权书",
        "器械备案信息",
        "资质材料发你",
        "发你核对",
        "让店长直接",
        "多年皮秒实操经验",
        "我把营业执照发你",
        "把营业执照发你",
        "发送营业执照",
        "营业执照发你",
        "直接发执照",
        "医生会先看",
        "医生面诊",
        "专业医生",
        "可以放心",
        "您放心",
        "你放心",
        "放心哦",
        "不用担心",
        "别担心",
        "真人客服",
        "我是人工",
        "不是AI",
        "不是机器人",
    ]
    if any(term in text for term in hard_forbidden):
        return True
    if not _has_actual_image_context(state) and any(term in text for term in ["你发的图片", "您发的图片", "从你发的图片", "从您发的图片", "结合照片", "前面照片", "照片里"]):
        return True
    diagnosis_terms = ["雀斑", "晒斑", "黄褐斑", "皮炎", "感染", "玫瑰痤疮", "毛囊炎"]
    if any(term in text for term in diagnosis_terms):
        return True
    if "trust_issue" not in intents and any(term in text for term in ["医疗机构执业许可证", "执业许可证", "资质图片", "正规资质"]):
        return True
    if "store_inquiry" not in intents and any(term in text for term in ["地址是：", "停车场", "直接导航到"]):
        return True
    if not intents & {"appointment_intent", "appointment_confirm", "appointment_change", "appointment_cancel"}:
        if any(term in text for term in ["面诊名额", "帮您约", "帮你约", "确认具体时间", "什么时候方便呢", "近期可约时段", "帮你看看近期", "帮您看看近期"]):
            return True
    if not _should_show_appointment_context(state):
        if any(term in text for term in ["已有预约", "已有预约记录", "预约记录：", "你这边已有预约"]):
            return True
    if "store_inquiry" in intents:
        city = _extract_city(content)
        lookup = state.get("tool_results", {}).get("store_lookup") or {}
        stores = lookup.get("stores", []) if isinstance(lookup, dict) else []
        if city and city not in text:
            return True
        if city and not stores and any(other_city in text for other_city in CITY_NAMES if other_city != city):
            return True
        if "停车" in content and "停车场" not in text:
            return True
        if "地址" in content and "地址" not in text and "厦门市" not in text:
            return True
    if intents & {"appointment_intent", "appointment_confirm", "appointment_change", "appointment_cancel"}:
        available = state.get("tool_results", {}).get("available_time") or {}
        if isinstance(available, dict) and available.get("slots") and not re.search(r"\d{1,2}:\d{2}", text):
            return True
        if isinstance(available, dict) and available.get("error") and any(term in text for term in ["有空档", "可以安排", "可以预约"]):
            return True
    if "after_sales" in intents and any(term in text for term in ["是正常的", "属于正常", "不用太担心", "不用担心"]):
        return True
    if "price_inquiry" in intents:
        if project and f"没有{project}项目" in text:
            return True
        if project == "皮秒" and ("光子嫩肤或者水光" in text or "光子嫩肤或水光" in text):
            return True
        if project and project not in text and not re.search(r"\d+\s*元?", text):
            return True
        if not re.search(r"\d+\s*元?", text) and any(term in text for term in ["具体价格要看", "价格要看", "准确价格", "配置"]) and "暂时没查到" not in text:
            return True
        if _asks_daily_single_price(content) and "日常单次" not in text and "日常价" not in text:
            return True
        if _has_price_objection(content):
            if not _has_budget_or_price_answer(text):
                return True
            if _is_project_only_after_price_objection(text):
                return True
        if not intents & {"store_inquiry", "appointment_intent", "appointment_confirm", "appointment_change", "appointment_cancel"}:
            if any(term in text for term in ["所在城市", "附近门店", "门店优惠", "更具体的优惠", "优惠信息", "到店时间"]):
                return True
    if not intents & {"appointment_intent", "appointment_confirm", "appointment_change", "appointment_cancel"}:
        if any(term in text for term in ["哪天方便到店", "方便到店", "到店面诊", "约个面诊", "约个时间到店", "面诊下皮肤状态"]):
            return True
    if not intents & {"price_inquiry", "campaign_inquiry", "competitor_compare"}:
        if any(term in text for term in ["新客体验价", "活动价", "日常单次"]):
            return True
    if "case_request" in intents:
        if not any(term in text for term in ["案例", "前后", "对比", "改善参考", "同类"]):
            return True
        if any(term in text for term in ["案例价格", "哪家门店可以看案例"]) and not any(term in text for term in ["同类", "祛斑", "淡斑", "改善"]):
            return True
    if "project_process" in intents:
        if not any(term in text for term in ["流程", "步骤", "操作", "清洁", "检测", "评估", "分钟", "时长", "多久"]):
            return True
    if "ad_price_check" in intents:
        digits = _extract_price_digits(content)
        if digits and not any(digit in text for digit in digits[:2]):
            return True
        if not any(term in text for term in ["广告", "活动", "预约金", "尾款", "包含", "另收费", "隐形"]):
            return True
    if _too_similar_to_recent_assistant_reply(state, text):
        return True
    return False


def _too_similar_to_recent_assistant_reply(state: AgentState, text: str) -> bool:
    normalized = _normalize_reply_for_similarity(text)
    if len(normalized) < 18:
        return False
    for recent in _recent_assistant_replies(state, limit=4):
        recent_norm = _normalize_reply_for_similarity(recent)
        if len(recent_norm) < 18:
            continue
        if normalized == recent_norm:
            return True
        ratio = SequenceMatcher(None, normalized, recent_norm).ratio()
        if ratio >= 0.92:
            return True
    return False


def _normalize_reply_for_similarity(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").strip())


def _tool_results_contain(state: AgentState, term: str) -> bool:
    tool_results = state.get("tool_results") or {}
    for value in tool_results.values():
        if isinstance(value, dict):
            if term in json_dumps(value):
                return True
        elif term in str(value):
            return True
    return False


def _has_known_image_context(state: AgentState) -> bool:
    image_info = state.get("image_info") or {}
    if image_info.get("has_image"):
        return True
    profile = state.get("customer_profile") or {}
    if isinstance(profile, dict):
        profile_text = " ".join(
            str(item)
            for key in ["pain_points", "needs", "projects", "concerns"]
            for item in (profile.get(key) or [])
        )
        if any(term in profile_text for term in ["点状斑", "面部色沉", "肤色不均", "泛红", "痘印", "毛孔"]):
            return True
    for event in state.get("history_events", [])[-8:]:
        event_text = json_dumps(event) if isinstance(event, dict) else str(event)
        if any(term in event_text for term in ["图片", "照片", "可见", "点状斑", "色沉", "肤色不均", "泛红"]):
            return True
    for message in state.get("conversation_history", [])[-8:]:
        message_text = str(message)
        if any(term in message_text for term in ["图片", "照片", "可见", "点状斑", "色沉", "肤色不均", "泛红"]):
            return True
    return False


def _has_actual_image_context(state: AgentState) -> bool:
    image_info = state.get("image_info") or {}
    if image_info.get("has_image"):
        return True
    if state.get("file_image"):
        return True
    for message in state.get("conversation_history", [])[-8:]:
        text = str(message)
        if "[图片]" in text or "file_image" in text or "<img" in text:
            return True
    return False


def _known_visible_concerns_from_state(state: AgentState) -> list[str]:
    concerns: list[str] = []
    profile = state.get("customer_profile") or {}
    if isinstance(profile, dict):
        for key in ["pain_points", "concerns"]:
            for item in profile.get(key) or []:
                text = str(item).strip()
                if any(term in text for term in ["点状斑", "斑点", "色沉", "肤色不均", "泛红", "痘印", "痘坑", "毛孔"]):
                    concerns.append(text)
    for event in state.get("history_events", [])[-8:]:
        event_text = json_dumps(event) if isinstance(event, dict) else str(event)
        for term in ["点状斑点", "点状斑", "片状色沉", "面部色沉", "肤色不均", "泛红", "痘印", "痘坑", "毛孔"]:
            if term in event_text:
                concerns.append(term)
    for message in state.get("conversation_history", [])[-8:]:
        message_text = str(message)
        for term in ["点状斑点", "点状斑", "片状色沉", "面部色沉", "肤色不均", "泛红", "痘印", "痘坑", "毛孔"]:
            if term in message_text:
                concerns.append(term)
    return _dedupe_strings(concerns)[:5]


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _model_usage_snapshot(model_client: ModelClient | None) -> dict[str, Any]:
    usage = getattr(model_client, "last_usage", None) if model_client else None
    if not isinstance(usage, dict):
        return {}
    raw_usage = usage.get("usage") if isinstance(usage.get("usage"), dict) else {}
    return {
        "provider": usage.get("provider", ""),
        "model": usage.get("model", ""),
        "prompt_tokens": raw_usage.get("prompt_tokens", 0),
        "completion_tokens": raw_usage.get("completion_tokens", 0),
        "total_tokens": raw_usage.get("total_tokens", 0),
    }


def _price_message(state: AgentState) -> str:
    tool_results = state.get("tool_results", {})
    content = state.get("normalized_content") or ""
    project = _canonical_price_project(_contextual_price_project(state))
    if _is_vague_reference_price_question(content) and not project and not _recent_project_from_state(state):
        return "你说的“这个”我这边还没法确定具体是哪一项，小贝先不拿不相关项目乱报价。你把项目名或广告截图发我，我按对应项目帮你核对价格。"
    kb_rows = _filter_pricing_rows_for_project(_pricing_rows_from_kb(tool_results), project)
    rows = kb_rows or _filter_pricing_rows_for_project(_pricing_rows(tool_results), project)
    intent_set = {item.get("intent") for item in state.get("intents", [])}
    guidance = _context_guidance_inline(state, content, project)
    if _has_price_objection(content):
        return _price_objection_message(state, rows, project)
    if not rows:
        kb_hint = _kb_price_hint(tool_results, project)
        if kb_hint:
            return f"{kb_hint}，但里面没有明确价格字段，小贝先不乱报数字。{guidance}"
        if project:
            return f"小贝这边暂时没查到{project}的明确单次价格，不能拿别的项目价格代替报价。{guidance}"
        image_guidance = _image_guidance_inline(state, "")
        if image_guidance:
            return f"{image_guidance}价格这块小贝先不拿不相关项目乱报，淡斑方向一般要先确认更适合肤色改善类、针对性色素淡化类还是组合方案后再核对。"
        return "价格要看具体项目和配置，小贝先不乱报数字；你告诉我想问哪个项目，我按项目帮你核对。"
    if len(rows) > 1 and not _requires_exact_price(project):
        multi = _multi_price_message(rows, project)
        if multi:
            return multi

    row = rows[0]
    name = row.get("project_name", "这个项目")
    source = row.get("_source") or ("local_xlsx" if state.get("tool_results", {}).get("pricing_local", {}).get("rows") else "coze_db")
    if _asks_daily_single_price(content):
        daily_price = _value(row.get("daily_price"))
        new_price = _value(row.get("new_price"))
        promo_price = _value(row.get("promo_price"))
        if daily_price:
            extra_bits = []
            if new_price:
                extra_bits.append(f"新客体验价{new_price}")
            if promo_price:
                extra_bits.append(f"活动价{promo_price}")
            extra = f"；如果是首次体验，也可以参考{'、'.join(extra_bits)}。" if extra_bits else "。"
            return f"{name}日常单次价是{daily_price}{extra}{guidance}"
    if source == "project_price_kb":
        return _price_point_from_kb_row(row, project, guidance)
    new_price = _value(row.get("new_price"))
    promo_price = _value(row.get("promo_price"))
    daily_price = _value(row.get("daily_price"))
    prefix = ""
    if project and name and project not in str(name) and source == "local_xlsx":
        if _requires_exact_price(project):
            kb_rows = _pricing_rows_from_kb(tool_results)
            if kb_rows:
                return _price_point_from_kb_row(kb_rows[0], project, guidance)
            kb_hint = _kb_price_hint(tool_results, project)
            if kb_hint:
                return f"{kb_hint}，但里面没有明确价格字段，小贝先不乱报数字。{guidance}"
            return f"当前价格表没看到{project}单项，小贝先不拿其他淡斑产品价格代替报价；我可以继续按{project}单项帮你核对。{guidance}"
        prefix = f"当前价格表没看到{project}单项，淡斑相关配置里，"
    if new_price and promo_price:
        return f"小贝先给你一个预算参考：{prefix}{name}新客体验价{new_price}，活动价一般是{promo_price}。{_price_guidance_for_customer_goal(state, guidance, name)}"
    if new_price:
        return f"{prefix}{name}目前新客体验价是{new_price}，可以先按这个做预算参考。{_price_guidance_for_customer_goal(state, guidance, name)}"
    if promo_price:
        return f"{prefix}{name}目前活动价是{promo_price}，可以先按这个做预算参考。{_price_guidance_for_customer_goal(state, guidance, name)}"
    if daily_price:
        return f"{prefix}{name}日常单次价是{daily_price}，可以先作为预算参考。{_price_guidance_for_customer_goal(state, guidance, name)}"
    return f"{name}已查到项目记录，但价格需要结合当前配置核对，小贝先不乱报数字。"


def _is_vague_reference_price_question(content: str) -> bool:
    if not content:
        return False
    return any(term in content for term in ["这个", "那个", "刚刚那个", "这项", "这个项目"]) and any(
        term in content for term in ["多少钱", "多少", "价格", "费用", "预算"]
    )


def _price_guidance_for_customer_goal(state: AgentState, guidance: str, price_project_name: str = "") -> str:
    if not _has_confirmed_spot_goal(state):
        return guidance
    if guidance:
        return guidance
    name = str(price_project_name or "")
    if "光子" in name:
        return "按你前面照片里的斑点和肤色不均，这个可以先做预算参考；如果后面判断更适合皮秒/祛斑类，价格会按对应项目另看。"
    if "皮秒" in name or "祛斑" in name:
        return "按你前面说的斑点方向，这个可以先做预算参考；具体还要看斑的深浅、范围和皮肤耐受。"
    return "按你前面说的斑点方向，这个先做预算参考；具体项目还要看斑的深浅和范围。"


def _pricing_rows_from_kb(tool_results: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    items = tool_results.get("project_price", {}).get("items") or []
    for item in items:
        if not isinstance(item, dict):
            continue
        row = _parse_price_kb_content(str(item.get("content") or ""))
        if row and any(_value(row.get(key)) for key in ["new_price", "promo_price", "daily_price", "old_price"]):
            rows.append(dict(row, _source="project_price_kb"))
    return rows


def _parse_price_kb_content(content: str) -> dict[str, str]:
    mapping = {
        "项目名称": "project_name",
        "日常单次价": "daily_price",
        "新客体验价": "new_price",
        "老客单次价": "old_price",
        "老客推荐卡项": "old_card",
        "活动价": "promo_price",
        "活动适用人群": "promo_target",
        "可赠送福利": "gift_item",
        "福利触发场景": "gift_scene",
        "报价备注": "price_note",
    }
    row: dict[str, str] = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if "：" not in line:
            continue
        label, value = line.split("：", 1)
        key = mapping.get(label.strip())
        if key:
            row[key] = value.strip()
    return row


def _price_point_from_kb_row(row: dict[str, Any], project: str, guidance: str) -> str:
    name = str(row.get("project_name") or row.get("price_note") or project or "相关项目")
    point = _price_point_from_row(name, row, requested_project="", source="project_price_kb")
    return f"{point}{guidance}" if guidance else point


def _price_objection_message(state: AgentState, rows: list[dict[str, Any]], project: str) -> str:
    content = state.get("normalized_content") or ""
    if not rows:
        if project:
            return f"能理解你会在意预算。小贝这边暂时没查到{project}明确价格，不能直接承诺降价或拿别的项目价格替代；如果有新客/活动配置，我会优先按更低门槛的给你核对。"
        return "能理解你会在意预算。你先告诉小贝具体是哪个项目，我按对应的新客价、活动价和日常价帮你核对，不拿别的项目乱报。"

    row = rows[0]
    name = str(row.get("project_name") or project or "这个项目")
    new_price = _value(row.get("new_price"))
    promo_price = _value(row.get("promo_price"))
    daily_price = _value(row.get("daily_price"))
    old_price = _value(row.get("old_price"))
    low_options: list[str] = []
    if new_price:
        low_options.append(f"新客体验价{new_price}")
    if promo_price:
        low_options.append(f"活动价{promo_price}")

    if any(term in content for term in ["便宜点", "便宜一点", "能便宜", "优惠点", "还能优惠", "能优惠", "少点", "打折", "最低", "底价"]):
        if low_options:
            daily = f"，日常单次价是{daily_price}" if daily_price else ""
            return f"小贝这边不能直接改价或承诺底价，不过{name}可以先看{'、'.join(low_options)}{daily}，这几个是目前能给你做预算参考的档位。"
        if daily_price:
            return f"{name}目前能明确参考的是日常单次价{daily_price}。小贝不能直接改价或承诺底价，如果有活动配置我会优先帮你核对。"
        return f"{name}目前没有查到可直接引用的优惠价，小贝不能直接改价或承诺底价。"

    if low_options:
        daily = f"；日常单次是{daily_price}" if daily_price else ""
        return f"能理解，第一次了解会觉得价格有压力。{name}如果先体验，可以优先看{'、'.join(low_options)}{daily}，不一定一上来就按高档配置做。"
    if daily_price:
        return f"能理解你觉得有点贵。{name}日常单次价是{daily_price}，小贝这边不乱降价；如果你主要卡预算，我优先帮你看有没有新客或活动配置。"
    if old_price:
        return f"能理解你会在意预算。{name}目前能明确参考的是老客单次价{old_price}，新客或活动价需要再核对，不能直接承诺降价。"
    return f"能理解你会在意预算。{name}已查到配置，但价格字段不完整，小贝不能直接承诺降价或报底价。"


def _price_objection_point(name: str, row: dict[str, Any], project: str) -> str:
    pseudo_state: AgentState = {"normalized_content": "太贵了", "tool_results": {}, "intents": []}  # type: ignore[assignment]
    return _price_objection_message(pseudo_state, [row], project or name)


def _price_followup_message(state: AgentState) -> str:
    tool_results = state.get("tool_results", {})
    content = state.get("normalized_content") or ""
    project = _canonical_price_project(_contextual_price_project(state))
    rows = _filter_pricing_rows_for_project(_pricing_rows_from_kb(tool_results), project) or _filter_pricing_rows_for_project(
        _pricing_rows(tool_results), project
    )
    if _has_price_objection(content):
        return _price_objection_message(state, rows, project)
    if not rows:
        if project:
            return f"换个说法哈，{project}这次没有查到可直接引用的单次价，所以小贝不能拿别的项目价格替你报。"
        return "换个说法哈，价格这块要先锁定具体项目，小贝才能按对应的新客价、活动价和日常价给你核对。"

    row = rows[0]
    name = str(row.get("project_name") or project or "这个项目")
    daily_price = _value(row.get("daily_price"))
    new_price = _value(row.get("new_price"))
    promo_price = _value(row.get("promo_price"))
    if _asks_daily_single_price(content) and daily_price:
        extras = []
        if new_price:
            extras.append(f"新客{new_price}")
        if promo_price:
            extras.append(f"活动{promo_price}")
        tail = f"；{'、'.join(extras)}是体验/活动档位。" if extras else "。"
        return f"你问普通一次的话，主要看日常单次：{name}日常单次是{daily_price}{tail}"
    bits = []
    if new_price:
        bits.append(f"新客体验{new_price}")
    if promo_price:
        bits.append(f"活动{promo_price}")
    if daily_price:
        bits.append(f"日常单次{daily_price}")
    if bits:
        return f"我给你捋一下：{name}目前可以按{'、'.join(bits)}来参考，第一次了解一般先看新客或活动档位就行。"
    return f"{name}有项目记录，但这次价格字段不完整，小贝先不硬报数字。"


def _kb_price_hint(tool_results: dict[str, Any], project: str) -> str:
    if _is_broad_price_category(project):
        return ""
    items = tool_results.get("project_price", {}).get("items") or []
    for item in items:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "")
        row = _parse_price_kb_content(content)
        note = row.get("price_note") or _extract_label(content, "报价备注")
        name = row.get("project_name") or _extract_label(content, "项目名称")
        hint = name or note
        if hint and (not project or project in hint or project in content):
            return f"小贝这边看到{hint}相关配置"
    return ""


def _extract_label(content: str, label: str) -> str:
    match = re.search(rf"{re.escape(label)}：([^\n\r]+)", content)
    return match.group(1).strip() if match else ""


def _project_guidance_inline(content: str, project: str) -> str:
    if project == "皮秒" and ("斑" in content or "淡斑" in content or "祛斑" in content or "点状" in content):
        return "另外，从你说的点状斑/淡斑方向看，皮秒一般属于光电淡斑方向，但还要看斑的深浅、范围和皮肤耐受。"
    if "斑" in content or "淡斑" in content or "祛斑" in content:
        return "另外，淡斑类不能只看项目名，主要还要看斑型、深浅、范围和恢复期。"
    return ""


def _context_guidance_inline(state: AgentState, content: str, project: str) -> str:
    image_guidance = _image_guidance_inline(state, project)
    if image_guidance:
        return image_guidance
    return _project_guidance_inline(content, project)


def _image_guidance_inline(state: AgentState, project: str = "") -> str:
    image_info = state.get("image_info") or {}
    visible_items = image_info.get("visible_concerns") or _known_visible_concerns_from_state(state)
    if not visible_items:
        return ""
    visible = "、".join(str(item) for item in visible_items[:3])
    if not visible:
        return ""
    spot_like = _has_image_concern(image_info, ["点状斑", "褐色斑点", "色沉", "肤色不均", "斑点"]) or any(
        term in visible for term in ["点状斑", "斑点", "色沉", "肤色不均", "面部斑"]
    )
    if spot_like:
        if project == "皮秒":
            return f"另外，从你发的图片看，主要可见{visible}，皮秒属于淡斑方向之一，但还要看斑的深浅、范围和皮肤耐受。"
        return f"另外，从你发的图片看，主要可见{visible}，淡斑类还要结合斑型、深浅和范围判断项目方向。"
    acne_like = _has_image_concern(image_info, ["痘印", "痘坑", "毛孔", "泛红"]) or any(
        term in visible for term in ["痘印", "痘坑", "毛孔", "泛红"]
    )
    if acne_like:
        return f"另外，从你发的图片看，主要可见{visible}，这类要先区分痘印、痘坑、毛孔或泛红，再看适合的改善方式。"
    return f"另外，从你发的图片看，主要可见{visible}，后续会结合你想改善的重点看项目方向。"


def _multi_price_message(rows: list[dict[str, Any]], project: str) -> str:
    candidates = []
    for row in rows[:3]:
        name = str(row.get("project_name") or "相关项目")
        price = _value(row.get("promo_price")) or _value(row.get("new_price")) or _value(row.get("daily_price"))
        if not price:
            continue
        label = "活动价" if _value(row.get("promo_price")) else "新客价" if _value(row.get("new_price")) else "日常价"
        candidates.append(f"{name}{label}{price}")
    if not candidates:
        return ""
    if project:
        return f"{project}相关小贝先给你列几个预算参考：{'；'.join(candidates)}。你主要看哪一个配置，我再按那个给你拆细。"
    return f"小贝先给你列几个预算参考：{'；'.join(candidates)}。你主要想看哪个方向，我再按那个拆细。"


def _project_message(state: AgentState) -> str:
    content = state.get("normalized_content") or ""
    image_info = state.get("image_info") or {}
    project_items = state.get("tool_results", {}).get("project_qa", {}).get("items") or []
    project = _extract_project(content)
    if _is_generic_project_intro(content):
        return "可以呀，小贝先按你想了解项目来讲：你可以直接说想改善斑点、痘印、毛孔、暗沉、松弛这类问题，我再按方向给你介绍；如果有照片也可以发我，我会先看可见问题。"
    if _is_unclear_need(content):
        return "可以的，小贝先不直接给你报项目名。你说脸看着累，常见会先看肤色暗沉、眼周疲惫、肤质粗糙或轮廓松弛这几类；你可以发张自然光照片，我先帮你看主要像哪一类。"
    visible_items = image_info.get("visible_concerns") or _known_visible_concerns_from_state(state)
    if visible_items:
        visible = "、".join(str(item) for item in visible_items[:4])
        if _has_image_concern(image_info, ["点状斑", "褐色斑点", "色沉", "肤色不均", "斑点"]):
            return f"我先帮你看下，图片里主要能看到{visible}。方向上可以先看淡斑和肤色改善：一类更偏肤色不均、泛红暗沉和浅层色沉，另一类更偏点状色素，具体看斑的深浅和范围。"
        if any(term in visible for term in ["点状斑", "褐色斑点", "色沉", "肤色不均", "斑点"]):
            return f"结合你前面发的照片/描述，主要是{visible}。方向上可以先看淡斑和肤色改善：先分肤色不均/浅层色沉，和点状色素这两类方向。"
        if _has_image_concern(image_info, ["痘印", "痘坑", "毛孔", "泛红"]):
            return f"我先帮你看下，图片里主要能看到{visible}。如果偏泛红和肤色不均，可以先看温和光电或修护方向；如果是凹陷痘坑，就不是同一类处理方式。"
        return f"图片小贝看到了，主要可见{visible}。你告诉我最想改善哪一点，我就按那个方向帮你看项目。"
    if project == "皮秒" and project_items:
        return "皮秒属于光电淡斑方向，通常会围绕色素、肤色不均这类问题看；你说点状斑为主，重点还是看斑的深浅、范围和皮肤耐受。"
    if "斑" in content or "淡斑" in content or "祛斑" in content or "点状" in content:
        if "点状" in content:
            return "嗯，点状斑想淡化的话，一般会先往光电淡斑方向看，区别主要看斑的深浅、范围和皮肤耐受。"
        return "可以的，淡斑类一般先看斑型、深浅和范围，再判断适合肤色改善、针对性色素淡化还是组合方案。"
    if "痘印" in content:
        return "痘印要先看是红痘印、黑痘印还是凹陷痘坑，方向会不一样，小贝可以先按你主要问题帮你拆。"
    if "毛孔" in content:
        return "毛孔问题要看是出油粗大、黑头明显还是皮肤松弛带来的毛孔感，项目方向会不一样。"
    if any(term in content for term in ["变白", "美白", "亮一点", "提亮", "暗沉"]):
        return "想提亮变白一点的话，小贝会先分两类看：如果是肤色不均、泛红和浅层色沉，偏肤色改善方向；如果是缺水暗沉、肤质粗糙，偏修护补水方向。"
    return "小贝可以先按你的主要困扰帮你看项目方向，你直接说想改善斑、痘印、毛孔、暗沉还是抗衰就行。"


def _project_followup_message(state: AgentState) -> str:
    visible_items = (state.get("image_info") or {}).get("visible_concerns") or _known_visible_concerns_from_state(state)
    if visible_items:
        visible = "、".join(str(item) for item in visible_items[:4])
        return f"换个说法哈，结合你前面照片里的{visible}，可以先按淡斑和肤色改善方向看；一类偏肤色不均/浅层色沉，另一类偏点状色素。"
    content = state.get("normalized_content") or ""
    project = _extract_project(content)
    if project:
        return f"换个说法哈，{project}我会先按你想改善的问题来判断，不只看项目名；你最在意的是效果、恢复期还是价格？"
    return "换个说法哈，小贝先按你最想改善的点来帮你拆项目方向，比单纯报项目名更准一些。"


def _trust_message(state: AgentState) -> str:
    items = state.get("tool_results", {}).get("trust_assets", {}).get("items") or []
    content = state.get("normalized_content") or ""
    store_hint = "厦门店" if "厦门" in content else "门店"
    intent_set = {item.get("intent") for item in state.get("intents", [])}
    image_guidance = "" if intent_set & {"price_inquiry", "campaign_inquiry"} else _image_guidance_inline(state, _extract_project(content))
    if _is_identity_question(content):
        return "我是小贝，先帮你把问题说清楚；涉及价格、项目适合度或售后这类具体判断，我也会按流程帮你同步专业同事确认。你直接说现在最想问哪一点就行。"
    if _has_effect_guarantee_request(content):
        return "这个小贝不能跟你承诺“一次一定有效”。淡化效果会受个人基础、斑的深浅范围、项目配置和后续护理影响；更稳妥的是先看清楚情况，再判断适合的方案和预期。"
    if items:
        base = f"你先确认正规性是对的哈，医美确实不能只听口头介绍。小贝可以先帮你按{store_hint}的资质核验、产品来源和服务保障这几块说明，具体证照以门店/官方渠道核验为准。"
    else:
        base = "你先确认正规性是对的哈，医美确实不能只听口头介绍。小贝可以从资质、产品来源和服务保障这几块帮你对清楚。"
    return f"{base}{image_guidance}"


def _memory_context_sentence(state: AgentState) -> str:
    content = state.get("normalized_content") or ""
    portrait = state.get("customer_profile") or {}
    if not isinstance(portrait, dict):
        return ""
    pain_points = [str(item) for item in portrait.get("pain_points", []) if item]
    needs = [str(item) for item in portrait.get("needs", []) if item]
    if "热玛吉" in content or "超声炮" in content:
        if any(need in needs for need in ["抗衰", "紧致", "轮廓改善"]):
            return "你前面提到过抗衰或紧致需求，这类项目还要结合松弛程度和部位看配置。"
        return ""
    if "水光" in content:
        if any(need in needs for need in ["补水", "肤质改善", "肤色改善"]) or any(point in pain_points for point in ["暗沉", "肤色不均", "面部色沉"]):
            return "我也会结合你前面的肤质改善需求来看水光配置，不只按项目名判断。"
        return ""
    if "点状斑点" in pain_points:
        return "你前面说点状斑为主，这类还是要看斑的深浅和范围，再确认更适合的项目配置。"
    if "面部色沉" in pain_points:
        return "你前面提到有面部色沉，后面看项目时要结合色沉范围、深浅和恢复期一起判断。"
    if needs:
        return f"我也会结合你前面提到的{needs[0]}需求来帮你看，不会只按项目名直接判断。"
    return ""


def _store_messages(state: AgentState, *, start_order: int) -> list[dict[str, Any]]:
    lookup = state.get("tool_results", {}).get("store_lookup", {})
    stores = lookup.get("stores", []) if isinstance(lookup, dict) else []
    content = state.get("normalized_content") or ""
    if not stores:
        city = str(lookup.get("city") or _extract_city(content) or "").strip() if isinstance(lookup, dict) else _extract_city(content)
        if city:
            return [
                {
                    "type": "text",
                    "order": start_order,
                    "content": f"小贝先按{city}帮你查了下，目前没有匹配到可用门店信息；我这边不拿其他城市门店来代替发你。",
                }
            ]
        return [
            {
                "type": "text",
                "order": start_order,
                "content": "你在哪个城市呀？小贝确认后直接发你附近门店和地址。",
            }
        ]

    wants_parking = bool(lookup.get("wants_parking")) or "停车" in content
    wants_route = bool(lookup.get("wants_route")) or any(term in content for term in ["地址", "导航", "哪里", "怎么过去", "位置"])
    messages: list[dict[str, Any]] = []
    if len(stores) == 1:
        store = stores[0]
        content_parts = [f"{store.get('name')}地址：{store.get('address')}"]
        if store.get("business_hours"):
            content_parts.append(f"营业时间：{store.get('business_hours')}")
        if wants_route and store.get("map_url"):
            content_parts.append(f"导航：{store.get('map_url')}")
        if wants_parking:
            parking = _parking_text(store)
            if parking:
                content_parts.append(parking)
        messages.append({"type": "text", "order": start_order, "content": "\n".join(content_parts)})
        return messages

    city = lookup.get("city") or _extract_city(content) or "这边"
    lines = [f"{city}这边小贝先匹配到{len(stores)}家门店，你看哪家更方便："]
    for index, store in enumerate(stores[:3], start=1):
        line_parts = [f"{index}. {store.get('name')}：{store.get('address')}"]
        if wants_parking:
            parking = _parking_text(store)
            if parking:
                line_parts.append(parking)
        lines.append("\n".join(line_parts))
    messages.append({"type": "text", "order": start_order, "content": "\n".join(lines)})
    return messages


def _parking_text(store: dict[str, Any]) -> str:
    parking_name = str(store.get("parking_name") or "").strip()
    parking_address = str(store.get("parking_address") or "").strip()
    if parking_name and parking_address:
        return f"停车：{parking_name}，{parking_address}"
    if parking_name:
        return f"停车：{parking_name}"
    if parking_address:
        return f"停车：{parking_address}"
    return ""


def _after_sales_message(state: AgentState) -> str:
    output = _module_output_for_skill(state, "after_sales")
    reply_points = output.get("reply_points", []) if isinstance(output, dict) else []
    missing_slots = output.get("missing_slots", []) if isinstance(output, dict) else []
    risk_flags = output.get("risk_flags", []) if isinstance(output, dict) else []
    content = state.get("normalized_content") or ""

    if risk_flags:
        return "你这个情况小贝先不在线判断，涉及明显不适或加重风险，我帮你同步给专业同事协助确认；你先补充做的项目、做完第几天和照片。"

    if any(term in content for term in ["三天", "3天", "第三天"]) and any(term in content for term in ["泛红", "有点红", "发红", "红"]) and _denies_severe_after_sales(content):
        return "做完三天有点红/泛红，但你说没有流脓和发烧的话，先按轻度反应护理观察：这几天重点保湿修复和防晒，别刷酸、去角质或用刺激性护肤；如果红肿疼痛加重，及时让专业同事看。"
    if _denies_severe_after_sales(content) and any(term in content for term in ["干", "干燥", "轻微", "泛红", "不疼", "没有疼"]):
        return "没有流脓、发烧或明显加重的话，先按轻度护理处理：这两天重点保湿修复和防晒，先别用酸类、去角质或刺激性护肤；如果红肿疼痛加重，再让护理老师看。"
    if "泛红" in content and not any(term in content for term in SEVERE_AFTER_SALES_KEYWORDS):
        return "轻微泛红要结合项目和做完第几天看。你先注意保湿修复和防晒，别刷酸、去角质或用刺激性护肤；方便的话补充做完第几天、有没有疼痛加重，再发张照片，小贝帮你对照护理建议看。"

    if "反黑" in content or "变黑" in content:
        return "先别急，光电后局部颜色变化要结合做完第几天、照片和最近防晒情况看，短期不能直接判断是不是反黑。你先发张照片，再告诉小贝做完第几天、最近有没有暴晒，我帮你对照护理建议看。"

    base = str(reply_points[0]) if reply_points else "做完后的反应要结合项目和时间看，小贝先帮你把情况问清楚。"
    if missing_slots:
        uncovered = [str(item) for item in missing_slots if item and not _after_sales_slot_covered(str(item), base)]
        slots = "、".join(uncovered[:3])
        if slots:
            return f"{base} 你再补充下{slots}就行，小贝帮你对照护理建议看。"
    return base


def _after_sales_slot_covered(slot: str, base: str) -> bool:
    if "项目" in slot and "项目" in base:
        return True
    if ("时间" in slot or "第几天" in slot) and ("时间" in base or "第几天" in base):
        return True
    if "照片" in slot and "照片" in base:
        return True
    if "暴晒" in slot and "暴晒" in base:
        return True
    if ("红肿" in slot or "疼痛" in slot or "症状" in slot) and ("症状" in base or "表现" in base or "红肿" in base or "疼" in base):
        return True
    return False


def _competitor_message(state: AgentState) -> str:
    output = _module_output_for_skill(state, "competitor")
    reply_points = output.get("reply_points", []) if isinstance(output, dict) else []
    missing_slots = output.get("missing_slots", []) if isinstance(output, dict) else []
    content = state.get("normalized_content") or ""
    project = _extract_project(content) or _recent_project_from_state(state)
    price_digits = _extract_price_digits(content)
    scenario = _competitor_scenario(content)
    if price_digits:
        subject = f"{project}{price_digits[0]}" if project else f"{price_digits[0]}这个价格"
        base = f"能理解的，你会拿{subject}来对比很正常。小贝不直接按别家的数字压价，主要是要先看产品、剂量、部位、次数和售后是不是同一套配置。"
    elif scenario in {"price_compare", "effect_claim", "quote_compare", "fear_trap"}:
        base = _competitor_default_reply(content, project, price_digits, scenario)
    else:
        base = str(reply_points[0]) if reply_points else "多对比一下是对的哈。小贝建议别只看项目名和数字，还要看产品、剂量、部位、次数、操作人员和售后是不是一致。"
    if missing_slots and any(word in (state.get("normalized_content") or "") for word in ["截图", "报价", "套餐"]):
        slots = "、".join(str(item) for item in missing_slots[:4] if item)
        if slots:
            return f"{base} 你可以把{slots}这几项发我，小贝帮你一起拆开对比。"
    return base


def _handoff_message(state: AgentState) -> str:
    terms = set(state.get("guardrail_result", {}).get("terms", []))
    content = state.get("normalized_content") or ""
    if terms & set(SEVERE_AFTER_SALES_KEYWORDS) or any(term in content for term in SEVERE_AFTER_SALES_KEYWORDS):
        return "你这个情况小贝先不在线判断，也别自行挤压或乱用药。你先发一张清晰照片，再补充做的项目和做完第几天，我帮你同步给专业同事协助确认。"
    if terms & {"退款", "投诉", "维权", "曝光", "骗子", "骗人", "骗钱", "骗我", "效果纠纷"}:
        recent = " ".join(_recent_assistant_replies(state, limit=4))
        if any(term in recent for term in ["专业同事", "协助核实", "同步给"]):
            return "小贝已经把你的反馈和处理诉求记录下来了，这个我继续同步专业同事跟进；你这边先保留付款记录、项目记录和照片，后面核实时会用到。"
        image_guidance = _image_guidance_inline(state, "")
        image_guidance = re.sub(r"^另外[，,]\s*", "", image_guidance)
        prefix = f"{image_guidance}" if image_guidance else ""
        if any(term in content for term in ["退款", "投诉", "维权", "曝光", "骗我钱", "骗钱"]):
            return f"{prefix}你这个反馈小贝先认真记录下来，涉及投诉、退款或费用争议，不能在聊天里直接给处理结论。我会让专业同事结合付款记录、项目记录和沟通记录继续协助核实。"
        return f"{prefix}你刚才反馈做完没有看到淡化、体验也不满意，这个不能只当普通咨询处理。小贝先帮你把情况记录下来，让专业同事结合你做的项目、时间和照片一起协助核实。"
    if "未成年" in terms:
        return "未成年相关项目需要更谨慎，小贝先不直接报价格或判断能不能做，我帮你同步专业同事确认咨询和服务要求。"
    if terms & {"孕妇", "哺乳期", "病例", "诊断证明"}:
        return "这个情况需要更谨慎，小贝先不直接给结论，我帮你同步给专业同事协助确认。"
    return "这个情况小贝先帮你记录下，涉及具体判断和处理，我让专业同事接着协助你确认。"


def _module_output_for_skill(state: AgentState, skill: str) -> dict[str, Any]:
    for output in state.get("module_outputs", []):
        if isinstance(output, dict) and output.get("skill") == skill:
            return output
    return {}


def _appointment_context_sentence(state: AgentState) -> str:
    appointment = state.get("appointment_cache") or {}
    if not isinstance(appointment, dict) or not appointment.get("has_active"):
        return ""
    summary = str(appointment.get("summary") or "").strip()
    store_name = str(appointment.get("store_name") or "").strip()
    appointment_time = str(appointment.get("appointment_time") or "").strip()
    if summary:
        return f"另外小贝也看到你这边已有预约记录：{summary}。如果这次要约新的门店或项目，我会按新的需求单独帮你确认。"
    if store_name or appointment_time:
        bits = " ".join(bit for bit in [store_name, appointment_time] if bit)
        return f"另外小贝也看到你这边已有预约记录：{bits}。"
    return "另外小贝也看到你这边已有预约记录，后面涉及改约、取消或查时间时会一起帮你对照。"


def _should_show_appointment_context(state: AgentState) -> bool:
    intents = {item.get("intent") for item in state.get("intents", [])}
    content = state.get("normalized_content") or ""
    if intents & {"appointment_confirm", "appointment_change", "appointment_cancel"}:
        return True
    if "appointment_intent" in intents:
        recent = " ".join(_recent_assistant_replies(state, limit=5))
        if any(term in recent for term in ["已有预约记录", "已有预约", "约的是", "预约记录"]):
            return any(term in content for term in ["我有没有预约", "我约的是", "预约成功", "改约", "取消预约", "帮我取消", "换个时间"])
        return any(term in content for term in ["我有没有预约", "我约的是", "预约成功", "改约", "取消预约", "帮我取消", "换个时间", "再约", "重新约"])
    explicit_terms = [
        "我有没有预约",
        "我约的是",
        "约的是几点",
        "预约成功",
        "查一下预约",
        "改约",
        "改时间",
        "取消预约",
        "帮我取消",
        "明天不去了",
        "换个时间",
    ]
    return any(term in content for term in explicit_terms)


def _appointment_message(state: AgentState) -> str:
    content = state.get("normalized_content") or ""
    appointment = state.get("appointment_cache") or {}
    asks_existing = any(
        term in content
        for term in [
            "我有没有预约",
            "有没有约",
            "是不是约了",
            "我约的是",
            "约的是几点",
            "预约成功",
            "查一下预约",
            "查下预约",
            "帮我查",
            "我之前是不是",
            "之前是不是",
        ]
    )
    asks_change_or_cancel = any(word in content for word in ["取消", "不去", "改约", "改时间", "换个时间"])
    if isinstance(appointment, dict) and appointment.get("has_active") and (asks_existing or asks_change_or_cancel):
        return _appointment_context_sentence(state)
    if asks_existing:
        return "小贝这边暂时没有看到当前有效预约记录；如果你是用别的手机号或其他门店约的，我可以再按门店、时间帮你核对。"
    if asks_change_or_cancel:
        return "可以，小贝先帮你按当前预约记录去确认，涉及改约或取消需要再和门店核对一下。"
    available = state.get("tool_results", {}).get("available_time") or {}
    if isinstance(available, dict) and (available.get("slots") or available.get("missing") or available.get("error")):
        return _available_time_message(state, available)
    if _extract_time_text(content) and not _has_explicit_location_or_store(content):
        return "可以，小贝先记下你想看的时间；查可约时间需要先确认具体城市或门店，你在哪个城市呀？"
    if _extract_city(content) or _extract_time_text(content):
        return "可以，小贝先按你说的城市/时间意向记录下来，下一步需要确认具体门店和可约时间。"
    return "可以，小贝先帮你看预约方向，下一步需要确认你方便的城市/门店和大概到店时间。"


def _appointment_query_from_state(content: str, store_lookup: dict[str, Any], state: AgentState) -> dict[str, Any]:
    stores = store_lookup.get("stores") if isinstance(store_lookup, dict) else []
    store = stores[0] if _has_explicit_location_or_store(content) and isinstance(stores, list) and stores else {}
    explicit_store_id = state.get("confirmed_store_id") or state.get("store_id")
    explicit_store_name = state.get("confirmed_store_name") or state.get("store_name")
    if explicit_store_id:
        store = {"id": explicit_store_id, "name": explicit_store_name or store.get("name", "")}
    if not store and _can_use_cached_appointment_store(content):
        appointment = state.get("appointment_cache") or {}
        if isinstance(appointment, dict) and appointment.get("store_id"):
            store = {"id": appointment.get("store_id"), "name": appointment.get("store_name", "")}
    date_text = _extract_date_value(content)
    missing = []
    if not store.get("id"):
        missing.append("store_id")
    if not date_text:
        missing.append("date")
    return {
        "store_id": str(store.get("id") or ""),
        "store_name": str(store.get("name") or ""),
        "date": date_text,
        "missing": missing,
    }


def _has_explicit_location_or_store(content: str) -> bool:
    if not content:
        return False
    if _extract_city(content):
        return True
    return any(term in content for term in ["店", "门店", "这家", "那家", "刚刚那家", "附近", "地址", "上海", "厦门", "重庆", "成都", "北京", "广州", "深圳"])


def _can_use_cached_appointment_store(content: str) -> bool:
    if not content:
        return False
    return any(term in content for term in ["原来那家", "之前那家", "上次那家", "预约的门店", "已约的", "还是那家", "改约", "改时间", "换个时间", "取消"])


def _available_time_message(state: AgentState, available: dict[str, Any]) -> str:
    content = state.get("normalized_content") or ""
    missing = available.get("missing") or []
    if "store_id" in missing:
        return "可以，小贝先帮你看可约时间；不过需要先确认具体门店，才能查到当天可预约的时间段。"
    if "date" in missing:
        return "可以，小贝先帮你看这家门店；你告诉我想约哪一天，我再帮你查当天可预约的时间段。"
    if available.get("error"):
        store_name = str(available.get("store_name") or "这家门店")
        date_value = str(available.get("date") or _extract_date_value(content) or "")
        target = f"{store_name} {date_value}".strip()
        return f"小贝先按{target}帮你查可约时间，但当前还没拿到具体空档；我会按这个门店和日期继续帮你核对。"

    slots = available.get("slots") or {}
    if not isinstance(slots, dict):
        slots = {}
    times = _available_time_values(slots)
    preferred_times = _filter_times_by_preference(times, content)
    store_name = str(available.get("store_name") or "这家门店")
    date_value = str(available.get("date") or "")
    target = f"{store_name} {date_value}".strip()
    if preferred_times:
        shown = "、".join(preferred_times[:6])
        more = "等" if len(preferred_times) > 6 else ""
        return f"我先帮你看了下，{target}目前可约时间有 {shown}{more}。你看哪个时间方便，小贝再继续帮你确认。"
    if times and any(word in content for word in ["下午", "晚上", "上午", "中午", "6点后", "六点后"]):
        shown = "、".join(times[:6])
        more = "等" if len(times) > 6 else ""
        return f"我先帮你看了下，{target}暂时没看到完全匹配你说的时段，其他可约时间有 {shown}{more}。你看要不要换个时间段，小贝再继续帮你确认。"
    return f"我先帮你看了下，{target}暂时没有看到明确可约时间，小贝可以再帮你换一天或换附近门店看看。"


def _available_time_values(slots: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for key in ["new", "old", "pre", "new_addon", "old_addon"]:
        values = slots.get(key) or []
        if isinstance(values, list):
            for value in values:
                text = str(value).strip()
                if text and text not in result:
                    result.append(text)
    return result


def _filter_times_by_preference(times: list[str], content: str) -> list[str]:
    if not times:
        return []

    def hour_of(value: str) -> int:
        try:
            return int(value.split(":", 1)[0])
        except (ValueError, IndexError):
            return -1

    if "上午" in content:
        return [time for time in times if 0 <= hour_of(time) < 12]
    if "中午" in content:
        return [time for time in times if 11 <= hour_of(time) < 14]
    if "下午" in content:
        return [time for time in times if 12 <= hour_of(time) < 18]
    if "晚上" in content or "6点后" in content or "六点后" in content:
        return [time for time in times if hour_of(time) >= 18]
    return times


def _extract_date_value(content: str) -> str:
    explicit = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", content)
    if explicit:
        year, month, day = [int(part) for part in explicit.groups()]
        return date(year, month, day).isoformat()
    today = date.today()
    if "今天" in content:
        return today.isoformat()
    if "明天" in content:
        return (today + timedelta(days=1)).isoformat()
    if "后天" in content:
        return (today + timedelta(days=2)).isoformat()
    weekday_map = {
        "周一": 0,
        "星期一": 0,
        "周二": 1,
        "星期二": 1,
        "周三": 2,
        "星期三": 2,
        "周四": 3,
        "星期四": 3,
        "周五": 4,
        "星期五": 4,
        "周六": 5,
        "星期六": 5,
        "周日": 6,
        "星期日": 6,
        "周末": 5,
    }
    for text, target in weekday_map.items():
        if text in content:
            days = (target - today.weekday()) % 7
            if days == 0:
                days = 7
            return (today + timedelta(days=days)).isoformat()
    month_day = re.search(r"(\d{1,2})月(\d{1,2})[日号]?", content)
    if month_day:
        month, day = [int(part) for part in month_day.groups()]
        year = today.year
        candidate = date(year, month, day)
        if candidate < today:
            candidate = date(year + 1, month, day)
        return candidate.isoformat()
    return ""


def _extract_profile_update(state: AgentState) -> dict[str, Any]:
    content = state.get("normalized_content") or ""
    image_info = state.get("image_info", {})
    intents = {item.get("intent") for item in state.get("intents", [])}

    needs: list[str] = []
    pain_points: list[str] = []
    projects: list[str] = []
    concerns: list[str] = []
    style_tags: list[str] = []

    if "祛斑" in content or "淡斑" in content or "斑" in content:
        needs.extend(["祛斑", "淡斑"])
        if "点状" in content:
            pain_points.append("点状斑点")
        else:
            pain_points.append("面部斑点")
    if _has_image_concern(image_info, ["点状斑", "点状褐色", "褐色斑点", "斑点", "色沉", "肤色不均"]):
        needs.extend(["祛斑", "淡斑"])
        if _has_image_concern(image_info, ["点状斑", "点状褐色", "褐色斑点"]):
            pain_points.append("点状斑点")
        elif _has_image_concern(image_info, ["片状", "色沉", "肤色不均"]):
            pain_points.append("面部色沉")
        else:
            pain_points.append("面部斑点")
    if "色沉" in content or "暗沉" in content:
        pain_points.append("面部色沉")
        needs.append("肤色改善")
    if _has_image_concern(image_info, ["暗沉", "肤色不均"]):
        pain_points.append("肤色不均")
        needs.append("肤色改善")
    if _has_image_concern(image_info, ["毛孔"]):
        pain_points.append("毛孔明显")
        needs.append("肤质改善")
    if _has_image_concern(image_info, ["痘印"]):
        pain_points.append("痘印")
        needs.append("淡化痘印")
    if "痘印" in content:
        pain_points.append("痘印")
        needs.append("淡化痘印")
    if "毛孔" in content:
        pain_points.append("毛孔明显")
        needs.append("肤质改善")

    for concern in image_info.get("visible_concerns", []) or []:
        normalized = str(concern)
        if normalized and normalized not in pain_points:
            pain_points.append(normalized)
    for project in PROJECT_KEYWORDS:
        if project in content and project not in projects:
            projects.append(project)

    if "trust_issue" in intents:
        concerns.append("担心正规性或服务保障")
        style_tags.append("谨慎观望")
    if "price_inquiry" in intents:
        concerns.append("关注价格")
        style_tags.append("直接问价")
    if "competitor_compare" in intents:
        style_tags.append("喜欢对比")

    update: dict[str, Any] = {}
    if needs or pain_points or projects or concerns or style_tags:
        update["portrait"] = {
            "summary": _profile_summary(needs, pain_points, projects, concerns),
            "needs": _dedupe_strings(needs),
            "pain_points": _dedupe_strings(pain_points),
            "projects": _dedupe_strings(projects),
            "concerns": _dedupe_strings(concerns),
            "budget_sens": "high" if "price_inquiry" in intents and ("贵" in content or "预算" in content or "多少钱" in content) else "unknown",
            "intent_level": "medium" if intents & {"price_inquiry", "project_inquiry", "image_inquiry"} else "weak",
            "trust_level": "low" if "trust_issue" in intents else "unknown",
            "decision_stage": "了解中",
            "style_tags": _dedupe_strings(style_tags),
        }
    city = _extract_city(content)
    if city:
        update["basic_info"] = {"city": city}
    return update


def _extract_event_updates(state: AgentState, profile_update: dict[str, Any]) -> list[dict[str, Any]]:
    content = state.get("normalized_content") or ""
    intents = state.get("intents", [])
    if not intents and not profile_update:
        return []

    events: list[dict[str, Any]] = []
    for index, item in enumerate(intents[:3], start=1):
        event_type = _event_type_for_intent(str(item.get("intent")))
        if not event_type:
            continue
        facts = _event_facts(event_type, content, state)
        events.append(
            {
                "event_id": f"evt_{state.get('request_id', 'unknown')}_{index}",
                "event_time": "",
                "event_type": event_type,
                "stage": state.get("route_result", {}).get("scene", "S3_deep_consult"),
                "summary": _event_summary(event_type, facts),
                "facts": facts,
                "impact": _event_impact(event_type),
                "confidence": 0.78,
            }
        )
    return events


def _event_facts(event_type: str, content: str, state: AgentState) -> dict[str, Any]:
    image_info = state.get("image_info", {})
    project = _extract_project(content)
    if event_type == "price_inquiry":
        return {"project": project, "price_focus": "价格咨询", "budget_sens": "high" if "贵" in content or "预算" in content else "unknown"}
    if event_type == "project_inquiry":
        return {
            "project": project,
            "question_focus": "项目方向",
            "visible_concerns": image_info.get("visible_concerns", []),
            "image_desc": image_info.get("image_desc", ""),
        }
    if event_type == "image_inquiry":
        return {
            "image_type": image_info.get("image_type", ""),
            "image_intent": image_info.get("image_intent", ""),
            "body_part": image_info.get("body_part", ""),
            "visible_concerns": image_info.get("visible_concerns", []),
            "text_clues": image_info.get("text_clues", []),
        }
    if event_type == "trust_issue":
        return {"concern": "正规性/服务保障", "trust_level": "low"}
    if event_type == "store_inquiry":
        return {"city": _extract_city(content), "location_focus": "门店/地址/路线"}
    if event_type == "appoint_intent":
        return {"intent_level": "medium", "preferred_time": _extract_time_text(content), "preferred_store": ""}
    if event_type == "after_sales":
        return {"issue": "售后/恢复咨询", "severity": "unknown"}
    if event_type == "competitor_compare":
        return {"compare_focus": "竞品/报价对比"}
    return {}


def _event_type_for_intent(intent: str) -> str:
    return {
        "price_inquiry": "price_inquiry",
        "ad_price_check": "price_inquiry",
        "campaign_inquiry": "campaign_inquiry",
        "project_inquiry": "project_inquiry",
        "case_request": "project_inquiry",
        "project_process": "project_inquiry",
        "image_inquiry": "image_inquiry",
        "trust_issue": "trust_issue",
        "store_inquiry": "store_inquiry",
        "appointment_intent": "appoint_intent",
        "after_sales": "after_sales",
        "competitor_compare": "competitor_compare",
        "human_request": "human_request",
    }.get(intent, "")


def _event_summary(event_type: str, facts: dict[str, Any]) -> str:
    if event_type == "price_inquiry":
        project = facts.get("project") or "项目"
        return f"客户咨询{project}价格。"
    if event_type == "project_inquiry":
        return "客户咨询项目方向或适合项目。"
    if event_type == "image_inquiry":
        return "客户上传图片进行面诊类咨询。"
    if event_type == "trust_issue":
        return "客户表达正规性或服务保障顾虑。"
    if event_type == "store_inquiry":
        return "客户咨询门店、地址或路线信息。"
    if event_type == "appoint_intent":
        return "客户表达预约或到店意向。"
    if event_type == "after_sales":
        return "客户咨询售后或恢复相关问题。"
    if event_type == "competitor_compare":
        return "客户提到竞品或外部报价对比。"
    return "客户产生新的业务咨询事件。"


def _event_impact(event_type: str) -> str:
    return {
        "price_inquiry": "后续回复应承接价格敏感和项目配置说明。",
        "project_inquiry": "后续可围绕需求、照片和适合项目继续沟通。",
        "image_inquiry": "后续项目和画像节点应优先使用图片理解结果。",
        "trust_issue": "后续应优先建立信任，避免强推。",
        "store_inquiry": "后续可继续承接门店和到店路径。",
        "appoint_intent": "后续应确认门店和时间，并检查已有预约。",
        "after_sales": "后续应谨慎收集项目、时间、症状，必要时升级专业人士。",
        "competitor_compare": "后续应不跟价不诋毁，拆清对比维度。",
    }.get(event_type, "后续客服回复可参考该事件。")


def _profile_summary(needs: list[str], pain_points: list[str], projects: list[str], concerns: list[str]) -> str:
    parts = []
    if pain_points:
        parts.append(f"关注{ '、'.join(_dedupe_strings(pain_points)[:3]) }")
    if needs:
        parts.append(f"希望{ '、'.join(_dedupe_strings(needs)[:3]) }")
    if projects:
        parts.append(f"提到项目{ '、'.join(_dedupe_strings(projects)[:3]) }")
    if concerns:
        parts.append(f"顾虑{ '、'.join(_dedupe_strings(concerns)[:2]) }")
    return "，".join(parts) + "。" if parts else ""


def _extract_city(content: str) -> str:
    for city in CITY_NAMES:
        if city in content:
            return city
    return ""


def _request_context_from_state(state: AgentState) -> dict[str, Any]:
    context = dict(state.get("request_context") or {})
    fields = {
        "user_id": state.get("user_id"),
        "corp_id": state.get("corp_id"),
        "wechat": state.get("wechat"),
        "external_userid": state.get("external_userid"),
        "customer_id": state.get("customer_id"),
        "customer_add_wechat_id": state.get("customer_add_wechat_id"),
        "confirmed_store_id": state.get("confirmed_store_id"),
        "confirmed_store_name": state.get("confirmed_store_name"),
        "store_id": state.get("store_id"),
        "store_name": state.get("store_name"),
        "appointment_id": state.get("appointment_id"),
        "appointment_time": state.get("appointment_time"),
    }
    for key, value in fields.items():
        if value not in (None, ""):
            context[key] = value
    return context


def _store_query_from_state(content: str, state: AgentState) -> str:
    content = (content or "").strip()
    city = _extract_city(content) or _known_city_from_state(state)
    area = _extract_store_area(content)
    explicit_store = str(state.get("confirmed_store_name") or state.get("store_name") or "").strip()
    parts = []
    if city and city not in content:
        parts.append(city)
    if area and area not in content:
        parts.append(area)
    if explicit_store and explicit_store not in content:
        parts.append(explicit_store)
    parts.append(content)
    return " ".join(part for part in parts if part).strip()


def _known_city_from_state(state: AgentState) -> str:
    basic = state.get("customer_basic_info") or {}
    if isinstance(basic, dict):
        city = str(basic.get("city") or "").strip()
        if city:
            return city
    for event in reversed(state.get("history_events", [])[-10:]):
        if isinstance(event, dict):
            facts = event.get("facts") if isinstance(event.get("facts"), dict) else {}
            city = str(facts.get("city") or "").strip()
            if city:
                return city
            text = json_dumps(event)
        else:
            text = str(event)
        city = _extract_city(text)
        if city:
            return city
    for message in reversed(state.get("conversation_history", [])[-10:]):
        city = _extract_city(str(message))
        if city:
            return city
    profile = state.get("customer_profile") or {}
    if isinstance(profile, dict):
        city = _extract_city(json_dumps(profile))
        if city:
            return city
    return ""


def _extract_store_area(content: str) -> str:
    for area in ["虹口", "浦东", "嘉定", "思明", "湖里", "百星", "渝北", "南岸", "渝中", "大坪"]:
        if area in content:
            return area
    return ""


def _extract_time_text(content: str) -> str:
    for word in ["今天", "明天", "后天", "周六", "周日", "周末", "上午", "下午", "晚上"]:
        if word in content:
            return word
    return ""


def _dedupe_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in result:
            result.append(text)
    return result


def _price_point_from_row(name: str, row: dict[str, Any], *, requested_project: str = "", source: str = "") -> str:
    prefix = ""
    if requested_project and requested_project not in name and source == "local_xlsx":
        if _requires_exact_price(requested_project):
            return f"当前价格表没看到{requested_project}单项，小贝先不拿其他淡斑产品价格代替报价。"
        prefix = f"当前价格表没看到{requested_project}单项，淡斑相关配置里，"
    new_price = _value(row.get("new_price"))
    promo_price = _value(row.get("promo_price"))
    daily_price = _value(row.get("daily_price"))
    if new_price and promo_price:
        return f"{prefix}{name}可以先按新客体验价{new_price}、活动价{promo_price}做预算参考。"
    if new_price:
        return f"{prefix}{name}可以先按新客体验价{new_price}做预算参考。"
    if promo_price:
        return f"{name}可以先按活动价{promo_price}做预算参考。"
    if daily_price:
        return f"{name}日常单次价是{daily_price}，可以先作为预算参考。"
    return f"{name}价格需要结合当前配置确认。"


def _filter_pricing_rows_for_project(rows: list[dict[str, Any]], project: str) -> list[dict[str, Any]]:
    project = _canonical_price_project(project)
    if _is_broad_price_category(project):
        return []
    if not project or not _requires_exact_price(project):
        return rows
    aliases = _price_project_aliases(project)
    filtered: list[dict[str, Any]] = []
    for row in rows:
        haystack = " ".join(
            str(row.get(key) or "")
            for key in ["project_name", "price_note", "promo_target", "gift_scene"]
        )
        if any(alias and alias in haystack for alias in aliases):
            filtered.append(row)
    return filtered


def _price_project_aliases(project: str) -> list[str]:
    aliases = {
        "光子嫩肤": ["光子嫩肤", "光子"],
        "光子": ["光子", "光子嫩肤"],
        "皮秒": ["皮秒"],
        "热玛吉": ["热玛吉"],
        "超声炮": ["超声炮"],
    }
    return aliases.get(project, [project])


def _pricing_rows(tool_results: dict[str, Any]) -> list[dict[str, Any]]:
    rows = tool_results.get("pricing_db", {}).get("rows") or []
    if rows:
        return [dict(row, _source="coze_db") for row in rows if isinstance(row, dict)]
    local_rows = tool_results.get("pricing_local", {}).get("rows") or []
    return [dict(row, _source="local_xlsx") for row in local_rows if isinstance(row, dict)]


def _requires_exact_price(project: str) -> bool:
    return project in {"皮秒", "光子", "光子嫩肤", "热玛吉", "超声炮"}


def _is_broad_price_category(project: str) -> bool:
    return str(project or "").strip() in {"淡斑", "祛斑", "斑", "色沉", "肤色不均", "毛孔", "痘印", "痘坑", "抗衰", "紧致"}


def _pricing_sql(content: str) -> str:
    project = _extract_project(content)
    if not project:
        return "SELECT * FROM items_pricing_system WHERE 1=0"
    escaped = project.replace("'", "''")
    return f"SELECT * FROM items_pricing_system WHERE project_name LIKE '%{escaped}%' AND status='true' ORDER BY id LIMIT 10"


def _pricing_sql_from_state(state: AgentState) -> str:
    project = _contextual_price_project(state)
    if not project:
        return "SELECT * FROM items_pricing_system WHERE 1=0"
    escaped = project.replace("'", "''")
    return f"SELECT * FROM items_pricing_system WHERE project_name LIKE '%{escaped}%' AND status='true' ORDER BY id LIMIT 10"


def _extract_project(content: str) -> str:
    for word in PROJECT_KEYWORDS:
        if word in content:
            return word
    return ""


def _canonical_price_project(project: str) -> str:
    project = str(project or "").strip()
    return PRICE_PROJECT_ALIASES.get(project, project)


def _recent_project_from_state(state: AgentState) -> str:
    content = state.get("normalized_content") or ""
    project = _extract_project(content)
    if project:
        return project
    for message in reversed(state.get("conversation_history", [])[-10:]):
        project = _extract_project(str(message))
        if project:
            return project
    profile = state.get("customer_profile") or {}
    if isinstance(profile, dict):
        for item in profile.get("projects", []) or []:
            project = _extract_project(str(item))
            if project:
                return project
    return ""


def _contextual_price_project(state: AgentState) -> str:
    content = state.get("normalized_content") or ""
    project = _recent_project_from_state(state)
    if project:
        return _canonical_price_project(project)
    if any(term in content for term in ["脸上的斑", "脸上有斑", "斑点", "色沉", "肤色不均", "淡斑", "祛斑"]):
        return "淡斑"
    if any(term in content for term in ["痘印", "痘坑"]):
        return "痘印"
    if "毛孔" in content:
        return "毛孔"
    image_info = state.get("image_info") or {}
    if _has_image_concern(image_info, ["点状斑", "褐色斑点", "色沉", "肤色不均", "斑点"]):
        return "淡斑"
    if _has_image_concern(image_info, ["痘印", "痘坑"]):
        return "痘印"
    if _has_image_concern(image_info, ["毛孔"]):
        return "毛孔"
    history = _recent_conversation_text(state)
    if any(term in history for term in ["点状斑", "小斑点", "色沉", "淡斑", "祛斑"]):
        return "淡斑"
    return ""


def _price_bits(row: dict[str, Any]) -> list[str]:
    name = row.get("project_name", "相关项目")
    result = []
    for key, label in [("new_price", "新客体验价"), ("promo_price", "活动价"), ("daily_price", "日常单次价"), ("old_price", "老客单次价")]:
        value = _value(row.get(key))
        if value:
            result.append(f"{name}{label}{value}")
    return result


def _price_fact_for_brief(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_name": row.get("project_name") or row.get("price_note") or "相关项目",
        "new_price": _value(row.get("new_price")),
        "promo_price": _value(row.get("promo_price")),
        "daily_price": _value(row.get("daily_price")),
        "old_price": _value(row.get("old_price")),
        "old_card": str(row.get("old_card") or "").strip(),
        "promo_target": str(row.get("promo_target") or "").strip(),
        "price_note": str(row.get("price_note") or "").strip(),
        "source": row.get("_source") or "",
    }


def _price_risk_terms(content: str) -> list[str]:
    terms = []
    for word in ["底价", "最低价", "再便宜", "便宜点", "太贵", "贵了", "预算不够", "别家", "同价", "活动价", "套餐", "半脸", "未成年"]:
        if word in content:
            terms.append(word)
    return terms


def _value(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text in {"0", "0.00", "None", "null"}:
        return ""
    if re.fullmatch(r"\d+(\.0+)?", text):
        text = text.split(".")[0]
    return f"{text}元" if text.isdigit() else text


def _safe_query(content: str, skill: Any) -> str:
    text = (content or "").strip()
    if text and not _looks_bad_text(text):
        skill_name = str(skill)
        project = _extract_project(text)
        needs = []
        if "淡斑" in text:
            needs.append("淡斑")
        if "祛斑" in text:
            needs.append("祛斑")
        if "点状" in text:
            needs.append("点状斑")
        if "片状" in text:
            needs.append("片状斑")
        if skill_name == "project_consult":
            if _has_case_request(text):
                parts = [project, *needs, "案例", "效果", "前后对比", "改善参考"]
                return " ".join(part for part in parts if part).strip()
            if _has_project_process_question(text):
                parts = [project, *needs, "操作流程", "时长", "恢复", "注意事项"]
                return " ".join(part for part in parts if part).strip()
            parts = [project, *needs, "项目建议", "适合人群"]
            return " ".join(part for part in parts if part).strip()
        if skill_name == "price_consult":
            if _has_ad_price_check(text):
                parts = [project, *_extract_price_digits(text)[:2], "广告价", "预约金", "尾款", "包含项", "是否另收费"]
                return " ".join(part for part in parts if part).strip()
            return project or "项目价格"
        if skill_name == "trust_build":
            return "正规 资质 医疗机构执业许可证 门店"
        if skill_name == "competitor":
            terms = _competitor_query_terms(text)
            return " ".join(part for part in [project, *terms, "竞品对比", "不诋毁", "不跟价"] if part).strip()
        if skill_name == "after_sales":
            symptoms = _after_sales_query_terms(text)
            return " ".join(part for part in [project, *symptoms, "术后护理"] if part).strip()
        return text
    fallback = {
        "project_consult": "项目咨询 适合项目",
        "price_consult": "项目价格 当前报价",
        "trust_build": "正规 资质 产品来源 服务保障",
        "competitor": "竞品对比 不诋毁 不跟价",
        "after_sales": "术后护理 恢复 注意事项",
    }
    return fallback.get(str(skill), "医美客服咨询")


def _safe_query_from_state(state: AgentState, skill: Any) -> str:
    content = state.get("normalized_content") or ""
    skill_name = str(skill)
    if skill_name == "price_consult":
        if _has_ad_price_check(content):
            project = _canonical_price_project(_contextual_price_project(state) or _extract_project(content))
            parts = [project, *_extract_price_digits(content)[:2], "广告价", "预约金", "尾款", "包含项", "是否另收费"]
            return " ".join(part for part in parts if part).strip()
        project = _canonical_price_project(_contextual_price_project(state))
        return project or "项目价格"
    if skill_name == "project_consult":
        if _has_case_request(content):
            project = _extract_project(content)
            return " ".join(part for part in [project, "案例", "效果", "前后对比", "改善参考"] if part).strip()
        if _has_project_process_question(content):
            project = _extract_project(content)
            return " ".join(part for part in [project, "操作流程", "时长", "恢复", "注意事项"] if part).strip()
        image_info = state.get("image_info") or {}
        if _has_image_concern(image_info, ["点状斑", "褐色斑点", "色沉", "肤色不均", "斑点"]):
            return "淡斑 祛斑 点状斑 色沉 项目建议"
    return _safe_query(content, skill)


def _after_sales_query_terms(content: str) -> list[str]:
    terms: list[str] = []
    mapping = [
        ("结痂", "皮秒 祛斑后结痂 不能抠痂"),
        ("抠", "不能抠痂"),
        ("反黑", "光电术后反黑担心 色沉观察"),
        ("变黑", "光电术后反黑担心 色沉观察"),
        ("红肿", "红肿"),
        ("疼", "疼痛"),
        ("恢复", "恢复期"),
        ("脱皮", "化学焕肤后脱皮 护理建议"),
        ("流脓", "流脓 分泌物"),
        ("出血", "出血"),
        ("没效果", "效果反馈"),
        ("护理", "售后总则 安全优先"),
    ]
    for trigger, query_term in mapping:
        if trigger in content:
            terms.append(query_term)
    return _dedupe_strings(terms)


def _competitor_query_terms(content: str) -> list[str]:
    terms: list[str] = []
    if any(word in content for word in ["别家", "更便宜", "同价", "做到这个价"]):
        terms.append("低价对比")
    if any(word in content for word in ["报价", "截图", "套餐"]):
        terms.append("竞品报价截图")
    if any(word in content for word in ["一次见效", "包效果", "保证"]):
        terms.append("竞品承诺效果")
    if any(word in content for word in ["坑", "套路", "隐形消费"]):
        terms.append("担心被坑")
    terms.extend(_extract_price_digits(content)[:2])
    return _dedupe_strings(terms)


def _looks_bad_text(text: str) -> bool:
    return text.count("?") >= 2 and not any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _subflow_for_skill(skill: str) -> str:
    return {
        "handoff": "HUMAN_HANDOFF",
        "project_consult": "SF3_project_consult",
        "price_consult": "SF7_price_consult",
        "trust_build": "SF10_trust_build",
        "competitor": "SF5_competitor_response",
        "after_sales": "SF12_after_sales",
        "store": "SF6_store_match",
        "appointment": "SF9_appointment",
    }.get(skill, "DIRECT_REPLY")


def _intent_for_skill(skill: str) -> str:
    return {
        "project_consult": "project_inquiry",
        "price_consult": "price_inquiry",
        "trust_build": "trust_issue",
        "competitor": "competitor_compare",
        "after_sales": "after_sales",
        "store": "store_inquiry",
        "appointment": "appointment_intent",
    }.get(skill, "emotion_chat")


def _next_step_for_skill(skill: str, content: str) -> str:
    if skill == "price_consult":
        return "确认项目配置"
    if skill == "project_consult":
        return "补充需求或照片"
    if skill == "trust_build":
        return "提供资质和服务保障说明"
    if skill == "appointment":
        return "确认门店和时间"
    return ""


def _infer_scene(intent: str) -> str:
    if intent in {"appointment_intent", "store_inquiry"}:
        return "S4_appointment_negotiating"
    if intent == "after_sales":
        return "S7_dealed_active"
    return "S3_deep_consult"


def _primary_goal(intents: list[dict[str, Any]]) -> str:
    names = "、".join(str(item["intent"]) for item in intents[:3])
    return f"处理客户本轮的{names}诉求，并在合适时轻度推进项目了解或到店面诊。"


def _renumber(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for message in messages:
        key = (str(message.get("type") or ""), str(message.get("content") or "").strip())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(message)
    for index, message in enumerate(deduped, start=1):
        message["order"] = index
    return deduped

