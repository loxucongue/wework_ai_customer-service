from __future__ import annotations

from typing import Any

from app.graph.state import AgentState
from app.policies.business_rules import load_business_rules


def activity_intro_image_url(state: AgentState) -> str:
    rules = state.get("business_rules") if isinstance(state.get("business_rules"), dict) else load_business_rules()
    offer = rules.get("offer") if isinstance(rules.get("offer"), dict) else {}
    return str(offer.get("activity_intro_image_url") or "").strip()


def append_activity_intro_image(
    messages: list[dict[str, Any]],
    state: AgentState,
    warnings: list[Any] | None = None,
) -> list[dict[str, Any]]:
    rules = state.get("business_rules") if isinstance(state.get("business_rules"), dict) else load_business_rules()
    offer = rules.get("offer") if isinstance(rules.get("offer"), dict) else {}
    url = str(offer.get("activity_intro_image_url") or "").strip()
    if not url or _messages_contain_image(messages, url):
        return messages
    policy = offer.get("activity_intro_image_policy") if isinstance(offer.get("activity_intro_image_policy"), dict) else {}
    send_once = bool(policy.get("send_once", True))
    if send_once and _activity_intro_image_sent(state, url):
        return messages
    sub_rule_ids = {str(item).strip() for item in policy.get("sub_rule_ids", []) if str(item).strip()}
    resend_terms = [str(item).strip() for item in policy.get("resend_terms", []) if str(item).strip()]
    sub_rule_id = str(state.get("planner_sub_rule_id") or "").strip()
    content = str(state.get("normalized_content") or state.get("content") or "")
    should_send = (sub_rule_id in sub_rule_ids) or any(term and term in content for term in resend_terms)
    if not should_send:
        return messages
    output = _renumber([*messages, {"type": "image", "order": len(messages) + 1, "content": {"url": url}}])
    if warnings is not None:
        warnings.append(
            {
                "message": "activity_intro_image_appended",
                "detail": {"url": url, "sub_rule_id": sub_rule_id},
            }
        )
    return output


def _messages_contain_image(messages: list[dict[str, Any]], url: str) -> bool:
    target = url.strip()
    if not target:
        return False
    return any(
        str(item.get("type") or "") == "image" and _message_url(item.get("content")) == target
        for item in messages
        if isinstance(item, dict)
    )


def _activity_intro_image_sent(state: AgentState, url: str) -> bool:
    summary = state.get("sent_message_summary") if isinstance(state.get("sent_message_summary"), dict) else {}
    if bool(summary.get("activity_intro_image_sent")):
        return True
    for event in state.get("history_events") or []:
        if not isinstance(event, dict):
            continue
        if str(event.get("event_type") or "") == "activity_intro_image_sent":
            return True
    for item in state.get("conversation_history") or []:
        text = str(item or "")
        if url in text or "anniversary-268.jpg" in text:
            return True
    return False


def _message_url(content: Any) -> str:
    if isinstance(content, dict):
        return str(content.get("url") or content.get("image_url") or "")
    return str(content or "")


def _renumber(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for index, item in enumerate(messages, start=1):
        if not isinstance(item, dict):
            continue
        output.append({**item, "order": index})
    return output
