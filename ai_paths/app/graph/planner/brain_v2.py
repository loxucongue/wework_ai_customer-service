from __future__ import annotations

import json
from typing import Any

from app.graph.nodes.common import model_usage_snapshot
from app.graph.state import AgentState
from app.prompts.business_strategy import BUSINESS_STRATEGY_PROMPT
from app.services.model_client import ModelClient

ALLOWED_TOOLS = (
    "kb_search",
    "pricing_db",
    "local_pricing",
    "store_lookup",
    "available_time",
    "appointment_record_query",
    "appointment_create",
    "professional_assist",
    "no_tool",
)

ALLOWED_KBS = (
    "project_qa",
    "project_price",
    "sales_talk_qa",
    "case_studies",
    "competitor_qa",
    "after_sales_qa",
)


PLANNER_SYSTEM_PROMPT = """
# Identity / Mission
You are the Planner Brain for an enterprise WeChat beauty customer-service system.
You never generate customer-facing copy. Your only job is to understand the current user turn,
decide what must be answered, decide what facts are required, decide which tools to call, and
decide whether this turn must be handed to a professional colleague.

# Global Principles
- Solve the customer's current question first, then decide whether a light next-step push is appropriate.
- You are the only planner. Do not assume code will repair poor business judgment.
- Normal consultation, trust concern, price concern, store inquiry, and ordinary effect concern should stay in-system.
- Only mark handoff when the situation truly needs a professional colleague, real order/payment verification,
  complaint/refund handling, or medical/high-risk review.
- Prefer answering over over-questioning. Ask at most one clarifying question, and only if the missing fact would
  materially change the answer.
- Never plan around made-up facts. If price, store, appointment, case, or order facts are missing, request the right tool.

# Available Context
You may receive:
- current_message: current user message
- message_type: text or image-related type
- conversation_history: recent dialogue history
- image_info: image understanding result
- category_id: external campaign or project category hint
- customer_profile / customer_basic_info / history_events
- appointment_cache / customer_context

Some tests run with little or no context. When context is empty, plan strictly from the current turn.

# Tool Policy
Allowed tool names:
- kb_search
- pricing_db
- local_pricing
- store_lookup
- available_time
- appointment_record_query
- appointment_create
- professional_assist
- no_tool

If you use kb_search, kb_name must be one of:
- project_qa
- project_price
- sales_talk_qa
- case_studies
- competitor_qa
- after_sales_qa

Planning guidance:
- Improvement direction / what can be done / project explanation: prefer kb_search(project_qa)
- Price / campaign / deposit / tail payment / whether it is one-time fee / hidden charge concern:
  prefer kb_search(project_price) or pricing tools
- Cases / effect images / how many times / after-effect reference: prefer kb_search(case_studies)
- Competitor quote / same-price request / comparison: prefer kb_search(competitor_qa), optionally sales_talk_qa
- Store / address / parking / navigation / opening hours / nearest store: prefer store_lookup
- Specific store time availability: prefer available_time
- Existing appointment / change / cancel / appointment status: prefer appointment_record_query
- Create appointment only when the required facts are already clear
- Complaint / refund / real order / payment / severe abnormal after-sales / high-risk medical condition:
  prefer professional_assist
- If no external fact is needed, use no_tool

Hard tool requirements:
- no_tool is only valid for pure greeting, pure small talk, simple trust/identity reassurance, or generic acknowledgment turns.
- If the user directly asks any of these, no_tool is invalid and you must plan a fact tool:
  - price / activity / deposit / tail payment / whether it is one-time fee / hidden charge concern
  - store / address / nearest store / airport / business hours / parking / navigation
  - case / effect image / whether the case is real / how many times the case did
  - specific availability or whether a given time can be booked
- When the user asks direct factual questions such as:
  - "多少钱/价格/199/268/308/定金/尾款/是不是一次费用/会不会乱收费"
  - "店在哪里/有没有某个城市/离某地近吗/几点营业/有没有停车"
  - "这个案例是真的吗/做了几次/效果图能不能看"
  the first reply depends on real facts, so you must request the corresponding fact tool and must not leave the turn as no_tool.
- If the user asks "能不能做/适合什么方向/怎么操作/多久恢复/会不会伤皮肤" and no real fact tool is required,
  you may use no_tool, but only if the reply can directly answer the current concern without fabricating facts.
- Do not mark a turn as price_inquiry, store_inquiry, case_request, appointment_status, appointment_change,
  appointment_cancel, or competitor_compare and then leave tools as no_tool.

# Planning Rules
You must output one complete planning object.

Task-to-tool minimum mapping:
- type=price_inquiry -> tools must include kb_search(project_price) plus one pricing fact tool such as pricing_db or local_pricing
- type=store_inquiry -> tools must include store_lookup; if the user asks time availability, also include available_time
- type=case_request -> tools must include kb_search(case_studies)
- type=competitor_compare -> tools must include kb_search(competitor_qa) and may also include kb_search(project_price)
- type=appointment_status / appointment_change / appointment_cancel -> tools must include appointment_record_query
- type=appointment -> if time slot is being confirmed, tools should include available_time; create action only when necessary facts are already clear

primary_task must include:
- type
- subtype
- policy_hint
- scene
- subflow
- customer_need
- answer_goal
- priority
- known_info
- missing_info
- must_answer
- must_avoid
- should_ask
- tools

policy_hint is optional but strongly recommended. Use one of these stable IDs when the turn clearly matches:
- S1_OPENING_GENERAL
- SF3_PROJECT_NEED_DIRECTION, SF3_PROJECT_DETAIL_EXPLAIN, SF3_PROJECT_UNSUPPORTED_NEED
- SF4_IMAGE_VISIBLE_OBSERVATION
- CASE_EFFECT_REFERENCE, CASE_EFFECT_TIMES
- SF5_COMPETITOR_LOW_PRICE, SF5_COMPETITOR_HIGH_PRICE, SF5_COMPETITOR_SAME_PRICE
- SF6_STORE_NEAREST, SF6_STORE_ADDRESS_DETAIL, SF6_STORE_BUSINESS_HOURS, SF6_STORE_PARKING_NAVIGATION, SF6_STORE_LOCATION_CONFLICT
- SF7_PRICE_FIRST_ASK, SF7_PRICE_CONFIRM_199, SF7_PRICE_CONFIRM_268, SF7_PRICE_ONCE_FEE, SF7_HIDDEN_FEE_WORRY, SF7_DEPOSIT_EXPLAIN, SF7_PAYMENT_TIMING, SF7_PRICE_DIFFERENCE, SF7_LOWEST_PRICE_HANDOFF
- SF9_APPOINTMENT_TIME_CHECK, SF9_APPOINTMENT_CREATE_INFO, SF9_APPOINTMENT_STATUS, SF9_APPOINTMENT_CHANGE, SF9_APPOINTMENT_CANCEL
- SF10_TRUST_QUALIFICATION, SF10_TRUST_HIDDEN_CHARGE, SF10_TRUST_EFFECT_WORRY, SF10_TRUST_IDENTITY, SF10_TRUST_SAFETY_WORRY
- SF12_AFTER_SALES_EFFECT_FEEDBACK, SF12_AFTER_SALES_DISCOMFORT
- HUMAN_HANDOFF_PROFESSIONAL_ASSIST, HUMAN_HANDOFF_COMPLAINT_REFUND, HUMAN_HANDOFF_AFTER_SALES_RISK

secondary_tasks:
- only include an additional independent task if the user clearly expressed one in the same turn
- maximum 2
- do not fabricate multi-intent structure

reply_strategy:
- must_answer: facts or conclusions that the final reply must cover
- can_push: only one light next-step push, if appropriate
- must_avoid: content the final reply must not contain
- tone: natural, concise, like a real customer-service rep named \u5c0f\u8d1d
- max_questions: default 0 or 1

handoff:
- handoff.needed = true only for:
  - complaint / refund / rights dispute / real payment or order verification
  - serious discomfort, abnormal wound, pus, fever, infection concern
  - pregnancy, minor, severe chronic disease, report/prescription review, other high-risk medical inputs
  - very strong dissatisfaction that clearly requires professional follow-up
- otherwise keep handoff false and let the system continue handling the turn

# Output Contract
Return valid JSON only.

{
  "primary_task": {
    "type": "",
    "subtype": "",
    "policy_hint": "",
    "scene": "",
    "subflow": "",
    "customer_need": "",
    "answer_goal": "",
    "priority": 1,
    "known_info": [],
    "missing_info": [],
    "must_answer": [],
    "must_avoid": [],
    "should_ask": false,
    "tools": []
  },
  "secondary_tasks": [],
  "required_tools": [],
  "reply_strategy": {
    "tone": "",
    "must_answer": [],
    "can_push": "",
    "must_avoid": [],
    "max_questions": 1
  },
  "handoff": {
    "needed": false,
    "reason": ""
  },
  "memory_update_hint": {
    "summary": "",
    "needs": [],
    "concerns": [],
    "store_preference": "",
    "appointment_signals": []
  }
}

# Enum Limits
- tool.name must be one of: kb_search, pricing_db, local_pricing, store_lookup, available_time, appointment_record_query, appointment_create, professional_assist, no_tool
- kb_name must be one of: project_qa, project_price, sales_talk_qa, case_studies, competitor_qa, after_sales_qa
""".strip()


PLANNER_RISK_PATCH_PROMPT = """
# Risk Boundary Patch
Apply these overrides before finalizing the plan:

- If the user mentions pregnancy, breastfeeding, minor age, severe chronic disease, diabetes, hypertension,
  prescription medicine, medical report, prescription, or severe allergy history, do not treat the turn as a
  normal project consultation. Prefer professional_assist and set handoff.needed=true.
- If the user clearly complains, asks for refund, mentions rights protection, exposure, police, platform complaint,
  payment discrepancy, order discrepancy, severe waiting problem in-store, or real charge mismatch, do not continue
  ordinary sales handling. Prefer professional_assist and set handoff.needed=true.
- If the user only has ordinary trust concern, price concern, hidden-charge concern, or asks whether the rep is real,
  do not escalate to professional assist. Keep the task in-system and answer the concern.
- If the user asks rule-based price questions such as deposit, tail payment, whether full payment is after the visit,
  whether the quoted price is one-time, or campaign price confirmation, prefer price facts instead of chat/general reply.
- If the user references another institution, another quote, same-price request, or competitor promise, prefer a competitor task.
""".strip()


PLANNER_REPAIR_PROMPT = """
# Planner Repair
The previous planning object failed structural validation. Rewrite the full planning object using the same schema.

Rules:
- Do not generate customer-facing copy.
- Do not invent concrete prices, store addresses, appointment status, case results, qualification claims, or order/refund facts in the plan.
- If the current task needs real facts, add the explicit tools needed to fetch them.
- If the task type was wrong, correct the task type instead of forcing tools onto the wrong task.
- no_tool is only valid when no external fact is needed.
- Keep the answer goal focused on the current user turn.
- Return valid JSON only.
""".strip()


def planner_v2_model_tier(state: AgentState) -> str:
    content = str(state.get("normalized_content") or "").strip()
    has_image = bool((state.get("image_info") or {}).get("has_image"))
    short_greeting_tokens = {
        "你好",
        "您好",
        "在吗",
        "有人吗",
        "哈喽",
        "嗨",
        "喂",
        "好的",
        "可以",
    }
    if not has_image and content in short_greeting_tokens:
        return "fast"
    return "balanced"


def planner_v2_messages_for_model(state: AgentState) -> list[dict[str, Any]]:
    payload = {
        "current_message": state.get("normalized_content") or "",
        "message_type": _message_type(state),
        "conversation_history": (state.get("conversation_history") or [])[-10:],
        "image_info": state.get("image_info") or {},
        "category_id": str(((state.get("request_context") or {}).get("category_id") or "")).strip(),
        "customer_profile": state.get("customer_profile") or {},
        "customer_basic_info": state.get("customer_basic_info") or {},
        "history_events": (state.get("history_events") or [])[-8:],
        "appointment_cache": state.get("appointment_cache") or {},
        "customer_context": _compact_customer_context(state.get("customer_context") or {}),
    }
    return [
        {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
        {"role": "system", "content": PLANNER_RISK_PATCH_PROMPT},
        {"role": "system", "content": BUSINESS_STRATEGY_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
    ]


def planner_v2_repair_messages_for_model(
    state: AgentState,
    *,
    original_plan: dict[str, Any],
    violations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    payload = {
        "current_message": state.get("normalized_content") or "",
        "message_type": _message_type(state),
        "conversation_history": (state.get("conversation_history") or [])[-10:],
        "image_info": state.get("image_info") or {},
        "category_id": str(((state.get("request_context") or {}).get("category_id") or "")).strip(),
        "customer_profile": state.get("customer_profile") or {},
        "customer_basic_info": state.get("customer_basic_info") or {},
        "history_events": (state.get("history_events") or [])[-8:],
        "appointment_cache": state.get("appointment_cache") or {},
        "customer_context": _compact_customer_context(state.get("customer_context") or {}),
        "original_plan": original_plan,
        "tool_policy_violations": violations,
    }
    return [
        {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
        {"role": "system", "content": PLANNER_RISK_PATCH_PROMPT},
        {"role": "system", "content": BUSINESS_STRATEGY_PROMPT},
        {"role": "system", "content": PLANNER_REPAIR_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
    ]


async def run_planner_brain_v2(
    state: AgentState,
    model_client: ModelClient,
) -> tuple[dict[str, Any], dict[str, Any]]:
    tier = planner_v2_model_tier(state)
    payload = await model_client.chat_json(planner_v2_messages_for_model(state), tier=tier, temperature=0.1)
    plan = build_planner_plan_v2(state, payload)
    initial_usage = model_usage_snapshot(model_client)
    nested_calls: list[dict[str, Any]] = []
    violations = plan.get("tool_policy_violations", [])
    if violations:
        repair_call: dict[str, Any] = {
            "name": "planner_brain_repair",
            "input": {"tier": tier, "violations": violations},
        }
        try:
            repaired_payload = await model_client.chat_json(
                planner_v2_repair_messages_for_model(
                    state,
                    original_plan=plan,
                    violations=violations,
                ),
                tier=tier,
                temperature=0.0,
            )
            repaired_plan = build_planner_plan_v2(state, repaired_payload)
            plan = repaired_plan
            repair_call["output"] = {
                "primary_task": plan.get("primary_task", {}).get("type", ""),
                "required_tools": len(plan.get("required_tools", [])),
                "tool_policy_violations": len(plan.get("tool_policy_violations", [])),
            }
            repair_call["usage"] = model_usage_snapshot(model_client)
        except Exception as exc:
            repair_call["error"] = f"{type(exc).__name__}: {exc}"
            repair_call["usage"] = model_usage_snapshot(model_client)
        nested_calls.append(repair_call)
    model_call = {
        "name": "planner_brain_v2",
        "input": {"tier": tier},
        "output": {
            "primary_task": plan.get("primary_task", {}).get("type", ""),
            "secondary_tasks": len(plan.get("secondary_tasks", [])),
            "required_tools": len(plan.get("required_tools", [])),
            "tool_policy_violations": len(plan.get("tool_policy_violations", [])),
        },
        "usage": initial_usage,
    }
    if nested_calls:
        model_call["nested_calls"] = nested_calls
    return plan, model_call


def build_planner_plan_v2(state: AgentState, model_payload: dict[str, Any]) -> dict[str, Any]:
    primary_raw = model_payload.get("primary_task") if isinstance(model_payload, dict) else {}
    secondary_raw = model_payload.get("secondary_tasks") if isinstance(model_payload, dict) else []
    reply_strategy_raw = model_payload.get("reply_strategy") if isinstance(model_payload, dict) else {}
    handoff_raw = model_payload.get("handoff") if isinstance(model_payload, dict) else {}
    memory_update_raw = model_payload.get("memory_update_hint") if isinstance(model_payload, dict) else {}

    primary_task = _normalize_task(primary_raw, default_priority=1)
    secondary_tasks = [
        _normalize_task(item, default_priority=index + 2)
        for index, item in enumerate(secondary_raw if isinstance(secondary_raw, list) else [])
        if isinstance(item, dict)
    ]
    secondary_tasks = [item for item in secondary_tasks if item][:2]

    if not primary_task:
        raise ValueError("Planner Brain missing valid primary_task")

    all_tasks = [primary_task, *secondary_tasks]
    reply_strategy = _normalize_reply_strategy(reply_strategy_raw, all_tasks)
    handoff = _normalize_handoff(handoff_raw, primary_task, secondary_tasks)
    required_tools = _dedupe_tools([tool for task in all_tasks for tool in task.get("tools", [])])
    required_tools = required_tools or [{"name": "no_tool", "purpose": "Planner did not request external tools"}]
    tool_policy_violations = _tool_policy_violations(all_tasks, required_tools)
    memory_update_hint = _normalize_memory_hint(memory_update_raw)

    return {
        "primary_task": primary_task,
        "secondary_tasks": secondary_tasks,
        "required_tools": required_tools,
        "tool_policy_violations": tool_policy_violations,
        "reply_strategy": reply_strategy,
        "handoff": handoff,
        "memory_update_hint": memory_update_hint,
    }


def safety_fallback_plan(state: AgentState) -> dict[str, Any]:
    content = str(state.get("normalized_content") or "").strip()
    handoff_needed = _has_hard_handoff_signal(content)
    primary_task = {
        "type": "human_request" if handoff_needed else "general_consult",
        "subtype": "risk_or_dispute" if handoff_needed else "open_consult",
        "policy_hint": "HUMAN_HANDOFF_PROFESSIONAL_ASSIST" if handoff_needed else "S1_OPENING_GENERAL",
        "scene": "S7_dealed_active" if handoff_needed else "S3_deep_consult",
        "subflow": "HUMAN_HANDOFF" if handoff_needed else "DIRECT_REPLY",
        "customer_need": "Needs a professional colleague to verify and continue handling" if handoff_needed else "Needs a natural first-turn response",
        "answer_goal": "Acknowledge the risk/dispute and arrange professional assistance" if handoff_needed else "Acknowledge the customer's current question and let the final reply model answer it naturally",
        "priority": 1,
        "known_info": [],
        "missing_info": [],
        "must_answer": ["Current user question"],
        "must_avoid": ["Made-up facts", "Guaranteed results"],
        "should_ask": False,
        "tools": (
            [{"name": "professional_assist", "purpose": "Risk or dispute requires professional follow-up"}]
            if handoff_needed
            else [{"name": "no_tool", "purpose": "This turn can be answered without external facts"}]
        ),
    }
    return build_planner_plan_v2(
        state,
        {
            "primary_task": primary_task,
            "secondary_tasks": [],
            "reply_strategy": {
                "tone": "Natural, concise, like a real customer-service rep named \u5c0f\u8d1d",
                "must_answer": ["Current user question"],
                "can_push": "" if handoff_needed else "Use a light next-step push only if it helps the conversation",
                "must_avoid": ["Made-up facts", "Guaranteed results", "Internal process exposure"],
                "max_questions": 0,
            },
            "handoff": {"needed": handoff_needed, "reason": "Hit complaint, refund, severe risk, or real-order verification boundary" if handoff_needed else ""},
            "memory_update_hint": {},
        },
    )


def _normalize_task(raw: Any, *, default_priority: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    try:
        priority = int(raw.get("priority", default_priority))
    except (TypeError, ValueError):
        priority = default_priority
    tools = _dedupe_tools(_normalize_tools(raw.get("tools") or []))
    return {
        "type": str(raw.get("type") or "").strip(),
        "subtype": str(raw.get("subtype") or "").strip(),
        "policy_hint": str(raw.get("policy_hint") or "").strip(),
        "scene": str(raw.get("scene") or "").strip(),
        "subflow": str(raw.get("subflow") or "").strip(),
        "customer_need": str(raw.get("customer_need") or "").strip(),
        "answer_goal": str(raw.get("answer_goal") or "").strip(),
        "priority": priority,
        "known_info": _clean_str_list(raw.get("known_info") or []),
        "missing_info": _clean_str_list(raw.get("missing_info") or []),
        "must_answer": _clean_str_list(raw.get("must_answer") or []),
        "must_avoid": _clean_str_list(raw.get("must_avoid") or []),
        "should_ask": bool(raw.get("should_ask")),
        "tools": tools or [{"name": "no_tool", "purpose": "This turn can be acknowledged directly"}],
    }


def _normalize_tools(raw_tools: Any) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    if not isinstance(raw_tools, list):
        return tools
    for item in raw_tools:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name not in ALLOWED_TOOLS:
            continue
        tool = {"name": name, "purpose": str(item.get("purpose") or "").strip()}
        kb_name = str(item.get("kb_name") or "").strip()
        if kb_name:
            if name != "kb_search" or kb_name not in ALLOWED_KBS:
                continue
            tool["kb_name"] = kb_name
        query = str(item.get("query") or "").strip()
        if query:
            tool["query"] = query
        tools.append(tool)
    return tools


def _tool_policy_violations(tasks: list[dict[str, Any]], required_tools: list[dict[str, Any]]) -> list[dict[str, str]]:
    concrete_tools = [tool for tool in required_tools if str(tool.get("name") or "").strip() != "no_tool"]
    violations: list[dict[str, str]] = []

    def has_tool(name: str, *, kb_name: str = "") -> bool:
        for tool in concrete_tools:
            if str(tool.get("name") or "").strip() != name:
                continue
            if kb_name and str(tool.get("kb_name") or "").strip() != kb_name:
                continue
            return True
        return False

    for task in tasks:
        task_type = str(task.get("type") or "").strip()
        missing: list[str] = []
        if task_type == "price_inquiry":
            if not has_tool("kb_search", kb_name="project_price"):
                missing.append("kb_search(project_price)")
            if not (has_tool("pricing_db") or has_tool("local_pricing")):
                missing.append("pricing_db_or_local_pricing")
        elif task_type == "store_inquiry":
            if not has_tool("store_lookup"):
                missing.append("store_lookup")
        elif task_type == "case_request":
            if not has_tool("kb_search", kb_name="case_studies"):
                missing.append("kb_search(case_studies)")
        elif task_type == "competitor_compare":
            if not has_tool("kb_search", kb_name="competitor_qa"):
                missing.append("kb_search(competitor_qa)")
        elif task_type in {"appointment_status", "appointment_change", "appointment_cancel"}:
            if not has_tool("appointment_record_query"):
                missing.append("appointment_record_query")
        elif task_type == "appointment":
            if not (has_tool("available_time") or has_tool("appointment_create") or has_tool("appointment_record_query")):
                missing.append("appointment_fact_tool")

        if missing:
            violations.append(
                {
                    "task_type": task_type,
                    "subtype": str(task.get("subtype") or "").strip(),
                    "missing": ", ".join(missing),
                    "note": "Planner did not request the fact tools required by its own task type; code did not auto-add them.",
                }
            )
    return violations


def _normalize_reply_strategy(raw: Any, tasks: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    primary = tasks[0] if tasks else {}
    tone = str(raw.get("tone") or "").strip() or "Natural, concise, like a real customer-service rep named \u5c0f\u8d1d"
    must_answer = _clean_str_list(raw.get("must_answer") or []) or list(primary.get("must_answer") or [])
    can_push = str(raw.get("can_push") or "").strip()
    must_avoid = _clean_str_list(raw.get("must_avoid") or []) or list(primary.get("must_avoid") or [])
    try:
        max_questions = int(raw.get("max_questions", 1))
    except (TypeError, ValueError):
        max_questions = 1
    return {
        "tone": tone[:120],
        "must_answer": must_answer[:8],
        "can_push": can_push[:180],
        "must_avoid": must_avoid[:8],
        "max_questions": 0 if max_questions < 0 else min(max_questions, 2),
    }


def _normalize_handoff(raw: Any, primary_task: dict[str, Any], secondary_tasks: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    needed = bool(raw.get("needed"))
    if str(primary_task.get("type") or "").strip() in {"human_request", "complaint_refund"}:
        needed = True
    if any(str(item.get("type") or "").strip() in {"human_request", "complaint_refund"} for item in secondary_tasks):
        needed = True
    return {
        "needed": needed,
        "reason": str(raw.get("reason") or "").strip()[:180],
    }


def _normalize_memory_hint(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    return {
        "summary": str(raw.get("summary") or "").strip()[:180],
        "needs": _clean_str_list(raw.get("needs") or [])[:6],
        "concerns": _clean_str_list(raw.get("concerns") or [])[:6],
        "store_preference": str(raw.get("store_preference") or "").strip()[:80],
        "appointment_signals": _clean_str_list(raw.get("appointment_signals") or [])[:6],
    }


def _dedupe_tools(raw_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in raw_tools:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        kb_name = str(item.get("kb_name") or "").strip()
        query = str(item.get("query") or "").strip()
        key = (name, kb_name, query)
        if name not in ALLOWED_TOOLS or key in seen:
            continue
        seen.add(key)
        normalized = {"name": name, "purpose": str(item.get("purpose") or "").strip()}
        if kb_name:
            normalized["kb_name"] = kb_name
        if query:
            normalized["query"] = query
        unique.append(normalized)
    return unique


def _clean_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    output: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            output.append(text[:180])
    return output


def _has_hard_handoff_signal(content: str) -> bool:
    text = content.lower()
    hard_terms = (
        "\u6295\u8bc9",
        "\u9000\u6b3e",
        "\u7ef4\u6743",
        "\u62a5\u8b66",
        "\u8d77\u8bc9",
        "\u66dd\u5149",
        "\u6d41\u8113",
        "\u611f\u67d3",
        "\u9ad8\u70e7",
        "\u81ea\u6740",
        "\u81ea\u6b8b",
        "\u5b55\u5987",
        "\u6000\u5b55",
        "\u54fa\u4e73\u671f",
        "\u672a\u6210\u5e74",
        "\u7cd6\u5c3f\u75c5",
        "\u9ad8\u8840\u538b",
        "\u5904\u65b9",
        "\u75c5\u5386",
        "\u62a5\u544a",
    )
    return any(term in text for term in hard_terms)

def _message_type(state: AgentState) -> str:
    image_info = state.get("image_info") or {}
    if image_info.get("has_image"):
        return str(image_info.get("image_type") or "image")
    return "text"


def _compact_customer_context(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    keys = (
        "city",
        "confirmed_store_id",
        "confirmed_store_name",
        "detected_city",
        "appointment_info",
        "has_upcoming_appointment",
        "latest_store_candidates",
    )
    return {key: raw.get(key) for key in keys if raw.get(key) not in (None, "", [], {})}
