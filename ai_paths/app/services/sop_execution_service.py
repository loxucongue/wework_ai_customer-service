from __future__ import annotations

import time
from typing import Any

from app.schemas import ChatRequest
from app.services.model_client import ModelClient
from app.services.sop_reply_pack_service import SopReplyPackService
from app.services.storage.serialization import utc_now_iso
from app.services.trace_logger import compact


class SopExecutionService:
    """Select and record configured SOP packs for realtime and event-driven SOP flows."""

    def __init__(
        self,
        *,
        repository: Any,
        sop_reply_pack_service: SopReplyPackService,
        model_client: ModelClient,
    ) -> None:
        self.repository = repository
        self.sop_reply_pack_service = sop_reply_pack_service
        self.model_client = model_client

    async def evaluate_chat_gate(
        self,
        request: ChatRequest,
        *,
        request_id: str,
        request_context: dict[str, Any],
    ) -> dict[str, Any]:
        started = time.perf_counter()
        result: dict[str, Any] = {
            "mode": "normal",
            "send_sop": False,
            "sop_pack_id": "",
            "sop_pack_name": "",
            "need_ai_reply": False,
            "reason": "",
            "reply_messages": [],
            "unfinished_count": 0,
            "completed_sop_pack_ids": [],
            "task": {},
            "model_usage": {},
            "error": "",
        }
        try:
            if request_context.get("skip_sop_gate"):
                result.update({"mode": "skipped", "reason": "skip_sop_gate"})
                return _finish(result, started)

            identity = _chat_identity(request, request_context)
            enabled_packs = _enabled_chat_packs(self.sop_reply_pack_service.load())
            if not enabled_packs:
                result.update({"mode": "complete", "reason": "no_enabled_sop_packs"})
                return _finish(result, started)

            completed_ids = set(
                self.repository.list_sent_sop_pack_ids_for_customer(
                    customer_id=identity["customer_id"],
                    external_userid=identity["external_userid"],
                )
            )
            completed_categories = set(_sent_categories(self.repository, identity))
            unfinished = [
                pack
                for pack in enabled_packs
                if _string(pack.get("id")) not in completed_ids
                and _pack_category(pack) not in completed_categories
            ]
            result["completed_sop_pack_ids"] = sorted(completed_ids)
            result["completed_sop_categories"] = sorted(completed_categories)
            result["unfinished_count"] = len(unfinished)
            if not unfinished:
                result.update({"mode": "complete", "reason": "all_sop_packs_completed"})
                return _finish(result, started)

            selector_input = _chat_selector_input(request, unfinished)
            result["selector_input"] = compact(selector_input, max_chars=6000)
            selector_output = await self._select_chat_sop(selector_input)
            result["selector_output"] = selector_output
            result["model_usage"] = dict(self.model_client.last_usage or {})
            selected = _selected_pack(selector_output, unfinished)
            if not selected:
                result.update(
                    {
                        "mode": "no_sop_selected",
                        "need_ai_reply": True,
                        "reason": str(selector_output.get("reason") or "selector_did_not_choose_sop"),
                    }
                )
                return _finish(result, started)

            messages = _pack_messages(selected)
            if not messages:
                result.update(
                    {
                        "mode": "no_sop_selected",
                        "need_ai_reply": True,
                        "reason": "selected_sop_has_empty_reply_messages",
                    }
                )
                return _finish(result, started)

            task = self._record_chat_gate_task(
                request=request,
                request_id=request_id,
                request_context=request_context,
                identity=identity,
                pack=selected,
                reply_messages=messages,
            )
            result.update(
                {
                    "mode": "selected",
                    "send_sop": True,
                    "sop_pack_id": str(selected.get("id") or ""),
                    "sop_pack_name": str(selected.get("name") or ""),
                    "need_ai_reply": bool(selector_output.get("need_ai_reply")),
                    "reason": str(selector_output.get("reason") or ""),
                    "reply_messages": messages,
                    "task": task,
                }
            )
            return _finish(result, started)
        except Exception as exc:
            result.update(
                {
                    "mode": "error",
                    "send_sop": False,
                    "need_ai_reply": True,
                    "error": f"{type(exc).__name__}: {exc}",
                    "reason": "sop_gate_failed_continue_ai",
                }
            )
            return _finish(result, started)

    async def _select_chat_sop(self, selector_input: dict[str, Any]) -> dict[str, Any]:
        messages = [
            {
                "role": "system",
                "content": (
                    "# SOP Gate Role\n"
                    "你是企业微信线上活动接待链路里的 SOP Gate，不是客服回复模型。\n"
                    "你只判断“本轮是否先发送一个已配置 SOP 话术包”，以及“发送 SOP 后是否还必须继续异步 AI 回复”。\n"
                    "你不能生成客户可见文案，不能改写 SOP 内容，不能调用工具，不能补门店、价格、档期、案例或订单事实。\n\n"
                    "# Business Mission\n"
                    "当前业务目标是让新客按销冠主线完成前置认知：活动介绍、信任建立、效果/案例铺垫、费用规则、预约金价值和下一步成交动作。\n"
                    "普通聊天 AI 负责回答复杂实时问题；SOP Gate 负责在新客 SOP 未完成前，优先把配置好的话术包按客户当前阶段铺出去。\n"
                    "如果 SOP 已经覆盖客户当前关心点，不要再让 AI 补发，避免客户同一轮收到重复内容。\n\n"
                    "# Input\n"
                    "你会收到：\n"
                    "- current_message：客户当前消息。\n"
                    "- conversation_history：最近极短对话。\n"
                    "- unfinished_sops：尚未发送过的 SOP 包，只包含 id/name/purpose/order/tags/triggers/reply_messages 摘要。\n"
                    "你不会收到完整门店事实、档期事实、案例结果或订单详情，因此不能判断这些事实本身。\n\n"
                    "# Task\n"
                    "1. 先理解客户当前消息和最近对话处于哪个成交阶段。\n"
                    "2. 从 unfinished_sops 中最多选择一个当前最该先发的 SOP。\n"
                    "3. 判断该 SOP 是否已经足够承接本轮客户问题。\n"
                    "4. 只有 SOP 无法覆盖、且客户问题必须实时答复时，才设置 need_ai_reply=true。\n\n"
                    "# Decision Policy\n"
                    "- 新客 SOP 没完成前，默认优先选择一个合适 SOP，不要轻易跳过直接进入普通 AI 聊天。\n"
                    "- 选择 SOP 时优先看 purpose、order、triggers、reply_messages 摘要与当前客户问题是否匹配，不要只按关键词机械匹配。\n"
                    "- 如果多个 SOP 都可用，选择最靠近当前成交阶段且 order 更靠前的一个。\n"
                    "- 发送 SOP 后默认 need_ai_reply=false。\n"
                    "- 如果选中的 SOP 已经覆盖价格、效果、活动、预约金、普通顾虑、品牌信任或成交推进诉求，即使客户问题明确，也保持 need_ai_reply=false。\n"
                    "- 只有以下情况才允许 need_ai_reply=true：客户明确索要具体门店地址/导航/真实档期/预约或订单状态；投诉退款、身体不适、强人工诉求；或客户同一句包含多个独立问题，而当前 SOP 只覆盖其中一部分。\n"
                    "- 如果所有 unfinished_sops 都明显不适合当前客户状态，可以 send_sop=false，并让普通 AI 继续处理。\n\n"
                    "# Do Not\n"
                    "- 不生成客户可见 text/image/payment_collection/store_address/video。\n"
                    "- 不改写 SOP 文案，不补写话术包。\n"
                    "- 不输出工具名、门店名、价格细节、档期承诺、案例描述。\n"
                    "- 不因为客户问价格/效果/预约/顾虑就自动 need_ai_reply=true；先看 SOP 是否已覆盖。\n"
                    "- 不输出旧链路字段、阶段分析长文或多余 JSON 字段。\n\n"
                    "# Output\n"
                    "只能输出 JSON，字段必须是 send_sop、sop_pack_id、need_ai_reply、reason。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "请根据以下输入返回 JSON：\n"
                    "{\n"
                    '  "send_sop": true/false,\n'
                    '  "sop_pack_id": "未完成 SOP 的 id 或空字符串",\n'
                    '  "need_ai_reply": true/false,\n'
                    '  "reason": "一句内部原因"\n'
                    "}\n"
                    f"输入：{selector_input}"
                ),
            },
        ]
        data = await self.model_client.chat_json(messages, tier="reply", temperature=0)
        return data if isinstance(data, dict) else {}

    async def evaluate_event_suggestion(
        self,
        *,
        payload: dict[str, Any],
        customer: dict[str, Any],
        identity: dict[str, str],
        event_type: str,
        conversation_messages: list[dict[str, Any]],
        candidate_packs: list[dict[str, Any]] | None = None,
        actions_reply_messages: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        candidate_packs = candidate_packs or []
        actions_reply_messages = actions_reply_messages or []
        result: dict[str, Any] = {
            "mode": "event_suggestion",
            "send_sop": False,
            "sop_pack_id": "",
            "sop_pack_name": "",
            "need_ai_reply": False,
            "reason": "",
            "completed_sop_pack_ids": [],
            "model_usage": {},
            "error": "",
        }
        try:
            completed_ids = self.repository.list_sent_sop_pack_ids_for_customer(
                customer_id=identity.get("customer_id", ""),
                external_userid=identity.get("external_userid", ""),
            )
            completed_categories = _sent_categories(self.repository, identity)
            result["completed_sop_pack_ids"] = completed_ids
            result["completed_sop_categories"] = completed_categories
            selector_input = {
                "mode": "platform_actions" if event_type == "sop_platform_task" else "first_add_flow",
                "event": _event_summary(payload, customer),
                "recent_conversation": _conversation_context(conversation_messages),
                "candidate_sops": [_sop_summary(pack) for pack in candidate_packs],
                "platform_actions_summary": _messages_summary(actions_reply_messages),
                "completed_sop_pack_ids": completed_ids,
                "completed_sop_categories": completed_categories,
            }
            result["selector_input"] = compact(selector_input, max_chars=6000)
            selector_output = await self._judge_event_sop(selector_input)
            result["selector_output"] = selector_output
            result["model_usage"] = dict(self.model_client.last_usage or {})

            if event_type == "sop_friend_added_schedule_batch":
                selected = _selected_pack(selector_output, candidate_packs)
                send_sop = bool(selector_output.get("send_sop") and selected)
                result.update(
                    {
                        "sop_pack_id": str(selected.get("id") or ""),
                        "sop_pack_name": str(selected.get("name") or ""),
                        "send_sop": send_sop,
                    }
                )
            elif event_type == "sop_platform_task":
                send_sop = bool(selector_output.get("send_sop"))
                result.update({"sop_pack_id": "platform_actions", "sop_pack_name": "platform_actions", "send_sop": send_sop})
            else:
                send_sop = False
                result.update({"send_sop": False, "reason": f"unsupported_event_type:{event_type}"})

            result.update(
                {
                    "mode": "event_selected" if send_sop else "event_rejected",
                    "need_ai_reply": False,
                    "reason": str(selector_output.get("reason") or result.get("reason") or ""),
                }
            )
            return _finish(result, started)
        except Exception as exc:
            result.update(
                {
                    "mode": "event_model_error",
                    "send_sop": False,
                    "need_ai_reply": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "reason": "event_sop_model_failed",
                }
            )
            return _finish(result, started)

    async def _judge_event_sop(self, selector_input: dict[str, Any]) -> dict[str, Any]:
        messages = [
            {
                "role": "system",
                "content": (
                    "# SOP Event Role\n"
                    "你是企业微信 SOP 事件判断器，不是客服回复模型。\n"
                    "你只判断平台主动触发的 SOP 事件现在该不该发送；你不能生成客户可见文案，不能改写平台 actions，不能调用工具。\n\n"
                    "# Event Modes\n"
                    "1. first_add_flow：首次加微后的定时提醒。你只能从 candidate_sops 中选择一个当前应该发送的事件专用 SOP，或选择不发。\n"
                    "2. platform_actions：公司业务群发任务。你只能判断 platform_actions 当前是否适合发送，不能换成新客 SOP，不能改写内容。\n\n"
                    "# Input\n"
                    "你会收到：事件字段、最近 30 条聊天摘要、candidate_sops、platform_actions_summary、completed_sop_pack_ids、completed_sop_categories。\n"
                    "candidate_sops 已经按同客户已发送包 ID 和同类目做过预过滤，但你仍要结合最近聊天判断是否重复或不合时机。\n\n"
                    "# Decision Policy\n"
                    "- 事件到了时间不等于必须发送；必须看最近 30 条聊天是否已经覆盖同类内容。\n"
                    "- 如果 completed_sop_pack_ids 或 completed_sop_categories 显示客户已收到相同包或同类 SOP，必须 send_sop=false。\n"
                    "- 如果最近 30 条聊天里已经出现同类图片、活动图、付款入口、门店地址、报价或效果铺垫，也要拒发。\n"
                    "- 如果客户刚刚提出明确问题或正在正常对话，且事件包会打断当前沟通，拒发。\n"
                    "- first_add_flow 只能选择 candidate_sops 里的 sop_pack_id。\n"
                    "- platform_actions 只判断平台 actions 是否发送；send_sop=true 时 sop_pack_id 为空即可。\n\n"
                    "# Do Not\n"
                    "- 不追加普通 AI 回复，need_ai_reply 必须是 false。\n"
                    "- 不补门店、价格、档期、案例或客户事实。\n"
                    "- 不因为事件时间到了就机械发送。\n"
                    "- 不输出多余字段或内部长分析。\n\n"
                    "# Output\n"
                    "只能输出 JSON，字段必须是 send_sop、sop_pack_id、need_ai_reply、reason。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "请根据以下输入返回 JSON：\n"
                    "{\n"
                    '  "send_sop": true/false,\n'
                    '  "sop_pack_id": "first_add_flow 模式下来自 candidate_sops 的 id；platform_actions 模式下为空",\n'
                    '  "need_ai_reply": false,\n'
                    '  "reason": "一句内部原因"\n'
                    "}\n"
                    f"输入：{selector_input}"
                ),
            },
        ]
        data = await self.model_client.chat_json(messages, tier="reply", temperature=0)
        return data if isinstance(data, dict) else {}

    def _record_chat_gate_task(
        self,
        *,
        request: ChatRequest,
        request_id: str,
        request_context: dict[str, Any],
        identity: dict[str, str],
        pack: dict[str, Any],
        reply_messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        event_id = f"chat_gate:{request_id}"
        self.repository.create_sop_event(
            {
                "event_id": event_id,
                "event_type": "chat_gate",
                "source": "ai_paths_platform_reply",
                "request_reply": True,
                "created_at": utc_now_iso(),
                "request_context": request_context,
                "customer": {
                    "customer_id": request.customer_id,
                    "external_userid": request.external_userid,
                },
            }
        )
        sop_pack_id = str(pack.get("id") or "")
        task = self.repository.create_sop_send_task(
            event_id=event_id,
            idempotency_key="|".join(
                [
                    "chat_gate",
                    request_id,
                    identity["external_userid"] or identity["customer_id"],
                    sop_pack_id,
                ]
            ),
            customer_id=identity["customer_id"],
            external_userid=identity["external_userid"],
            corp_id=identity["corp_id"],
            user_id=identity["user_id"],
            wechat=identity["wechat"],
            sop_pack_id=sop_pack_id,
            sop_pack_name=str(pack.get("name") or ""),
            sop_category=_pack_category(pack),
            trigger_source="chat_gate",
            reply_messages=reply_messages,
            status="pending",
        )
        if task.get("id"):
            task = self.repository.update_sop_send_task(
                str(task["id"]),
                status="sent",
                send_payload={
                    "mode": "sync_http_response",
                    "request_id": request_id,
                    "reply_messages": reply_messages,
                },
                send_response={"accepted": True, "mode": "sync_http_response"},
                sent_at=utc_now_iso(),
            )
        return task


def _finish(result: dict[str, Any], started: float) -> dict[str, Any]:
    result["duration_ms"] = int((time.perf_counter() - started) * 1000)
    return result


def _enabled_chat_packs(config: dict[str, Any]) -> list[dict[str, Any]]:
    packs = config.get("packs") if isinstance(config.get("packs"), list) else []
    enabled = []
    for pack in packs:
        if not isinstance(pack, dict) or not bool(pack.get("enabled")) or not _pack_messages(pack):
            continue
        if _pack_scope(pack) != "chat_gate":
            continue
        event_type = _string(pack.get("event_type"))
        if event_type and event_type != "sop_friend_added_schedule_batch":
            continue
        enabled.append(pack)
    return sorted(enabled, key=lambda item: (int(item.get("order") or 0), str(item.get("id") or "")))


def first_add_candidate_packs(
    config: dict[str, Any],
    *,
    completed_sop_pack_ids: list[str],
    completed_sop_categories: list[str] | None = None,
    delay_minutes: int,
) -> list[dict[str, Any]]:
    packs = config.get("packs") if isinstance(config.get("packs"), list) else []
    completed = set(completed_sop_pack_ids)
    completed_categories = set(completed_sop_categories or [])
    candidates: list[dict[str, Any]] = []
    for pack in packs:
        if not isinstance(pack, dict) or not bool(pack.get("enabled")) or not _pack_messages(pack):
            continue
        if _pack_scope(pack) != "event_first_add":
            continue
        if _string(pack.get("event_type")) != "sop_friend_added_schedule_batch":
            continue
        pack_id = _string(pack.get("id"))
        if pack_id in completed:
            continue
        if _pack_category(pack) in completed_categories:
            continue
        pack_delay = _int(pack.get("delay_minutes"), 0)
        if delay_minutes > 0 and pack_delay > delay_minutes:
            continue
        candidates.append(pack)
    return sorted(candidates, key=lambda item: (int(item.get("order") or 0), str(item.get("id") or "")))


def _chat_selector_input(request: ChatRequest, unfinished_packs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "current_message": str(request.content or "").strip(),
        "recent_conversation": _recent_history(request.conversation_history),
        "unfinished_sops": [_sop_summary(pack) for pack in unfinished_packs],
    }


def _event_summary(payload: dict[str, Any], customer: dict[str, Any]) -> dict[str, Any]:
    root_sop = payload.get("sop") if isinstance(payload.get("sop"), dict) else {}
    customer_sop = customer.get("sop") if isinstance(customer.get("sop"), dict) else {}
    first_added = customer.get("first_added_event") if isinstance(customer.get("first_added_event"), dict) else {}
    return {
        "event_type": _string(payload.get("event_type")),
        "delay_minutes": _string(customer_sop.get("delay_minutes")) or _string(root_sop.get("delay_minutes")),
        "day_stage": _string(customer_sop.get("day_stage")) or _string(root_sop.get("day_stage")),
        "customer_state": _string(customer_sop.get("customer_state")) or _string(root_sop.get("customer_state")),
        "stage_tag": _string(customer_sop.get("stage_tag")) or _string(root_sop.get("stage_tag")),
        "platform_task_id": _string(customer_sop.get("platform_task_id")) or _string(root_sop.get("platform_task_id")),
        "first_added_trace_id": _string(first_added.get("trace_id")),
    }


def _conversation_context(messages: list[dict[str, Any]]) -> list[str]:
    output: list[str] = []
    for item in messages[-30:]:
        if not isinstance(item, dict):
            continue
        direction = _string(item.get("direction") or item.get("role") or item.get("sender_type") or item.get("from"))
        sender = _string(item.get("sender_name") or item.get("sender_id"))
        content = _message_text(item.get("content"))
        if not content:
            continue
        label = direction or sender
        output.append(f"{label}:{content}"[:300] if label else content[:300])
    return output


def _message_text(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("text", "content", "url", "store_id"):
            text = _string(value.get(key))
            if text:
                return text
        return ""
    return _string(value)


def _recent_history(history: Any) -> list[str]:
    if not isinstance(history, list):
        return []
    return [str(item)[:240] for item in history[-8:] if str(item or "").strip()]


def _sop_summary(pack: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(pack.get("id") or ""),
        "scope": _pack_scope(pack),
        "sop_category": _pack_category(pack),
        "name": str(pack.get("name") or ""),
        "purpose": str(pack.get("purpose") or "")[:240],
        "order": int(pack.get("order") or 0),
        "tags": [str(item) for item in pack.get("triggers") or [] if str(item or "").strip()],
        "event_type": str(pack.get("event_type") or ""),
        "delay_minutes": int(pack.get("delay_minutes") or 0),
        "stage_tag": str(pack.get("stage_tag") or ""),
        "reply_messages_summary": _messages_summary(_pack_messages(pack)),
    }


def _messages_summary(messages: list[dict[str, Any]]) -> list[str]:
    output: list[str] = []
    for message in messages[:8]:
        message_type = str(message.get("type") or "")
        content = message.get("content") if isinstance(message.get("content"), dict) else {}
        if message_type == "text":
            output.append("text:" + str(content.get("text") or "")[:120])
        elif message_type in {"image", "video"}:
            output.append(message_type + ":" + str(content.get("url") or content.get("key") or "")[:80])
        else:
            output.append(message_type)
    return output


def _selected_pack(selector_output: dict[str, Any], packs: list[dict[str, Any]]) -> dict[str, Any]:
    if not bool(selector_output.get("send_sop")):
        return {}
    selected_id = str(selector_output.get("sop_pack_id") or "").strip()
    if not selected_id:
        return {}
    for pack in packs:
        if str(pack.get("id") or "") == selected_id:
            return pack
    return {}


def _pack_messages(pack: dict[str, Any]) -> list[dict[str, Any]]:
    messages = pack.get("reply_messages") if isinstance(pack.get("reply_messages"), list) else []
    output: list[dict[str, Any]] = []
    for index, message in enumerate(messages, start=1):
        if not isinstance(message, dict):
            continue
        item = {
            "type": str(message.get("type") or "text"),
            "order": int(message.get("order") or index),
            "content": message.get("content") if isinstance(message.get("content"), dict) else {},
        }
        output.append(item)
    return sorted(output, key=lambda item: int(item.get("order") or 0))


def _pack_scope(pack: dict[str, Any]) -> str:
    scope = _string(pack.get("scope"))
    return scope if scope in {"chat_gate", "event_first_add", "event_platform_task"} else "chat_gate"


def _pack_category(pack: dict[str, Any]) -> str:
    return _string(pack.get("sop_category")) or _string(pack.get("id"))


def _sent_categories(repository: Any, identity: dict[str, str]) -> list[str]:
    func = getattr(repository, "list_sent_sop_categories_for_customer", None)
    if not callable(func):
        return []
    return list(
        func(
            customer_id=identity.get("customer_id", ""),
            external_userid=identity.get("external_userid", ""),
        )
        or []
    )


def _chat_identity(request: ChatRequest, request_context: dict[str, Any]) -> dict[str, str]:
    external_userid = _string(request_context.get("external_userid")) or _string(request.external_userid)
    customer_id = _string(request_context.get("customer_id")) or _string(request.customer_id)
    return {
        "corp_id": _string(request_context.get("corp_id")) or _string(request.corp_id),
        "user_id": _string(request_context.get("user_id")) or _string(request.user_id),
        "wechat": _string(request_context.get("wechat")) or _string(request.wechat),
        "external_userid": external_userid,
        "customer_id": external_userid or customer_id,
    }


def _string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
