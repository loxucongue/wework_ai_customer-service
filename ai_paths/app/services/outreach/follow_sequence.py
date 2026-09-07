from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, time as clock_time, timedelta, timezone
from typing import Any


FOLLOW_SEQUENCE_SELECTOR_PROMPT_VERSION = "opened_silence_follow_sequence_selector_zh_v1"

FOLLOW_SEQUENCE_SELECTOR_PROMPT = """
# 角色
你是企业微信淡斑销售的沉默唤醒计划决策节点。客户已经真实开口，并在销售或 AI 最新回复后沉默。
你只选择当前适用的已发布跟进序列，或选择尚未完成的主线 SOP；不写客户可见话术。

# 决策目标
1. 先判断是否存在健康风险、明确停止联系、投诉退款、人工接管、当前已预约/已支付、关系删除或会话归属不可靠等硬边界。存在时停止创建计划。
2. 只有客户本人消息明确表达尚未解决的价格、效果、信任、距离、时间、家人决策、健康顾虑等卡点时，才算“有卡点”。沉默本身、普通询价、客服主动提到某个问题都不是客户卡点。客户明确说明现实时间安排导致近期无法继续（例如工作忙、近期没空、只能以后）属于时间卡点，不能因为语气柔和降为无卡点；只有“考虑一下”但没有给出具体原因时，才按无明确卡点低压换价值。
3. 有卡点时，必须从 `follow_sequences` 中选择一条最贴近当前原话、阶段和允许推进压力的序列，输出真实 `selected_sequence_id`。不得自造序列或节点。平台没有完全一致的二级场景时，可以选择相同卡点类型中节奏最接近的一条，`sequence_match_scope=checkpoint_type`；此时序列只提供动作节奏，不得把其二级场景名称强加给客户。确实直接匹配时使用 `exact_checkpoint`。
4. 无明确卡点时，`decision_mode=mainline`，从 `mainline_sources` 中选择仍能提供新价值的真实来源。任务数量不限于两步；只选择确实适合继续发送、且没有重复交付的来源。
5. 不要因为没有支付卡、没有门店或客户说考虑一下就停止。软拒绝应降压换价值，明确退订才停止。

# 时间
- 跟进序列的节点、顺序和时间来自平台，不能改写。
- 主线任务可以为每个来源给出从计划创建时起的累计 `delay_minutes`，必须非负并按顺序不下降；不要套用固定15～20分钟。
- 夜间顺延和夜间活跃40分钟压缩由代码处理，你不要删除节点。

# 输出
只输出一个 JSON 对象：
{
  "eligible": true,
  "suppress_reason": "",
  "hard_boundary": {"active": false, "type": "none", "message_indexes": [], "fact": ""},
  "decision_mode": "follow_sequence|mainline",
  "checkpoint": {"code": "none或目录真实编码", "name": "无卡点或目录真实名称", "message_indexes": [], "evidence": "客户原话证据"},
  "selected_sequence_id": "有卡点时填写目录真实ID，否则为空",
  "sequence_match_scope": "exact_checkpoint|checkpoint_type|none",
  "script_search_query": "有卡点时把客户原话改写成便于检索话术的短语，包含同义业务表达；无卡点为空",
  "selection_reason": "为什么适用",
  "mainline_tasks": [
    {"source_id": "mainline_sources中的真实ID", "delay_minutes": 0, "objective": "本节点唯一目标"}
  ],
  "customer_mainline": {
    "latest_customer_main_need": "客户当前真正需要什么",
    "silence_barrier": "明确卡点；无卡点则写无明确卡点",
    "next_business_action": "本计划下一步"
  },
  "confidence": 0.0,
  "evidence": [{"message_index": 0, "fact": "简短证据"}]
}

`decision_mode=follow_sequence` 时 `mainline_tasks=[]` 且 match scope 不能为 none；`decision_mode=mainline` 时 `selected_sequence_id=""`、`sequence_match_scope=none`、checkpoint.code="none"。
停止计划时 `eligible=false`、`hard_boundary.active=true`、`decision_mode=mainline`、序列ID为空、主线任务为空。
""".strip()


HARD_BOUNDARY_TYPES = {
    "health_risk",
    "paid",
    "booked",
    "complaint_refund",
    "deleted_relation",
    "manual_takeover",
    "stop_contact",
    "unreliable_conversation",
}


def compact_follow_sequence_catalog(result: dict[str, Any]) -> dict[str, Any]:
    items = [item for item in result.get("items") or [] if isinstance(item, dict)]
    compact: list[dict[str, Any]] = []
    for item in items:
        steps = [step for step in item.get("steps") or [] if isinstance(step, dict)]
        action_outline: list[str] = []
        for step in steps:
            label = _string(step.get("action_name")) or _string(step.get("action_code"))
            if label and label not in action_outline:
                action_outline.append(label)
        compact.append(
            {
                "id": _string(item.get("id")),
                "sequence_name": _string(item.get("sequence_name")),
                "checkpoint_code": _string(item.get("checkpoint_code")),
                "checkpoint_name": _string(item.get("checkpoint_name")),
                "description": _string(item.get("description"))[:300],
                "step_count": len(steps),
                # The selector only needs the sales outline. Full node IDs,
                # order and timing stay in the validated catalog and are
                # materialized after the model selects a real sequence ID.
                "action_outline": action_outline[:5],
            }
        )
    return {
        "status": _string(result.get("status")),
        "reason": _string(result.get("reason")),
        "source": _string(result.get("source")),
        "declared_total": _int(result.get("total")),
        "usable_total": len(compact),
        "invalid_total": _int(result.get("invalid_item_count")),
        "items": compact,
    }


def normalize_follow_sequence_decision(raw: Any) -> dict[str, Any]:
    value = dict(raw) if isinstance(raw, dict) else {}
    checkpoint = value.get("checkpoint") if isinstance(value.get("checkpoint"), dict) else {}
    hard_boundary = value.get("hard_boundary") if isinstance(value.get("hard_boundary"), dict) else {}
    mainline = []
    for item in value.get("mainline_tasks") or []:
        if not isinstance(item, dict):
            continue
        mainline.append(
            {
                "source_id": _string(item.get("source_id")),
                "delay_minutes": max(0, _int(item.get("delay_minutes"))),
                "objective": _string(item.get("objective")),
            }
        )
    boundary_type = _string(hard_boundary.get("type")).lower() or "none"
    boundary_type = {
        "explicit_exit": "stop_contact",
        "explicit_stop_contact": "stop_contact",
        "unsubscribe": "stop_contact",
        "do_not_contact": "stop_contact",
        "human_takeover": "manual_takeover",
        "appointment_complete": "booked",
        "order_paid": "paid",
    }.get(boundary_type, boundary_type)
    return {
        "eligible": value.get("eligible") if isinstance(value.get("eligible"), bool) else None,
        "suppress_reason": _string(value.get("suppress_reason")),
        "hard_boundary": {
            "active": hard_boundary.get("active") if isinstance(hard_boundary.get("active"), bool) else None,
            "type": boundary_type,
            "message_indexes": [
                index for index in hard_boundary.get("message_indexes") or [] if isinstance(index, int) and index >= 0
            ],
            "fact": _string(hard_boundary.get("fact")),
        },
        "decision_mode": _string(value.get("decision_mode")),
        "checkpoint": {
            "code": _string(checkpoint.get("code")).lower() or "none",
            "name": _string(checkpoint.get("name")) or "无卡点",
            "message_indexes": [
                index for index in checkpoint.get("message_indexes") or [] if isinstance(index, int) and index >= 0
            ],
            "evidence": _string(checkpoint.get("evidence")),
        },
        "selected_sequence_id": _string(value.get("selected_sequence_id")),
        "sequence_match_scope": _string(value.get("sequence_match_scope")).lower()
        or ("exact_checkpoint" if _string(value.get("selected_sequence_id")) else "none"),
        "script_search_query": _string(value.get("script_search_query"))[:300],
        "selection_reason": _string(value.get("selection_reason")),
        "mainline_tasks": mainline,
        "customer_mainline": (
            dict(value.get("customer_mainline")) if isinstance(value.get("customer_mainline"), dict) else {}
        ),
        "confidence": value.get("confidence"),
        "evidence": [item for item in value.get("evidence") or [] if isinstance(item, dict)],
    }


def follow_sequence_decision_error(
    decision: dict[str, Any],
    *,
    sequences: list[dict[str, Any]],
    mainline_sources: list[dict[str, Any]],
    message_count: int,
) -> str:
    if not isinstance(decision.get("eligible"), bool):
        return "eligible must be boolean"
    hard_boundary = decision.get("hard_boundary") or {}
    if not isinstance(hard_boundary.get("active"), bool):
        return "hard_boundary.active must be boolean"
    if any(
        not isinstance(index, int) or index < 0 or index >= message_count
        for index in hard_boundary.get("message_indexes") or []
    ):
        return "hard_boundary message index is invalid"
    if decision["eligible"] is False:
        if not hard_boundary.get("active") or _string(hard_boundary.get("type")) not in HARD_BOUNDARY_TYPES:
            return "ineligible decision requires a supported hard boundary"
        if decision.get("selected_sequence_id") or decision.get("mainline_tasks"):
            return "ineligible decision cannot contain tasks"
        return ""
    if hard_boundary.get("active"):
        return "eligible decision cannot contain an active hard boundary"
    mode = _string(decision.get("decision_mode"))
    if mode not in {"follow_sequence", "mainline"}:
        return "decision_mode must be follow_sequence or mainline"
    sequences_by_id = {_string(item.get("id")): item for item in sequences}
    if mode == "follow_sequence":
        sequence = sequences_by_id.get(_string(decision.get("selected_sequence_id")))
        if not sequence:
            return "selected_sequence_id is not in the published catalog"
        steps = [step for step in sequence.get("steps") or [] if isinstance(step, dict)]
        if not steps or len(steps) != _int(sequence.get("step_count")):
            return "selected sequence is incomplete"
        if decision.get("mainline_tasks"):
            return "follow_sequence decision cannot contain mainline_tasks"
        if _string(decision.get("sequence_match_scope")) not in {
            "exact_checkpoint",
            "checkpoint_type",
        }:
            return "follow sequence match scope is invalid"
        checkpoint = decision.get("checkpoint") or {}
        if _string(checkpoint.get("code")) != _string(sequence.get("checkpoint_code")):
            return "checkpoint code must match the selected sequence"
        if not checkpoint.get("message_indexes") or not _string(checkpoint.get("evidence")):
            return "follow sequence requires direct customer checkpoint evidence"
        if any(index < 0 or index >= message_count for index in checkpoint.get("message_indexes") or []):
            return "checkpoint message index is invalid"
        return ""
    if decision.get("selected_sequence_id"):
        return "mainline decision cannot select a follow sequence"
    if _string(decision.get("sequence_match_scope")) != "none":
        return "mainline decision must use sequence_match_scope=none"
    if _string((decision.get("checkpoint") or {}).get("code")) != "none":
        return "mainline decision must not invent a checkpoint"
    source_ids = {_string(item.get("source_id")) for item in mainline_sources}
    tasks = decision.get("mainline_tasks") or []
    if not tasks:
        return "mainline decision requires at least one task"
    seen: set[str] = set()
    previous_delay = -1
    for item in tasks:
        source_id = _string(item.get("source_id"))
        if not source_id or source_id not in source_ids or source_id in seen:
            return "mainline task source is missing, unknown, or duplicated"
        if not _string(item.get("objective")):
            return "mainline task objective is required"
        delay = _int(item.get("delay_minutes"))
        if delay < previous_delay:
            return "mainline delays must be non-decreasing"
        previous_delay = delay
        seen.add(source_id)
    return ""


def find_selected_sequence(sequences: list[dict[str, Any]], selected_sequence_id: Any) -> dict[str, Any]:
    selected = _string(selected_sequence_id)
    return next(
        (dict(item) for item in sequences if _string(item.get("id")) == selected),
        {},
    )


def sequence_checksum(sequence: dict[str, Any]) -> str:
    payload = json.dumps(sequence, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def rank_follow_scripts_for_node(
    scripts: list[dict[str, Any]],
    *,
    node: dict[str, Any],
    limit: int = 6,
    checkpoint_code: str = "",
    query_text: str = "",
) -> list[dict[str, Any]]:
    action_code = _string(node.get("action_code")).lower()
    action_name = _string(node.get("action_name")).lower()
    checkpoint = _string(checkpoint_code).lower()
    query_terms = _retrieval_terms(query_text)

    def score(script: dict[str, Any]) -> tuple[int, int, int, int, str]:
        script_action = _string(script.get("action_code")).lower()
        script_action_name = _string(script.get("action_name")).lower()
        return (
            _script_relevance_score(query_terms, script),
            1 if action_code and script_action == action_code else 0,
            1 if action_name and action_name == script_action_name else 0,
            _int(script.get("weight")),
            _string(script.get("id") or script.get("script_code")),
        )

    values = [dict(script) for script in scripts if isinstance(script, dict)]
    cap = max(1, int(limit))
    if not checkpoint:
        return sorted(values, key=score, reverse=True)[:cap]

    same_checkpoint = [item for item in values if _string(item.get("checkpoint_code")).lower() == checkpoint]
    exact_action = [
        item for item in same_checkpoint if action_code and _string(item.get("action_code")).lower() == action_code
    ]
    same_checkpoint_other = [item for item in same_checkpoint if item not in exact_action]
    global_semantic = [
        item
        for item in values
        if _string(item.get("checkpoint_code")).lower() != checkpoint and _script_relevance_score(query_terms, item) > 0
    ]
    exact_action.sort(key=score, reverse=True)
    same_checkpoint_other.sort(key=score, reverse=True)
    global_semantic.sort(key=score, reverse=True)

    selected: list[dict[str, Any]] = []

    def append(items: list[dict[str, Any]], count: int, scope: str) -> None:
        for item in items:
            if len([value for value in selected if value.get("outreach_match_scope") == scope]) >= count:
                break
            identity = _string(item.get("id") or item.get("script_code"))
            if any(_string(value.get("id") or value.get("script_code")) == identity for value in selected):
                continue
            selected.append({**item, "outreach_match_scope": scope})

    append(exact_action, min(2, cap), "checkpoint_action")
    append(same_checkpoint_other, min(2, max(0, cap - len(selected))), "checkpoint")
    append(global_semantic, min(2, max(0, cap - len(selected))), "semantic_global")
    remaining = [*exact_action, *same_checkpoint_other, *global_semantic]
    for item in sorted(remaining, key=score, reverse=True):
        if len(selected) >= cap:
            break
        identity = _string(item.get("id") or item.get("script_code"))
        if any(_string(value.get("id") or value.get("script_code")) == identity for value in selected):
            continue
        scope = (
            "checkpoint_action"
            if item in exact_action
            else "checkpoint"
            if item in same_checkpoint_other
            else "semantic_global"
        )
        selected.append({**item, "outreach_match_scope": scope})
    return selected[:cap]


def compact_script_for_model(script: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _string(script.get("id")),
        "script_code": _string(script.get("script_code")),
        "script_name": _string(script.get("script_name")),
        "action_code": _string(script.get("action_code")),
        "action_name": _string(script.get("action_name")),
        "checkpoint_name": _string(script.get("checkpoint_name")),
        "checkpoint_tag_name": _string((script.get("checkpoint_tag") or {}).get("name")),
        "match_scope": _string(script.get("outreach_match_scope")),
        "body_text": _string(script.get("body_text"))[:1000],
        "paragraphs": [
            {
                "paragraph_no": _int(paragraph.get("paragraph_no")),
                "messages": [
                    {
                        "type": _string(message.get("type")),
                        "content": _string(message.get("content"))[:1000],
                        "title": _string(message.get("title")),
                        "remark": _string(message.get("remark")),
                        "has_media": bool(_string(message.get("url"))),
                    }
                    for message in paragraph.get("messages") or []
                    if isinstance(message, dict)
                ],
            }
            for paragraph in script.get("paragraphs") or []
            if isinstance(paragraph, dict)
        ],
    }


def _script_searchable_text(script: dict[str, Any]) -> str:
    return " ".join(
        value
        for value in (
            _string(script.get("script_name")),
            _string(script.get("body_text"))[:1500],
            _string(script.get("checkpoint_name")),
            _string((script.get("checkpoint_tag") or {}).get("name")),
            _string(script.get("action_name")),
        )
        if value
    )


def _script_relevance_score(query_terms: set[str], script: dict[str, Any]) -> int:
    """Prefer explicit business labels over accidental overlap in a long body."""
    if not query_terms:
        return 0
    metadata = " ".join(
        value
        for value in (
            _string(script.get("script_name")),
            _string(script.get("checkpoint_name")),
            _string((script.get("checkpoint_tag") or {}).get("name")),
            _string(script.get("action_name")),
        )
        if value
    )
    metadata_overlap = _retrieval_overlap(query_terms, metadata)
    body_overlap = min(
        6,
        _retrieval_overlap(query_terms, _string(script.get("body_text"))[:1500]),
    )
    return metadata_overlap * 10 + body_overlap


def _retrieval_terms(value: Any) -> set[str]:
    text = _string(value).lower()
    if not text:
        return set()
    terms = set(re.findall(r"[a-z0-9_\-]{2,}", text))
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(run) <= 8:
            terms.add(run)
        for size in (2, 3):
            terms.update(run[index : index + size] for index in range(max(0, len(run) - size + 1)))
    return terms


def _retrieval_overlap(query_terms: set[str], value: Any) -> int:
    if not query_terms:
        return 0
    return len(query_terms.intersection(_retrieval_terms(value)))


def script_media_assets(script: dict[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    script_id = _string(script.get("id") or script.get("script_code"))
    for paragraph in script.get("paragraphs") or []:
        if not isinstance(paragraph, dict):
            continue
        paragraph_no = max(1, _int(paragraph.get("paragraph_no")))
        for position, message in enumerate(paragraph.get("messages") or [], start=1):
            if not isinstance(message, dict) or _string(message.get("type")) not in {"image", "video"}:
                continue
            url = _string(message.get("url"))
            if not url:
                continue
            output.append(
                {
                    "asset_id": f"follow-script:{script_id}:p{paragraph_no}:m{position}",
                    "type": _string(message.get("type")),
                    "url": url,
                    "source": "follow_knowledge_script",
                    "name": _string(message.get("title")) or _string(script.get("script_name")),
                    "annotation": _string(message.get("remark")),
                    "tags": [
                        _string(script.get("checkpoint_code")),
                        _string(script.get("action_code")),
                    ],
                }
            )
    return output


def normalize_follow_sequence_schedule(
    now_value: str,
    steps: list[dict[str, Any]],
    *,
    source_snapshot: dict[str, Any],
    quiet_start: str = "22:00",
    quiet_end: str = "08:00",
    quiet_resume: str = "08:30",
    night_active_window_minutes: int = 40,
) -> list[dict[str, Any]]:
    now = _parse_iso(now_value) or datetime.now(timezone.utc)
    now = now.astimezone(timezone.utc)
    latest_staff = (
        _parse_iso((source_snapshot.get("conversation_activity") or {}).get("latest_staff_message_at")) or now
    )
    latest_customer = _parse_iso((source_snapshot.get("conversation_activity") or {}).get("latest_customer_message_at"))
    requested: list[datetime] = []
    for item in steps:
        schedule = item.get("schedule_source") if isinstance(item.get("schedule_source"), dict) else {}
        trigger_base = _string(schedule.get("trigger_base")).lower()
        fixed_time = _parse_clock(_string(schedule.get("fixed_time")))
        if trigger_base == "add_wecom_day" and fixed_time is not None:
            candidate = datetime.combine(
                now.astimezone(BEIJING_TIMEZONE).date(),
                fixed_time,
                tzinfo=BEIJING_TIMEZONE,
            )
            if candidate.astimezone(timezone.utc) < now:
                candidate += timedelta(days=1)
            scheduled = candidate.astimezone(timezone.utc)
        else:
            delay = max(0, _int(schedule.get("relative_minutes"), _int(item.get("delay_minutes"))))
            base = latest_staff.astimezone(timezone.utc) if trigger_base == "last_reply" else now
            scheduled = max(now, base + timedelta(minutes=delay))
        if requested and scheduled <= requested[-1]:
            scheduled = requested[-1] + timedelta(seconds=1)
        requested.append(scheduled)

    start_clock = _parse_clock(quiet_start) or clock_time(22, 0)
    end_clock = _parse_clock(quiet_end) or clock_time(8, 0)
    resume_clock = _parse_clock(quiet_resume) or clock_time(8, 30)
    active_window = max(1, int(night_active_window_minutes))
    night_active = False
    deadline: datetime | None = None
    if latest_customer is not None:
        local_customer = latest_customer.astimezone(BEIJING_TIMEZONE)
        deadline = latest_customer.astimezone(timezone.utc) + timedelta(minutes=active_window)
        night_active = (
            _in_quiet_window(local_customer.timetz().replace(tzinfo=None), start_clock, end_clock)
            and now <= deadline
            and now - latest_customer.astimezone(timezone.utc) <= timedelta(minutes=active_window)
        )

    normalized = list(requested)
    mode = "daytime_original"
    if night_active and deadline is not None and normalized:
        available_seconds = max(0.0, (deadline - now).total_seconds())
        requested_offsets = [max(0.0, (value - now).total_seconds()) for value in normalized]
        largest = max(requested_offsets, default=0.0)
        if largest > available_seconds and largest > 0:
            factor = available_seconds / largest
            requested_offsets = [offset * factor for offset in requested_offsets]
            mode = "night_active_compressed"
        else:
            mode = "night_active_original"
        normalized = []
        previous: datetime | None = None
        for offset in requested_offsets:
            value = now + timedelta(seconds=offset)
            if previous is not None and value <= previous:
                value = previous + timedelta(seconds=1)
            if value > deadline:
                value = deadline
            normalized.append(value)
            previous = value
    else:
        carry = timedelta(0)
        for index, requested_at in enumerate(requested):
            value = requested_at + carry
            local = value.astimezone(BEIJING_TIMEZONE)
            if _in_quiet_window(local.timetz().replace(tzinfo=None), start_clock, end_clock):
                resume = _next_resume(local, resume_clock, end_clock)
                shift = resume.astimezone(timezone.utc) - value
                carry += shift
                value += shift
                mode = "quiet_hours_deferred"
            if index and value <= normalized[index - 1]:
                value = normalized[index - 1] + timedelta(seconds=1)
            normalized[index] = value

    return [
        {
            "scheduled_at": scheduled.isoformat(),
            "requested_at": requested_at.isoformat(),
            "requested_delay_minutes": max(0, round((requested_at - now).total_seconds() / 60, 3)),
            "normalized_delay_minutes": max(0, round((scheduled - now).total_seconds() / 60, 3)),
            "schedule_mode": mode,
            "night_active": night_active,
            "night_deadline": deadline.isoformat() if deadline else "",
        }
        for requested_at, scheduled in zip(requested, normalized)
    ]


BEIJING_TIMEZONE = timezone(timedelta(hours=8))


def _next_resume(local: datetime, resume: clock_time, quiet_end: clock_time) -> datetime:
    if local.timetz().replace(tzinfo=None) < quiet_end:
        day = local.date()
    else:
        day = local.date() + timedelta(days=1)
    return datetime.combine(day, resume, tzinfo=BEIJING_TIMEZONE)


def _in_quiet_window(value: clock_time, start: clock_time, end: clock_time) -> bool:
    if start == end:
        return False
    if start < end:
        return start <= value < end
    return value >= start or value < end


def _parse_clock(value: str) -> clock_time | None:
    try:
        hour, minute = value.split(":", 1)
        return clock_time(int(hour), int(minute))
    except (AttributeError, TypeError, ValueError):
        return None


def _parse_iso(value: Any) -> datetime | None:
    raw = _string(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _string(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
