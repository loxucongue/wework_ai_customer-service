from __future__ import annotations

import re
from typing import Any

from app.graph.nodes.memory_usage_policy import order_session_state
from app.graph.nodes.store_context import (
    current_real_store_from_state,
    known_city_from_state,
    known_store_area_from_history,
    should_use_known_store_context,
    should_use_recent_store_fact_context,
    store_query_from_state,
)
from app.graph.planner.planner_contract import ALLOWED_KBS, ALLOWED_TOOLS
from app.graph.signals.project import has_case_request
from app.graph.state import AgentState
from app.policies.constants import APPOINTMENT_KEYWORDS, CITY_NAMES, STORE_AREA_TERMS, STORE_KEYWORDS
from app.policies.sop_rules import normalize_sop_stage
from app.services import store_text


STORE_FACT_TERMS = tuple(
    dict.fromkeys(
        STORE_KEYWORDS
        + (
            "哪家近",
            "离我近",
            "最近门店",
            "附近门店",
            "发地址",
            "详细地址",
            "门店位置",
            "定位发我",
            "导航链接",
            "地铁怎么去",
            "机场附近",
            "高铁附近",
            "停车场",
        )
    )
)

STORE_LOCATION_HINT_TERMS = tuple(
    dict.fromkeys(
        CITY_NAMES
        + STORE_AREA_TERMS
        + (
            "我在",
            "我住",
            "我到",
            "附近",
            "机场",
            "地铁",
            "高铁",
            "火车站",
            "商圈",
            "科技园",
            "哪家",
            "最近",
            "离我近",
        )
    )
)

APPOINTMENT_TIME_TERMS = tuple(
    dict.fromkeys(
        APPOINTMENT_KEYWORDS
        + (
            "能约",
            "可以约",
            "能不能约",
            "今天",
            "明天",
            "后天",
            "上午",
            "中午",
            "下午",
            "晚上",
            "周一",
            "周二",
            "周三",
            "周四",
            "周五",
            "周六",
            "周日",
            "星期一",
            "星期二",
            "星期三",
            "星期四",
            "星期五",
            "星期六",
            "星期天",
            "几点",
            "现在过去",
            "现在过来",
            "过去",
            "过来",
            "到店",
            "见",
        )
    )
)

_APPOINTMENT_EXPLICIT_TIME_TERMS = (
    "今天",
    "明天",
    "后天",
    "上午",
    "中午",
    "下午",
    "晚上",
    "周一",
    "周二",
    "周三",
    "周四",
    "周五",
    "周六",
    "周日",
    "星期一",
    "星期二",
    "星期三",
    "星期四",
    "星期五",
    "星期六",
    "星期天",
    "星期日",
    "几点",
    "现在过去",
    "现在过来",
)

_APPOINTMENT_BOOKING_INTENT_TERMS = (
    "预约",
    "约一下",
    "先约",
    "安排一下",
    "帮我安排",
    "登记一下",
    "帮我登记",
    "报个名",
    "报名",
    "留个名额",
    "锁个名额",
    "锁一下",
    "交10",
    "交十",
    "先交10",
    "先付10",
    "付10",
    "预约金",
    "定金",
    "订金",
)

_STORE_FACT_TERMS_UTF8 = (
    "\u95e8\u5e97",
    "\u54ea\u5bb6",
    "\u54ea\u4e2a\u5e97",
    "\u5730\u5740",
    "\u4f4d\u7f6e",
    "\u5bfc\u822a",
    "\u8def\u7ebf",
    "\u9644\u8fd1",
    "\u6700\u8fd1",
    "\u79bb\u6211\u8fd1",
    "\u8425\u4e1a\u65f6\u95f4",
    "\u505c\u8f66",
    "\u673a\u573a",
    "\u9ad8\u94c1",
    "\u5730\u94c1",
    "\u706b\u8f66\u7ad9",
    "\u5546\u5708",
    "\u79d1\u6280\u56ed",
)

_LOCATION_HINT_TERMS_UTF8 = (
    "\u6211\u5728",
    "\u6211\u4f4f",
    "\u6211\u5230",
    "\u9644\u8fd1",
    "\u5468\u8fb9",
    "\u673a\u573a",
    "\u9ad8\u94c1",
    "\u5730\u94c1",
    "\u706b\u8f66\u7ad9",
    "\u5546\u5708",
    "\u79d1\u6280\u56ed",
    "\u53bf\u57ce",
    "\u5f00\u53d1\u533a",
    "\u7ecf\u5f00\u533a",
    "\u65b0\u533a",
    "\u9ad8\u65b0\u533a",
)

_APPOINTMENT_TIME_TERMS_UTF8 = (
    "\u4eca\u5929",
    "\u660e\u5929",
    "\u540e\u5929",
    "\u4e0a\u5348",
    "\u4e2d\u5348",
    "\u4e0b\u5348",
    "\u665a\u4e0a",
    "\u5468\u4e00",
    "\u5468\u4e8c",
    "\u5468\u4e09",
    "\u5468\u56db",
    "\u5468\u4e94",
    "\u5468\u516d",
    "\u5468\u65e5",
    "\u5468\u5929",
    "\u5468\u672b",
    "\u661f\u671f\u4e00",
    "\u661f\u671f\u4e8c",
    "\u661f\u671f\u4e09",
    "\u661f\u671f\u56db",
    "\u661f\u671f\u4e94",
    "\u661f\u671f\u516d",
    "\u661f\u671f\u65e5",
    "\u51e0\u70b9",
    "\u8fc7\u6765",
    "\u8fc7\u53bb",
    "\u5230\u5e97",
    "\u6765\u5e97",
)

_APPOINTMENT_BOOKING_TERMS_UTF8 = (
    "\u9884\u7ea6",
    "\u767b\u8bb0",
    "\u62a5\u540d",
    "\u5e2e\u6211\u767b\u8bb0",
    "\u5e2e\u6211\u5b89\u6392",
    "\u5b89\u6392",
    "\u7559\u4e2a\u540d\u989d",
    "\u7ea6\u4e00\u4e2a",
    "\u4ea410",
    "\u4ed810",
    "\u5148\u4ea410",
    "\u5148\u4ed810",
    "\u9884\u7ea6\u91d1",
    "\u5b9a\u91d1",
    "\u8ba2\u91d1",
)

_CONTACT_TERMS_UTF8 = (
    "\u6211\u53eb",
    "\u59d3\u540d",
    "\u540d\u5b57",
    "\u7535\u8bdd",
    "\u624b\u673a",
    "\u53f7\u7801",
)


def normalize_tools(raw_tools: Any) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    if not isinstance(raw_tools, list):
        return tools
    for item in raw_tools:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name not in ALLOWED_TOOLS:
            continue
        kb_name = str(item.get("kb_name") or "").strip()
        query = str(item.get("query") or "").strip()
        if name == "kb_search" and (kb_name not in ALLOWED_KBS or not query):
            continue
        tool = {"name": name, "purpose": str(item.get("purpose") or "").strip()}
        if kb_name:
            tool["kb_name"] = kb_name
        if query:
            tool["query"] = query
        distance_origin = str(item.get("distance_origin") or "").strip()
        if name == "store_lookup" and distance_origin:
            tool["distance_origin"] = distance_origin
        tools.append(tool)
    return tools


def _case_studies_query_from_state(state: AgentState, query: str) -> str:
    """Enrich only case material retrieval; keep sales_talk_qa on the original wording."""
    base = str(query or "").strip()
    context_parts = [base]
    for item in (state.get("conversation_history") or [])[-8:]:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        text = str(content.get("text") if isinstance(content, dict) else content or "").strip()
        if text:
            context_parts.append(text)
    context = " ".join(context_parts)

    hints: list[str] = []
    concern_terms = (
        "黑色素",
        "斑点",
        "淡斑",
        "色沉",
        "晒斑",
        "老年斑",
        "雀斑",
        "黄褐斑",
        "肤色不均",
    )
    for term in concern_terms:
        if term in context:
            hints.append(term)

    if any(term in context for term in ("斑", "黑色素", "色沉")) and "淡斑" not in hints:
        hints.append("淡斑")
    if any(term in base for term in ("效果", "案例", "做完", "真实", "参考", "对比", "明显", "能看到")):
        hints.extend(["客户做完", "效果对比", "真实案例"])
    if not hints:
        hints.extend(["淡斑", "斑点", "效果对比"])

    values = list(dict.fromkeys([base] + hints))
    return " ".join(part for part in values if part).strip()[:160]


def enforce_required_tools(
    state: AgentState,
    tasks: list[dict[str, Any]],
    required_tools: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    tools = [tool for tool in required_tools if str(tool.get("name") or "").strip() != "no_tool"]
    original_user_query = str(state.get("normalized_content") or "").strip()[:160]
    fallback_query = _policy_tool_query(tasks) or original_user_query
    primary_task_type = str((tasks[0] if tasks else {}).get("type") or "").strip()
    sop_stage = normalize_sop_stage(
        state.get("sop_stage") or (tasks[0].get("sop_stage") if tasks else ""),
        task_type=primary_task_type,
    )
    state_markers = _state_policy_markers(state, sop_stage)

    def add_tool(tool: dict[str, Any]) -> None:
        name = str(tool.get("name") or "").strip()
        kb_name = str(tool.get("kb_name") or "").strip()
        query = str(tool.get("query") or "").strip()
        distance_origin = str(tool.get("distance_origin") or "").strip()
        if name not in ALLOWED_TOOLS:
            return
        if name == "kb_search" and (kb_name not in ALLOWED_KBS or not query):
            return
        for existing in tools:
            if str(existing.get("name") or "").strip() != name:
                continue
            if kb_name and str(existing.get("kb_name") or "").strip() != kb_name:
                continue
            purpose = str(tool.get("purpose") or "").strip()
            if query and (
                name == "store_lookup"
                or (name == "kb_search" and kb_name == "case_studies")
                or not str(existing.get("query") or "").strip()
            ):
                existing["query"] = query
            if name == "store_lookup" and distance_origin:
                existing["distance_origin"] = distance_origin
            if purpose and not str(existing.get("purpose") or "").strip():
                existing["purpose"] = purpose
            return
        normalized = {"name": name, "purpose": str(tool.get("purpose") or "").strip()}
        if kb_name:
            normalized["kb_name"] = kb_name
        if query:
            normalized["query"] = query
        if name == "store_lookup" and distance_origin:
            normalized["distance_origin"] = distance_origin
        tools.append(normalized)

    def ensure_sales_talk_reference(purpose: str) -> None:
        if original_user_query:
            add_tool(
                {
                    "name": "kb_search",
                    "kb_name": "sales_talk_qa",
                    "query": original_user_query,
                    "purpose": purpose,
                }
            )

    def ensure_store_lookup(purpose: str) -> None:
        query = store_query_from_state(original_user_query or fallback_query, state)
        add_tool(
            {
                "name": "store_lookup",
                "query": query or original_user_query or fallback_query,
                "distance_origin": _distance_origin_from_state_or_text(state, original_user_query),
                "purpose": purpose,
            }
        )

    def ensure_case_studies() -> None:
        query = _case_studies_query_from_state(state, original_user_query or fallback_query)
        add_tool(
            {
                "name": "kb_search",
                "kb_name": "case_studies",
                "query": query,
                "purpose": "Need real case materials before answering effect or comparison requests",
            }
        )

    def ensure_available_time() -> None:
        ensure_store_lookup("Need real store facts before checking appointment availability")
        add_tool(
            {
                "name": "available_time",
                "purpose": "Need real appointment availability before answering time or visit intent",
            }
        )

    def ensure_appointment_create() -> None:
        ensure_store_lookup("Need real store facts before creating appointment deposit order")
        ensure_available_time()
        add_tool(
            {
                "name": "appointment_create",
                "purpose": "Customer shows explicit booking or deposit intent and a real appointment order may be needed",
            }
        )

    # Text signals are mandatory facts, not optional model preferences.
    if needs_store_lookup_request(state, original_user_query):
        ensure_store_lookup("Customer mentions city, area, address, route, nearby, hours, or parking")
    if needs_appointment_time_request(original_user_query):
        ensure_available_time()
    if needs_appointment_create_request(state, original_user_query):
        ensure_appointment_create()
    if has_case_request(original_user_query):
        ensure_case_studies()

    # Policy/SOP markers are a second safety net when the planner type drifts.
    if _is_store_marker(state_markers):
        ensure_store_lookup("Policy/SOP indicates store, address, route, or nearby-store facts")
    if _is_appointment_marker(state_markers):
        ensure_available_time()
    if _is_case_marker(state_markers):
        ensure_case_studies()

    for task in tasks:
        task_type = str(task.get("type") or "").strip()
        subtype = str(task.get("subtype") or "").strip()
        policy_hint = str(task.get("policy_hint") or "").strip().upper()
        subflow = str(task.get("subflow") or "").strip().upper()
        task_stage = normalize_sop_stage(task.get("sop_stage") or sop_stage, task_type=task_type)
        markers = " ".join([task_type, subtype, policy_hint, subflow, task_stage]).upper()

        if task_stage in {"S1_GREETING_INTRO", "S2_STORE_ADDRESS", "S3_PRICE_CLOSE", "S4_FOLLOWUP_REACTIVATE"}:
            ensure_sales_talk_reference("Need sales talk wording for the current SOP stage")
        if task_type in {
            "project_consult",
            "image_consult",
            "price_inquiry",
            "competitor_compare",
            "trust_issue",
            "after_sales",
        }:
            ensure_sales_talk_reference("Need sales champion wording and business-answer logic using the original wording")
        if task_stage == "S2_STORE_ADDRESS" or task_type == "store_inquiry" or _is_store_marker(markers):
            ensure_store_lookup("Need real store facts before answering store, address, route, hours, or parking")
        if task_type == "appointment" or _is_appointment_marker(markers):
            ensure_available_time()
        if (
            task_type
            in {
                "appointment_create",
                "signup_close",
                "create_deposit_order",
                "deposit_order",
                "execute_deposit",
                "payment_link_request",
            }
            or subtype
            in {
                "direct_payment_link",
                "appointment_payment",
                "deposit_payment",
                "payment_link_request",
                "reservation_deposit_payment",
                "reservation_deposit",
                "deposit_link",
                "payment_entry",
            }
            or (
                task_type == "price_close"
                and any(token in subtype.lower() for token in ("deposit", "payment", "link", "order", "reservation"))
            )
            or any(
                token in markers
                for token in (
                    "BOOK_ORDER",
                    "DIRECT_PAYMENT_LINK",
                    "DEPOSIT_ORDER",
                    "PAYMENT_LINK_REQUEST",
                    "RESERVATION_DEPOSIT",
                    "PAYMENT_ENTRY",
                    "预约金",
                    "付款",
                    "支付",
                    "收单",
                )
            )
        ):
            ensure_appointment_create()
        if task_type in {"appointment_status", "appointment_change", "appointment_cancel"} or any(
            token in markers for token in ("APPOINTMENT_STATUS", "APPOINTMENT_CHANGE", "APPOINTMENT_CANCEL")
        ):
            add_tool(
                {
                    "name": "appointment_record_query",
                    "purpose": "Need real appointment record before status, change, or cancel handling",
                }
            )
        if task_type == "case_request" or _is_case_marker(markers):
            ensure_case_studies()
        if task_type in {"human_request", "complaint_refund"} or "HUMAN_HANDOFF" in markers:
            add_tool(
                {
                    "name": "professional_assist",
                    "purpose": "Need professional colleague for complaint, refund, order/payment, or high-risk handling",
                }
            )

    tools = dedupe_tools(tools)
    if _should_skip_store_lookup_for_confirmed_appointment(state, original_user_query, tools):
        tools = [tool for tool in tools if str(tool.get("name") or "").strip() != "store_lookup"]
    return tools


def tool_policy_violations(tasks: list[dict[str, Any]], required_tools: list[dict[str, Any]]) -> list[dict[str, str]]:
    concrete_tools = [tool for tool in required_tools if str(tool.get("name") or "").strip() != "no_tool"]
    violations: list[dict[str, str]] = []

    for tool in concrete_tools:
        name = str(tool.get("name") or "").strip()
        if name != "kb_search":
            continue
        kb_name = str(tool.get("kb_name") or "").strip()
        query = str(tool.get("query") or "").strip()
        missing_args: list[str] = []
        if not kb_name:
            missing_args.append("kb_name")
        if not query:
            missing_args.append("query")
        if missing_args:
            violations.append(
                {
                    "task_type": "tool_argument",
                    "subtype": "kb_search",
                    "missing": "kb_search_missing_query" if "query" in missing_args else "kb_search_missing_kb_name",
                    "note": "Every kb_search must include both kb_name and a concrete query.",
                }
            )

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
        markers = " ".join(
            str(task.get(key) or "")
            for key in ("type", "subtype", "policy_hint", "subflow", "sop_stage")
        ).upper()
        missing: list[str] = []
        if (task_type == "store_inquiry" or _is_store_marker(markers)) and not has_tool("store_lookup"):
            missing.append("store_lookup")
        elif (task_type == "case_request" or _is_case_marker(markers)) and not has_tool(
            "kb_search", kb_name="case_studies"
        ):
            missing.append("kb_search(case_studies)")
        elif task_type == "competitor_compare" and not has_tool("kb_search", kb_name="sales_talk_qa"):
            missing.append("kb_search(sales_talk_qa)")
        elif task_type in {"appointment_status", "appointment_change", "appointment_cancel"} and not has_tool(
            "appointment_record_query"
        ):
            missing.append("appointment_record_query")
        elif (task_type == "appointment" or _is_appointment_marker(markers)) and not (
            has_tool("available_time") or has_tool("appointment_create") or has_tool("appointment_record_query")
        ):
            missing.append("appointment_fact_tool")
        if missing:
            violations.append(
                {
                    "task_type": task_type,
                    "subtype": str(task.get("subtype") or "").strip(),
                    "missing": ", ".join(missing),
                    "note": "Planner did not request the fact tools required by its own task or policy marker.",
                }
            )
    return violations


def dedupe_tools(raw_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in raw_tools:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        kb_name = str(item.get("kb_name") or "").strip()
        query = str(item.get("query") or "").strip()
        if name not in ALLOWED_TOOLS:
            continue
        if name == "kb_search" and (kb_name not in ALLOWED_KBS or not query):
            continue
        key = (name, kb_name, query)
        if key in seen:
            continue
        seen.add(key)
        normalized = {"name": name, "purpose": str(item.get("purpose") or "").strip()}
        if kb_name:
            normalized["kb_name"] = kb_name
        if query:
            normalized["query"] = query
        distance_origin = str(item.get("distance_origin") or "").strip()
        if name == "store_lookup" and distance_origin:
            normalized["distance_origin"] = distance_origin
        unique.append(normalized)
    return unique


def needs_store_lookup_request(state: AgentState, content: str) -> bool:
    if not content:
        return False
    if _looks_like_fee_or_price_only_turn(content) and not _has_specific_store_or_location_signal(content):
        return False
    asks_store_fact = any(term in content for term in STORE_FACT_TERMS) or any(
        term in content for term in _STORE_FACT_TERMS_UTF8
    )
    has_location_hint = any(term in content for term in STORE_LOCATION_HINT_TERMS) or any(
        term in content for term in _LOCATION_HINT_TERMS_UTF8
    )
    has_district_or_county_store_question = bool(
        re.search(r"[\u4e00-\u9fa5A-Za-z0-9]{2,20}(区|县城|县级市|开发区|经开区|新区|高新区|县)", content)
        and any(term in content for term in ("店", "门店", "地址", "位置", "附近", "最近", "哪家"))
    )
    if asks_store_fact or has_location_hint or has_district_or_county_store_question:
        return True
    return should_use_known_store_context(content) or should_use_recent_store_fact_context(content, state)


def needs_appointment_time_request(content: str) -> bool:
    if not content:
        return False
    if any(term in content for term in _APPOINTMENT_TIME_TERMS_UTF8):
        return True
    if any(term in content for term in _APPOINTMENT_EXPLICIT_TIME_TERMS):
        return True
    if any(term in content for term in ("什么时候去", "什么时候过来", "几点去", "几点过来", "哪天去")):
        return True
    if "点" in content and any(term in content for term in ("去", "过来", "过去", "到店", "来店", "见")):
        return True
    if any(term in content for term in ("现在过来", "现在过去", "现在到店", "现在来店")):
        return True
    return False


def needs_appointment_create_request(state: AgentState, content: str) -> bool:
    if not content:
        return False
    has_booking_intent = any(term in content for term in _APPOINTMENT_BOOKING_INTENT_TERMS) or any(
        term in content for term in _APPOINTMENT_BOOKING_TERMS_UTF8
    )
    has_customer_details_after_booking = _has_contact_detail(content) and _recent_booking_intent(state)
    if not has_booking_intent and not has_customer_details_after_booking:
        return False
    session = order_session_state(state)
    if str(session.get("confirmed_store_id") or "").strip() or str(session.get("confirmed_store_name") or "").strip():
        return True
    return should_use_recent_store_fact_context(content, state)


def _has_contact_detail(content: str) -> bool:
    text = str(content or "")
    if re.search(r"1[3-9]\d{9}", text):
        return True
    return any(term in text for term in ("我叫", "姓名", "名字", "电话", "手机号", "手机")) or any(
        term in text for term in _CONTACT_TERMS_UTF8
    )


def _recent_booking_intent(state: AgentState) -> bool:
    for item in reversed(state.get("conversation_history") or []):
        if isinstance(item, dict):
            role = str(item.get("role") or item.get("direction") or "").lower()
            if role and role not in {"user", "customer"}:
                continue
            content = item.get("content")
            text = str(content.get("text") if isinstance(content, dict) else content or "")
        else:
            text = str(item or "")
            if text.startswith(("小贝：", "小贝:", "客服：", "客服:", "AI回复：", "AI回复:", "助手：", "助手:")):
                continue
            if text.startswith(("客户：", "客户:", "用户：", "用户:")):
                text = text.split("：", 1)[-1] if "：" in text else text.split(":", 1)[-1]
        if any(term in text for term in _APPOINTMENT_BOOKING_INTENT_TERMS) or any(
            term in text for term in _APPOINTMENT_BOOKING_TERMS_UTF8
        ):
            return True
    return False


def _policy_tool_query(tasks: list[dict[str, Any]]) -> str:
    fragments: list[str] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        for key in ("customer_need", "answer_goal", "subtype", "policy_hint"):
            text = str(task.get(key) or "").strip()
            if text:
                fragments.append(text)
    return " ".join(fragments)[:160].strip()


def _state_policy_markers(state: AgentState, sop_stage: str) -> str:
    values: list[str] = [sop_stage]
    for key in (
        "policy_family_id",
        "exact_policy_id",
        "active_scene_id",
        "active_scene_match_level",
        "sop_stage",
        "sop_step",
        "intent",
        "subflow",
    ):
        value = state.get(key)
        if isinstance(value, str) and value:
            values.append(value)
    return " ".join(values).upper()


def _is_store_marker(markers: str) -> bool:
    return any(token in markers for token in ("SF6", "STORE", "ADDRESS", "NEAREST", "PARKING", "NAVIGATION", "ROUTE"))


def _is_appointment_marker(markers: str) -> bool:
    return any(token in markers for token in ("SF9", "APPOINTMENT", "TIME_CHECK", "VISIT_INTENT", "CONFIRM_TIME", "WEEKEND"))


def _is_case_marker(markers: str) -> bool:
    return any(
        token in markers
        for token in (
            "CASE",
            "EFFECT_REFERENCE",
            "EFFECT_CASE",
            "SHOW_CASE",
            "CASE_STUDIES",
            "效果案例",
            "案例铺垫",
            "效果铺垫",
            "案例展示",
            "看案例",
            "发案例",
            "效果图",
            "前后对比",
            "效果对比",
            "案例对比",
        )
    )


def _looks_like_fee_or_price_only_turn(content: str) -> bool:
    return any(
        term in content
        for term in (
            "乱收费",
            "隐形消费",
            "加价",
            "推销",
            "定金",
            "订金",
            "预约金",
            "尾款",
            "全款",
            "到店再付",
            "多少钱",
            "价格",
            "费用",
        )
    )


def _has_specific_store_or_location_signal(content: str) -> bool:
    return any(term in content for term in CITY_NAMES + STORE_AREA_TERMS) or any(
        term in content
        for term in (
            "我在",
            "我住",
            "附近",
            "区",
            "县",
            "县城",
            "县级市",
            "开发区",
            "经开区",
            "新区",
            "高新区",
            "机场",
            "高铁",
            "地铁",
            "科技园",
            "哪家",
            "最近",
            "离我近",
            "地址",
            "位置",
            "导航",
            "停车",
            "营业时间",
            "门店在哪里",
            "哪里有店",
        )
    ) or any(term in content for term in _STORE_FACT_TERMS_UTF8 + _LOCATION_HINT_TERMS_UTF8)


def _should_skip_store_lookup_for_confirmed_appointment(
    state: AgentState,
    content: str,
    tools: list[dict[str, Any]],
) -> bool:
    if not any(str(tool.get("name") or "").strip() == "store_lookup" for tool in tools):
        return False
    if not any(
        str(tool.get("name") or "").strip() in {"available_time", "appointment_create"}
        for tool in tools
    ):
        return False
    current_store = current_real_store_from_state(state)
    if not (str(current_store.get("id") or "").strip() or str(current_store.get("name") or "").strip()):
        return False
    text = str(content or "").strip()
    if not text:
        return False
    if _has_specific_store_or_location_signal(text) and needs_store_lookup_request(state, text):
        return False
    return (
        needs_appointment_time_request(text)
        or any(term in text for term in _APPOINTMENT_BOOKING_INTENT_TERMS)
        or any(term in text for term in _APPOINTMENT_BOOKING_TERMS_UTF8)
        or _has_contact_detail(text)
    )


def _distance_origin_from_state_or_text(state: AgentState, content: str) -> str:
    def finalize(value: str, *, fallback_city: str = "") -> str:
        result = str(value or "").strip()
        if not result:
            return ""
        if any(city_name and city_name in result for city_name in CITY_NAMES):
            return result
        area = store_text.extract_area_or_landmark(result)
        area_city = store_text.city_for_area_or_landmark(area)
        city_value = str(fallback_city or "").strip() or area_city
        if city_value:
            return result if city_value in result else f"{city_value}{result}"
        return ""

    existing = str(state.get("distance_origin") or "").strip()
    if existing:
        return finalize(existing)
    session = order_session_state(state)
    city = str(session.get("city") or "").strip() or known_city_from_state(state)
    area_or_landmark = (
        str(session.get("area_or_landmark") or "").strip()
        or str(session.get("location_preference") or "").strip()
        or known_store_area_from_history(state)
    )
    text = str(content or "").strip()
    parsed_city = store_text.extract_city(text, [])
    parsed_area = store_text.extract_area_or_landmark(text)
    if parsed_city and parsed_area:
        if parsed_city in parsed_area:
            return finalize(parsed_area, fallback_city=parsed_city)
        return finalize(f"{parsed_city}{parsed_area}", fallback_city=parsed_city)
    if parsed_area and not city:
        parsed_area_city = store_text.city_for_area_or_landmark(parsed_area)
        if parsed_area_city:
            return finalize(f"{parsed_area_city}{parsed_area}", fallback_city=parsed_area_city)
    concrete_landmark_terms = ("机场", "高崎", "科技园", "高铁", "火车站", "地铁", "商圈", "广场", "大厦")
    if text and any(term in text for term in concrete_landmark_terms):
        if city and city not in text:
            return finalize(f"{city}{text}", fallback_city=city)
        return finalize(text, fallback_city=parsed_city)
    if city and area_or_landmark:
        if city in area_or_landmark:
            return finalize(area_or_landmark, fallback_city=city)
        return finalize(f"{city}{area_or_landmark}", fallback_city=city)
    if city and any(term in text for term in ("附近", "最近", "离我近", "哪家近", "哪个近")):
        return finalize(city, fallback_city=city)
    return ""
