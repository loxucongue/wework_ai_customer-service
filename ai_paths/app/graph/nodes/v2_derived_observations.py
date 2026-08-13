from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


ASSISTANT_ROLES = {"assistant", "staff", "ai"}
CUSTOMER_ROLES = {"customer", "user"}


def build_v2_derived_observations(
    *,
    conversation: list[dict[str, Any]],
    history_events: list[dict[str, Any]],
    current_message: dict[str, Any],
) -> dict[str, Any]:
    """Build source-referenced observations without creating business predicates.

    Every value is either copied from an append-only event/model observation or
    calculated directly from timestamps. Nothing here routes a turn or labels a
    customer, objection, intent, stage, or recommended action.
    """

    return {
        "schema_version": "v2_derived_observations_v1",
        "recent_asset_deliveries": _recent_asset_deliveries(history_events),
        "recent_assistant_messages": _recent_assistant_messages(conversation),
        "reply_timing": _reply_timing(conversation, current_message),
        "prior_model_observations": _prior_model_observations(history_events),
        "authority": (
            "Raw measurements and prior model observations only. Current customer text and "
            "authoritative facts always override them. They never control routing or actions."
        ),
    }


def _recent_asset_deliveries(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    supported = {
        "case_image_sent",
        "activity_intro_image_sent",
        "store_address_sent",
        "payment_collection_sent",
        "sop_pack_sent",
    }
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for event in events or []:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("event_type") or "").strip()
        if event_type not in supported:
            continue
        facts = event.get("facts") if isinstance(event.get("facts"), dict) else {}
        identity = str(
            facts.get("sop_pack_id")
            or facts.get("store_id")
            or facts.get("image_url")
            or facts.get("request_id")
            or ""
        ).strip()
        grouped.setdefault((event_type, identity), []).append(event)

    output: list[dict[str, Any]] = []
    for (event_type, identity), items in grouped.items():
        latest = max(items, key=lambda item: _event_timestamp(item) or float("-inf"))
        event_id = str(latest.get("event_id") or latest.get("id") or "").strip()
        source_ref = event_id or ":".join(
            part
            for part in (
                event_type,
                identity,
                str(latest.get("event_time") or latest.get("created_at") or ""),
            )
            if part
        )
        facts = latest.get("facts") if isinstance(latest.get("facts"), dict) else {}
        output.append(
            {
                "event_type": event_type,
                "asset_identity": identity,
                "delivery_count": len(items),
                "last_delivered_at": str(
                    latest.get("event_time")
                    or latest.get("created_at")
                    or latest.get("timestamp")
                    or ""
                ),
                "last_delivery_facts": {
                    key: facts.get(key)
                    for key in (
                        "sop_pack_id",
                        "message_types",
                        "store_id",
                        "image_url",
                        "image_urls",
                        "amount",
                        "request_id",
                    )
                    if facts.get(key) not in (None, "", [], {})
                },
                "source_refs": [f"history_event:{source_ref}"],
            }
        )
    output.sort(key=lambda item: str(item.get("last_delivered_at") or ""), reverse=True)
    return output[:12]


def _recent_assistant_messages(conversation: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for item in reversed(conversation or []):
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or "").strip().lower() not in ASSISTANT_ROLES:
            continue
        ref = str(item.get("message_ref") or "").strip()
        output.append(
            {
                "content": str(item.get("content") or ""),
                "sent_at": item.get("sent_at") or item.get("timestamp") or item.get("created_at"),
                "source_ref": ref,
            }
        )
        if len(output) == 2:
            break
    return output


def _reply_timing(
    conversation: list[dict[str, Any]],
    current_message: dict[str, Any],
) -> dict[str, Any]:
    current_at = _parse_datetime(current_message.get("sent_at"))
    if current_at is None:
        return {}
    previous: dict[str, Any] | None = None
    for item in reversed(conversation or []):
        if not isinstance(item, dict):
            continue
        item_at = _parse_datetime(item.get("sent_at") or item.get("timestamp") or item.get("created_at"))
        if item_at is None or item_at > current_at:
            continue
        previous = item
        break
    if previous is None:
        return {}
    previous_at = _parse_datetime(
        previous.get("sent_at") or previous.get("timestamp") or previous.get("created_at")
    )
    if previous_at is None:
        return {}
    return {
        "seconds_since_previous_message": max(0, int((current_at - previous_at).total_seconds())),
        "previous_message_role": str(previous.get("role") or ""),
        "source_refs": [
            str(previous.get("message_ref") or ""),
            "current_message",
        ],
    }


def _prior_model_observations(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for event in reversed(events or []):
        if not isinstance(event, dict) or str(event.get("event_type") or "") != "v2_reply_model_observation":
            continue
        facts = event.get("facts") if isinstance(event.get("facts"), dict) else {}
        event_id = str(event.get("event_id") or event.get("id") or "").strip()
        output.append(
            {
                "primary_objective": str(facts.get("primary_objective") or "")[:500],
                "customer_friction_observation": str(
                    facts.get("customer_friction_observation") or ""
                )[:500],
                "observed_at": str(event.get("event_time") or event.get("created_at") or ""),
                "source_ref": f"history_event:{event_id}" if event_id else "",
                "authority": "prior_model_observation_not_customer_fact",
            }
        )
        if len(output) == 2:
            break
    return output


def _event_timestamp(event: dict[str, Any]) -> float | None:
    parsed = _parse_datetime(
        event.get("event_time") or event.get("created_at") or event.get("timestamp")
    )
    return parsed.timestamp() if parsed is not None else None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) or str(value or "").strip().isdigit():
        try:
            number = float(value)
            if number > 10_000_000_000:
                number /= 1000
            parsed = datetime.fromtimestamp(number, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    else:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
