from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

from app.prompts.global_contract import GLOBAL_BUSINESS_RHYTHM_CONTRACT, GLOBAL_STRUCTURED_NODE_CONTRACT
from app.schemas import ChatRequest
from app.services.customer_payment_state import is_paid_deposit_state, payment_collection_order_fact, resolved_payment_fact
from app.services.customer_scope import customer_scope_from_identity
from app.services.model_client import ModelClient
from app.services.sop_message_sanitizer import apply_sop_text_adjustments, sanitize_sop_reply_messages
from app.services.sop_reply_pack_service import SopReplyPackService
from app.services.storage.serialization import utc_now_iso
from app.services.trace_logger import compact


FIRST_ADD_NEXT_STEP_LOOKAHEAD_MINUTES = 30
FIRST_ADD_NEXT_STEP_MAX_CANDIDATES = 1


SOP_EVENT_SYSTEM_PROMPT = f"""
# Node Role
你是企业微信主动 SOP 事件的发送前判断与受限话术润色节点，不是自由客服回复模型。
你不调用工具，不补业务事实，不重新设计 SOP，也不生成独立的客户回复。

{GLOBAL_STRUCTURED_NODE_CONTRACT}

# Narrow Output Exception
本节点唯一可以包含客户可见文本的位置是 `text_adjustments` 和 `message_operations`。
它们只用于让已选 SOP 在当前聊天里更自然：可改写已有 text，也可对 text 做插入、删除、合并、拆分和顺序微调。
这不是自由生成回复，不能新增、拆分、合并、重排或修改任何只读结构消息；唯一例外是 `payment_collection_gate` 明确不支持发卡时，可删除该 `payment_collection` 并同步调整文本。

# Business Background And Goal
客户是通过企业微信进入的活动新客。平台已经按时间和客户阶段触发 SOP；你的目标是让已配置 SOP 按销售节奏自然发送，建立信任、解决阶段顾虑，并逐步推进到真实门店、登记、预约金和到店。

`/sop/events` 被触发，本身就表示平台提醒现在应该主动触达客户。它不是要求你机械按 `delay_minutes` 强制发送对应时间点的话术包。除非客户刚发了新消息正在等普通 AI/销售回复，或小贝/销售刚刚在最近几分钟内回复过客户，或者候选包会和客户当前明确立场硬冲突，否则不能空拒。你的核心任务是判断“当前应该发哪一个未完成步骤的包、如何加过渡话术，让客户重新开口”。

{GLOBAL_BUSINESS_RHYTHM_CONTRACT}

# Input
你会收到：
- `mode`：`first_add_flow` 或 `platform_actions`。
- `event`：触发事件、延迟、阶段和客户状态。
- `recent_conversation`：最近 30 条已发生聊天，保留方向、来源、消息类型和时间。
- `conversation_activity`：基于最新会话计算的客户回复、最后消息方向和时间可靠性摘要。
- `candidate_sops`：可选的新客 SOP；每个包有阶段目的、候选分组、完整 `editable_text_messages` 与只读 `readonly_messages`。
- `platform_actions`：平台任务中的完整可编辑 text 与只读结构消息。
- `current_platform_task.message_content`：平台本轮明确要求触达的原始内容，是 `platform_actions` 模式下的当前任务目标。
- `completed_sop_pack_ids`、`completed_sop_categories`：已经发送过的包与类目。
- `customer_profile`、`customer_basic_info`、`lifecycle_stage`、`history_events`：已有客户画像、基础信息、生命周期和最近历史事件，只用于补充背景。

`editable_text_messages` 是主要可操作文本素材。`readonly_messages` 中的图片、视频、门店卡和内部 notice 都是结构事实，不能修改、删除、重排或复制。
`payment_collection` 也是结构事实；只有当输入里的 `payment_collection_gate.status` 明确为 `missing_matching_current_order` 或 `paid_skip_card`，且当前阶段仍适合轻触达时，才允许用 `message_operations.remove_message` 删除该预约金卡，并同步把 text 改成不承诺“已发入口/付完”的自然轻触达。

# Task
1. 理解事件触发的 SOP 阶段、最近聊天和候选包的阶段目的。
2. 判断是否发送：`first_add_flow` 只能选择一个 `candidate_sops.id`；`platform_actions` 只能决定平台 actions 是否发送。
3. 如果发送内容与当前对话的称呼、语气、消息数量或承接顺序明显不自然，才输出 `text_adjustments` 或 `message_operations` 调整 text；正常时输出空数组。

# Decision Policy
- 事实优先级必须是：最新聊天 > 当前事件事实 > 已实际发送的 SOP > 客户画像和较旧历史事件。低优先级信息不得覆盖高优先级事实。
- `platform_actions` 模式下，`current_platform_task.message_content` 是平台根据当前流程选出的本轮触达任务。除最新聊天或订单/支付等硬事实与它明确冲突外，必须优先分析它的阶段目的，不能因旧画像、历史累计或“客户已付”而整体忽略。
- 已支付预约金只禁止再次发送预约金卡或催付，不禁止催到店、姓名电话登记、活动服务说明及其他与已付状态一致的后续触达。平台内容属于后续到店推进时，应保留其目标并按上下文自然润色。
- 客户画像和旧事件不是当前对话事实，不能成为强制发送依据，也不能覆盖近期聊天中的城市、问题、顾虑、拒绝或已经完成的行动。
- 固定首次加微流程中，只有“最新真实客户消息还没被普通 AI/销售回复”会在代码层阻断。客户之前回复过、但最近是小贝/销售发完后客户沉默，属于主动触达场景，必须尽量发 SOP 或轻触，不得因为“客户曾回复过/之前追问过”而空拒。
- 当 `conversation_activity.latest_customer_pending_ai_reply=true` 时，这是客户最新问题等待普通 AI/销售回答的硬边界，必须 `send_sop=false`；不能改选 `next_step` 绕过，也不能用润色把 SOP 当成答复。
- 小贝/销售刚刚回复完客户的几分钟保护窗口会在代码层阻断，防止 SOP 紧贴上一条回复刷屏。进入本节点通常表示已经过了最近活跃保护窗口，或不存在刚回复完的活跃聊天。
- 先做拒发审查，通过后才考虑“默认按 SOP 全流程发送”；不能用流程目标覆盖客户当前明确立场。
- 拒发审查按以下顺序：销冠正在连续承接且会被打断；客户当前立场与候选包的核心行动相反；候选包与当前真实诉求冲突；同阶段的目标、核心事实和行动已被完整覆盖；同包或同类已经完成。
- 判断重复时比较“阶段目标 + 核心事实 + 行动目标”，不要因为句子换了说法就当作没发过；但只是同一活动主题或只发过普通图片不等于完整覆盖。
- 话术像公告、通知或机器人，只是调整理由，不是拒发理由；也是旧口径里的“只是润色理由，不是拒发理由”。如果阶段和内容本身可以发，必须 `send_sop=true` 并通过 `text_adjustments/message_operations` 改成自然聊天；不能因为原文生硬就选择不发。
- 客户未回复、只有 staff 消息、前序 SOP 已正常发送、同一活动主题或仅发过普通图片，都不构成拒发。

- `first_add_flow` 按破冰/介绍 -> 需求与门店 -> 效果案例 -> 活动报价 -> 登记与预约金的阶段推进；`delay_minutes` 只表示这次可以检查到哪个候选范围，不等于必须发送该时间点最高阶段的包。
- `candidate_sops.candidate_group` 可能是 `due` 或 `next_step`：`due` 是当前已到期/逾期的未完成包；`next_step` 是最近的下一阶段未完成包，只是给你在 due 包重复、冲突或已经被最近聊天覆盖时继续 SOP 节奏的备选，不是强制跳阶段。
- `stage_tag/customer_state` 是阶段前置语义，不只是描述文字。`payment_followup/deposit_push/quoted_no_deposit/deposit_unpaid_*` 这类后续包，必须由最近对话、completed_sop_pack_ids/categories 或客户状态证明活动报价/预约金已经真实触达；不能仅因它和报价包同一时刻到期就越过未完成的 `price_quote`。
- 如果 `price_quote` 仍未完成且近期只完成效果/门店铺垫，应优先选择活动报价包。它的 `payment_collection_gate=missing_matching_current_order` 只表示删除收款卡并同步调整 text，不是跳过报价、改选未付款跟进包的理由。
- 选择包时按“最近真实聊天状态 + 已触达步骤 + 未完成步骤 + 候选包阶段目标”判断。客户正在聊且最新客户消息等待普通 AI/销售回复时，不发 SOP；客户沉默时，优先推进下一个合理 SOP 价值点。
- 某个步骤已经被问过或轻触过一次，例如已经问过城市/区域、斑点情况、姓名电话或预约金，客户继续沉默时，不要无限重复追问同一个问题；应往后推进到下一个未完成且不会制造事实错误的 SOP 包，并用第一条 text 自然承接“这个信息后面您方便再补，我先给您看/说下一步”。
- 只有这个必要信息从未触达过，或当前候选只有该步骤，才继续轻触该问题；已经触达过但客户沉默，不得因为“任务未解决”而空拒。
- 当 due 候选和最近聊天严重重合，或客户已被轻触过同一问题但仍沉默时，不要直接 `send_sop=false`；应继续评估 `next_step` 候选，选择一个不编造事实、不涉及风险和支付冲突、能让客户继续开口的下一阶段包。
- 只有在 due 与 next_step 都不适合发送时，才拒发；拒发理由必须说明是客户最新消息待回复、明确冲突、健康/投诉/支付异常、事实错误风险，还是所有候选都严重重复。
- 客户刚提出一个问题并不当然拒发。只有销售正在实时处理该问题，或本包会明显答非所问、硬打断时才拒发。
- 不把活动图、门店图、品牌图当成效果案例；不把“同一活动”误判为严重重合。
- 平台自动加好友开场不是有效客户咨询；没有后续客户消息时，仍按未回复的 SOP 跟进判断。
- 当 `conversation_activity.assistant_waiting_customer=true` 且 `latest_customer_pending_ai_reply=false`，并且已经过了最近活跃保护窗口：这是典型的沉默触达场景。你应优先 `send_sop=true`，目标是让客户再次开口或继续被 SOP 推进；不要因为“刚追问过、staff 已经回复过、客户没接话”而拒发。
- 若候选 SOP 与当前未完成问题不完全一致，但没有硬冲突：仍应选择最合适的下一步候选并用 `text_adjustments/message_operations` 做过渡。例如门店城市还没补齐但已经问过一次且客户沉默，可以先承接“门店后面您发城市/定位我再匹配”，再衔接效果图、活动报价或预约金价值。
- 候选包如果包含 `payment_collection`：
  - `payment_collection_gate.status=supported`：可按正常 SOP 判断发送。
  - `payment_collection_gate.status=missing_matching_current_order`：不能原样发送预约金卡。若客户当前阶段适合继续主动触达，应删除该卡并把 text 改成轻触达、登记或解释活动价值；若删除卡后只剩不合适内容，才拒发。
  - `payment_collection_gate.status=paid_skip_card`：客户已付，不得再发预约金卡；只可保留/改写为已付后的姓名电话或到店安排轻触达。
- 只有在 `conversation_activity.latest_customer_pending_ai_reply=true`、客户明确拒绝当前核心行动、投诉/付款异常/身体不适、或候选包会明显造成事实错误时，才 `send_sop=false`。

# Few-Shot Calibration
- 客户明确表示想到店再付、暂时不交预约金，候选包的核心行动是立即发收款卡：客户立场与核心行动相反，拒发，不通过润色继续推卡。
- 近聊已完整说明活动价、预约金、到店抵扣、尾款和保留名额，候选包又是同一活动介绍与同一行动：阶段语义已完整覆盖，拒发。
- 前序只发过破冰和门店铺垫，客户未回复，候选包用于发同类效果参考：属于正常下一阶段，发送。

- 刚破冰后还没有问过城市/区域，5分钟问地址包候选可用：发送问地址包，轻触客户补城市/区域。
- 已经问过城市/区域或定位，客户仍沉默，后续事件候选里有效果铺垫包：不要再次卡在门店步骤，也不要空拒；发送效果铺垫包，并可在第一条 text 前半句承接“门店后面您发城市/定位我再匹配”，再发效果参考。
- 已经发过效果铺垫，客户仍沉默，后续候选里有活动报价包：推进报价和活动价值，不要因为客户没有回复效果图而空拒。
- 已经发过效果铺垫、活动报价尚未发送，而同一批候选同时出现活动报价包和“未付款效果跟进”：选择活动报价包；若缺匹配订单，删除其中 payment_collection 并把收款承诺改成自然的活动价值轻触达，不能先发“未付款跟进”。
- 已经报价，客户仍沉默，后续候选里有预约金价值或收款包：可推进预约金价值；如果客户明确拒付、已付、投诉/付款异常/身体不适，则不发该包。

# Text Adjustment Policy
- 由你语义判断是否需要润色，不按关键词机械判断。
- 调整目的仅限于让既有 SOP 更像真人顺着当前聊天自然发出：可调整称呼、语气、连接句、表达顺序，以及 text 消息的拆合和数量。
- 这是企业微信一对一聊天，不是群发公告、短信通知或机构宣传稿。称呼可以用“您”或“亲”，也可以直接接上文；不要用“尊敬的客户/尊敬的顾客”这类式称呼。
- 如果原文像系统通知或公告，不能只换一两个词；要在不改事实和阶段目标的前提下，改成销售正在微信里接着聊的短句。避免“您好，温馨提醒”“请及时参与”“本机构现隆重开展”“诚邀您参与”等通知体。
- 聊天口吻应该是短、直接、有上下文：先顺着客户刚才的问题或前序阶段，再说本包要推进的内容。不要写“温馨提醒、及时参与、感谢您的关注”这类客服模板句。
- 最近一条真实客户消息包含明确顾虑、问题或不便，且最终决定发送时，最早一条可编辑 text 必须先用一句短话直接承接该内容，再衔接原话术包目标；原文已经自然承接时不必硬改。
- “共情”必须对应客户真实表达，不能机械添加“理解您、确实不容易”；客户只是普通询问时直接回答并衔接即可。
- 只有 `send_sop=true` 时才能输出 `text_adjustments/message_operations`；调整不能把拒发冲突改写成可发，润色不能把拒发冲突改写成可发。
- 可用 `message_operations`：
  - `insert_text_before/insert_text_after`：只插入不含新数字事实的 text，用于补一句承接或把通知体拆得更像聊天。
  - `remove_text`：只删除不含数字事实的多余 text，不能删除最后一条付款说明 text。
  - `merge_text`：合并多条 text，必须保留这些 text 的全部数字事实。
  - `split_text`：拆分一条 text，拆分后必须保留原 text 的全部数字事实。
  - `replace_text`：等同 text_adjustments，改写同一 order 的 text。
  - `remove_message`：仅用于删除 `payment_collection_gate.status` 不支持发送的 `payment_collection`，必须同步调整 text，不能让客户以为同轮已经发了收款卡。
- 除 `remove_message` 删除不支持发送的 `payment_collection` 外，`message_operations` 只能操作 editable text；不能操作其他 `readonly_messages`，不能新增 image/video/payment_collection/store_address/human_handoff_notice，不能把 text 改成其他消息类型。
- 必须保留该文本的阶段目标、已有价格、金额、优惠、退款口径、门店、日期时间、支付方式及承诺边界。
- 所有数字及其出现次数必须与对应原文一致，不能为了口语化重复或省略金额。
- 不能编造新事实，不能把普通答疑改成另一阶段的强推销，不能新增催付、预约承诺、门店事实或效果承诺。
- `store_address`、`image`、`video`、`human_handoff_notice` 永远保持原样；若 text 与这些只读消息有关，润色不得改变其事实含义。`payment_collection` 只有在 gate 明确不支持时才可删除，不能改金额或复制生成。

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
  "text_adjustments": [{{"order": 1, "text": "仅改写已有 text 的润色结果"}}],
  "message_operations": [{{"op": "insert_text_after", "after_order": 1, "text": "只新增一句无新事实的承接 text"}}]
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
        memory_store: Any | None = None,
        customer_context_service: Any | None = None,
        event_model_retry_attempts: int = 3,
        event_model_retry_delay_seconds: float = 1.0,
        event_model_attempt_timeout_seconds: float = 45.0,
        event_model_max_concurrency: int = 2,
    ) -> None:
        self.repository = repository
        self.sop_reply_pack_service = sop_reply_pack_service
        self.model_client = model_client
        self.memory_store = memory_store
        self.customer_context_service = customer_context_service
        self.event_model_retry_attempts = max(1, int(event_model_retry_attempts or 1))
        self.event_model_retry_delay_seconds = max(0.0, float(event_model_retry_delay_seconds or 0.0))
        self.event_model_attempt_timeout_seconds = max(1.0, float(event_model_attempt_timeout_seconds or 45.0))
        self._event_model_semaphore = asyncio.Semaphore(max(1, int(event_model_max_concurrency or 1)))

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
            "sop_progress_evidence": {
                "completed_pack_ids": [],
                "completed_categories": [],
                "unfinished_sops": [],
            },
            "task": {},
            "model_usage": {},
            "text_adjustments": [],
            "message_operations": [],
            "message_adjustment": {},
            "order_gate": {},
            "error": "",
        }
        try:
            if is_platform_auto_opening_message(request.content):
                identity = _chat_identity(request, request_context)
                if not _string(identity.get("wechat")):
                    result.update(
                        {
                            "mode": "missing_sales_account_scope",
                            "send_sop": False,
                            "need_ai_reply": False,
                            "reason": "wechat_required_for_sop_scope",
                        }
                    )
                    return _finish(result, started)
                customer_memory = self._load_chat_customer_memory(identity)
                order_gate = self._load_chat_order_gate(
                    request=request,
                    request_context=request_context,
                    identity=identity,
                    customer_memory=customer_memory,
                )
                result["order_gate"] = order_gate.get("summary", {})
                if _apply_chat_order_gate_block(result, order_gate):
                    return _finish(result, started)
                self._handle_platform_auto_opening(
                    result=result,
                    request=request,
                    request_id=request_id,
                    request_context=request_context,
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
            if not _string(identity.get("wechat")):
                result.update(
                    {
                        "mode": "missing_sales_account_scope",
                        "send_sop": False,
                        "need_ai_reply": True,
                        "reason": "wechat_required_for_sop_scope",
                    }
                )
                return _finish(result, started)
            enabled_packs = _enabled_chat_packs(self.sop_reply_pack_service.load())
            if not enabled_packs:
                result.update({"mode": "complete", "reason": "no_enabled_sop_packs"})
                return _finish(result, started)

            completed_ids = set(
                self.repository.list_sent_sop_pack_ids_for_customer(
                    customer_id=identity["customer_id"],
                    external_userid=identity["external_userid"],
                    corp_id=identity.get("corp_id", ""),
                    wechat=identity.get("wechat", ""),
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
            result["sop_progress_evidence"] = {
                "completed_pack_ids": sorted(completed_ids),
                "completed_categories": sorted(completed_categories),
                "unfinished_sops": [_sop_progress_summary(pack) for pack in unfinished],
            }
            if not unfinished:
                result.update({"mode": "complete", "reason": "all_sop_packs_completed"})
                return _finish(result, started)

            customer_memory = self._load_chat_customer_memory(identity)
            order_gate = self._load_chat_order_gate(
                request=request,
                request_context=request_context,
                identity=identity,
                customer_memory=customer_memory,
            )
            result["order_gate"] = order_gate.get("summary", {})
            if _apply_chat_order_gate_block(result, order_gate):
                return _finish(result, started)
            if _chat_gate_realtime_customer_message_should_use_ai(request, request_context):
                result.update(
                    {
                        "mode": "realtime_customer_ai_reply",
                        "send_sop": False,
                        "need_ai_reply": True,
                        "reason": "workflow_compatible_customer_message_use_ai_reply",
                    }
                )
                return _finish(result, started)

            selector_input = _chat_selector_input(request, unfinished)
            result["selector_input"] = compact(selector_input, max_chars=6000)
            selector_output = await self._select_chat_sop(selector_input)
            result["selector_output"] = selector_output
            result["model_usage"] = dict(self.model_client.last_usage or {})
            result["text_adjustments"] = _text_adjustments(selector_output.get("text_adjustments"))
            result["message_operations"] = _message_operations(selector_output.get("message_operations"))
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

            adjusted_messages, adjustment_summary = apply_sop_text_adjustments(
                _pack_messages(selected),
                result["text_adjustments"],
                result["message_operations"],
            )
            result["message_adjustment"] = adjustment_summary
            messages, sanitize_summary = sanitize_sop_reply_messages(
                adjusted_messages,
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

            if not _chat_sop_payment_collection_supported(
                messages,
                request=request,
                customer_memory=customer_memory,
                customer_context=order_gate.get("customer_context", {}),
            ):
                result.update(
                    {
                        "mode": "skipped_missing_payment_order",
                        "send_sop": False,
                        "need_ai_reply": True,
                        "reason": "payment_collection_requires_matching_current_order",
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

    def _load_chat_customer_memory(self, identity: dict[str, str]) -> dict[str, Any]:
        """Load existing memory for fresh order selection without mutating it."""
        if not self.memory_store:
            return {}
        scope = customer_scope_from_identity(identity)
        if not scope.persistence_allowed:
            return {}
        try:
            memory = self.memory_store.load(scope.sales_contact_key)
        except Exception:
            return {}
        return memory if isinstance(memory, dict) else {}

    def _load_chat_order_gate(
        self,
        *,
        request: ChatRequest,
        request_context: dict[str, Any],
        identity: dict[str, str],
        customer_memory: dict[str, Any],
    ) -> dict[str, Any]:
        """Freshly query the current order before a chat-gate model or SOP can run."""
        if not self.customer_context_service:
            return {"status": "not_configured", "customer_context": {}, "summary": {"source": "not_configured"}}
        effective_context = _chat_order_request_context(request, request_context, identity)
        try:
            context = self.customer_context_service.load(
                customer_id=identity.get("customer_id", ""),
                memory=customer_memory,
                request_context=effective_context,
            )
        except Exception as exc:
            return {
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "summary": {"source": "exception"},
            }
        if not isinstance(context, dict) or context.get("source") != "platform_agent" or context.get("orders_error"):
            error = str(
                (context or {}).get("orders_error")
                or (context or {}).get("error")
                or "platform_order_context_unavailable"
            )
            return {
                "status": "failed",
                "error": error,
                "summary": {"source": str((context or {}).get("source") or "unknown")},
            }
        basic = customer_memory.get("basic_info") if isinstance(customer_memory.get("basic_info"), dict) else {}
        stored = basic.get("deposit_state")
        stored_fact = stored if isinstance(stored, dict) else {}
        payment = resolved_payment_fact(
            orders=context.get("orders"),
            existing_state=str(stored_fact.get("status") or stored or ""),
            existing_source=str(stored_fact.get("source") or ""),
            existing_fact=stored_fact,
        )
        summary = {
            "source": "platform_agent.order_index",
            "order_count": len(context.get("orders") or []),
            "order_id": str(payment.get("order_id") or ""),
            "store_id": str(payment.get("store_id") or ""),
            "deposit_state": str(payment.get("deposit_state") or "unknown"),
            "prepay_required": payment.get("prepay_required"),
            "prepay_paid": payment.get("prepay_paid"),
        }
        return {
            "status": "paid" if is_paid_deposit_state(payment.get("deposit_state")) else "unpaid",
            "customer_context": context,
            "payment": payment,
            "summary": summary,
        }

    def _handle_platform_auto_opening(
        self,
        *,
        result: dict[str, Any],
        request: ChatRequest,
        request_id: str,
        request_context: dict[str, Any],
    ) -> None:
        """Send the configured first-add opening for WeCom's automatic add-friend event."""
        pack = _platform_auto_opening_pack(self.sop_reply_pack_service.load())
        if not pack:
            result.update(
                {
                    "mode": "platform_auto_opening_config_error",
                    "send_sop": False,
                    "need_ai_reply": False,
                    "reason": "platform_auto_opening_pack_unavailable",
                    "error": "s10_new_customer_opening is not enabled with reply messages",
                }
            )
            return

        identity = _chat_identity(request, request_context)
        messages, sanitize_summary = sanitize_sop_reply_messages(
            _pack_messages(pack),
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
                    "mode": "platform_auto_opening_config_error",
                    "send_sop": False,
                    "need_ai_reply": False,
                    "reason": "platform_auto_opening_pack_has_no_messages",
                }
            )
            return

        task = self._record_chat_gate_task(
            request=request,
            request_id=request_id,
            request_context=request_context,
            identity=identity,
            pack=pack,
            reply_messages=messages,
            trigger_source="platform_auto_opening",
        )
        if task.get("status") == "sent":
            result.update(
                {
                    "mode": "platform_auto_opening_sop",
                    "send_sop": True,
                    "sop_pack_id": str(pack.get("id") or ""),
                    "sop_pack_name": str(pack.get("name") or ""),
                    "need_ai_reply": False,
                    "reason": "platform_auto_opening_first_add_sop",
                    "reply_messages": messages,
                    "task": task,
                }
            )
            return

        result.update(
            {
                "mode": "platform_auto_opening_duplicate",
                "send_sop": False,
                "need_ai_reply": False,
                "reason": str(task.get("error") or "platform_auto_opening_sop_already_sent"),
                "task": task,
            }
        )

    async def _select_chat_sop(self, selector_input: dict[str, Any]) -> dict[str, Any]:
        messages = [
            {
                "role": "system",
                "content": (
                    "# SOP Gate Role\n"
                    "你是企业微信线上活动接待链路里的 SOP Gate，不是客服回复模型。\n"
                    "你只判断“本轮是否先发送一个已配置 SOP 话术包”，以及“发送 SOP 后是否还必须继续异步 AI 回复”。\n"
                    "你只能通过 text_adjustments 和 message_operations 调整所选话术包中的 text，让静态 SOP 更像真人顺着当前聊天发出；不能改变结构消息或调用工具，也不能补门店、价格、档期、案例或订单事实。\n\n"
                    "# Business Mission\n"
                    "当前业务目标是让新客按销冠主线完成前置认知：活动介绍、信任建立、效果/案例铺垫、费用规则、预约金价值和下一步成交动作。\n"
                    "普通聊天 AI 负责回答复杂实时问题；SOP Gate 负责在新客 SOP 未完成前，优先把配置好的话术包按客户当前阶段铺出去。\n"
                    "如果 SOP 已经覆盖客户当前关心点，不要再让 AI 补发，避免客户同一轮收到重复内容。\n\n"
                    "# Source Priority\n"
                    "判断时按当前消息、最近对话、unfinished_sops 的 purpose/order/tags/triggers/reply_messages 摘要排序。\n"
                    "最终是否可发必须以 reply_messages 摘要能否真实回答当前问题为准；purpose 和 triggers 只是候选线索，不能替代消息内容匹配。\n"
                    "SOP Gate 不拥有门店、档期、支付、订单、案例事实；这些事实缺失时不能自行补全，只能决定是否让普通 AI 继续。\n\n"
                    "# Input\n"
                    "你会收到：\n"
                    "- current_message：客户当前消息。\n"
                    "- conversation_history：最近极短对话。\n"
                    "- conversation_activity：事件层整理的当前会话状态，只是事实证据，包含最新一条是否客户待回复、是否小贝/销售正在等客户、沉默时长和时间可靠性。\n"
                    "- unfinished_sops：尚未发送过的 SOP 包，只包含 id/name/purpose/order/tags/triggers/reply_messages 摘要。\n"
                    "你不会收到完整门店事实、档期事实、案例结果或订单详情，因此不能判断这些事实本身。\n\n"
                    "# Task\n"
                    "1. 先理解客户当前消息和最近对话处于哪个成交阶段。\n"
                    "2. 从 unfinished_sops 中最多选择一个当前最该先发的 SOP。\n"
                    "3. 逐条核对该 SOP 的 reply_messages 摘要：它是否真正回答客户当前问题，还是只覆盖相邻但不同的顾虑。\n"
                    "4. 只有 SOP 无法覆盖、且客户问题必须实时答复时，才设置 need_ai_reply=true。\n"
                    "5. 决定发送时，检查 editable_text_messages 的称呼、语气、条数和顺序是否自然承接当前消息；不自然时输出 text_adjustments 或 message_operations。\n\n"
                    "# Decision Policy\n"
                    "- 新客 SOP 没完成前，默认优先选择一个合适 SOP，不要轻易跳过直接进入普通 AI 聊天。\n"
                    "- 选择 SOP 时先看 purpose/order/triggers 找候选，再用 reply_messages 摘要做最终覆盖判断；不要只按关键词或宽泛目的机械匹配。\n"
                    "- 如果多个 SOP 都可用，选择最靠近当前成交阶段且 order 更靠前的一个。\n"
                    "- 如果 conversation_activity.assistant_waiting_customer=true，说明上一轮小贝/销售已经发出问题或铺垫后客户沉默。客户沉默不是拒绝，也不是永久跳过 SOP；但下一步要像真人销售一样轻触承接，避免直接跳到大段报价、收款或无过渡的下一阶段。\n"
                    "- 如果上一轮主要是在问城市、区域、门店、斑点情况、姓名电话或时间，客户未回复，本轮候选 SOP 必须能自然承接这个未完成问题；可以通过 text_adjustments/message_operations 加一句轻触或换一种问法，再衔接原包目标。不要一上来堆效果、报价和预约金。\n"
                    "- 如果 conversation_activity.latest_customer_pending_ai_reply=true，正常情况下事件层会拦截；若仍进入这里，也应 send_sop=false、need_ai_reply=true，避免定时 SOP 抢在普通 AI 前面。\n"
                    "- 发送 SOP 后默认 need_ai_reply=false。\n"
                    "- 如果选中的 SOP 的实际消息已经覆盖价格、效果、活动、预约金、普通顾虑、品牌信任或成交推进诉求，即使客户问题明确，也保持 need_ai_reply=false。\n"
                    "- 只有以下情况才允许 need_ai_reply=true：客户明确索要具体门店地址/导航/真实档期/预约或订单状态；投诉退款、身体不适、强人工诉求；客户在追问项目内容、费用包含、是否只是检测/清洁/洗脸、是否真正包含斑点改善，而候选 SOP 只是泛效果图/案例/能不能做铺垫；或客户同一句包含多个独立问题，而当前 SOP 只覆盖其中一部分。\n"
                    "- 客户刚刚被问城市/区域/定位/附近门店后，本轮补充了城市、区、地标、定位或详细地址，这是“门店匹配槽位已补齐”，不属于静态 SOP 铺垫；必须 send_sop=false、need_ai_reply=true，交给普通 AI 基于门店工具匹配门店并承接节奏。\n"
                    "- 客户一边给出位置，一边说正在上班、没时间、之后再联系、先加微信方便联系，本质仍是“位置事实 + 到店时间顾虑”；先让普通 AI 回应当前位置和时间顾虑、匹配门店或轻推名额，不要直接用活动介绍包覆盖。\n"
                    "- 如果所有 unfinished_sops 都明显不适合当前客户状态，可以 send_sop=false，并让普通 AI 继续处理。\n\n"
                    "# Contextual Text Adjustment\n"
                    "- 只有 send_sop=true 时才能输出 text_adjustments/message_operations；每项 order 必须来自所选包的 editable_text_messages 或以 text order 为锚点。\n"
                    "- 客户当前有明确顾虑、问题或不便时，最早一条可编辑 text 应先用一句短话直接承接，再自然衔接原包目标；原文已经承接时输出空数组。\n"
                    "- 不要机械添加“理解您、确实不容易”；普通询问直接回答并衔接即可。\n"
                    "- 必须保留原文的阶段目标、价格、金额、优惠、退款、门店、时间、支付及承诺边界。\n"
                    "- 所有数字及其出现次数必须与对应原文完全一致，不能重复、删减或改写数字。\n"
                    "- message_operations 只允许 replace_text、insert_text_before、insert_text_after、remove_text、merge_text、split_text、remove_message。插入 text 不能新增数字事实，删除 text 不能删除数字事实，合并/拆分必须保留全部数字事实。\n"
                    "- image、video、store_address、human_handoff_notice 都是只读消息，不能改写、删除、重排、复制或由 text 转换生成。payment_collection 只有在 payment_collection_gate.status 不支持发送时才可用 remove_message 删除，不能改金额或复制生成。\n\n"
                    "# Negative Cases\n"
                    "- 客户只是沉默、刚加微后未回复、或上一阶段 SOP 正常铺垫后没有新 customer 消息：不算冲突，优先继续 SOP；但如果最近一轮刚问了一个关键问题而客户没答，优先轻触/承接这个问题，不要机械跳阶段。\n"
                    "- 客户正在问具体门店地址、真实档期、订单/付款异常、投诉退款或身体不适：SOP 不足以覆盖，need_ai_reply=true。\n"
                    "- 客户回复城市、区、商圈、地标或定位，是给普通 AI 做门店匹配的事实输入；即使候选 SOP 能讲活动、价格或预约金，也不能替代门店匹配回复。\n"
                    "- 候选包只是和历史同属一个活动主题，不等于严重重合；只有同阶段核心目的和核心素材都已覆盖，才算严重重合。\n"
                    "- 普通价格、效果、信任、隐形消费顾虑如果候选 SOP 的实际消息已覆盖，就不需要额外 AI 文案。\n"
                    "- 如果客户当前问的是效果真实性、怕没效果、反黑、做坏、伤肤等效果/安全顾虑，而候选包实际消息主要是收费、预约金、隐形消费或活动价格规则，则该 SOP 没有覆盖当前问题；send_sop=false，need_ai_reply=true，交给普通 AI 结合历史案例和当前上下文回答并推进。\n"
                    "- 如果客户当前问的是“268/活动价是否只包含检测、清洁、扫斑、洗脸，没有真正斑点改善”这类项目内容与费用包含顾虑，泛效果案例包不能覆盖；除非候选 SOP 的实际消息明确解释“检测清洁是前置步骤、不是全部项目、适合后会做斑点改善、费用透明”，否则 send_sop=false，need_ai_reply=true。\n"
                    "- 如果客户当前问的是套路、乱收费、强制消费、预约金抵扣/可退或费用是否清楚，而候选包实际消息正是在解释明码标价和预约金规则，可以发送该 SOP。\n\n"
                    "# Few-Shot Calibration\n"
                    "- 新客未回复，1分钟介绍包已发，5分钟问地址包候选可用：send_sop=true，need_ai_reply=false，可以把文案润色成更轻的“亲，还在吗，我先确认下您在哪个城市/区域，方便给您匹配门店”。\n"
                    "- 上一轮小贝问“您在哪个城市哪个区”，客户沉默 5 分钟，候选效果或报价包可用：不要直接跳到效果/报价；优先选择能追问位置或轻触承接的包，或调整当前包首条 text 先轻触未答问题。\n"
                    "- 客户刚问“这家地址发我”：如果候选 SOP 不是门店地址事实，send_sop=false 或 need_ai_reply=true，交给普通 AI 查门店。\n"
                    "- 上一轮小贝问“您在哪个城市哪个区，我把离您最近的门店位置发您”，客户回“我现在在黄浦区，现在上班没时间，先加微信后面联系”：send_sop=false，need_ai_reply=true，交给普通 AI 匹配黄浦区/同城门店并顺着没时间顾虑承接。\n"
                    "- 客户问“效果怎么样”：候选效果案例/效果铺垫包可用且未发送过，send_sop=true，need_ai_reply=false。\n"
                    "- 客户问“真有这么好的效果？”且历史已经发过效果图/案例，候选包是收费与预约金顾虑处理：该包没有回答效果真实性，send_sop=false，need_ai_reply=true。\n"
                    "- 客户问“应该只是检测和洗脸，没有去斑吧？”且候选包是效果案例图：该包没有解释项目内容和费用包含，send_sop=false，need_ai_reply=true。\n"
                    "- 客户问“是不是套路，会不会乱收费？”且候选包是收费与预约金顾虑处理：该包覆盖收费和信任顾虑，send_sop=true，need_ai_reply=false。\n"
                    "- 客户说“我付款多扣了”：send_sop=false，need_ai_reply=true。\n\n"
                    "# Do Not\n"
                    "- 不生成独立客户回复；可以为承接自然插入少量 text，但不得新增 image/payment_collection/store_address/video/handoff。\n"
                    "- 不改写所选包之外的文案，不补写新的话术包。\n"
                    "- 不输出工具名、门店名、价格细节、档期承诺、案例描述。\n"
                    "- 不因为客户问价格/效果/预约/顾虑就自动 need_ai_reply=true；先看 SOP 是否已覆盖。\n"
                    "- 不输出旧链路字段、阶段分析长文或多余 JSON 字段。\n\n"
                    "# Output\n"
                    "只能输出 JSON，字段必须是 send_sop、sop_pack_id、need_ai_reply、reason、text_adjustments、message_operations。"
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
                    '  "reason": "一句内部原因",\n'
                    '  "text_adjustments": [{"order": 1, "text": "只改写所选包已有 text"}],\n'
                    '  "message_operations": [{"op": "insert_text_after", "after_order": 1, "text": "只新增无新事实的 text"}]\n'
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
        conversation_activity: dict[str, Any] | None = None,
        customer_memory: dict[str, Any] | None = None,
        customer_context: dict[str, Any] | None = None,
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
            "message_operations": [],
            "model_usage": {},
            "error": "",
        }
        try:
            sent_before = _event_created_at(payload)
            completed_ids = self.repository.list_sent_sop_pack_ids_for_customer(
                customer_id=identity.get("customer_id", ""),
                external_userid=identity.get("external_userid", ""),
                corp_id=identity.get("corp_id", ""),
                wechat=identity.get("wechat", ""),
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
                conversation_activity=conversation_activity or {},
                customer_memory=customer_memory or {},
                customer_context=customer_context or {},
                candidate_packs=candidate_packs,
                actions_reply_messages=actions_reply_messages,
                completed_sop_pack_ids=completed_ids,
                completed_sop_categories=completed_categories,
            )
            result["selector_input"] = compact(selector_input, max_chars=6000)
            selector_output, model_attempts, model_error = await self._judge_event_sop_with_retries(selector_input)
            result["model_attempts"] = model_attempts
            if model_error:
                result.update(
                    {
                        "mode": "event_model_error",
                        "send_sop": False,
                        "need_ai_reply": False,
                        "error": model_error,
                        "reason": "event_sop_model_retries_exhausted",
                    }
                )
                return _finish(result, started)
            result["selector_output"] = selector_output
            result["model_usage"] = dict(self.model_client.last_usage or {})
            result["text_adjustments"] = _text_adjustments(selector_output.get("text_adjustments"))
            result["message_operations"] = _message_operations(selector_output.get("message_operations"))

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
                    "need_ai_reply": bool(selector_output.get("need_ai_reply")),
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

    async def _judge_event_sop_with_retries(
        self,
        selector_input: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
        attempts: list[dict[str, Any]] = []
        last_error = ""
        for attempt in range(1, self.event_model_retry_attempts + 1):
            started = time.perf_counter()
            deadline = time.monotonic() + self.event_model_attempt_timeout_seconds
            try:
                async with self._event_model_semaphore:
                    output = await self._judge_event_sop(selector_input, deadline_monotonic=deadline)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                attempts.append(
                    {
                        "attempt": attempt,
                        "status": "failed",
                        "duration_ms": int((time.perf_counter() - started) * 1000),
                        "error": last_error,
                        "model_usage": compact(self.model_client.last_usage or {}, max_chars=1800),
                    }
                )
                if attempt < self.event_model_retry_attempts and self.event_model_retry_delay_seconds:
                    await asyncio.sleep(self.event_model_retry_delay_seconds)
                continue
            attempts.append(
                {
                    "attempt": attempt,
                    "status": "succeeded",
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                    "model_usage": compact(self.model_client.last_usage or {}, max_chars=1800),
                }
            )
            return output, attempts, ""
        return {}, attempts, last_error or "event model retries exhausted"

    async def _judge_event_sop(
        self,
        selector_input: dict[str, Any],
        *,
        deadline_monotonic: float | None = None,
    ) -> dict[str, Any]:
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
        data = await self.model_client.chat_json(
            messages,
            tier="reply",
            temperature=0,
            deadline_monotonic=deadline_monotonic,
        )
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
        trigger_source: str = "chat_gate",
    ) -> dict[str, Any]:
        event_id = f"{trigger_source}:{request_id}"
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
            trigger_source=trigger_source,
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


def _platform_auto_opening_pack(config: dict[str, Any]) -> dict[str, Any]:
    packs = config.get("packs") if isinstance(config.get("packs"), list) else []
    for pack in packs:
        if not isinstance(pack, dict):
            continue
        if _string(pack.get("id")) != "s10_new_customer_opening":
            continue
        if not bool(pack.get("enabled")) or not _pack_messages(pack):
            return {}
        if not (_pack_has_scope(pack, "chat_gate") or _pack_has_scope(pack, "event_first_add")):
            return {}
        return pack
    return {}


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
    match_context: dict[str, Any] | None = None,
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
        if _is_final_close_pack(pack) and not _final_close_context_matches(pack, delay_minutes, match_context):
            continue
        candidates.append(
            _annotated_first_add_candidate(
                pack,
                group="due",
                reason_hint="currently_due_or_overdue",
            )
        )
    candidates = sorted(candidates, key=lambda item: (int(item.get("order") or 0), str(item.get("id") or "")))

    if delay_minutes <= 0:
        return candidates
    future_candidates: list[dict[str, Any]] = []
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
        if pack_delay <= delay_minutes:
            continue
        if _is_final_close_pack(pack) and not _final_close_context_matches(pack, delay_minutes, match_context):
            continue
        future_candidates.append(
            _annotated_first_add_candidate(
                pack,
                group="next_step",
                reason_hint="fallback_next_unfinished_step_when_due_candidates_repeat_or_conflict",
            )
        )
    if not future_candidates:
        return candidates
    next_delay = min(_int(pack.get("delay_minutes"), 0) for pack in future_candidates)
    next_step_candidates = sorted(
        [pack for pack in future_candidates if _int(pack.get("delay_minutes"), 0) == next_delay],
        key=lambda item: (int(item.get("order") or 0), str(item.get("id") or "")),
    )[:FIRST_ADD_NEXT_STEP_MAX_CANDIDATES]
    if not candidates:
        return next_step_candidates
    if next_delay - delay_minutes <= FIRST_ADD_NEXT_STEP_LOOKAHEAD_MINUTES:
        return candidates + next_step_candidates
    return candidates


def _annotated_first_add_candidate(pack: dict[str, Any], *, group: str, reason_hint: str) -> dict[str, Any]:
    candidate = dict(pack)
    candidate["_candidate_group"] = group
    candidate["_selection_reason_hint"] = reason_hint
    return candidate


def _is_final_close_pack(pack: dict[str, Any]) -> bool:
    if _pack_category(pack) == "final_close":
        return True
    if _string(pack.get("stage_tag")) == "final_close":
        return True
    return "day1_18_final_close" in {str(item) for item in pack.get("triggers") or []}


def _final_close_context_matches(pack: dict[str, Any], delay_minutes: int, match_context: dict[str, Any] | None) -> bool:
    # Keep backward compatibility for platform schedules that still express the
    # final close as a late delay, while allowing explicit 18:00 stage triggers.
    if delay_minutes >= 600:
        return True
    if not match_context:
        return False
    context_values = {
        _string(match_context.get("customer_state")),
        _string(match_context.get("stage_tag")),
        _string(match_context.get("day_stage")),
    }
    pack_values = {
        _string(pack.get("customer_state")),
        _string(pack.get("stage_tag")),
        _string(pack.get("day_stage")),
    }
    if any(value and value in pack_values for value in context_values):
        return True
    event_id = _string(match_context.get("event_id"))
    if event_id:
        return any(str(trigger or "").strip() and str(trigger).strip() in event_id for trigger in pack.get("triggers") or [])
    return False


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
    conversation = customer.get("conversation") if isinstance(customer.get("conversation"), dict) else {}
    return {
        "event_type": _string(payload.get("event_type")),
        "delay_minutes": _string(customer_sop.get("delay_minutes")) or _string(root_sop.get("delay_minutes")),
        "day_stage": _string(customer_sop.get("day_stage")) or _string(root_sop.get("day_stage")),
        "customer_state": _string(customer_sop.get("customer_state")) or _string(root_sop.get("customer_state")),
        "stage_tag": _string(customer_sop.get("stage_tag")) or _string(root_sop.get("stage_tag")),
        "platform_task_id": _string(customer_sop.get("platform_task_id")) or _string(root_sop.get("platform_task_id")),
        "first_added_trace_id": _string(first_added.get("trace_id")),
        "ai_auto_reply": conversation.get("ai_auto_reply"),
    }


def _conversation_context(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Preserve message provenance and timing in the event model context."""
    output: list[dict[str, Any]] = []
    for item in messages[-30:]:
        if not isinstance(item, dict):
            continue
        direction = _string(item.get("direction") or item.get("role") or item.get("sender_type") or item.get("from"))
        source = _string(item.get("source"))
        message_type = _string(item.get("msgtype") or item.get("message_type") or item.get("type"))
        content = _message_text(item.get("content"))
        message_time = next(
            (item.get(key) for key in ("msgtime", "timestamp", "created_at", "time") if item.get(key) not in (None, "")),
            "",
        )
        if not any((direction, source, message_type, content, message_time)):
            continue
        output.append(
            {
                "direction": direction,
                "source": source,
                "message_type": message_type,
                "content": content[:300],
                "message_time": message_time,
            }
        )
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


def _sop_summary(
    pack: dict[str, Any],
    *,
    customer_memory: dict[str, Any] | None = None,
    customer_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    messages = _pack_messages(pack)
    return {
        "id": str(pack.get("id") or ""),
        "scope": _pack_scope(pack),
        "scopes": _pack_scopes(pack),
        "sop_category": _pack_category(pack),
        "name": str(pack.get("name") or ""),
        "purpose": str(pack.get("purpose") or "")[:240],
        "order": int(pack.get("order") or 0),
        "candidate_group": _string(pack.get("_candidate_group")) or "due",
        "selection_reason_hint": _string(pack.get("_selection_reason_hint")),
        "tags": [str(item) for item in pack.get("triggers") or [] if str(item or "").strip()],
        "event_type": str(pack.get("event_type") or ""),
        "delay_minutes": int(pack.get("delay_minutes") or 0),
        "stage_tag": str(pack.get("stage_tag") or ""),
        "reply_messages_summary": _messages_summary(messages),
        "payment_collection_gate": _payment_collection_gate_summary(
            messages,
            customer_memory=customer_memory or {},
            customer_context=customer_context or {},
        ),
        **_message_editing_context(messages),
    }


def _sop_progress_summary(pack: dict[str, Any]) -> dict[str, Any]:
    """Expose workflow progress without leaking static SOP message bodies."""
    return {
        "id": str(pack.get("id") or ""),
        "sop_category": _pack_category(pack),
        "name": str(pack.get("name") or ""),
        "purpose": str(pack.get("purpose") or "")[:240],
        "order": int(pack.get("order") or 0),
        "triggers": [str(item) for item in pack.get("triggers") or [] if str(item or "").strip()],
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
    conversation_activity: dict[str, Any],
    customer_memory: dict[str, Any],
    customer_context: dict[str, Any],
    candidate_packs: list[dict[str, Any]],
    actions_reply_messages: list[dict[str, Any]],
    completed_sop_pack_ids: list[str],
    completed_sop_categories: list[str],
) -> dict[str, Any]:
    memory_context = _customer_memory_context(customer_memory)
    due_count = sum(1 for pack in candidate_packs if _string(pack.get("_candidate_group")) != "next_step")
    next_step_count = sum(1 for pack in candidate_packs if _string(pack.get("_candidate_group")) == "next_step")
    return {
        "mode": "platform_actions" if event_type == "sop_platform_task" else "first_add_flow",
        "event": _event_summary(payload, customer),
        "current_platform_task": {
            "priority": "current_outreach_objective_after_hard_facts",
            "message_content": _platform_task_message_content(payload, customer),
        },
        "recent_conversation": _conversation_context(conversation_messages),
        "conversation_activity": conversation_activity,
        "candidate_policy": {
            "due_candidates": due_count,
            "next_step_candidates": next_step_count,
            "selection_rule": (
                "优先评估 due 候选；如果 due 候选与最近聊天重复、冲突或已被覆盖，"
                "再评估 next_step 候选。next_step 只用于继续同一新客 SOP 节奏，不能编造事实或绕过风险边界。"
            ),
        },
        **memory_context,
        "candidate_sops": [
            _sop_summary(pack, customer_memory=customer_memory, customer_context=customer_context)
            for pack in candidate_packs
        ],
        "platform_actions_summary": _messages_summary(actions_reply_messages),
        "platform_actions": _message_editing_context(actions_reply_messages),
        "platform_payment_collection_gate": _payment_collection_gate_summary(
            actions_reply_messages,
            customer_memory=customer_memory,
            customer_context=customer_context,
        ),
        "current_payment_state": _payment_state_summary(customer_memory, customer_context),
        "completed_sop_pack_ids": completed_sop_pack_ids,
        "completed_sop_categories": completed_sop_categories,
    }


def _platform_task_message_content(payload: dict[str, Any], customer: dict[str, Any]) -> list[dict[str, str]]:
    customer_sop = customer.get("sop") if isinstance(customer.get("sop"), dict) else {}
    root_sop = payload.get("sop") if isinstance(payload.get("sop"), dict) else {}
    customer_task = customer_sop.get("platform_task") if isinstance(customer_sop.get("platform_task"), dict) else {}
    root_task = root_sop.get("platform_task") if isinstance(root_sop.get("platform_task"), dict) else {}
    raw_messages = customer_task.get("message_content") or root_task.get("message_content") or []
    if not isinstance(raw_messages, list):
        return []
    output: list[dict[str, str]] = []
    for item in raw_messages[:12]:
        if not isinstance(item, dict):
            continue
        message_type = _string(item.get("type")) or "text"
        content = _platform_content_text(item.get("content"))
        if content:
            output.append({"type": message_type, "content": content[:1200]})
    return output


def _platform_content_text(value: Any) -> str:
    text = _string(value)
    if len(text) < 2 or text[0] != '"' or text[-1] != '"':
        return text
    try:
        decoded = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return text
    return decoded if isinstance(decoded, str) else text


def _payment_state_summary(customer_memory: dict[str, Any], customer_context: dict[str, Any]) -> dict[str, Any]:
    basic = customer_memory.get("basic_info") if isinstance(customer_memory.get("basic_info"), dict) else {}
    payment = resolved_payment_fact(
        orders=customer_context.get("orders") if isinstance(customer_context, dict) else [],
        existing_state=_string(basic.get("deposit_state")),
        existing_source=_string(basic.get("deposit_source")),
        existing_fact=basic.get("deposit_fact"),
    )
    order_gate = customer_context.get("_sop_order_gate") if isinstance(customer_context.get("_sop_order_gate"), dict) else {}
    return {
        "deposit_state": _string(payment.get("deposit_state")) or _string(order_gate.get("deposit_state")) or "unknown",
        "source": _string(payment.get("source")) or _string(order_gate.get("source")) or "unknown",
        "order_id": _string(payment.get("order_id")) or _string(order_gate.get("order_id")),
        "store_id": _string(payment.get("store_id")) or _string(order_gate.get("store_id")),
        "prepay_required": payment.get("prepay_required", order_gate.get("prepay_required")),
        "prepay_paid": payment.get("prepay_paid", order_gate.get("prepay_paid")),
    }


def _customer_memory_context(memory: dict[str, Any]) -> dict[str, Any]:
    """Expose existing profile and recent events as low-priority model context."""
    portrait = memory.get("portrait") if isinstance(memory.get("portrait"), dict) else {}
    basic_info = memory.get("basic_info") if isinstance(memory.get("basic_info"), dict) else {}
    history_events = memory.get("history_events") if isinstance(memory.get("history_events"), list) else []
    return {
        "customer_profile": portrait,
        "customer_basic_info": basic_info,
        "lifecycle_stage": _string(memory.get("lifecycle_stage")),
        "history_events": [compact(item, max_chars=800) for item in history_events[-12:] if isinstance(item, dict)],
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


def _payment_collection_gate_summary(
    messages: list[dict[str, Any]],
    *,
    customer_memory: dict[str, Any],
    customer_context: dict[str, Any],
) -> dict[str, Any]:
    cards = [
        message
        for message in messages
        if isinstance(message, dict) and _string(message.get("type")) == "payment_collection"
    ]
    if not cards:
        return {"has_payment_collection": False, "status": "not_required"}
    basic = customer_memory.get("basic_info") if isinstance(customer_memory.get("basic_info"), dict) else {}
    payment_fact = resolved_payment_fact(
        orders=customer_context.get("orders") if isinstance(customer_context, dict) else [],
        existing_state=_string(basic.get("deposit_state")),
        existing_source=_string(basic.get("deposit_source")),
        existing_fact=basic.get("deposit_fact"),
    )
    if is_paid_deposit_state(payment_fact.get("deposit_state")) or is_paid_deposit_state(basic.get("deposit_state")):
        return {
            "has_payment_collection": True,
            "status": "paid_skip_card",
            "amounts": [_payment_message_amount(card) for card in cards],
            "source": payment_fact.get("source") or "customer_memory",
        }
    unsupported: list[int] = []
    supported: list[int] = []
    for card in cards:
        amount = _payment_message_amount(card)
        state = {
            "customer_context": customer_context if isinstance(customer_context, dict) else {},
            "customer_basic_info": basic,
            "confirmed_store_id": _string(basic.get("confirmed_store_id")),
            "payment_decision": {"amount": amount},
        }
        if payment_collection_order_fact(state, amount=amount):
            supported.append(amount)
        else:
            unsupported.append(amount)
    if unsupported:
        return {
            "has_payment_collection": True,
            "status": "missing_matching_current_order",
            "amounts": supported + unsupported,
            "supported_amounts": supported,
            "unsupported_amounts": unsupported,
        }
    return {
        "has_payment_collection": True,
        "status": "supported",
        "amounts": supported,
    }


def _payment_message_amount(message: dict[str, Any]) -> int:
    content = message.get("content") if isinstance(message.get("content"), dict) else {}
    return _positive_int(content.get("amount"), 10)


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


def _message_operations(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    output: list[dict[str, Any]] = []
    allowed = {"replace_text", "insert_text_before", "insert_text_after", "remove_text", "merge_text", "split_text", "remove_message"}
    for item in value[:8]:
        if not isinstance(item, dict):
            continue
        op = _string(item.get("op") or item.get("operation"))
        if op not in allowed:
            continue
        normalized: dict[str, Any] = {"op": op}
        if op == "replace_text":
            order = _positive_int(item.get("order"), 0)
            text = _string(item.get("text"))
            if order > 0 and text and len(text) <= 500:
                normalized.update({"order": order, "text": text})
            else:
                continue
        elif op in {"insert_text_before", "insert_text_after"}:
            key = "before_order" if op == "insert_text_before" else "after_order"
            order = _positive_int(item.get(key), 0)
            text = _string(item.get("text"))
            if order > 0 and text and len(text) <= 360:
                normalized.update({key: order, "text": text})
            else:
                continue
        elif op == "remove_text":
            order = _positive_int(item.get("order"), 0)
            if order > 0:
                normalized["order"] = order
            else:
                continue
        elif op == "remove_message":
            order = _positive_int(item.get("order"), 0)
            if order > 0:
                normalized["order"] = order
            else:
                continue
        elif op == "merge_text":
            orders = [_positive_int(order, 0) for order in item.get("orders") or []]
            orders = [order for order in orders if order > 0]
            text = _string(item.get("text"))
            if 2 <= len(orders) <= 4 and text and len(text) <= 700:
                normalized.update({"orders": orders, "text": text})
            else:
                continue
        elif op == "split_text":
            order = _positive_int(item.get("order"), 0)
            texts = [_string(text) for text in item.get("texts") or []]
            texts = [text for text in texts if text]
            if order > 0 and 2 <= len(texts) <= 4 and all(len(text) <= 360 for text in texts):
                normalized.update({"order": order, "texts": texts})
            else:
                continue
        output.append(normalized)
    return output


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


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
    wechat = _string(identity.get("wechat")).lower()
    if not pack_id or not customer_key or not wechat:
        return ""
    corp_id = _string(identity.get("corp_id")).lower()
    customer_kind = "external" if external_userid else "customer"
    return f"sop_pack:{pack_id}|corp:{corp_id}|wechat:{wechat}|{customer_kind}:{customer_key}"


def _sent_categories(repository: Any, identity: dict[str, str], *, sent_before: str = "") -> list[str]:
    func = getattr(repository, "list_sent_sop_categories_for_customer", None)
    if not callable(func):
        return []
    return list(
        func(
            customer_id=identity.get("customer_id", ""),
            external_userid=identity.get("external_userid", ""),
            corp_id=identity.get("corp_id", ""),
            wechat=identity.get("wechat", ""),
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


def _apply_chat_order_gate_block(result: dict[str, Any], order_gate: dict[str, Any]) -> bool:
    """Apply a fail-closed order gate result before any chat SOP can be selected or sent."""
    if order_gate.get("status") == "failed":
        result.update(
            {
                "mode": "failed_order_fetch",
                "send_sop": False,
                "need_ai_reply": True,
                "reason": "sop_gate_order_fetch_failed",
                "error": str(order_gate.get("error") or "platform_order_context_unavailable"),
            }
        )
        return True
    if order_gate.get("status") == "paid":
        result.update(
            {
                "mode": "skipped_deposit_paid",
                "send_sop": False,
                "need_ai_reply": True,
                "reason": "deposit_paid_skip_sop_gate",
            }
        )
        return True
    return False


def _chat_order_request_context(
    request: ChatRequest,
    request_context: dict[str, Any],
    identity: dict[str, str],
) -> dict[str, Any]:
    """Build the platform order query context from the current chat identity."""
    output = dict(request.request_context or {})
    output.update(request_context or {})
    request_values = {
        "corp_id": request.corp_id,
        "user_id": request.user_id,
        "wechat": request.wechat,
        "external_userid": request.external_userid,
        "customer_add_wechat_id": request.customer_add_wechat_id,
        "confirmed_store_id": request.confirmed_store_id or request.store_id,
        "confirmed_store_name": request.confirmed_store_name or request.store_name,
    }
    for key, value in request_values.items():
        if output.get(key) in (None, "") and value not in (None, ""):
            output[key] = value
    for key in ("corp_id", "user_id", "wechat", "external_userid", "customer_id"):
        if output.get(key) in (None, "") and identity.get(key) not in (None, ""):
            output[key] = identity[key]
    return output


def _chat_sop_payment_collection_supported(
    messages: list[dict[str, Any]],
    *,
    request: ChatRequest,
    customer_memory: dict[str, Any],
    customer_context: dict[str, Any],
) -> bool:
    """Require a matching active unpaid order before a chat-gate payment card."""
    payment_message = next(
        (item for item in messages if isinstance(item, dict) and _string(item.get("type")) == "payment_collection"),
        None,
    )
    if not payment_message:
        return True
    content = payment_message.get("content") if isinstance(payment_message.get("content"), dict) else {}
    basic = customer_memory.get("basic_info") if isinstance(customer_memory.get("basic_info"), dict) else {}
    confirmed_store_id = str(
        request.confirmed_store_id
        or request.store_id
        or basic.get("confirmed_store_id")
        or ""
    ).strip()
    state = {
        "customer_context": customer_context,
        "customer_basic_info": basic,
        "confirmed_store_id": confirmed_store_id,
        "payment_decision": {"amount": content.get("amount")},
    }
    return bool(payment_collection_order_fact(state, amount=content.get("amount")))


def _chat_gate_realtime_customer_message_should_use_ai(request: ChatRequest, request_context: dict[str, Any]) -> bool:
    """Do not let chat-gate static packs replace realtime customer replies.

    `/sop/events` owns proactive SOP touches. The workflow-compatible reply
    endpoint receives an actual customer turn, so the normal Planner/Reply
    chain should interpret the customer's current intent with SOP progress as
    evidence instead of sending a static pack as the whole answer.
    """
    merged_context = dict(request.request_context or {})
    merged_context.update(request_context or {})
    if _string(merged_context.get("source_protocol")) != "workflow-compatible":
        return False
    if not _string(request.content):
        return False
    if is_platform_auto_opening_message(request.content):
        return False
    return True


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
