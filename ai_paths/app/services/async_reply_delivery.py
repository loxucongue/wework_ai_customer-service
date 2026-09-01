from __future__ import annotations

from typing import Any

from app.chat_runtime_helpers import safe_repository_call
from app.services.memory_store import CustomerMemoryStore
from app.services.storage import AppRepository


class AsyncReplyDeliveryFinalizer:
    """Persist customer-visible Reply facts after confirmed platform delivery."""

    def __init__(self, repository: AppRepository, memory_store: CustomerMemoryStore) -> None:
        self.repository = repository
        self.memory_store = memory_store

    def finalize(self, dispatch: dict[str, Any]) -> None:
        if str(dispatch.get("status") or "") != "send_succeeded":
            return
        context = dispatch.get("source_context") if isinstance(dispatch.get("source_context"), dict) else {}
        reply_messages = dispatch.get("reply_messages") if isinstance(dispatch.get("reply_messages"), list) else []
        conversation_id = str(dispatch.get("conversation_id") or "").strip()
        assistant_request_id = str(
            context.get("assistant_request_id") or dispatch.get("source_request_id") or ""
        ).strip()
        if conversation_id and assistant_request_id and reply_messages:
            safe_repository_call(
                self.repository.add_assistant_message,
                conversation_id=conversation_id,
                request_id=assistant_request_id,
                reply_messages=reply_messages,
            )
        if not bool(context.get("memory_persist_allowed")):
            return
        sales_contact_key = str(context.get("sales_contact_key") or "").strip()
        if not sales_contact_key:
            return
        case_record = context.get("case_image_record") if isinstance(context.get("case_image_record"), dict) else {}
        if case_record.get("document_ids"):
            self.memory_store.record_case_images_sent(
                sales_contact_key,
                document_ids=case_record.get("document_ids") or [],
                image_urls=case_record.get("image_urls") or [],
                request_id=str(dispatch.get("source_request_id") or ""),
            )
        activity_record = (
            context.get("activity_intro_record")
            if isinstance(context.get("activity_intro_record"), dict)
            else {}
        )
        if activity_record.get("image_url"):
            self.memory_store.record_activity_intro_image_sent(
                sales_contact_key,
                image_url=str(activity_record.get("image_url") or ""),
                request_id=str(dispatch.get("source_request_id") or ""),
                send_mode=str(activity_record.get("send_mode") or "async"),
            )
        store_record = context.get("store_fact_record") if isinstance(context.get("store_fact_record"), dict) else {}
        for item in store_record.get("records") or []:
            if isinstance(item, dict) and isinstance(item.get("store"), dict):
                self.memory_store.record_store_fact(
                    sales_contact_key,
                    store=item["store"],
                    event_type=str(item.get("event_type") or "store_address_sent"),
                    request_id=str(dispatch.get("source_request_id") or ""),
                )
