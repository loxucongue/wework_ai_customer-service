from __future__ import annotations

import json
import re
import time
from typing import Any

from app.prompts.global_contract import GLOBAL_BUSINESS_RHYTHM_CONTRACT, GLOBAL_STRUCTURED_NODE_CONTRACT
from app.schemas import ChatRequest
from app.services.model_client import ModelClient
from app.services.sop_message_sanitizer import sanitize_sop_reply_messages
from app.services.sop_reply_pack_service import SopReplyPackService
from app.services.storage.serialization import utc_now_iso
from app.services.trace_logger import compact


SOP_EVENT_SYSTEM_PROMPT = f"""
# Node Role
你是企业微信主动 SOP 事件的发送前判断与受限话术润色节点，不是自由客服回复模型。
你不调用工具，不补业务事实，不重新设计 SOP，也不生成独立的客户回复。

{GLOBAL_STRUCTURED_NODE_CONTRACT}

# Narrow Output Exception
本节点唯一可以包含客户可见文本的位置是 `text_adjustments`：它只能改写输入中已有 text 消息的同一个 order。
这不是自由生成回复，也不能新增、删除、拆分、合并或重排任何消息。

# Business Background And Goal
客户是通过企业微信进入的活动新客。平台已经按时间和客户阶段触发 SOP；你的目标是让已配置 SOP 按销售节奏自然发送，建立信任、解决阶段顾虑，并逐步推进到真实门店、登记、预约金和到店。
平台触达默认应继续既定 SOP；只有确实会打断当前真实对话、与当前诉求冲突或已严重重合时才拒发。

{GLOBAL_BUSINESS_RHYTHM_CONTRACT}

# Input
你会收到：
- `mode`：`first_add_flow` 或 `platform_actions`。
- `event`：触发事件、延迟、阶段和客户状态。
- `recent_conversation`：最近 30 条已发生聊天摘要。
- `candidate_sops`：可选的新客 SOP；每个包有阶段目的、完整 `editable_text_messages` 与只读 `readonly_messages`。
- `platform_actions`：平台任务中的完整可编辑 text 与只读结构消息。
- `completed_sop_pack_ids`、`completed_sop_categories`：已经发送过的包与类目。

`editable_text_messages` 是唯一可改写的原文。`readonly_messages` 中的图片、视频、预约金卡、门店卡和内部 notice 都是结构事实，不能修改。

# Task
1. 理解事件触发的 SOP 阶段、最近聊天和候选包的阶段目的。
2. 判断是否发送：`first_add_flow` 只能选择一个 `candidate_sops.id`；`platform_actions` 只能决定平台 actions 是否发送。
3. 如果发送内容与当前对话的称呼、语气或承接明显不自然，才针对对应已有 text 输出小幅 `text_adjustments`；正常时输出空数组。

# Decision Policy
- 先做拒发审查，通过后才考虑“默认按 SOP 全流程发送”；不能用流程目标覆盖客户当前明确立场。
- 拒发审查按以下顺序：销冠正在连续承接且会被打断；客户当前立场与候选包的核心行动相反；候选包与当前真实诉求冲突；同阶段的目标、核心事实和行动已被完整覆盖；同包或同类已经完成。
- 判断重复时比较“阶段目标 + 核心事实 + 行动目标”，不要因为句子换了说法就当作没发过；但只是同一活动主题或只发过普通图片不等于完整覆盖。
- 话术像公告、通知或机器人，只是润色理由，不是拒发理由。如果阶段和内容本身可以发，必须 `send_sop=true` 并通过 `text_adjustments` 改成自然聊天；不能因为原文生硬就选择不发。
- 客户未回复、只有 staff 消息、前序 SOP 已正常发送、同一活动主题或仅发过普通图片，都不构成拒发。
- `first_add_flow` 按破冰/介绍 -> 需求与门店 -> 效果案例 -> 活动报价 -> 登记与预约金的阶段推进；优先选择与 event 的 delay 和 stage 适配的候选，不倒退补发更早阶段，除非它是唯一合理候选。
- 客户刚提出一个问题并不当然拒发。只有销售正在实时处理该问题，或本包会明显答非所问、硬打断时才拒发。
- 不把活动图、门店图、品牌图当成效果案例；不把“同一活动”误判为严重重合。
- 平台自动加好友开场不是有效客户咨询；没有后续客户消息时，仍按未回复的 SOP 跟进判断。

# Few-Shot Calibration
- 客户明确表示想到店再付、暂时不交预约金，候选包的核心行动是立即发收款卡：客户立场与核心行动相反，拒发，不通过润色继续推卡。
- 近聊已完整说明活动价、预约金、到店抵扣、尾款和保留名额，候选包又是同一活动介绍与同一行动：阶段语义已完整覆盖，拒发。
- 前序只发过破冰和门店铺垫，客户未回复，候选包用于发同类效果参考：属于正常下一阶段，发送。

# Text Adjustment Policy
- 由你语义判断是否需要润色，不按关键词机械判断。
- 润色目的仅限于让既有 SOP 更像真人顺着当前聊天自然发出：可调整称呼、语气、连接句和表达顺序。
- 这是企业微信一对一聊天，不是群发公告、短信通知或机构宣传稿。称呼可以用“您”或“亲”，也可以直接接上文；不要用“尊敬的客户/尊敬的顾客”这类式称呼。
- 如果原文像系统通知或公告，不能只换一两个词；要在不改事实和阶段目标的前提下，改成销售正在微信里接着聊的短句。避免“您好，温馨提醒”“请及时参与”“本机构现隆重开展”“诚邀您参与”等通知体。
- 聊天口吻应该是短、直接、有上下文：先顺着客户刚才的问题或前序阶段，再说本包要推进的内容。不要写“温馨提醒、及时参与、感谢您的关注”这类客服模板句。
- 只有 `send_sop=true` 时才能输出 `text_adjustments`；润色不能把拒发冲突改写成可发。
- 必须保留该文本的阶段目标、已有价格、金额、优惠、退款口径、门店、日期时间、支付方式及承诺边界。
- 不能编造新事实，不能把普通答疑改成另一阶段的强推销，不能新增催付、预约承诺、门店事实或效果承诺。
- `payment_collection`、`store_address`、`image`、`video`、`human_handoff_notice` 永远保持原样；若 text 与这些只读消息有关，润色不得改变其事实含义。

# Text Style Calibration
- 原文：“尊敬的顾客您好，本机构现隆重开展淡斑活动，诚邀您参与。”客户刚说自己脸上有斑：改成类似“亲，您是想了解淡斑对吧，我简单跟您说下这次活动。”
- 原文：“您好，温馨提醒您及时参与本次活动。”前面已介绍过活动：改成类似“亲，前面和您说的活动还可以参加，有哪里不清楚您直接问我就行。”
- 上面只校准口吻和改写幅度，不是要求复读固定句子。根据输入上下文自然改写。

# Do Not
- 不输出普通 AI 回复；`need_ai_reply` 必须为 false。
- 不补门店、价格、档期、案例、订单或客户事实。
- 不因为客户未回复、前序 SOP 已发或最近只有 staff 消息而拒发后续阶段。
- 不输出内部分析、markdown 或 schema 之外的字段。

# Output Schema
只输出 JSON：
{{
  "send_sop": true,
  "sop_pack_id": "first_add_flow 时必须来自 candidate_sops；platform_actions 时为空字符串",
  "need_ai_reply": false,
  "reason": "一句内部判断原因",
  "text_adjustments": [{{"order": 1, "text": "仅改写已有 text 的润色结果"}}]
}}
""".strip()


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
            if is_platform_auto_opening_message(request.content):
                result.update(
                    {
                        "mode": "ignored_platform_auto_message",
                        "send_sop": False,
                        "need_ai_reply": False,
                        "reason": "platform_auto_opening_message",
                    }
                )
                return _finish(result, started)
            if request_context.get("skip_sop_gate"):
                result.update({"mode": "skipped", "reason": "skip_sop_gate"})
                return _finish(result, started)
            gate_risk = _chat_gate_professional_assist_risk(request)
            if gate_risk:
                result.update({"mode": "skipped", "need_ai_reply": True, "reason": gate_risk})
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

            messages, sanitize_summary = sanitize_sop_reply_messages(
                _pack_messages(selected),
                state={
                    "content": request.content,
                    "normalized_content": request.content,
                    "conversation_history": request.conversation_history if isinstance(request.conversation_history, list) else [],
                },
            )
            result["message_sanitize"] = sanitize_summary
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
            if task.get("status") != "sent":
                result.update(
                    {
                        "mode": "complete",
                        "send_sop": False,
                        "need_ai_reply": True,
                        "reason": str(task.get("error") or "sop_pack_already_sent_or_pending"),
                        "task": task,
                    }
                )
                return _finish(result, started)
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
                    "# Source Priority\n"
                    "判断时按当前消息、最近对话、unfinished_sops 的 purpose/order/tags/triggers/reply_messages 摘要排序。\n"
                    "SOP Gate 不拥有门店、档期、支付、订单、案例事实；这些事实缺失时不能自行补全，只能决定是否让普通 AI 继续。\n\n"
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
                    "# Negative Cases\n"
                    "- 客户只是沉默、刚加微后未回复、或上一阶段 SOP 正常铺垫后没有新 customer 消息：不算冲突，优先继续 SOP。\n"
                    "- 客户正在问具体门店地址、真实档期、订单/付款异常、投诉退款或身体不适：SOP 不足以覆盖，need_ai_reply=true。\n"
                    "- 候选包只是和历史同属一个活动主题，不等于严重重合；只有同阶段核心目的和核心素材都已覆盖，才算严重重合。\n"
                    "- 普通价格、效果、信任、隐形消费顾虑如果候选 SOP 已覆盖，就不需要额外 AI 文案。\n\n"
                    "# Few-Shot Calibration\n"
                    "- 新客未回复，1分钟介绍包已发，5分钟问地址包候选可用：send_sop=true，need_ai_reply=false。\n"
                    "- 客户刚问“这家地址发我”：如果候选 SOP 不是门店地址事实，send_sop=false 或 need_ai_reply=true，交给普通 AI 查门店。\n"
                    "- 客户问“效果怎么样”：候选效果案例/效果铺垫包可用且未发送过，send_sop=true，need_ai_reply=false。\n"
                    "- 客户说“我付款多扣了”：send_sop=false，need_ai_reply=true。\n\n"
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
            "text_adjustments": [],
            "model_usage": {},
            "error": "",
        }
        try:
            sent_before = _event_created_at(payload)
            completed_ids = self.repository.list_sent_sop_pack_ids_for_customer(
                customer_id=identity.get("customer_id", ""),
                external_userid=identity.get("external_userid", ""),
                sent_before=sent_before,
            )
            completed_categories = _sent_categories(self.repository, identity, sent_before=sent_before)
            result["completed_sop_pack_ids"] = completed_ids
            result["completed_sop_categories"] = completed_categories
            selector_input = _event_selector_input(
                payload=payload,
                customer=customer,
                event_type=event_type,
                conversation_messages=conversation_messages,
                candidate_packs=candidate_packs,
                actions_reply_messages=actions_reply_messages,
                completed_sop_pack_ids=completed_ids,
                completed_sop_categories=completed_categories,
            )
            result["selector_input"] = compact(selector_input, max_chars=6000)
            selector_output = await self._judge_event_sop(selector_input)
            result["selector_output"] = selector_output
            result["model_usage"] = dict(self.model_client.last_usage or {})
            result["text_adjustments"] = _text_adjustments(selector_output.get("text_adjustments"))

            if event_type in {"sop_friend_added_schedule_batch", "sop_friend_added_immediate"}:
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
                "content": SOP_EVENT_SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": (
                    "根据系统提示词和以下 JSON 输入，返回严格 JSON。\n"
                    + json.dumps(selector_input, ensure_ascii=False, separators=(",", ":"))
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
            send_once_key=_send_once_key(identity, sop_pack_id),
        )
        if task.get("id") and task.get("status") == "pending":
            created = bool(task.get("created"))
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
            task["created"] = created
        return task


def _finish(result: dict[str, Any], started: float) -> dict[str, Any]:
    result["duration_ms"] = int((time.perf_counter() - started) * 1000)
    return result


def is_platform_auto_opening_message(content: str) -> bool:
    normalized = re.sub(r"[\s，,。.!！?？:：；;、\"'“”‘’（）()【】\[\]《》<>-]+", "", str(content or ""))
    return normalized in {
        "我已经添加了你现在我们可以开始聊天了",
        "我已经添加了你现在可以开始聊天了",
    }


def _enabled_chat_packs(config: dict[str, Any]) -> list[dict[str, Any]]:
    packs = config.get("packs") if isinstance(config.get("packs"), list) else []
    enabled = []
    for pack in packs:
        if not isinstance(pack, dict) or not bool(pack.get("enabled")) or not _pack_messages(pack):
            continue
        if not _pack_has_scope(pack, "chat_gate"):
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
    event_type: str = "sop_friend_added_schedule_batch",
) -> list[dict[str, Any]]:
    packs = config.get("packs") if isinstance(config.get("packs"), list) else []
    completed = set(completed_sop_pack_ids)
    completed_categories = set(completed_sop_categories or [])
    candidates: list[dict[str, Any]] = []
    for pack in packs:
        if not isinstance(pack, dict) or not bool(pack.get("enabled")) or not _pack_messages(pack):
            continue
        if not _pack_has_scope(pack, "event_first_add"):
            continue
        pack_event_type = _string(pack.get("event_type"))
        if pack_event_type and pack_event_type != event_type:
            continue
        pack_id = _string(pack.get("id"))
        if pack_id in completed:
            continue
        if _pack_category(pack) in completed_categories:
            continue
        pack_delay = _int(pack.get("delay_minutes"), 0)
        if delay_minutes <= 0 and pack_delay > 0:
            continue
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
    messages = _pack_messages(pack)
    return {
        "id": str(pack.get("id") or ""),
        "scope": _pack_scope(pack),
        "scopes": _pack_scopes(pack),
        "sop_category": _pack_category(pack),
        "name": str(pack.get("name") or ""),
        "purpose": str(pack.get("purpose") or "")[:240],
        "order": int(pack.get("order") or 0),
        "tags": [str(item) for item in pack.get("triggers") or [] if str(item or "").strip()],
        "event_type": str(pack.get("event_type") or ""),
        "delay_minutes": int(pack.get("delay_minutes") or 0),
        "stage_tag": str(pack.get("stage_tag") or ""),
        "reply_messages_summary": _messages_summary(messages),
        **_message_editing_context(messages),
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


def _event_selector_input(
    *,
    payload: dict[str, Any],
    customer: dict[str, Any],
    event_type: str,
    conversation_messages: list[dict[str, Any]],
    candidate_packs: list[dict[str, Any]],
    actions_reply_messages: list[dict[str, Any]],
    completed_sop_pack_ids: list[str],
    completed_sop_categories: list[str],
) -> dict[str, Any]:
    return {
        "mode": "platform_actions" if event_type == "sop_platform_task" else "first_add_flow",
        "event": _event_summary(payload, customer),
        "recent_conversation": _conversation_context(conversation_messages),
        "candidate_sops": [_sop_summary(pack) for pack in candidate_packs],
        "platform_actions_summary": _messages_summary(actions_reply_messages),
        "platform_actions": _message_editing_context(actions_reply_messages),
        "completed_sop_pack_ids": completed_sop_pack_ids,
        "completed_sop_categories": completed_sop_categories,
    }


def _message_editing_context(messages: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    editable_text_messages: list[dict[str, Any]] = []
    readonly_messages: list[dict[str, Any]] = []
    for index, message in enumerate(messages, start=1):
        if not isinstance(message, dict):
            continue
        order = int(message.get("order") or index)
        message_type = _string(message.get("type")) or "text"
        content = message.get("content") if isinstance(message.get("content"), dict) else {}
        if message_type == "text":
            text = _string(content.get("text"))
            if text:
                if len(text) <= 600:
                    editable_text_messages.append({"order": order, "text": text})
                else:
                    readonly_messages.append(
                        {
                            "order": order,
                            "type": "text",
                            "facts": {"editable": False, "reason": "text_too_long_to_safely_rewrite"},
                        }
                    )
            continue
        readonly_messages.append(
            {
                "order": order,
                "type": message_type,
                "facts": _readonly_message_facts(message_type, content),
            }
        )
    return {
        "editable_text_messages": editable_text_messages,
        "readonly_messages": readonly_messages,
    }


def _readonly_message_facts(message_type: str, content: dict[str, Any]) -> dict[str, Any]:
    if message_type == "payment_collection":
        return {"amount": content.get("amount"), "remark": _string(content.get("remark"))}
    if message_type == "store_address":
        return {"store_id": _string(content.get("store_id") or content.get("id"))}
    if message_type == "human_handoff_notice":
        return {"handoff_reason": _string(content.get("handoff_reason"))}
    if message_type in {"image", "video"}:
        return {"asset": "configured"}
    return {}


def _text_adjustments(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    output: list[dict[str, Any]] = []
    seen_orders: set[int] = set()
    for item in value[:8]:
        if not isinstance(item, dict):
            continue
        try:
            order = int(item.get("order") or 0)
        except (TypeError, ValueError):
            continue
        text = _string(item.get("text"))
        if order <= 0 or not text or len(text) > 360 or order in seen_orders:
            continue
        seen_orders.add(order)
        output.append({"order": order, "text": text})
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
    return _pack_scopes(pack)[0]


def _pack_scopes(pack: dict[str, Any]) -> list[str]:
    raw_scopes = pack.get("scopes")
    values = raw_scopes if isinstance(raw_scopes, list) else [pack.get("scope")]
    scopes: list[str] = []
    for value in values:
        scope = _string(value)
        if scope in {"chat_gate", "event_first_add", "event_platform_task"} and scope not in scopes:
            scopes.append(scope)
    return scopes or ["chat_gate"]


def _pack_has_scope(pack: dict[str, Any], scope: str) -> bool:
    return scope in _pack_scopes(pack)


def _pack_category(pack: dict[str, Any]) -> str:
    return _string(pack.get("sop_category")) or _string(pack.get("id"))


def _send_once_key(identity: dict[str, str], sop_pack_id: str) -> str:
    pack_id = _string(sop_pack_id).lower()
    external_userid = _string(identity.get("external_userid")).lower()
    customer_id = _string(identity.get("customer_id")).lower()
    customer_key = external_userid or customer_id
    if not pack_id or not customer_key:
        return ""
    corp_id = _string(identity.get("corp_id")).lower()
    customer_kind = "external" if external_userid else "customer"
    return f"sop_pack:{pack_id}|corp:{corp_id}|{customer_kind}:{customer_key}"


def _sent_categories(repository: Any, identity: dict[str, str], *, sent_before: str = "") -> list[str]:
    func = getattr(repository, "list_sent_sop_categories_for_customer", None)
    if not callable(func):
        return []
    return list(
        func(
            customer_id=identity.get("customer_id", ""),
            external_userid=identity.get("external_userid", ""),
            sent_before=sent_before,
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


def _chat_gate_professional_assist_risk(request: ChatRequest) -> str:
    content = _string(request.content)
    if not content:
        return ""
    risk_terms = {
        "health_or_medical_risk": (
            "心脏病",
            "高血压",
            "怀孕",
            "孕期",
            "哺乳",
            "未成年",
            "过敏",
            "病史",
            "慢病",
            "用药",
            "处方",
        ),
        "complaint_or_payment_risk": (
            "退款",
            "退钱",
            "投诉",
            "维权",
            "报警",
            "曝光",
            "付款失败",
            "支付失败",
            "扣了",
            "扣款",
            "多收",
            "订单",
        ),
        "after_sales_discomfort": ("红肿", "刺痛", "流脓", "发烧", "烂脸", "严重不适"),
        "explicit_human_request": ("真人", "人工", "机器人", "换人"),
    }
    for reason, terms in risk_terms.items():
        if any(term in content for term in terms):
            return reason
    return ""


def _event_created_at(payload: dict[str, Any]) -> str:
    value = payload.get("created_at") or payload.get("upstream_created_at")
    return _string(value)


def _string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
