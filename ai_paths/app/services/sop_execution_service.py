from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.policies.sales_flow import (
    mainline_pack_sort_key,
    mainline_stage_for_event_pack,
    mainline_stage_for_event_values,
    mainline_stage_for_pack,
    precision_qa_index_for_gate,
    sales_mainline_for_model,
)
from app.prompts.global_contract import GLOBAL_BUSINESS_RHYTHM_CONTRACT, GLOBAL_STRUCTURED_NODE_CONTRACT
from app.prompts.sop_chat_gate import build_sop_chat_gate_messages, build_sop_chat_gate_repair_messages
from app.schemas import ChatRequest
from app.services.customer_payment_state import is_paid_deposit_state, resolved_payment_fact
from app.services.customer_scope import customer_scope_from_identity
from app.services.model_client import ModelClient
from app.services.sop_event_decision import normalize_event_decision, selected_candidate_packs
from app.services.sop_message_sanitizer import apply_sop_text_adjustments, sanitize_sop_reply_messages
from app.services.sop_reply_pack_service import SopReplyPackService
from app.services.storage.serialization import utc_now_iso
from app.services.trace_logger import compact


FIRST_ADD_NEXT_STEP_LOOKAHEAD_MINUTES = 0
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

聊天轨和沉默事件轨是两套节奏。聊天轨可以随客户回应连续推进；沉默事件轨只能从已经通过计时基准、阶段前置、付款状态和当天频率资格的 `candidate_sops` 中选择。沉默事件轨按“门店轻触 -> 效果铺垫 -> 活动报价 -> 预约金 -> 未付跟进”渐进，不能把完整聊天包当成沉默跟进包，也不能在活动报价前发送预约金卡或催付。

{GLOBAL_BUSINESS_RHYTHM_CONTRACT}

# Input
你会收到：
- `mode`：`first_add_flow` 或 `platform_actions`。
- `event`：触发事件、延迟、阶段和客户状态。
- `recent_conversation`：最近 30 条已发生聊天，保留方向、来源、消息类型和时间。
- `conversation_activity`：基于最新会话计算的客户回复、最后消息方向和时间可靠性摘要。
- `candidate_sops`：可选的新客 SOP；每个包有阶段目的、候选分组、完整 `editable_text_messages` 与只读 `readonly_messages`。
- `mainline_stage_status`：按销售主线整理的阶段状态、结构完成证据、按时间线整理的聊天覆盖证据和本轮候选包。它用于判断最早未完成阶段，不能被 raw `order` 覆盖。
- `platform_actions`：平台任务中的完整可编辑 text 与只读结构消息。
- `current_platform_task.message_content`：平台本轮明确要求触达的原始内容，是 `platform_actions` 模式下的当前任务目标。
- `completed_sop_pack_ids`、`completed_sop_categories`：已经发送过的包与类目。
- `event_policy_evidence`：代码整理的触达频率、夜间积压和普通 AI 接管资格事实。
- `mainline`：当前配置的销售主线阶段，只用于判断未完成步骤和相邻步骤。
- `customer_profile`、`customer_basic_info`、`lifecycle_stage`、`history_events`：已有客户画像、基础信息、生命周期和最近历史事件，只用于补充背景。

`editable_text_messages` 是主要可操作文本素材。`readonly_messages` 中的图片、视频、门店卡和内部 notice 都是结构事实，不能修改、删除、重排或复制。
`payment_collection` 也是结构事实；只有当输入里的 `payment_collection_gate.status` 明确为 `paid_skip_card`，且当前阶段仍适合轻触达时，才允许用 `message_operations.remove_message` 删除该预约金卡，并同步把 text 改成不承诺“已发入口/付完”的自然轻触达。缺匹配订单不是删卡理由。首次完整活动介绍只负责讲清活动与价格，不能同轮发送 `payment_collection`；只有历史已经完成活动报价、客户后续出现报名或付款推进时，才进入收款阶段。`activity_intro_required` 不能靠删卡绕过，必须选择活动介绍等合法前序候选或拒发。
选择 `merge` 时，文本 order 必须以 `adjacent_merge_options` 中对应组合的 `message_editing_context` 为准；不要沿用单包内部可能重复的 order。

# Task
1. 理解事件触发的 SOP 阶段、最近聊天和候选包的阶段目的。
2. 输出 `send/merge/send_ai_touch/handoff_or_safety_notice/skip/defer/handoff_to_ai_reply` 之一；`first_add_flow` 的包 ID 必须来自 `candidate_sops.id`，`platform_actions` 只能决定平台 actions 是否发送。
3. 如果发送内容与当前对话的称呼、语气、消息数量或承接顺序明显不自然，才输出 `text_adjustments` 或 `message_operations` 调整 text；正常时输出空数组。

# Decision Policy
- 事实优先级必须是：最新聊天 > 当前事件事实 > 已实际发送的 SOP > 客户画像和较旧历史事件。低优先级信息不得覆盖高优先级事实。
- `platform_actions` 模式下，`current_platform_task.message_content` 是平台根据当前流程选出的本轮触达任务。除最新聊天或订单/支付等硬事实与它明确冲突外，必须优先分析它的阶段目的，不能因旧画像、历史累计或“客户已付”而整体忽略。
- `platform_actions` 模式不受 `first_add_flow` 的新客主线前置限制。只要 `current_platform_task.message_content` 有完整 text/image，且没有正在聊天、明确拒绝、已付禁收款、健康投诉等硬冲突，就应 `send`；不要因为缺门店定位、缺活动铺垫或缺候选 SOP 而拒发平台任务。
- `platform_actions` 模式如果输入已有可发送的 text/image 内容，不能改成 `send_ai_touch` 来替代平台任务；`send_ai_touch` 只用于 `first_add_flow` 固定候选都不合适但仍要轻触的场景。平台任务需要润色时，使用 `send + text_adjustments/message_operations`。
- 已支付预约金只禁止再次发送预约金卡或催付，不禁止催到店、姓名电话登记、活动服务说明及其他与已付状态一致的后续触达。平台内容属于后续到店推进时，应保留其目标并按上下文自然润色。
- 客户画像和旧事件不是当前对话事实，不能成为强制发送依据，也不能覆盖近期聊天中的城市、问题、顾虑、拒绝或已经完成的行动。
- 固定首次加微流程中，只有“最新真实客户消息还没被普通 AI/销售回复”会在代码层阻断。客户之前回复过、但最近是小贝/销售发完后客户沉默，属于主动触达场景，必须尽量发 SOP 或轻触，不得因为“客户曾回复过/之前追问过”而空拒。
- 但客户已经真实开口、提供信息或进入后续登记/门店/支付/到店承接后，不能再发送 `s10_new_customer_opening`。这类情况应把破冰阶段视为已由聊天时间线覆盖，再选择下一主线包或 `send_ai_touch`。
- 当 `conversation_activity.latest_customer_pending_ai_reply=true` 时，这是客户最新问题等待普通 AI/销售回答的硬边界，必须 `send_sop=false`；不能改选 `next_step` 绕过，也不能用润色把 SOP 当成答复。
- 小贝/销售刚刚回复完客户的几分钟保护窗口会在代码层阻断，防止 SOP 紧贴上一条回复刷屏。进入本节点通常表示已经过了最近活跃保护窗口，或不存在刚回复完的活跃聊天。
- 先做拒发审查，通过后才考虑“默认按 SOP 全流程发送”；不能用流程目标覆盖客户当前明确立场。
- 拒发审查按以下顺序：销冠正在连续承接且会被打断；客户当前立场与候选包的核心行动相反；候选包与当前真实诉求冲突；同阶段的目标、核心事实和行动已被完整覆盖；同包或同类已经完成。
- 判断重复时比较“阶段目标 + 核心事实 + 行动目标”，不要因为句子换了说法就当作没发过；但只是同一活动主题或只发过普通图片不等于完整覆盖。
- 话术像公告、通知或机器人，只是调整理由，不是拒发理由；也是旧口径里的“只是润色理由，不是拒发理由”。如果阶段和内容本身可以发，必须 `send_sop=true` 并通过 `text_adjustments/message_operations` 改成自然聊天；不能因为原文生硬就选择不发。
- 客户未回复、只有 staff 消息、前序 SOP 已正常发送、同一活动主题或仅发过普通图片，都不构成拒发。

- `first_add_flow` 按破冰/介绍 -> 需求与门店 -> 效果案例 -> 活动报价 -> 登记与预约金的阶段推进；`delay_minutes` 只表示这次可以检查到哪个候选范围，不等于必须发送该时间点最高阶段的包。
- `candidate_sops` 已经通过结构资格过滤；模型只能在这些到期候选中判断当前最自然的内容，不得自行选择未到时间或未满足前置的下一阶段。
- `stage_tag/customer_state` 是阶段前置语义，不只是描述文字。`payment_followup/deposit_push/quoted_no_deposit/deposit_unpaid_*` 这类后续包，必须由最近对话、completed_sop_pack_ids/categories 或客户状态证明活动报价/预约金已经真实触达；不能仅因它和报价包同一时刻到期就越过未完成的 `price_quote`。
- 如果 `price_quote` 仍未完成且近期只完成效果/门店铺垫，应优先选择活动报价包。活动报价真实完成后，预约金卡不再要求先有匹配订单；订单只用于后台关联，不是发送前置。
- 选择包时按“最近真实聊天状态 + 已触达步骤 + 未完成步骤 + 候选包阶段目标”判断。客户正在聊且最新客户消息等待普通 AI/销售回复时，不发 SOP；客户沉默时，优先推进下一个合理 SOP 价值点。
- 某个步骤已经被问过或轻触过一次，例如已经问过城市/区域、斑点情况、姓名电话或预约金，客户继续沉默时，不要无限重复追问同一个问题；应往后推进到下一个未完成且不会制造事实错误的 SOP 包，并用第一条 text 自然承接“这个信息后面您方便再补，我先给您看/说下一步”。
- 只有这个必要信息从未触达过，或当前候选只有该步骤，才继续轻触该问题；已经触达过但客户沉默，不得因为“任务未解决”而空拒。
- 当到期候选和最近聊天严重重合时，不要编造下一个尚未合格的阶段；可在允许边界内润色当前包，确实重复或冲突时再 `send_ai_touch/defer/skip`，并写清原因。
- 客户刚提出一个问题并不当然拒发。只有销售正在实时处理该问题，或本包会明显答非所问、硬打断时才拒发。
- 不把活动图、门店图、品牌图当成效果案例；不把“同一活动”误判为严重重合。
- 平台自动加好友开场不是有效客户咨询；没有后续客户消息时，仍按未回复的 SOP 跟进判断。
- 当 `conversation_activity.assistant_waiting_customer=true` 且 `latest_customer_pending_ai_reply=false`，并且已经过了最近活跃保护窗口：这是典型的沉默触达场景。你应优先 `send_sop=true`，目标是让客户再次开口或继续被 SOP 推进；不要因为“刚追问过、staff 已经回复过、客户没接话”而拒发。
- 若候选 SOP 与当前未完成问题不完全一致，但没有硬冲突：仍应选择最合适的下一步候选并用 `text_adjustments/message_operations` 做过渡。例如门店城市还没补齐但已经问过一次且客户沉默，可以先承接“门店后面您发城市/定位我再匹配”，再衔接效果图、活动报价或预约金价值。
- 候选包如果包含 `payment_collection`：
  - `payment_collection_gate.status=supported`：可按正常 SOP 判断发送。
  - `payment_collection_gate.status=paid_skip_card`：客户已付，不得再发预约金卡；只可保留/改写为已付后的姓名电话或到店安排轻触达。
  - `payment_collection_gate.status=activity_intro_required`：完整活动介绍/价格铺垫还没有真实完成证据，不得发送预约金卡。结构化完成和近期聊天语义完成都是真实证据；如果近期已经讲清活动价、10元预约金、抵扣和可退，不要重复活动包，应写 `stage_skip_evidence` 后评估预约金轻触/收款候选。若没有这些证据，应优先选择活动介绍、效果铺垫或其他非收款候选；如果候选里没有合适包，拒发并说明还需先补活动介绍。
  - 聊天轨使用完整 `s10_activity_intro`，沉默事件轨使用 `event_s10_price_quote_60min`；两者分别是各自轨道的活动与价格铺垫，真实发送任一包都可形成活动报价完成证据。如果近期真实聊天已经完整讲过活动价、10元预约金、到店抵扣、未做或不满意可退、到店时间可按客户方便安排，也可作为语义完成证据，但必须在 `stage_skip_evidence` 写清楚，不能重复发送活动报价。
- 当 `mainline_stage_status.activity_and_price.structural_completed=true`，客户未付且没有明确拒付、投诉、付款异常、健康风险或最新待回复问题时，如果候选里存在 `deposit_decision/payment_followup/deposit_push` 阶段包，应优先选择后续预约金/未付跟进包或 `send_ai_touch`，不要 `skip`。这类场景的目标是继续推动客户决策，而不是重复活动介绍，也不是空触达。
- `deposit_decision` 是一个包含多个顺序子步骤的阶段：首次预约金推动、发卡后1小时效果跟进、发卡后2小时操作视频、当天收单可以分别发送。该阶段已有任一包完成，不代表其他候选包重复；只根据候选包自身 ID、类目、历史内容和配置时间判断。
- 如果近期聊天已有权威结构事实证明某个前序候选的核心内容已经真实发过，例如真实案例图片已经发送，而下一个主线候选也已到期，应选择下一个候选，并在 `stage_skip_evidence` 写明证据；不得一边说“已经覆盖”一边仍发送相同候选。
- 对“近期真实案例图片已发送”的判断是强约束：若候选同时包含效果铺垫和后续活动报价，必须跳过效果铺垫、选择活动报价，并输出 `stage_skip_evidence`；只有历史里没有真实 image 消息、只有文字承诺“给您看案例”时，才可继续发送效果铺垫。
- `payment_collection_gate` 必须逐个候选包独立判断。一个后置收款包是 `activity_intro_required`，不代表同轮其他非收款候选也不可发；如果候选中存在 `not_required/supported` 的活动介绍或效果包，应选择合法的前序包，不能因为另一个候选被拦而整轮 `skip`。
- 只有在 `conversation_activity.latest_customer_pending_ai_reply=true`、客户明确拒绝当前核心行动、投诉/付款异常/身体不适、或候选包会明显造成事实错误时，才 `send_sop=false`。
- 客户明确表示“不交/不想付/先别发预约金/到店再付”等拒绝当前预约金动作时，整个收款阶段候选都与当前立场冲突，必须 `skip` 或 `defer`。不能通过删除 `payment_collection` 后继续发送“留名额、付完登记”等催款文本来绕过拒绝；文本润色也不能把拒付改写成可继续催付。

# Plan A Decision Contract
- `send`：发送一个未完成主线包；这是进入主动触达判断后的默认动作。
- `merge`：只用于夜间积压或节奏明显落后，且只可选择两个顺序相邻的未完成主线包。不能把三个以上包一次发出。
- `strategy=continue_mainline/recover_backlog` 表示本轮实际推进，只能搭配 `send/merge`；若因客户立场、当前诉求或事实风险拒发，使用 `strategy=conflict_guard` 搭配 `skip/defer`；只有频率限制才使用 `frequency_guard`。不要一边声称继续主线，一边输出 skip。
- 当固定 SOP 包都不适合，但当前仍应该主动触达客户开口时，使用 `send_ai_touch`，并在 `ai_touch_messages` 输出 1-2 条自然微信 text。它用于软拒绝、候选包重复、已问过同一问题但客户沉默、已付后的到店/登记提醒等场景；目标是重新开口、回到主线或推进下一步，不是复读整套 SOP。
- 当客户出现投诉、退款、付款异常、健康风险、严重不适或强人工诉求时，使用 `handoff_or_safety_notice`，并在 `ai_touch_messages` 输出安抚和承接 text。该分支禁止预约金压单、禁止发营销包、禁止承诺效果；只能降低风险、收集必要事实或引导人工处理。
- `send_ai_touch/handoff_or_safety_notice` 只能输出 text，不得输出 image、video、store_address、payment_collection、human_handoff_notice 等结构消息；不得编造门店、订单、付款、效果图、检测结论或已安排事实。
- `skip/defer` 只保留给真正不该触达的少数情况：客户最新问题正在等待普通 AI/销售回复、强烈明确拒绝继续沟通、频率软上限且无新进展、会话事实不可靠、或任何触达都会造成安全/投诉风险。不要因为固定 SOP 重复就直接 skip，先考虑 `send_ai_touch`。
- “最近忙、改天、过段时间、暂时没空、路远”属于行动阻力或软拒绝，不等于拒绝继续沟通，也不构成 `skip/defer`。活动报价已完成时，应使用 `send_ai_touch` 自然承接“到店时间后面按方便安排”，再用一个真实活动价值点推进保留名额；如果固定预约金候选与语气适配，也可以润色后发送。
- `candidate_sops` 已按主线先后顺序排列。除非第一个候选已由更高优先级事实证明完成、当前明确冲突或结构不合法，否则选择必须从第一个候选开始；仅仅“距离触发时间已久”或“节奏落后”不能跳过第一个候选。
- 如果你判断某个更早阶段已经被近期聊天语义覆盖，但 `completed_sop_pack_ids/categories` 没有记录，必须在 `stage_skip_evidence` 写清楚被跳过的 `stage_id`、`pack_id` 和具体证据摘要；否则结构校验会按未完成前序阶段处理。
- 近期聊天语义覆盖的判断要服务于“不重复、不越级”：
  - 已经问过客户城市/区域/定位且客户沉默，可视为 `location_capture` 已被轻触覆盖，本轮可继续需求案例；如果从未问过门店位置，也没有真实门店事实，先补门店轻触。
  - 近期已经发送真实案例图或已经清楚承接“斑点类型、效果参考、到店检测”，可视为 `need_and_case` 已覆盖，不要重复发需求案例包。
  - 近期已经完整讲过活动价、10元预约金、到店抵扣、未做或不满意可退、到店时间可按客户方便安排，可视为 `activity_and_price` 已覆盖，不要再发 `s10_activity_intro`；应进入预约金轻触、支付方式、转账/收款卡或已付后登记。
  - 近期已经发过收款卡或清楚催客户付好截图登记，普通情况下不要重复整套活动介绍；客户有新成交进展或要求付款时可继续预约金动作。
- 选择前必须做一次“重复阶段检查”：如果你在 `recent_conversation` 里能找到当前候选阶段已经被助手明确做过，就不要再选择同阶段候选；应写入 `stage_skip_evidence` 并选择后续未完成阶段。尤其禁止：
  - 刚问过城市/区域/定位后，再发送同一句问位置包。若候选包含 `event_s10_store_prompt_5min` 和 `s10_need_and_case`，近期已问过城市/区域/定位且客户沉默，应跳过 `event_s10_store_prompt_5min`，选择 `s10_need_and_case`。
  - 刚讲过 268、10元预约金、到店抵扣、可退等活动事实后，再发送 `s10_activity_intro`。
- 夜间积压两个以上阶段时，只能发送第一个候选，或合并第一个与第二个候选；不能单独选择第二个，也不能绕过前序阶段挑后面的包。`backlog_count>=3` 仍然最多只恢复前两个，剩余阶段留给以后触达。
- `skip`：当前频率过高、语义重复、客户立场硬冲突或没有合法候选时本次不发。
- `defer`：内容仍应发送，但当前时段或顺序不合适；必须说明建议窗口，不能把它当永久跳过。
- `handoff_to_ai_reply`：极少数异常分支。只有 `event_policy_evidence.ai_reply_policy.allowed=true` 才可选择；否则绝对禁止。

普通 AI 交接不是“表达更自然”的替代方案。它必须同时具备未处理的新客户消息和可执行的普通 AI 接管链路。客户沉默、最后一条是小贝/销售/SOP、只回复短确认、未付但没有新问题、或候选 SOP 能覆盖当前阶段时，都不得交普通 AI。

客户沉默时，平台事件本身就是“现在检查主动触达”的依据；没有客户新消息不等于不能发。只要近期不在连续聊天、没有明确拒绝/风险/重复、候选主线顺序正确，就应发送当前候选，不需要额外等待客户先表现出付款意愿或正向承接。

触达频率只作为模型判断证据：优先看当前客户是否有新进展，再看今天发送次数和最近发送时间，最后才看历史累计。不能仅因历史累计次数较多永久停止触达。夜间积压最多融合两个相邻主线包，且不得跨过未完成的活动价格铺垫直接发收款卡。
`touch_frequency.daily_soft_limit` 是平台可调整的当日软上限，不是代码硬禁令。输入会同时给出 `daily_soft_limit_reached`、`silent_soft_limit_reached` 和 `has_new_customer_progress_since_last_touch` 三个确定性比较事实。当两个 reached 都为 true、`has_new_customer_progress_since_last_touch=false`、`pending_backlog.has_pending=false` 时，本次必须 `skip` 或 `defer`，不要继续机械触达。只有存在新的客户进展、明确重发诉求或夜间 backlog 时才可说明例外理由后继续发送；历史累计次数本身仍不能永久阻止触达。
- `has_new_customer_progress_since_last_touch=true` 是代码根据触达后真实客户消息计算的确定性事实，不等于“仍有待回复消息”。即使销售随后已经回答，客户的新开口、认可或状态推进仍然打破了连续沉默；当前候选正好是下一阶段且无冲突时，应允许本轮继续推进，不能重新推断成“没有新进展”再用软上限跳过。

# Few-Shot Calibration
- 客户明确表示想到店再付、暂时不交预约金，候选包的核心行动是立即发收款卡：客户立场与核心行动相反，拒发，不通过润色继续推卡。
- 近聊已完整说明活动价、预约金、到店抵扣、尾款和保留名额，候选包又是同一活动介绍与同一行动：阶段语义已完整覆盖，拒发。
- 前序只发过破冰和门店铺垫，客户未回复，候选包用于发同类效果参考：属于正常下一阶段，发送。

- 刚破冰后还没有问过城市/区域，5分钟问地址包候选可用：发送问地址包，轻触客户补城市/区域。
- 已经问过城市/区域或定位，客户仍沉默，后续事件候选里有效果铺垫包：不要再次卡在门店步骤，也不要空拒；发送效果铺垫包，并可在第一条 text 前半句承接“门店后面您发城市/定位我再匹配”，再发效果参考。
- 已经发过效果铺垫，客户仍沉默，后续候选里有活动报价包：推进报价和活动价值，不要因为客户没有回复效果图而空拒。
- 已经发过效果铺垫、活动报价尚未发送，而同一批候选同时出现活动报价包和“未付款效果跟进”：选择活动报价包；首次活动报价包不能包含 `payment_collection`，应在图文后保留一句自然动作，引导客户确认人数、登记或继续咨询；不能先发“未付款跟进”。历史已经完成活动报价后，后续收款候选可以发送 `payment_collection`，不以匹配订单为前置。
- 已经报价，客户仍沉默，后续候选里有预约金价值或收款包：可推进预约金价值；如果客户明确拒付、已付、投诉/付款异常/身体不适，则不发该包。
- 候选同时有 `s10_activity_intro` 和收款包，且收款包显示 `activity_intro_required`：如果近期没有完整活动价格铺垫，发送 `s10_activity_intro`，不要合并收款包，也不要误判成“没有合规候选”；如果近期聊天已经完整讲过活动价、预约金、抵扣和可退，则跳过重复活动包，选择后续预约金/轻触达候选。
- `daily_soft_limit_reached=true` 且 `silent_soft_limit_reached=true`，同时没有夜间积压和触达后的客户新进展：本次必须跳过或延后，并在 `frequency_reason` 说明频率保护；不能因为还有未完成包就继续刷屏。

# Text Adjustment Policy
- 由你语义判断是否需要润色，不按关键词机械判断。
- 调整目的仅限于让既有 SOP 更像真人顺着当前聊天自然发出：可调整称呼、语气、连接句、表达顺序，以及 text 消息的拆合和数量。
- 这是企业微信一对一聊天，不是群发公告、短信通知或机构宣传稿。称呼可以用“您”或“亲”，也可以直接接上文；不要用“尊敬的客户/尊敬的顾客”这类式称呼。
- 如果原文像系统通知或公告，不能只换一两个词；要在不改事实和阶段目标的前提下，改成销售正在微信里接着聊的短句。避免“您好，温馨提醒”“请及时参与”“本机构现隆重开展”“诚邀您参与”等通知体。
- 润色只能承接输入中真实出现过的聊天、已发送步骤和事件事实。没有聊天或完成记录证明时，不得擅自写“前面和您说过”“刚才发您的”“还是那家”“您之前看过”“已经给您留了”等虚构历史；平台动作首次触达应直接自然表达当前内容。
- 聊天口吻应该是短、直接、有上下文：先顺着客户刚才的问题或前序阶段，再说本包要推进的内容。不要写“温馨提醒、及时参与、感谢您的关注”这类客服模板句。
- 最近一条真实客户消息包含明确顾虑、问题或不便，且最终决定发送时，最早一条可编辑 text 必须先用一句短话直接承接该内容，再衔接原话术包目标；原文已经自然承接时不必硬改。
- 原消息已经完整提出城市、区域、斑点情况、姓名电话或付款等行动时，不要再插入一句同义追问，也不要把同一行动换个说法重复两遍。润色的目标是衔接自然，不是增加消息数量；原文自然时保持空调整。
- `latest_messages` 为空且当前发送的是普通候选 SOP 包时，没有需要承接的客户原话；候选包本身可独立发送就必须保持 `text_adjustments=[]`、`message_operations=[]`。不得凭生命周期、阶段名或旧画像臆造“前面/刚才/那家”等上下文。
- 同一条原始 text 不要同时执行 `insert_text_before/after` 和 `replace_text`；不得插入与原 text 目标、事实和行动基本相同的铺垫句。需要改写时只 replace，需要补充真实上下文时才 insert，两者不要重复同一句意思。
- 主动触达的目标是让客户重新开口并继续主线，不要把本可直接介绍的内容改成“您要不要了解/想了解我再说”的被动征询；可直接自然说明当前内容，或只询问确实缺失的必要信息。
- 对 `sop_platform_task`，平台传入的 `actions` 就是本轮受信发送内容，`selected_pack_ids` 可以为空；润色后必须仍是一条信息完整、可以单独发送的微信消息。不要只写“我简单跟您说下/我给您介绍一下/我接着发您”却没有本轮实际内容，也不要擅自再选择不存在的 SOP 包。

平台公告润色正反例：
- 输入只有“尊敬的客户您好，温馨提醒您及时参与本次活动”，且近期聊天/完成记录为空。
- 正确：`亲，这次活动现在还可以参加，您有顾虑直接跟我说就行。`
- 错误：`前面和您说的活动...`，因为输入没有证明前面说过。
- 错误：`您是想了解活动对吧，我简单说下。`，因为它替客户假设意图，而且只预告、不提供本轮内容。

频率与立场对照例：
- 客户已明确“先别发预约金”，候选是收款包：必须 `skip/defer`，不能删掉卡片后发送剩余催付文本。
- `today_count=2` 且两个软上限都达到，但 `has_new_customer_progress_since_last_touch=true`，销售已承接客户新进展，候选是顺序正确的下一阶段：软上限不再代表连续沉默，应发送下一阶段；不要因为最新一条是销售回复就否认客户刚产生过的新进展。
- “共情”必须对应客户真实表达，不能机械添加“理解您、确实不容易”；客户只是普通询问时直接回答并衔接即可。
- 只有 `send_sop=true` 时才能输出 `text_adjustments/message_operations`；调整不能把拒发冲突改写成可发，润色不能把拒发冲突改写成可发。
- 可用 `message_operations`：
  - `insert_text_before/insert_text_after`：只插入不含新数字事实的 text，用于补一句承接或把通知体拆得更像聊天。
  - `remove_text`：只删除不含数字事实的多余 text，不能删除最后一条付款说明 text；如果原包在最后一张 image/video/结构卡之后有收尾动作 text，至少保留一条，不能让整包停在素材或卡片上。
  - `merge_text`：合并多条 text，必须保留这些 text 的全部数字事实。
  - `split_text`：拆分一条 text，拆分后必须保留原 text 的全部数字事实。
  - `replace_text`：等同 text_adjustments，改写同一 order 的 text。
  - `remove_message`：仅用于删除 `payment_collection_gate.status` 不支持发送的 `payment_collection`，必须同步调整 text，不能让客户以为同轮已经发了收款卡。
- 除 `remove_message` 删除不支持发送的 `payment_collection` 外，`message_operations` 只能操作 editable text；不能操作其他 `readonly_messages`，不能新增 image/video/payment_collection/store_address/human_handoff_notice，不能把 text 改成其他消息类型。
- 必须保留该文本的阶段目标、已有价格、金额、优惠、退款口径、门店、日期时间、支付方式及承诺边界。
- 所有数字及其出现次数必须与对应原文一致，不能为了口语化重复或省略金额。
- 不能编造新事实，不能把普通答疑改成另一阶段的强推销，不能新增催付、预约承诺、门店事实或效果承诺。
- `store_address`、`image`、`video`、`human_handoff_notice` 永远保持原样；若 text 与这些只读消息有关，润色不得改变其事实含义。`payment_collection` 只有在 gate 明确不支持时才可删除，不能改金额或复制生成。
- 非终态 SOP 必须保留原包的阶段出口。原包在最后一张图片、视频或结构卡后已有行动引导时，润色后仍须保留至少一条自然收尾 text；活动介绍不能只剩活动正文和海报，必须保留登记、人数确认或继续咨询中的一个动作。

# Text Style Calibration
- 原文：“尊敬的顾客您好，本机构现隆重开展淡斑活动，诚邀您参与。”客户刚说自己脸上有斑：改成类似“亲，您是想了解淡斑对吧，我简单跟您说下这次活动。”
- 原文：“您好，温馨提醒您及时参与本次活动。”前面已介绍过活动：改成类似“亲，前面和您说的活动还可以参加，有哪里不清楚您直接问我就行。”
- 上面只校准口吻和改写幅度，不是要求复读固定句子。根据输入上下文自然改写。

# Do Not
- 不输出普通 AI 的客户可见回复；只有合法 `handoff_to_ai_reply` 决策的兼容字段 `need_ai_reply` 才能为 true。
- 不补门店、价格、档期、案例、订单或客户事实。
- 不因为客户未回复、前序 SOP 已发或最近只有 staff 消息而拒发后续阶段。
- 不输出内部分析、markdown 或 schema 之外的字段。

# Output Schema
只输出 JSON：
{{
  "decision": "send | merge | send_ai_touch | handoff_or_safety_notice | skip | defer | handoff_to_ai_reply",
  "strategy": "continue_mainline | recover_backlog | soft_touch | safety_notice | conflict_guard | frequency_guard | realtime_handoff",
  "selected_pack_ids": ["first_add_flow 时来自 candidate_sops；send 只能1个，merge 必须是相邻2个"],
  "merge_pack_ids": [],
  "touch_goal": "resume_mainline | soften_objection | collect_info | payment_followup | visit_followup | safety_handoff | none",
  "ai_touch_messages": [{{"type": "text", "content": {{"text": "send_ai_touch/handoff_or_safety_notice 时才输出的客户可见短句"}}}}],
  "skip_reason": "skip/defer 时的内部原因",
  "frequency_reason": "基于发送频率证据的判断",
  "backlog_handling": "none | recover_one | merge_two",
  "suggested_next_window": "defer 时给出建议窗口，否则空字符串",
  "reason": "一句内部判断原因",
  "stage_skip_evidence": [{{"stage_id": "被近期聊天覆盖的前序阶段", "pack_id": "被跳过的候选包", "evidence": "近期聊天中覆盖该阶段的事实摘要"}}],
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
        event_model_total_timeout_seconds: float = 60.0,
        chat_gate_total_timeout_seconds: float = 15.0,
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
        self.event_model_total_timeout_seconds = max(1.0, float(event_model_total_timeout_seconds or 60.0))
        self.chat_gate_total_timeout_seconds = max(1.0, float(chat_gate_total_timeout_seconds or 15.0))
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
            non_text_reason = _chat_non_text_ai_route_reason(request, request_context)
            if non_text_reason:
                result.update({"mode": "skipped", "need_ai_reply": True, "reason": non_text_reason})
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
            completed_mainline_stages = _completed_mainline_stages(
                completed_ids,
                completed_categories,
            )
            recent_delivery_evidence = _recent_chat_sop_delivery_evidence(
                self.repository,
                identity,
            )
            unfinished = [
                pack
                for pack in enabled_packs
                if _string(pack.get("id")) not in completed_ids
                and _pack_category(pack) not in completed_categories
                and not (
                    mainline_stage_for_event_pack(pack) == "activity_and_price"
                    and "activity_and_price" in completed_mainline_stages
                )
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
            selector_input = _chat_selector_input(
                request,
                unfinished,
                sop_progress_evidence=result["sop_progress_evidence"],
                recent_delivery_evidence=recent_delivery_evidence,
                customer_memory=customer_memory,
                customer_context=order_gate.get("customer_context", {}),
            )
            result["selector_input"] = compact(selector_input, max_chars=6000)
            selector_output = await self._select_chat_sop(
                selector_input,
                deadline_monotonic=time.monotonic() + self.chat_gate_total_timeout_seconds,
            )
            result["selector_output"] = selector_output
            result["decision"] = _string(selector_output.get("decision"))
            result["selected_pack_ids"] = [
                _string(item) for item in selector_output.get("selected_pack_ids") or [] if _string(item)
            ]
            result["frequency_reason"] = _string(selector_output.get("frequency_reason"))
            result["backlog_handling"] = _string(selector_output.get("backlog_handling"))
            result["suggested_next_window"] = _string(selector_output.get("suggested_next_window"))
            result["model_usage"] = dict(self.model_client.last_usage or {})
            route = _chat_gate_route(selector_output)
            result["route"] = route
            result["coverage"] = _chat_gate_coverage(selector_output)
            result["priority_question_id"] = _string(selector_output.get("priority_question_id"))
            result["resume_stage"] = _string(selector_output.get("resume_stage"))
            result["text_adjustments"] = _text_adjustments(selector_output.get("text_adjustments"))
            result["message_operations"] = _message_operations(selector_output.get("message_operations"))
            selected = _selected_pack(selector_output, unfinished)
            if not selected:
                result.update(
                    {
                        "mode": route,
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
                        "mode": "skipped_deposit_paid",
                        "send_sop": False,
                        "need_ai_reply": True,
                        "reason": "payment_collection_blocked_by_paid_state",
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
                mark_sent=route != "ai_then_sop",
            )
            expected_task_status = "pending" if route == "ai_then_sop" else "sent"
            if task.get("status") != expected_task_status:
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
                    "mode": route,
                    "send_sop": True,
                    "sop_pack_id": str(selected.get("id") or ""),
                    "sop_pack_name": str(selected.get("name") or ""),
                    "need_ai_reply": route == "ai_then_sop",
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

    async def _select_chat_sop(
        self,
        selector_input: dict[str, Any],
        *,
        deadline_monotonic: float | None = None,
    ) -> dict[str, Any]:
        data = await self.model_client.chat_json(
            build_sop_chat_gate_messages(selector_input),
            tier="reply",
            temperature=0,
            deadline_monotonic=deadline_monotonic,
        )
        output = data if isinstance(data, dict) else {}
        violations = _chat_gate_output_violations(output, selector_input)
        if not violations:
            return output
        repaired = await self.model_client.chat_json(
            build_sop_chat_gate_repair_messages(selector_input, output, violations),
            tier="reply",
            temperature=0,
            deadline_monotonic=deadline_monotonic,
        )
        repaired_output = repaired if isinstance(repaired, dict) else {}
        repaired_violations = _chat_gate_output_violations(repaired_output, selector_input)
        if not repaired_violations:
            repaired_output["repair_applied"] = True
            repaired_output["initial_violations"] = violations
            return repaired_output
        return {
            "route": "ai_only",
            "coverage": "none",
            "priority_question_id": "",
            "sop_pack_id": "",
            "resume_stage": "",
            "reason": "chat_gate_invalid_after_repair_continue_ai",
            "text_adjustments": [],
            "message_operations": [],
            "initial_violations": violations,
            "repair_violations": repaired_violations,
        }

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
        event_policy_evidence: dict[str, Any] | None = None,
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
            event_policy = event_policy_evidence or {}
            if event_type in {"sop_friend_added_schedule_batch", "sop_friend_added_immediate"} and _event_suggestion_activity_block(
                conversation_activity or {},
                event_policy,
            ):
                result.update(
                    {
                        "mode": "event_rejected",
                        "send_sop": False,
                        "need_ai_reply": False,
                        "reason": "event_suggestion_active_chat_or_pending_customer_reply",
                    }
                )
                return _finish(result, started)
            completed_ids = self.repository.list_sent_sop_pack_ids_for_customer(
                customer_id=identity.get("customer_id", ""),
                external_userid=identity.get("external_userid", ""),
                corp_id=identity.get("corp_id", ""),
                wechat=identity.get("wechat", ""),
            )
            completed_categories = _sent_categories(self.repository, identity)
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
                event_policy_evidence=event_policy,
            )
            result["selector_input"] = compact(selector_input, max_chars=6000)
            selector_output, model_attempts, model_error = await self._judge_event_sop_with_retries(selector_input)
            result["model_attempts"] = model_attempts
            if model_error:
                fallback_output = _model_error_event_fallback(
                    selector_input,
                    event_type=event_type,
                    actions_reply_messages=actions_reply_messages,
                    model_error=model_error,
                )
                if fallback_output:
                    selector_output = fallback_output
                    result["selector_output"] = selector_output
                    result["model_usage"] = dict(self.model_client.last_usage or {})
                    result["text_adjustments"] = []
                    result["message_operations"] = []
                    result["error"] = model_error
                    decision_name = _string(selector_output.get("decision"))
                    if event_type in {"sop_friend_added_schedule_batch", "sop_friend_added_immediate"}:
                        selected = selected_candidate_packs(selector_output, candidate_packs)
                        send_sop = bool(selector_output.get("send_sop") and selected)
                        result.update(
                            {
                                "sop_pack_id": str(selected[0].get("id") or "") if selected else "",
                                "sop_pack_name": " + ".join(str(pack.get("name") or "") for pack in selected),
                                "send_sop": send_sop,
                                "mode": "event_selected" if send_sop else "event_rejected",
                                "need_ai_reply": False,
                                "reason": str(selector_output.get("reason") or "event_model_error_candidate_fallback"),
                            }
                        )
                        return _finish(result, started)
                    if event_type == "sop_platform_task":
                        messages, sanitize_summary = sanitize_sop_reply_messages(
                            actions_reply_messages,
                            conversation_messages=conversation_messages,
                        )
                        send_sop = bool(messages)
                        result.update(
                            {
                                "sop_pack_id": "platform_actions",
                                "sop_pack_name": "platform_actions",
                                "send_sop": send_sop,
                                "reply_messages": messages,
                                "message_sanitize": sanitize_summary,
                                "mode": "event_selected" if send_sop else "event_rejected",
                                "need_ai_reply": False,
                                "reason": str(selector_output.get("reason") or "event_model_error_platform_actions_fallback"),
                            }
                        )
                        return _finish(result, started)
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
            decision_name = _string(selector_output.get("decision"))

            if event_type in {"sop_friend_added_schedule_batch", "sop_friend_added_immediate"}:
                if decision_name in {"send_ai_touch", "handoff_or_safety_notice"}:
                    touch_messages = (
                        selector_output.get("ai_touch_messages")
                        if isinstance(selector_output.get("ai_touch_messages"), list)
                        else []
                    )
                    messages, sanitize_summary = sanitize_sop_reply_messages(
                        touch_messages,
                        conversation_messages=conversation_messages,
                    )
                    send_sop = bool(messages)
                    result.update(
                        {
                            "sop_pack_id": decision_name,
                            "sop_pack_name": decision_name,
                            "send_sop": send_sop,
                            "reply_messages": messages,
                            "message_sanitize": sanitize_summary,
                        }
                    )
                else:
                    selected = selected_candidate_packs(selector_output, candidate_packs)
                    send_sop = bool(selector_output.get("send_sop") and selected)
                    result.update(
                        {
                            "sop_pack_id": str(selected[0].get("id") or "") if selected else "",
                            "sop_pack_name": " + ".join(str(pack.get("name") or "") for pack in selected),
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
                    "mode": (
                        "event_selected"
                        if send_sop
                        else "event_deferred"
                        if decision_name == "defer"
                        else "event_handoff"
                        if decision_name == "handoff_to_ai_reply"
                        else "event_rejected"
                    ),
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
        overall_started = time.monotonic()
        overall_deadline = overall_started + self.event_model_total_timeout_seconds
        for attempt in range(1, self.event_model_retry_attempts + 1):
            remaining_before_ms = max(0, int((overall_deadline - time.monotonic()) * 1000))
            if remaining_before_ms <= 0:
                last_error = "TimeoutError: event model total deadline exhausted"
                break
            started = time.perf_counter()
            deadline = min(
                overall_deadline,
                time.monotonic() + self.event_model_attempt_timeout_seconds,
            )
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
                        "remaining_budget_ms_before": remaining_before_ms,
                        "remaining_budget_ms_after": max(0, int((overall_deadline - time.monotonic()) * 1000)),
                        "total_deadline_seconds": self.event_model_total_timeout_seconds,
                        "model_usage": compact(self.model_client.last_usage or {}, max_chars=1800),
                    }
                )
                if attempt < self.event_model_retry_attempts and self.event_model_retry_delay_seconds:
                    sleep_seconds = min(
                        self.event_model_retry_delay_seconds,
                        max(0.0, overall_deadline - time.monotonic()),
                    )
                    if sleep_seconds > 0:
                        await asyncio.sleep(sleep_seconds)
                continue
            attempts.append(
                {
                    "attempt": attempt,
                    "status": "succeeded",
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                    "remaining_budget_ms_before": remaining_before_ms,
                    "remaining_budget_ms_after": max(0, int((overall_deadline - time.monotonic()) * 1000)),
                    "total_deadline_seconds": self.event_model_total_timeout_seconds,
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
        normalized, violations = normalize_event_decision(data if isinstance(data, dict) else {}, selector_input)
        if not violations:
            return normalized
        repair_messages = [
            {"role": "system", "content": SOP_EVENT_SYSTEM_PROMPT},
            {
                "role": "system",
                "content": (
                    "# Repair Task\n"
                    "上一份 JSON 违反主动 SOP 决策结构合同。只修正枚举、候选包数量、候选顺序、相邻关系、"
                    "已完成包幂等、结构消息发送资格和交接资格；"
                    "若候选的 payment_collection_gate 是 paid_skip_card，"
                    "且该阶段仍应触达，保留候选包并用 remove_message 删除每一张受限收款卡，同时改写相关 text；"
                    "activity_intro_required 不能靠删卡绕过，必须选择合法前序候选或拒发。"
                    "如果 violations 包含 repeated_candidates_should_use_ai_touch，说明候选包已重复但没有客户立场、风险或频率硬阻断；"
                    "此时必须改成 decision=send_ai_touch，输出一条简短自然的 ai_touch_messages，引导客户继续开口或接上活动流程，"
                    "不要继续 skip/defer，也不要选择重复候选包。"
                    "如果 violations 包含 backlog_should_use_mainline_candidate，说明这是夜间或积压恢复，且仍有合法主线候选；"
                    "此时不能降级成 send_ai_touch，必须选择第一个合法候选，或 merge 相邻两个合法候选，最多两个。"
                    "不要改变输入事实，不要输出 schema 外字段。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "selector_input": selector_input,
                        "invalid_output": data if isinstance(data, dict) else {},
                        "violations": violations,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            },
        ]
        repaired = await self.model_client.chat_json(
            repair_messages,
            tier="reply",
            temperature=0,
            deadline_monotonic=deadline_monotonic,
        )
        repaired_output, repaired_violations = normalize_event_decision(
            repaired if isinstance(repaired, dict) else {},
            selector_input,
        )
        if not repaired_violations:
            repaired_output["repair_applied"] = True
            repaired_output["initial_violations"] = violations
            return repaired_output
        if "completed_activity_with_deposit_candidate_should_continue" in set(violations + repaired_violations):
            fallback = _completed_activity_deposit_fallback(
                selector_input,
                initial_violations=violations,
                repair_violations=repaired_violations,
            )
            if fallback:
                return fallback
        if "backlog_should_use_mainline_candidate" in set(violations + repaired_violations):
            fallback = _backlog_mainline_candidate_fallback(
                selector_input,
                initial_violations=violations,
                repair_violations=repaired_violations,
            )
            if fallback:
                return fallback
        if "repeated_candidates_should_use_ai_touch" in set(violations + repaired_violations):
            return _repeated_candidate_ai_touch_fallback(
                initial_violations=violations,
                repair_violations=repaired_violations,
            )
        return {
            "decision": "skip",
            "send_sop": False,
            "sop_pack_id": "",
            "selected_pack_ids": [],
            "merge_pack_ids": [],
            "need_ai_reply": False,
            "reason": "event_decision_invalid_after_repair",
            "error": "event_decision_invalid_after_repair:" + ",".join(repaired_violations),
            "text_adjustments": [],
            "message_operations": [],
            "initial_violations": violations,
            "repair_violations": repaired_violations,
        }

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
        mark_sent: bool = True,
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
            send_once_key=_send_once_key(
                identity,
                str(pack.get("send_once_group") or sop_pack_id),
            ),
        )
        if mark_sent and task.get("id") and task.get("status") == "pending":
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

    def confirm_chat_gate_task_sent(
        self,
        task: dict[str, Any],
        *,
        request_id: str,
        reply_messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        task_id = _string(task.get("id")) if isinstance(task, dict) else ""
        if not task_id or _string(task.get("status")) != "pending":
            return task
        created = bool(task.get("created"))
        updated = self.repository.update_sop_send_task(
            task_id,
            status="sent",
            send_payload={
                "mode": "sync_http_response",
                "request_id": request_id,
                "reply_messages": reply_messages,
            },
            send_response={"accepted": True, "mode": "sync_http_response"},
            sent_at=utc_now_iso(),
        )
        updated["created"] = created
        return updated

    def fail_chat_gate_task(self, task: dict[str, Any], *, error: str) -> dict[str, Any]:
        task_id = _string(task.get("id")) if isinstance(task, dict) else ""
        if not task_id or _string(task.get("status")) != "pending":
            return task
        created = bool(task.get("created"))
        updated = self.repository.update_sop_send_task(
            task_id,
            status="failed",
            send_response={"accepted": False, "error": str(error or "ai_reply_failed_before_sop_send")[:240]},
        )
        updated["created"] = created
        return updated


def _finish(result: dict[str, Any], started: float) -> dict[str, Any]:
    result["duration_ms"] = int((time.perf_counter() - started) * 1000)
    return result


def _repeated_candidate_ai_touch_fallback(
    *,
    initial_violations: list[str],
    repair_violations: list[str],
) -> dict[str, Any]:
    return {
        "decision": "send_ai_touch",
        "strategy": "soft_touch",
        "selected_pack_ids": [],
        "merge_pack_ids": [],
        "send_sop": False,
        "sop_pack_id": "",
        "need_ai_reply": False,
        "touch_goal": "resume_mainline",
        "ai_touch_messages": [
            {
                "type": "text",
                "content": {
                    "text": "亲，您这边如果还有顾虑可以直接跟我说，我继续帮您按活动流程接着安排。"
                },
            }
        ],
        "reason": "repeated_candidates_ai_touch_fallback",
        "error": "",
        "text_adjustments": [],
        "message_operations": [],
        "initial_violations": initial_violations,
        "repair_violations": repair_violations,
        "fallback_applied": True,
    }


def _completed_activity_deposit_fallback(
    selector_input: dict[str, Any],
    *,
    initial_violations: list[str],
    repair_violations: list[str],
) -> dict[str, Any]:
    candidates = selector_input.get("candidate_sops")
    if not isinstance(candidates, list):
        return {}
    for pack in sorted(
        [item for item in candidates if isinstance(item, dict)],
        key=mainline_pack_sort_key,
    ):
        if mainline_stage_for_event_pack(pack) != "deposit_decision":
            continue
        payment_gate = pack.get("payment_collection_gate") if isinstance(pack.get("payment_collection_gate"), dict) else {}
        gate_status = _string(payment_gate.get("status"))
        if gate_status in {"paid_skip_card", "activity_intro_required", "unsupported", "blocked"}:
            continue
        pack_id = _string(pack.get("id"))
        if not pack_id:
            continue
        return {
            "decision": "send",
            "strategy": "continue_mainline",
            "selected_pack_ids": [pack_id],
            "merge_pack_ids": [],
            "send_sop": True,
            "sop_pack_id": pack_id,
            "need_ai_reply": False,
            "touch_goal": "payment_followup",
            "ai_touch_messages": [],
            "reason": "completed_activity_deposit_candidate_fallback",
            "error": "",
            "text_adjustments": [],
            "message_operations": [],
            "initial_violations": initial_violations,
            "repair_violations": repair_violations,
            "fallback_applied": True,
        }
    return {}


def _backlog_mainline_candidate_fallback(
    selector_input: dict[str, Any],
    *,
    initial_violations: list[str],
    repair_violations: list[str],
) -> dict[str, Any]:
    candidates = selector_input.get("candidate_sops")
    if not isinstance(candidates, list):
        return {}
    completed_ids = {
        _string(item)
        for item in selector_input.get("completed_sop_pack_ids") or []
        if _string(item)
    }
    completed_categories = {
        _string(item)
        for item in selector_input.get("completed_sop_categories") or []
        if _string(item)
    }
    completed_stages = _completed_mainline_stage_ids(selector_input)
    eligible: list[dict[str, Any]] = []
    for pack in sorted(
        [item for item in candidates if isinstance(item, dict)],
        key=mainline_pack_sort_key,
    ):
        pack_id = _string(pack.get("id"))
        if not pack_id or pack_id in completed_ids:
            continue
        if _pack_category(pack) in completed_categories:
            continue
        if mainline_stage_for_event_pack(pack) in completed_stages:
            continue
        payment_gate = pack.get("payment_collection_gate") if isinstance(pack.get("payment_collection_gate"), dict) else {}
        if _string(payment_gate.get("status")) in {"paid_skip_card", "activity_intro_required", "unsupported", "blocked"}:
            continue
        eligible.append(pack)
    if not eligible:
        return {}
    selected = eligible[:2]
    selected_ids = [_string(pack.get("id")) for pack in selected]
    decision = "merge" if len(selected_ids) == 2 else "send"
    return {
        "decision": decision,
        "strategy": "recover_backlog",
        "selected_pack_ids": selected_ids,
        "merge_pack_ids": selected_ids if decision == "merge" else [],
        "send_sop": True,
        "sop_pack_id": selected_ids[0],
        "need_ai_reply": False,
        "touch_goal": "resume_mainline",
        "ai_touch_messages": [],
        "reason": "backlog_mainline_candidate_fallback",
        "error": "",
        "text_adjustments": [],
        "message_operations": [],
        "backlog_handling": "merge_two" if decision == "merge" else "recover_one",
        "initial_violations": initial_violations,
        "repair_violations": repair_violations,
        "fallback_applied": True,
    }


def _model_error_event_fallback(
    selector_input: dict[str, Any],
    *,
    event_type: str,
    actions_reply_messages: list[dict[str, Any]] | None,
    model_error: str,
) -> dict[str, Any]:
    """Non-business fallback for model outages: use already-built structural candidates."""

    if event_type == "sop_platform_task":
        if actions_reply_messages:
            return {
                "decision": "send",
                "strategy": "platform_actions_model_error_fallback",
                "selected_pack_ids": [],
                "merge_pack_ids": [],
                "send_sop": True,
                "sop_pack_id": "platform_actions",
                "need_ai_reply": False,
                "reason": "event_model_error_platform_actions_fallback",
                "error": model_error,
                "text_adjustments": [],
                "message_operations": [],
                "fallback_applied": True,
            }
        return {}

    event_policy = (
        selector_input.get("event_policy_evidence")
        if isinstance(selector_input.get("event_policy_evidence"), dict)
        else {}
    )
    if any(
        bool(event_policy.get(key))
        for key in (
            "customer_rejection",
            "active_chat_window",
            "pending_customer_reply",
            "customer_pending_ai_reply",
            "health_risk",
            "complaint_or_payment_risk",
        )
    ):
        return {}
    candidates = selector_input.get("candidate_sops")
    if not isinstance(candidates, list):
        return {}
    completed_ids = {
        _string(item)
        for item in selector_input.get("completed_sop_pack_ids") or []
        if _string(item)
    }
    completed_categories = {
        _string(item)
        for item in selector_input.get("completed_sop_categories") or []
        if _string(item)
    }
    completed_stages = _completed_mainline_stage_ids(selector_input)
    for pack in sorted(
        [item for item in candidates if isinstance(item, dict)],
        key=mainline_pack_sort_key,
    ):
        pack_id = _string(pack.get("id"))
        if not pack_id or pack_id in completed_ids:
            continue
        if _pack_category(pack) in completed_categories:
            continue
        if mainline_stage_for_event_pack(pack) in completed_stages:
            continue
        payment_gate = pack.get("payment_collection_gate") if isinstance(pack.get("payment_collection_gate"), dict) else {}
        if _string(payment_gate.get("status")) in {"paid_skip_card", "activity_intro_required", "unsupported", "blocked"}:
            continue
        return {
            "decision": "send",
            "strategy": "model_error_candidate_fallback",
            "selected_pack_ids": [pack_id],
            "merge_pack_ids": [],
            "send_sop": True,
            "sop_pack_id": pack_id,
            "need_ai_reply": False,
            "touch_goal": "resume_mainline",
            "ai_touch_messages": [],
            "reason": "event_model_error_candidate_fallback",
            "error": model_error,
            "text_adjustments": [],
            "message_operations": [],
            "fallback_applied": True,
        }
    return {}


def _completed_mainline_stage_ids(selector_input: dict[str, Any]) -> set[str]:
    raw = selector_input.get("mainline_stage_status")
    if isinstance(raw, dict):
        items = raw.items()
    elif isinstance(raw, list):
        items = (
            (_string(item.get("stage_id") or item.get("mainline_stage")), item)
            for item in raw
            if isinstance(item, dict)
        )
    else:
        return set()
    completed: set[str] = set()
    for stage_id, value in items:
        if not isinstance(value, dict):
            continue
        if bool(value.get("structural_completed")) or bool(value.get("semantic_completed")):
            completed.add(_string(stage_id))
    return {stage_id for stage_id in completed if stage_id}


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
    delivery_evidence: dict[str, Any] | None = None,
    payment_state: str = "",
) -> list[dict[str, Any]]:
    packs = config.get("packs") if isinstance(config.get("packs"), list) else []
    completed = set(completed_sop_pack_ids)
    completed_categories = set(completed_sop_categories or [])
    completed_mainline_stages = _completed_mainline_stages(completed, completed_categories)
    if "activity_and_price" in completed_mainline_stages:
        completed_categories.update({"price_quote", "s10_activity_intro"})
    normalized_delivery_evidence = dict(delivery_evidence or {})
    category_times = (
        dict(normalized_delivery_evidence.get("category_last_sent_at") or {})
        if isinstance(normalized_delivery_evidence.get("category_last_sent_at"), dict)
        else {}
    )
    if "price_quote" not in category_times and category_times.get("s10_activity_intro"):
        category_times["price_quote"] = category_times["s10_activity_intro"]
    if "s10_activity_intro" not in category_times and category_times.get("price_quote"):
        category_times["s10_activity_intro"] = category_times["price_quote"]
    normalized_delivery_evidence["category_last_sent_at"] = category_times
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
        if (
            mainline_stage_for_event_pack(pack) == "activity_and_price"
            and "activity_and_price" in completed_mainline_stages
        ):
            continue
        if not _event_pack_schedule_eligible(
            pack,
            delay_minutes=delay_minutes,
            match_context=match_context,
            delivery_evidence=normalized_delivery_evidence,
            payment_state=payment_state,
            completed_categories=completed_categories,
        ):
            continue
        if _is_final_close_pack(pack) and not _final_close_context_matches(pack, delay_minutes, match_context):
            continue
        candidates.append(
            _annotated_first_add_candidate(
                pack,
                group="due",
                reason_hint="currently_due_or_overdue",
                prerequisite_status=_event_pack_prerequisite_status(pack, completed_categories),
            )
        )
    candidates = _sort_first_add_candidates(candidates)

    if delay_minutes <= 0 or FIRST_ADD_NEXT_STEP_LOOKAHEAD_MINUTES <= 0:
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
        key=mainline_pack_sort_key,
    )[:FIRST_ADD_NEXT_STEP_MAX_CANDIDATES]
    if not candidates:
        return next_step_candidates
    if next_delay - delay_minutes <= FIRST_ADD_NEXT_STEP_LOOKAHEAD_MINUTES:
        return _sort_first_add_candidates(candidates + next_step_candidates)
    return candidates


def _event_pack_schedule_eligible(
    pack: dict[str, Any],
    *,
    delay_minutes: int,
    match_context: dict[str, Any] | None,
    delivery_evidence: dict[str, Any],
    payment_state: str,
    completed_categories: set[str],
) -> bool:
    basis = _string(pack.get("schedule_basis")) or "friend_added"
    pack_delay = _int(pack.get("delay_minutes"), 0)
    min_gap = _int(pack.get("min_gap_minutes"), 0)
    required = {
        _string(item)
        for item in [
            *(pack.get("requires_completed_categories") or []),
            *(pack.get("forbidden_before_categories") or []),
        ]
        if _string(item)
    }
    required_payment_state = _string(pack.get("requires_payment_state"))
    if required_payment_state and required_payment_state != payment_state:
        return False
    max_daily_sends = _int(pack.get("max_daily_sends"), 0)
    if max_daily_sends > 0 and _int(delivery_evidence.get("today_count"), 0) >= max_daily_sends:
        return False
    if basis == "local_clock":
        return _final_close_context_matches(pack, delay_minutes, match_context)
    if basis == "payment_card_sent":
        anchor = _parse_prompt_time(delivery_evidence.get("payment_card_last_sent_at"))
        return bool(anchor and _elapsed_minutes(anchor, delivery_evidence.get("event_at")) >= min_gap)
    if basis == "previous_stage_sent":
        category_times = (
            delivery_evidence.get("category_last_sent_at")
            if isinstance(delivery_evidence.get("category_last_sent_at"), dict)
            else {}
        )
        anchors = [
            _parse_prompt_time(category_times.get(category))
            for category in required
            if category in completed_categories
        ]
        anchors = [anchor for anchor in anchors if anchor]
        if anchors:
            return _elapsed_minutes(max(anchors), delivery_evidence.get("event_at")) >= min_gap
        # Keep the due candidate visible when the structured send record is
        # incomplete. The event model may only skip the prerequisite with
        # explicit stage_skip_evidence from the recent conversation.
        return pack_delay <= delay_minutes
    if delay_minutes <= 0:
        return pack_delay <= 0
    return pack_delay <= delay_minutes


def _parse_prompt_time(value: Any) -> datetime | None:
    text = _string(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _elapsed_minutes(anchor: datetime, event_at: Any) -> int:
    current = _parse_prompt_time(event_at) or datetime.now(timezone.utc)
    return max(0, int((current - anchor).total_seconds() // 60))


def _sort_first_add_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(candidates, key=mainline_pack_sort_key)


def _event_pack_prerequisite_status(
    pack: dict[str, Any],
    completed_categories: set[str],
) -> str:
    required = {
        _string(item)
        for item in [
            *(pack.get("requires_completed_categories") or []),
            *(pack.get("forbidden_before_categories") or []),
        ]
        if _string(item)
    }
    if not required:
        return "not_required"
    if required.issubset(completed_categories):
        return "structurally_completed"
    return "semantic_evidence_required"


def _annotated_first_add_candidate(
    pack: dict[str, Any],
    *,
    group: str,
    reason_hint: str,
    prerequisite_status: str = "",
) -> dict[str, Any]:
    candidate = dict(pack)
    candidate["_candidate_group"] = group
    candidate["_selection_reason_hint"] = reason_hint
    candidate["_prerequisite_status"] = prerequisite_status or "not_required"
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


def _chat_selector_input(
    request: ChatRequest,
    unfinished_packs: list[dict[str, Any]],
    *,
    sop_progress_evidence: dict[str, Any] | None = None,
    recent_delivery_evidence: list[dict[str, Any]] | None = None,
    customer_memory: dict[str, Any] | None = None,
    customer_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "current_message": str(request.content or "").strip(),
        "recent_conversation": _recent_history(request.conversation_history),
        "recent_sop_delivery_evidence": recent_delivery_evidence or [],
        "mainline": sales_mainline_for_model(),
        "mainline_progress": sop_progress_evidence or {},
        "precision_qa_index": precision_qa_index_for_gate(),
        "unfinished_sops": [
            _sop_summary(
                pack,
                customer_memory=customer_memory or {},
                customer_context=customer_context or {},
            )
            for pack in unfinished_packs
        ],
    }


def _recent_chat_sop_delivery_evidence(
    repository: Any,
    identity: dict[str, str],
) -> list[dict[str, Any]]:
    """Expose recent structured SOP deliveries without inferring customer intent."""
    list_tasks = getattr(repository, "list_recent_sop_send_tasks_for_customer", None)
    if not callable(list_tasks):
        return []
    try:
        tasks = list_tasks(
            customer_id=identity.get("customer_id", ""),
            external_userid=identity.get("external_userid", ""),
            corp_id=identity.get("corp_id", ""),
            wechat=identity.get("wechat", ""),
            limit=8,
        )
    except Exception:
        return []
    output: list[dict[str, Any]] = []
    for task in tasks:
        if not isinstance(task, dict) or _string(task.get("status")).lower() != "sent":
            continue
        messages = task.get("reply_messages") if isinstance(task.get("reply_messages"), list) else []
        message_types = [
            _string(item.get("type")).lower()
            for item in messages
            if isinstance(item, dict) and _string(item.get("type"))
        ]
        output.append(
            _drop_empty(
                {
                    "sop_pack_id": _string(task.get("sop_pack_id")),
                    "sop_category": _string(task.get("sop_category")),
                    "sent_at": _string(task.get("sent_at") or task.get("updated_at")),
                    "message_types": message_types,
                    "image_count": sum(1 for item in message_types if item == "image"),
                    "video_count": sum(1 for item in message_types if item == "video"),
                }
            )
        )
    return output[:8]


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
        "event_time": _event_time_summary(payload),
    }


def _event_time_summary(payload: dict[str, Any]) -> dict[str, Any]:
    raw = _string(payload.get("created_at") or payload.get("upstream_created_at"))
    if not raw:
        return {"source": "missing", "timezone": "Asia/Shanghai"}
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        utc_value = parsed.astimezone(timezone.utc)
        local_value = parsed.astimezone(ZoneInfo("Asia/Shanghai"))
    except (TypeError, ValueError):
        return {"source": "payload", "raw": raw, "timezone": "Asia/Shanghai", "parse_status": "failed"}
    return {
        "source": "payload",
        "utc": utc_value.isoformat(),
        "local": local_value.isoformat(),
        "timezone": "Asia/Shanghai",
        "local_date": local_value.date().isoformat(),
        "local_hour": local_value.hour,
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
    event_scope: bool = False,
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
        "mainline_stage": mainline_stage_for_event_pack(pack)
        if event_scope
        else str(pack.get("mainline_stage") or mainline_stage_for_pack(str(pack.get("id") or ""))),
        "direct_answer_capabilities": [
            str(item)
            for item in pack.get("direct_answer_capabilities") or []
            if str(item or "").strip()
        ],
        "candidate_group": _string(pack.get("_candidate_group")) or "due",
        "selection_reason_hint": _string(pack.get("_selection_reason_hint")),
        "prerequisite_status": _string(pack.get("_prerequisite_status")) or "not_required",
        "tags": [str(item) for item in pack.get("triggers") or [] if str(item or "").strip()],
        "event_type": str(pack.get("event_type") or ""),
        "delay_minutes": int(pack.get("delay_minutes") or 0),
        "schedule_basis": _string(pack.get("schedule_basis")) or "friend_added",
        "min_gap_minutes": _int(pack.get("min_gap_minutes"), 0),
        "requires_completed_categories": [
            _string(item) for item in pack.get("requires_completed_categories") or [] if _string(item)
        ],
        "forbidden_before_categories": [
            _string(item) for item in pack.get("forbidden_before_categories") or [] if _string(item)
        ],
        "requires_payment_state": _string(pack.get("requires_payment_state")),
        "max_daily_sends": _int(pack.get("max_daily_sends"), 0),
        "silence_only": bool(pack.get("silence_only")),
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
        "mainline_stage": str(pack.get("mainline_stage") or mainline_stage_for_pack(str(pack.get("id") or ""))),
        "direct_answer_capabilities": [
            str(item)
            for item in pack.get("direct_answer_capabilities") or []
            if str(item or "").strip()
        ],
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
    event_policy_evidence: dict[str, Any],
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
                "candidate_sops 已按销售主线阶段排序，而不是按配置 raw order 排序。优先评估最早未完成主线阶段；"
                "如果前序阶段与最近聊天重复、冲突或已被覆盖，必须在 stage_skip_evidence 写明证据后再评估后续候选。"
                "next_step 只用于继续同一新客 SOP 节奏，不能编造事实或绕过风险边界。"
            ),
        },
        "mainline": sales_mainline_for_model(),
        "mainline_stage_status": _mainline_stage_status(
            candidate_packs=candidate_packs,
            completed_sop_pack_ids=completed_sop_pack_ids,
            completed_sop_categories=completed_sop_categories,
            conversation_messages=conversation_messages,
            conversation_activity=conversation_activity,
            customer_memory=customer_memory,
            customer_context=customer_context,
        ),
        "event_policy_evidence": event_policy_evidence,
        **memory_context,
        "candidate_sops": [
            _sop_summary(pack, customer_memory=customer_memory, customer_context=customer_context, event_scope=True)
            for pack in candidate_packs
        ],
        "adjacent_merge_options": _adjacent_merge_options(candidate_packs),
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


def _completed_mainline_stages(
    completed_pack_ids: set[str],
    completed_categories: set[str],
) -> set[str]:
    stages = {
        mainline_stage_for_event_values(pack_id=_string(pack_id))
        for pack_id in completed_pack_ids
        if _string(pack_id)
    }
    stages.update(
        mainline_stage_for_event_values(category=_string(category))
        for category in completed_categories
        if _string(category)
    )
    return {stage for stage in stages if stage}


def _mainline_stage_status(
    *,
    candidate_packs: list[dict[str, Any]],
    completed_sop_pack_ids: list[str],
    completed_sop_categories: list[str],
    conversation_messages: list[dict[str, Any]] | None = None,
    conversation_activity: dict[str, Any] | None = None,
    customer_memory: dict[str, Any] | None = None,
    customer_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    completed_ids = {_string(item) for item in completed_sop_pack_ids if _string(item)}
    completed_categories = {_string(item) for item in completed_sop_categories if _string(item)}
    candidate_by_stage: dict[str, list[str]] = {}
    for pack in candidate_packs:
        if not isinstance(pack, dict):
            continue
        stage_id = mainline_stage_for_event_pack(pack)
        if not stage_id:
            continue
        candidate_by_stage.setdefault(stage_id, []).append(_string(pack.get("id")))

    completed_by_stage: dict[str, dict[str, list[str]]] = {}
    for pack_id in completed_ids:
        stage_id = mainline_stage_for_event_values(pack_id=pack_id)
        if stage_id:
            completed_by_stage.setdefault(stage_id, {"pack_ids": [], "categories": []})["pack_ids"].append(pack_id)
    for category in completed_categories:
        stage_id = mainline_stage_for_event_values(category=category)
        if stage_id:
            completed_by_stage.setdefault(stage_id, {"pack_ids": [], "categories": []})["categories"].append(category)

    payment_progress = _mainline_payment_progress_evidence(customer_memory or {}, customer_context or {})
    for stage_id, evidence in payment_progress.items():
        if evidence:
            completed_by_stage.setdefault(stage_id, {"pack_ids": [], "categories": []})

    stage_order_by_id = {
        _string(stage.get("id")): _int(stage.get("order"), 9999)
        for stage in (sales_mainline_for_model().get("stages") or [])
        if isinstance(stage, dict) and _string(stage.get("id"))
    }
    completed_stage_order = max(
        (
            stage_order_by_id.get(stage_id, 0)
            for stage_id, completed in completed_by_stage.items()
            if completed.get("pack_ids") or completed.get("categories") or payment_progress.get(stage_id)
        ),
        default=0,
    )
    timeline_evidence = _mainline_timeline_evidence(
        conversation_messages or [],
        conversation_activity or {},
    )
    output: list[dict[str, Any]] = []
    for stage in (sales_mainline_for_model().get("stages") or []):
        if not isinstance(stage, dict):
            continue
        stage_id = _string(stage.get("id"))
        if not stage_id:
            continue
        completed = completed_by_stage.get(stage_id, {"pack_ids": [], "categories": []})
        candidate_ids = [item for item in candidate_by_stage.get(stage_id, []) if item]
        timeline_completed = bool(timeline_evidence.get(stage_id))
        payment_completed = bool(payment_progress.get(stage_id))
        progression_completed = bool(completed_stage_order and _int(stage.get("order"), 9999) < completed_stage_order)
        structural_completed = bool(
            completed.get("pack_ids")
            or completed.get("categories")
            or timeline_completed
            or payment_completed
            or progression_completed
        )
        output.append(
            _drop_empty(
                {
                    "stage_id": stage_id,
                    "order": stage.get("order"),
                    "goal": _string(stage.get("goal"))[:180],
                    "candidate_pack_ids": candidate_ids,
                    "structural_completed": structural_completed,
                    "timeline_completed": timeline_completed,
                    "timeline_evidence": timeline_evidence.get(stage_id),
                    "payment_completed": payment_completed,
                    "payment_evidence": payment_progress.get(stage_id),
                    "progression_completed": progression_completed,
                    "progression_evidence": _drop_empty(
                        {
                            "source": "later_structural_stage",
                            "completed_stage_order": completed_stage_order,
                        }
                    )
                    if progression_completed
                    else {},
                    "completed_pack_ids": sorted(set(completed.get("pack_ids") or [])),
                    "completed_categories": sorted(set(completed.get("categories") or [])),
                    "model_semantic_review_required": bool(candidate_ids and not structural_completed),
                }
            )
        )
    return output


def _mainline_timeline_evidence(
    conversation_messages: list[dict[str, Any]],
    conversation_activity: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Expose time-ordered conversation facts without deciding normal sales semantics."""

    output: dict[str, dict[str, Any]] = {}
    location_evidence = _recent_location_capture_evidence(conversation_messages)
    if location_evidence:
        output["location_capture"] = location_evidence
    activity_evidence = _recent_activity_price_evidence(conversation_messages)
    if activity_evidence:
        output["activity_and_price"] = activity_evidence

    real_customer_count = _int(conversation_activity.get("real_customer_message_count"), 0)
    customer_replied = bool(conversation_activity.get("customer_replied"))
    if real_customer_count <= 0 and not customer_replied:
        return output
    customer_samples: list[str] = []
    for item in conversation_messages[-30:]:
        if not isinstance(item, dict):
            continue
        direction = _string(item.get("direction") or item.get("role") or item.get("sender_type") or item.get("from")).lower()
        if direction != "customer":
            continue
        text = _message_text(item.get("content"))
        if text:
            customer_samples.append(text[:80])
        if len(customer_samples) >= 3:
            break
    evidence = {
        "source": "recent_conversation_timeline",
        "reason": "customer_has_real_messages_after_first_add",
        "real_customer_message_count": real_customer_count,
        "latest_customer_message_at": _string(conversation_activity.get("latest_customer_message_at")),
        "latest_assistant_message_at": _string(conversation_activity.get("latest_assistant_message_at")),
        "last_message_direction": _string(conversation_activity.get("last_message_direction")),
        "sample_customer_messages": customer_samples,
    }
    output["opening_and_positioning"] = _drop_empty(evidence)
    return output


def _recent_location_capture_evidence(conversation_messages: list[dict[str, Any]]) -> dict[str, Any]:
    assistant_texts: list[str] = []
    for item in conversation_messages[-12:]:
        if not isinstance(item, dict):
            continue
        direction = _string(item.get("direction") or item.get("role") or item.get("sender_type") or item.get("from")).lower()
        if direction not in {"assistant", "staff", "ai", "agent"}:
            continue
        text = _message_text(item.get("content"))
        if text:
            assistant_texts.append(text)
    if not assistant_texts:
        return {}
    combined = "\n".join(assistant_texts)[-900:]
    compact = combined.replace(" ", "")
    asked_scope = any(term in compact for term in ("城市", "哪个区", "区域", "定位", "门店位置", "附近门店"))
    asked_customer = any(term in compact for term in ("您在", "您是", "发我", "给我", "方便发", "在哪"))
    if not (asked_scope and asked_customer):
        return {}
    return _drop_empty(
        {
            "source": "recent_assistant_location_capture_prompt",
            "reason": "recent_chat_already_asked_customer_for_city_district_or_location",
            "sample_assistant_text": combined[-240:],
        }
    )


def _recent_activity_price_evidence(conversation_messages: list[dict[str, Any]]) -> dict[str, Any]:
    assistant_texts: list[str] = []
    for item in conversation_messages[-30:]:
        if not isinstance(item, dict):
            continue
        direction = _string(item.get("direction") or item.get("role") or item.get("sender_type") or item.get("from")).lower()
        if direction not in {"assistant", "staff", "ai", "agent"}:
            continue
        text = _message_text(item.get("content"))
        if text:
            assistant_texts.append(text)
    if not assistant_texts:
        return {}
    combined = "\n".join(assistant_texts)[-1800:]
    compact = combined.replace(" ", "")
    facts = {
        "activity_price_268": "268" in compact,
        "deposit_10": "10" in compact and any(term in compact for term in ("预约金", "定金", "报名", "收款卡", "小程序")),
        "deduct_at_store": "抵扣" in compact,
        "refund_or_satisfaction": any(term in compact for term in ("可退", "退", "不满意")),
    }
    if not all(facts.values()):
        return {}
    return _drop_empty(
        {
            "source": "recent_assistant_activity_price_facts",
            "reason": "recent_chat_contains_complete_activity_price_and_deposit_facts",
            "facts": facts,
            "sample_assistant_text": combined[-360:],
        }
    )


def _mainline_payment_progress_evidence(
    customer_memory: dict[str, Any],
    customer_context: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    payment = _payment_state_summary(customer_memory, customer_context)
    deposit_state = _string(payment.get("deposit_state"))
    if not is_paid_deposit_state(deposit_state):
        return {}
    evidence = _drop_empty(
        {
            "source": "current_payment_state",
            "reason": "deposit_paid_enters_post_paid_registration",
            "deposit_state": deposit_state,
            "payment_source": _string(payment.get("source")),
        }
    )
    return {
        "opening_and_positioning": evidence,
        "location_capture": evidence,
        "need_and_case": evidence,
        "activity_and_price": evidence,
        "deposit_decision": evidence,
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
    stored_deposit_state = _string(basic.get("deposit_state"))
    payment = resolved_payment_fact(
        orders=customer_context.get("orders") if isinstance(customer_context, dict) else [],
        existing_state=stored_deposit_state,
        existing_source=_string(basic.get("deposit_source")),
        existing_fact=basic.get("deposit_fact"),
    )
    order_gate = customer_context.get("_sop_order_gate") if isinstance(customer_context.get("_sop_order_gate"), dict) else {}
    fallback_paid_state = stored_deposit_state if is_paid_deposit_state(stored_deposit_state) else ""
    return {
        "deposit_state": _string(payment.get("deposit_state"))
        or _string(order_gate.get("deposit_state"))
        or fallback_paid_state
        or "unknown",
        "source": _string(payment.get("source"))
        or _string(order_gate.get("source"))
        or (_string(basic.get("deposit_source")) if fallback_paid_state else "")
        or "unknown",
        "order_id": _string(payment.get("order_id")) or _string(order_gate.get("order_id")),
        "store_id": _string(payment.get("store_id")) or _string(order_gate.get("store_id")),
        "prepay_required": payment.get("prepay_required", order_gate.get("prepay_required")),
        "prepay_paid": payment.get("prepay_paid", order_gate.get("prepay_paid")),
    }


def _adjacent_merge_options(candidate_packs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        (pack for pack in candidate_packs if isinstance(pack, dict)),
        key=mainline_pack_sort_key,
    )
    output: list[dict[str, Any]] = []
    for index in range(max(0, len(ordered) - 1)):
        pair = ordered[index : index + 2]
        combined: list[dict[str, Any]] = []
        order = 1
        for pack in pair:
            for message in sorted(_pack_messages(pack), key=lambda item: int(item.get("order") or 0)):
                item = dict(message)
                item["order"] = order
                combined.append(item)
                order += 1
        output.append(
            {
                "pack_ids": [_string(pack.get("id")) for pack in pair],
                "message_editing_context": _message_editing_context(combined),
            }
        )
    return output


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
    amounts = [_payment_message_amount(card) for card in cards]
    return {
        "has_payment_collection": True,
        "status": "supported",
        "amounts": amounts,
        "reason": "activity_intro_completed_or_not_required",
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
    if _chat_gate_route(selector_output) not in {"sop_only", "ai_then_sop"}:
        return {}
    selected_id = str(selector_output.get("sop_pack_id") or "").strip()
    if not selected_id:
        return {}
    for pack in packs:
        if str(pack.get("id") or "") == selected_id:
            return pack
    return {}


def _chat_gate_route(selector_output: dict[str, Any]) -> str:
    route = _string(selector_output.get("route"))
    if route in {"sop_only", "ai_only", "ai_then_sop"}:
        return route
    if bool(selector_output.get("send_sop")):
        return "ai_then_sop" if bool(selector_output.get("need_ai_reply")) else "sop_only"
    return "ai_only"


def _chat_gate_coverage(selector_output: dict[str, Any]) -> str:
    coverage = _string(selector_output.get("coverage"))
    if coverage in {"exact", "partial", "none"}:
        return coverage
    route = _chat_gate_route(selector_output)
    return "exact" if route == "sop_only" else ("partial" if route == "ai_then_sop" else "none")


def _chat_gate_output_violations(
    selector_output: dict[str, Any],
    selector_input: dict[str, Any],
) -> list[str]:
    route = _chat_gate_route(selector_output)
    coverage = _chat_gate_coverage(selector_output)
    pack_id = _string(selector_output.get("sop_pack_id"))
    resume_stage = _string(selector_output.get("resume_stage"))
    priority_question_id = _string(selector_output.get("priority_question_id"))
    packs = {
        _string(item.get("id")): item
        for item in selector_input.get("unfinished_sops") or []
        if isinstance(item, dict) and _string(item.get("id"))
    }
    question_ids = {
        _string(item.get("id"))
        for item in selector_input.get("precision_qa_index") or []
        if isinstance(item, dict) and _string(item.get("id"))
    }
    violations: list[str] = []
    expected_coverage = {
        "sop_only": "exact",
        "ai_then_sop": "partial",
        "ai_only": "none",
    }[route]
    if coverage != expected_coverage:
        violations.append(f"route_coverage_mismatch:{route}:{coverage}")
    if priority_question_id and priority_question_id not in question_ids:
        violations.append("unknown_priority_question_id")
    if route in {"sop_only", "ai_then_sop"}:
        if not pack_id or pack_id not in packs:
            violations.append("selected_pack_missing_or_not_unfinished")
        elif resume_stage != _string(packs[pack_id].get("mainline_stage")):
            violations.append("resume_stage_must_match_selected_pack")
    else:
        if pack_id:
            violations.append("ai_only_must_not_select_pack")
        if resume_stage and resume_stage not in {
            _string(item.get("id"))
            for item in (selector_input.get("mainline") or {}).get("stages") or []
            if isinstance(item, dict)
        }:
            violations.append("unknown_resume_stage")
    return violations


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


def _event_suggestion_activity_block(conversation_activity: dict[str, Any], event_policy: dict[str, Any]) -> bool:
    if any(
        bool(conversation_activity.get(key))
        for key in (
            "latest_customer_pending_ai_reply",
            "recent_active_chat",
            "active_chat_window",
            "uncertain_customer_timing",
        )
    ):
        return True
    return bool(event_policy.get("active_chat_window"))


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
    """Allow chat-gate payment cards once activity quote has been paved; paid state is blocked earlier."""
    payment_message = next(
        (item for item in messages if isinstance(item, dict) and _string(item.get("type")) == "payment_collection"),
        None,
    )
    if not payment_message:
        return True
    basic = customer_memory.get("basic_info") if isinstance(customer_memory.get("basic_info"), dict) else {}
    payment_fact = resolved_payment_fact(
        orders=customer_context.get("orders") if isinstance(customer_context, dict) else [],
        existing_state=_string(basic.get("deposit_state")),
        existing_source=_string(basic.get("deposit_source")),
        existing_fact=basic.get("deposit_fact"),
    )
    return not (
        is_paid_deposit_state(payment_fact.get("deposit_state"))
        or is_paid_deposit_state(basic.get("deposit_state"))
    )


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


def _chat_non_text_ai_route_reason(request: ChatRequest, request_context: dict[str, Any]) -> str:
    msgtype = _string(request_context.get("msgtype")).lower()
    if request.file_image:
        return "non_text_message_to_ai_tools:image"
    if msgtype and msgtype != "text":
        return f"non_text_message_to_ai_tools:{msgtype}"
    return ""


def _event_created_at(payload: dict[str, Any]) -> str:
    value = payload.get("created_at") or payload.get("upstream_created_at")
    return _string(value)


def _string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _drop_empty(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, "", [], {})}


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
