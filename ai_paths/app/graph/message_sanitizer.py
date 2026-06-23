from __future__ import annotations

from typing import Any

from app.graph.state import AgentState


PLACEHOLDER_TERMS = ("XX号", "xx号", "某路", "某街", "某大厦", "某商场")


def sanitize_unsupported_placeholder_text(
    messages: list[dict[str, Any]],
    state: AgentState,
    warnings: list[Any] | None = None,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    changed = False
    for message in messages:
        if not isinstance(message, dict) or str(message.get("type") or "") != "text":
            output.append(message)
            continue
        text = _text_content(message.get("content"))
        if not _has_placeholder(text):
            output.append(message)
            continue
        replacement = _fact_store_address_text(state) or _generic_store_card_text(messages)
        output.append({**message, "content": {"text": replacement}})
        changed = True
    if changed and warnings is not None:
        warnings.append(
            {
                "node": "message_sanitizer",
                "message": "unsupported_placeholder_text_replaced",
                "detail": {"terms": [term for term in PLACEHOLDER_TERMS if any(term in _text_content(item.get("content")) for item in messages if isinstance(item, dict))]},
            }
        )
    return _renumber(output)


def _has_placeholder(text: str) -> bool:
    return any(term in text for term in PLACEHOLDER_TERMS)


def _text_content(content: Any) -> str:
    if isinstance(content, dict):
        return str(content.get("text") or "")
    return str(content or "")


def _fact_store_address_text(state: AgentState) -> str:
    structured = _structured_facts(state)
    for store in _fact_stores(structured):
        name = str(store.get("name") or "").strip()
        address = str(store.get("address") or "").strip()
        if not address or _has_placeholder(address):
            continue
        parts = []
        parts.append(f"{name}地址：{address}" if name else f"门店地址：{address}")
        hours = str(store.get("business_hours") or "").strip()
        if hours and not _has_placeholder(hours):
            parts.append(f"营业时间{hours}")
        return "，".join(parts) + "。门店卡片我也发您，点开可以导航。"
    recommended = structured.get("recommended_store") if isinstance(structured.get("recommended_store"), dict) else {}
    name = str(recommended.get("name") or "").strip()
    address = str(recommended.get("address") or "").strip()
    if address and not _has_placeholder(address):
        return (f"{name}地址：{address}" if name else f"门店地址：{address}") + "。门店卡片我也发您，点开可以导航。"
    return ""


def _fact_stores(structured: dict[str, Any]) -> list[dict[str, Any]]:
    stores = structured.get("store_facts") if isinstance(structured.get("store_facts"), list) else []
    return [store for store in stores if isinstance(store, dict)]


def _structured_facts(state: AgentState) -> dict[str, Any]:
    fact_envelope = state.get("fact_envelope") if isinstance(state.get("fact_envelope"), dict) else {}
    structured = fact_envelope.get("structured_facts") if isinstance(fact_envelope.get("structured_facts"), dict) else {}
    return structured


def _generic_store_card_text(messages: list[dict[str, Any]]) -> str:
    has_store_card = any(isinstance(item, dict) and str(item.get("type") or "") == "store_address" for item in messages)
    if has_store_card:
        return "门店位置卡片我发您，点开可以查看地址和导航。"
    return "这家门店的详细地址我还需要核对一下，您发下具体区域我帮您确认。"


def _renumber(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**item, "order": index} for index, item in enumerate(messages, start=1)]
