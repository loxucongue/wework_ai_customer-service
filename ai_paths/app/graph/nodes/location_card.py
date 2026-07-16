from __future__ import annotations

from typing import Any


LOCATION_CARD_HEADER = "【客户发送定位卡片】"
LOCATION_CARD_PREFIX = "定位卡片："
LOCATION_TITLE_LABEL = "标题"
LOCATION_ADDRESS_LABEL = "地址"
LOCATION_COORDINATES_LABEL = "坐标"
LOCATION_ZOOM_LABEL = "地图缩放"


def location_card_from_context(request_context: dict[str, Any] | None) -> dict[str, str]:
    context = request_context if isinstance(request_context, dict) else {}
    title = _string(context.get("location_title"))
    address = _string(context.get("location_address"))
    coordinates = _string(context.get("location"))
    zoom = _string(context.get("location_zoom"))
    msgtype = _string(context.get("msgtype"))
    if not any((title, address, coordinates, zoom)):
        return {}
    return {
        "msgtype": msgtype,
        "title": title,
        "address": address,
        "coordinates": coordinates,
        "zoom": zoom,
    }


def location_card_from_state(state: dict[str, Any]) -> dict[str, str]:
    context = state.get("request_context") if isinstance(state.get("request_context"), dict) else {}
    return location_card_from_context(context)


def location_card_fact_text(location_card: dict[str, Any] | None) -> str:
    card = location_card if isinstance(location_card, dict) else {}
    lines = []
    title = _string(card.get("title"))
    address = _string(card.get("address"))
    coordinates = _string(card.get("coordinates"))
    zoom = _string(card.get("zoom"))
    if title:
        lines.append(f"{LOCATION_TITLE_LABEL}：{title}")
    if address:
        lines.append(f"{LOCATION_ADDRESS_LABEL}：{address}")
    if coordinates:
        lines.append(f"{LOCATION_COORDINATES_LABEL}：{coordinates}")
    if zoom:
        lines.append(f"{LOCATION_ZOOM_LABEL}：{zoom}")
    if not lines:
        return ""
    return "\n".join([LOCATION_CARD_HEADER, *lines])


def append_location_card_to_content(content: str, request_context: dict[str, Any] | None) -> tuple[str, dict[str, str]]:
    normalized = _string(content)
    card = location_card_from_context(request_context)
    fact_text = location_card_fact_text(card)
    if not fact_text:
        return normalized, card
    normalized = _normalize_location_card_prefix(normalized)
    if fact_text in normalized:
        return normalized, card
    if not normalized:
        return fact_text, card
    return f"{normalized}\n{fact_text}", card


def _normalize_location_card_prefix(content: str) -> str:
    text = _string(content)
    for prefix in ("门店位置：", "门店位置:"):
        if text.startswith(prefix):
            return f"{LOCATION_CARD_PREFIX}{text[len(prefix):].strip()}"
    return text


def _string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()
