from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    content: str = ""
    customer_id: str
    corp_id: str
    conversation_history: list[str] = Field(default_factory=list)
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
    type: Literal["text", "image", "human_handoff", "appointment_push"] = "text"
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


class CozeKbItem(BaseModel):
    content: str
    document_id: str = ""


class CozeKbResult(BaseModel):
    kb_name: str
    items: list[CozeKbItem] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)
