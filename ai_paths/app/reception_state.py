"""Strict wire contract for reception notifications; no sales decisions."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator

PositiveID = Annotated[StrictInt, Field(gt=0, le=9007199254740991)]


class ReceptionData(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    service_mode: StrictInt
    is_deleted: StrictBool
    ai_version: Literal["v1", "v2", "v3"] | None = None

    @field_validator("service_mode")
    @classmethod
    def valid_mode(cls, value: int) -> int:
        if value not in (1, 2):
            raise ValueError("invalid service mode")
        return value


class ReceptionNotification(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    event_id: Annotated[StrictStr, Field(min_length=1, max_length=64)]
    customer_id: PositiveID
    customer_add_wechat_id: PositiveID
    wecom_corp_id: Annotated[StrictStr, Field(min_length=1, max_length=64)]
    employee_wechat_id: Annotated[StrictStr, Field(min_length=1, max_length=64)]
    customer_external_user_id: Annotated[StrictStr, Field(min_length=1, max_length=128)]
    state_version: PositiveID
    occurred_at: PositiveID
    data: ReceptionData

    @field_validator("event_id", "wecom_corp_id", "employee_wechat_id", "customer_external_user_id")
    @classmethod
    def clean_identifier(cls, value: str) -> str:
        if value != value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError("invalid identifier")
        return value


class ReceptionConflict(Exception):
    """Only stable reason codes, never customer payloads."""

