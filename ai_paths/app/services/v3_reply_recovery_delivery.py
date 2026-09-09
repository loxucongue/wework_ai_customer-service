from __future__ import annotations

from typing import Any

from app.services.memory_store import CustomerMemoryStore
from app.services.storage import AppRepository


_TERMINAL_DELIVERY_STATUSES = {"send_succeeded", "send_failed", "partial_failed"}


class V3ReplyRecoveryDeliveryFinalizer:
    """Reconcile the terminal platform receipt for an out-of-band V3 recovery.

    The recovery worker can only know that the platform accepted a submission.
    This finalizer closes the later delivery lifecycle without attempting another
    send: terminal failures become an auditable manual-review state.
    """

    def __init__(
        self,
        repository: AppRepository,
        memory_store: CustomerMemoryStore | None = None,
    ) -> None:
        self.repository = repository
        self.memory_store = memory_store

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
        expected = "recovered" if delivery_status == "send_succeeded" else "manual_review"
        actual = str(result.get("status") or "")
        if actual != expected:
            raise ValueError(
                f"recovery delivery transition incomplete: expected {expected}, got {actual or '<empty>'}"
            )
        if delivery_status == "send_succeeded":
            self._record_confirmed_sales_stage(
                dispatch=dispatch,
                dispatch_id=dispatch_id,
                request_id=request_id,
            )
        return result

    def _record_confirmed_sales_stage(
        self,
        *,
        dispatch: dict[str, Any],
        dispatch_id: str,
        request_id: str,
    ) -> None:
        """Persist Reply's declared text stage only after confirmed delivery.

        Callback payloads and worker reconciliation may carry different amounts
        of dispatch detail.  The durable dispatch is authoritative because it
        contains the exact source context captured before the send.
        """

        if self.memory_store is None:
            return
        stored = self.repository.get_message_dispatch(dispatch_id)
        source = stored if isinstance(stored, dict) and stored else dispatch
        context = source.get("source_context") if isinstance(source.get("source_context"), dict) else {}
        if not bool(context.get("memory_persist_allowed")):
            return
        sales_contact_key = str(context.get("sales_contact_key") or "").strip()
        stage_record = (
            context.get("sales_stage_record")
            if isinstance(context.get("sales_stage_record"), dict)
            else {}
        )
        stage = str(stage_record.get("stage") or "").strip()
        action_type = str(stage_record.get("action_type") or "").strip()
        if not sales_contact_key or stage != "activity_offer" or action_type != "explain_activity":
            return
        self.memory_store.record_sales_stage_delivered(
            sales_contact_key,
            stage=stage,
            action_type=action_type,
            request_id=request_id,
            interface_version="v3",
        )


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
