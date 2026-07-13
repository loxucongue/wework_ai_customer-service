from __future__ import annotations

from typing import Any


CONTEXTUAL_SHORT_MESSAGES = {
    "人呢",
    "在吗",
    "还在吗",
    "怎么不回",
    "可以",
    "可以的",
    "好",
    "好的",
    "嗯",
    "行",
    "那就这家",
    "就这家",
    "再发一下",
    "没收到",
    "明天",
    "下午",
    "三点",
    "3点",
    "报名",
    "发吧",
    "发我",
    "等会儿",
    "这家",
    "那家",
    "刚刚那家",
    "刚才那家",
}


def is_contextual_short_message(content: str) -> bool:
    text = "".join(str(content or "").split())
    return text in CONTEXTUAL_SHORT_MESSAGES


def short_message_context_for_model(
    *,
    content: str,
    conversation_history: list[Any],
    sent_message_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not is_contextual_short_message(content):
        return {}
    last_assistant = _last_assistant_text(conversation_history)
    output: dict[str, Any] = {"is_contextual_short_message": True}
    if last_assistant:
        output["pending_context"] = f"上一轮助手回复：{last_assistant[:220]}"
        output["last_reply_summary"] = last_assistant[:160]
        if "?" in last_assistant or "？" in last_assistant:
            output["last_assistant_question"] = last_assistant[:160]
    special = _last_special_message(sent_message_summary or {})
    if special:
        output["last_special_message"] = special
    if len(output) == 1:
        output["needs_recent_context"] = True
    return output


def _last_assistant_text(history: list[Any]) -> str:
    for item in reversed(history or []):
        if isinstance(item, dict):
            role = str(item.get("role") or item.get("direction") or "").lower()
            if role and role not in {"assistant", "staff", "service", "bot"}:
                continue
            content = item.get("content")
            text = str(content.get("text") if isinstance(content, dict) else content or "").strip()
            if text:
                return text
            continue
        raw = str(item or "").strip()
        for prefix in ("小贝:", "小贝：", "客服:", "客服：", "助手:", "助手：", "AI回复:", "AI回复："):
            if raw.startswith(prefix):
                return raw[len(prefix) :].strip()
    return ""


def _last_special_message(summary: dict[str, Any]) -> str:
    parts: list[str] = []
    if summary.get("payment_collection_sent"):
        frequency = summary.get("payment_collection") if isinstance(summary.get("payment_collection"), dict) else {}
        today_count = frequency.get("today_count")
        prior_count = frequency.get("prior_count")
        last_sent_at = str(frequency.get("last_sent_at") or "").strip()
        if isinstance(today_count, int) and isinstance(prior_count, int):
            detail = f"预约金卡发送证据：今天{today_count}次、此前{prior_count}次"
        else:
            detail = "历史有预约金卡发送记录，具体日期不确定"
        if last_sent_at:
            detail += f"，最近记录时间{last_sent_at}"
        parts.append(detail)
    store_ids = summary.get("store_address_sent_by_store_id")
    if isinstance(store_ids, list) and store_ids:
        parts.append(f"最近发过门店位置卡：{', '.join(str(item) for item in store_ids[:4])}")
    if summary.get("activity_intro_image_sent"):
        parts.append("最近发过活动宣传图")
    return "；".join(parts)
