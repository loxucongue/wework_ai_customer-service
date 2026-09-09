from __future__ import annotations

import re
from typing import Any

from app.graph.nodes.material_selection import parallel_reply_payload
from app.graph.nodes.reply_validation import (
    _validate_no_placeholder_facts,
    _validate_parallel_claimed_deposit_evidence,
    _validate_parallel_appointment_confirmation_facts,
    _validate_parallel_business_hours_facts,
    _validate_parallel_media_facts,
    _validate_parallel_payment_boundaries,
    _validate_parallel_selected_content_delivery,
    _validate_structured_delivery_promises,
    _validate_unconfirmed_store_availability_claim,
    _validate_store_address_message_facts,
    _validate_store_delivery_text_matches_cards,
    _validate_store_resolution_delivery_mode,
    _validate_store_resolution_contract,
    message_content_text,
)
from app.graph.nodes.sales_fact_validation import (
    validate_customer_visible_identity_boundaries,
    validate_sales_price_fact_boundaries,
)
from app.services.customer_payment_state import is_completed_order, is_inactive_order


def validate_model_led_reply_admission(messages: list[dict[str, Any]], state: dict[str, Any]) -> None:
    """Validate structures and externally consequential factual claims.

    Reply owns meaning, sales posture, and wording.  The checks below only
    compare emitted structures, model-cited provenance, and narrow completed
    state claims with authoritative runtime facts.
    """

    violations: list[str] = []
    checks = (
        lambda: _validate_structured_delivery_conversation_shape(messages),
        lambda: _validate_no_placeholder_facts(messages),
        lambda: _validate_selected_content_provenance(state),
        lambda: _validate_parallel_claimed_deposit_evidence(messages, state),
        lambda: _validate_parallel_payment_boundaries(messages, state),
        lambda: _validate_parallel_media_facts(messages, state),
        lambda: _validate_parallel_selected_content_delivery(messages, state),
        lambda: _validate_structured_delivery_promises(messages, state),
        lambda: _validate_store_resolution_contract(messages, state),
        lambda: _validate_store_resolution_delivery_mode(messages, state),
        lambda: _validate_store_address_message_facts(
            messages,
            state,
            check_visible_text=False,
        ),
        # This is a structure-delivery check, not a sales-intent keyword route:
        # once Reply itself promises to send an address/location card, the same
        # payload must contain an authorized store_address structure.
        lambda: _validate_store_delivery_text_matches_cards(messages, state),
        lambda: _validate_parallel_appointment_confirmation_facts(messages, state),
        lambda: _validate_parallel_business_hours_facts(messages, state),
        lambda: validate_customer_visible_identity_boundaries(messages),
        lambda: validate_sales_price_fact_boundaries(messages),
        lambda: _validate_mainline_sales_action(state),
        lambda: _validate_customer_visible_mainline_boundary(messages, state),
        lambda: _validate_unrelated_historical_store_claim(messages, state),
        lambda: _validate_completed_store_scope_requery(messages, state),
        lambda: _validate_terminal_store_distance_objection(messages, state),
        lambda: _validate_unconfirmed_store_availability_claim(messages, state),
    )
    for check in checks:
        try:
            check()
        except (TypeError, ValueError) as exc:
            detail = str(exc).strip()
            if detail and detail not in violations:
                violations.append(detail)
    if violations:
        raise ValueError("reply_admission_violations::" + ";;".join(violations))


def _validate_mainline_sales_action(state: dict[str, Any]) -> None:
    """Keep the model's declared next action inside delivered-stage bounds.

    This validates an explicit enum against deterministic delivery facts.  It
    does not infer sales intent from customer words or inspect visible reply
    copy.  The one permitted repair remains responsible for making the model's
    wording match a valid action.
    """

    if not isinstance(state.get("evidence_join"), dict):
        return
    mainline = (
        state.get("mainline_delivery_state")
        if isinstance(state.get("mainline_delivery_state"), dict)
        else parallel_reply_payload(state).get("mainline_delivery_state", {})
    )
    allowed = {
        str(item or "").strip()
        for item in mainline.get("allowed_next_sales_action_types") or []
        if str(item or "").strip()
    }
    if not allowed:
        return
    sales = (
        state.get("reply_sales_judgment")
        if isinstance(state.get("reply_sales_judgment"), dict)
        else {}
    )
    next_action = (
        sales.get("next_sales_action")
        if isinstance(sales.get("next_sales_action"), dict)
        else {}
    )
    action_type = str(next_action.get("type") or "").strip()
    policy = (
        state.get("reply_policy_decision")
        if isinstance(state.get("reply_policy_decision"), dict)
        else {}
    )
    closing = (
        policy.get("closing_decision")
        if isinstance(policy.get("closing_decision"), dict)
        else {}
    )
    customer_state = str(closing.get("customer_state") or "").strip()
    if customer_state in {"continue_sales", "pause_current_turn"} and not action_type:
        raise ValueError("next_sales_action_required")
    if customer_state == "pause_current_turn" and action_type in {
        "invite_booking",
        "send_payment",
    }:
        raise ValueError("paused_turn_cannot_advance_transaction")
    if customer_state == "hard_stop_marketing" and action_type and action_type != "stop":
        raise ValueError("hard_stop_requires_stop_action")
    if customer_state == "post_payment_service" and action_type not in {
        "post_payment_service",
        "keep_open",
        "ask_missing_fact",
    }:
        raise ValueError("post_payment_requires_service_action")
    if customer_state in {"continue_sales", "pause_current_turn"} and action_type not in allowed:
        raise ValueError(
            "next_sales_action_exceeds_delivered_mainline:"
            f"{action_type}:{mainline.get('next_missing_stage') or ''}"
        )


def _validate_terminal_store_distance_objection(
    messages: list[dict[str, Any]],
    state: dict[str, Any],
) -> None:
    """Keep a completed same-city recommendation from looping on location.

    Router owns whether the current turn is a distance objection. This check
    only combines that structured decision with the persisted store-search
    boundary; it never classifies customer prose or chooses a sales response.
    """

    joined = state.get("evidence_join") if isinstance(state.get("evidence_join"), dict) else {}
    route = joined.get("semantic_route") if isinstance(joined.get("semantic_route"), dict) else {}
    if not _route_has_distance_objection(route, joined):
        return
    recommendation = _latest_store_recommendation(joined)
    if not _recommendation_is_terminal(recommendation):
        return
    if recommendation.get("clarification_would_change_result") is True:
        return

    sales = (
        state.get("reply_sales_judgment")
        if isinstance(state.get("reply_sales_judgment"), dict)
        else {}
    )
    next_action = (
        sales.get("next_sales_action")
        if isinstance(sales.get("next_sales_action"), dict)
        else {}
    )
    if str(next_action.get("type") or "").strip() in {"ask_missing_fact", "send_store"}:
        raise ValueError("terminal_store_distance_objection_same_city_requery")

    text = re.sub(
        r"\s+",
        "",
        "\n".join(
            message_content_text(item.get("content"))
            for item in messages
            if isinstance(item, dict) and str(item.get("type") or "text") == "text"
        ),
    )
    if not text:
        return
    if any(
        marker in text
        for marker in (
            "距离",
            "太远",
            "有点远",
            "确实远",
            "折腾",
            "麻烦",
            "不方便",
            "不太方便",
            "路程",
        )
    ):
        raise ValueError("terminal_store_distance_objection_restates_negative")
    if re.search(
        r"(?:告诉|说|发|回复)(?:我|这边)?[^。！？!?]{0,10}(?:哪个位置|在哪里|在哪儿|哪边|地铁站|路口|楼栋|几号)",
        text,
    ):
        raise ValueError("terminal_store_distance_objection_same_city_requery")


def _validate_unrelated_historical_store_claim(
    messages: list[dict[str, Any]],
    state: dict[str, Any],
) -> None:
    """Do not turn unrelated historical store provenance into a new claim.

    The prompt builder explicitly records whether this turn needs historical
    store evidence.  When it does not, a completed-delivery statement or the
    name of a store that appears only on an inactive order is a provenance
    mismatch, not a sales-language judgement.
    """

    if state.get("_historical_store_context_allowed") is not False:
        return
    text = re.sub(
        r"\s+",
        "",
        "\n".join(
            message_content_text(item.get("content"))
            for item in messages
            if isinstance(item, dict) and str(item.get("type") or "text") == "text"
        ),
    )
    if not text:
        return
    if _claims_historical_store_topic(text):
        raise ValueError("stale_historical_store_topic_leak")
    if any(name in text for name in _inactive_order_store_names(state)):
        raise ValueError("stale_historical_store_topic_leak")


def _validate_completed_store_scope_requery(
    messages: list[dict[str, Any]],
    state: dict[str, Any],
) -> None:
    """Do not ask for finer detail after a complete empty scope lookup.

    The store workflow already determined whether another district or landmark
    can change the result.  This check validates that authoritative terminal
    fact only; it does not classify the customer's objection or choose whether
    Reply should offer another city, an effect example, or another sales step.
    """

    store_fact = parallel_reply_payload(state).get("store_fact_status")
    if not isinstance(store_fact, dict):
        return
    if str(store_fact.get("status") or "").strip() != "no_valid_candidate":
        return
    if store_fact.get("candidate_search_complete") is not True:
        return
    text = re.sub(
        r"\s+",
        "",
        "\n".join(
            message_content_text(item.get("content"))
            for item in messages
            if isinstance(item, dict) and str(item.get("type") or "text") == "text"
        ),
    )
    if re.search(
        r"(?:哪个|哪一个|具体|什么)[^。！？!?]{0,8}"
        r"(?:区域|区县|商圈|地铁站|路口|楼栋|位置)",
        text,
    ):
        raise ValueError("store_scope_confirmed_same_region_requery")


def _claims_historical_store_topic(text: str) -> bool:
    for clause in re.split(r"[，。！？；,.!?;]+", str(text or "")):
        if not clause:
            continue
        if not any(
            marker in clause
            for marker in ("之前", "前面", "刚才", "刚刚", "已经", "已发", "发过")
        ):
            continue
        if any(
            marker in clause
            for marker in ("门店", "店址", "地址", "位置", "定位", "导航", "门店卡")
        ):
            return True
    return False


def _inactive_order_store_names(state: dict[str, Any]) -> set[str]:
    joined = state.get("evidence_join") if isinstance(state.get("evidence_join"), dict) else {}
    shared = joined.get("shared_context") if isinstance(joined.get("shared_context"), dict) else {}
    facts = (
        shared.get("authoritative_facts")
        if isinstance(shared.get("authoritative_facts"), dict)
        else {}
    )
    order_payment = (
        facts.get("orders_and_payment")
        if isinstance(facts.get("orders_and_payment"), dict)
        else {}
    )
    names: set[str] = set()
    for order in order_payment.get("orders") or []:
        if not isinstance(order, dict):
            continue
        expired = str(order.get("paid_protection_status") or "").strip() in {
            "expired",
            "inactive_order_expired",
            "completed_order_expired",
        }
        if not (expired or is_inactive_order(order) or is_completed_order(order)):
            continue
        name = str(order.get("store_name") or "").strip()
        if name:
            names.add(name)
    return names


def _route_has_distance_objection(route: dict[str, Any], joined: dict[str, Any]) -> bool:
    friction = route.get("current_friction") if isinstance(route.get("current_friction"), dict) else {}
    if str(friction.get("status") or "none").strip() == "none":
        return False
    checkpoint = route.get("checkpoint") if isinstance(route.get("checkpoint"), dict) else {}
    recall = joined.get("sales_recall") if isinstance(joined.get("sales_recall"), dict) else {}
    structured_labels: list[str] = [
        str(friction.get(key) or "")
        for key in (
            "checkpoint_code",
            "checkpoint_type_name",
            "checkpoint_tag_name",
            "summary",
        )
    ]
    structured_labels.extend(
        str(checkpoint.get(key) or "")
        for key in ("primary_code", "primary_name", "primary_tag_name", "reason")
    )
    for sequence in recall.get("sequence_candidates") or []:
        if not isinstance(sequence, dict):
            continue
        structured_labels.extend(
            str(sequence.get(key) or "")
            for key in ("sequence_name", "checkpoint_code", "checkpoint_name")
        )
    for candidate in recall.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        checkpoint_type = (
            candidate.get("checkpoint_type")
            if isinstance(candidate.get("checkpoint_type"), dict)
            else {}
        )
        checkpoint_tag = (
            candidate.get("checkpoint_tag")
            if isinstance(candidate.get("checkpoint_tag"), dict)
            else {}
        )
        structured_labels.extend(
            [
                str(candidate.get("checkpoint_code") or ""),
                str(checkpoint_type.get("name") or ""),
                str(checkpoint_tag.get("name") or ""),
            ]
        )
    catalog_text = " ".join(structured_labels).lower()
    return any(marker in catalog_text for marker in ("distance", "距离", "店太远", "路程远", "太远"))


def _latest_store_recommendation(joined: dict[str, Any]) -> dict[str, Any]:
    shared = joined.get("shared_context") if isinstance(joined.get("shared_context"), dict) else {}
    facts = (
        shared.get("authoritative_facts")
        if isinstance(shared.get("authoritative_facts"), dict)
        else {}
    )
    sent = facts.get("sent_messages") if isinstance(facts.get("sent_messages"), dict) else {}
    recommendation = (
        sent.get("latest_store_recommendation")
        if isinstance(sent.get("latest_store_recommendation"), dict)
        else {}
    )
    evidence = (
        recommendation.get("store_search_evidence")
        if isinstance(recommendation.get("store_search_evidence"), dict)
        else recommendation
    )
    return evidence if isinstance(evidence, dict) else {}


def _recommendation_is_terminal(value: dict[str, Any]) -> bool:
    if value.get("recommendation_final_for_destination") is True:
        return True
    return bool(
        value.get("candidate_search_complete") is True
        and (value.get("recommended_store_id") or value.get("delivery_store_ids") or value.get("store_ids"))
    )


def _validate_customer_visible_mainline_boundary(
    messages: list[dict[str, Any]],
    state: dict[str, Any],
) -> None:
    """Reject an explicit booking invitation before that stage is available.

    The model still owns sales wording and progression.  This narrow check only
    prevents customer-visible transaction advancement from contradicting the
    deterministic delivered-mainline contract, even when the model labels its
    structured action as a harmless value-delivery action.
    """

    if not isinstance(state.get("evidence_join"), dict):
        return
    mainline = (
        state.get("mainline_delivery_state")
        if isinstance(state.get("mainline_delivery_state"), dict)
        else parallel_reply_payload(state).get("mainline_delivery_state", {})
    )
    allowed = {
        str(item or "").strip()
        for item in mainline.get("allowed_next_sales_action_types") or []
        if str(item or "").strip()
    }
    policy = (
        state.get("reply_policy_decision")
        if isinstance(state.get("reply_policy_decision"), dict)
        else {}
    )
    closing = (
        policy.get("closing_decision")
        if isinstance(policy.get("closing_decision"), dict)
        else {}
    )
    customer_state = str(closing.get("customer_state") or "").strip()
    if "invite_booking" in allowed and customer_state not in {
        "pause_current_turn",
        "hard_stop_marketing",
    }:
        return
    text = "\n".join(
        message_content_text(item.get("content"))
        for item in messages
        if isinstance(item, dict) and str(item.get("type") or "text") == "text"
    )
    if not text.strip():
        return
    patterns = (
        r"(?:我|这边)?(?:可以|先|现在|马上|直接)?(?:帮|给)您"
        r"(?:做|办|提交)?(?:个|一下)?(?:预约登记|预约)(?:一下|上)?"
        r"(?:吧|哈|哦|呢|呀|[，。！？!?]|$)",
        r"(?:您|你)?(?:可以直接|就可以|直接|先)预约到店(?:就行|即可|可以)?"
        r"(?:吧|呢|呀|[，。！？!?]|$)",
        r"(?:您|你)?(?:大概|准备|打算|想)?"
        r"(?:工作日还是周末|周末还是工作日)(?:方便|过来|到店|来店)?"
        r"(?:呢|吗|呀|吧|[?？])",
        r"(?:您|你)?(?:大概|准备|打算|想)?(?:周末|工作日)"
        r"(?:方便)?(?:过来|到店|来店|来)(?:呢|吗|呀|吧|[?？])",
        r"(?:您|你)?(?:大概|准备|打算|想)?"
        r"(?:哪天|什么时候|几点|几号|什么时间|哪个时间)(?:方便)?"
        r"(?:过来|到店|来店|来)(?:呢|吗|呀|吧|[?？])",
        r"(?:您|你)?(?:大概|准备|打算|想)?(?:过来|到店|来店|来)(?:的)?"
        r"(?:哪天|什么时候|几点|几号|什么时间|哪个时间)(?:方便)?"
        r"(?:呢|吗|呀|吧|[?？])",
    )
    immediate_negation = re.compile(
        r"(?:不用|不需要|无需|不必|不能|不会|无法|暂时不|暂不|先不|"
        r"不建议|不要|不是让您|并非让您|不由)(?:现在|马上|立刻|直接)?$"
    )
    compact = re.sub(r"\s+", "", text)
    for pattern in patterns:
        for match in re.finditer(pattern, compact):
            prefix = compact[max(0, match.start() - 14) : match.start()]
            if immediate_negation.search(prefix):
                continue
            raise ValueError(
                "next_sales_action_exceeds_delivered_mainline:visible_invite_booking:"
                f"{mainline.get('next_missing_stage') or ''}"
            )


def _validate_structured_delivery_conversation_shape(
    messages: list[dict[str, Any]],
) -> None:
    """Require a text turn around customer-visible structured delivery.

    This is a message-shape contract only. It does not inspect text meaning or
    choose the sales action; Reply must produce the complete conversation.
    """

    message_types = {
        str(item.get("type") or "").strip()
        for item in messages
        if isinstance(item, dict)
    }
    structured_types = {"store_address", "payment_collection", "image", "video"}
    if message_types.intersection(structured_types) and "text" not in message_types:
        raise ValueError("structured_delivery_requires_text_message")


def _validate_selected_content_provenance(state: dict[str, Any]) -> None:
    joined = state.get("evidence_join") if isinstance(state.get("evidence_join"), dict) else {}
    allowed_ids = {
        str(item.get("content_id") or item.get("id") or "").strip()
        for item in joined.get("content_candidates") or []
        if isinstance(item, dict)
        and str(item.get("content_id") or item.get("id") or "").strip()
    }
    selected_ids = {
        str(item or "").strip()
        for item in state.get("reply_selected_content_ids") or []
        if str(item or "").strip()
    }
    if not selected_ids.issubset(allowed_ids):
        raise ValueError("selected_content_id_not_nominated")
