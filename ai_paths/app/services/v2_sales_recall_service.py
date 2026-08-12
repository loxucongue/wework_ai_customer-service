from __future__ import annotations

import json
import re
import time
from typing import Any

from app.services.coze_client import CozeClient


_AUTO_OPENING_TEXT = "我已经添加了你，现在我们可以开始聊天了。"
_PRICE_PATTERN = re.compile(
    r"(?:(?:原价|现价|价格|活动价)\s*(?:\d+(?:\.\d+)?|[一二三四五六七八九十百千万两]+)|(?:\d+(?:\.\d+)?|[一二三四五六七八九十百千万两]+)\s*(?:元|块|人民币|rmb))",
    re.I,
)
_DISTANCE_PATTERN = re.compile(
    r"(?:\d+(?:\.\d+)?|[一二三四五六七八九十百千万两]+)\s*(?:公里|千米|km|分钟|小时|个小时|h)",
    re.I,
)
_DATE_PATTERN = re.compile(r"(?:今天|明天|后天|周[一二三四五六日天]|星期[一二三四五六日天]|\d{1,2}[月/-]\d{1,2}[日号]?)")
_TEACHER_PATTERN = re.compile(r"(?:指定|固定|安排|总监|院长|主任|专家|老师)")
_ABSOLUTE_EFFECT_PATTERN = re.compile(r"(?:包干净|一次(?:就)?(?:彻底|完全)|保证|百分百|不会反弹|永不复发)")
_GIFT_PATTERN = re.compile(r"(?:赠送|送你|送您|礼品|护理|小气泡|美白|补水)")


class V2SalesRecallService:
    """Recall top-sales reference material for V2 Reply without making it authoritative."""

    def __init__(self, coze_client: CozeClient | None) -> None:
        self.coze_client = coze_client

    async def recall(self, shared_context: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        settings = getattr(self.coze_client, "settings", None) if self.coze_client is not None else None
        if self.coze_client is None or settings is None:
            return _status("disabled", "coze_client_unavailable", started)
        if not bool(getattr(settings, "v2_sales_recall_enabled", True)):
            return _status("disabled", "settings_disabled", started)
        workflow_id = str(getattr(settings, "v2_sales_recall_workflow_id", "") or "").strip()
        if not workflow_id:
            return _status("disabled", "workflow_id_missing", started)
        if _is_opening_only(shared_context):
            return _status("skipped_opening", "auto_opening_or_empty_customer_message", started)

        conversation = _conversation_for_recall(shared_context)
        if not conversation:
            return _status("skipped_empty", "conversation_empty", started)
        try:
            raw = await self.coze_client.run_workflow(workflow_id, {"input": conversation})
        except Exception as exc:
            return _status("error", f"{type(exc).__name__}: {exc}", started)
        code = raw.get("code") if isinstance(raw, dict) else None
        if code not in (None, 0):
            return _status("error", f"coze_workflow_code_{code}: {raw.get('msg') or ''}", started)

        candidates = parse_v2_sales_recall_response(
            raw,
            max_candidates=max(0, int(getattr(settings, "v2_sales_recall_max_candidates", 3) or 3)),
        )
        return {
            "schema_version": "v2_sales_recall_v1",
            "status": "ok" if candidates else "empty",
            "source": "coze_workflow",
            "workflow_id": workflow_id,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "candidate_count": len(candidates),
            "candidates": candidates,
        }


def parse_v2_sales_recall_response(raw: dict[str, Any], *, max_candidates: int = 3) -> list[dict[str, Any]]:
    payload = _parse_data(raw)
    output = payload.get("output")
    if not isinstance(output, list):
        output = payload.get("outputList") or payload.get("outputlist") or []
    candidates: list[dict[str, Any]] = []
    for item in output:
        parsed = _parse_output_item(item)
        if parsed is None:
            continue
        candidates.append(parsed)
        if len(candidates) >= max_candidates:
            break
    return candidates


def _parse_output_item(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    nested = item.get("output")
    if isinstance(nested, str):
        try:
            parsed = json.loads(nested)
        except json.JSONDecodeError:
            parsed = {"话术内容": nested}
    elif isinstance(nested, dict):
        parsed = nested
    else:
        parsed = item
    if not isinstance(parsed, dict):
        return None

    source_id = _plain_text(parsed.get("内容编号") or parsed.get("source_id") or item.get("documentId") or "")
    objection_type = _plain_text(parsed.get("卡点类型") or parsed.get("objection_type") or "")
    scene = _plain_text(parsed.get("适用场景") or parsed.get("applicable_scene") or "")
    text = _plain_text(parsed.get("话术内容") or parsed.get("content") or "")
    if not source_id and not text:
        return None
    sanitized_text, risk_flags = _sanitize_reference_text(text)
    return {
        "source_id": source_id or "unknown",
        "objection_type": objection_type,
        "applicable_reason": scene,
        "reusable_logic": _reusable_logic(objection_type, scene),
        "style_reference": sanitized_text,
        "allowed_materials": _allowed_materials(text),
        "risk_flags": risk_flags,
        "authority": "reference_only_not_business_fact",
        "copy_policy": "learn_reasoning_and_tone_do_not_copy_verbatim",
    }


def _sanitize_reference_text(text: str) -> tuple[str, list[dict[str, str]]]:
    flags: list[dict[str, str]] = []
    sanitized = text
    for code, pattern, marker in (
        ("old_or_external_price", _PRICE_PATTERN, "[价格事实已移除，以系统权威活动事实为准]"),
        ("distance_or_time_fact", _DISTANCE_PATTERN, "[距离/耗时事实已移除，以工具事实为准]"),
        ("fixed_date_fact", _DATE_PATTERN, "[固定日期已移除，以当前活动事实为准]"),
    ):
        if pattern.search(sanitized):
            flags.append({"code": code, "severity": "must_not_quote"})
            sanitized = pattern.sub(marker, sanitized)
    if _TEACHER_PATTERN.search(text):
        flags.append({"code": "teacher_or_staff_claim", "severity": "must_not_claim_without_fact"})
    if _ABSOLUTE_EFFECT_PATTERN.search(text):
        flags.append({"code": "absolute_effect_claim", "severity": "must_not_copy"})
    if _GIFT_PATTERN.search(text):
        flags.append({"code": "gift_material", "severity": "allowed_as_cautious_sales_material"})
    return sanitized.strip(), flags


def _allowed_materials(text: str) -> list[str]:
    materials: list[str] = []
    if _GIFT_PATTERN.search(text):
        materials.append("gift_or_bonus_may_be_used_cautiously")
    if "案例" in text or "对比" in text or "效果" in text:
        materials.append("case_or_effect_evidence_angle")
    if "活动" in text or "名额" in text or "预约" in text:
        materials.append("activity_or_commitment_angle")
    return materials


def _reusable_logic(objection_type: str, scene: str) -> str:
    pieces = [item for item in (objection_type, scene) if item]
    prefix = "；".join(pieces) if pieces else "销冠经验召回"
    return (
        f"{prefix}：只借鉴承接顺序、换维度方式、信心表达和逼单角度；"
        "价格、距离、门店、老师、日期、排客、支付和退款必须以系统权威事实为准。"
    )


def _conversation_for_recall(shared_context: dict[str, Any]) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for item in shared_context.get("conversation") or []:
        if not isinstance(item, dict):
            continue
        content = _plain_text(item.get("content") or "")
        message_type = _plain_text(item.get("message_type") or item.get("type") or "text")
        if not content and message_type not in {"image", "location", "voice"}:
            continue
        output.append(
            {
                "role": _plain_text(item.get("role") or ""),
                "time": _plain_text(item.get("sent_at") or item.get("time") or ""),
                "message_type": message_type,
                "content": content or f"[{message_type}]",
            }
        )
    current = shared_context.get("current_message") if isinstance(shared_context.get("current_message"), dict) else {}
    current_text = _plain_text(current.get("content") or current.get("raw_content") or "")
    if current_text and not _already_has_current_message(output, current_text):
        output.append(
            {
                "role": "customer",
                "time": _plain_text(current.get("sent_at") or ""),
                "message_type": _plain_text(current.get("message_type") or "text"),
                "content": current_text,
            }
        )
    return output[-50:]


def _already_has_current_message(conversation: list[dict[str, str]], current_text: str) -> bool:
    if not conversation:
        return False
    last = conversation[-1]
    return (
        str(last.get("role") or "").lower() in {"customer", "user"}
        and _plain_text(last.get("content") or "") == current_text
    )


def _is_opening_only(shared_context: dict[str, Any]) -> bool:
    current = shared_context.get("current_message") if isinstance(shared_context.get("current_message"), dict) else {}
    content = _plain_text(current.get("content") or current.get("raw_content") or "")
    if content == _AUTO_OPENING_TEXT:
        return True
    if not content:
        return True
    customer_messages = [
        _plain_text(item.get("content") or "")
        for item in shared_context.get("conversation") or []
        if isinstance(item, dict) and str(item.get("role") or "").lower() in {"customer", "user"}
    ]
    substantive = [item for item in customer_messages if item and item != _AUTO_OPENING_TEXT]
    return not substantive and content in {_AUTO_OPENING_TEXT, "[握手]"}


def _parse_data(raw: dict[str, Any]) -> dict[str, Any]:
    data = raw.get("data")
    if isinstance(data, str) and data:
        try:
            parsed = json.loads(data)
            return parsed if isinstance(parsed, dict) else {"output": parsed}
        except json.JSONDecodeError:
            return {"output": data}
    if isinstance(data, dict):
        return data
    return raw


def _plain_text(value: Any) -> str:
    return str(value or "").replace("\x00", "").strip()


def _status(status: str, reason: str, started: float) -> dict[str, Any]:
    return {
        "schema_version": "v2_sales_recall_v1",
        "status": status,
        "source": "coze_workflow",
        "reason": reason,
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "candidate_count": 0,
        "candidates": [],
    }
