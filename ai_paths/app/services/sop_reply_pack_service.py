from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings


ALLOWED_MESSAGE_TYPES = {"text", "image", "video", "payment_collection", "store_address", "human_handoff"}
ALLOWED_SOP_SCOPES = {"chat_gate", "event_first_add", "event_platform_task"}
ALLOWED_SOP_CATEGORIES = {
    "opening",
    "intro",
    "effect_case",
    "activity_intro",
    "store_prompt",
    "store_address",
    "price_quote",
    "deposit_push",
    "payment_followup",
    "final_close",
    "platform_actions",
}

DEFAULT_SOP_REPLY_PACKS: dict[str, Any] = {
    "version": 1,
    "updated_at": "",
    "packs": [
        {
            "id": "s10_new_customer_opening",
            "enabled": False,
            "scope": "chat_gate",
            "sop_category": "opening",
            "name": "新客破冰",
            "purpose": "新客首次加微后的基础破冰话术包。",
            "order": 10,
            "send_once": True,
            "event_type": "sop_friend_added_schedule_batch",
            "delay_minutes": 1,
            "day_stage": "day1",
            "customer_state": "first_add_ai_notice",
            "stage_tag": "first_add_ai_notice",
            "triggers": ["new_customer_added"],
            "reply_messages": [],
        },
        {
            "id": "s10_need_and_case",
            "enabled": False,
            "scope": "chat_gate",
            "sop_category": "effect_case",
            "name": "需求与效果承接",
            "purpose": "客户首次表达斑点、效果或是否能做时的话术包。",
            "order": 20,
            "send_once": True,
            "event_type": "sop_friend_added_schedule_batch",
            "delay_minutes": 3,
            "day_stage": "day1",
            "customer_state": "first_add_ai_notice",
            "stage_tag": "first_add_ai_notice",
            "triggers": ["first_need", "first_effect_question"],
            "reply_messages": [],
        },
        {
            "id": "s10_activity_intro",
            "enabled": False,
            "scope": "chat_gate",
            "sop_category": "activity_intro",
            "name": "活动介绍",
            "purpose": "客户首次了解活动、价格或预约金时的话术包。",
            "order": 30,
            "send_once": True,
            "event_type": "sop_friend_added_schedule_batch",
            "delay_minutes": 5,
            "day_stage": "day1",
            "customer_state": "first_add_ai_notice",
            "stage_tag": "first_add_ai_notice",
            "triggers": ["first_activity_question", "first_price_question"],
            "reply_messages": [],
        },
        {
            "id": "s10_objection_resolution",
            "enabled": False,
            "scope": "chat_gate",
            "sop_category": "intro",
            "name": "顾虑处理",
            "purpose": "客户担心效果、套路、乱收费、预约金或时间时的话术包。",
            "order": 40,
            "send_once": True,
            "event_type": "sop_friend_added_schedule_batch",
            "delay_minutes": 5,
            "day_stage": "day1",
            "customer_state": "first_add_ai_notice",
            "stage_tag": "first_add_ai_notice",
            "triggers": ["first_objection"],
            "reply_messages": [],
        },
        {
            "id": "s10_deposit_close",
            "enabled": False,
            "scope": "chat_gate",
            "sop_category": "deposit_push",
            "name": "预约金推进",
            "purpose": "客户已有明确兴趣或愿意登记时的话术包。",
            "order": 50,
            "send_once": True,
            "event_type": "sop_friend_added_schedule_batch",
            "delay_minutes": 5,
            "day_stage": "day1",
            "customer_state": "first_add_ai_notice",
            "stage_tag": "first_add_ai_notice",
            "triggers": ["deposit_intent"],
            "reply_messages": [],
        },
    ],
}


EVENT_FIRST_ADD_TEMPLATE_PACKS: list[dict[str, Any]] = [
    {
        "id": "event_s10_intro_1min",
        "enabled": True,
        "scope": "event_first_add",
        "sop_category": "intro",
        "name": "事件-1分钟介绍补发",
        "purpose": "加微后客户未回复时，补发技术和效果铺垫，建立基础信任。",
        "order": 110,
        "send_once": True,
        "event_type": "sop_friend_added_schedule_batch",
        "delay_minutes": 1,
        "day_stage": "day1",
        "customer_state": "first_add_no_reply",
        "stage_tag": "intro_followup",
        "triggers": ["first_add_1min_no_reply", "intro_followup"],
        "reply_messages": [
            {
                "type": "text",
                "order": 1,
                "content": {
                    "text": "亲，给您介绍一下，我们现在做的是肌源调肤点斑技术，随做随走，不影响上班出门。"
                },
            },
            {
                "type": "text",
                "order": 2,
                "content": {
                    "text": "您主要是脸上斑点、晒斑、老年斑，还是色沉痘印这类？我先按您的情况给您看适合的方向。"
                },
            },
        ],
    },
    {
        "id": "event_s10_store_prompt_5min",
        "enabled": True,
        "scope": "event_first_add",
        "sop_category": "store_prompt",
        "name": "事件-5分钟问地址",
        "purpose": "介绍后客户未回复时，追问城市或区域，为门店匹配做铺垫。",
        "order": 120,
        "send_once": True,
        "event_type": "sop_friend_added_schedule_batch",
        "delay_minutes": 5,
        "day_stage": "day1",
        "customer_state": "intro_sent_no_reply",
        "stage_tag": "store_prompt",
        "triggers": ["first_add_5min_no_reply", "store_prompt"],
        "reply_messages": [
            {
                "type": "text",
                "order": 1,
                "content": {
                    "text": "亲，您是在什么城市哪个区呢？我们全国连锁门店比较多，我先帮您看附近方便到店的位置。"
                },
            }
        ],
    },
    {
        "id": "event_s10_effect_warmup_30min",
        "enabled": True,
        "scope": "event_first_add",
        "sop_category": "effect_case",
        "name": "事件-30分钟效果铺垫",
        "purpose": "门店或地址沟通后未回复时，发送效果参考，强化客户价值预期。",
        "order": 130,
        "send_once": True,
        "event_type": "sop_friend_added_schedule_batch",
        "delay_minutes": 30,
        "day_stage": "day1",
        "customer_state": "store_prompt_no_reply",
        "stage_tag": "effect_warmup",
        "triggers": ["effect_warmup", "after_store_no_reply"],
        "reply_messages": [
            {
                "type": "text",
                "order": 1,
                "content": {
                    "text": "亲，您看一下这个是参加活动进来的顾客做完后的参考，主要是淡化黑色素，不伤皮肤。"
                },
            },
            {
                "type": "image",
                "order": 2,
                "content": {
                    "url": "https://wecom.cs.4ba.cn/media-objects/signed/objects/ent-1a4d8d9c8e844b0eb6488975720fce49/e5cceb3ace15992de1843262-c460c3231942f8497d604240.jpg?token=eyJyZXNvdXJjZV90eXBlIjogIm9iamVjdCIsICJyZXNvdXJjZV9pZCI6ICJlbnQtMWE0ZDhkOWM4ZTg0NGIwZWI2NDg4OTc1NzIwZmNlNDkvZTVjY2ViM2FjZTE1OTkyZGUxODQzMjYyLWM0NjBjMzIzMTk0MmY4NDk3ZDYwNDI0MC5qcGciLCAiZXhwIjogMTc4Mjg4ODYzNiwgInNpZyI6ICI4ODM4MDUyYTM0ODViZTYwNzc4MmQ0MWU3MTBjZTc2OWUxM2I0OGVhODdkZjQzYWQ3Y2JiZTkyNjE0YmZmN2ZlIn0%3D"
                },
            },
        ],
    },
    {
        "id": "event_s10_price_quote_60min",
        "enabled": True,
        "scope": "event_first_add",
        "sop_category": "price_quote",
        "name": "事件-60分钟报价",
        "purpose": "客户沉默时补齐活动价、预约金、尾款和可退规则，推进优惠名额。",
        "order": 140,
        "send_once": True,
        "event_type": "sop_friend_added_schedule_batch",
        "delay_minutes": 60,
        "day_stage": "day1",
        "customer_state": "effect_warmup_no_reply",
        "stage_tag": "price_quote",
        "triggers": ["price_quote", "day1_quote"],
        "reply_messages": [
            {
                "type": "text",
                "order": 1,
                "content": {
                    "text": "现在周年庆活动价是268元，线上先付10元预约金锁活动名额，到店抵扣10元，做完再付258。"
                },
            },
            {
                "type": "text",
                "order": 2,
                "content": {
                    "text": "到店先看效果和方案，满意再做；不做的话10元预约金也退，主要是先帮您保留活动价名额。"
                },
            },
            {
                "type": "image",
                "order": 3,
                "content": {
                    "url": "https://wecom.cs.4ba.cn/media-objects/signed/objects/ent-1a4d8d9c8e844b0eb6488975720fce49/0d4a7bcf357ce10aa57be7f7-4ebd0c703a6cbfa257a076a7.jpg?token=eyJyZXNvdXJjZV90eXBlIjogIm9iamVjdCIsICJyZXNvdXJjZV9pZCI6ICJlbnQtMWE0ZDhkOWM4ZTg0NGIwZWI2NDg4OTc1NzIwZmNlNDkvMGQ0YTdiY2YzNTdjZTEwYWE1N2JlN2Y3LTRlYmQwYzcwM2E2Y2JmYTI1N2EwNzZhNy5qcGciLCAiZXhwIjogMTc4Mjg4ODc1NiwgInNpZyI6ICJkM2JiYWQ5ZGVlOWQ3NmYwODUyNDRhZGZhMjFlNmM0MDRlZGRkNWYzY2ZmN2I2N2JjYTQ5MTNiYjFjNzM5MDJjIn0%3D"
                },
            },
        ],
    },
    {
        "id": "event_s10_deposit_push_70min",
        "enabled": True,
        "scope": "event_first_add",
        "sop_category": "deposit_push",
        "name": "事件-70分钟通单收款",
        "purpose": "报价后客户未成交时，轻推预约金入口，完成活动名额锁定。",
        "order": 150,
        "send_once": True,
        "event_type": "sop_friend_added_schedule_batch",
        "delay_minutes": 70,
        "day_stage": "day1",
        "customer_state": "quoted_no_deposit",
        "stage_tag": "deposit_push",
        "triggers": ["deposit_push", "after_quote_no_reply"],
        "reply_messages": [
            {
                "type": "text",
                "order": 1,
                "content": {
                    "text": "亲，我先给您把优惠名额留住，10元只是预约金，到店直接抵扣，不满意也可以退。"
                },
            },
            {
                "type": "payment_collection",
                "order": 2,
                "content": {"amount": 10, "remark": ""},
            },
            {
                "type": "text",
                "order": 3,
                "content": {
                    "text": "付完我这边就按活动价给您登记，后面您有空再安排到店时间就行。"
                },
            },
        ],
    },
    {
        "id": "event_s10_unpaid_effect_1h",
        "enabled": True,
        "scope": "event_first_add",
        "sop_category": "payment_followup",
        "name": "事件-未付款1小时效果跟进",
        "purpose": "客户未付预约金时，继续用效果参考和可退规则降低付款压力。",
        "order": 160,
        "send_once": True,
        "event_type": "sop_friend_added_schedule_batch",
        "delay_minutes": 120,
        "day_stage": "day1",
        "customer_state": "deposit_unpaid_1h",
        "stage_tag": "payment_followup",
        "triggers": ["deposit_unpaid_1h", "effect_followup"],
        "reply_messages": [
            {
                "type": "text",
                "order": 1,
                "content": {
                    "text": "您看下这个改善参考，活动名额先锁住更稳，不限到店时间，不做10元也是退给您的。"
                },
            },
            {
                "type": "image",
                "order": 2,
                "content": {
                    "url": "https://wecom.cs.4ba.cn/media-objects/signed/objects/ent-1a4d8d9c8e844b0eb6488975720fce49/ba9ef713a0eaa163486b819c-db88a9a985655fcb7432c357.jpg?token=eyJyZXNvdXJjZV90eXBlIjogIm9iamVjdCIsICJyZXNvdXJjZV9pZCI6ICJlbnQtMWE0ZDhkOWM4ZTg0NGIwZWI2NDg4OTc1NzIwZmNlNDkvYmE5ZWY3MTNhMGVhYTE2MzQ4NmI4MTljLWRiODhhOWE5ODU2NTVmY2I3NDMyYzM1Ny5qcGciLCAiZXhwIjogMTc4Mjg4ODYzNiwgInNpZyI6ICI3MzIzZWFmNmUyODczZmI0MDA0MTcyZmE3NTczYWIxMGIwZWU4MjI5NjhkZDEwYzczN2Q1NTJlZWM1YjQ3ZTg5In0%3D"
                },
            },
        ],
    },
    {
        "id": "event_s10_unpaid_video_2h",
        "enabled": True,
        "scope": "event_first_add",
        "sop_category": "operation_video",
        "name": "事件-未付款2小时操作视频",
        "purpose": "客户未付预约金且未回复时，发送操作视频缓解效果和安全顾虑。",
        "order": 170,
        "send_once": True,
        "event_type": "sop_friend_added_schedule_batch",
        "delay_minutes": 180,
        "day_stage": "day1",
        "customer_state": "deposit_unpaid_2h",
        "stage_tag": "operation_video",
        "triggers": ["deposit_unpaid_2h", "operation_video"],
        "reply_messages": [
            {
                "type": "text",
                "order": 1,
                "content": {
                    "text": "亲，您看下老师操作视频，过程是比较轻松的，想去可以先放心预约名额。"
                },
            },
            {
                "type": "video",
                "order": 2,
                "content": {
                    "url": "http://wecom.cs.4ba.cn/media-objects/signed/objects/ent-1a4d8d9c8e844b0eb6488975720fce49/973e07398cdaa9cccc5a352a-bab56f91a8660848324dd216.mp4?token=eyJyZXNvdXJjZV90eXBlIjogIm9iamVjdCIsICJyZXNvdXJjZV9pZCI6ICJlbnQtMWE0ZDhkOWM4ZTg0NGIwZWI2NDg4OTc1NzIwZmNlNDkvOTczZTA3Mzk4Y2RhYTljY2NjNWEzNTJhLWJhYjU2ZjkxYTg2NjA4NDgzMjRkZDIxNi5tcDQiLCAiZXhwIjogMTc4Mjg4OTA1NCwgInNpZyI6ICJhMDI1NDBlYTQzYTliODJiYzJiYzRkMGNkODQ1MmFlNjhiMmEzNGQ3YTRhOGMyMDc3YTI0ZmFjMDI5ZTM5NTE4In0%3D"
                },
            },
        ],
    },
    {
        "id": "event_s10_day1_final_close",
        "enabled": True,
        "scope": "event_first_add",
        "sop_category": "final_close",
        "name": "事件-当天18点最后收单",
        "purpose": "加微当天未付款时，做当天最后一次活动名额提醒。",
        "order": 180,
        "send_once": True,
        "event_type": "sop_friend_added_schedule_batch",
        "delay_minutes": 600,
        "day_stage": "day1_evening",
        "customer_state": "day1_unpaid",
        "stage_tag": "final_close",
        "triggers": ["day1_18_final_close"],
        "reply_messages": [
            {
                "type": "text",
                "order": 1,
                "content": {
                    "text": "亲，我帮您看了下优惠名额今天还有，您先登记一个活动名额，后面有空再到店也可以。"
                },
            },
            {
                "type": "payment_collection",
                "order": 2,
                "content": {"amount": 10, "remark": ""},
            },
        ],
    },
]


class SopReplyPackService:
    def __init__(self, settings: Settings) -> None:
        self.path = settings.sop_reply_packs_path

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return deepcopy(DEFAULT_SOP_REPLY_PACKS)
        try:
            with self.path.open("r", encoding="utf-8") as file:
                payload = json.load(file)
            return self._normalize(payload)
        except (OSError, json.JSONDecodeError, ValueError):
            return deepcopy(DEFAULT_SOP_REPLY_PACKS)

    def save(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize(payload)
        normalized["version"] = int(normalized.get("version") or 1)
        normalized["updated_at"] = datetime.now(UTC).isoformat()
        self._write_json(self.path, normalized)
        return normalized

    def append_missing_event_first_add_templates(self) -> dict[str, Any]:
        current = self.load()
        existing_ids = {
            str(pack.get("id") or "")
            for pack in current.get("packs", [])
            if isinstance(pack, dict)
        }
        appended = [
            deepcopy(pack)
            for pack in EVENT_FIRST_ADD_TEMPLATE_PACKS
            if str(pack.get("id") or "") not in existing_ids
        ]
        if appended:
            current["packs"] = [*current.get("packs", []), *appended]
        saved = self.save(current)
        saved["appended_pack_ids"] = [str(pack.get("id") or "") for pack in appended]
        return saved

    def _normalize(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        raw_packs = payload.get("packs")
        if not isinstance(raw_packs, list):
            raise ValueError("packs must be a list")
        packs = [self._normalize_pack(item, index) for index, item in enumerate(raw_packs)]
        seen_ids: set[str] = set()
        for pack in packs:
            if pack["id"] in seen_ids:
                raise ValueError(f"duplicated SOP pack id: {pack['id']}")
            seen_ids.add(pack["id"])
        packs.sort(key=lambda item: (item["order"], item["id"]))
        return {
            "version": int(payload.get("version") or 1),
            "updated_at": str(payload.get("updated_at") or ""),
            "packs": packs,
        }

    def _normalize_pack(self, item: Any, index: int) -> dict[str, Any]:
        if not isinstance(item, dict):
            raise ValueError(f"pack #{index + 1} must be an object")
        pack_id = _clean_identifier(item.get("id") or f"sop_pack_{index + 1}")
        if not pack_id:
            raise ValueError(f"pack #{index + 1} id is required")
        triggers = item.get("triggers") if isinstance(item.get("triggers"), list) else []
        messages = item.get("reply_messages") if isinstance(item.get("reply_messages"), list) else []
        return {
            "id": pack_id,
            "enabled": bool(item.get("enabled")),
            "scope": _choice_text(item.get("scope"), "chat_gate", ALLOWED_SOP_SCOPES),
            "sop_category": _choice_text(item.get("sop_category"), pack_id, ALLOWED_SOP_CATEGORIES, allow_custom=True),
            "name": _checked_text(item.get("name"), f"SOP {index + 1}"),
            "purpose": _checked_text(item.get("purpose"), ""),
            "order": _positive_int(item.get("order"), (index + 1) * 10),
            "send_once": bool(item.get("send_once", True)),
            "event_type": _checked_text(item.get("event_type"), ""),
            "delay_minutes": _non_negative_int(item.get("delay_minutes"), 0),
            "day_stage": _checked_text(item.get("day_stage"), ""),
            "customer_state": _checked_text(item.get("customer_state"), ""),
            "stage_tag": _checked_text(item.get("stage_tag"), ""),
            "triggers": [_checked_text(value, "") for value in triggers if _checked_text(value, "")],
            "reply_messages": [
                self._normalize_message(message, message_index)
                for message_index, message in enumerate(messages)
                if isinstance(message, dict)
            ],
        }

    def _normalize_message(self, item: dict[str, Any], index: int) -> dict[str, Any]:
        message_type = str(item.get("type") or "text").strip()
        if message_type not in ALLOWED_MESSAGE_TYPES:
            raise ValueError(f"unsupported reply message type: {message_type}")
        content = item.get("content") if isinstance(item.get("content"), dict) else {}
        normalized_content = self._normalize_message_content(message_type, content)
        return {
            "type": message_type,
            "order": _positive_int(item.get("order"), index + 1),
            "content": normalized_content,
        }

    def _normalize_message_content(self, message_type: str, content: dict[str, Any]) -> dict[str, Any]:
        if message_type == "text":
            return {"text": _checked_text(content.get("text"), "")}
        if message_type in {"image", "video"}:
            media_content = {"url": _checked_text(content.get("url"), "")}
            key = _checked_text(content.get("key"), "")
            if key:
                media_content["key"] = key
            return media_content
        if message_type == "payment_collection":
            return {
                "amount": _positive_int(content.get("amount"), 10),
                "remark": _checked_text(content.get("remark"), ""),
            }
        if message_type == "store_address":
            return {"store_id": _checked_text(content.get("store_id"), "")}
        if message_type == "human_handoff":
            return {"handoff_reason": _checked_text(content.get("handoff_reason") or content.get("text"), "")}
        return {}

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(f"{path.suffix}.tmp")
        with tmp_path.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.write("\n")
        tmp_path.replace(path)


def _checked_text(value: Any, default: str) -> str:
    text = str(value if value is not None else default).strip()
    if "{{" in text or "}}" in text:
        raise ValueError("SOP reply packs must use fixed content, not template placeholders")
    return text


def _choice_text(value: Any, default: str, choices: set[str], *, allow_custom: bool = False) -> str:
    text = _checked_text(value, default)
    if allow_custom:
        cleaned = _clean_identifier(text)
        return cleaned or _clean_identifier(default)
    return text if text in choices else default


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _non_negative_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def _clean_identifier(value: Any) -> str:
    text = str(value if value is not None else "").strip()
    return "".join(char for char in text if char.isalnum() or char in {"_", "-"})
