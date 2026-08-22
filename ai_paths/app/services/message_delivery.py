from __future__ import annotations

from copy import deepcopy
import hashlib
from typing import Any
from uuid import uuid4

from app.config import Settings
from app.schemas import MessageDeliveryCallback
from app.services.storage import AppRepository


_PENDING_DELIVERY_STATUSES = {"created", "submitting", "platform_accepted", "submission_unknown", "sending"}
_TERMINAL_DELIVERY_STATUSES = {"send_succeeded", "send_failed", "partial_failed"}


class MessageDeliveryService:
    def __init__(self, settings: Settings, repository: AppRepository) -> None:
        self.settings = settings
        self.repository = repository
        if self.callback_required and not self.callback_url:
            raise RuntimeError(
                "MESSAGE_DELIVERY_CALLBACK_PUBLIC_URL is required when delivery callbacks are enabled"
            )
        if self.callback_required and not str(settings.message_delivery_callback_token or "").strip():
            raise RuntimeError(
                "MESSAGE_DELIVERY_CALLBACK_TOKEN is required when delivery callbacks are enabled"
            )

    @property
    def callback_required(self) -> bool:
        return bool(self.settings.message_delivery_callback_required)

    @property
    def callback_url(self) -> str:
        return str(self.settings.message_delivery_callback_public_url or "").strip()

    def prepare_dispatch(
        self,
        *,
        source_channel: str,
        source_kind: str,
        source_request_id: str,
        source_task_id: str,
        conversation_id: str,
        identity: dict[str, Any],
        plan_id: str,
        task_id: str,
        reply_messages: list[dict[str, Any]],
        source_context: dict[str, Any] | None = None,
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        clean_key = _idempotency_key(
            idempotency_key or f"{source_kind}:{source_task_id or source_request_id or task_id}"
        )
        dispatch = self.repository.create_message_dispatch(
            dispatch_id=str(uuid4()),
            idempotency_key=clean_key,
            source_channel=str(source_channel or "").strip(),
            source_kind=str(source_kind or "").strip(),
            source_request_id=str(source_request_id or "").strip(),
            source_task_id=str(source_task_id or "").strip(),
            conversation_id=str(conversation_id or "").strip(),
            identity=identity,
            plan_id=str(plan_id or "").strip(),
            task_id=str(task_id or "").strip(),
            reply_messages=reply_messages,
            source_context=source_context or {},
        )
        outbound_messages = deepcopy(dispatch.get("reply_messages") or reply_messages)
        items = dispatch.get("items") if isinstance(dispatch.get("items"), list) else []
        item_ids = {
            int(item.get("message_index") or 0): str(item.get("client_message_id") or "")
            for item in items
            if isinstance(item, dict)
        }
        for index, message in enumerate(outbound_messages):
            if isinstance(message, dict) and item_ids.get(index):
                message["client_message_id"] = item_ids[index]
        return {
            "dispatch": dispatch,
            "dispatch_id": str(dispatch.get("id") or ""),
            "reply_messages": outbound_messages,
            "callback_url": self.callback_url,
            "callback_required": self.callback_required,
        }

    def record_submission(
        self,
        dispatch_id: str,
        *,
        status: str,
        platform_request_id: str = "",
        system_msgid: str = "",
        error_code: str = "",
        error_message: str = "",
    ) -> dict[str, Any]:
        return self.repository.update_message_dispatch_submission(
            dispatch_id,
            status=status,
            platform_request_id=platform_request_id,
            system_msgid=system_msgid,
            error_code=error_code,
            error_message=error_message,
        )

    def accept_callback(self, callback: MessageDeliveryCallback) -> dict[str, Any]:
        dispatch = self.repository.get_message_dispatch(callback.dispatch_id)
        if not dispatch:
            raise LookupError(f"unknown dispatch_id: {callback.dispatch_id}")
        if str(dispatch.get("task_id") or "") and callback.task_id:
            if str(dispatch.get("task_id") or "") != str(callback.task_id or ""):
                raise ValueError("callback task_id does not match dispatch")
        if callback.status == "partial_failed" and not callback.items:
            raise ValueError("partial_failed callback requires per-message items")
        result = self.repository.apply_message_delivery_event(callback.model_dump())
        return result

    def mark_finalized(self, dispatch_id: str) -> dict[str, Any]:
        return self.repository.mark_message_dispatch_finalized(dispatch_id)

    @staticmethod
    def is_terminal(dispatch: dict[str, Any]) -> bool:
        return str(dispatch.get("status") or "") in _TERMINAL_DELIVERY_STATUSES

    @staticmethod
    def needs_finalization(dispatch: dict[str, Any]) -> bool:
        return MessageDeliveryService.is_terminal(dispatch) and not str(dispatch.get("finalized_at") or "").strip()

    @staticmethod
    def is_pending(dispatch: dict[str, Any]) -> bool:
        return str(dispatch.get("status") or "") in _PENDING_DELIVERY_STATUSES


def delivery_response_metadata(payload: Any) -> dict[str, str]:
    data = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else {}
    if not isinstance(payload, dict):
        payload = {}
    return {
        "platform_request_id": str(
            data.get("platform_request_id")
            or data.get("request_id")
            or payload.get("platform_request_id")
            or payload.get("request_id")
            or ""
        ).strip(),
        "system_msgid": str(data.get("system_msgid") or payload.get("system_msgid") or "").strip(),
    }


def _idempotency_key(value: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        return f"message-dispatch:{uuid4()}"
    if len(clean) <= 180:
        return clean
    return f"message-dispatch:{hashlib.sha256(clean.encode('utf-8')).hexdigest()}"
