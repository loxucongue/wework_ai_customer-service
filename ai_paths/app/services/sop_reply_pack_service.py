from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.services.payment_collection import PAYMENT_COLLECTION_ALLOWED_AMOUNTS


ALLOWED_MESSAGE_TYPES = {"text", "image", "video", "payment_collection", "store_address", "human_handoff", "human_handoff_notice"}
ALLOWED_SOP_SCOPES = {"chat_gate", "event_first_add", "event_platform_task"}
ALLOWED_SCHEDULE_BASES = {"friend_added", "previous_stage_sent", "payment_card_sent", "local_clock"}
ALLOWED_PAYMENT_STATES = {"", "unpaid", "paid"}
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
            "scopes": ["chat_gate", "event_first_add"],
            "sop_category": "opening",
            "name": "新客破冰",
            "purpose": "新客加微后建立基本信任，介绍技术优势，并引导客户提供城市/区域或定位以便匹配附近门店。",
            "order": 10,
            "send_once": True,
            "event_type": "",
            "delay_minutes": 0,
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
            "scopes": ["chat_gate", "event_first_add"],
            "sop_category": "effect_case",
            "name": "需求与效果承接",
            "purpose": "客户第一次问斑点、效果或是否能做时，承接需求、发送效果案例参考，并衔接客户是否来自线上推广活动。",
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
            "purpose": "客户第一次了解活动、价格或预约金时，说明活动价值、费用规则、预约金可退抵扣口径、名额保留，并补充效果案例参考。",
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
            "purpose": "仅用于客户当前主要顾虑是套路、隐形消费、乱收费、费用规则、预约金抵扣/可退或活动价格真实性时的话术包。",
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
        "enabled": False,
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
                    "text": "亲，给您介绍一下，我们这边主要做斑点、色素、痘印痘坑这类改善，用的是肌源调肤点斑技术。"
                },
            },
            {
                "type": "text",
                "order": 2,
                "content": {
                    "text": "到店会先看斑点和皮肤状态，适合再操作。您主要是脸上斑点、晒斑、老年斑、痘印痘坑，还是色沉这类？我先按您的情况给您看适合的方向。"
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
                    "text": "到店先看效果和方案，满意再做；线上10元预约金到店抵扣，未做或不满意可退，主要是先帮您保留活动价名额。"
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
                    "text": "亲，我先给您把优惠名额留住，10元只是预约金，到店直接抵扣；后面不做或不满意可以退，按付款记录核对。"
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
        "purpose": "客户未付预约金时，继续用效果参考和到店抵扣规则降低付款压力。",
        "order": 160,
        "send_once": True,
        "event_type": "sop_friend_added_schedule_batch",
        "delay_minutes": 60,
        "day_stage": "day1",
        "customer_state": "deposit_unpaid_1h",
        "stage_tag": "payment_followup",
        "triggers": ["deposit_unpaid_1h", "effect_followup"],
        "reply_messages": [
            {
                "type": "text",
                "order": 1,
                "content": {
                    "text": "您看下这个改善参考，活动名额先锁住更稳，到店时间按您方便安排，10元预约金到店抵扣；未做或不满意可退。"
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
        "delay_minutes": 120,
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
        "delay_minutes": 0,
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
            normalized = self._normalize(deepcopy(DEFAULT_SOP_REPLY_PACKS), allow_legacy_event_scopes=True)
            normalized["audit"] = _audit_config(normalized)
            return normalized
        try:
            with self.path.open("r", encoding="utf-8") as file:
                payload = json.load(file)
            normalized = self._normalize(payload, allow_legacy_event_scopes=True)
            normalized["audit"] = _audit_config(normalized)
            return normalized
        except (OSError, json.JSONDecodeError, ValueError):
            normalized = self._normalize(deepcopy(DEFAULT_SOP_REPLY_PACKS), allow_legacy_event_scopes=True)
            normalized["audit"] = _audit_config(normalized)
            return normalized

    def save(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize(payload, allow_legacy_event_scopes=False)
        audit = _audit_config(normalized)
        errors = [issue for issue in audit["issues"] if issue.get("severity") == "error"]
        if errors:
            summary = "; ".join(str(issue.get("message") or issue.get("code") or "") for issue in errors[:5])
            raise ValueError(f"SOP reply pack audit failed: {summary}")
        normalized["version"] = int(normalized.get("version") or 1)
        normalized["updated_at"] = datetime.now(UTC).isoformat()
        self._write_json(self.path, normalized)
        normalized["audit"] = _audit_config(normalized)
        return normalized

    def append_missing_event_first_add_templates(self) -> dict[str, Any]:
        raise ValueError("主动事件话术已迁移到第三方 SOP 平台")

    def _normalize(self, payload: dict[str, Any], *, allow_legacy_event_scopes: bool) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        raw_packs = payload.get("packs")
        if not isinstance(raw_packs, list):
            raise ValueError("packs must be a list")
        packs: list[dict[str, Any]] = []
        for index, item in enumerate(raw_packs):
            if not isinstance(item, dict):
                raise ValueError(f"pack #{index + 1} must be an object")
            scopes = _normalize_scopes(item)
            if "chat_gate" not in scopes:
                if allow_legacy_event_scopes:
                    continue
                raise ValueError("AI reply mainline packs must use chat_gate scope")
            if not allow_legacy_event_scopes and any(scope != "chat_gate" for scope in scopes):
                raise ValueError("active event scopes are no longer supported")
            pack = self._normalize_pack(item, index)
            pack.update(
                {
                    "scope": "chat_gate",
                    "scopes": ["chat_gate"],
                    "event_type": "",
                    "delay_minutes": 0,
                    "schedule_basis": "friend_added",
                    "min_gap_minutes": 0,
                    "max_daily_sends": 0,
                    "silence_only": False,
                    "day_stage": "",
                    "customer_state": "",
                    "stage_tag": "",
                }
            )
            packs.append(pack)
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
        scopes = _normalize_scopes(item)
        return {
            "id": pack_id,
            "enabled": bool(item.get("enabled")),
            "scope": scopes[0],
            "scopes": scopes,
            "sop_category": _choice_text(item.get("sop_category"), pack_id, ALLOWED_SOP_CATEGORIES, allow_custom=True),
            "name": _checked_text(item.get("name"), f"SOP {index + 1}"),
            "purpose": _checked_text(item.get("purpose"), ""),
            "order": _positive_int(item.get("order"), (index + 1) * 10),
            "send_once": bool(item.get("send_once", True)),
            "send_once_group": _clean_identifier(item.get("send_once_group")),
            "event_type": _checked_text(item.get("event_type"), ""),
            "delay_minutes": _non_negative_int(item.get("delay_minutes"), 0),
            "schedule_basis": _choice_text(
                item.get("schedule_basis"),
                "friend_added",
                ALLOWED_SCHEDULE_BASES,
            ),
            "min_gap_minutes": _non_negative_int(item.get("min_gap_minutes"), 0),
            "requires_completed_categories": _identifier_list(item.get("requires_completed_categories")),
            "forbidden_before_categories": _identifier_list(item.get("forbidden_before_categories")),
            "requires_payment_state": _choice_text(
                item.get("requires_payment_state"),
                "",
                ALLOWED_PAYMENT_STATES,
            ),
            "max_daily_sends": _non_negative_int(item.get("max_daily_sends"), 0),
            "silence_only": bool(item.get("silence_only", False)),
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
        if message_type == "human_handoff":
            message_type = "human_handoff_notice"
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
        if message_type == "human_handoff_notice":
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


def _audit_config(config: dict[str, Any]) -> dict[str, Any]:
    packs = config.get("packs") if isinstance(config.get("packs"), list) else []
    issues: list[dict[str, Any]] = []
    for pack in packs:
        if not isinstance(pack, dict):
            continue
        pack_id = str(pack.get("id") or "")
        enabled = bool(pack.get("enabled"))
        messages = pack.get("reply_messages") if isinstance(pack.get("reply_messages"), list) else []
        if enabled and not messages:
            issues.append(_audit_issue("error", "enabled_pack_empty", pack_id, "启用的 SOP 话术包不能为空。"))
        previous_text = ""
        for index, message in enumerate(messages, start=1):
            if not isinstance(message, dict):
                continue
            message_type = str(message.get("type") or "")
            content = message.get("content") if isinstance(message.get("content"), dict) else {}
            if message_type == "text":
                text = str(content.get("text") or "")
                if enabled and not text.strip():
                    issues.append(_audit_issue("error", "empty_text", pack_id, "启用包存在空 text 消息。", order=index))
                if "不做退10元" in text or "不做退还10元" in text:
                    issues.append(_audit_issue("error", "legacy_deposit_refund_policy", pack_id, "预约金退款口径必须统一为“到店抵扣；未做或不满意可退，实际按付款记录核对”。", order=index))
                previous_text = text
                continue
            if message_type in {"image", "video"}:
                url = str(content.get("url") or "")
                if enabled and not url.strip():
                    issues.append(_audit_issue("error", "empty_media_url", pack_id, "启用包存在空媒体 URL。", order=index))
                elif url and "test.by4dev.4ba.cn" not in url:
                    issues.append(_audit_issue("warning", "non_test_oss_url", pack_id, "媒体 URL 不是 test.by4dev.4ba.cn，需要确认长期有效。", order=index))
                continue
            if message_type == "payment_collection":
                amount = _positive_int(content.get("amount"), 10)
                if amount not in PAYMENT_COLLECTION_ALLOWED_AMOUNTS:
                    issues.append(_audit_issue("error", "invalid_payment_amount", pack_id, "预约金金额只能是 10/20/30/40。", order=index))
                if enabled and not previous_text:
                    issues.append(_audit_issue("warning", "payment_without_intro_text", pack_id, "payment_collection 前应有 text 说明锁名额、到店抵扣和可退规则。", order=index))

    for pack in packs:
        if isinstance(pack, dict) and _normalize_scopes(pack) != ["chat_gate"]:
            issues.append(
                _audit_issue(
                    "error",
                    "non_chat_gate_scope",
                    str(pack.get("id") or ""),
                    "AI回复主线话术只能用于 chat_gate。",
                )
            )
    errors = sum(1 for issue in issues if issue.get("severity") == "error")
    warnings = sum(1 for issue in issues if issue.get("severity") == "warning")
    return {
        "status": "error" if errors else ("warning" if warnings else "ok"),
        "error_count": errors,
        "warning_count": warnings,
        "issues": issues,
    }


def _audit_first_add_candidates(packs: list[Any], issues: list[dict[str, Any]]) -> None:
    opening = next((pack for pack in packs if isinstance(pack, dict) and str(pack.get("id") or "") == "s10_new_customer_opening"), {})
    if not opening or not bool(opening.get("enabled")):
        issues.append(_audit_issue("error", "first_add_opening_disabled", "s10_new_customer_opening", "首次加微必须启用 s10_new_customer_opening。"))

    immediate = [
        pack
        for pack in packs
        if isinstance(pack, dict)
        and bool(pack.get("enabled"))
        and "event_first_add" in _normalize_scopes(pack)
        and (not str(pack.get("event_type") or "").strip() or str(pack.get("event_type") or "").strip() == "sop_friend_added_immediate")
        and _non_negative_int(pack.get("delay_minutes"), 0) <= 0
        and pack.get("reply_messages")
    ]
    scheduled = [
        pack
        for pack in packs
        if isinstance(pack, dict)
        and bool(pack.get("enabled"))
        and "event_first_add" in _normalize_scopes(pack)
        and (not str(pack.get("event_type") or "").strip() or str(pack.get("event_type") or "").strip() == "sop_friend_added_schedule_batch")
        and _non_negative_int(pack.get("delay_minutes"), 0) <= 5
        and pack.get("reply_messages")
    ]
    if not scheduled:
        issues.append(
            _audit_issue(
                "error",
                "first_add_schedule_no_candidate",
                "",
                "事件轨前 5 分钟没有可发送的沉默客户轻触包。",
            )
        )


def _audit_shared_activity_quote(packs: list[Any], issues: list[dict[str, Any]]) -> None:
    canonical = next(
        (pack for pack in packs if isinstance(pack, dict) and str(pack.get("id") or "") == "s10_activity_intro"),
        {},
    )
    legacy = next(
        (
            pack
            for pack in packs
            if isinstance(pack, dict) and str(pack.get("id") or "") == "event_s10_price_quote_60min"
        ),
        {},
    )
    if not bool(canonical.get("enabled")) and not bool(legacy.get("enabled")):
        return
    canonical_scopes = set(_normalize_scopes(canonical))
    if not bool(canonical.get("enabled")) or "chat_gate" not in canonical_scopes:
        issues.append(
            _audit_issue(
                "error",
                "shared_activity_quote_scope_missing",
                "s10_activity_intro",
                "聊天轨活动报价必须由 s10_activity_intro 覆盖 chat_gate。",
            )
        )
    if "event_first_add" in canonical_scopes:
        issues.append(
            _audit_issue(
                "error",
                "chat_activity_pack_leaks_into_event_flow",
                "s10_activity_intro",
                "完整聊天活动包不得进入沉默事件轨；事件轨应使用独立轻量报价包。",
            )
        )
    if not bool(legacy.get("enabled")) or "event_first_add" not in set(_normalize_scopes(legacy)):
        issues.append(
            _audit_issue(
                "error",
                "event_activity_quote_missing",
                "event_s10_price_quote_60min",
                "沉默事件轨必须启用独立的 60 分钟活动报价包。",
            )
        )
    canonical_group = _clean_identifier(canonical.get("send_once_group"))
    event_group = _clean_identifier(legacy.get("send_once_group"))
    if not canonical_group or canonical_group != event_group:
        issues.append(
            _audit_issue(
                "error",
                "activity_quote_send_once_group_mismatch",
                "s10_activity_intro",
                "聊天轨和沉默事件轨的活动报价包必须配置同一个非空跨入口去重组。",
            )
        )
    canonical_messages = canonical.get("reply_messages") if isinstance(canonical.get("reply_messages"), list) else []
    event_messages = legacy.get("reply_messages") if isinstance(legacy.get("reply_messages"), list) else []
    canonical_text = _first_message_value(canonical_messages, "text", "text")
    event_text = _first_message_value(event_messages, "text", "text")
    if not canonical_text or canonical_text != event_text:
        issues.append(
            _audit_issue(
                "error",
                "activity_quote_core_text_mismatch",
                "s10_activity_intro",
                "两个入口的活动报价核心正文必须保持一致。",
            )
        )
    canonical_image = _first_message_value(canonical_messages, "image", "url")
    event_image = _first_message_value(event_messages, "image", "url")
    if not canonical_image or canonical_image != event_image:
        issues.append(
            _audit_issue(
                "error",
                "activity_quote_image_mismatch",
                "s10_activity_intro",
                "两个入口的活动报价必须使用同一张活动图。",
            )
        )


def _audit_issue(severity: str, code: str, pack_id: str, message: str, *, order: int | None = None) -> dict[str, Any]:
    issue = {
        "severity": severity,
        "code": code,
        "pack_id": pack_id,
        "message": message,
    }
    if order is not None:
        issue["message_order"] = order
    return issue


def _first_message_value(messages: list[Any], message_type: str, field: str) -> str:
    for message in messages:
        if not isinstance(message, dict) or str(message.get("type") or "") != message_type:
            continue
        content = message.get("content") if isinstance(message.get("content"), dict) else {}
        value = str(content.get(field) or "").strip()
        if value:
            return value
    return ""


def _choice_text(value: Any, default: str, choices: set[str], *, allow_custom: bool = False) -> str:
    text = _checked_text(value, default)
    if allow_custom:
        cleaned = _clean_identifier(text)
        return cleaned or _clean_identifier(default)
    return text if text in choices else default


def _normalize_scopes(item: dict[str, Any]) -> list[str]:
    raw_scopes = item.get("scopes")
    values = raw_scopes if isinstance(raw_scopes, list) else [item.get("scope")]
    scopes: list[str] = []
    for value in values:
        scope = _choice_text(value, "", ALLOWED_SOP_SCOPES)
        if scope and scope not in scopes:
            scopes.append(scope)
    return scopes or ["chat_gate"]


def _identifier_list(value: Any) -> list[str]:
    values = value if isinstance(value, list) else []
    output: list[str] = []
    for item in values:
        cleaned = _clean_identifier(item)
        if cleaned and cleaned not in output:
            output.append(cleaned)
    return output


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
