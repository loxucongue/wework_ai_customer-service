from __future__ import annotations

from typing import Any


LOCATION_CARD_HEADER = "\u3010\u5ba2\u6237\u53d1\u9001\u5b9a\u4f4d\u5361\u7247\u3011"
LOCATION_TITLE_LABEL = "\u6807\u9898"
LOCATION_ADDRESS_LABEL = "\u5730\u5740"
LOCATION_COORDINATES_LABEL = "\u5750\u6807"
LOCATION_ZOOM_LABEL = "\u5730\u56fe\u7f29\u653e"


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
        lines.append(f"{LOCATION_TITLE_LABEL}\uff1a{title}")
    if address:
        lines.append(f"{LOCATION_ADDRESS_LABEL}\uff1a{address}")
    if coordinates:
        lines.append(f"{LOCATION_COORDINATES_LABEL}\uff1a{coordinates}")
    if zoom:
        lines.append(f"{LOCATION_ZOOM_LABEL}\uff1a{zoom}")
    if not lines:
        return ""
    return "\n".join([LOCATION_CARD_HEADER, *lines])


def append_location_card_to_content(content: str, request_context: dict[str, Any] | None) -> tuple[str, dict[str, str]]:
    normalized = _string(content)
    card = location_card_from_context(request_context)
    fact_text = location_card_fact_text(card)
    if not fact_text or fact_text in normalized:
        return normalized, card
    if not normalized:
        return fact_text, card
    return f"{normalized}\n{fact_text}", card


def _string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()
