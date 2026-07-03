from __future__ import annotations

from typing import Any, Callable

from app.graph.nodes.activity_intro_image import activity_intro_image_url, append_activity_intro_image
from app.graph.nodes.common import model_usage_snapshot
from app.graph.nodes.reply_validation import validate_reply_consistency
from app.services.payment_collection import normalize_deposit_refund_policy_text, payment_collection_content
from app.graph.state import AgentState
from app.services.model_client import ModelClient
from app.services.trace_logger import TraceLogger


def create_synthesize_reply_node(
    *,
    trace_logger: TraceLogger,
    model_client: ModelClient | None,
    debug_message_contents: Callable[[list[dict[str, Any]]], list[str]],
    reply_messages_for_model: Callable[[AgentState], list[dict[str, Any]]],
    should_use_model_reply: Callable[[AgentState], bool],
    validated_model_messages: Callable[..., list[dict[str, Any]]],
    schedule_background_task: Callable[[AgentState], Any] | None = None,
):
    async def synthesize_reply(state: AgentState) -> dict[str, Any]:
        with trace_logger.node(
            state,
            "synthesize_reply",
            {"fact_envelope": state.get("fact_envelope"), "required_tools": state.get("required_tools")},
        ) as span:
            errors = list(state.get("errors", []))
            warnings = list(state.get("warnings", []))
            messages: list[dict[str, Any]] = []
            planner_messages: list[dict[str, Any]] = []
            reply_source = "main_model"
            model_call: dict[str, Any] | None = None

            try:
                planner_decision = str(state.get("planner_decision") or "").strip()
                planner_messages = _normalize_planner_reply_messages(state.get("planner_reply_messages"), state=state)
                planner_direct_valid = False
                if planner_decision == "direct_reply" and planner_messages and not state.get("tool_policy_violations"):
                    try:
                        validate_reply_consistency(planner_messages, state)
                        planner_direct_valid = True
                    except Exception as planner_validation_exc:
                        warnings.append(
                            {
                                "node": "synthesize_reply",
                                "message": "planner_direct_reply_rejected",
                                "detail": f"{type(planner_validation_exc).__name__}: {planner_validation_exc}",
                            }
                        )
                if planner_decision == "no_reply":
                    no_reply_fallback = _maybe_build_no_reply_dissatisfaction_fallback(state)
                    if no_reply_fallback:
                        messages = no_reply_fallback
                        validate_reply_consistency(messages, state)
                        reply_source = "deterministic_dissatisfaction_fallback"
                        model_call = {
                            "name": "deterministic_dissatisfaction_fallback",
                            "input": {"decision": planner_decision, "messages": 0},
                            "output": {"messages": len(messages)},
                        }
                    else:
                        reply_source = "planner_no_reply"
                        model_call = {
                            "name": "planner_direct_reply",
                            "input": {"decision": planner_decision, "messages": 0},
                            "output": {"messages": 0},
                        }
                elif planner_direct_valid:
                    messages = planner_messages
                    reply_source = "planner_direct_reply"
                    model_call = {
                        "name": "planner_direct_reply",
                        "input": {"decision": planner_decision, "messages": len(planner_messages)},
                        "output": {"messages": len(messages)},
                    }
                else:
                    if not (model_client and model_client.available and should_use_model_reply(state)):
                        raise RuntimeError("reply_synthesizer_model_required")
                    model_call = {"name": "reply_synthesizer_model", "input": {"tier": "reply", "required": True}}
                    model_messages = reply_messages_for_model(state)
                    payload = await model_client.chat_json(model_messages, tier="reply")
                    model_call["usage"] = model_usage_snapshot(model_client)
                    try:
                        messages = validated_model_messages(payload, state)
                        validate_reply_consistency(messages, state)
                    except Exception as validation_exc:
                        retry_messages = _reply_retry_messages(model_messages, validation_exc)
                        retry_payload = await model_client.chat_json(retry_messages, tier="reply")
                        model_call["retry"] = {
                            "reason": f"{type(validation_exc).__name__}: {validation_exc}",
                            "usage": model_usage_snapshot(model_client),
                        }
                        try:
                            messages = validated_model_messages(retry_payload, state)
                            validate_reply_consistency(messages, state)
                        except Exception as retry_exc:
                            retry_validation_exc = retry_exc
                            handoff_fallback = _maybe_build_handoff_notice_fallback(messages, state, retry_validation_exc)
                            if handoff_fallback is not None:
                                messages = handoff_fallback
                                validate_reply_consistency(messages, state)
                            else:
                                repaired_messages = _maybe_append_required_store_address(messages, state, retry_validation_exc)
                                if repaired_messages is None:
                                    raise
                                messages = repaired_messages
                                validate_reply_consistency(messages, state)
                        else:
                            retry_validation_exc = None
                        if retry_validation_exc is not None and _messages_have_handoff_notice(messages):
                            model_call["fallback"] = {
                                "reason": f"{type(retry_validation_exc).__name__}: {retry_validation_exc}",
                                "strategy": "deterministic_handoff_notice",
                            }
                    messages = _filter_unsupported_images(messages, state, warnings)
                    model_call["draft_messages"] = debug_message_contents(messages)
                    model_call["output"] = {"messages": len(messages)}
                messages = _normalize_deposit_refund_policy_messages(append_activity_intro_image(messages, state, warnings))
                for warning in warnings:
                    if isinstance(warning, dict) and warning.get("message") == "activity_intro_image_appended":
                        warning.setdefault("node", "synthesize_reply")
            except Exception as exc:
                model_call = model_call or {"name": "reply_synthesizer_model", "input": {}}
                primary_error = f"{type(exc).__name__}: {exc}"
                handoff_fallback = _maybe_build_handoff_notice_fallback(messages or planner_messages, state, exc)
                if handoff_fallback is not None:
                    messages = handoff_fallback
                    validate_reply_consistency(messages, state)
                    reply_source = "deterministic_handoff_notice_fallback"
                    model_call["fallback"] = {
                        "reason": primary_error,
                        "strategy": "deterministic_handoff_notice",
                    }
                    model_call["output"] = {"messages": len(messages)}
                else:
                    model_call["error"] = primary_error
                    errors.append(
                        {
                            "node": "synthesize_reply",
                            "message": "final_reply_failed",
                            "detail": primary_error,
                        }
                    )
                    messages = []

            if model_call:
                span["entry"]["tool_calls"] = [model_call]
            output = {
                "reply_messages": messages,
                "reply_source": reply_source,
                "postprocess_changed": False,
                "postprocess_reasons": [],
                "errors": errors,
                "warnings": warnings,
                "trace": state.get("trace", []),
            }
            span["output_snapshot"] = output
            _schedule_profile_event_background(schedule_background_task, {**state, **output})
            return output

    return synthesize_reply


def _reply_retry_messages(messages: list[dict[str, Any]], exc: Exception) -> list[dict[str, Any]]:
    repair_hint = _reply_repair_hint(str(exc))
    retry_instruction = (
        "上一次输出没有通过 JSON schema 校验。"
        f"错误：{type(exc).__name__}: {exc}。"
        f"{repair_hint}"
        "请只重新输出严格 JSON 对象，顶层必须包含非空 reply_messages 数组；"
        "不要解释错误，不要输出 markdown，不要输出内部分析。"
    )
    return [*messages, {"role": "user", "content": retry_instruction}]


def _maybe_append_required_store_address(
    messages: list[dict[str, Any]],
    state: AgentState,
    exc: Exception,
) -> list[dict[str, Any]] | None:
    if "store_address_message_required_when_reply_promises_location_card" not in str(exc):
        return None
    store_id = _single_store_fact_id(state)
    if not store_id:
        return None
    if any(isinstance(item, dict) and str(item.get("type") or "") == "store_address" for item in messages):
        return None
    return _renumber([*messages, {"type": "store_address", "content": {"store_id": store_id}}])


def _maybe_build_handoff_notice_fallback(
    messages: list[dict[str, Any]],
    state: AgentState,
    exc: Exception,
) -> list[dict[str, Any]] | None:
    if not (
        "human_handoff_notice" in str(exc)
        or _messages_have_handoff_notice(messages)
        or _state_requests_handoff_notice(state)
    ):
        return None
    reason = _handoff_notice_reason(state)
    fallback = [
        {"type": "text", "order": 1, "content": _handoff_customer_text(state, reason)},
        {"type": "human_handoff_notice", "order": 2, "content": {"handoff_reason": reason}},
    ]
    return _renumber(fallback)


def _maybe_build_no_reply_dissatisfaction_fallback(state: AgentState) -> list[dict[str, Any]] | None:
    content = str(state.get("normalized_content") or state.get("content") or "")
    if not content or _contains_any(content, ("别回", "不用回", "不要回", "别理我", "不想聊")):
        return None
    if not _contains_any(content, ("说了三遍", "说了很多遍", "一直问", "还问", "烦死了", "很烦", "不会回答", "换人")):
        return None
    reason = "客户强烈不满：反复询问或回答不顺导致不满"
    return _renumber(
        [
            {
                "type": "text",
                "order": 1,
                "content": "抱歉，刚刚反复确认让您不舒服了。我先按您前面说的重点接着处理，不再重复问；您把当前最要紧的问题发我，我直接承接。",
            },
            {"type": "human_handoff_notice", "order": 2, "content": {"handoff_reason": reason}},
        ]
    )


def _messages_have_handoff_notice(messages: list[dict[str, Any]]) -> bool:
    return any(
        isinstance(item, dict) and str(item.get("type") or "") in {"human_handoff", "human_handoff_notice"}
        for item in messages
    )


def _state_requests_handoff_notice(state: AgentState) -> bool:
    handoff = state.get("handoff") if isinstance(state.get("handoff"), dict) else {}
    if bool(handoff.get("needed")):
        return True
    required_tools = state.get("required_tools") if isinstance(state.get("required_tools"), list) else []
    if any(isinstance(item, dict) and str(item.get("name") or "") == "professional_assist" for item in required_tools):
        return True
    tool_results = state.get("tool_results") if isinstance(state.get("tool_results"), dict) else {}
    assist = tool_results.get("professional_assist") if isinstance(tool_results.get("professional_assist"), dict) else {}
    if str(assist.get("status") or "") == "requested":
        return True
    structured = _structured_facts(state)
    assist_fact = structured.get("professional_assist") if isinstance(structured.get("professional_assist"), dict) else {}
    return str(assist_fact.get("status") or "") == "requested"


def _handoff_notice_reason(state: AgentState) -> str:
    candidates: list[str] = []
    structured = _structured_facts(state)
    assist_fact = structured.get("professional_assist") if isinstance(structured.get("professional_assist"), dict) else {}
    candidates.append(str(assist_fact.get("reason") or ""))
    tool_results = state.get("tool_results") if isinstance(state.get("tool_results"), dict) else {}
    assist = tool_results.get("professional_assist") if isinstance(tool_results.get("professional_assist"), dict) else {}
    candidates.append(str(assist.get("reason") or ""))
    handoff = state.get("handoff") if isinstance(state.get("handoff"), dict) else {}
    candidates.append(str(handoff.get("reason") or ""))
    for item in state.get("required_tools") or []:
        if isinstance(item, dict) and str(item.get("name") or "") == "professional_assist":
            candidates.append(str(item.get("reason") or item.get("purpose") or ""))
    for value in candidates:
        reason = " ".join(value.split())
        if reason:
            return reason[:180]
    return "高风险或人工诉求，需要内部关注"


def _handoff_customer_text(state: AgentState, reason: str) -> str:
    text = " ".join(
        str(value or "")
        for value in (
            state.get("normalized_content"),
            state.get("content"),
            reason,
            state.get("planner_sub_rule_id"),
        )
    )
    if _contains_any(text, ("心脏", "高血压", "糖尿病", "病史", "过敏", "怀孕", "孕期", "哺乳", "未成年", "处方", "用药", "病例", "病历", "报告")):
        return "这个需要到店先做专业检测，让门店专业人员看下适不适合再安排。您什么时候方便到店？"
    if _contains_any(text, ("红肿", "肿痛", "刺痛", "发烫", "感染", "坏死", "伤口", "出血", "看不清", "胸闷", "喘不过气", "严重不适")):
        return "您这个不适情况先别继续刺激皮肤。您是在我们哪家门店做的、做的什么项目、什么时间做的？把这些信息发我，我按实际记录核对。"
    if _contains_any(text, ("退款", "退钱", "投诉", "维权", "曝光", "付款", "支付", "扣款", "多收", "订单", "骗")):
        return "我先把情况核对清楚。您是在我们哪家门店做的？把门店、付款时间和项目发我一下，我按实际记录核对。"
    if _contains_any(text, ("人工", "真人", "换人")):
        return "您先把具体问题发我，我直接帮您把当前情况梳理清楚；如果涉及门店、付款或项目，也把门店和时间一起发我。"
    return "我先把情况核对清楚。您把具体问题、涉及的门店和时间发我一下，我按实际记录核对。"


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _structured_facts(state: AgentState) -> dict[str, Any]:
    fact_envelope = state.get("fact_envelope") if isinstance(state.get("fact_envelope"), dict) else {}
    structured = fact_envelope.get("structured_facts") if isinstance(fact_envelope.get("structured_facts"), dict) else {}
    return structured if isinstance(structured, dict) else {}


def _single_store_fact_id(state: AgentState) -> str:
    structured = _structured_facts(state)
    store_facts = [item for item in structured.get("store_facts") or [] if isinstance(item, dict)]
    ids = list(
        dict.fromkeys(
            str(item.get("store_id") or item.get("id") or "").strip()
            for item in store_facts
            if str(item.get("store_id") or item.get("id") or "").strip()
        )
    )
    return ids[0] if len(ids) == 1 else ""


def _reply_repair_hint(error: str) -> str:
    if "payment_collection_required" in error:
        return "如果文本承诺发送预约金入口或 next_step=send_deposit，必须同时输出 payment_collection；否则删除发入口承诺并调整回复节奏。"
    if "payment_collection_amount_text_mismatch" in error:
        return "预约金卡片金额必须和文本一致；同行按每位10元锁活动名额，2位说一共20元，3位说一共30元，4位说一共40元。"
    if "payment_participant_count_confirm_required" in error:
        return "客户同行人数超过4位时不要发送 payment_collection；改成 text 确认一共几位到店，或说明多人同行先由门店承接确认。"
    if "human_handoff_notice" in error:
        return "需要内部关注时，先用客户可见 text 正面回答和引导到店检测或核对事实，再追加 human_handoff_notice；text 不要说转人工、转同事、专业同事、稍等一下哈。"
    if "ambiguous_deposit_refund_wording" in error:
        return "预约金退款口径统一说“到店抵扣，不做退10元”。不要说“退还10元/退还20元/全额退款/一分不少退还/不满意退”，避免同客户口径冲突。"
    if "case_context_must_not_use_activity_intro_image" in error:
        return "本轮客户在问效果或案例，且已有 case_facts 案例图片事实。必须回答效果顾虑，并且如输出 image，只能使用 case_facts 里的 image_url；不要输出活动宣传图。"
    if "reply_too_similar" in error:
        return "客户在重复追问同类问题，请换一个角度回答，不要复用上一轮核心话术。"
    if "two_text_required" in error:
        return "这条回复同时包含回答和下一步推进，请改成两条短 text：第一条只回答问题，第二条只轻推一个动作。"
    if "parking_fact_required" in error:
        return "没有停车工具事实时，不要说有停车场或可以停车，只能说需要核对或询问门店/区域。"
    if "business_hours_fact_required" in error:
        return "没有营业时间工具事实时，不要输出具体营业时间。"
    if "store_address_fact_required" in error:
        return "没有门店详情事实时，不要输出具体地址。"
    if "unsupported_store_address_message" in error:
        return "store_address 卡片的 store_id 必须来自本轮门店工具事实或请求里明确确认的门店 ID；没有匹配门店事实时，不要输出 store_address，只能用文字说明暂时没查到并继续确认城市、区域或门店。"
    if "store_address_message_required_when_reply_promises_location_card" in error:
        return "你已经在文本里承诺发地址、位置或让客户点开导航；如果本轮有门店事实，必须追加对应 store_address 卡片。若不想发卡，就删除“我发您/点开导航/位置卡”等承诺。"
    if "distance_value_not_customer_visible" in error:
        return "distance_calculate 只用于内部排序门店。客户可见回复只说优先哪家或哪家更近一些，不要输出几公里、几分钟、车程或步行时长。"
    if "distance_fact_required" in error:
        return "没有 distance_calculate 排序事实时，不要输出最近、离您最近、较近、就近等距离排序表达。只回答门店名、地址、停车或营业时间等已有门店事实，再问客户哪个区域/哪家更方便。"
    if "available_time_fact_required" in error:
        return "available_time 工具失败、超时或没有返回可用 slots 时，不要说有空、可以约、有时间或有名额；只能说明暂时没查到实时档期，并继续确认门店/时间或让门店核对。"
    if "too_many_appointment_time_options" in error:
        return "档期回复最多只能给 1 个推荐时间和 1 个备选时间。请基于 recommended_slot 和 backup_slots 重写，不要列完整时间表。"
    if "unfinished_appointment_lookup_promise" in error:
        return "没有真实 available_time 档期事实时，不要说“查档期/核对档期/看档期/可约时间”。如果本轮已有门店事实，只回答门店位置并引导客户选择区域或门店；如果缺门店或具体时间，只问客户补一个关键字段。"
    if "unfinished_tool_promise_after_tool_execution" in error:
        return "本轮工具已经执行完，不能再说“马上查、帮您查一下、帮您找案例、稍后给您”。请直接基于已有事实回答；如果事实不足，只问客户补一个关键字段，或说明当前没有可发事实。"
    return ""


def _filter_unsupported_images(
    messages: list[dict[str, Any]],
    state: AgentState,
    warnings: list[Any],
) -> list[dict[str, Any]]:
    allowed_urls = _case_image_urls(state)
    filtered: list[dict[str, Any]] = []
    removed_urls: list[str] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "") != "image":
            filtered.append(item)
            continue
        url = _message_url(item.get("content"))
        if url and url in allowed_urls:
            filtered.append(item)
        else:
            removed_urls.append(url or "")
    if removed_urls:
        warnings.append(
            {
                "node": "synthesize_reply",
                "message": "unsupported_image_removed",
                "detail": {"removed_urls": removed_urls},
            }
        )
    return _renumber(filtered)


def _case_image_urls(state: AgentState) -> set[str]:
    fact_envelope = state.get("fact_envelope") if isinstance(state.get("fact_envelope"), dict) else {}
    structured = fact_envelope.get("structured_facts") if isinstance(fact_envelope.get("structured_facts"), dict) else {}
    urls: set[str] = set()
    for item in structured.get("case_facts") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("image_url") or "").strip()
        if url:
            urls.add(url)
    activity_url = activity_intro_image_url(state)
    if activity_url:
        urls.add(activity_url)
    return urls


def _message_url(content: Any) -> str:
    if isinstance(content, dict):
        return str(content.get("url") or content.get("image_url") or "").strip()
    return str(content or "").strip()


def _renumber(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, item in enumerate(messages, start=1):
        result.append({**item, "order": index})
    return result


def _normalize_planner_reply_messages(value: Any, *, state: AgentState | None = None) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    messages: list[dict[str, Any]] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            continue
        message_type = str(item.get("type") or "text").strip()
        content = item.get("content")
        if message_type == "text":
            if isinstance(content, dict):
                text = str(content.get("text") or "").strip()
            else:
                text = str(content or item.get("text") or "").strip()
            text = normalize_deposit_refund_policy_text(text)
            if text:
                messages.append({"type": "text", "order": int(item.get("order") or index), "content": {"text": text}})
            continue
        if message_type == "payment_collection":
            messages.append(
                {
                    "type": "payment_collection",
                    "order": int(item.get("order") or index),
                    "content": payment_collection_content(content, state=state, messages=messages),
                }
            )
            continue
        if message_type in {"human_handoff", "human_handoff_notice"}:
            reason = str(content.get("handoff_reason") if isinstance(content, dict) else content or "").strip()
            if reason:
                messages.append({"type": "human_handoff_notice", "order": int(item.get("order") or index), "content": {"handoff_reason": reason}})
            continue
        if message_type == "store_address":
            store_id = str(content.get("store_id") if isinstance(content, dict) else content or "").strip()
            if store_id:
                messages.append({"type": "store_address", "order": int(item.get("order") or index), "content": {"store_id": store_id}})
    return messages


def _normalize_deposit_refund_policy_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "") != "text":
            output.append(item)
            continue
        content = item.get("content")
        if isinstance(content, dict):
            text = normalize_deposit_refund_policy_text(str(content.get("text") or ""))
            output.append({**item, "content": {**content, "text": text}})
        else:
            output.append({**item, "content": normalize_deposit_refund_policy_text(str(content or ""))})
    return _renumber(output)


def _schedule_profile_event_background(
    schedule_background_task: Callable[[AgentState], Any] | None,
    state: AgentState,
) -> None:
    if not schedule_background_task:
        return
    try:
        schedule_background_task(state)
    except RuntimeError:
        return
