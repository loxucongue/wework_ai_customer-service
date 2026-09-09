from __future__ import annotations

from typing import Any

from app.services.storage import AppRepository


_TERMINAL_DELIVERY_STATUSES = {"send_succeeded", "send_failed", "partial_failed"}


class V3ReplyRecoveryDeliveryFinalizer:
    """Reconcile the terminal platform receipt for an out-of-band V3 recovery.

    The recovery worker can only know that the platform accepted a submission.
    This finalizer closes the later delivery lifecycle without attempting another
    send: terminal failures become an auditable manual-review state.
    """

    def __init__(self, repository: AppRepository) -> None:
        self.repository = repository

    def finalize(self, dispatch: dict[str, Any]) -> dict[str, Any]:
        source_kind = str(dispatch.get("source_kind") or "").strip()
        if source_kind != "v3_reply_recovery":
            raise ValueError(f"unexpected recovery delivery source_kind: {source_kind or '<empty>'}")

        delivery_status = str(dispatch.get("status") or "").strip().lower()
        if delivery_status not in _TERMINAL_DELIVERY_STATUSES:
            raise ValueError(f"recovery delivery is not terminal: {delivery_status or '<empty>'}")

        dispatch_id = str(dispatch.get("id") or "").strip()
        request_id = str(
            dispatch.get("source_request_id")
            or dispatch.get("source_task_id")
            or (
                dispatch.get("source_context", {}).get("original_request_id")
                if isinstance(dispatch.get("source_context"), dict)
                else ""
            )
            or ""
        ).strip()
        if not dispatch_id or not request_id:
            raise ValueError("recovery delivery requires dispatch_id and source_request_id")

        result = self.repository.finalize_v3_recovery_delivery(
            request_id=request_id,
            dispatch_id=dispatch_id,
            delivery_status=delivery_status,
            error=_delivery_error(dispatch),
        )
        if not bool(result.get("found")):
            raise LookupError(f"recovery run not found: {request_id}")
        if str(result.get("status") or "") in {"dispatch_mismatch", "state_conflict"}:
            raise ValueError(str(result.get("reason") or result.get("status") or "recovery mismatch"))
        return result


def _delivery_error(dispatch: dict[str, Any]) -> str:
    values = [
        str(dispatch.get("error_code") or "").strip(),
        str(dispatch.get("error_message") or "").strip(),
    ]
    for item in dispatch.get("items") or []:
        if not isinstance(item, dict) or str(item.get("status") or "") != "send_failed":
            continue
        values.extend(
            (
                str(item.get("error_code") or "").strip(),
                str(item.get("error_message") or "").strip(),
            )
        )
    return "; ".join(dict.fromkeys(value for value in values if value))[:2000]
