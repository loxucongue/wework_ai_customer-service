from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class ChatRequest(BaseModel):
    content: str = ""
    customer_id: str
    corp_id: str
    conversation_history: list[str] = Field(default_factory=list)
    conversation_history_count: int | None = None
    file_image: str | None = None
    user_id: int | None = None
    wechat: str | None = None
    external_userid: str | None = None
    customer_add_wechat_id: str | int | None = None
    confirmed_store_id: str | int | None = None
    confirmed_store_name: str | None = None
    store_id: str | int | None = None
    store_name: str | None = None
    appointment_id: str | int | None = None
    appointment_time: str | None = None
    request_context: dict[str, Any] = Field(default_factory=dict)


class ReplyMessage(BaseModel):
    type: Literal["text", "image", "video", "human_handoff", "human_handoff_notice", "payment_collection", "store_address"] = "text"
    order: int
    content: str | dict[str, Any]


class ChatResponse(BaseModel):
    request_id: str
    reply_messages: list[ReplyMessage]
    scene: str = ""
    intent: str = ""
    subflow: str = ""
    trace_url: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class MessageDeliveryCallbackItem(BaseModel):
    client_message_id: str = Field(min_length=1, max_length=255)
    message_index: int | None = None
    platform_message_id: str = ""
    status: Literal["sending", "send_succeeded", "send_failed"]
    sent_at: str = ""
    error_code: str = ""
    error_message: str = ""


class MessageDeliveryCallback(BaseModel):
    event_id: str = Field(min_length=1, max_length=255)
    dispatch_id: str = Field(min_length=1, max_length=255)
    task_id: str = ""
    status: Literal["sending", "send_succeeded", "send_failed", "partial_failed"]
    occurred_at: str = ""
    platform_request_id: str = ""
    system_msgid: str = ""
    retryable: bool = False
    error_code: str = ""
    error_message: str = ""
    items: list[MessageDeliveryCallbackItem] = Field(default_factory=list)


class ConversationModeChangedEvent(BaseModel):
    event_id: str = Field(min_length=1, max_length=255)
    event_type: Literal["conversation_mode_changed"] = "conversation_mode_changed"
    occurred_at: str = Field(min_length=1, max_length=64)
    sequence: int | None = Field(default=None, ge=0)
    corp_id: str = Field(min_length=1, max_length=255)
    wechat: str = Field(min_length=1, max_length=255)
    external_userid: str = Field(min_length=1, max_length=255)
    customer_id: str = Field(min_length=1, max_length=255)
    conversation_id: str = Field(default="", max_length=512)
    from_mode: Literal["ai", "human"]
    to_mode: Literal["ai", "human"]
    reason_code: str = Field(default="", max_length=255)
    operator_id: str = Field(default="", max_length=255)
    operator_name: str = Field(default="", max_length=255)

    @model_validator(mode="after")
    def validate_transition(self) -> "ConversationModeChangedEvent":
        if self.from_mode == self.to_mode:
            raise ValueError("from_mode and to_mode must be different")
        return self


class CozeKbItem(BaseModel):
    content: str
    document_id: str = ""


class CozeKbResult(BaseModel):
    kb_name: str
    items: list[CozeKbItem] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)
