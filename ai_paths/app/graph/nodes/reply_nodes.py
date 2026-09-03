from __future__ import annotations

import copy

import json


from typing import Any, Callable

from app.graph.nodes.activity_intro_image import activity_intro_image_url, append_activity_intro_image


from app.graph.nodes.reply_quality import (
    collect_reply_soft_warnings,
)

from app.graph.nodes.reply_validation import (
    _paid_deposit_context,
    _parallel_paid_deposit_context,
    _parallel_shared_context,
    extract_image_url_from_text,
    message_content_text,
    completed_parallel_selected_content_ids,
    validate_reply_consistency,
)

from app.graph.nodes.reply_context import reply_recovery_payload_for_model

from app.graph.nodes.material_selection import (
    parallel_reply_payload,
)


from app.prompts.reply_synthesizer import (
    build_parallel_reply_messages,
)

from app.graph.nodes.common import json_dumps

from app.services.payment_collection import (
    normalize_payment_amount_text,
    payment_collection_content,
    payment_collection_context,
)

from app.services.risk_hold import explicit_professional_assist_reason, health_risk_hold, is_hard_health_risk_hold


from app.graph.state import AgentState

from app.services.model_client import ModelClient


REPLY_RECOVERY_SYSTEM_PROMPT = """你是企业微信淡斑活动的真人销售回复模型。完整 Reply 已超时或未通过硬事实校验，请根据去重后的完整业务事实重新生成客户可见回复。精简只删除重复字段，不代表可以忽略业务规则、最近历史或结构事实。

要求：
- 只输出 JSON 对象：{\"reply_messages\":[{\"type\":\"text\",\"order\":1,\"content\":\"...\"}]}。
- 当前消息优先，结合最近12条原序历史。先直接解决本轮问题，再自然承接一个销售动作；“人呢/在吗”等短催促直接续最近未完动作，不列选项重问意图；不要复读整套规则，不要说“继续处理、安排下一步、温馨提醒、尊敬的客户”。
- 像真人微信聊天：短句、口语、具体，不暴露“事实、排序、工具、系统、流程、状态”等内部表达。客户只回“好/嗯”时，确认并轻推下一步，不重播上一轮顾虑、案例、价格和预约金全套内容。
- 只能使用输入中的工具、门店、订单、支付、图片和档期事实；没有事实就不要编。
- 发过 payment_collection 只代表发过卡，不代表已支付。成功支付截图、订单 prepay_paid 或结构化 paid_by_platform_transfer_event 是权威已付；客户普通文字说已付只能按声明承接，不能说已核款。
- 人数按到店总人数理解：“我朋友也一起”通常是本人+1位朋友=2位；“我带两个朋友”是本人+2位朋友=3位。卡片金额必须服从 Planner 的人数和金额决策。
- 客户明确要入口/预约时，不要因为缺订单或开单失败暴露“入口没对上/不能发卡”，也不能再反问“如果你要我再发”；活动报价已铺垫且无硬阻断时按当前结构事实发卡，否则只补最小必要信息。
- 没有真实 case_facts/image 不能说“我给您发图/图发您了”；有图且当前明确要图时才输出 image。上一轮顾虑已回答、客户只确认时不要擅自重发案例。
- 客户因担心一次没效果或可能需要多次而说不做时，这是带原因的效果异议，不按“不做了”字面送客。先明确“当前淡斑效果活动价就是268元、绝大多数客户都是一次就好”，有真实 case_facts 时同轮发图，再说明完成线上活动登记后可到线下门店免费做皮肤检测并由门店结合具体情况讲解；不得说因人而异、可能需要多次、单次单部位或以后再来找我。明确要求停止联系或近期已处理同一异议后再次强拒绝时才收口。
- `store_resolution_fact.status=no_valid_candidate` 且 `candidate_search_complete=true` 表示客户位置已经确认，完整查询后对应省、市或地区当前没有可发送的合法门店。不要承诺发卡；如实、简短地说明覆盖事实，不再追问该范围内更具体的区县或商圈，也不承诺重新找其他门店。可以根据完整聊天自主决定是否用真实效果或活动价值承接。若 `candidate_search_complete=false` 或 status=search_incomplete，这是门店事实加载不完整，不能断言没有门店、不能让客户重复提供已经足够的位置，也不能猜测城市或门店。
- 退款、扣款异常只能先核对门店、时间、金额、项目或截图；不能说已经同意/正在办理退款，也不能承诺自动退回、原路到账或处理时效。
- 不输出公里、分钟、车程；不承诺绝对效果；没有真实预约事实不能说已经安排好。
- payment_collection、store_address、image、human_handoff_notice 必须使用输入中已核验的结构事实。
- 使用自然微信口吻，不解释系统故障，不输出 markdown 或内部分析。
"""

def _parallel_content_selection_metrics(
    state: AgentState,
    *,
    messages: list[dict[str, Any]],
    selected_ids: list[str],
    used_fact_refs: list[str],
) -> dict[str, Any]:
    joined = state.get("evidence_join") if isinstance(state.get("evidence_join"), dict) else {}
    nominated_ids = [
        str(item.get("content_id") or item.get("id") or "").strip()
        for item in joined.get("content_candidates") or []
        if isinstance(item, dict)
        and str(item.get("content_id") or item.get("id") or "").strip()
    ]
    adopted_ids = [str(item).strip() for item in selected_ids if str(item).strip()]
    metric_state: AgentState = dict(state)
    metric_state["reply_used_fact_refs"] = [
        str(item).strip() for item in used_fact_refs if str(item).strip()
    ]
    delivered_ids = completed_parallel_selected_content_ids(
        messages,
        metric_state,
        adopted_ids,
    )
    return {
        "schema_version": "v2_content_selection_metrics_v1",
        "nominated_ids": list(dict.fromkeys(nominated_ids)),
        "adopted_ids": list(dict.fromkeys(adopted_ids)),
        "delivered_ids": list(dict.fromkeys(delivered_ids)),
        "nominated_count": len(set(nominated_ids)),
        "adopted_count": len(set(adopted_ids)),
        "delivered_count": len(set(delivered_ids)),
    }

def _planner_direct_reply_is_valid(
    planner_decision: str,
    planner_messages: list[dict[str, Any]],
    state: AgentState,
    warnings: list[dict[str, Any]],
) -> bool:
    if planner_decision != "direct_reply" or not planner_messages:
        return False
    try:
        validate_reply_consistency(planner_messages, state)
        if state.get("tool_policy_violations"):
            warnings.append(
                {
                    "node": "synthesize_reply",
                    "message": "planner_direct_reply_used_despite_non_visible_tool_policy_violations",
                    "detail": "Planner draft passed final reply consistency; violations remain in trace for repair analysis.",
                }
            )
        return True
    except Exception as exc:
        warnings.append(
            {
                "node": "synthesize_reply",
                "message": "planner_direct_reply_rejected",
                "detail": f"{type(exc).__name__}: {exc}",
            }
        )
        return False

def _validate_selected_content_ids(payload: dict[str, Any], state: AgentState) -> None:
    """Require adoption metadata to reference a deliverable current candidate."""

    selected_ids = {
        str(item or "").strip()
        for item in payload.get("selected_content_ids") or []
        if str(item or "").strip()
    }
    if not selected_ids:
        return
    allowed_ids = {
        str(item or "").strip()
        for item in parallel_reply_payload(state).get("allowed_selected_content_ids") or []
        if str(item or "").strip()
    }
    invalid_ids = sorted(selected_ids - allowed_ids)
    if invalid_ids:
        raise ValueError("selected_content_id_not_selectable:" + ",".join(invalid_ids))

def _resolve_selected_content_media_placeholders(
    payload: dict[str, Any],
    state: AgentState,
) -> bool:
    """Expand media placeholders backed by Reply's explicit content selection.

    Some JSON-capable models emit ``{"type":"image","content":"content_id"}``
    while also selecting that exact content ID. The ID is not a platform URL,
    but it is an unambiguous structural pointer to the current Gate candidate.
    Resolve only that exact pointer; never select a candidate or media item on
    the model's behalf.
    """

    selected_ids = {
        str(item or "").strip()
        for item in payload.get("selected_content_ids") or []
        if str(item or "").strip()
    }
    messages = payload.get("reply_messages")
    if not selected_ids or not isinstance(messages, list):
        return False

    joined = state.get("evidence_join") if isinstance(state.get("evidence_join"), dict) else {}
    raw_candidates = (
        joined.get("content_candidates")
        if isinstance(joined.get("content_candidates"), list)
        else []
    )
    media_by_content_id: dict[str, dict[str, list[str]]] = {}
    for candidate in raw_candidates:
        if not isinstance(candidate, dict):
            continue
        content_id = str(candidate.get("content_id") or candidate.get("id") or "").strip()
        if content_id not in selected_ids:
            continue
        typed_urls: dict[str, list[str]] = {"image": [], "video": []}
        candidate_messages = candidate.get("messages")
        if not isinstance(candidate_messages, list):
            candidate_messages = candidate.get("media") if isinstance(candidate.get("media"), list) else []
        for candidate_message in candidate_messages:
            if not isinstance(candidate_message, dict):
                continue
            message_type = str(candidate_message.get("type") or "").strip()
            if message_type not in typed_urls:
                continue
            url = _passive_media_url(candidate_message)
            if url.lower().startswith(("http://", "https://")) and url not in typed_urls[message_type]:
                typed_urls[message_type].append(url)
        media_by_content_id[content_id] = typed_urls

    resolved = False
    offsets: dict[tuple[str, str], int] = {}
    for message in messages:
        if not isinstance(message, dict):
            continue
        message_type = str(message.get("type") or "").strip()
        if message_type not in {"image", "video"}:
            continue
        placeholder = str(message.get("content") or "").strip()
        if placeholder not in selected_ids:
            continue
        options = media_by_content_id.get(placeholder, {}).get(message_type) or []
        offset_key = (placeholder, message_type)
        offset = offsets.get(offset_key, 0)
        if offset >= len(options):
            continue
        message["content"] = options[offset]
        offsets[offset_key] = offset + 1
        resolved = True
    return resolved

def _reply_reference_set(value: Any) -> set[str]:
    refs: set[str] = set()

    def visit(item: Any, *, key: str = "") -> None:
        if isinstance(item, dict):
            for child_key, child in item.items():
                normalized_key = str(child_key or "").strip().lower()
                if normalized_key in {"ref", "used_fact_ref"} and isinstance(child, str) and child.strip():
                    refs.add(child.strip())
                else:
                    visit(child, key=normalized_key)
            return
        if isinstance(item, list):
            if key.endswith("refs"):
                refs.update(str(child).strip() for child in item if isinstance(child, str) and child.strip())
            for child in item:
                visit(child, key=key)

    visit(value)
    return refs

def _validate_parallel_raw_reply_schema(payload: dict[str, Any]) -> None:
    """Reject lossy compatibility before customer-visible normalization."""

    sales_judgment = payload.get("sales_judgment")
    if isinstance(sales_judgment, dict):
        # Some providers place optional top-level audit fields inside the
        # adjacent sales_judgment object. Lifting those fields is lossless
        # schema normalization; it does not reinterpret the model's decision.
        for field in (
            "used_fact_refs",
            "selected_content_ids",
            "content_decisions",
            "knowledge_use",
            "payment_assessment",
            "deposit_evidence",
            "safety_assessment",
            "party_size_assessment",
            "commit_actions",
            "policy_decision",
        ):
            if field not in payload and field in sales_judgment:
                payload[field] = sales_judgment.pop(field)

    payload["action"] = _reply_action_from_payload(payload)

    # commit_actions is optional and an omitted/null value means no deferred
    # write. Normalizing that value is schema cleanup, not a business action.
    if payload.get("commit_actions") is None:
        payload["commit_actions"] = []

    for field in (
        "used_fact_refs",
        "selected_content_ids",
        "content_decisions",
        "commit_actions",
    ):
        if not isinstance(payload.get(field), list):
            payload[field] = []

    messages = payload.get("reply_messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("Model JSON missing reply_messages")
    allowed_types = {
        "text",
        "image",
        "video",
        "store_address",
        "payment_collection",
        "human_handoff_notice",
    }
    payment_count = 0
    handoff_count = 0
    handoff_seen = False
    for index, item in enumerate(messages):
        if not isinstance(item, dict):
            raise ValueError(f"invalid_parallel_reply_message_object:{index}")
        message_type = item.get("type")
        if message_type not in allowed_types:
            raise ValueError(f"invalid_parallel_reply_message_type:{index}")
        content = item.get("content")
        if message_type in {"store_address", "payment_collection"} and isinstance(content, str):
            try:
                parsed_content = json.loads(content)
            except json.JSONDecodeError:
                parsed_content = None
            if isinstance(parsed_content, dict):
                content = parsed_content
                item["content"] = parsed_content
        if message_type == "text":
            if not isinstance(content, str) or not content.strip():
                raise ValueError(f"invalid_parallel_reply_message_content:{index}:text")
            if extract_image_url_from_text(content):
                raise ValueError(f"parallel_text_must_not_embed_image_url:{index}")
        elif message_type in {"image", "video"}:
            if isinstance(content, dict):
                media_url = str(
                    content.get("url")
                    or content.get("image_url")
                    or content.get("video_url")
                    or ""
                ).strip()
                if media_url:
                    # Lossless provider-schema normalization. The platform
                    # protocol requires the URL string as message content.
                    item["content"] = media_url
                    content = media_url
            else:
                media_url = str(content or "").strip()
            if not media_url.lower().startswith(("http://", "https://")):
                raise ValueError(f"invalid_parallel_reply_message_content:{index}:{message_type}")
        elif message_type == "store_address":
            if not isinstance(content, dict) or not str(content.get("store_id") or "").strip():
                raise ValueError(f"invalid_parallel_reply_message_content:{index}:store_address")
        elif message_type == "payment_collection":
            if not isinstance(content, dict):
                raise ValueError(f"invalid_parallel_reply_message_content:{index}:payment_collection")
        elif not message_content_text(content):
            raise ValueError(f"invalid_parallel_reply_message_content:{index}:{message_type}")
        if message_type == "payment_collection":
            payment_count += 1
        if message_type == "human_handoff_notice":
            handoff_count += 1
            handoff_seen = True
        elif handoff_seen:
            raise ValueError("parallel_handoff_notice_must_follow_visible_messages")
    if payment_count > 1:
        raise ValueError("duplicate_payment_collection_in_single_turn")
    if handoff_count > 1:
        raise ValueError("duplicate_human_handoff_notice_in_single_turn")

def _raise_repairable_reply_quality_issues(messages: list[dict[str, Any]], state: AgentState) -> None:
    # Style and phrasing diagnostics are observations only.  Promoting them
    # to hard repair would let Python overrule Reply's sales judgement.
    if not state.get("evidence_join"):
        collect_reply_soft_warnings(messages, state)

def _prepare_structural_messages(
    messages: list[dict[str, Any]],
    state: AgentState,
    warnings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if state.get("evidence_join"):
        # Reply owns content selection, wording, and sales judgement. Once it
        # explicitly adopts and cites a current Gate asset, its configured
        # image/video payload is a delivery artifact rather than a new sales
        # decision. Complete that passive-media delivery without touching text,
        # store selection, payment cards, or the chosen action.
        prepared, materialized_content_ids = _materialize_selected_content_media(
            messages,
            state,
        )
        if materialized_content_ids:
            warnings.append(
                {
                    "node": "synthesize_reply",
                    "message": "selected_content_media_materialized",
                    "content_ids": materialized_content_ids,
                }
            )
        # Current-turn tool structures are factual delivery artifacts, so
        # materialize exact verified store cards separately. Gate assets can
        # never authorize or choose stores through the helper above.
        prepared, store_delivery_materialized = _materialize_required_store_delivery(
            prepared,
            state,
        )
        if store_delivery_materialized:
            warnings.append(
                {
                    "node": "synthesize_reply",
                    "message": "store_delivery_materialized_from_tool_fact",
                }
            )
        return _renumber(prepared)
    prepared = _filter_unsupported_media(messages, state, warnings)
    prepared = append_activity_intro_image(prepared, state, warnings)
    prepared, duplicate_payment_removed = _dedupe_payment_collection_messages(prepared)
    if duplicate_payment_removed:
        warnings.append({"node": "synthesize_reply", "message": "duplicate_payment_collection_removed"})
    prepared = _normalize_payment_amount_text_messages(prepared)
    prepared = _maybe_append_planner_payment_structure(prepared, state)
    prepared, duplicate_payment_removed = _dedupe_payment_collection_messages(prepared)
    if duplicate_payment_removed:
        warnings.append({"node": "synthesize_reply", "message": "duplicate_payment_collection_removed"})
    for warning in warnings:
        if isinstance(warning, dict) and warning.get("message") == "activity_intro_image_appended":
            warning.setdefault("node", "synthesize_reply")
    return prepared

def _materialize_selected_content_media(
    messages: list[dict[str, Any]],
    state: AgentState,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Complete image/video delivery already chosen by Reply.

    Selection remains entirely model-owned. The selected content ID is the
    model's explicit adoption decision; requiring a second fact-reference field
    for the same decision creates a contradictory contract and can discard an
    otherwise valid reply. This helper accepts only the exact current Gate
    candidate payload and never materializes store or payment actions.
    """

    selected_ids = list(
        dict.fromkeys(
            str(item or "").strip()
            for item in state.get("reply_selected_content_ids") or []
            if str(item or "").strip()
        )
    )
    if not selected_ids:
        return list(messages), []

    joined = state.get("evidence_join") if isinstance(state.get("evidence_join"), dict) else {}
    candidates = joined.get("content_candidates") if isinstance(joined.get("content_candidates"), list) else []
    candidates_by_id = {
        str(item.get("content_id") or item.get("id") or "").strip(): item
        for item in candidates
        if isinstance(item, dict)
        and str(item.get("content_id") or item.get("id") or "").strip()
    }

    prepared = list(messages)
    emitted = {
        (str(item.get("type") or "").strip(), _passive_media_url(item))
        for item in prepared
        if isinstance(item, dict)
        and str(item.get("type") or "").strip() in {"image", "video"}
        and _passive_media_url(item)
    }
    materialized_ids: list[str] = []
    pending_media: list[dict[str, Any]] = []
    for content_id in selected_ids:
        candidate = candidates_by_id.get(content_id)
        if not isinstance(candidate, dict):
            continue
        if str(candidate.get("delivery_status") or "").strip() == "completed":
            continue
        candidate_messages = candidate.get("messages")
        if not isinstance(candidate_messages, list):
            candidate_messages = (
                candidate.get("reply_messages")
                if isinstance(candidate.get("reply_messages"), list)
                else []
            )
        added = False
        for item in candidate_messages:
            if not isinstance(item, dict):
                continue
            message_type = str(item.get("type") or "").strip()
            if message_type not in {"image", "video"}:
                continue
            media_url = _passive_media_url(item)
            if not media_url or not media_url.lower().startswith(("http://", "https://")):
                continue
            key = (message_type, media_url)
            if key in emitted:
                continue
            pending_media.append({"type": message_type, "content": media_url})
            emitted.add(key)
            added = True
        if added:
            materialized_ids.append(content_id)
    if pending_media:
        # Passive evidence belongs before payment or handoff side effects. This
        # preserves those message types' terminal position without changing
        # Reply text or choosing any new action.
        insert_at = next(
            (
                index
                for index, item in enumerate(prepared)
                if isinstance(item, dict)
                and str(item.get("type") or "").strip()
                in {"payment_collection", "human_handoff_notice"}
            ),
            len(prepared),
        )
        prepared = [
            *prepared[:insert_at],
            *pending_media,
            *prepared[insert_at:],
        ]
    return _renumber(prepared), materialized_ids

def _passive_media_url(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, dict):
        return str(
            content.get("url")
            or content.get("image_url")
            or content.get("video_url")
            or ""
        ).strip()
    return str(content or "").strip()

def _maybe_append_planner_payment_structure(
    messages: list[dict[str, Any]],
    state: AgentState,
) -> list[dict[str, Any]]:
    if not messages or _messages_have_payment_collection(messages):
        return messages
    if not any(str(item.get("type") or "") == "text" for item in messages if isinstance(item, dict)):
        return messages
    if not _state_requires_payment_collection(state):
        return messages
    if is_hard_health_risk_hold(health_risk_hold(state)) or _state_has_paid_deposit_context(state):
        return messages
    context = payment_collection_context(state=state, messages=[])
    if context.get("over_limit"):
        return messages
    amount = int(context.get("amount") or 10)
    return _renumber(
        [
            *messages,
            {
                "type": "payment_collection",
                "content": payment_collection_content({"amount": amount}, state=state, messages=messages),
            },
        ]
    )

def _reply_recovery_messages(
    state: AgentState,
    *,
    primary_error: Exception | None = None,
) -> list[dict[str, Any]]:
    if state.get("evidence_join"):
        # Parallel Reply is the sole business brain. Its recovery must retain
        # the complete Gate candidates, conversation, tool facts, and layered
        # rules; generic deep compaction can turn a full SOP candidate into an
        # isolated card and materially change the business decision. The round
        # deadline still bounds this evidence-complete retry.
        payload = parallel_reply_payload(state)
        messages = build_parallel_reply_messages(payload, json_dumps=json_dumps)
        content_candidates = (
            state.get("evidence_join", {}).get("content_candidates")
            if isinstance(state.get("evidence_join"), dict)
            else []
        )
        if content_candidates and len(messages) > 1:
            messages = copy.deepcopy(messages)
            messages[1]["content"] += (
                "\n\n【异常恢复专用：完整候选证据】\n"
                + json_dumps(content_candidates)
            )
        if primary_error is not None:
            return _reply_retry_messages(messages, primary_error)
        return messages
    payload = _compact_recovery_value(reply_recovery_payload_for_model(state))
    return [
        {"role": "system", "content": REPLY_RECOVERY_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
    ]

def _reply_metadata_from_model_call(
    model_call: dict[str, Any] | None,
    *,
    state: AgentState | None = None,
) -> dict[str, Any]:
    if not isinstance(model_call, dict):
        return {}
    payload = model_call.get("validated_json_output")
    if not isinstance(payload, dict):
        return {}
    action = _reply_action_from_payload(payload)
    payload["action"] = action
    selected_content_ids = [
        str(item).strip() for item in payload.get("selected_content_ids") or [] if str(item).strip()
    ]
    return {
        "used_fact_refs": [str(item).strip() for item in payload.get("used_fact_refs") or [] if str(item).strip()],
        "selected_content_ids": selected_content_ids,
        "content_decisions": _normalized_content_decisions(payload.get("content_decisions")),
        "action": action,
        "action_reason": str(payload.get("action_reason") or "")[:500],
        "sales_judgment": _normalized_sales_judgment(payload.get("sales_judgment")),
        "knowledge_use": _normalized_follow_knowledge_use(
            payload.get("knowledge_use"),
            state=state,
            selected_content_ids=selected_content_ids,
        ),
        **_normalized_policy_decision(payload.get("policy_decision"), state=state),
        "payment_assessment": _normalized_payment_assessment(payload.get("payment_assessment")),
        "payment_channel": _normalized_payment_channel(payload.get("payment_assessment")),
        "deposit_evidence": _normalized_deposit_evidence(
            payload.get("deposit_evidence"),
            strict=False,
        ),
        "safety_assessment": _normalized_safety_assessment(payload.get("safety_assessment")),
        "party_size_assessment": _normalized_party_size_assessment(payload.get("party_size_assessment")),
        "commit_actions": [item for item in payload.get("commit_actions") or [] if isinstance(item, dict)],
    }

def _reply_validation_state(state: AgentState, payload: dict[str, Any]) -> AgentState:
    if not state.get("evidence_join"):
        return state
    validation_state: AgentState = dict(state)
    safety = _normalized_safety_assessment(payload.get("safety_assessment"))
    party_size = _normalized_party_size_assessment(payload.get("party_size_assessment"))
    valid_refs = _customer_message_refs(state)
    # Assessments are Reply's ephemeral business judgement. Invalid citation
    # metadata is discarded instead of rejecting an otherwise valid visible
    # answer. Deterministic payment/store/write boundaries below still use
    # authoritative facts and actual structured messages.
    safety["evidence_refs"] = _valid_customer_refs(safety.get("evidence_refs"), valid_refs)
    if safety.get("status") != "none" and not safety["evidence_refs"]:
        # A model label without a real customer citation is not an
        # authoritative safety fact and must not block an external action.
        safety = {"status": "none", "evidence_refs": []}
    party_size["evidence_refs"] = _valid_customer_refs(party_size.get("evidence_refs"), valid_refs)
    validation_state["reply_safety_assessment"] = safety
    validation_state["reply_party_size_assessment"] = party_size
    validation_state["reply_sales_judgment"] = _normalized_sales_judgment(payload.get("sales_judgment"))
    payment = _normalized_payment_assessment(payload.get("payment_assessment"))
    if str(payment.get("status") or "") == "authoritative_paid":
        if not _parallel_paid_deposit_context(state):
            # This field is explanatory model metadata, not a second source of
            # truth. Ignore an unsupported label instead of rejecting an
            # otherwise valid customer-visible reply. Actual paid-only writes
            # and payment-card boundaries still use authoritative state.
            payment = {"status": "unknown", "evidence_refs": []}
        else:
            payment["evidence_refs"] = ["payment_fact:authoritative_paid"]
    else:
        payment["evidence_refs"] = _valid_customer_refs(payment.get("evidence_refs"), valid_refs)
    validation_state["reply_payment_assessment"] = payment
    validation_state["reply_payment_channel"] = _normalized_payment_channel(
        payload.get("payment_assessment")
    )
    validation_state["reply_payment_channel_explicit"] = bool(
        isinstance(payload.get("payment_assessment"), dict)
        and "payment_channel" in payload["payment_assessment"]
    )
    action = _reply_action_from_payload(payload)
    payload["action"] = action
    validation_state["reply_action"] = action
    validation_state["reply_deposit_evidence"] = _normalized_deposit_evidence(
        payload.get("deposit_evidence"),
        strict=False,
    )
    validation_state["reply_selected_content_ids"] = [
        str(item).strip() for item in payload.get("selected_content_ids") or [] if str(item).strip()
    ]
    validation_state["reply_content_decisions"] = _normalized_content_decisions(
        payload.get("content_decisions")
    )
    reply_payload = parallel_reply_payload(state)
    validation_state["reply_payment_channel_availability"] = (
        reply_payload.get("payment_channel_availability")
        if isinstance(reply_payload.get("payment_channel_availability"), dict)
        else {}
    )
    delivery_options = (
        reply_payload.get("structured_delivery_options")
        if isinstance(reply_payload.get("structured_delivery_options"), dict)
        else {}
    )
    reply_used_fact_refs = [
        str(item).strip() for item in payload.get("used_fact_refs") or [] if str(item).strip()
    ]
    valid_fact_refs = _reply_reference_set(reply_payload)
    for option in delivery_options.values():
        if isinstance(option, dict):
            fact_ref = str(option.get("fact_ref") or "").strip()
            if fact_ref:
                valid_fact_refs.add(fact_ref)
    valid_fact_refs.add("current_message")
    validation_state["reply_used_fact_refs"] = [
        ref for ref in reply_used_fact_refs if ref in valid_fact_refs
    ]
    return validation_state

def _normalized_reply_action(value: Any) -> str:
    action = str(value or "none").strip()
    if action == "answer":
        # `answer` is a common accidental copy of sales_judgment.posture.
        # Mapping it to the no-structure action is enum compatibility only;
        # it does not alter customer text or choose a sales move.
        return "none"
    if action not in {"none", "ask", "offer", "payment", "registration"}:
        return "none"
    return action

def _reply_action_from_payload(payload: dict[str, Any]) -> str:
    """Derive the legacy action enum from explicit output structures.

    V3 no longer asks Reply to duplicate its customer-visible decision in an
    additional action field.  Keep accepting that field from older retries and
    fixtures, but prefer objective structures when it is omitted.  This is
    schema compatibility only and never changes the visible reply.
    """

    raw_action = str(payload.get("action") or "").strip()
    if raw_action:
        return _normalized_reply_action(raw_action)
    messages = payload.get("reply_messages")
    if isinstance(messages, list) and any(
        isinstance(item, dict)
        and str(item.get("type") or "").strip() == "payment_collection"
        for item in messages
    ):
        return "payment"
    commit_actions = payload.get("commit_actions")
    if isinstance(commit_actions, list) and any(isinstance(item, dict) for item in commit_actions):
        return "registration"
    return "none"

def _normalized_content_decisions(value: Any) -> list[dict[str, str]]:
    """Keep model-owned candidate reasoning as audit metadata only."""

    if not isinstance(value, list):
        return []
    allowed_decisions = {"adopt", "skip"}
    allowed_reasons = {
        "directly_useful",
        "already_delivered",
        "irrelevant",
        "conflicting",
    }
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        content_id = str(item.get("content_id") or "").strip()
        decision = str(item.get("decision") or "").strip().lower()
        reason = str(item.get("reason") or "").strip().lower()
        if not content_id or content_id in seen or decision not in allowed_decisions:
            continue
        if reason not in allowed_reasons:
            reason = ""
        normalized.append(
            {
                "content_id": content_id,
                "decision": decision,
                "reason": reason,
            }
        )
        seen.add(content_id)
    return normalized

def _normalized_follow_knowledge_use(
    value: Any,
    *,
    state: AgentState | None,
    selected_content_ids: list[str],
) -> dict[str, Any]:
    """Normalize knowledge provenance without interpreting customer semantics."""

    raw = value if isinstance(value, dict) else {}
    recall = state.get("sales_recall") if isinstance(state, dict) and isinstance(state.get("sales_recall"), dict) else {}
    sequences = {
        str(item.get("sequence_id") or "").strip(): item
        for item in recall.get("sequence_candidates") or []
        if isinstance(item, dict) and str(item.get("sequence_id") or "").strip()
    }
    scripts: dict[str, dict[str, Any]] = {}
    for item in recall.get("candidates") or []:
        if not isinstance(item, dict):
            continue
        for key in ("script_id", "id", "source_id", "script_code"):
            script_key = str(item.get(key) or "").strip()
            if script_key:
                scripts[script_key] = item
    selected_script_ids: list[str] = []
    for content_id in selected_content_ids:
        if not content_id.startswith("follow_script:"):
            continue
        source_id = content_id.split(":", 1)[1].rsplit(":p", 1)[0]
        if source_id in scripts:
            selected_script = scripts[source_id]
            canonical_script_id = str(
                selected_script.get("script_id")
                or selected_script.get("id")
                or selected_script.get("source_id")
                or selected_script.get("script_code")
                or source_id
            ).strip()
            if canonical_script_id and canonical_script_id not in selected_script_ids:
                selected_script_ids.append(canonical_script_id)
    primary_script_id = str(raw.get("script_id") or "").strip()
    if primary_script_id.startswith("follow_script:"):
        primary_script_id = primary_script_id.split(":", 1)[1].rsplit(":p", 1)[0]
    if primary_script_id in scripts:
        primary_script = scripts[primary_script_id]
        canonical_script_id = str(
            primary_script.get("script_id")
            or primary_script.get("id")
            or primary_script.get("source_id")
            or primary_script.get("script_code")
            or primary_script_id
        ).strip()
        if canonical_script_id and canonical_script_id not in selected_script_ids:
            selected_script_ids.append(canonical_script_id)
    sequence_id = str(raw.get("sequence_id") or "").strip()
    step_id = str(raw.get("step_id") or "").strip()
    if sequence_id not in sequences:
        sequence_id = ""
        step_id = ""
    valid_steps = {
        str(step.get("step_id") or "").strip(): step
        for step in (sequences.get(sequence_id) or {}).get("steps") or []
        if isinstance(step, dict) and str(step.get("step_id") or "").strip()
    }
    if step_id not in valid_steps:
        step_id = ""

    compatible_links: list[dict[str, Any]] = []
    for script_id in selected_script_ids:
        for link in (scripts.get(script_id) or {}).get("sequence_links") or []:
            if not isinstance(link, dict):
                continue
            linked_sequence = str(link.get("sequence_id") or "").strip()
            linked_step = str(link.get("step_id") or "").strip()
            if linked_sequence in sequences and linked_step:
                compatible_links.append(link)
    unique_links = {
        (
            str(link.get("sequence_id") or "").strip(),
            str(link.get("step_id") or "").strip(),
        )
        for link in compatible_links
    }
    if not sequence_id and len(unique_links) == 1:
        sequence_id, step_id = next(iter(unique_links))
        valid_steps = {
            str(step.get("step_id") or "").strip(): step
            for step in (sequences.get(sequence_id) or {}).get("steps") or []
            if isinstance(step, dict) and str(step.get("step_id") or "").strip()
        }
    elif sequence_id and not step_id:
        linked_steps = {
            linked_step
            for linked_sequence, linked_step in unique_links
            if linked_sequence == sequence_id
        }
        if len(linked_steps) == 1:
            step_id = next(iter(linked_steps))

    sequence = sequences.get(sequence_id) or {}
    step = valid_steps.get(step_id) or {}
    return {
        "sequence_id": sequence_id,
        "sequence_name": str(sequence.get("sequence_name") or "").strip(),
        "step_id": step_id,
        "checkpoint_code": str(sequence.get("checkpoint_code") or "").strip(),
        "action_code": str(step.get("action_code") or "").strip(),
        "selected_script_ids": list(dict.fromkeys(selected_script_ids)),
        "reason": str(raw.get("reason") or "").strip()[:500],
        "authority": "reply_selected_reference_not_customer_fact",
    }

def _legacy_normalized_structured_delivery_decisions(value: Any) -> list[dict[str, str]]:
    """Normalize the old V1 repair payload without entering the V2 contract."""

    if not isinstance(value, list):
        return []
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        fact_ref = str(item.get("fact_ref") or "").strip()
        decision = str(item.get("decision") or "").strip()
        reason = str(item.get("reason") or "").strip()
        if not fact_ref or decision not in {"deliver", "defer"} or fact_ref in seen:
            continue
        if decision == "defer" and not reason:
            continue
        seen.add(fact_ref)
        normalized.append(
            {"fact_ref": fact_ref, "decision": decision, "reason": reason[:500]}
        )
    return normalized

def _normalized_safety_assessment(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    status = str(raw.get("status") or "none").strip()
    if status not in {"none", "health_risk", "complaint_refund", "explicit_reject"}:
        status = "none"
    return {
        "status": status,
        "evidence_refs": _normalized_evidence_refs(raw.get("evidence_refs")),
    }

def _normalized_sales_judgment(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    posture = str(raw.get("posture") or "answer").strip()
    if posture not in {"answer", "advance", "switch", "pause", "close"}:
        posture = "answer"
    return {
        "customer_goal": str(raw.get("customer_goal") or "")[:500],
        "primary_objective": str(raw.get("primary_objective") or "")[:500],
        "customer_friction_observation": str(
            raw.get("customer_friction_observation") or ""
        )[:500],
        "posture": posture,
        "reason": str(raw.get("reason") or "")[:500],
    }

def _normalized_policy_decision(
    value: Any,
    *,
    state: AgentState | None,
) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    runtime_state = state or {}
    policy = runtime_state.get("ai_sales_policy") if isinstance(runtime_state.get("ai_sales_policy"), dict) else {}
    if str(policy.get("runtime_mode") or "off") == "off":
        return {
            "policy_decision": {},
            "decision_status": "",
            "decision_reasons": [],
            "primary_task": {},
            "secondary_tasks": [],
            "realtime_intent": {},
            "emotion_decision": {},
            "closing_decision": {},
            "cardpoint_decision": {},
        }

    degraded_reasons: list[str] = []

    def degrade(reason: str) -> None:
        if reason not in degraded_reasons:
            degraded_reasons.append(reason)

    def checked_enum(candidate: Any, allowed: set[str], default: str, *, field: str) -> str:
        normalized = str(candidate or "").strip()
        if not normalized:
            degrade(f"missing_{field}")
            return default
        if normalized not in allowed:
            degrade(f"invalid_{field}")
            return default
        return normalized

    if not isinstance(value, dict):
        degrade("missing_policy_decision")

    routing = policy.get("routing") if isinstance(policy.get("routing"), dict) else {}
    allowed_tasks = {
        str(item.get("key") or "").strip()
        for collection in (routing.get("fixed_priority") or [], routing.get("business_tasks") or [])
        for item in collection
        if isinstance(item, dict) and str(item.get("key") or "").strip()
    }

    def task(candidate: Any, *, field: str) -> dict[str, Any]:
        item = candidate if isinstance(candidate, dict) else {}
        task_type = str(item.get("type") or "").strip()
        if task_type not in allowed_tasks:
            if candidate not in (None, {}, ""):
                degrade(f"invalid_{field}")
            return {}
        if not str(item.get("goal") or "").strip():
            degrade(f"missing_{field}_goal")
        return {
            "type": task_type,
            "goal": str(item.get("goal") or "").strip()[:500],
            "basis": _policy_string_list(item.get("basis"), limit=6),
        }

    primary_task = task(raw.get("primary_task"), field="primary_task")
    if not primary_task:
        degrade("missing_primary_task")
    secondary_candidates = raw.get("secondary_tasks")
    if secondary_candidates is not None and not isinstance(secondary_candidates, list):
        degrade("invalid_secondary_tasks")
    elif isinstance(secondary_candidates, list) and len(secondary_candidates) > 3:
        degrade("too_many_secondary_tasks")
    secondary_tasks: list[dict[str, Any]] = []
    seen_secondary_tasks: set[str] = set()
    for item in secondary_candidates if isinstance(secondary_candidates, list) else []:
        normalized = task(item, field="secondary_task")
        task_type = str(normalized.get("type") or "")
        if not normalized or task_type == primary_task.get("type") or task_type in seen_secondary_tasks:
            if normalized:
                degrade("duplicate_secondary_task")
            continue
        seen_secondary_tasks.add(task_type)
        secondary_tasks.append(normalized)
        if len(secondary_tasks) == 3:
            break

    intent_policy = policy.get("intent") if isinstance(policy.get("intent"), dict) else {}
    allowed_intents = {
        str(item.get("key") or "").strip()
        for item in intent_policy.get("realtime_intents") or []
        if isinstance(item, dict) and str(item.get("key") or "").strip()
    }
    intent_raw = raw.get("realtime_intent") if isinstance(raw.get("realtime_intent"), dict) else {}
    intent_type = str(intent_raw.get("type") or "").strip()
    if not isinstance(raw.get("realtime_intent"), dict):
        degrade("missing_realtime_intent")
    elif intent_type not in allowed_intents:
        degrade("invalid_realtime_intent")
    secondary_intents_raw = intent_raw.get("secondary_types")
    if secondary_intents_raw is not None and not isinstance(secondary_intents_raw, list):
        degrade("invalid_secondary_intents")
    elif isinstance(secondary_intents_raw, list) and len(secondary_intents_raw) > 3:
        degrade("too_many_secondary_intents")
    secondary_types: list[str] = []
    for candidate in secondary_intents_raw if isinstance(secondary_intents_raw, list) else []:
        candidate_type = str(candidate or "").strip()
        if candidate_type not in allowed_intents:
            degrade("invalid_secondary_intent")
            continue
        if candidate_type == intent_type or candidate_type in secondary_types:
            continue
        secondary_types.append(candidate_type)
        if len(secondary_types) == 3:
            break
    valid_customer_refs = _customer_message_refs(runtime_state)
    realtime_intent = (
        {
            "type": intent_type,
            "secondary_types": secondary_types,
            "confidence": checked_enum(
                intent_raw.get("confidence"),
                {"high", "medium", "low"},
                "low",
                field="intent_confidence",
            ),
            "evidence_refs": _valid_customer_refs(intent_raw.get("evidence_refs"), valid_customer_refs)[:6],
            "basis": _policy_string_list(intent_raw.get("basis"), limit=6),
        }
        if intent_type in allowed_intents
        else {}
    )

    emotion_policy = policy.get("emotion") if isinstance(policy.get("emotion"), dict) else {}
    emotions = {
        str(item.get("key") or "").strip(): item
        for item in emotion_policy.get("labels") or []
        if isinstance(item, dict) and str(item.get("key") or "").strip()
    }
    emotion_raw = raw.get("emotion_decision") if isinstance(raw.get("emotion_decision"), dict) else {}
    emotion_label = str(emotion_raw.get("label") or "").strip()
    emotion_definition = emotions.get(emotion_label) or {}
    if not isinstance(raw.get("emotion_decision"), dict):
        degrade("missing_emotion_decision")
    elif not emotion_definition:
        degrade("invalid_emotion_label")
    emotion_decision = (
        {
            "label": emotion_label,
            "confidence": checked_enum(
                emotion_raw.get("confidence"),
                {"high", "medium", "low"},
                "low",
                field="emotion_confidence",
            ),
            "pressure": checked_enum(
                emotion_raw.get("pressure"),
                {"normal", "low", "none"},
                "normal",
                field="emotion_pressure",
            ),
            "flow_action": str(emotion_definition.get("flow_action") or "keep"),
            "evidence_refs": _valid_customer_refs(emotion_raw.get("evidence_refs"), valid_customer_refs)[:6],
            "basis": _policy_string_list(emotion_raw.get("basis"), limit=6),
        }
        if emotion_definition
        else {}
    )

    closing_policy = policy.get("closing") if isinstance(policy.get("closing"), dict) else {}
    sequences = {
        str(item.get("sequence_key") or "").strip(): item
        for item in closing_policy.get("sequences") or []
        if isinstance(item, dict)
        and item.get("enabled")
        and str(item.get("sequence_key") or "").strip()
    }
    closing_raw = raw.get("closing_decision") if isinstance(raw.get("closing_decision"), dict) else {}
    if not isinstance(raw.get("closing_decision"), dict):
        degrade("missing_closing_decision")
    action = checked_enum(
        closing_raw.get("action"),
        {"none", "enter", "advance", "pause", "fallback", "complete"},
        "none",
        field="closing_action",
    )
    sequence_key = str(closing_raw.get("sequence_key") or "none").strip() or "none"
    sequence = sequences.get(sequence_key)
    if action == "none":
        sequence_key = "none"
        sequence = None
    elif sequence is None:
        degrade("invalid_closing_sequence")
        action = "pause" if action in {"enter", "advance", "fallback", "pause"} else "none"
        sequence_key = "none"
    node_key = str(closing_raw.get("node_key") or "").strip()
    allowed_nodes = {
        str(item.get("node_key") or "").strip()
        for item in (sequence or {}).get("nodes") or []
        if isinstance(item, dict) and str(item.get("node_key") or "").strip()
    }
    if node_key not in allowed_nodes:
        if node_key:
            degrade("invalid_closing_node")
        node_key = ""
    if action in {"enter", "advance"} and not node_key:
        degrade("closing_advance_requires_valid_node")
        action = "pause"
    closing_decision = {
        "action": action,
        "sequence_key": sequence_key,
        "node_key": node_key,
        "trigger": checked_enum(
            closing_raw.get("trigger"),
            {"explicit_transaction", "blocker_resolved", "positive_progress", "silent_due", "none"},
            "none",
            field="closing_trigger",
        ),
        "customer_state": checked_enum(
            closing_raw.get("customer_state"),
            {
                "engaged", "hesitant", "soft_reject", "not_buying_now", "hard_stop",
                "new_blocker", "transaction_terminal_or_handoff", "none",
            },
            "none",
            field="closing_customer_state",
        ),
        "pressure": checked_enum(
            closing_raw.get("pressure"),
            {"normal", "low", "none"},
            "none",
            field="closing_pressure",
        ),
        "evidence_refs": _valid_customer_refs(closing_raw.get("evidence_refs"), valid_customer_refs)[:6],
        "basis": _policy_string_list(closing_raw.get("basis"), limit=6),
    }

    if realtime_intent.get("type") == "defer":
        if closing_decision["action"] in {"enter", "advance"}:
            closing_decision["action"] = "pause"
            closing_decision["node_key"] = ""
            degrade("defer_cannot_advance_closing")
        if closing_decision["pressure"] == "normal":
            closing_decision["pressure"] = "low"
            degrade("defer_requires_lower_pressure")
    if closing_decision["customer_state"] == "new_blocker":
        if closing_decision["action"] != "pause":
            closing_decision["action"] = "pause"
            closing_decision["node_key"] = ""
            degrade("new_blocker_requires_pause")
        if closing_decision["pressure"] == "normal":
            closing_decision["pressure"] = "low"
            degrade("new_blocker_requires_lower_pressure")
    if closing_decision["customer_state"] in {
        "not_buying_now",
        "transaction_terminal_or_handoff",
    }:
        if closing_decision["action"] != "complete":
            closing_decision["action"] = "complete"
            closing_decision["node_key"] = ""
            degrade("terminal_customer_state_requires_complete")
        if closing_decision["pressure"] != "none":
            closing_decision["pressure"] = "none"
            degrade("terminal_customer_state_requires_no_pressure")
    if emotion_decision.get("flow_action") == "lower_pressure":
        if emotion_decision.get("pressure") == "normal":
            emotion_decision["pressure"] = "low"
            degrade("emotion_requires_lower_pressure")
        if closing_decision.get("pressure") == "normal":
            closing_decision["pressure"] = "low"
            degrade("emotion_requires_lower_closing_pressure")
    elif emotion_decision.get("flow_action") in {"pause_marketing_turn", "handoff_by_system_rule"}:
        if emotion_decision.get("pressure") != "none":
            emotion_decision["pressure"] = "none"
            degrade("emotion_requires_no_pressure")
        if closing_decision["action"] in {"enter", "advance", "fallback"}:
            closing_decision["action"] = "pause"
            closing_decision["node_key"] = ""
            degrade("emotion_cannot_advance_closing")

    catalog = runtime_state.get("sales_strategy_catalog") if isinstance(runtime_state.get("sales_strategy_catalog"), dict) else {}
    category_keys = {
        str(item.get("category_key") or "").strip()
        for item in catalog.get("categories") or []
        if isinstance(item, dict) and str(item.get("category_key") or "").strip()
    }
    tactic_tags = {str(item).strip() for item in catalog.get("tactic_tags") or [] if str(item).strip()}
    card_raw = raw.get("cardpoint_decision") if isinstance(raw.get("cardpoint_decision"), dict) else {}
    category_key = str(card_raw.get("category_key") or "").strip()
    card_state = _policy_enum(card_raw.get("state"), {"active", "resolved", "repeated", "none"}, "none")
    cardpoint_decision = (
        {
            "category_key": category_key,
            "scenario_query": str(card_raw.get("scenario_query") or "").strip()[:300],
            "tactic_tags": [item for item in _policy_string_list(card_raw.get("tactic_tags"), limit=4) if item in tactic_tags],
            "state": card_state,
            "confidence": _policy_enum(card_raw.get("confidence"), {"high", "medium", "low"}, "low"),
            "basis": _policy_string_list(card_raw.get("basis"), limit=6),
        }
        if category_key in category_keys and card_state != "none"
        else {}
    )

    if cardpoint_decision.get("state") in {"active", "repeated"}:
        if closing_decision["action"] != "pause":
            closing_decision["action"] = "pause"
            closing_decision["node_key"] = ""
            degrade("active_cardpoint_requires_pause")
        if closing_decision["pressure"] == "normal":
            closing_decision["pressure"] = "low"
            degrade("active_cardpoint_requires_lower_pressure")

    if realtime_intent.get("type") == "explicit_exit":
        if primary_task.get("type") != "hard_stop":
            degrade("explicit_exit_requires_hard_stop")
        if closing_decision.get("action") != "complete" or closing_decision.get("customer_state") != "hard_stop":
            degrade("explicit_exit_requires_complete")
        primary_task = task(
            {"type": "hard_stop", "goal": "停止自动营销", "basis": realtime_intent.get("basis") or []},
            field="primary_task",
        )
        secondary_tasks = []
        closing_decision.update(
            {
                "action": "complete",
                "node_key": "",
                "trigger": "none",
                "customer_state": "hard_stop",
                "pressure": "none",
            }
        )
    normalized_decision = {
        "primary_task": primary_task,
        "secondary_tasks": secondary_tasks,
        "realtime_intent": realtime_intent,
        "emotion_decision": emotion_decision,
        "closing_decision": closing_decision,
        "cardpoint_decision": cardpoint_decision,
    }
    return {
        "policy_decision": normalized_decision,
        "decision_status": "degraded" if degraded_reasons else "ok",
        "decision_reasons": degraded_reasons,
        **normalized_decision,
    }


def _validate_policy_reply_consistency(payload: dict[str, Any], state: AgentState) -> None:
    """Reject model outputs whose structures contradict their own safety decision.

    This does not infer intent from customer text.  It only checks that Reply's
    structured customer action agrees with Reply's structured policy decision,
    so the existing single repair can correct a self-contradictory output.
    """

    normalized = _normalized_policy_decision(payload.get("policy_decision"), state=state)
    decision = normalized.get("policy_decision")
    structural_reasons = [
        str(reason)
        for reason in normalized.get("decision_reasons") or []
        if str(reason).startswith(("missing_", "invalid_", "too_many_", "duplicate_"))
    ]
    if structural_reasons:
        raise ValueError(
            "policy_decision_schema_invalid:" + ",".join(structural_reasons)
        )
    if not isinstance(decision, dict) or not decision:
        return
    intent = decision.get("realtime_intent") if isinstance(decision.get("realtime_intent"), dict) else {}
    emotion = decision.get("emotion_decision") if isinstance(decision.get("emotion_decision"), dict) else {}
    cardpoint = decision.get("cardpoint_decision") if isinstance(decision.get("cardpoint_decision"), dict) else {}
    cardpoint = decision.get("cardpoint_decision") if isinstance(decision.get("cardpoint_decision"), dict) else {}
    explicit_exit = str(intent.get("type") or "") == "explicit_exit"
    pause_marketing = str(emotion.get("flow_action") or "") in {
        "pause_marketing_turn",
        "handoff_by_system_rule",
    }
    closing = decision.get("closing_decision") if isinstance(decision.get("closing_decision"), dict) else {}
    active_cardpoint = (
        str(cardpoint.get("state") or "") in {"active", "repeated"}
        or str(closing.get("customer_state") or "") == "new_blocker"
    )
    if not explicit_exit and not pause_marketing and not active_cardpoint:
        return

    sales = _normalized_sales_judgment(payload.get("sales_judgment"))
    reply_action = _reply_action_from_payload(payload)
    messages = payload.get("reply_messages") if isinstance(payload.get("reply_messages"), list) else []
    structured_sales_types = {
        str(item.get("type") or "").strip()
        for item in messages
        if isinstance(item, dict)
    } & {"payment_collection", "store_address", "image", "video"}
    commit_actions = [item for item in payload.get("commit_actions") or [] if isinstance(item, dict)]

    conflicts: list[str] = []
    if sales.get("posture") in {"advance", "switch"}:
        conflicts.append("sales_posture")
    allowed_actions = (
        {"none"}
        if explicit_exit
        else {"none", "ask"}
        if pause_marketing
        else {"none", "ask", "offer"}
    )
    if reply_action not in allowed_actions:
        conflicts.append("reply_action")
    if structured_sales_types and (
        explicit_exit or pause_marketing or "payment_collection" in structured_sales_types
    ):
        conflicts.append("structured_sales_message")
    if commit_actions:
        conflicts.append("commit_actions")
    if conflicts:
        reason = (
            "explicit_exit"
            if explicit_exit
            else "pause_marketing"
            if pause_marketing
            else "active_cardpoint"
        )
        raise ValueError(
            f"policy_decision_{reason}_conflict:" + ",".join(conflicts)
        )


def _policy_safety_floor(payload: dict[str, Any], state: AgentState) -> str:
    """Capture only grounded, model-declared safety state for a repair attempt."""

    normalized = _normalized_policy_decision(payload.get("policy_decision"), state=state)
    decision = normalized.get("policy_decision")
    if not isinstance(decision, dict):
        return ""
    intent = decision.get("realtime_intent") if isinstance(decision.get("realtime_intent"), dict) else {}
    emotion = decision.get("emotion_decision") if isinstance(decision.get("emotion_decision"), dict) else {}
    cardpoint = decision.get("cardpoint_decision") if isinstance(decision.get("cardpoint_decision"), dict) else {}
    if str(intent.get("type") or "") == "explicit_exit" and intent.get("evidence_refs"):
        return "explicit_exit"
    if (
        str(emotion.get("flow_action") or "")
        in {"pause_marketing_turn", "handoff_by_system_rule"}
        and emotion.get("evidence_refs")
    ):
        return "pause_marketing"
    if str(cardpoint.get("state") or "") in {"active", "repeated"}:
        category_key = str(cardpoint.get("category_key") or "").strip()
        if category_key:
            return f"active_cardpoint:{category_key}"
    return ""


def _validate_policy_safety_floor(
    payload: dict[str, Any],
    state: AgentState,
    safety_floor: str,
) -> None:
    if not safety_floor:
        return
    current_floor = _policy_safety_floor(payload, state)
    if safety_floor == "explicit_exit" and current_floor != "explicit_exit":
        raise ValueError("policy_safety_floor_removed:explicit_exit")
    if safety_floor == "pause_marketing" and current_floor not in {
        "pause_marketing",
        "explicit_exit",
    }:
        raise ValueError("policy_safety_floor_removed:pause_marketing")
    if safety_floor.startswith("active_cardpoint:") and current_floor != safety_floor:
        raise ValueError("policy_safety_floor_removed:active_cardpoint")

def _policy_string_list(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip()[:500] for item in value if str(item or "").strip()][:limit]

def _policy_enum(value: Any, allowed: set[str], default: str) -> str:
    normalized = str(value or "").strip()
    return normalized if normalized in allowed else default

def _normalized_deposit_evidence(value: Any, *, strict: bool = True) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    supporting_key = str(raw.get("supporting_key") or "").strip()
    supporting_key = {"none": ""}.get(supporting_key, supporting_key)
    if strict and supporting_key not in {"", "address", "effect", "objection"}:
        raise ValueError("invalid_reply_deposit_supporting_key")
    return {
        "offer_prior_turn_refs": _normalized_evidence_refs(raw.get("offer_prior_turn_refs")),
        "supporting_key": supporting_key,
        "supporting_refs": _normalized_evidence_refs(raw.get("supporting_refs")),
        "current_intent_refs": _normalized_evidence_refs(raw.get("current_intent_refs")),
    }

def _normalized_payment_assessment(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    status = str(raw.get("status") or "unknown").strip()
    status = {
        "unverified_oral_paid_claim": "unverified_paid_claim",
    }.get(status, status)
    if status not in {
        "unknown",
        "none",
        "manual_transfer",
        "unverified_paid_claim",
        "payment_request",
        "authoritative_paid",
    }:
        status = "unknown"
    return {
        "status": status,
        "evidence_refs": _normalized_evidence_refs(raw.get("evidence_refs")),
    }

def _normalized_payment_channel(value: Any) -> str:
    raw = value if isinstance(value, dict) else {}
    status = str(raw.get("status") or "unknown").strip()
    channel = str(raw.get("payment_channel") or "").strip()
    if not channel:
        # Backward-compatible enum normalization only. The existing status has
        # already been chosen by Reply; this does not infer customer intent.
        channel = {
            "payment_request": "payment_card",
            "manual_transfer": "transfer",
        }.get(status, "none")
    if channel not in {"none", "payment_card", "transfer", "red_packet"}:
        return "none"
    return channel

def _normalized_party_size_assessment(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    status = str(raw.get("status") or "unknown").strip()
    if status == "none":
        # `none` and `unknown` both mean that Reply found no party-size fact.
        # This is enum compatibility only and cannot create or change a count.
        status = "unknown"
    if status not in {"unknown", "known", "over_limit"}:
        status = "unknown"
    party_size: int | None = None
    if status in {"known", "over_limit"}:
        try:
            party_size = int(raw.get("party_size"))
        except (TypeError, ValueError):
            status = "unknown"
            party_size = None
        if status == "known" and party_size is not None and not 1 <= party_size <= 4:
            status = "unknown"
            party_size = None
        if status == "over_limit" and party_size is not None and party_size <= 4:
            status = "unknown"
            party_size = None
    return {
        "status": status,
        "party_size": party_size,
        "evidence_refs": _normalized_evidence_refs(raw.get("evidence_refs")),
    }

def _normalized_evidence_refs(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))

def _customer_message_refs(state: AgentState) -> set[str]:
    shared = state.get("shared_context") if isinstance(state.get("shared_context"), dict) else {}
    refs: set[str] = set()
    for item in shared.get("conversation") or []:
        if not isinstance(item, dict) or str(item.get("role") or "").strip().lower() not in {"customer", "user"}:
            continue
        ref = str(item.get("message_ref") or "").strip()
        if ref:
            refs.add(ref)
    refs.add("current_message")
    return refs

def _canonical_assessment_refs(value: Any, valid_refs: set[str]) -> list[str]:
    """Normalize unambiguous reference notation without inferring semantics."""

    aliases = {
        "current_message.content": "current_message",
        "shared_context.current_message": "current_message",
        "shared_context.current_message.content": "current_message",
        "evidence.shared_context.current_message": "current_message",
        "evidence.shared_context.current_message.content": "current_message",
    }
    output: list[str] = []
    for raw in _normalized_evidence_refs(value):
        ref = aliases.get(raw, raw)
        for prefix in ("conversation:", "shared_context.conversation:", "evidence.shared_context.conversation:"):
            if ref.startswith(prefix):
                candidate = ref[len(prefix) :]
                if candidate in valid_refs:
                    ref = candidate
                break
        if ref not in output:
            output.append(ref)
    return output

def _valid_customer_refs(value: Any, valid_refs: set[str]) -> list[str]:
    return [
        ref
        for ref in _canonical_assessment_refs(value, valid_refs)
        if ref in valid_refs
    ]

def _compact_recovery_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 4:
        return str(value)[:240]
    if isinstance(value, dict):
        return {
            str(key): _compact_recovery_value(item, depth=depth + 1)
            for key, item in list(value.items())[:24]
            if item not in (None, "", [], {})
        }
    if isinstance(value, list):
        return [_compact_recovery_value(item, depth=depth + 1) for item in value[-12:]]
    if isinstance(value, str):
        return value[:600]
    return value

async def _chat_json_with_deadline(
    model_client: ModelClient,
    messages: list[dict[str, Any]],
    *,
    tier: str,
    deadline_monotonic: float,
) -> dict[str, Any]:
    try:
        return await model_client.chat_json(
            messages,
            tier=tier,
            temperature=0.0,
            deadline_monotonic=deadline_monotonic,
        )
    except TypeError as exc:
        if "deadline_monotonic" not in str(exc) and "temperature" not in str(exc):
            raise
        return await model_client.chat_json(messages, tier=tier)

def _model_budget_seconds(model_client: ModelClient, name: str, default: float) -> float:
    settings = getattr(model_client, "settings", None)
    value = getattr(settings, name, default) if settings is not None else default
    try:
        return max(0.1, float(value))
    except (TypeError, ValueError):
        return default

def _capped_deadline(node_deadline: float, round_deadline: float | None) -> float:
    return min(node_deadline, round_deadline) if round_deadline is not None else node_deadline

def _reply_full_task_retry_messages(
    messages: list[dict[str, Any]],
    exc: Exception,
) -> list[dict[str, Any]]:
    """Retry the original Reply task when no candidate JSON was produced."""

    retry_instruction = (
        "上一次调用没有返回任何可校验的 json 对象，"
        f"失败类型为 {type(exc).__name__}。"
        "请基于以上完整聊天、权威事实、工具事实和内容候选，重新执行原始 Reply 任务。"
        "这不是对某个旧答案的局部结构修复：请重新完成完整业务判断，并严格遵守原输出合同。"
        "不要降级成占位回复，不要凭空补事实，也不要输出 markdown 或解释错误；"
        "只输出一个完整、合法的严格 json 对象。"
    )
    return [*copy.deepcopy(messages), {"role": "user", "content": retry_instruction}]

def _reply_retry_messages(
    messages: list[dict[str, Any]],
    exc: Exception,
    *,
    previous_payload: dict[str, Any] | None = None,
    validation_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if "structured_delivery_requires_text_message" in str(exc):
        previous_output = (
            [{"role": "assistant", "content": json.dumps(previous_payload, ensure_ascii=False, separators=(",", ":"))}]
            if isinstance(previous_payload, dict)
            else []
        )
        return [
            *copy.deepcopy(messages),
            *previous_output,
            {
                "role": "user",
                "content": (
                    "上一版只返回了卡片、图片或视频，缺少客户能读懂的完整微信对话，"
                    "没有满足原始 Reply 输出合同。请基于上面的完整聊天和证据重新执行一次完整 Reply 任务："
                    "保留仍然正确的真实结构消息，由你重新决定自然表达和本轮唯一相邻销售动作；"
                    "不要只给结构消息，不要机械添加空泛包装句，不要跨过未建立的决策基础。"
                    "只输出修复后的完整严格 JSON，不解释错误。"
                ),
            },
        ]
    if isinstance(validation_context, dict) and validation_context.get("schema_version") == "parallel_reply_repair_context_v2":
        return _parallel_generic_reply_repair_messages(
            messages,
            exc,
            previous_payload=previous_payload,
            validation_context=validation_context,
        )
    repair_hint = _reply_repair_hint(str(exc))
    payment_repair_guard = _reply_payment_repair_guard(previous_payload)
    structural_repair_guard = _reply_structural_repair_guard(
        str(exc),
        previous_payload=previous_payload,
        validation_context=validation_context,
    )
    context_hint = ""
    if validation_context:
        context_hint = (
            "本轮可用的结构校验引用如下，必须逐字使用，不能编造："
            f"{json_dumps(validation_context)}。"
        )
    if structural_repair_guard:
        retry_instruction = (
            "只执行下面最高优先级结构清单，并输出修复后的完整 JSON："
            f"{structural_repair_guard}"
            "上一版业务判断已经完成，本次只修复校验器明确指出的结构或事实表达错误。"
            f"错误：{type(exc).__name__}: {exc}。"
            "不要重新展开整套销售策略，也不要为了逃避结构修复而撤销已经有完整证据的合法动作。"
            "只有结构清单明确要求重新阅读 current_message 时才允许修正支付状态；"
            "其他情况下不得重判客户意图。"
            "除最终结构清单明确允许改变的字段外，保持上一版已通过校验的字段和客户可见结构不变。"
            "客户可见 text 必须是对 current_message 的自然回应，禁止原样复制客户消息来代替回答。"
            "请只输出修复后的完整严格 JSON 对象，顶层必须包含非空 reply_messages 数组；"
            "不要解释错误，不要输出 markdown，不要输出内部分析。"
        )
    else:
        retry_instruction = (
            "先执行本次针对性修复要求："
            f"{payment_repair_guard}"
            f"{repair_hint}"
            "然后再按以下通用一致性合同复核整份输出。"
            "上一次输出没有通过 JSON schema 校验。"
            f"错误：{type(exc).__name__}: {exc}。"
            "这是一次基于原始上下文的完整一致性修复，不是机械追加缺失字段。先核对上一版客户可见动作是否被本轮真实引用和硬事实允许："
            "若动作本身不合法，必须撤销冲突的结构消息、deposit_evidence、selected_content_ids 和客户可见承诺；"
            "若动作合法，才保留原业务判断并一次补齐它要求的全部结构、引用和客户可见内容。"
            "不要因为错误写着‘缺卡片’就直接加卡，也不要因为补卡会麻烦就逃避已经合法的 payment。"
            "修复后必须重新检查整份 JSON，确保没有用当前轮内容资产冒充更早证据、没有修复一项又制造新的金额、支付、登记或候选交付冲突。"
            f"{context_hint}"
            "提交前统一复核：每个 selected_content_ids 都必须来自允许候选，并交付该候选要求的全部结构消息；"
            "本轮实际发送 payment_collection 时必须有且只有一张，并同时输出完整 deposit_evidence；"
            "权威已付登记不得再次发送 payment_collection。"
            f"最后再次执行本轮付款状态的最高优先级结构要求：{payment_repair_guard}"
            "请只重新输出严格 JSON 对象，顶层必须包含非空 reply_messages 数组；"
            "不要解释错误，不要输出 markdown，不要输出内部分析。"
        )
    previous_output = (
        [{"role": "assistant", "content": json.dumps(previous_payload, ensure_ascii=False, separators=(",", ":"))}]
        if isinstance(previous_payload, dict)
        else []
    )
    if structural_repair_guard:
        # Structural repair is deliberately isolated from the full sales prompt.
        # The model already made the business decision; repeating the 10k+ token
        # strategy contract makes exact citation/message repairs less reliable.
        # Python only exposes source facts and allowed choices here. The model
        # still decides whether the cited customer text supports the action.
        repair_facts = {
            "current_message": (
                validation_context.get("current_message")
                if isinstance(validation_context, dict)
                else {}
            ),
            "prior_message_options": (
                validation_context.get("prior_message_options") or []
                if isinstance(validation_context, dict)
                else []
            ),
        }
        return [
            {
                "role": "system",
                "content": (
                    "你是 JSON 结构修复器，不重新制定销售策略。"
                    "复制上一版完整 JSON，只按最后一条用户消息中的结构清单修复。"
                    "不得自动补造证据、素材或客户话术；证据是否支持动作仍由你阅读原文判断。"
                    "只输出一个完整、严格、可解析的 json 对象。"
                ),
            },
            {
                "role": "user",
                "content": "本次修复可使用的原始事实：" + json_dumps(repair_facts),
            },
            *previous_output,
            {"role": "user", "content": retry_instruction},
        ]
    return [*messages, *previous_output, {"role": "user", "content": retry_instruction}]

def _parallel_generic_reply_repair_messages(
    messages: list[dict[str, Any]],
    exc: Exception,
    *,
    previous_payload: dict[str, Any] | None,
    validation_context: dict[str, Any],
) -> list[dict[str, Any]]:
    """Repair only schema, provenance and side-effect structure with readable instructions."""

    raw_error = str(exc)
    markers = (
        "reply_admission_violations::",
        "parallel_reply_hard_violations::",
    )
    marker = next((item for item in markers if item in raw_error), "")
    violation_codes = raw_error.split(marker, 1)[1].split(";;") if marker else [raw_error]
    violations = [item.strip() for item in violation_codes if item.strip()]
    structured_delivery_options = (
        validation_context.get("structured_delivery_options")
        if isinstance(validation_context.get("structured_delivery_options"), dict)
        else {}
    )
    repair_contract = {
        "schema_version": "parallel_reply_generic_repair_v4",
        "failure_class": _parallel_repair_failure_class(violations),
        "violations": violations,
        "required_change": (
            "只修复列出的结构、引用或确定性事实冲突。若原动作的副作用条件无法证明，"
            "降级 action，并删除对应结构消息、资产选择、content_asset 引用和副作用声明；"
            "保留未冲突的事实解释、客户可见内容和销售判断。"
        ),
        "rules": [
            "不得重新判断客户心理、成交阶段或销售节奏，不得按错误码生成新销售话术。",
            "所有 ID、URL、金额、结构消息和 evidence_refs 只能取自 valid_reference_contract。",
            "采用内容资产就完整交付其必需结构；无法完整交付就删除该资产 ID 和对应引用。",
            "只输出完整严格 json，不解释错误，不输出 markdown 或内部分析。",
        ],
        "previous_reply_claims": {
            "action": str((previous_payload or {}).get("action") or ""),
            "payment_assessment": (
                (previous_payload or {}).get("payment_assessment")
                if isinstance((previous_payload or {}).get("payment_assessment"), dict)
                else {}
            ),
        },
        "exact_payment_delivery_contract": (
            structured_delivery_options.get("payment_collection")
            if isinstance(structured_delivery_options.get("payment_collection"), dict)
            else {}
        ),
        "valid_reference_contract": {
            "current_message": validation_context.get("current_message") or {},
            "prior_message_options": validation_context.get("prior_message_options") or [],
            "valid_customer_message_refs": validation_context.get("valid_customer_message_refs") or [],
            "valid_deposit_evidence_refs": validation_context.get("valid_deposit_evidence_refs") or [],
            "allowed_selected_content_ids": validation_context.get("allowed_selected_content_ids") or [],
            "content_candidate_reference_options": validation_context.get("content_candidate_reference_options") or [],
            "tool_fact_reference_options": validation_context.get("tool_fact_reference_options") or [],
            "authoritative_fact_reference_options": validation_context.get("authoritative_fact_reference_options") or [],
            "content_candidate_delivery_requirements": validation_context.get("content_candidate_delivery_requirements") or [],
            "authoritative_paid": bool(validation_context.get("authoritative_paid")),
        },
    }
    evidence_messages = [
        item
        for item in messages
        if isinstance(item, dict) and str(item.get("role") or "") == "user"
    ]
    previous_output = (
        [{"role": "assistant", "content": json.dumps(previous_payload, ensure_ascii=False, separators=(",", ":"))}]
        if isinstance(previous_payload, dict)
        else []
    )
    return [
        {
            "role": "system",
            "content": (
                "你是最终 Reply 的通用校验修复器，不是第二个销售大脑。"
                "只处理 schema、结构素材、引用或确定性事实冲突。"
                "保留所有未冲突内容，只输出完整严格 json。"
            ),
        },
        *evidence_messages,
        *previous_output,
        {
            "role": "user",
            "content": "这是一次事实与结构最小修复，不是重新制定销售策略。" + json_dumps(repair_contract),
        },
    ]

def _legacy_parallel_generic_reply_repair_messages(
    messages: list[dict[str, Any]],
    exc: Exception,
    *,
    previous_payload: dict[str, Any] | None,
    validation_context: dict[str, Any],
) -> list[dict[str, Any]]:
    """Repair schema, structural delivery, or deterministic fact conflicts only."""

    raw_error = str(exc)
    markers = (
        "reply_admission_violations::",
        "parallel_reply_hard_violations::",
    )
    marker = next((item for item in markers if item in raw_error), "")
    violation_codes = raw_error.split(marker, 1)[1].split(";;") if marker else [raw_error]
    violations = [item.strip() for item in violation_codes if item.strip()]
    failure_class = _parallel_repair_failure_class(violations)
    required_changes = _parallel_repair_required_changes(violations)
    structured_delivery_options = (
        validation_context.get("structured_delivery_options")
        if isinstance(validation_context.get("structured_delivery_options"), dict)
        else {}
    )
    payment_delivery_contract = (
        structured_delivery_options.get("payment_collection")
        if isinstance(structured_delivery_options.get("payment_collection"), dict)
        else {}
    )
    repair_contract = {
        "schema_version": "parallel_reply_generic_repair_v3",
        "failure_class": failure_class,
        "violations": violations,
        "required_changes": required_changes,
        "rules": [
            "只修复 violations 指出的 schema、结构素材/引用或确定性事实冲突。",
            "保留上一版未冲突的客户可见回复、销售判断和动作，不重新判断客户心理、成交阶段或销售节奏。",
            "所有 ID、URL、金额、结构消息和 evidence_refs 只能逐字取自 valid_reference_contract。",
            "选择内容资产时完整交付其要求的结构素材；不采用时删除该 ID 和对应 content_asset 引用，不得半选半发。",
            "结构选项不足以支持原动作时，删除不受支持的结构声明；不得编造事实或按错误码补写销售话术。",
            "只输出完整严格 json，不解释错误，不输出 markdown 或内部分析。",
        ],
        "output_schema_constraints": {
            "sales_judgment_required_fields": [
                "primary_objective",
                "customer_friction_observation",
                "posture",
            ],
            "sales_judgment_posture": ["answer", "advance", "switch", "pause", "close"],
            "payment_assessment_status": [
                "none",
                "manual_transfer",
                "unverified_paid_claim",
                "payment_request",
                "authoritative_paid",
            ],
            "payment_channel": ["none", "payment_card", "transfer", "red_packet"],
        },
        "previous_reply_claims": {
            "action": str((previous_payload or {}).get("action") or ""),
            "payment_assessment": (
                (previous_payload or {}).get("payment_assessment")
                if isinstance((previous_payload or {}).get("payment_assessment"), dict)
                else {}
            ),
        },
        "exact_payment_delivery_contract": payment_delivery_contract,
        "valid_reference_contract": {
            "current_message": validation_context.get("current_message") or {},
            "prior_message_options": validation_context.get("prior_message_options") or [],
            "valid_customer_message_refs": validation_context.get("valid_customer_message_refs") or [],
            "valid_deposit_evidence_refs": validation_context.get("valid_deposit_evidence_refs") or [],
            "allowed_selected_content_ids": validation_context.get("allowed_selected_content_ids") or [],
            "content_candidate_reference_options": validation_context.get("content_candidate_reference_options") or [],
            "tool_fact_reference_options": validation_context.get("tool_fact_reference_options") or [],
            "authoritative_fact_reference_options": (
                validation_context.get("authoritative_fact_reference_options") or []
            ),
            "store_fact_status": validation_context.get("store_fact_status") or {},
            "registration_fact_status": validation_context.get("registration_fact_status") or {},
            "content_candidate_delivery_requirements": validation_context.get("content_candidate_delivery_requirements") or [],
            "authoritative_paid": bool(validation_context.get("authoritative_paid")),
        },
    }
    previous_output = (
        [{"role": "assistant", "content": json.dumps(previous_payload, ensure_ascii=False, separators=(",", ":"))}]
        if isinstance(previous_payload, dict)
        else []
    )
    evidence_messages = [
        item
        for item in messages
        if isinstance(item, dict) and str(item.get("role") or "") == "user"
    ]
    return [
        {
            "role": "system",
            "content": (
                "你是最终 Reply 的通用校验修复器，不是第二个销售大脑。"
                "只处理 schema、结构素材/引用或确定性事实冲突。"
                "不得按场景生成新销售策略；保留所有未冲突内容，只输出完整严格 json。"
            ),
        },
        *evidence_messages,
        *previous_output,
        {
            "role": "user",
            "content": (
                "这是一次事实与结构最小修复，不是重新制定销售策略。"
                + json_dumps(repair_contract)
            ),
        },
    ]

def _parallel_repair_required_changes(violations: Any) -> list[dict[str, str]]:
    """Describe one of three generic repair obligations, never sales intent."""

    required: list[dict[str, str]] = []
    for raw in violations if isinstance(violations, list) else []:
        code = str(raw or "")
        required.append(
            {
                "violation": code,
                "repair_class": _parallel_repair_failure_class([code]),
                "required_change": (
                    "按 violation 做最小修复；只使用 valid_reference_contract 中的合法枚举、真实引用和结构选项。"
                    "无法支持时删除冲突字段或结构声明，保留其他客户可见内容与销售判断。"
                ),
            }
        )
    return required

def _parallel_repair_failure_class(violations: list[str]) -> str:
    text = " ".join(violations).lower()
    schema_markers = (
        "schema",
        "json",
        "missing reply_messages",
        "reply_messages are empty",
        "invalid_parallel_reply_action",
        "invalid_parallel_reply_list_field",
    )
    structure_markers = (
        "selected_content",
        "structured_delivery",
        "message_content",
        "evidence_ref",
        "payment_collection",
        "store_address",
        "media",
        "image",
        "video",
    )
    if any(marker in text for marker in schema_markers):
        return "schema"
    if any(marker in text for marker in structure_markers):
        return "structure_and_provenance"
    return "deterministic_fact_conflict"

def _reply_payment_repair_guard(previous_payload: dict[str, Any] | None) -> str:
    """Keep repair focused without inferring payment semantics in Python.

    The guard only reflects Reply's own previous structured assessment. The
    repair model still reads the original customer message and owns any
    semantic correction.
    """

    if not isinstance(previous_payload, dict):
        return ""
    assessment = (
        previous_payload.get("payment_assessment")
        if isinstance(previous_payload.get("payment_assessment"), dict)
        else {}
    )
    status = str(assessment.get("status") or "").strip()
    status = {
        "unverified_oral_paid_claim": "unverified_paid_claim",
    }.get(status, status)
    channel = str(assessment.get("payment_channel") or "").strip()
    if status in {"manual_transfer", "unverified_paid_claim"}:
        channel_rule = (
            f"payment_channel 必须继续保持 {channel}；"
            if channel in {"transfer", "red_packet"}
            else "若为未核验已付声明，payment_channel 使用 none；若为客户明确选择的人工渠道，只能按原文选择 transfer 或 red_packet；"
        )
        return (
            f"上一版 Reply 已将 payment_assessment.status 判断为 {status}。本次错误若未明确指出该状态或其引用非法，"
            "就必须保留这一更具体的非小程序支付判断，只修正冲突结构：action 使用 none/ask，"
            f"{channel_rule}selected_content_ids=[]，deposit_evidence 全部清空，不发送 payment_collection，也不声称已到账或已登记。"
        )
    if status == "payment_request":
        return (
            "上一版写了 payment_assessment.status=payment_request，但枚举合法不代表语义一定正确。"
            "修复前先由你重新阅读原始 current_message：普通文字声称已经付好/转好应改为 unverified_paid_claim；"
            "客户明确选择人工转账应改为 manual_transfer + transfer，明确选择微信红包应改为 manual_transfer + red_packet；"
            "只有仍是一般报名付款请求或明确索要小程序收款卡时"
            "才保留 payment_request。这个判断必须由你根据原文完成，代码没有替你做关键词判定。"
            "这是结构修复，不允许把 payment_request 改成含糊的 none 来逃避补卡、补素材或补引用。"
            "除非原文实际属于 manual_transfer/unverified_paid_claim，或输入存在硬禁区，否则必须保留"
            " payment_request，并让 action、deposit_evidence 和客户可见结构与它一致。"
            "如果你把状态纠正为 manual_transfer 或 unverified_paid_claim，必须在同一个 JSON 中成组完成结构修复："
            "action 改为 none/ask；selected_content_ids=[]；deposit_evidence 四个字段全部清空；删除候选图片和"
            "payment_collection；manual_transfer 保留客户明确选择的 transfer 或 red_packet，unverified_paid_claim 使用 none。"
            "不得只改 payment_assessment 枚举却保留发卡结构，也不得把红包静默改成转账。"
        )
    return ""

def _reply_structural_repair_guard(
    error: str,
    *,
    previous_payload: dict[str, Any] | None,
    validation_context: dict[str, Any] | None,
) -> str:
    """Build a final evidence-only checklist for common parallel Reply repairs.

    The checklist exposes exact references and candidate messages. It never
    selects evidence, changes a sales action, or manufactures customer-visible
    structures in Python; the repair model still owns those decisions.
    """

    if not isinstance(previous_payload, dict) or not isinstance(validation_context, dict):
        return ""

    tasks: list[dict[str, Any]] = []
    if "offer_total_tail_amount_conflict" in error:
        immutable_fields = {
            key: previous_payload.get(key)
            for key in (
                "action",
                "payment_assessment",
                "deposit_evidence",
                "selected_content_ids",
                "used_fact_refs",
            )
        }
        non_text_messages = [
            item
            for item in previous_payload.get("reply_messages") or []
            if isinstance(item, dict) and str(item.get("type") or "").strip() != "text"
        ]
        tasks.append(
            {
                "violation": "text_only_tail_amount_wording_repair",
                "instruction": (
                    "本次错误只允许修正客户可见 text 中把258元尾款写成抵扣金额的事实错误；"
                    "改为到店再付或补付258元。不得重新审理销售动作，不得删除或新增结构消息。"
                ),
                "immutable_fields": immutable_fields,
                "required_non_text_messages": non_text_messages,
            }
        )
    if (
        "registration_confirmation_fact_required" in error
        or "appointment_confirmation_fact_required" in error
    ):
        immutable_fields = {
            key: previous_payload.get(key)
            for key in (
                "action",
                "payment_assessment",
                "deposit_evidence",
                "selected_content_ids",
                "used_fact_refs",
            )
        }
        non_text_messages = [
            item
            for item in previous_payload.get("reply_messages") or []
            if isinstance(item, dict) and str(item.get("type") or "").strip() != "text"
        ]
        tasks.append(
            {
                "violation": "unverified_registration_or_appointment_wording",
                "instruction": (
                    "只修正客户可见 text 中把尚未支付、登记或预约写成已经留好、已经安排、"
                    "已经登记的事实错误。未付时改为条件表达，例如付10元预约金后才能留活动资格；"
                    "不得索要已付登记信息，不得重新审理销售动作。"
                ),
                "immutable_fields": immutable_fields,
                "required_non_text_messages": non_text_messages,
            }
        )
    if "payment_collection_requires_prior_supporting_key_evidence" in error:
        deposit = (
            previous_payload.get("deposit_evidence")
            if isinstance(previous_payload.get("deposit_evidence"), dict)
            else {}
        )
        allowed_refs = {
            str(item or "").strip()
            for item in validation_context.get("prior_assistant_message_refs") or []
            if str(item or "").strip()
        }
        delivery_options = [
            {
                "ref": str(item.get("ref") or "").strip(),
                "role": str(item.get("role") or "").strip().lower(),
                "content": str(item.get("content") or ""),
            }
            for item in validation_context.get("prior_message_options") or []
            if isinstance(item, dict)
            and str(item.get("role") or "").strip().lower() in {"assistant", "staff", "ai"}
            and str(item.get("ref") or "").strip() in allowed_refs
        ]
        tasks.append(
            {
                "violation": "missing_prior_supporting_delivery_reference",
                "previous_supporting_key": str(deposit.get("supporting_key") or "").strip(),
                "previous_supporting_refs": [
                    str(item or "").strip()
                    for item in deposit.get("supporting_refs") or []
                    if str(item or "").strip()
                ],
                "allowed_prior_delivery_options": delivery_options,
                "structured_delivered_assets": (
                    validation_context.get("structured_delivered_assets") or []
                ),
                "choice_keep_payment": (
                    "由你阅读历史交付；仅当更早客服消息或结构化已完成资产确实交付了"
                    " previous_supporting_key 对应维度时，把真实 ref 加入 supporting_refs，"
                    "并保持其余合法付款结构。客户无需另行确认该交付。"
                ),
                "choice_cancel_payment": {
                    "when": "没有任何更早的真实交付证据",
                    "action": "改为真实的非付款动作",
                    "selected_content_ids": [],
                    "deposit_evidence": {
                        "offer_prior_turn_refs": [],
                        "supporting_key": "",
                        "supporting_refs": [],
                        "current_intent_refs": [],
                    },
                    "payment_collection": "删除",
                },
            }
        )

    repair_payment_assessment = (
        previous_payload.get("payment_assessment")
        if isinstance(previous_payload.get("payment_assessment"), dict)
        else {}
    )
    repair_payment_status = str(repair_payment_assessment.get("status") or "").strip()
    repair_payment_channel = str(repair_payment_assessment.get("payment_channel") or "").strip()
    if repair_payment_status in {"manual_transfer", "unverified_paid_claim"}:
        tasks.append(
            {
                "violation": "non_card_payment_status_requires_structural_cleanup",
                "payment_status": repair_payment_status,
                "payment_channel": repair_payment_channel or (
                    "none" if repair_payment_status == "unverified_paid_claim" else "transfer | red_packet"
                ),
                "required_structure": {
                    "action": "none 或确实需要客户补截图时 ask",
                    "selected_content_ids": [],
                    "deposit_evidence": {
                        "offer_prior_turn_refs": [],
                        "supporting_key": "",
                        "supporting_refs": [],
                        "current_intent_refs": [],
                    },
                    "payment_collection": "禁止",
                    "sales_assessment.dimension_decision": "stay | switch | pause | close",
                },
                "instruction": (
                    "保持模型已经判断的具体支付状态和客户已选择的人工付款渠道，只清除小程序卡及冲突成交结构；"
                    "红包不得静默改成转账，未核验已付声明的 payment_channel 必须为 none。"
                    "客户可见 text 必须自然回答客户，不能原样复制 current_message。"
                ),
            }
        )

    if "invalid_reply_dimension_decision" in error:
        tasks.append(
            {
                "violation": "invalid_sales_assessment_dimension_decision",
                "allowed_values": ["stay", "switch", "pause", "close"],
                "instruction": (
                    "只把 sales_assessment.dimension_decision 修正为一个合法枚举；"
                    "不得借此重新改变支付状态、动作或客户可见结构。"
                ),
            }
        )

    delivery_options = (
        validation_context.get("structured_delivery_options")
        if isinstance(validation_context.get("structured_delivery_options"), dict)
        else {}
    )
    store_delivery = (
        delivery_options.get("store_address")
        if isinstance(delivery_options.get("store_address"), dict)
        else {}
    )
    store_payloads = [
        item
        for item in store_delivery.get("message_payloads") or []
        if isinstance(item, dict)
    ]
    if store_payloads:
        prior_decisions = _legacy_normalized_structured_delivery_decisions(
            previous_payload.get("structured_delivery_decisions")
        )
        prior_store_decision = next(
            (
                item
                for item in prior_decisions
                if item["fact_ref"] == "tool_fact:customer_store_lookup"
            ),
            None,
        )
        tasks.append(
            {
                "violation": "current_tool_store_delivery_requires_explicit_decision",
                "tool_status": str(store_delivery.get("status") or ""),
                "fact_ref": str(
                    store_delivery.get("fact_ref")
                    or "tool_fact:customer_store_lookup"
                ),
                "available_store_messages": store_payloads,
                "previous_decision": prior_store_decision or {},
                "instruction": (
                    "只修复结构交付冲突，并保留 Reply 原有销售判断。必须对 fact_ref 明确选择 deliver 或 defer："
                    "选择 deliver 时引用 fact_ref 并逐项输出 available_store_messages；"
                    "选择 defer 时保留不交付的上下文理由，不得伪称已经发送。"
                ),
            }
        )

    if "completed_content_repeat_requires_current_customer_ref" in error:
        tasks.append(
            {
                "violation": "completed_content_repeat_reference",
                "instruction": (
                    "重复采用已完成内容资产时，只有当前客户确实再次要求该内容才保留资产；"
                    "否则删除该资产 ID。"
                    "无论选择哪条路径，都不得覆盖本轮真实工具交付。"
                ),
            }
        )

    selected_ids = [
        str(item or "").strip()
        for item in previous_payload.get("selected_content_ids") or []
        if str(item or "").strip()
    ]
    if selected_ids or "selected_content_delivery_missing" in error:
        selected_set = set(selected_ids)
        requirements = [
            item
            for item in validation_context.get("content_candidate_delivery_requirements") or []
            if isinstance(item, dict) and str(item.get("content_id") or "").strip() in selected_set
        ]
        tasks.append(
            {
                "violation": "selected_content_delivery_incomplete",
                "selected_content_ids": selected_ids,
                "exact_delivery_requirements": requirements,
                "choice_keep_asset": (
                    "保留 ID 时，逐项输出 exact_delivery_requirements.messages 中全部结构消息，"
                    "不得只补其中一项。"
                ),
                "choice_drop_asset": (
                    "不交付整套素材时，删除该 ID。"
                    "若独立预约金证据仍合法，可保留一张 payment_collection，"
                    "但不得再声明采用该内容资产。"
                ),
            }
        )

    previous_messages = [
        item for item in previous_payload.get("reply_messages") or [] if isinstance(item, dict)
    ]
    previous_payment_messages = [
        {"type": "payment_collection", "content": item.get("content")}
        for item in previous_messages
        if str(item.get("type") or "").strip() == "payment_collection"
    ]
    if str(previous_payload.get("action") or "").strip() == "payment" or previous_payment_messages:
        assessment = (
            previous_payload.get("payment_assessment")
            if isinstance(previous_payload.get("payment_assessment"), dict)
            else {}
        )
        deposit = (
            previous_payload.get("deposit_evidence")
            if isinstance(previous_payload.get("deposit_evidence"), dict)
            else {}
        )
        tasks.append(
            {
                "violation": "preserve_or_cancel_payment_structure_as_one_group",
                "previous_payment_status": str(assessment.get("status") or "").strip(),
                "previous_payment_messages": previous_payment_messages,
                "previous_deposit_evidence": deposit,
                "choice_keep_payment": (
                    "仅当重新阅读 current_message 后仍是合法 payment_request 且硬事实允许时，"
                    "保持 action=payment、完整 deposit_evidence，并输出且只输出一张 payment_collection。"
                ),
                "choice_cancel_payment": (
                    "若支付位置应改为 manual_transfer/unverified_paid_claim 或命中硬禁区，"
                    "同时撤销 payment、删除 payment_collection、清空 deposit_evidence 和冲突候选；"
                    "不得只修文字后丢卡，也不得只留卡而清空证据。"
                ),
            }
        )

    assessment = (
        previous_payload.get("payment_assessment")
        if isinstance(previous_payload.get("payment_assessment"), dict)
        else {}
    )
    supporting_delivery_violation = (
        "payment_collection_requires_prior_supporting_key_evidence" in error
    )
    if (
        str(assessment.get("status") or "").strip() == "payment_request"
        and not supporting_delivery_violation
    ):
        tasks.append(
            {
                "violation": "payment_request_decision_must_remain_structurally_consistent",
                "instruction": (
                    "先重新阅读 current_message。若它仍是普通报名、预约、付款请求或索要小程序收款卡，"
                    "必须保留 payment_assessment.status=payment_request；不得改成 none 来逃避结构修复。"
                    "此时若原 deposit_evidence 经引用修复后完整，就必须保持 action=payment，"
                    "并输出且只输出一张 payment_collection。可以放弃某个内容资产及其图片，"
                    "但不能同时放弃独立合法的付款动作。只有原文实际是人工转账、无权威已付声明，"
                    "或输入存在硬禁区时，才允许改成对应状态并成组撤销付款结构。"
                ),
                "previous_payment_assessment": assessment,
                "previous_deposit_evidence": (
                    previous_payload.get("deposit_evidence")
                    if isinstance(previous_payload.get("deposit_evidence"), dict)
                    else {}
                ),
            }
        )

    if not tasks:
        return ""
    contract = {
        "schema_version": "parallel_reply_structural_repair_v1",
        "instruction": "逐项完成全部 tasks；每项只能选择其中一条路径，禁止混合或遗漏。",
        "tasks": tasks,
    }
    return f"最高优先级最终结构清单（覆盖前面冲突的通用措辞）：{json_dumps(contract)}。"

def _parallel_reply_repair_context(state: AgentState) -> dict[str, Any]:
    """Expose factual reference choices needed for schema repair."""

    if not state.get("evidence_join"):
        return {}
    shared = _parallel_shared_context(state)
    conversation = [item for item in shared.get("conversation") or [] if isinstance(item, dict)]
    prior_customer_refs = [
        str(item.get("message_ref") or "").strip()
        for item in conversation
        if str(item.get("role") or "").strip().lower() in {"customer", "user"}
        and str(item.get("message_ref") or "").strip()
        and str(item.get("message_ref") or "").strip() != "current_message"
    ]
    prior_assistant_refs = [
        str(item.get("message_ref") or "").strip()
        for item in conversation
        if str(item.get("role") or "").strip().lower() in {"assistant", "staff"}
        and str(item.get("message_ref") or "").strip()
        and str(item.get("message_ref") or "").strip() != "current_message"
    ]
    prior_message_options = [
        {
            "ref": str(item.get("message_ref") or "").strip(),
            "role": str(item.get("role") or "").strip().lower(),
            "content": str(item.get("content") or ""),
        }
        for item in conversation
        if str(item.get("message_ref") or "").strip()
        and str(item.get("message_ref") or "").strip() != "current_message"
    ]
    payload = parallel_reply_payload(state)
    joined = state.get("evidence_join") if isinstance(state.get("evidence_join"), dict) else {}
    candidate_requirements: list[dict[str, Any]] = []
    for candidate in joined.get("content_candidates") or []:
        if not isinstance(candidate, dict):
            continue
        content_id = str(candidate.get("content_id") or "").strip()
        if not content_id:
            continue
        candidate_requirements.append(
            {
                "content_id": content_id,
                "required_used_fact_ref": f"content_asset:{content_id}",
                "asset_role": str(candidate.get("asset_role") or "").strip(),
                "delivery_status": str(candidate.get("delivery_status") or "").strip(),
                "repeat_delivery_required": (
                    str(candidate.get("delivery_status") or "").strip() != "completed"
                ),
                "messages": [
                    {
                        "type": str(item.get("type") or "").strip(),
                        "content": item.get("content"),
                    }
                    for item in candidate.get("messages") or []
                    if isinstance(item, dict) and str(item.get("type") or "").strip()
                ],
            }
        )
    return {
        "schema_version": "parallel_reply_repair_context_v2",
        "current_message": dict(shared.get("current_message") or {}),
        "prior_customer_message_refs": prior_customer_refs,
        "prior_message_options": prior_message_options,
        "valid_customer_message_refs": payload.get("valid_customer_message_refs") or [],
        "valid_deposit_evidence_refs": payload.get("valid_deposit_evidence_refs") or [],
        "structured_prior_activity_refs": payload.get("structured_prior_activity_refs") or [],
        "structured_prior_supporting_refs": (
            payload.get("structured_prior_supporting_refs") or []
        ),
        "prior_assistant_message_refs": (
            payload.get("prior_assistant_message_refs") or prior_assistant_refs
        ),
        "structured_delivered_assets": payload.get("structured_delivered_assets") or [],
        "structured_delivery_options": payload.get("structured_delivery_options") or {},
        "store_fact_status": payload.get("store_fact_status") or {},
        "registration_fact_status": payload.get("registration_fact_status") or {},
        "prior_message_and_delivery_refs": (
            payload.get("prior_message_and_delivery_refs") or []
        ),
        "allowed_selected_content_ids": payload.get("allowed_selected_content_ids") or [],
        "content_candidate_reference_options": (
            payload.get("content_candidate_reference_options") or []
        ),
        "tool_fact_reference_options": payload.get("tool_fact_reference_options") or [],
        "authoritative_fact_reference_options": (
            payload.get("authoritative_fact_reference_options") or []
        ),
        "content_candidate_delivery_requirements": candidate_requirements,
        "authoritative_paid": bool(_parallel_paid_deposit_context(state)),
    }

def _reply_model_tier(state: AgentState) -> str:
    if _needs_strong_reply_model(state):
        return "strong"
    return "reply"

def _needs_strong_reply_model(state: AgentState) -> bool:
    if state.get("evidence_join"):
        # The final Reply receives the complete joined evidence and owns the
        # business interpretation. Do not select a model from legacy stage,
        # blocker, customer-type, or Planner fields.
        return False
    handoff = state.get("handoff") if isinstance(state.get("handoff"), dict) else {}
    if handoff.get("needed"):
        return True
    for tool in state.get("required_tools") or []:
        if isinstance(tool, dict) and str(tool.get("name") or "") == "professional_assist":
            return True
    structured = _structured_facts(state)
    professional_assist = structured.get("professional_assist")
    if isinstance(professional_assist, dict) and str(professional_assist.get("status") or ""):
        return True
    risk_hold_state = health_risk_hold(state)
    if is_hard_health_risk_hold(risk_hold_state):
        return True
    for key in ("planner_stage", "conversion_stage", "main_blocker", "sub_rule_id", "customer_type"):
        value = str(state.get(key) or "").lower()
        if any(marker in value for marker in ("complaint", "refund", "payment_exception", "risk", "handoff")):
            return True
    return False

def _maybe_build_required_payment_collection_fallback(
    state: AgentState,
    exc: Exception,
    *,
    messages: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]] | None:
    if state.get("evidence_join"):
        # A structured payment card is a final Reply decision in the parallel
        # chain. Python may validate it, but must never manufacture one.
        return None
    if "payment_collection_required_when_reply_promises_payment_entry" not in str(exc):
        return None
    source_messages = [item for item in (messages or []) if isinstance(item, dict)]
    if not source_messages or _messages_have_payment_collection(source_messages):
        return None
    if is_hard_health_risk_hold(health_risk_hold(state)):
        return None
    if _state_has_paid_deposit_context(state):
        return None
    if not _state_requires_payment_collection(state):
        return None
    context = payment_collection_context(state=state, messages=[])
    if context.get("over_limit"):
        return None
    amount = int(context.get("amount") or 10)
    return _renumber(
        [
            *source_messages,
            {
                "type": "payment_collection",
                "order": len(source_messages) + 1,
                "content": payment_collection_content({"amount": amount}, state=state, messages=source_messages),
            },
        ]
    )

def _messages_have_payment_collection(messages: list[dict[str, Any]]) -> bool:
    return any(isinstance(item, dict) and str(item.get("type") or "") == "payment_collection" for item in messages)

def _state_requires_payment_collection(state: AgentState) -> bool:
    payment_decision = state.get("payment_decision") if isinstance(state.get("payment_decision"), dict) else {}
    decision_action = str(payment_decision.get("action") or "")
    if decision_action in {"send_now", "resend"}:
        return True
    if decision_action in {"none", "explain", "manual_transfer", "after_paid_next_step", "ask_party_size"}:
        return False
    payment_action = str(state.get("payment_action") or "")
    if payment_action in {"none", "manual_transfer", "offer_resend", "explain_existing", "confirm_next_step"}:
        return False
    if payment_action == "send_now":
        return True
    if str(state.get("payment_state") or "") == "customer_claimed_paid":
        return False
    return False

def _state_has_paid_deposit_context(state: AgentState) -> bool:
    """Use the same authoritative paid-fact boundary as final validation."""
    return _paid_deposit_context(state)

def _ensure_required_handoff_notice(messages: list[dict[str, Any]], state: AgentState) -> tuple[list[dict[str, Any]], bool]:
    if not messages or _messages_have_handoff_notice(messages) or not _state_requests_handoff_notice(state):
        return messages, False
    reason = _handoff_notice_reason(state)
    return (
        _renumber(
            [
                *messages,
                {
                    "type": "human_handoff_notice",
                    "order": len(messages) + 1,
                    "content": {"handoff_reason": reason},
                },
            ]
        ),
        True,
    )

def _suppress_stale_handoff_notice(messages: list[dict[str, Any]], state: AgentState) -> tuple[list[dict[str, Any]], bool]:
    if _state_has_current_handoff_notice_signal(state):
        return messages, False
    if not messages or not _is_stale_handoff_context(state):
        return messages, False
    changed = False
    filtered: list[dict[str, Any]] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "") in {"human_handoff", "human_handoff_notice"}:
            changed = True
            continue
        if str(item.get("type") or "") == "text" and _is_stale_handoff_status_text(_message_text(item.get("content"))):
            changed = True
            continue
        filtered.append(item)
    if not filtered:
        return messages, False
    return _renumber(filtered), changed

def _is_stale_handoff_context(state: AgentState) -> bool:
    if explicit_professional_assist_reason(state):
        return False
    if is_hard_health_risk_hold(health_risk_hold(state)):
        return False
    current = str(state.get("normalized_content") or state.get("content") or "")
    if _contains_any(current, ("说了三遍", "说了很多遍", "一直问", "还问", "烦死了", "很烦", "不会回答", "强烈不满")):
        return False
    return True

def _is_stale_handoff_status_text(text: str) -> bool:
    compact = "".join(str(text or "").split())
    if not compact:
        return False
    stale_markers = (
        "健康评估正在",
        "健康评估未闭环",
        "专业团队核验",
        "加急处理",
        "结果出来后",
        "内部关注",
    )
    return any(marker in compact for marker in stale_markers)

def _message_text(content: Any) -> str:
    if isinstance(content, dict):
        for key in ("text", "handoff_reason", "reason", "url", "store_id", "amount"):
            value = content.get(key)
            if value not in (None, ""):
                return str(value)
        return ""
    return str(content or "")

def _messages_have_handoff_notice(messages: list[dict[str, Any]]) -> bool:
    return any(
        isinstance(item, dict) and str(item.get("type") or "") in {"human_handoff", "human_handoff_notice"}
        for item in messages
    )

def _state_requests_handoff_notice(state: AgentState) -> bool:
    handoff = state.get("handoff") if isinstance(state.get("handoff"), dict) else {}
    if bool(handoff.get("needed")):
        return True
    return _state_has_current_handoff_notice_signal(state)

def _state_has_current_handoff_notice_signal(state: AgentState) -> bool:
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
    risk_hold = health_risk_hold(state)
    if is_hard_health_risk_hold(risk_hold):
        reason = str(risk_hold.get("reason") or "").strip()
        if reason:
            return reason[:180]
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

def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)

def _structured_facts(state: AgentState) -> dict[str, Any]:
    fact_envelope = state.get("fact_envelope") if isinstance(state.get("fact_envelope"), dict) else {}
    structured = fact_envelope.get("structured_facts") if isinstance(fact_envelope.get("structured_facts"), dict) else {}
    if isinstance(structured, dict) and structured:
        return structured
    joined = state.get("evidence_join") if isinstance(state.get("evidence_join"), dict) else {}
    normalized = (
        joined.get("normalized_tool_facts")
        if isinstance(joined.get("normalized_tool_facts"), dict)
        else {}
    )
    structured = normalized.get("structured_facts") if isinstance(normalized.get("structured_facts"), dict) else {}
    return structured if isinstance(structured, dict) else {}

def _compact_text(value: Any) -> str:
    return "".join(str(value or "").split()).lower()

def _reply_repair_hint(error: str) -> str:
    aggregate_marker = "parallel_reply_hard_violations::"
    if aggregate_marker in error:
        raw = error.split(aggregate_marker, 1)[1]
        combined_asset_deposit_error = (
            "selected_content_delivery_missing" in raw
            and "payment_collection_requires_prior_supporting_key_evidence" in raw
        )
        hints: list[str] = [_reply_repair_hint(raw)] if combined_asset_deposit_error else []
        for violation in (item.strip() for item in raw.split(";;")):
            if not violation:
                continue
            if combined_asset_deposit_error and (
                "selected_content_delivery_missing" in violation
                or "payment_collection_requires_prior_supporting_key_evidence" in violation
            ):
                continue
            hint = _reply_repair_hint(violation)
            if hint and hint not in hints:
                hints.append(hint)
        if hints:
            return "本次输出同时违反多项硬事实或结构合同，必须在同一个新 JSON 中全部修正：" + " ".join(hints)
    if "Model JSON missing reply_messages" in error or "Model reply_messages are empty" in error:
        return (
            "reply_messages 不能缺失或为空。即使 Gate 没有候选、工具没有结果或你决定暂停推进，也必须根据当前消息、"
            "完整聊天和权威事实生成至少一条客户可见 text；不采用候选时 selected_content_ids 留空即可。"
        )
    if "invalid_parallel_reply_action" in error:
        return (
            "action 必须逐字使用 none、ask、offer、payment、registration 之一，并与本轮实际可见消息一致。"
            "不要用自定义枚举，也不要用 registration 表示未付客户参加活动。"
        )
    if "invalid_parallel_reply_list_field" in error:
        return (
            "selected_content_ids 和 commit_actions 若输出，必须是 JSON 数组，不能写成字符串或对象；"
            "不需要时直接省略。"
        )
    if (
        "safety_assessment_has_invalid_evidence_ref" in error
        or "party_size_assessment_has_invalid_evidence_ref" in error
    ):
        return (
            "safety_assessment.evidence_refs and party_size_assessment.evidence_refs may only use exact values "
            "from validation_context.valid_customer_message_refs. Do not cite assistant/staff messages. "
            "If there is no valid customer evidence for an assessment, use status=none/unknown and an empty list. "
            "This is a citation-only repair: preserve the valid customer-visible answer and business action, remove only invalid refs, "
            "and do not newly select a content asset, add deposit_evidence, add a payment/registration action, or introduce a new sales step. "
            "Unless the original output already validly used them, keep selected_content_ids and deposit_evidence empty."
        )
    if (
        "payment_assessment_requires_customer_message_evidence" in error
        or "payment_assessment_has_invalid_evidence_ref" in error
        or "payment_assessment_none_requires_empty_evidence" in error
    ):
        return (
            "payment_assessment 只描述本轮客户支付位置。manual_transfer、unverified_paid_claim 和 payment_request "
            "必须逐字引用 valid_customer_message_refs 中的客户消息；none 必须使用空 evidence_refs。"
            "不要为了保留原 action 改写支付状态，也不要引用客服消息冒充客户选择。"
        )
    if "payment_assessment_authoritative_paid_requires_fact" in error:
        return (
            "输入没有权威已付事实，payment_assessment 不能使用 authoritative_paid。"
            "客户普通文字称已转好只能用 unverified_paid_claim 并引用客户原话；同时不得发卡、不得进入 registration。"
        )
    if "payment_assessment_blocks_payment_collection" in error:
        return (
            "你已经把本轮支付位置判断为 manual_transfer 或 unverified_paid_claim，但输出仍在发小程序卡或执行 payment。"
            "必须保持该真实判断：action 改为 none 或确有必要时 ask，selected_content_ids=[]，删除 payment_collection 和候选图片，"
            "并把 deposit_evidence 精确清空为 "
            "{\"offer_prior_turn_refs\":[],\"supporting_key\":\"\",\"supporting_refs\":[],\"current_intent_refs\":[]}。"
            "人工转账或红包只说明客户明确选择的那个渠道，不能同时给多个付款方案；待核对声明的 payment_channel 使用 none，"
            "只说明核对付款记录或请客户补成功截图。"
        )
    if "payment_collection_requires_payment_request_assessment" in error:
        return (
            "payment_collection 只能与 payment_assessment.status=payment_request 一致。"
            "重新根据客户原话判断：索要小程序收款卡、明确问报名/预约/付款可用 payment_request；"
            "明确人工转账必须用 manual_transfer + transfer，明确红包必须用 manual_transfer + red_packet；"
            "普通文字称已转好或红包已发但无权威事实必须用 unverified_paid_claim + none。"
            "不要为了保留卡片把后两类改写成 payment_request；若不是小程序付款请求，就撤销 payment、清空候选和 deposit_evidence。"
        )
    if "manual_transfer_requires_manual_payment_channel" in error:
        return (
            "payment_assessment.status=manual_transfer 时必须保留客户明确选择的唯一人工渠道："
            "转账使用 payment_channel=transfer，微信红包使用 payment_channel=red_packet。"
            "不得同时说明两个渠道，不得发送 payment_collection，也不得把红包静默改成转账。"
        )
    if "unverified_paid_claim_requires_no_channel" in error:
        return (
            "客户普通文字声称已经转账或已经发红包仍只是待核对声明。"
            "保持 payment_assessment.status=unverified_paid_claim，payment_channel 改为 none；"
            "不得发卡、不得重复提供付款渠道、不得声称到账或进入已付登记。"
        )
    if "payment_card_requires_payment_request_assessment" in error:
        return (
            "payment_channel=payment_card 只能与 payment_assessment.status=payment_request、action=payment "
            "和同轮唯一一张 payment_collection 成组出现。只修复这组结构一致性，不新增成交事实。"
        )
    if "payment_request_requires_payment_collection" in error:
        return (
            "你已明确输出 payment_assessment.status=payment_request 且 payment_channel=payment_card，"
            "必须同轮输出唯一一张 payment_collection；若重新阅读完整历史后判断当前并非付款行动信号，"
            "则由 Reply 一并撤销 payment_request、payment_card、payment action 和 deposit_evidence，不能留下半套付款结构。"
        )
    if "registration_action_requires_authoritative_paid_assessment" in error:
        return (
            "registration 只允许 payment_assessment.status=authoritative_paid，且输入必须真实存在 payment_fact:authoritative_paid。"
            "没有该事实就撤销 registration，不得提前收姓名电话或声称已登记。"
        )
    if "invalid_parallel_reply_message_object" in error or "invalid_parallel_reply_message_type" in error:
        return (
            "reply_messages 的每一项必须是包含 type 和 content 的 JSON 对象；type 只能使用输出合同允许的消息类型，"
            "不能输出纯字符串或自定义类型。"
        )
    if "duplicate_payment_collection_in_single_turn" in error:
        return "同一轮最多一张 payment_collection；保留唯一正确金额的卡片，删除其余重复卡片。"
    if (
        "selected_content_id_not_in_gate_candidates" in error
        or "selected_content_id_not_selectable" in error
    ):
        return (
            "selected_content_ids 只能逐字取自输入 allowed_selected_content_ids。"
            "delivery_status=completed 的历史素材不能声明为本轮采用；客户没有明确要求重发时也不要承诺再次发送。"
            "请删除错误中的不可选 ID；仍可使用聊天和权威业务事实自行回答。"
        )
    if "invalid_reply_deposit_supporting_key" in error or "payment_collection_requires_supporting_sales_key" in error:
        return (
            "这次错误说明 supporting_key 枚举或其证据不合法。修复前先按 payment_assessment 的信息特异性重新阅读"
            "原始 current_message：普通文字称已经付好/转好属于待核对声明，明确选择人工转账属于 manual_transfer，"
            "二者都优先于一般 payment_request，并且都必须清空 deposit_evidence、候选和小程序卡。"
            "若原文复核后仍是一般付款请求，才保留 payment_request 和支付动作，只修正证据。"
            "只有实际发送 payment_collection 时才填写 deposit_evidence。supporting_key 只能是 address、effect、"
            "objection 之一，并且要与 supporting_refs 所引用的历史承接一致；时间、忙碌、改天、观望或费用顾虑"
            "都属于 objection 维度，不能自造 time/refusal/price 等枚举。若历史没有真实成立的另一把钥匙，就取消本轮发卡并清空 deposit_evidence；"
            "若证据存在且支付位置复核仍为 payment_request，只修正 supporting_key 和 supporting_refs，不改写客户可见支付方式。"
        )
    if "payment_collection_requires_prior_activity_evidence" in error:
        return (
            "你决定发卡，但 offer_prior_turn_refs 没有引用更早轮次中由客服讲清活动与268元价格的消息，"
            "或没有引用输入允许的 sop_completed 活动事实。offer_prior_turn_refs 必须至少包含一个"
            " prior_assistant_message_refs 中的更早客服原文，或 structured_prior_activity_refs 中的结构化活动交付引用。"
            "这些列表只证明来源和时间；你必须重新阅读原文，确认它确实讲清活动，不能把案例、门店或普通寒暄误当活动介绍。"
            "如果不存在，就取消本轮 payment_collection 和 action=payment，只先讲活动。"
        )
    if (
        "selected_content_delivery_missing" in error
        and "payment_collection_requires_prior_supporting_key_evidence" in error
    ):
        return (
            "组合修复：上一版已经同时声明采用内容资产和发送预约金，但结构素材与预约金证据没有一次补齐。"
            "先重新阅读 current_message 并按支付位置的信息特异性核对上一版 payment_assessment：已付/转好待核对声明和"
            "人工转账选择优先于一般 payment_request，不能为了补候选素材而保留错误的小程序通道。只有复核后仍是"
            "一般付款请求或明确索要收款卡，才保留上一版 payment_request 和支付动作；不要把真实索要收款卡误改成人工转账。"
            "再用 validation_context.prior_message_options 和 structured_delivered_assets 核对更早真实交付，"
            "由你判断哪条客服消息或已完成资产属于 address、effect 或 objection；若另一把钥匙确实已交付，"
            "supporting_refs 引用对应客服消息或结构资产即可，不要求客户另行确认。"
            "然后二选一：如果继续采用 selected_content_ids 中的候选，严格按"
            " content_candidate_delivery_requirements 一次输出它要求的全部 image/video/store_address/"
            "payment_collection，并补齐 content_asset:<id>；如果不采用整套候选，就删除该 ID，但仍可在真实证据"
            "成立时保留 action=payment、自然文字和一张 payment_collection。若历史没有真实交付另一把钥匙，"
            "则取消 payment、清空 deposit_evidence，改为补最有价值的缺口。当前未权威已付时，不得改成"
            " registration，也不得声称已经登记或已经留好名额。提交前一次性复核所有原错误，不要只修第一项。"
        )
    if "payment_collection_requires_prior_supporting_key_evidence" in error:
        return (
            "你决定发卡，但 supporting_refs 尚未证明地址、效果或卡点排疑中的另一把钥匙已在更早轮次真实交付。"
            "补证据前必须先由你重新阅读 current_message 核对支付位置：已付/转好待核对声明和人工转账选择优先于"
            "一般 payment_request；若上一版把更具体的支付位置误归成 payment_request，应纠正状态、撤销小程序卡并清空"
            " deposit_evidence。只有复核后仍是一般付款请求或明确索要收款卡，这才是单纯证据引用修复，此时必须保留"
            " payment_request 和支付通道，不得把‘把收款卡发我/发卡给我’改写成人工转账。"
            "如果上一版 sales_judgment 已确认该维度更早完成交付，且本次唯一相关错误只是缺少引用，"
            "本次 repair 不得重新审理该业务结论，也不得仅因漏写 ref 改成 offer、pause 或取消收款卡；应从"
            " validation_context.prior_message_options 或 structured_delivered_assets 中找到真实的更早交付 ref，"
            "并追加到原 supporting_refs。current_message 只证明本轮行动，不能替代另一把钥匙的历史交付。"
            "只有历史中确实不存在任何对应的真实交付时，才取消本轮发卡，先补最有价值的一把钥匙。"
        )
    if "payment_collection_requires_current_action_signal_evidence" in error:
        return (
            "你决定发卡时，current_intent_refs 必须包含 current_message，证明本轮客户明确在问付款/报名、同意参加"
            "或同意留名额；如果当前消息没有行动信号，就取消 payment_collection 和 action=payment。"
        )
    if "deposit_evidence_requires_payment_action" in error:
        return (
            "deposit_evidence 只用于 action=payment 的本轮成交审计，不能和 offer、ask、none 或 registration 并存。"
            "先检查当前消息是不是客户普通文字声称‘付好了/转好了’：如果没有平台转账成功事件、成功截图或实时订单已付，"
            "这不是新的付款行动信号，绝对不能重新发卡、不能进入 registration，也不能把它改写成仍需付款；应清空 "
            "deposit_evidence，使用 none 或确有必要时 ask，请客户发成功截图或说明会结合付款记录核对。这里的清空必须是精确对象："
            "{\"offer_prior_turn_refs\":[],\"supporting_key\":\"\",\"supporting_refs\":[],\"current_intent_refs\":[]}；"
            "不要在 current_intent_refs 中保留 current_message。此时也必须把 selected_content_ids 精确清空为 []，"
            "删除仅为这些候选添加的 content_asset:<id> 引用，不交付候选中的图片或 payment_collection；"
            "不能一边说等待核对，一边继续声明采用 deposit_close。"
            "先保留你对真实上下文的业务判断：如果这些引用确实证明更早活动、另一把钥匙和当前行动都成立，且无硬禁区，"
            "就改为 action=payment 并同轮交付一张 payment_collection；如果你并未认定条件齐全，就清空 deposit_evidence。"
            "代码不会替你判断客户心理，也不要为了通过校验编造引用。"
        )
    if "selected_content_requires_used_fact_ref" in error:
        return (
            "这是旧版内容引用错误。新合同不再要求模型输出 used_fact_refs；"
            "只核对 selected_content_ids 是否来自允许候选并已完整交付，不要新增销售动作或客户承诺。"
        )
    if "completed_content_repeat_requires_current_customer_ref" in error:
        return (
            "你重复采用了已完成交付的内容资产，但没有证明这是当前客户本轮明确请求或当前问题所需。"
            "如果上一版已经正确回答了客户本轮明确询问的同一内容，请保持客户可见回复和其他审计字段不变；"
            "否则删除该资产选择，"
            "自然承接客户当前问题，不要机械复读。这是引用元数据修复，不要借此重做销售决定。"
        )
    if "selected_content_delivery_missing" in error:
        return (
            "你在 selected_content_ids 中声明采用了 Gate 候选，但没有输出该候选的结构素材。"
            "错误会一次列出所有已选候选缺少的 required，请全部处理。请重新检查当前客户问题和硬边界："
            "如果候选适合本轮，优先保持上一版业务决定，并按错误中的 required 以及 "
            "validation_context.content_candidate_delivery_requirements 一次补齐真实的 "
            "image/video/store_address/payment_collection；如果候选与当前付款方式、已付、风险或客户状态冲突，"
            "就从 selected_content_ids 删除该候选，并保持回复内容与本轮实际决定一致。"
            "如果你只是依据权威业务事实自行回答或发送合法预约金卡、并不准备交付候选的整套素材，优先删除该候选 ID；"
            "仍可独立输出合法 payment_collection，不需要为了发卡强行选择 deposit_close。"
            "若你因此新增 payment_collection，还必须同时填写合法 deposit_evidence：更早活动引用、另一把钥匙的更早真实交付引用、"
            "current_message 行动信号。仅引用历史事实而不重发资产时，不要选择该资产。"
            "未付且本轮不发送 payment_collection 时，不得提前索要姓名或手机号。"
            "代码只会原样补齐你已明确选择并引用的当前 Gate 图片或视频；不会替你选择资产，"
            "也不会自动追加或删除 payment_collection、store_address 或客户可见文字。"
        )
    if "planned_store_lookup_requires_store_delivery" in error:
        return (
            "Tool Planner 已明确规划门店查询，且本轮权威 store_resolution_fact 已要求交付门店卡。"
            "请从错误中的 required_store_ids 逐个输出 "
            '`{"type":"store_address","content":{"store_id":"真实ID"}}`，不能把 content 写成门店名或地址字符串，'
            "也不能只在文字里提门店名或说稍后再发；"
            "这些 ID 必须全部发送且不得增加其他门店。"
        )
    if "planned_store_lookup_delivery_ids_mismatch" in error:
        return (
            "本轮门店卡与权威 store_resolution_fact 不一致。请删除现有 store_address，"
            "只按错误中的 required_store_ids 逐个输出 "
            '`{"type":"store_address","content":{"store_id":"真实ID"}}`，不得遗漏或增加其他门店，'
            "content 不能是门店名或地址字符串。"
        )
    if "precision_reply_passive_mainline_closure" in error:
        return "精准支线问题已经回答到点，但收尾不能等待客户许可。请删除“如果您想/如果您愿意/我可以继续/要不要/是否需要”等表达，直接落一个主线动作：问城市或区域、主动接活动、发案例、推进预约金或登记。没有真实图片或门店卡事实时，直接问城市/区或接活动，不要承诺稍后发。"
    if "precision_reply_missing_mainline_action" in error:
        return "精准支线问题不能只答疑后停住。请保留当前问题的正面回答，再补一条明确主线动作句：问城市或区域、主动接活动名额、发案例、推进预约金或登记到店时间。动作句要具体、像微信销售，不要写“继续处理/安排下一步/如果您想”。"
    if "precision_reply_weak_one_session_confidence" in error:
        return "客户问只能淡或一次效果时，先明确说当前淡斑效果活动价就是268元、绝大多数客户都是一次就好；有真实 case_facts 时同轮发效果图，再说明完成线上活动登记后可到线下门店免费做皮肤检测并由门店结合具体情况讲解。不要以因人而异、可能需要多次、需要看深浅和时间开头，不主动说单次单部位，也不要直接送客。"
    if "payment_collection_blocked_by_health_risk_hold" in error:
        return "客户近期有健康/过敏高风险，未到店检测确认适配前不要输出 payment_collection；只确认检测、门店或时间。"
    if "payment_collection_blocked_by_payment_action" in error:
        return "planner 的 payment_action 表示本轮不直接发预约金入口时，不要输出 payment_collection，也不要在 text 里说马上发入口；改成自然承接、询问是否需要重发或推进下一步。"
    if "payment_collection_blocked_by_precision_qa_boundary" in error:
        return "当前是精准问答边界：不支持项目不能发预约金卡；手脸/两个部位问题不能把部位当同行人数发卡。先把当前边界答清，再自然回到淡斑主线，不要承诺本轮发入口。"
    if "payment_collection_requires_activity_intro" in error:
        return "客户还没有看到完整活动报价/预约金规则时，不要输出 payment_collection，也不要说入口或卡片已发，不要写“付好截图发我”。先用自然话术补活动价268、每位10元预约金到店抵扣、未做或不满意可退，再用“您确认按这个活动参加的话，我马上给您发小程序收款卡”这类封闭式动作承接。"
    if "payment_action_requires_payment_collection" in error:
        return (
            "你已结构声明 action=payment，但本轮没有同时输出 payment_collection。先核验该 payment 是否真的成立："
            "offer_prior_turn_refs 必须来自 validation_context.structured_prior_activity_refs，或来自"
            " validation_context.prior_message_options 中当前消息之前且确实讲清活动与268元的客服原文；"
            "当前轮的 content_asset:<id> 不能冒充更早活动证据。另一把钥匙还必须有 prior_assistant_message_refs 中的更早客服消息，"
            "或 structured_prior_supporting_refs 中的结构化已完成资产作为真实交付引用。"
            "如果这些引用齐全且没有任何硬禁区，说明销售决定本身有效："
            "repair 必须保留 payment 并补齐卡片，不能改成 none/ask/offer 来逃避结构错误，也不能再问客户是否需要入口。"
            "如果 selected_content_ids 中采用了候选，还要一次输出 validation_context.content_candidate_delivery_requirements"
            " 中该候选要求的全部真实 image/video/store_address/payment_collection，并保持合法 deposit_evidence。"
            "若更早活动引用无效、另一把钥匙没有更早真实交付或命中硬禁区，就撤销 payment，删除卡片和发卡承诺，"
            "清空 deposit_evidence，并按当前真实上下文选择合法 action；不能为了补结构而制造提前发卡。"
        )
    if "registration_action_requires_paid_context" in error:
        return (
            "你已结构声明 action=registration，但本轮没有权威预约金已付事实。registration 只用于已付后收姓名、电话、门店和到店意向，"
            "不能表示未付客户参加活动。请重新根据当前问题决定合法动作，不得虚构已付或提前进入登记。"
            "只有 validation_context.prior_assistant_message_refs 中存在你从原文确认的活动介绍，或 structured_prior_activity_refs 有真实活动交付，"
            "并且另一把钥匙和当前行动信号也已经齐全，"
            "才可改为 action=payment，并一次输出 deposit_close 候选要求的真实 text、"
            "image 和 payment_collection，补齐对应 content_asset:<id> 引用与合法 deposit_evidence；或者删除该候选 ID，"
            "仅按权威交易事实输出自然 text、payment_collection 和合法 deposit_evidence。"
            "没有任何更早活动证据时必须使用 offer、ask 或 none，不得发卡，也不得索要姓名手机号。"
            "不要保留候选 ID 却漏素材。"
        )
    if (
        "unpaid_registration_details_requested_before_payment" in error
        or "unpaid_registration_claim_before_payment" in error
    ):
        return (
            "当前没有权威预约金已付事实，本轮也没有发送收款卡。姓名和手机号只在支付成功后登记；"
            "删除索要姓名、名字或手机号，以及声称已经帮客户登记、保留或留住名额的句子。"
            "如果上一版 payment_assessment 是 manual_transfer 或 unverified_paid_claim，本次只修正未付状态措辞，"
            "必须保留原支付通道、none/ask 动作、空 deposit_evidence 和无卡结构；绝对不能改成 payment_request、"
            "deposit_close 或小程序收款卡。可以条件式说明核对到账后再继续登记或保留资格，但不能写成当前已经完成。"
            "重新阅读当前客户原话：若客户只是普通文字声称已经付好或转好，这仍是待核对声明，"
            "不得重复发卡；请客户发截图，或说明会结合平台付款记录核对。"
            "只有当前客户是在开始付款、报名或预约，而不是声称已经付款，并且预约金证据确实齐全时，"
            "才直接发送一张 payment_collection。若上一版 deposit_evidence 已经同时给出更早活动、"
            "另一把钥匙和 current_message，并且当前原话是要求保留活动而非声称已付，"
            "应保留这些真实引用，改为 action=payment 并发送卡；只改写错误的完成态文字，不要删除卡片、"
            "不要清空 deposit_evidence，也不要改选 activity_offer 来逃避成交。文字把付款条件和结果连在一起，"
            "例如‘付10元预约金就能把活动名额留住’，不能写‘给您先留活动名额/先帮您留着’。"
            "若是首次活动咨询，则只讲活动价值。"
            "不要把客户和客服的动作主客体写反：不能让客户‘把活动名额发来/把名额给我’，"
            "也不能要求客户提供一个并不存在的‘活动名额’对象。应直接说明当前真实活动或下一步真实动作。"
            "不得创造口头登记或让客户先回复资料的中间步骤。"
        )
    if "registration_confirmation_fact_required" in error:
        return (
            "本轮没有权威已付、订单或登记完成事实，不能说已经登记、已经留好名额、已经安排或已经预约。"
            "逐字删除‘可以，先给您留着’、‘我先帮您留着’、‘先给您留好’这类付前完成态；"
            "如果本轮同时发送预约金卡，改成条件与结果相连的真实表述，例如‘付10元预约金就能把活动名额留住’，"
            "不要只删卡片或改掉已经成立的 payment 决策。"
            "请改成尚未完成的真实状态：如果历史活动报价已完成且客户明确要留名额，可选择 action=payment 并同轮输出一张 payment_collection；"
            "如果只是解释规则，则使用与实际文字一致的 action，不要把将来动作写成已经完成。"
        )
    if "payment_collection_required" in error:
        return "如果 payment_action=send_now、文本承诺发送预约金入口或 next_step=send_deposit，必须同时输出 payment_collection；否则删除发入口承诺并调整回复节奏。"
    if "payment_collection_amount_text_mismatch" in error:
        return "预约金卡片金额必须和文本一致；同行按每位10元锁活动名额，2位说一共20元，3位说一共30元，4位说一共40元。"
    if "payment_confirmation_fact_required" in error:
        return "当前没有成功支付截图、平台转账成功事件或实时订单已付事实。客户口头说已转账时，不得重复发卡、不得进入已付登记或索要姓名电话；请客户发转账截图，或说明会结合平台付款记录核对。不能说已收到、已到账、已核款或支付已确认。"
    if "offer_total_tail_amount_conflict" in error:
        return (
            "活动总价是268元。10元预约金计入总价，到店做时再付剩余258元；"
            "不能写成‘到店抵扣258元’，也不能把258说成最终总价、全部费用或一共只付258元。"
        )
    if "payment_participant_count_confirm_required" in error:
        return "客户同行人数超过4位时不要发送 payment_collection；改成 text 确认一共几位到店，或说明多人同行先由门店承接确认。"
    if "human_handoff_notice" in error:
        return "需要内部关注时，先用客户可见 text 正面承接当前问题，再追加 human_handoff_notice；客户可见文字应自然完整，不要只输出内部通知。"
    if "ambiguous_deposit_refund_wording" in error or "legacy_deposit_refund_policy" in error:
        return "预约金口径统一为“每位10元锁活动名额，到店抵扣；未做或不满意可退，实际按付款记录核对”。不要承诺自动退款、即时到账、具体退款金额或处理时效。"
    if "case_context_must_not_use_activity_intro_image" in error:
        return "本轮客户在问效果或案例，且已有 case_facts 案例图片事实。必须回答效果顾虑，并且如输出 image，只能使用 case_facts 里的 image_url；不要输出活动宣传图。"
    if "case_image_required_for_effect_turn" in error:
        return "本轮客户在问效果或案例，且已有 case_facts 案例图片事实。必须先用 text 肯定效果方向，再追加 1 条 case_facts.image_url 的 image。"
    if "effect_reply_confidence_order_required" in error:
        return "效果疑问要先明确当前淡斑效果活动价268元、绝大多数客户都是一次就好，并用真实案例建立信任；完成线上活动登记后可到门店免费做皮肤检测并听取具体情况讲解。不要第一句就说因人而异、可能需要多次、不保证或具体看个人情况。"
    if "effect_absolute_safety_claim" in error:
        return "效果和安全顾虑可以积极承接，允许‘一般不会反黑’和‘多数客户反馈都比较正常’这类信心表达；只避免明确的绝对、保证或100%安全承诺，再自然接到店检测或当前主线。"
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
    if "invalid_store_fact_integrity" in error:
        return (
            "本轮候选门店的结构事实存在地区冲突，必须删除对应 store_address 卡片，不能沿用该门店。"
            "只使用剩余 store_fact_integrity=valid 的真实候选重写；如果没有合法候选，不要输出“继续处理”或让客户重复同一地址，"
            "已解析到城市就问客户这个城市哪个区方便，或周边哪里常去；已解析到区县/乡镇就问更常去哪个市区/商圈。语气要像微信真人，不要说“不乱发、不确定的位置、真实门店、重新对”。"
        )
    if "unsupported_store_address_message" in error:
        return "store_address 卡片的 store_id 必须来自本轮门店工具事实或请求里明确确认的门店 ID；没有匹配门店事实时不要输出 store_address，按 store_resolution_fact 区分必要位置补充、当地无店或查询未完成，不能一律继续追问地址。"
    if "store_cards_not_allowed_when_location_clarification_required" in error:
        return "本轮 store_resolution_fact 要求 clarify_location。删除所有 store_address，只问一个确实会改变门店结果的最小必要位置字段；如果事实已经确认某省或地区没有门店，不得继续追问该范围内更具体的位置。"
    if "store_cards_not_allowed_for_province_only_scope" in error:
        return "客户当前只给了省份，且没有可靠的城市、区县或定位事实。删除所有 store_address，不按省中心猜门店；只自然追问具体城市、区县或请客户发定位。"
    if "store_address_message_required_when_reply_promises_location_card" in error:
        return "你在文本里承诺了发送地址或位置卡，但本轮没有对应门店卡。只有 store_resolution_fact.status=send_single/send_multiple 时，才按 delivery_store_ids 追加对应 store_address；其他状态必须删除发卡承诺，并严格按门店事实决定是补充必要位置、说明当地无店或等待事实恢复。"
    if "store_cards_not_allowed_when_service_area_clarification_required" in error:
        return "客户当前地址已经确认，完整查询后该范围目前没有可发送的合法门店。删除所有 store_address 和发卡承诺，如实、简短地说明对应省、市或地区当前没有门店；不要继续问该范围内的区县或商圈，不要让客户重复提供同一位置，也不要承诺重新查找其他门店。"
    if "store_scope_incomplete_system_disclaimer" in error or "store_scope_incomplete_unsupported_location_options" in error:
        return "本轮门店范围事实加载不完整，不能断言没有门店，也不能自行列出事实中没有的城市。删除系统免责话；客户位置已经足够时不得重复索要地址，也不得承诺稍后重新找店。当前问题只有门店且无合法事实可答时，使用非业务等待回复。"
    if "distance_value_not_customer_visible" in error:
        return "距离和驾车时间只用于内部排序门店。客户可见最多说“按您这个位置，这家相对近一些”，不要输出公里、分钟、车程、路线或步行时长。"
    if "distance_fact_required" in error:
        return "没有完整的门店排序事实和 customer_claim_level=relative_near 时，不要输出最近、离您最近、较近、就近或交通方便等排序表达。只使用已有门店事实，并按当前主线自然承接。"
    if "nearby_store_claim_without_location_fact" in error:
        return (
            "没有客户定位、门店工具或距离排序事实时，不要说‘附近门店/离您近’，也不要换成‘我帮您看下门店’这类"
            "没有实际交付的承诺。重新看当前客户是否真的在请求匹配门店：如果没有，只删除整条可选门店承诺并保留原问题的"
            "有效回答，action 与 selected_content_ids 同步保持最小；如果客户当前确实要求匹配门店，则直接问一个城市、区县或定位。"
            "不要仅因 Gate 提名了门店资产就新增门店步骤。"
        )
    if "available_time_fact_required" in error:
        return "available_time 工具失败、超时或没有返回可用 slots 时，不要说有空、可以约、有时间或有名额；只能说明暂时没查到实时档期，并继续确认门店/时间或让门店核对。如果本轮是效果/案例图场景且已有 case_facts，请删除所有旧历史里的今天/明天/几点、几位、预约金、锁名额表达，改成“当前淡斑效果活动价268元、绝大多数客户一次就好 + 发送 case_facts.image_url + 登记后可到门店免费检测并听取具体情况讲解”。"
    if "appointment_confirmation_fact_required" in error:
        return "available_time 只表示目标时段目前可选，不代表已经留位、改约或安排成功。普通预约可问“这个时间方便吗”；已有旧预约的改约场景只输出一条：“这个时间目前可以，您确认要改到这个时间吗？”。删除其他“继续核对/先按这个时间/帮您改过去/帮您留/锁定/安排/记上/预约成功”表达。"
    if "too_many_appointment_time_options" in error:
        return "档期回复最多只能给 1 个推荐时间和 1 个备选时间。请基于 recommended_slot 和 backup_slots 重写，不要列完整时间表。"
    if "unfinished_appointment_lookup_promise" in error:
        return "没有真实 available_time 档期事实时，不要说“查档期/核对档期/看档期/可约时间”。如果本轮已有门店事实，只回答门店位置并引导客户选择区域或门店；如果缺门店或具体时间，只问客户补一个关键字段。"
    if "unfinished_tool_promise_after_tool_execution" in error:
        return "本轮工具已经执行完，不能再说“马上查、帮您查一下、帮您找案例、稍后给您”。请直接基于已有事实回答；如果事实不足，只问客户补一个关键字段，或说明当前没有可发事实。"
    return ""

def _filter_unsupported_media(
    messages: list[dict[str, Any]],
    state: AgentState,
    warnings: list[Any],
) -> list[dict[str, Any]]:
    allowed_images = _case_image_urls(state)
    allowed_videos = _strategy_media_urls(state, "video_urls", "video_url")
    filtered: list[dict[str, Any]] = []
    removed_urls: list[str] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        message_type = str(item.get("type") or "")
        if message_type not in {"image", "video"}:
            filtered.append(item)
            continue
        url = _message_url(item.get("content"))
        allowed_urls = allowed_images if message_type == "image" else allowed_videos
        if url and _normalize_image_url(url) in allowed_urls:
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

def _filter_unsupported_images(
    messages: list[dict[str, Any]],
    state: AgentState,
    warnings: list[Any],
) -> list[dict[str, Any]]:
    """Backward-compatible name; the safety filter now covers image and video media."""
    return _filter_unsupported_media(messages, state, warnings)

def _case_image_urls(state: AgentState) -> set[str]:
    fact_envelope = state.get("fact_envelope") if isinstance(state.get("fact_envelope"), dict) else {}
    structured = fact_envelope.get("structured_facts") if isinstance(fact_envelope.get("structured_facts"), dict) else {}
    urls: set[str] = set()
    for item in structured.get("case_facts") or []:
        if not isinstance(item, dict):
            continue
        url = _normalize_image_url(str(item.get("image_url") or "").strip())
        if url:
            urls.add(url)
    joined = state.get("evidence_join") if isinstance(state.get("evidence_join"), dict) else {}
    for candidate in joined.get("content_candidates") or []:
        if not isinstance(candidate, dict):
            continue
        for message in candidate.get("messages") or []:
            if not isinstance(message, dict) or str(message.get("type") or "") != "image":
                continue
            url = _message_url(message.get("content"))
            if url:
                urls.add(_normalize_image_url(url))
    activity_url = _normalize_image_url(activity_intro_image_url(state))
    if activity_url:
        urls.add(activity_url)
    urls.update(_strategy_media_urls(state, "image_urls", "image_url"))
    return urls

def _strategy_media_urls(state: AgentState, plural_key: str, singular_key: str) -> set[str]:
    urls: set[str] = set()
    for item in state.get("cardpoint_candidates") or []:
        if not isinstance(item, dict):
            continue
        values = item.get(plural_key) if isinstance(item.get(plural_key), list) else []
        if not values and item.get(singular_key):
            values = [item.get(singular_key)]
        urls.update(_normalize_image_url(str(value)) for value in values if str(value or "").strip())
    return urls

def _normalize_image_url(value: str) -> str:
    return str(value or "").strip().replace("&amp;", "&")

def _message_url(content: Any) -> str:
    if isinstance(content, dict):
        return str(content.get("url") or content.get("image_url") or "").strip()
    return str(content or "").strip()

def _renumber(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, item in enumerate(messages, start=1):
        result.append({**item, "order": index})
    return result

def _materialize_required_store_delivery(
    messages: list[dict[str, Any]],
    state: AgentState,
) -> tuple[list[dict[str, Any]], bool]:
    """Materialize only current-turn verified store-card structures.

    The tool has already resolved and permission-checked these IDs. This helper
    does not choose a store, alter Reply text or infer customer intent.
    """

    resolution = _structured_facts(state).get("store_resolution_fact")
    if not isinstance(resolution, dict):
        return list(messages), False
    status = str(resolution.get("status") or "")
    if status not in {"send_single", "send_multiple"}:
        return list(messages), False
    required_ids = list(
        dict.fromkeys(
            str(item or "").strip()
            for item in resolution.get("delivery_store_ids") or []
            if str(item or "").strip()
        )
    )
    expected_count = (
        1
        if status == "send_single"
        else len(required_ids)
        if resolution.get("allow_broad_scope_delivery")
        else min(3, len(required_ids))
    )
    required_ids = required_ids[:expected_count]
    if len(required_ids) != expected_count or not required_ids:
        return list(messages), False

    emitted_ids = [
        str((item.get("content") or {}).get("store_id") or "").strip()
        for item in messages
        if isinstance(item, dict)
        and str(item.get("type") or "") == "store_address"
        and isinstance(item.get("content"), dict)
    ]
    if emitted_ids == required_ids:
        return list(messages), False

    first_card_index = next(
        (
            index
            for index, item in enumerate(messages)
            if isinstance(item, dict) and str(item.get("type") or "") == "store_address"
        ),
        -1,
    )
    without_cards = [
        item
        for item in messages
        if not (isinstance(item, dict) and str(item.get("type") or "") == "store_address")
    ]
    if first_card_index < 0:
        first_text_index = next(
            (
                index
                for index, item in enumerate(without_cards)
                if isinstance(item, dict) and str(item.get("type") or "") == "text"
            ),
            -1,
        )
        insert_at = first_text_index + 1 if first_text_index >= 0 else 0
    else:
        insert_at = min(first_card_index, len(without_cards))
    cards = [
        {"type": "store_address", "content": {"store_id": store_id}}
        for store_id in required_ids
    ]
    return [*without_cards[:insert_at], *cards, *without_cards[insert_at:]], True

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
    messages, _ = _dedupe_payment_collection_messages(messages)
    return _normalize_payment_amount_text_messages(messages)

def _dedupe_payment_collection_messages(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    output: list[dict[str, Any]] = []
    seen_payment = False
    changed = False
    for item in messages:
        if not isinstance(item, dict):
            changed = True
            continue
        if str(item.get("type") or "") == "payment_collection":
            if seen_payment:
                changed = True
                continue
            seen_payment = True
        output.append(item)
    return _renumber(output), changed

def _normalize_payment_amount_text_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    amount = _first_payment_collection_amount(messages)
    if amount <= 10:
        return _renumber(messages)
    output: list[dict[str, Any]] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "") != "text":
            output.append(item)
            continue
        content = item.get("content")
        if isinstance(content, dict):
            text = normalize_payment_amount_text(str(content.get("text") or ""), amount)
            output.append({**item, "content": {**content, "text": text}})
        else:
            output.append({**item, "content": normalize_payment_amount_text(str(content or ""), amount)})
    return _renumber(output)

def _first_payment_collection_amount(messages: list[dict[str, Any]]) -> int:
    for item in messages:
        if not isinstance(item, dict) or str(item.get("type") or "") != "payment_collection":
            continue
        content = item.get("content")
        if not isinstance(content, dict):
            continue
        try:
            amount = int(float(str(content.get("amount") or "").strip()))
        except (TypeError, ValueError):
            return 10
        return amount
    return 10

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
