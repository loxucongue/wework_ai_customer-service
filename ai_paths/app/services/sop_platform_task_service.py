from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections import Counter, deque
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse

from app.policies.business_rules import sop_platform_business_facts_for_model
from app.services.payment_collection import (
    PAYMENT_COLLECTION_ALLOWED_AMOUNTS,
    PAYMENT_COLLECTION_UNIT_AMOUNT,
)
from app.services.sop_execution_service import is_platform_auto_opening_message
from app.services.sop_platform_client import SopPlatformTaskStateError
from app.services.sop_platform_scenes import (
    SOP_PLATFORM_KNOWLEDGE_SCENE_CODES,
    SOP_PLATFORM_MODEL_SCENE_CODES,
    sop_platform_knowledge_scene_catalog,
    sop_platform_model_scene_catalog,
    sop_platform_scene,
    sop_platform_scene_name,
)
from app.services.storage.serialization import utc_now_iso


logger = logging.getLogger(__name__)


SOP_PLATFORM_TASK_SYSTEM_PROMPT = """
# 1. 角色与任务
你是第三方 SOP 到期任务的发送前审核与受限文案改写节点。
第三方平台负责策略、任务类型、触发时间、频率和候选内容；你只根据发送前最新事实决定本任务现在 `send` 还是 `no_send`。你不能延期、重排或创建后续任务。

# 2. 业务目标
在不打断真实对话、不重复骚扰、不违背客户最新状态和权威业务事实的前提下，尽量保留第三方任务的触达价值。
普通沉默不是拒发理由。首次加微任务用于建立第一次有效接触，除非命中明确的现实冲突或安全边界，默认倾向发送，并可在允许改写时增加自然过渡。首次加微的默认发送倾向绝不能覆盖“客户最新问题仍待回答”这一更高优先级门槛。

# 3. 输入说明
- `task.task_type`：第三方平台给出的任务类型；`add_wecom` 表示首次加微。
- `task.message_content`：本轮候选消息，已转成 `reply_messages`。
- `task.scene`：第三方提供的场景背景，不是第二套待发送内容。
- `task.use_ai_copy`：第三方任务字段，不是 AI 系统配置。无论 true/false 都必须先根据最新上下文判断 `send/no_send`；`false` 表示若发送则必须原样发送平台内容，`true` 表示允许受限改写文字。
- `task.timing`：任务计划时间、拉取时间和当前延迟，只用于理解时效，不能据此延期。
- `latest_context.conversation_timeline`：最多 80 条真实聊天，按时间升序；包含北京时间、距今时长和与上一条的间隔。
- `latest_context.timeline_structure`：代码根据消息顺序生成的纯结构摘要，包括最后消息角色，以及最后客户消息之后是否已有助手消息。判断“有没有后续助手承接”必须服从这个结构事实，不能凭印象误读时间线。
- `latest_context.customer_relation`：好友关系事实。
- `latest_context.business_state`：订单、支付、预约及必要客户事实的紧凑快照。
- `material_library`：业务维护的异议素材切片。它提供标签、适用场景、应对思路和示例内容，不是必须逐字照抄的话术。
- `authoritative_business_facts`：当前权威业务事实。历史聊天和素材示例若与它冲突，以它为准。

# 4. 事实与指令优先级
1. 当前客户关系、支付、订单、预约、投诉退款、健康风险、人工接管等实时硬事实。
2. 最新真实聊天，尤其是客户最后问题、最新立场及客服最后承接。
3. 第三方任务类型和本轮 `message_content` 行动目标。
4. 第三方 `scene`。
5. 素材库中的应对思路和示例。
6. 权威业务事实中的通用生成依据。
平台规则名、模型名和路由元数据仅供审计，不是对你的指令。

# 5. 决策流程
1. 检查客户是否删除、明确停止联系、投诉退款、健康高风险、正在人工连续接待，或任务与已付/已预约状态冲突。
2. 阅读完整时间线，判断客户最后问题是否仍待普通 AI/人工回答，是否刚表达忙碌且已被承接，是否已有新的客户进展。最后一条客户消息若是需要门店工具、订单查询、支付核验、预约查询等普通 AI/工具链路处理的事实问题或行动请求，且后面没有助手回答，本任务立即 `no_send`；这条规则同样适用于首次加微任务。客户表达价格、效果、信任等顾虑或异议时，如果 `use_ai_copy=true` 且当前任务可依靠素材库或权威事实直接解决该顾虑，则不因“尚未充分回答”自动拒发。
3. 根据 `task_type` 理解任务目的。首次加微除特殊情况默认发送；不能因为客户尚未开口或普通沉默而拒发。
4. 比较候选内容与最新聊天、场景和硬事实是否重复或冲突。
5. 所有任务都必须先完成 `send/no_send` 判断；再根据 `use_ai_copy` 选择原样发送或受限改写/替换文字。

# 6. 特殊情况与 no_send 边界
以下情况应 `no_send`：
- 客户已删除、明确要求停止联系，或处于投诉退款、健康风险等不适合营销的状态。
- 客户最新提出的问题尚未被普通 AI 或人工回答，SOP 会插入并打断正常回复链路。
- 客户刚说正在上班、开车、忙或稍后聊，客服已承接等待，之后没有新客户消息。
- 人工正在连续接待，本任务会插入真实会话。
- 已付或已预约，而任务仍在催付、重复预约或重复介绍已完成动作。
- 对非首次加微任务，近期已经完整发送相同核心内容、相同素材和相同行动要求。
- 对首次加微任务，只有本轮全部消息的类型、顺序、文字和 URL 与近期某一完整发送批次逐项完全一致时，才按重复内容 `no_send`。文案不完全一致、仅语义相近、连续两次询问地址或多次推送不同效果内容都允许发送。
- `use_ai_copy=false` 且候选内容与客户、场景或硬事实冲突，因为固定任务不允许 AI 篡改，只能 `no_send`。
- 候选消息包含冲突媒体；图片、视频和链接不可替换，无法仅靠文字改写解决。

# 7. 内容冲突时的处理
当 `use_ai_copy=true` 且冲突只涉及文字时，不要机械 `no_send`：
1. 先从最新聊天判断客户当前需求、主要顾虑、卡点和任务真正需要完成的动作。
2. 从 `material_library` 选择适合当前场景的应对思路和示例，改写成自然微信表达。命中素材后必须直接解决客户当前卡点，并让素材的核心应对思路在客户可见文字中落地；`response_approach` 中并列写出的每个关键应对点都必须覆盖，不能擅自换成另一个更泛的活动事实。禁止退化成“我给您发详情、您先看看、方便再说”等没有解决顾虑的泛化承接。普通价格、效果、信任异议有匹配素材且不存在硬边界时，默认优先 `send` 改写后的直接答疑，不要把它误判成待工具处理问题。
3. 如果素材库为空或没有合适切片，使用 `authoritative_business_facts` 按现行业务标准生成。
4. 保留第三方任务的合理目标，但不得保留已经过时、重复或与客户当前状态冲突的具体表述。
5. 只能改写已有文字；媒体、链接、数量、类型和顺序必须保持不变。若结构无法承载修复，返回 `no_send`。

# 8. use_ai_copy 边界
## false
- 必须调用本模型判断 `send/no_send`。
- 若判断 `send`，最终发送的所有类型、内容、URL 和顺序必须与平台输入完全一致；模型输出的改写文本会被代码丢弃。
- 若平台原文与最新聊天、客户状态或硬事实冲突，必须 `no_send`，不要试图改写修复。

## true
- 可以改写已有 text，让它承接最新聊天，像真人微信沟通。
- 不得改变权威价格、项目、门店、退款、支付和预约事实。
- 不得增删消息，不得修改或重排 image/video/link。
- `message_content` 为空时，可根据可信 scene、素材库或权威业务事实生成 1–2 条短 text；没有可信目标时 `no_send`。
- 不得生成预约金卡、任意 URL 或平台未提供的素材。

# 9. 风格
- 简短、口语、自然，先承接当前上下文，再完成本轮任务。
- 不写公告、公文、内部流程或“我继续帮您处理”式空话。
- 不复述大段历史，不重复客户已经听过的同一事实。
- 首次加微可以轻量修改开头，使第一句话自然，但不能把任务改成与首次接触无关的营销催促。

# 9.1 校准案例
1. `task_type=add_wecom`、客户从未开口或仅有企微自动欢迎语、没有现实冲突：倾向 `send`。
2. `task_type=add_wecom`，但客户最后问“你们店在哪里”，之后没有任何助手回答：必须 `no_send`，由普通 AI 先调用门店工具回答。
3. 客户说“我担心到店加价”，这属于可由素材和权威业务事实解决的异议，不等同于待工具处理的客户问题。素材库提供“先承接担心，再说明活动范围内费用透明且额外项目不强制”时，若允许改写且本轮适合发送，文字必须直接解释费用透明和不强制，不能只说“给您发详情”。
4. 首次加微近期发过“请问您在哪个区”，本轮是“您方便发城市或定位吗”：两段文案并不完全一致，应允许发送；不同效果文案或不同效果素材的连续推送同理。

# 10. 输出合同
只返回小写 `json` 对象，不要 Markdown、解释或额外字段：
{
  "decision": "send | no_send",
  "reason": "基于最新事实的简明依据",
  "reply_messages": []
}
`send` 时 `reply_messages` 必须非空；`no_send` 时必须是空数组。
文字消息示例：{"type":"text","order":1,"content":{"text":"客户可见内容"}}
""".strip() + """

# AICS platform SOP decision guard
This section is authoritative when it conflicts with vague wording above.

You are not the scheduler. The third-party SOP platform owns task timing,
frequency, strategy and message candidates. AICS only performs a final
send/no_send check against the latest customer state.

Decision hierarchy:
1. Hard no_send: deleted customer, explicit stop contact, complaint/refund,
   health risk, paid/appointment conflict, active human takeover, invalid task.
2. Unresolved latest customer request: no_send only when the latest customer
   message asks for a concrete action or fact that still needs the normal AI
   chain or a human/tool answer, such as store lookup, order/payment check, or
   appointment lookup.
3. Duplicate or near-duplicate: no_send when the final customer-visible copy
   would repeat the same core facts, same offer, same appointment/deposit CTA,
   and same reassurance that were already sent recently to the same customer by
   this receiving WeChat account. Do not require byte-for-byte equality when the
   semantic customer value is unchanged.
4. First-add tasks (`add_wecom`) default to send. Silence, no expressed demand,
   old resolved store context, ordinary hesitation, or `use_ai_copy=false` are
   never valid no_send reasons for first-add tasks.
5. If `use_ai_copy=false`, still judge send/no_send. If send, AICS will send
   the original platform messages exactly and ignore rewritten text.

When decision is no_send, include:
{
  "decision": "no_send",
  "reason_code": "one allowed code",
  "reason": "short evidence-based reason",
  "reply_messages": []
}

Allowed first-add no_send reason_code values:
customer_deleted, explicit_stop_contact, complaint_or_refund, health_risk,
paid_or_appointment_conflict, human_takeover, unresolved_customer_question,
exact_duplicate, invalid_task.

When decision is send, `reason_code` may be omitted or set to `send`.
Return lowercase valid json only.
""".strip()


FIRST_ADD_NO_SEND_REASON_CODES = {
    "customer_deleted",
    "explicit_stop_contact",
    "complaint_or_refund",
    "health_risk",
    "paid_or_appointment_conflict",
    "human_takeover",
    "unresolved_customer_question",
    "exact_duplicate",
    "invalid_task",
}

SOP_PLATFORM_KNOWLEDGE_NO_SEND_REASON_CODES = {
    "complaint_or_refund",
    "explicit_stop_contact",
    "customer_deleted",
    "health_risk",
    "paid_or_appointment_conflict",
    "human_takeover",
    "platform_content_conflict",
    "exact_duplicate",
}


SOP_PLATFORM_KNOWLEDGE_TASK_PROMPT = """
# 角色
你是第三方平台 SOP 到期任务的发送审核与轻量润色节点。

# 核心目标
客户已经真实开口，平台现在要求发送 `task.required_delivery` 中的本次 SOP 内容。
你的主要职责不是重新规划销售流程，也不是从知识库另选话术，而是：
1. 判断本次平台内容现在是否仍适合发送；
2. 适合发送时，保留本次任务的业务目标、事实和消息组合，只做必要的自然润色；
3. 输出最终实际发送的完整 `reply_messages`。

模型输入只包含本次平台原始消息、最近聊天、近期已发媒体、不可重复素材指纹和必要业务事实。`dispatchMode` 与 `useAiCopy` 已弃用，不参与判断。
默认倾向 `send`。普通沉默、普通考虑、距离远、价格/效果/时间顾虑、客户暂时未回复，都不是不发送理由。

# 决策原则
1. 先判断硬边界。只有硬边界才允许 `no_send`。
2. 本次平台内容与最新聊天不冲突、近期未重复、事实仍有效时，必须发送；不得为了“更自然”擅自删除图片、视频、卡片或关键业务信息。
3. 可以润色文本，使其承接最近聊天、像微信短聊，但不得改变业务场景、价格、退款口径、媒体 URL、卡片类型或消息顺序所表达的交付目标。
4. 如果客户提出了与本次内容相关的普通顾虑，应在保留本次任务目标的前提下自然承接，不能改成另一套销售流程，也不能用“以后再说、需要的话再发”回避交付。
5. 最近聊天已完整发送相同业务内容，或 `forbidden_media_fingerprints` 已包含本次图片/视频时，输出 `no_send/exact_duplicate`；不得换句话重复，也不得编造替代素材。
6. 本次平台内容与客户明确状态冲突时，输出 `no_send/platform_content_conflict`。例如客户已经明确拒绝当前活动、当前问题尚未处理而本次内容会明显答非所问，或本次内容包含已经失效的事实。
7. 所有称谓使用中性表达，如“亲、您、顾客、很多客户”，禁止推断性别。
8. 平台文本中的旧价格、旧活动、旧项目必须按权威业务事实修正：
   - 当前淡斑活动价 268 元；
   - 10 元预约金，到店抵扣，做的话再付 258 元；
   - 当前项目围绕淡斑、检测皮肤、基础清洁、肌肤补水；
   - 当前活动包含送一次价值180元的美白管理，也可表达为赠送美白小气泡；
   - 不主动强调具体原价金额，只能说名额满后恢复原价。
9. 不得编造当前事实中不存在的项目、门店、订单、支付成功、预约成功、赠品或额外服务。
10. 图片/视频是本次任务的真实交付。平台原文包含 image/video 时，除重复或硬冲突导致整单 `no_send` 外，必须保留原 URL，不得改成纯文本，也不得生成新 URL。
11. `payment_collection` 只能在本次平台原始消息本身包含该类型时保留，不能凭空新增。若已付/已预约或最近连续发过预约金卡，应 `no_send`，不得机械重复催付。
12. 文案要像微信短聊，直接承接客户，不写内部分析、流程解释或模型判断。

# no_send 边界
只允许以下原因：
- `complaint_or_refund`：严重客诉、退款纠纷、投诉升级；
- `explicit_stop_contact`：客户明确要求不要再联系；
- `customer_deleted`：客户关系删除；
- `health_risk`：健康高风险，不适合营销；
- `paid_or_appointment_conflict`：已付/已预约且本任务会重复催付或重复预约；
- `human_takeover`：人工正在连续接待，发送会插话；
- `platform_content_conflict`：平台原始消息与客户当前状态或权威事实存在明确冲突；
- `exact_duplicate`：平台原始内容、核心语义或媒体近期已经发送。

普通沉默、普通价格/效果/距离/时间顾虑、客户说考虑一下，都必须 `send`。

# 校准示例
1. 平台任务携带“活动价 + 活动图”，历史尚未完整发送：保留活动方向、关键信息和活动图，只把文字润色得更自然。
2. 平台任务携带“活动价 + 活动图”，最近已经完整发送同一活动和同一图片：输出 `no_send/exact_duplicate`，不得自行换素材。
3. 客户正在与人工连续对话，平台任务此时插入会打断人工：输出 `no_send/human_takeover`。
4. 客户明确投诉并要求不要再联系：输出 `no_send`，不得用其他营销内容绕开停止联系要求。

# 输出 JSON
只返回 JSON，不要 Markdown，不要解释。Schema：
{
  "decision": "send | no_send",
  "reason_code": "send 或 no_send 原因码",
  "reason": "简短说明为什么这样处理",
  "sceneName": "回写平台的场景名称",
  "sceneCode": "回写平台的场景编码",
  "knowledgeId": 0,
  "knowledgeParagraphNo": 0,
  "remark": "回写备注，说明发送审核结论",
  "reply_messages": [
    {"type": "text", "order": 1, "content": {"text": "客户可见内容"}},
    {"type": "image", "order": 2, "content": {"url": "https://..."}},
    {"type": "video", "order": 3, "content": {"url": "https://..."}},
    {"type": "payment_collection", "order": 4, "content": {"amount": 10, "remark": ""}}
  ]
}

`send` 时 `reply_messages` 必须非空。`no_send` 时 `reply_messages` 必须为空，但也必须输出 sceneName、sceneCode、reason_code 和 remark 用于回写。
不再从平台知识库选内容，`knowledgeId` 和 `knowledgeParagraphNo` 固定为 0。
发送时使用 `正常推进｜平台任务内容`，`sceneCode` 使用 `normal_platform_intent`；不发送时按硬边界填写对应场景和编码。
""".strip()

SOP_PLATFORM_KNOWLEDGE_TASK_PROMPT = """
# 角色
你是第三方平台 SOP 到期任务的发送审核与轻量润色节点。平台决定任务时间、频率和候选消息；你只判断当前任务现在发送还是不发送，并在允许发送时输出最终消息。

# 输入边界
- `task.required_delivery` 是本次必须审核的唯一消息来源。
- `latest_context` 是最近聊天、客户关系、业务状态、近期已发媒体和禁止重复素材指纹。
- `authoritative_business_facts` 高于历史聊天中的旧价格和旧口径。
- `scene_catalog` 是唯一合法业务场景。你只能输出其中一个 `sceneCode`，不得创造新标签。
- `knowledge_scene_catalog` 只用于记录知识命中。没有真实命中时 `knowledgeId` 和 `knowledgeParagraphNo` 都输出 0。

# 决策原则
1. 默认发送。普通沉默、考虑、距离、价格、效果或时间顾虑都不是不发送理由。
2. 只有场景目录中的不发送业务场景才允许 `no_send`：严重客诉或退款纠纷、明确停止联系、客户关系删除、健康风险、已付或已预约冲突、人工正在连续接待、平台内容与当前事实明确冲突。
3. 客户存在距离、效果、价格或时间异议时，选择对应异议场景，并在保留本次平台交付目标的前提下自然承接。不得改成另一套销售流程。
4. 没有明确异议时，根据平台内容选择“平台任务内容、轻触达效果展示、活动价格”之一。
5. 图片、视频、链接和预约金卡的类型、URL、数量和顺序必须与平台原始消息一致；只能润色已有文字，不得生成新素材或卡片。
6. 不得虚构门店、订单、支付、预约、价格、赠品或服务。使用中性称谓，不推断性别。
7. `sceneEvidence` 必须引用本轮可见事实，简短说明为什么选择该场景。
8. 若填写 `knowledgeId`，它必须与所选 `sceneCode` 在 `knowledge_scene_catalog` 中匹配；`knowledgeParagraphNo` 必须大于 0。否则两者都填 0。

# 输出合同
只返回 JSON，不要 Markdown 或额外字段：
{
  "decision": "send | no_send",
  "sceneCode": "scene_catalog 中的合法编码",
  "sceneEvidence": "场景证据",
  "knowledgeId": 0,
  "knowledgeParagraphNo": 0,
  "reason": "处理依据",
  "remark": "策略回传备注",
  "reply_messages": []
}

`send` 时 `reply_messages` 必须非空；`no_send` 时必须为空。不要输出 `sceneName`，名称由代码根据注册表生成。
""".strip()

# Backward-compatible export; the retired pre-dispatch prompt must not be used.
SOP_PLATFORM_TASK_SYSTEM_PROMPT = SOP_PLATFORM_KNOWLEDGE_TASK_PROMPT


class SopPlatformTaskService:
    RECOVERY_STATUSES = [
        "platform_claiming",
        "platform_judging",
        "platform_processing",
        "platform_processing_retry",
        "platform_send_uncertain",
        "platform_complete_pending",
        "platform_terminal_pending",
    ]

    def __init__(
        self,
        *,
        settings: Any,
        repository: Any,
        platform_client: Any,
        system_client: Any,
        model_client: Any,
        customer_context_service: Any,
        objection_material_service: Any | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.platform_client = platform_client
        self.system_client = system_client
        self.model_client = model_client
        self.customer_context_service = customer_context_service
        self.objection_material_service = objection_material_service
        self._locks: dict[str, asyncio.Lock] = {}
        queue_size = max(1, int(getattr(settings, "sop_platform_queue_size", 24) or 24))
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=queue_size)
        self._queued_ids: set[str] = set()
        self._in_flight_ids: set[str] = set()
        self._terminal_ids: set[str] = set()
        self._terminal_order: deque[str] = deque()
        self._terminal_reack_at: dict[str, float] = {}
        self._workers: list[asyncio.Task[None]] = []
        self._recovery_worker: asyncio.Task[None] | None = None
        self._running = False
        self._counters: Counter[str] = Counter()
        self._timings: dict[str, deque[float]] = {
            name: deque(maxlen=500)
            for name in ("pull", "claim", "context", "model", "send", "task", "queue_lag")
        }
        self._last_poll_at = ""
        self._last_poll_error = ""
        self._pending_total = 0
        self._oldest_due_lag_seconds = 0.0

    async def run(self) -> None:
        if self._running:
            raise RuntimeError("third-party SOP worker is already running")
        self._running = True
        concurrency = max(1, int(getattr(self.settings, "sop_platform_task_concurrency", 6) or 6))
        self._workers = [
            asyncio.create_task(self._queue_worker(index), name=f"sop-platform-worker-{index}")
            for index in range(concurrency)
        ]
        self._recovery_worker = asyncio.create_task(
            self._recovery_loop(),
            name="sop-platform-recovery",
        )
        try:
            while True:
                try:
                    result = await self.poll_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self._counters["poll_loop_error"] += 1
                    logger.exception("Third-party SOP polling iteration failed; worker will continue")
                    result = {
                        "pending_count": self._pending_total,
                        "enqueued_count": 0,
                        "queue_depth": self._queue.qsize(),
                        "in_flight_count": len(self._in_flight_ids),
                        "error_count": 1,
                    }
                if result.get("pending_count") or result.get("error_count"):
                    logger.info("Third-party SOP worker result: %s", result)
                await asyncio.sleep(max(0.2, float(self.settings.sop_platform_poll_seconds)))
        finally:
            self._running = False
            tasks = [*self._workers]
            if self._recovery_worker is not None:
                tasks.append(self._recovery_worker)
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            self._workers = []
            self._recovery_worker = None

    async def poll_once(self) -> dict[str, int]:
        free_slots = max(0, self._queue.maxsize - self._queue.qsize())
        if free_slots <= 0:
            return {
                "pending_count": self._pending_total,
                "enqueued_count": 0,
                "queue_depth": self._queue.qsize(),
                "in_flight_count": len(self._in_flight_ids),
                "error_count": 0,
            }
        configured_limit = max(1, min(int(self.settings.sop_platform_batch_size), 500))
        limit = max(1, min(max(configured_limit, free_slots * 20), 500))
        started = time.perf_counter()
        self._last_poll_at = utc_now_iso()
        try:
            page = await self.platform_client.pending(limit=limit)
            self._last_poll_error = ""
        except Exception as exc:
            self._last_poll_error = f"{type(exc).__name__}: {exc}"
            self._counters["poll_error"] += 1
            raise
        finally:
            self._observe("pull", time.perf_counter() - started)
        if isinstance(page, list):
            page = {"items": page, "total": len(page)}
        tasks = page.get("items") if isinstance(page.get("items"), list) else []
        tasks = sorted(
            (dict(item) for item in tasks if isinstance(item, dict)),
            key=lambda item: (_task_scheduled_epoch(item) or float("inf"), _task_id(item)),
        )
        self._pending_total = max(len(tasks), int(page.get("total") or 0))
        now_epoch = time.time()
        lags = [max(0.0, now_epoch - value) for value in map(_task_scheduled_epoch, tasks) if value]
        self._oldest_due_lag_seconds = max(lags, default=0.0)
        if self._oldest_due_lag_seconds > 120:
            logger.warning(
                "Third-party SOP queue lag is %.1fs (pending=%s)",
                self._oldest_due_lag_seconds,
                self._pending_total,
            )
        enqueued = 0
        pulled_at = utc_now_iso()
        for task in tasks:
            task_id = _task_id(task)
            if (
                not task_id
                or task_id in self._queued_ids
                or task_id in self._in_flight_ids
                or task_id in self._terminal_ids
            ):
                if task_id and task_id in self._terminal_ids:
                    await self._reack_terminal_pending_task(task_id)
                self._counters["duplicate_poll"] += 1
                continue
            if self._queue.full():
                break
            task["_aics_pulled_at"] = pulled_at
            try:
                event, local_task = await asyncio.to_thread(
                    self._ensure_local_task,
                    task,
                    status="platform_queued",
                )
            except Exception:
                self._counters["persistence_error"] += 1
                logger.exception("Unable to persist pulled third-party SOP task: %s", task_id)
                continue
            if str(event.get("status") or "") in self.RECOVERY_STATUSES or str(
                local_task.get("status") or ""
            ) in {"judging", "processing_retry", "sending", "failed"}:
                self._counters["recovery_deferred"] += 1
                continue
            self._queued_ids.add(task_id)
            self._queue.put_nowait(task)
            enqueued += 1
        self._counters["fetched"] += len(tasks)
        self._counters["enqueued"] += enqueued
        return {
            "pending_count": self._pending_total,
            "enqueued_count": enqueued,
            "queue_depth": self._queue.qsize(),
            "in_flight_count": len(self._in_flight_ids),
            "terminal_dedupe_count": len(self._terminal_ids),
            "error_count": 0,
        }

    async def _queue_worker(self, _index: int) -> None:
        while True:
            platform_task = await self._queue.get()
            task_id = _task_id(platform_task)
            self._queued_ids.discard(task_id)
            self._in_flight_ids.add(task_id)
            started = time.perf_counter()
            scheduled = _task_scheduled_epoch(platform_task)
            if scheduled:
                self._observe("queue_lag", max(0.0, time.time() - scheduled))
            try:
                result = await self.process_task(platform_task)
                self._record_result(result)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._counters["retry"] += 1
                logger.exception("Third-party SOP task failed and remains recoverable: %s", task_id)
            finally:
                self._observe("task", time.perf_counter() - started)
                self._in_flight_ids.discard(task_id)
                self._queue.task_done()

    async def _recovery_loop(self) -> None:
        while True:
            try:
                await self.process_recoveries()
            except asyncio.CancelledError:
                raise
            except Exception:
                self._counters["recovery_error"] += 1
                logger.exception("Third-party SOP recovery iteration failed")
            await asyncio.sleep(max(1.0, float(self.settings.sop_platform_poll_seconds)))

    async def process_recoveries(self) -> int:
        events = await asyncio.to_thread(
            self.repository.list_sop_events_by_statuses,
            self.RECOVERY_STATUSES,
            limit=self.settings.sop_platform_recovery_batch_size,
            event_type="platform_sop_task",
        )
        concurrency = max(1, int(getattr(self.settings, "sop_platform_recovery_concurrency", 2) or 2))
        semaphore = asyncio.Semaphore(concurrency)

        async def recover(event: dict[str, Any]) -> int:
            payload = event.get("raw_payload") if isinstance(event.get("raw_payload"), dict) else {}
            task = payload.get("platform_task") if isinstance(payload.get("platform_task"), dict) else {}
            if not task:
                event_id = str(event.get("event_id") or "")
                task_id = event_id.split(":", 1)[1] if event_id.startswith("platform_sop_task:") else ""
                if task_id:
                    try:
                        self.repository.update_sop_event_status(
                            event_id,
                            status="platform_terminal_pending",
                            error="missing_platform_task_payload",
                        )
                        response = await self.platform_client.consume(
                            task_id=task_id,
                            status=70,
                            remark="missing_platform_task_payload",
                        )
                        _require_platform_status(response, 70)
                        self.repository.update_sop_event_status(
                            event_id,
                            status="platform_failed",
                            error="missing_platform_task_payload",
                        )
                        self._remember_terminal(task_id)
                        self._counters["failed"] += 1
                        return 1
                    except Exception:
                        return 0
                self.repository.update_sop_event_status(
                    event_id,
                    status="platform_failed",
                    error="missing_platform_task_payload",
                )
                return 0
            task_id = _task_id(task)
            if task_id in self._queued_ids or task_id in self._in_flight_ids:
                return 0
            async with semaphore:
                try:
                    result = await self.process_task(task, recovery_status=str(event.get("status") or ""))
                    self._record_result(result)
                    return 1 if result.get("processed") else 0
                except Exception:
                    return 0

        if not events:
            return 0
        return sum(await asyncio.gather(*(recover(event) for event in events)))

    async def process_task(self, platform_task: dict[str, Any], *, recovery_status: str = "") -> dict[str, Any]:
        task_id = _task_id(platform_task)
        if not task_id:
            raise ValueError("platform task_id is required")
        try:
            lock = self._locks.setdefault(task_id, asyncio.Lock())
            async with lock:
                contact_lock_key = _platform_contact_lock_key(platform_task)
                if contact_lock_key:
                    contact_lock = self._locks.setdefault(f"platform-contact:{contact_lock_key}", asyncio.Lock())
                    async with contact_lock:
                        return await self._process_with_content_lock(
                            platform_task,
                            task_id=task_id,
                            recovery_status=recovery_status,
                        )
                return await self._process_with_content_lock(
                    platform_task,
                    task_id=task_id,
                    recovery_status=recovery_status,
                )
        except SopPlatformTaskStateError as exc:
            return self._finish_terminal_platform_task(platform_task, task_id=task_id, error=exc)

    def _finish_terminal_platform_task(
        self,
        platform_task: dict[str, Any],
        *,
        task_id: str,
        error: SopPlatformTaskStateError,
    ) -> dict[str, Any]:
        event_id = f"platform_sop_task:{task_id}"
        local_task = self.repository.get_sop_send_task_by_idempotency_key(f"platform-sop:{task_id}")
        state_contract = {
            "已取消": ("platform_task_cancelled", "platform_cancelled", "completed_without_send"),
            "已完成": ("platform_task_already_completed", "platform_completed", "completed_without_send"),
            "已失败": ("platform_task_already_failed", "platform_failed", "failed"),
            "失败": ("platform_task_already_failed", "platform_failed", "failed"),
            "已不发送": ("platform_task_already_no_send", "platform_no_send", "completed_without_send"),
            "不发送": ("platform_task_already_no_send", "platform_no_send", "completed_without_send"),
        }
        reason, event_status, task_status = state_contract.get(
            error.state,
            ("platform_task_already_completed", "platform_completed", "completed_without_send"),
        )
        if local_task:
            self.repository.update_sop_send_task(
                str(local_task.get("id") or ""),
                status=task_status,
                send_response={"platform_terminal_state": error.state, "response": error.payload},
                error=reason,
            )
        self.repository.update_sop_event_status(event_id, status=event_status, error=reason)
        self._counters[reason] += 1
        logger.info("Third-party SOP task %s reached platform terminal state: %s", task_id, error.state)
        return {
            "processed": True,
            "status": task_status,
            "task_id": task_id,
            "reason": reason,
            "platform_response": error.payload,
        }

    async def _commit_platform_terminal(
        self,
        *,
        task_id: str,
        event_id: str,
        terminal_status: int,
        event_status: str = "",
        remark: str | None = None,
    ) -> dict[str, Any]:
        if terminal_status not in {30, 70}:
            raise ValueError(f"unsupported platform terminal status: {terminal_status}")
        local_task = self.repository.get_sop_send_task_by_idempotency_key(f"platform-sop:{task_id}")
        if local_task:
            audit = local_task.get("send_payload") if isinstance(local_task.get("send_payload"), dict) else {}
            self.repository.update_sop_send_task(
                str(local_task.get("id") or ""),
                status=str(local_task.get("status") or "platform_received"),
                send_payload={
                    **audit,
                    "platform_terminal_status": terminal_status,
                    "platform_terminal_event_status": event_status,
                },
            )
        self.repository.update_sop_event_status(event_id, status="platform_terminal_pending")
        response = await self.platform_client.consume(
            task_id=task_id,
            status=terminal_status,
            remark=_platform_consume_remark(local_task) if remark is None else str(remark)[:500],
        )
        _require_platform_status(response, terminal_status)
        self.repository.update_sop_event_status(
            event_id,
            status=event_status or {30: "platform_completed", 70: "platform_no_send"}[terminal_status],
        )
        return response

    def _stored_terminal_status(self, local_task: dict[str, Any]) -> int:
        payload = local_task.get("send_payload") if isinstance(local_task.get("send_payload"), dict) else {}
        try:
            explicit = int(payload.get("platform_terminal_status") or 0)
        except (TypeError, ValueError):
            explicit = 0
        if explicit in {30, 70}:
            return explicit
        if explicit == 40:
            return 70
        task_status = str(local_task.get("status") or "")
        error = str(local_task.get("error") or "")
        decision = payload.get("decision") if isinstance(payload.get("decision"), dict) else {}
        reason_code = str(decision.get("reason_code") or "")
        if task_status == "sent":
            return 30
        if task_status == "failed":
            return 70
        if task_status in {"completed_without_send", "shadow_no_send"}:
            return 70
        if error or reason_code == "no_send_downstream_rejected":
            return 70
        return 70

    def _stored_terminal_event_status(self, local_task: dict[str, Any]) -> str:
        payload = local_task.get("send_payload") if isinstance(local_task.get("send_payload"), dict) else {}
        explicit = str(payload.get("platform_terminal_event_status") or "")
        if explicit in {"platform_completed", "platform_failed", "platform_no_send"}:
            return explicit
        return "platform_failed" if str(local_task.get("status") or "") == "failed" else ""

    async def _process_with_content_lock(
        self,
        platform_task: dict[str, Any],
        *,
        task_id: str,
        recovery_status: str,
    ) -> dict[str, Any]:
        duplicate_key = _platform_duplicate_send_once_key(platform_task)
        if duplicate_key:
            content_lock = self._locks.setdefault(f"platform-content:{duplicate_key}", asyncio.Lock())
            async with content_lock:
                return await self._process_locked(platform_task, task_id=task_id, recovery_status=recovery_status)
        return await self._process_locked(platform_task, task_id=task_id, recovery_status=recovery_status)

    def runtime_status(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "queue_depth": self._queue.qsize(),
            "queue_capacity": self._queue.maxsize,
            "queued_count": len(self._queued_ids),
            "in_flight_count": len(self._in_flight_ids),
            "pending_total": self._pending_total,
            "oldest_due_lag_seconds": round(self._oldest_due_lag_seconds, 3),
            "last_poll_at": self._last_poll_at,
            "last_poll_error": self._last_poll_error,
            "quiet_hours": {
                "enabled": bool(getattr(self.settings, "sop_platform_quiet_hours_enabled", True)),
                "timezone": "Asia/Shanghai",
                "start_hour": _bounded_hour(
                    getattr(self.settings, "sop_platform_quiet_start_hour", 0),
                    default=0,
                ),
                "end_hour": _bounded_hour(
                    getattr(self.settings, "sop_platform_quiet_end_hour", 8),
                    default=8,
                ),
                "first_add_grace_minutes": max(
                    0,
                    int(getattr(self.settings, "sop_platform_quiet_first_add_grace_minutes", 30) or 0),
                ),
            },
            "counters": dict(self._counters),
            "timings_ms": {name: _timing_summary(values) for name, values in self._timings.items()},
        }

    async def admin_task_logs(
        self,
        *,
        limit: int = 100,
        bucket: str = "",
        decision: str = "",
        task_id: str = "",
        customer_id: str = "",
        refresh_platform: bool = True,
    ) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit or 100), 500))
        platform_page: dict[str, Any] = {"items": [], "total": 0}
        platform_error = ""
        if refresh_platform:
            try:
                platform_page = await self.platform_client.pending(limit=safe_limit)
            except Exception as exc:
                platform_error = f"{type(exc).__name__}: {exc}"
        local_records = self.repository.list_platform_sop_task_records(
            limit=safe_limit,
            task_id=task_id,
            customer_id=customer_id,
        )
        platform_items = platform_page.get("items") if isinstance(platform_page.get("items"), list) else []
        items = _merge_platform_task_logs(platform_items=platform_items, local_records=local_records)
        if task_id:
            items = [item for item in items if item["task_id"] == str(task_id).strip()]
        if customer_id:
            items = [item for item in items if item["customer_id"] == str(customer_id).strip()]
        if bucket:
            items = [item for item in items if item["bucket"] == bucket]
        if decision:
            items = [item for item in items if item["decision"] == decision]
        items = items[:safe_limit]
        summary = Counter(item["bucket"] for item in items)
        return {
            "summary": {
                "platform_pending_total": int(platform_page.get("total") or 0),
                "visible_total": len(items),
                "platform_pending": summary["platform_pending"],
                "pulled_unjudged": summary["pulled_unjudged"],
                "judging": summary["judging"],
                "judged_send": summary["judged_send"],
                "judged_no_send": summary["judged_no_send"],
                "sending": summary["sending"],
                "sent": summary["sent"],
                "failed": summary["failed"],
                "recovery": summary["recovery"],
            },
            "platform": {
                "refreshed": refresh_platform,
                "error": platform_error,
                "query_start_time": platform_page.get("start_time"),
                "query_end_time": platform_page.get("end_time"),
            },
            "worker": self.runtime_status(),
            "items": items,
        }

    async def admin_resend_task(self, task_id: str) -> dict[str, Any]:
        clean_task_id = str(task_id or "").strip()
        if not clean_task_id:
            raise ValueError("task_id is required")
        event = self.repository.get_sop_event(f"platform_sop_task:{clean_task_id}")
        raw_payload = event.get("raw_payload") if isinstance(event.get("raw_payload"), dict) else {}
        platform_task = raw_payload.get("platform_task") if isinstance(raw_payload.get("platform_task"), dict) else {}
        lock = self._locks.setdefault(clean_task_id, asyncio.Lock())
        async with lock:
            contact_lock_key = _platform_contact_lock_key(platform_task)
            if contact_lock_key:
                contact_lock = self._locks.setdefault(f"platform-contact:{contact_lock_key}", asyncio.Lock())
                async with contact_lock:
                    return await self._admin_resend_task_locked(clean_task_id)
            return await self._admin_resend_task_locked(clean_task_id)

    async def _admin_resend_task_locked(self, task_id: str) -> dict[str, Any]:
        event_id = f"platform_sop_task:{task_id}"
        event = self.repository.get_sop_event(event_id)
        if not event:
            raise ValueError("platform task not found")
        payload = event.get("raw_payload") if isinstance(event.get("raw_payload"), dict) else {}
        platform_task = payload.get("platform_task") if isinstance(payload.get("platform_task"), dict) else {}
        if not platform_task:
            raise ValueError("platform task payload is missing")
        event_status = str(event.get("status") or "")
        local_task = self.repository.get_sop_send_task_by_idempotency_key(f"platform-sop:{task_id}")
        if not local_task:
            _event, local_task = self._ensure_local_task(platform_task, status="platform_received")
        task_status = str(local_task.get("status") or "")
        if task_status in {"sent", "sending"} or event_status == "platform_send_uncertain":
            raise RuntimeError("task already sent or sending")

        identity = _task_identity(platform_task)
        preflight_reason = _manual_resend_preflight_block_reason(
            platform_task,
            identity=identity,
            settings=self.settings,
        )
        if preflight_reason:
            raise RuntimeError(f"task cannot be resent: {preflight_reason}")

        await self._manual_resend_relation_guard(identity)
        resend_context = await self._load_context(platform_task, identity=identity)
        resend_opening_state = (
            resend_context.get("opening_state")
            if isinstance(resend_context.get("opening_state"), dict)
            else {}
        )
        resend_guard = self._platform_contact_delivery_guard(
            identity=identity,
            task_id=task_id,
            opening_state=resend_opening_state,
        )
        if resend_guard.get("blocked"):
            raise RuntimeError(f"task cannot be resent: {resend_guard.get('reason')}")
        messages = _manual_resend_messages(local_task, platform_task)
        decision_reason = "manual_resend"
        context: dict[str, Any] = {
            "source": "manual_resend",
            "original_event_status": event_status,
            "original_task_status": task_status,
            "platform_contact_delivery_guard": resend_guard,
        }
        if not messages:
            context = resend_context
            context["platform_contact_delivery_guard"] = resend_guard
            decision = await self._decide(platform_task, context=context)
            if decision["decision"] != "send" or not decision["reply_messages"]:
                raise RuntimeError(f"manual resend produced no sendable content: {decision.get('reason') or 'no_send'}")
            messages = decision["reply_messages"]
            decision_reason = f"manual_resend_ai_copy:{decision.get('reason') or ''}"

        send_payload = {
            **identity,
            "plan_id": f"platform-sop-{task_id}",
            **_platform_send_trace_fields(platform_task),
            "reply_messages": messages,
        }
        audit_payload = {
            "decision": {"decision": "send", "reason": decision_reason, "reply_messages": messages},
            "request": send_payload,
            "context": _context_audit(context),
        }
        original_terminal_status = {
            "platform_completed": 30,
            "platform_failed": 70,
            "platform_no_send": 70,
        }.get(event_status)
        if original_terminal_status:
            audit_payload["platform_terminal_status"] = original_terminal_status
        self.repository.update_sop_send_task(str(local_task.get("id") or ""), status="sending", send_payload=audit_payload)
        started = time.perf_counter()
        send_result = await self.system_client.send(**send_payload)
        self._observe("send", time.perf_counter() - started)
        send_status = str((send_result.get("data") or {}).get("send_status") or send_result.get("msg") or "")
        if send_status == "accepted_no_response":
            self.repository.update_sop_event_status(event_id, status="platform_send_uncertain", error="active_send_timeout_unknown_result")
            self.repository.update_sop_send_task(
                str(local_task.get("id") or ""),
                status="processing_retry",
                send_payload=audit_payload,
                error="active_send_timeout_unknown_result",
            )
            raise RuntimeError("active_send_timeout_unknown_result")

        self.repository.update_sop_send_task(
            str(local_task.get("id") or ""),
            status="sent",
            send_payload=audit_payload,
            send_response=send_result,
            sent_at=utc_now_iso(),
        )
        if event_status not in {"platform_completed", "platform_failed", "platform_no_send"} and not self.settings.sop_platform_shadow_mode:
            await self._commit_platform_terminal(task_id=task_id, event_id=event_id, terminal_status=30)
        self._remember_terminal(task_id)
        self._counters["manual_resend"] += 1
        return {
            "processed": True,
            "status": "sent",
            "task_id": task_id,
            "reply_messages": messages,
            "send_response": send_result,
        }

    async def _manual_resend_relation_guard(self, identity: dict[str, str]) -> None:
        missing = [key for key in ("corp_id", "customer_id", "external_userid", "user_id", "wechat") if not identity[key]]
        if missing:
            raise RuntimeError(f"task cannot be resent: invalid_identity:{','.join(missing)}")
        try:
            conversation = await self.system_client.conversation(**identity, limit=1)
        except Exception as exc:
            raise RuntimeError(f"manual resend relation check failed: {type(exc).__name__}: {exc}") from exc
        data = conversation.get("data") if isinstance(conversation.get("data"), dict) else conversation
        relation = data.get("customer_relation") if isinstance(data.get("customer_relation"), dict) else {}
        if relation.get("is_deleted") is True or str(relation.get("status") or "").lower() == "deleted":
            raise RuntimeError("task cannot be resent: customer_relation_deleted")

    async def _existing_platform_delivery(
        self,
        *,
        identity: dict[str, str],
        send_payload: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            conversation = await self.system_client.conversation(**identity, limit=30)
        except Exception as exc:
            return {"found": False, "error": f"{type(exc).__name__}: {exc}"}
        data = conversation.get("data") if isinstance(conversation.get("data"), dict) else conversation
        messages = data.get("messages") if isinstance(data.get("messages"), list) else []
        return _platform_delivery_match(
            messages,
            plan_id=str(send_payload.get("plan_id") or ""),
            task_id=str(send_payload.get("task_id") or ""),
            reply_messages=(
                send_payload.get("reply_messages") if isinstance(send_payload.get("reply_messages"), list) else []
            ),
        )

    async def _recent_near_duplicate_platform_delivery(
        self,
        *,
        identity: dict[str, str],
        task_id: str,
        reply_messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not hasattr(self.repository, "list_platform_sop_task_records"):
            return {"found": False, "match_type": "unsupported"}
        try:
            records = self.repository.list_platform_sop_task_records(
                limit=500,
                customer_id=identity.get("customer_id") or "",
            )
        except Exception as exc:
            return {"found": False, "match_type": "lookup_error", "error": f"{type(exc).__name__}: {exc}"}
        return _platform_near_duplicate_delivery_match(
            records if isinstance(records, list) else [],
            identity=identity,
            current_task_id=task_id,
            reply_messages=reply_messages,
        )

    def _platform_contact_delivery_guard(
        self,
        *,
        identity: dict[str, str],
        task_id: str,
        opening_state: dict[str, Any],
    ) -> dict[str, Any]:
        if not hasattr(self.repository, "list_platform_sop_task_records"):
            return {"blocked": False, "reason": "history_lookup_unsupported"}
        try:
            records = self.repository.list_platform_sop_task_records(
                limit=500,
                customer_id=identity.get("customer_id") or "",
            )
        except Exception as exc:
            return {
                "blocked": False,
                "reason": "history_lookup_error",
                "error": f"{type(exc).__name__}: {exc}",
            }
        return _platform_contact_delivery_guard(
            records if isinstance(records, list) else [],
            identity=identity,
            current_task_id=task_id,
            latest_customer_at=opening_state.get("latest_real_customer_at_beijing"),
        )

    def _observe(self, name: str, elapsed_seconds: float) -> None:
        values = self._timings.get(name)
        if values is not None:
            values.append(max(0.0, float(elapsed_seconds)) * 1000)

    def _record_result(self, result: dict[str, Any]) -> None:
        status = str(result.get("status") or "unknown")
        if status in {
            "sent",
            "failed",
            "completed_without_send",
            "platform_completed",
            "platform_failed",
            "platform_no_send",
            "shadow_send",
            "shadow_no_send",
        }:
            self._remember_terminal(str(result.get("task_id") or ""))
        if status == "sent":
            self._counters["sent"] += 1
        elif status in {"completed_without_send", "shadow_no_send"}:
            self._counters["no_send"] += 1
        elif status in {"failed", "platform_failed"}:
            self._counters["failed"] += 1
        elif status == "shadow_send":
            self._counters["shadow_send"] += 1
        elif status == "platform_send_uncertain":
            self._counters["send_uncertain"] += 1
            logger.error("Third-party SOP send result is uncertain: %s", result.get("task_id"))

    def _remember_terminal(self, task_id: str) -> None:
        if not task_id or task_id in self._terminal_ids:
            return
        self._terminal_ids.add(task_id)
        self._terminal_order.append(task_id)
        while len(self._terminal_order) > 50_000:
            removed = self._terminal_order.popleft()
            self._terminal_ids.discard(removed)
            self._terminal_reack_at.pop(removed, None)

    async def _reack_terminal_pending_task(self, task_id: str, *, event_status: str = "") -> None:
        if self.settings.sop_platform_shadow_mode:
            return
        if not event_status:
            event = self.repository.get_sop_event(f"platform_sop_task:{task_id}")
            event_status = str(event.get("status") or "") if isinstance(event, dict) else ""
        now = time.time()
        last = self._terminal_reack_at.get(task_id, 0.0)
        if now - last < 300:
            return
        self._terminal_reack_at[task_id] = now
        try:
            terminal_status = {
                "platform_completed": 30,
                "platform_failed": 70,
                "platform_no_send": 70,
            }.get(event_status, 30)
            local_task = self.repository.get_sop_send_task_by_idempotency_key(f"platform-sop:{task_id}")
            completed = await self.platform_client.consume(
                task_id=task_id,
                status=terminal_status,
                remark=_platform_consume_remark(local_task),
            )
            _require_platform_status(completed, terminal_status)
            self._counters["terminal_reack"] += 1
        except Exception as exc:
            self._counters["terminal_reack_error"] += 1
            logger.warning(
                "Third-party SOP terminal task still appeared pending and re-ack failed: task_id=%s error=%s",
                task_id,
                f"{type(exc).__name__}: {exc}",
            )

    def _ensure_local_task(
        self,
        platform_task: dict[str, Any],
        *,
        status: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        task_id = _task_id(platform_task)
        if not task_id:
            raise ValueError("platform task_id is required")
        event_id = f"platform_sop_task:{task_id}"
        event = self.repository.create_sop_event(
            {
                "event_id": event_id,
                "event_type": "platform_sop_task",
                "source": "third_party_sop_pending",
                "request_reply": False,
                "created_at": str(platform_task.get("scheduledAt") or platform_task.get("scheduled_at") or ""),
                "platform_task": platform_task,
            }
        )
        current_status = str(event.get("status") or "")
        if event.get("created") or current_status in {"", "accepted", "platform_received", "platform_queued"}:
            event = self.repository.update_sop_event_status(event_id, status=status)
        identity = _task_identity(platform_task)
        local_task = self.repository.create_sop_send_task(
            event_id=event_id,
            idempotency_key=f"platform-sop:{task_id}",
            send_once_key=_platform_duplicate_send_once_key(platform_task),
            customer_id=identity["customer_id"],
            external_userid=identity["external_userid"],
            corp_id=identity["corp_id"],
            user_id=identity["user_id"],
            wechat=identity["wechat"],
            sop_pack_id=f"platform-sop-{task_id}",
            sop_pack_name=str(platform_task.get("ruleName") or platform_task.get("sceneName") or "第三方SOP任务"),
            sop_category="platform_task",
            trigger_source="third_party_sop_pending",
            reply_messages=_platform_messages(platform_task),
            status=status,
        )
        return event, local_task

    async def _process_locked(
        self,
        platform_task: dict[str, Any],
        *,
        task_id: str,
        recovery_status: str,
    ) -> dict[str, Any]:
        event_id = f"platform_sop_task:{task_id}"
        event_payload = {
            "event_id": event_id,
            "event_type": "platform_sop_task",
            "source": "third_party_sop_pending",
            "request_reply": False,
            "created_at": str(platform_task.get("scheduledAt") or platform_task.get("scheduled_at") or ""),
            "platform_task": platform_task,
        }
        event = self.repository.create_sop_event(event_payload)
        current_status = str(event.get("status") or "")
        if current_status in {"platform_completed", "platform_failed", "platform_no_send"}:
            await self._reack_terminal_pending_task(task_id, event_status=current_status)
            return {"processed": False, "status": current_status, "task_id": task_id}
        identity = _task_identity(platform_task)
        local_task = self.repository.create_sop_send_task(
            event_id=event_id,
            idempotency_key=f"platform-sop:{task_id}",
            send_once_key=_platform_duplicate_send_once_key(platform_task),
            customer_id=identity["customer_id"],
            external_userid=identity["external_userid"],
            corp_id=identity["corp_id"],
            user_id=identity["user_id"],
            wechat=identity["wechat"],
            sop_pack_id=f"platform-sop-{task_id}",
            sop_pack_name=str(platform_task.get("ruleName") or platform_task.get("sceneName") or "第三方SOP任务"),
            sop_category="platform_task",
            trigger_source="third_party_sop_pending",
            reply_messages=_platform_messages(platform_task),
            status="platform_received",
        )
        local_status = str(local_task.get("status") or "")
        dispatch_mode = "deprecated_ignored"
        if not self.settings.sop_platform_shadow_mode and recovery_status in {
            "platform_claiming",
            "platform_judging",
            "platform_processing",
            "platform_processing_retry",
        }:
            error = str(local_task.get("error") or recovery_status or "platform_processing_abandoned")
            stored_payload = local_task.get("send_payload") if isinstance(local_task.get("send_payload"), dict) else {}
            self.repository.update_sop_send_task(
                str(local_task.get("id") or ""),
                status="failed",
                send_payload=stored_payload,
                error=error,
            )
            completed = await self._commit_platform_terminal(
                task_id=task_id,
                event_id=event_id,
                terminal_status=70,
                event_status="platform_failed",
                remark=error,
            )
            return {
                "processed": True,
                "status": "failed",
                "task_id": task_id,
                "platform_response": completed,
                "error": error,
            }
        duplicate_reason = _duplicate_platform_task_reason(
            self.repository,
            local_task=local_task,
            task_id=task_id,
        )
        if duplicate_reason:
            decision = {
                "decision": "no_send",
                "reason": duplicate_reason,
                "reason_code": "no_send_duplicate",
                "sceneName": sop_platform_scene_name("no_send_duplicate"),
                "sceneCode": "no_send_duplicate",
                "knowledgeId": 0,
                "knowledgeParagraphNo": 0,
                "remark": "同一平台任务或同一触达内容已处理，消费但不重复发送",
                "reply_messages": [],
            }
            context = {
                "source": "duplicate_platform_task_content",
                "duplicate_of_task_id": str(local_task.get("duplicate_of_task_id") or ""),
            }
            if self.settings.sop_platform_shadow_mode:
                self.repository.update_sop_send_task(
                    str(local_task.get("id") or ""),
                    status="shadow_no_send",
                    send_payload={"decision": decision, "context": context},
                )
                self.repository.update_sop_event_status(event_id, status="shadow_no_send")
                self._counters[duplicate_reason] += 1
                return {"processed": True, "status": "shadow_no_send", "task_id": task_id, "decision": decision}
            started = time.perf_counter()
            claimed = await self.platform_client.consume(task_id=task_id, status=20)
            self._observe("claim", time.perf_counter() - started)
            _require_platform_status(claimed, 20)
            self.repository.update_sop_send_task(
                str(local_task.get("id") or ""),
                status="completed_without_send",
                send_payload={
                    "decision": decision,
                    "context": context,
                    "rule_data_response": await self._report_rule_data(
                        platform_task,
                        decision=decision,
                        sent=False,
                    ),
                },
            )
            completed = await self._commit_platform_terminal(
                task_id=task_id,
                event_id=event_id,
                terminal_status=70,
            )
            self._counters[duplicate_reason] += 1
            return {
                "processed": True,
                "status": "completed_without_send",
                "task_id": task_id,
                "decision": decision,
                "platform_response": completed,
            }
        if self.settings.sop_platform_shadow_mode and local_status in {"shadow_send", "shadow_no_send"}:
            return {"processed": False, "status": local_status, "task_id": task_id}

        if not self.settings.sop_platform_shadow_mode and recovery_status in {
            "platform_complete_pending",
            "platform_terminal_pending",
        }:
            started = time.perf_counter()
            terminal_status = self._stored_terminal_status(local_task)
            if recovery_status == "platform_complete_pending" and str(local_task.get("status") or "") == "platform_received":
                terminal_status = 30
            completed = await self._commit_platform_terminal(
                task_id=task_id,
                event_id=event_id,
                terminal_status=terminal_status,
                event_status=self._stored_terminal_event_status(local_task),
            )
            self._observe("claim", time.perf_counter() - started)
            return {
                "processed": True,
                "status": local_status or "completed",
                "task_id": task_id,
                "platform_response": completed,
            }

        preflight_reason = _task_preflight_no_send_reason(
            platform_task,
            identity=identity,
            settings=self.settings,
            dispatch_mode=dispatch_mode,
        )
        quiet_hours: dict[str, Any] = {}
        if not preflight_reason:
            quiet_hours = await self._quiet_hours_guard(platform_task, identity=identity)
            if quiet_hours.get("blocked"):
                preflight_reason = str(quiet_hours.get("reason") or "quiet_hours_blocked")
        if self.settings.sop_platform_shadow_mode and preflight_reason:
            decision = _preflight_no_send_decision(platform_task, reason=preflight_reason)
            self.repository.update_sop_send_task(
                str(local_task.get("id") or ""),
                status="shadow_no_send",
                send_payload={
                    "decision": decision,
                    "context": {
                        "source": "preflight",
                        "quiet_hours": quiet_hours,
                    },
                },
            )
            self.repository.update_sop_event_status(event_id, status="shadow_no_send")
            self._counters[preflight_reason] += 1
            return {"processed": True, "status": "shadow_no_send", "task_id": task_id, "decision": decision}

        processing_status = "platform_judging"
        self.repository.update_sop_event_status(event_id, status=processing_status)
        self.repository.update_sop_send_task(
            str(local_task.get("id") or ""),
            status="judging",
            send_payload={
                "platform_task_id": task_id,
                "phase": "loading_opening_state",
                "dispatch_mode": dispatch_mode,
            },
        )

        claimed = recovery_status in {
            "platform_processing",
            "platform_processing_retry",
            "platform_send_uncertain",
            "platform_complete_pending",
            "platform_terminal_pending",
        }
        if not self.settings.sop_platform_shadow_mode and not claimed:
            self.repository.update_sop_event_status(event_id, status="platform_claiming")
            started = time.perf_counter()
            claim_response = await self.platform_client.consume(task_id=task_id, status=20)
            self._observe("claim", time.perf_counter() - started)
            _require_platform_status(claim_response, 20)
            self.repository.update_sop_event_status(event_id, status="platform_processing")

        if not self.settings.sop_platform_shadow_mode and recovery_status == "platform_send_uncertain":
            stored_payload = local_task.get("send_payload") if isinstance(local_task.get("send_payload"), dict) else {}
            send_payload = stored_payload.get("request") if isinstance(stored_payload.get("request"), dict) else {}
            if not send_payload:
                raise RuntimeError("uncertain send recovery is missing the original idempotent request")
            self.repository.update_sop_send_task(
                str(local_task.get("id") or ""),
                status="sent",
                send_payload=stored_payload,
                send_response={
                    "code": 0,
                    "msg": "accepted_no_response_assumed_sent",
                    "data": {"send_status": "accepted_no_response", "assumed_sent": True},
                },
                sent_at=utc_now_iso(),
            )
            completed = await self._commit_platform_terminal(
                task_id=task_id,
                event_id=event_id,
                terminal_status=30,
            )
            return {
                "processed": True,
                "status": "sent",
                "task_id": task_id,
                "platform_response": completed,
            }

        takeover_status: dict[str, Any] = {}
        takeover_decision: dict[str, Any] | None = None
        if all(identity.get(key) for key in ("corp_id", "customer_id", "wechat")):
            try:
                takeover_status = await self.system_client.conversation_status(**identity)
            except Exception as exc:
                takeover_decision = {
                    "decision": "no_send",
                    "reason": "takeover_status_unavailable",
                    "reason_code": "no_send_downstream_rejected",
                    "sceneName": sop_platform_scene_name("no_send_downstream_rejected"),
                    "sceneCode": "no_send_downstream_rejected",
                    "knowledgeId": 0,
                    "knowledgeParagraphNo": 0,
                    "remark": f"会话接管状态查询失败，保守不发送：{type(exc).__name__}",
                    "reply_messages": [],
                }
                self._counters["takeover_status_unavailable"] += 1
            else:
                if _is_human_takeover_status(takeover_status):
                    takeover_decision = {
                        "decision": "no_send",
                        "reason": "human_takeover_active",
                        "reason_code": "human_takeover",
                        "sceneName": sop_platform_scene_name("no_send_human_takeover"),
                        "sceneCode": "no_send_human_takeover",
                        "knowledgeId": 0,
                        "knowledgeParagraphNo": 0,
                        "remark": "当前会话已由人工接管，平台任务消费但不发送",
                        "reply_messages": [],
                    }
                    self._counters["human_takeover_status"] += 1

        try:
            if takeover_decision:
                context = {
                    "source": "conversation_takeover_status",
                    "dispatch_mode": dispatch_mode,
                    "task_timing": _task_timing(platform_task),
                    "takeover_status": _compact_takeover_status(takeover_status),
                }
                decision = takeover_decision
            elif preflight_reason:
                context = {
                    "source": "preflight",
                    "dispatch_mode": dispatch_mode,
                    "task_timing": _task_timing(platform_task),
                    "quiet_hours": quiet_hours,
                }
                decision = _preflight_no_send_decision(platform_task, reason=preflight_reason)
                self._counters[preflight_reason] += 1
            else:
                started = time.perf_counter()
                context = await self._load_context(platform_task, identity=identity)
                self._observe("context", time.perf_counter() - started)
                context["dispatch_mode"] = dispatch_mode
                opening_state = (
                    context.get("opening_state")
                    if isinstance(context.get("opening_state"), dict)
                    else {}
                )
                delivery_guard = self._platform_contact_delivery_guard(
                    identity=identity,
                    task_id=task_id,
                    opening_state=opening_state,
                )
                context["platform_contact_delivery_guard"] = delivery_guard
                relation = (
                    context.get("customer_relation")
                    if isinstance(context.get("customer_relation"), dict)
                    else {}
                )
                if delivery_guard.get("blocked"):
                    guard_reason = str(delivery_guard.get("reason") or "platform_contact_send_limit")
                    guard_scene_code = (
                        "no_send_contact_cooldown"
                        if guard_reason == "platform_contact_send_cooldown"
                        else "no_send_contact_send_limit"
                    )
                    decision = {
                        "decision": "no_send",
                        "reason": guard_reason,
                        "reason_code": guard_reason,
                        "sceneName": sop_platform_scene_name(guard_scene_code),
                        "sceneCode": guard_scene_code,
                        "knowledgeId": 0,
                        "knowledgeParagraphNo": 0,
                        "remark": (
                            "同一客户与企微账号5分钟内已有第三方SOP成功发送，本任务消费但不发送。"
                            if guard_reason == "platform_contact_send_cooldown"
                            else "同一客户与企微账号在客户未回复期间已成功发送2条第三方SOP，本任务消费但不发送。"
                        ),
                        "reply_messages": [],
                    }
                    self._counters[guard_reason] += 1
                elif relation.get("is_deleted") is True or str(relation.get("status") or "").lower() == "deleted":
                    decision = await self._decide(platform_task, context=context)
                elif opening_state.get("has_real_customer_message") is not True:
                    original_messages = _platform_messages(platform_task)
                    message_error = _platform_message_error(platform_task)
                    if original_messages and not message_error:
                        decision = {
                            "decision": "send",
                            "reason": "unopened_or_conversation_unavailable_platform_passthrough",
                            "reason_code": "send",
                            "sceneName": sop_platform_scene_name("ai_service_unopened_passthrough"),
                            "sceneCode": "ai_service_unopened_passthrough",
                            "knowledgeId": 0,
                            "knowledgeParagraphNo": 0,
                            "remark": "客户未真实开口或会话状态不可得，按平台消息原类型、原内容、原顺序发送",
                            "reply_messages": original_messages,
                        }
                        if not context.get("source"):
                            context["source"] = "unopened_platform_passthrough"
                        context["routing_decision"] = "unopened_or_conversation_unavailable_platform_passthrough"
                        context["knowledge_loaded"] = False
                        context["model_called"] = False
                        context["first_day_platform_sop_route"] = {
                            "route_checked": True,
                            "opening_state": opening_state,
                            "route_reason": "unopened_or_conversation_unavailable_platform_passthrough",
                        }
                    else:
                        decision = {
                            "decision": "no_send",
                            "reason": message_error or "invalid_message_content",
                            "reason_code": "invalid_message_content",
                            "sceneName": sop_platform_scene_name("no_send_invalid_message_content"),
                            "sceneCode": "no_send_invalid_message_content",
                            "knowledgeId": 0,
                            "knowledgeParagraphNo": 0,
                            "remark": f"客户未开口但平台任务没有可原样发送的合法消息：{message_error or 'empty_messages'}",
                            "reply_messages": [],
                        }
                else:
                    started = time.perf_counter()
                    decision = await self._decide(platform_task, context=context)
                    self._observe("model", time.perf_counter() - started)
            if self.settings.sop_platform_shadow_mode:
                status = f"shadow_{decision['decision']}"
                self.repository.update_sop_send_task(
                    str(local_task.get("id") or ""),
                    status=status,
                    send_payload={"decision": decision, "context": _context_audit(context)},
                )
                self.repository.update_sop_event_status(event_id, status=status)
                return {"processed": True, "status": status, "task_id": task_id, "decision": decision}

            if decision["decision"] == "no_send":
                rule_data_response = await self._report_rule_data(
                    platform_task,
                    decision=decision,
                    sent=False,
                )
                self.repository.update_sop_send_task(
                    str(local_task.get("id") or ""),
                    status="completed_without_send",
                    send_payload={
                        "decision": decision,
                        "context": _context_audit(context),
                        "rule_data_response": rule_data_response,
                    },
                )
            else:
                send_payload = {
                    **identity,
                    "plan_id": f"platform-sop-{task_id}",
                    **_platform_send_trace_fields(platform_task),
                    "reply_messages": decision["reply_messages"],
                }
                media_delivery_audit = {
                    "original": _platform_media_refs(_platform_messages(platform_task)),
                    "final": _platform_media_refs(decision["reply_messages"]),
                }
                existing_delivery = await self._existing_platform_delivery(
                    identity=identity,
                    send_payload=send_payload,
                )
                if existing_delivery.get("found"):
                    duplicate_decision = {
                        "decision": "no_send",
                        "reason": "existing_platform_delivery",
                        "reason_code": "exact_duplicate",
                        "sceneName": sop_platform_scene_name("no_send_duplicate"),
                        "sceneCode": "no_send_duplicate",
                        "knowledgeId": 0,
                        "knowledgeParagraphNo": 0,
                        "remark": "本地检测到同批平台触达已发送，消费但不重复发送",
                        "reply_messages": [],
                    }
                    rule_data_response = await self._report_rule_data(
                        platform_task,
                        decision=duplicate_decision,
                        sent=False,
                    )
                    self.repository.update_sop_send_task(
                        str(local_task.get("id") or ""),
                        status="completed_without_send",
                        send_payload={
                            "decision": duplicate_decision,
                            "request": send_payload,
                            "context": {
                                **_context_audit(context),
                                "existing_delivery": existing_delivery,
                                "media_delivery": media_delivery_audit,
                            },
                            "rule_data_response": rule_data_response,
                        },
                    )
                    completed = await self._commit_platform_terminal(
                        task_id=task_id,
                        event_id=event_id,
                        terminal_status=70,
                    )
                    return {
                        "processed": True,
                        "status": "completed_without_send",
                        "task_id": task_id,
                        "decision": duplicate_decision,
                        "platform_response": completed,
                    }
                near_duplicate = await self._recent_near_duplicate_platform_delivery(
                    identity=identity,
                    task_id=task_id,
                    reply_messages=decision["reply_messages"],
                )
                duplicate_repair: dict[str, Any] = {}
                if near_duplicate.get("found"):
                    media_duplicate = near_duplicate.get("match_type") == "duplicate_media"
                    if media_duplicate:
                        duplicate_reason = "duplicate_media_delivery"
                    else:
                        duplicate_reason = "near_duplicate_platform_delivery"
                    duplicate_decision = {
                        "decision": "no_send",
                        "reason": duplicate_reason,
                        "reason_code": "exact_duplicate",
                        "sceneName": sop_platform_scene_name("no_send_duplicate"),
                        "sceneCode": "no_send_duplicate",
                        "knowledgeId": 0,
                        "knowledgeParagraphNo": 0,
                        "remark": "本地检测到同客户近期第三方SOP最终文案高度重复，消费但不重复发送。",
                        "reply_messages": [],
                    }
                    rule_data_response = await self._report_rule_data(
                        platform_task,
                        decision=duplicate_decision,
                        sent=False,
                    )
                    self.repository.update_sop_send_task(
                        str(local_task.get("id") or ""),
                        status="completed_without_send",
                        send_payload={
                            "decision": duplicate_decision,
                            "request": send_payload,
                            "context": {
                                **_context_audit(context),
                                "near_duplicate_delivery": near_duplicate,
                                "duplicate_media_repair": duplicate_repair,
                                "media_delivery": media_delivery_audit,
                            },
                            "rule_data_response": rule_data_response,
                        },
                    )
                    completed = await self._commit_platform_terminal(
                        task_id=task_id,
                        event_id=event_id,
                        terminal_status=70,
                    )
                    return {
                        "processed": True,
                        "status": "completed_without_send",
                        "task_id": task_id,
                        "decision": duplicate_decision,
                        "platform_response": completed,
                    }
                self.repository.update_sop_send_task(
                    str(local_task.get("id") or ""),
                    status="sending",
                    send_payload={
                        "decision": decision,
                        "request": send_payload,
                        "context": {
                            **_context_audit(context),
                            "media_delivery": media_delivery_audit,
                            "duplicate_media_repair": duplicate_repair,
                        },
                    },
                )
                started = time.perf_counter()
                try:
                    send_result = await self.system_client.send(**send_payload)
                except Exception as exc:
                    delivery_failure = _terminal_delivery_failure(exc)
                    if not delivery_failure:
                        raise
                    self._observe("send", time.perf_counter() - started)
                    business_no_send = _delivery_rejection_is_no_send(delivery_failure)
                    failed_decision = {
                        **decision,
                        "decision": "no_send",
                        "reason": "downstream_delivery_rejected",
                        "reason_code": "no_send_downstream_rejected",
                        "sceneName": sop_platform_scene_name("no_send_downstream_rejected"),
                        "sceneCode": "no_send_downstream_rejected",
                        "knowledgeId": 0,
                        "knowledgeParagraphNo": 0,
                        "remark": (
                            f"{str(decision.get('remark') or '').strip()} "
                            f"Delivery rejected by downstream system: HTTP {delivery_failure['http_status']}."
                        ).strip(),
                        "reply_messages": [],
                    }
                    rule_data_response = await self._report_rule_data(
                        platform_task,
                        decision=failed_decision,
                        sent=False,
                    )
                    self.repository.update_sop_send_task(
                        str(local_task.get("id") or ""),
                        status="completed_without_send" if business_no_send else "failed",
                        send_payload={
                            "decision": failed_decision,
                            "attempted_decision": decision,
                            "request": send_payload,
                            "context": {
                                **_context_audit(context),
                                "media_delivery": media_delivery_audit,
                                "duplicate_media_repair": duplicate_repair,
                            },
                            "delivery_failure": delivery_failure,
                            "rule_data_response": rule_data_response,
                        },
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    completed = await self._commit_platform_terminal(
                        task_id=task_id,
                        event_id=event_id,
                        terminal_status=70,
                        event_status="" if business_no_send else "platform_failed",
                        remark=str(failed_decision.get("remark") or ""),
                    )
                    self._counters["terminal_delivery_rejected"] += 1
                    return {
                        "processed": True,
                        "status": "completed_without_send" if business_no_send else "failed",
                        "task_id": task_id,
                        "decision": failed_decision,
                        "delivery_failure": delivery_failure,
                        "platform_response": completed,
                    }
                self._observe("send", time.perf_counter() - started)
                send_status = str((send_result.get("data") or {}).get("send_status") or send_result.get("msg") or "")
                if send_status == "accepted_no_response":
                    send_result = {
                        **send_result,
                        "msg": "accepted_no_response_assumed_sent",
                        "data": {
                            **(send_result.get("data") if isinstance(send_result.get("data"), dict) else {}),
                            "assumed_sent": True,
                        },
                    }
                rule_data_response = await self._report_rule_data(
                    platform_task,
                    decision=decision,
                    sent=True,
                )
                self.repository.update_sop_send_task(
                    str(local_task.get("id") or ""),
                    status="sent",
                    send_payload={
                        "decision": decision,
                        "request": send_payload,
                        "context": {
                            **_context_audit(context),
                            "media_delivery": media_delivery_audit,
                            "duplicate_media_repair": duplicate_repair,
                        },
                        "rule_data_response": rule_data_response,
                    },
                    send_response=send_result,
                    sent_at=utc_now_iso(),
                )
            terminal_status = 30 if decision["decision"] == "send" else 70
            processing_failure = decision["decision"] != "send" and _decision_is_processing_failure(decision)
            if processing_failure:
                current_task = self.repository.get_sop_send_task_by_idempotency_key(f"platform-sop:{task_id}")
                current_payload = (
                    current_task.get("send_payload")
                    if isinstance(current_task.get("send_payload"), dict)
                    else {}
                )
                self.repository.update_sop_send_task(
                    str(current_task.get("id") or local_task.get("id") or ""),
                    status="failed",
                    send_payload=current_payload,
                    error=str(decision.get("reason") or decision.get("reason_code") or "platform_task_failed"),
                )
            completed = await self._commit_platform_terminal(
                task_id=task_id,
                event_id=event_id,
                terminal_status=terminal_status,
                event_status="platform_failed" if processing_failure else "",
                remark=str(decision.get("remark") or decision.get("reason") or ""),
            )
            return {
                "processed": True,
                "status": (
                    "sent"
                    if decision["decision"] == "send"
                    else "failed"
                    if processing_failure
                    else "completed_without_send"
                ),
                "task_id": task_id,
                "platform_response": completed,
            }
        except Exception as exc:
            event_after_error = self.repository.get_sop_event(event_id)
            event_status = str(event_after_error.get("status") or "")
            if event_status not in {
                "platform_send_uncertain",
                "platform_complete_pending",
                "platform_terminal_pending",
            }:
                error = f"{type(exc).__name__}: {exc}"
                current_task = self.repository.get_sop_send_task_by_idempotency_key(f"platform-sop:{task_id}")
                current_payload = (
                    current_task.get("send_payload")
                    if isinstance(current_task.get("send_payload"), dict)
                    else {}
                )
                self.repository.update_sop_send_task(
                    str(current_task.get("id") or local_task.get("id") or ""),
                    status="failed",
                    send_payload={
                        **current_payload,
                        "platform_task_id": task_id,
                        "retry_cancelled": True,
                    },
                    error=error,
                )
                completed = await self._commit_platform_terminal(
                    task_id=task_id,
                    event_id=event_id,
                    terminal_status=70,
                    event_status="platform_failed",
                    remark=error,
                )
                return {
                    "processed": True,
                    "status": "failed",
                    "task_id": task_id,
                    "platform_response": completed,
                    "error": error,
                }
            raise

    async def _quiet_hours_guard(
        self,
        platform_task: dict[str, Any],
        *,
        identity: dict[str, str],
    ) -> dict[str, Any]:
        summary = _quiet_hours_base_summary(platform_task, settings=self.settings)
        if not summary.get("in_quiet_hours"):
            return summary

        task_type = _task_type(platform_task)
        summary["task_type"] = task_type
        if task_type != "add_wecom":
            return {
                **summary,
                "blocked": True,
                "reason": "quiet_hours_marketing_blocked",
            }

        cutoff_epoch = float(summary.get("reference_epoch") or 0.0)
        try:
            conversation = await self.system_client.conversation(**identity, limit=80)
        except Exception as exc:
            return {
                **summary,
                "blocked": True,
                "reason": "quiet_hours_unknown_activity",
                "activity_error": f"{type(exc).__name__}: {exc}",
            }

        data = conversation.get("data") if isinstance(conversation.get("data"), dict) else conversation
        relation = data.get("customer_relation") if isinstance(data.get("customer_relation"), dict) else {}
        if relation.get("is_deleted") is True or str(relation.get("status") or "").lower() == "deleted":
            return {
                **summary,
                "blocked": True,
                "reason": "customer_relation_deleted",
                "customer_relation": _compact_customer_relation(relation),
            }
        messages = data.get("messages") if isinstance(data.get("messages"), list) else []
        activity = _quiet_hours_activity(messages[-80:], before_epoch=cutoff_epoch)
        summary["activity"] = activity
        grace_minutes = max(
            0,
            int(getattr(self.settings, "sop_platform_quiet_first_add_grace_minutes", 30) or 0),
        )
        if not activity.get("activity_epoch"):
            return {
                **summary,
                "blocked": True,
                "reason": "quiet_hours_unknown_activity",
            }
        if activity.get("customer_pending_reply"):
            return {
                **summary,
                "blocked": True,
                "reason": "quiet_hours_customer_pending_reply",
            }
        inactivity_minutes = int(activity.get("inactivity_minutes") or 0)
        if inactivity_minutes >= grace_minutes:
            return {
                **summary,
                "blocked": True,
                "reason": "quiet_hours_first_add_inactive",
            }
        return {
            **summary,
            "blocked": False,
            "reason": "quiet_hours_recent_first_add_allowed",
        }

    async def _load_context(self, platform_task: dict[str, Any], *, identity: dict[str, str]) -> dict[str, Any]:
        missing = [key for key in ("corp_id", "customer_id", "external_userid", "user_id", "wechat") if not identity[key]]
        if missing:
            return {
                "source": "identity_incomplete",
                "conversation_loaded": False,
                "conversation_error": f"missing_identity:{','.join(missing)}",
                "customer_relation": {},
                "conversation_timeline": [],
                "conversation_count": 0,
                "opening_state": {
                    "has_real_customer_message": None,
                    "opening_state_reliable": False,
                },
                "business_state": {"source": "skipped_identity_incomplete"},
                "task_timing": _task_timing(platform_task),
            }
        try:
            conversation = await self.system_client.conversation(**identity, limit=80)
        except Exception as exc:
            return {
                "source": "conversation_unavailable",
                "conversation_loaded": False,
                "conversation_error": f"{type(exc).__name__}: {exc}",
                "customer_relation": {},
                "conversation_timeline": [],
                "conversation_count": 0,
                "opening_state": {
                    "has_real_customer_message": None,
                    "opening_state_reliable": False,
                },
                "business_state": {"source": "skipped_conversation_unavailable"},
                "task_timing": _task_timing(platform_task),
            }
        data = conversation.get("data") if isinstance(conversation.get("data"), dict) else conversation
        task_timing = _task_timing(
            platform_task,
            conversation_added_at=data.get("added_at"),
        )
        relation = _compact_customer_relation(
            data.get("customer_relation") if isinstance(data.get("customer_relation"), dict) else {}
        )
        messages = data.get("messages") if isinstance(data.get("messages"), list) else []
        timeline = _conversation_timeline(messages[-80:])
        opening_state = _conversation_opening_state(
            messages,
            conversation_added_at=data.get("added_at"),
        )
        opening_state["opening_state_reliable"] = True
        if relation.get("is_deleted") is True or str(relation.get("status") or "").lower() == "deleted":
            return {
                "conversation_loaded": True,
                "customer_relation": relation,
                "conversation_timeline": timeline,
                "conversation_count": len(messages),
                "opening_state": opening_state,
                "business_state": {"source": "skipped_customer_deleted"},
                "task_timing": task_timing,
            }
        if opening_state.get("has_real_customer_message") is not True:
            return {
                "conversation_loaded": True,
                "customer_relation": relation,
                "conversation_timeline": timeline,
                "conversation_count": len(messages),
                "opening_state": opening_state,
                "business_state": {"source": "skipped_for_unopened_passthrough"},
                "task_timing": task_timing,
            }
        request_context = {
            "source_protocol": "third_party_sop_pending",
            "corp_id": identity["corp_id"],
            "customer_id": identity["customer_id"],
            "external_userid": identity["external_userid"],
            "user_id": identity["user_id"],
            "wechat": identity["wechat"],
            "order_id": platform_task.get("orderId") or platform_task.get("order_id"),
            "order_no": platform_task.get("orderNo") or platform_task.get("order_no"),
        }
        try:
            customer_context = await asyncio.to_thread(
                self.customer_context_service.load,
                customer_id=identity["customer_id"],
                memory={},
                request_context=request_context,
            )
            business_state = _compact_business_state(customer_context)
        except Exception as exc:
            business_state = {
                "source": "customer_context_unavailable",
                "error": f"{type(exc).__name__}: {exc}",
            }
        return {
            "conversation_loaded": True,
            "customer_relation": relation,
            "conversation_timeline": timeline,
            "conversation_count": len(messages),
            "opening_state": opening_state,
            "business_state": business_state,
            "task_timing": task_timing,
        }

    async def _report_rule_data(
        self,
        platform_task: dict[str, Any],
        *,
        decision: dict[str, Any],
        sent: bool,
    ) -> dict[str, Any]:
        if not callable(getattr(self.platform_client, "service_rule_data", None)):
            return {"skipped": True, "reason": "platform_client_rule_data_api_unavailable"}
        task_id = _task_id(platform_task)
        if not task_id:
            return {"skipped": True, "reason": "missing_task_id"}
        scene_code = str(decision.get("sceneCode") or decision.get("scene_code") or "").strip()
        scene = sop_platform_scene(scene_code)
        if scene is None:
            logger.error("Unregistered platform SOP scene code for task %s: %s", task_id, scene_code)
            scene_code = "normal_platform_intent" if sent else "no_send_invalid_message_content"
            scene = sop_platform_scene(scene_code)
        scene_name = scene.name
        try:
            return await self.platform_client.service_rule_data(
                task_id=task_id,
                scene_name=scene_name,
                scene_code=scene_code,
                knowledge_id=_int(decision.get("knowledgeId", decision.get("knowledge_id")), 0) or None,
                knowledge_paragraph_no=_int(
                    decision.get("knowledgeParagraphNo", decision.get("knowledge_paragraph_no")),
                    0,
                )
                or None,
                remark=str(decision.get("remark") or decision.get("reason") or ""),
                send_content=_send_content_for_rule_data(decision.get("reply_messages") or []),
            )
        except Exception as exc:
            logger.warning(
                "Failed to report platform SOP rule data for task %s: %s: %s",
                task_id,
                type(exc).__name__,
                exc,
            )
            return {
                "error": "service_rule_data_failed",
                "exception_type": type(exc).__name__,
                "message": str(exc),
            }

    async def _decide(self, platform_task: dict[str, Any], *, context: dict[str, Any]) -> dict[str, Any]:
        relation = context.get("customer_relation") if isinstance(context.get("customer_relation"), dict) else {}
        if relation.get("is_deleted") is True or str(relation.get("status") or "").lower() == "deleted":
            return {
                "decision": "no_send",
                "reason": "customer_relation_deleted",
                "reason_code": "no_send_customer_deleted",
                "sceneName": sop_platform_scene_name("no_send_customer_deleted"),
                "sceneCode": "no_send_customer_deleted",
                "knowledgeId": 0,
                "knowledgeParagraphNo": 0,
                "remark": "客户关系已删除，停止触达",
                "reply_messages": [],
            }
        original_messages = _platform_messages(platform_task)
        recent_media_lookup = await self._recent_near_duplicate_platform_delivery(
            identity=_task_identity(platform_task),
            task_id=_task_id(platform_task),
            reply_messages=[],
        )
        recent_sent_media = (
            recent_media_lookup.get("sent_media")
            if isinstance(recent_media_lookup.get("sent_media"), list)
            else []
        )
        sent_fingerprints = {
            str(item.get("fingerprint") or "")
            for item in recent_sent_media
            if isinstance(item, dict)
        }
        model_input = {
            "task": {
                "task_id": _task_id(platform_task),
                "task_type": str(
                    platform_task.get("triggerEvent")
                    or platform_task.get("trigger_event")
                    or platform_task.get("eventType")
                    or platform_task.get("event_type")
                    or ""
                ),
                "scene": platform_task.get("scene") if isinstance(platform_task.get("scene"), dict) else {},
                "scene_role": "current_delivery_context",
                "original_message_content": original_messages,
                "required_delivery": original_messages,
                "original_message_content_role": "locked_platform_delivery_model_may_only_review_or_polish",
                "platform_metadata": {
                    "rule_id": platform_task.get("ruleId") or platform_task.get("rule_id"),
                    "rule_name": platform_task.get("ruleName") or platform_task.get("rule_name"),
                    "scene_id": platform_task.get("sceneId") or platform_task.get("scene_id"),
                },
                "timing": (
                    context.get("task_timing")
                    if isinstance(context.get("task_timing"), dict)
                    else _task_timing(platform_task)
                ),
            },
            "latest_context": {
                "customer_relation": context.get("customer_relation") or {},
                "conversation_timeline": context.get("conversation_timeline") or [],
                "timeline_structure": _timeline_structure(context.get("conversation_timeline") or []),
                "business_state": context.get("business_state") or {},
                "recent_sent_media": recent_sent_media,
                "forbidden_media_fingerprints": sorted(value for value in sent_fingerprints if value),
            },
            "authoritative_business_facts": sop_platform_business_facts_for_model(),
            "scene_catalog": sop_platform_model_scene_catalog(),
            "knowledge_scene_catalog": sop_platform_knowledge_scene_catalog(),
            "output_contract": {
                "decision": "send | no_send",
                "reason": "string",
                "sceneCode": "required; one code from scene_catalog",
                "sceneEvidence": "required; short evidence from supplied context",
                "knowledgeId": "0 or a matching id from knowledge_scene_catalog",
                "knowledgeParagraphNo": "0 with no knowledge hit; otherwise positive",
                "remark": "required callback remark",
                "reply_messages": "send must be non-empty; no_send must be []",
            },
        }
        messages = [
            {"role": "system", "content": SOP_PLATFORM_KNOWLEDGE_TASK_PROMPT},
            {"role": "user", "content": json.dumps(model_input, ensure_ascii=False)},
        ]
        deadline = time.monotonic() + max(5.0, float(self.settings.sop_platform_model_timeout_seconds))
        raw = await self.model_client.chat_json(
            messages,
            tier="balanced",
            temperature=0.0,
            deadline_monotonic=deadline,
            max_parallel_candidates=1,
        )
        raw = _normalize_knowledge_decision_callback_fields(raw)
        error = _decision_error(raw)
        if not error:
            error = _platform_send_contract_error(raw, original_messages=original_messages)
        policy_error = "" if error else _decision_policy_error(raw)
        if error:
            repair_messages = [
                *messages,
                {"role": "assistant", "content": json.dumps(raw, ensure_ascii=False)},
                {
                    "role": "user",
                    "content": (
                        f"输出不合法：{error}。只返回规定的 json；只能 send/no_send，不得延时或新增任务。"
                    ),
                },
            ]
            raw = await self.model_client.chat_json(
                repair_messages,
                tier="balanced",
                temperature=0.0,
                deadline_monotonic=deadline,
                max_parallel_candidates=1,
            )
            raw = _normalize_knowledge_decision_callback_fields(raw)
            error = _decision_error(raw)
            if not error:
                error = _platform_send_contract_error(raw, original_messages=original_messages)
            policy_error = "" if error else _decision_policy_error(raw)
        if error or policy_error:
            repair_messages = [
                *messages,
                {"role": "assistant", "content": json.dumps(raw, ensure_ascii=False)},
                {
                    "role": "user",
                    "content": (
                        f"第二次修复：输出违反第三方 SOP 发送审核合同：{error or policy_error}。"
                        "只返回合法 JSON。除严重客诉、明确停止联系、客户关系删除、健康高风险、"
                        "已付/已预约冲突、人工接管、平台内容冲突或重复外，必须改为 send，并以"
                        "task.required_delivery 为唯一内容来源做必要润色。普通沉默、普通考虑、"
                        "价格/效果/距离/时间顾虑都不能 no_send。"
                    ),
                },
            ]
            raw = await self.model_client.chat_json(
                repair_messages,
                tier="balanced",
                temperature=0.0,
                deadline_monotonic=deadline,
                max_parallel_candidates=1,
            )
            raw = _normalize_knowledge_decision_callback_fields(raw)
            error = _decision_error(raw)
            if not error:
                error = _platform_send_contract_error(raw, original_messages=original_messages)
            policy_error = "" if error else _decision_policy_error(raw)
        if error:
            raise RuntimeError(f"invalid_sop_platform_model_output: {error}")
        if policy_error:
            raise RuntimeError(f"invalid_sop_platform_model_output: {policy_error}")
        decision = str(raw.get("decision") or "")
        if decision == "no_send":
            return {
                "decision": decision,
                "reason": str(raw.get("reason") or ""),
                "reason_code": str(raw.get("sceneCode") or ""),
                "sceneName": sop_platform_scene_name(str(raw.get("sceneCode") or "")),
                "sceneCode": str(raw.get("sceneCode") or ""),
                "sceneEvidence": str(raw.get("sceneEvidence") or ""),
                "knowledgeId": _int(raw.get("knowledgeId"), 0),
                "knowledgeParagraphNo": _int(raw.get("knowledgeParagraphNo"), 0),
                "remark": str(raw.get("remark") or raw.get("reason") or ""),
                "reply_messages": [],
            }
        output_messages = raw.get("reply_messages") if isinstance(raw.get("reply_messages"), list) else []
        return {
            "decision": decision,
            "reason": str(raw.get("reason") or ""),
            "reason_code": str(raw.get("sceneCode") or ""),
            "sceneName": sop_platform_scene_name(str(raw.get("sceneCode") or "")),
            "sceneCode": str(raw.get("sceneCode") or ""),
            "sceneEvidence": str(raw.get("sceneEvidence") or ""),
            "knowledgeId": _int(raw.get("knowledgeId"), 0),
            "knowledgeParagraphNo": _int(raw.get("knowledgeParagraphNo"), 0),
            "remark": str(raw.get("remark") or raw.get("reason") or ""),
            "reply_messages": output_messages,
        }


def _legacy_decision_error(raw: Any) -> str:
    if not isinstance(raw, dict):
        return "output must be an object"
    unexpected = set(raw).difference(
        {
            "decision",
            "reason",
            "reason_code",
            "sceneName",
            "scene_name",
            "sceneCode",
            "scene_code",
            "knowledgeId",
            "knowledge_id",
            "knowledgeParagraphNo",
            "knowledge_paragraph_no",
            "remark",
            "reply_messages",
        }
    )
    if unexpected:
        return f"unexpected output fields: {','.join(sorted(unexpected))}"
    decision = str(raw.get("decision") or "").strip()
    if decision not in {"send", "no_send"}:
        return "decision must be send or no_send"
    if not str(raw.get("sceneName") or raw.get("scene_name") or "").strip():
        return "sceneName is required"
    if not str(raw.get("sceneCode") or raw.get("scene_code") or "").strip():
        return "sceneCode is required"
    if raw.get("knowledgeId", raw.get("knowledge_id", 0)) not in (None, ""):
        try:
            int(raw.get("knowledgeId", raw.get("knowledge_id", 0)) or 0)
        except (TypeError, ValueError):
            return "knowledgeId must be an integer"
    if raw.get("knowledgeParagraphNo", raw.get("knowledge_paragraph_no", 0)) not in (None, ""):
        try:
            int(raw.get("knowledgeParagraphNo", raw.get("knowledge_paragraph_no", 0)) or 0)
        except (TypeError, ValueError):
            return "knowledgeParagraphNo must be an integer"
    messages = raw.get("reply_messages")
    if not isinstance(messages, list):
        return "reply_messages must be a list"
    if decision == "no_send":
        code = str(raw.get("reason_code") or "").strip()
        if code not in SOP_PLATFORM_KNOWLEDGE_NO_SEND_REASON_CODES:
            return "no_send reason_code must be one allowed hard-boundary code"
        return "no_send reply_messages must be empty" if messages else ""
    if not messages:
        return "send reply_messages must not be empty"
    if len(messages) > 6:
        return "send reply_messages may contain at most six messages"
    for index, candidate in enumerate(messages, start=1):
        if not isinstance(candidate, dict):
            return f"reply message {index} must be an object"
        if candidate.get("order") != index:
            return f"reply message {index} order must be {index}"
        message_type = str(candidate.get("type") or "").strip()
        if message_type not in {"text", "image", "video", "payment_collection"}:
            return f"reply message {index} type must be text, image, video, or payment_collection"
        content = candidate.get("content") if isinstance(candidate.get("content"), dict) else {}
        if message_type == "text":
            if not str(content.get("text") or "").strip():
                return f"reply message {index} text is empty"
        elif message_type in {"image", "video"} and not str(content.get("url") or "").strip():
            return f"reply message {index} media url is empty"
        elif message_type == "payment_collection":
            try:
                amount = int(content.get("amount") or 0)
            except (TypeError, ValueError):
                return f"reply message {index} payment amount must be an integer"
            if amount not in PAYMENT_COLLECTION_ALLOWED_AMOUNTS:
                return f"reply message {index} payment amount must be 10, 20, 30, or 40"
    return ""


def _legacy_normalize_knowledge_decision_callback_fields(raw: Any) -> Any:
    if not isinstance(raw, dict):
        return raw
    output = dict(raw)
    decision = str(output.get("decision") or "").strip()
    if "scene_name" in output and "sceneName" not in output:
        output["sceneName"] = output.pop("scene_name")
    if "scene_code" in output and "sceneCode" not in output:
        output["sceneCode"] = output.pop("scene_code")
    if "knowledge_id" in output and "knowledgeId" not in output:
        output["knowledgeId"] = output.pop("knowledge_id")
    if "knowledge_paragraph_no" in output and "knowledgeParagraphNo" not in output:
        output["knowledgeParagraphNo"] = output.pop("knowledge_paragraph_no")
    if not str(output.get("sceneName") or "").strip():
        output["sceneName"] = "正常推进｜活动价格" if decision == "send" else "不发送｜硬边界"
    if not str(output.get("sceneCode") or "").strip():
        output["sceneCode"] = "normal_activity_price" if decision == "send" else "no_send_hard_boundary"
    if output.get("knowledgeId") in (None, ""):
        output["knowledgeId"] = 0
    if output.get("knowledgeParagraphNo") in (None, ""):
        output["knowledgeParagraphNo"] = 0
    if not str(output.get("remark") or "").strip():
        output["remark"] = str(output.get("reason") or "")
    if decision == "send" and not str(output.get("reason_code") or "").strip():
        output["reason_code"] = str(output.get("sceneCode") or "send")
    return output


def _decision_policy_error(raw: dict[str, Any]) -> str:
    return ""


def _normalize_knowledge_decision_callback_fields(raw: Any) -> Any:
    if not isinstance(raw, dict):
        return raw
    output = dict(raw)
    if "scene_code" in output and "sceneCode" not in output:
        output["sceneCode"] = output.pop("scene_code")
    if "scene_evidence" in output and "sceneEvidence" not in output:
        output["sceneEvidence"] = output.pop("scene_evidence")
    if "knowledge_id" in output and "knowledgeId" not in output:
        output["knowledgeId"] = output.pop("knowledge_id")
    if "knowledge_paragraph_no" in output and "knowledgeParagraphNo" not in output:
        output["knowledgeParagraphNo"] = output.pop("knowledge_paragraph_no")
    output.pop("reason_code", None)
    if output.get("knowledgeId") in (None, ""):
        output["knowledgeId"] = 0
    if output.get("knowledgeParagraphNo") in (None, ""):
        output["knowledgeParagraphNo"] = 0
    if not str(output.get("remark") or "").strip():
        output["remark"] = str(output.get("reason") or "")
    return output


def _decision_error(raw: Any) -> str:
    if not isinstance(raw, dict):
        return "output must be an object"
    allowed_fields = {
        "decision",
        "reason",
        "sceneCode",
        "sceneEvidence",
        "knowledgeId",
        "knowledgeParagraphNo",
        "remark",
        "reply_messages",
    }
    unexpected = set(raw).difference(allowed_fields)
    if unexpected:
        return f"unexpected output fields: {','.join(sorted(unexpected))}"
    decision = str(raw.get("decision") or "").strip()
    if decision not in {"send", "no_send"}:
        return "decision must be send or no_send"
    scene_code = str(raw.get("sceneCode") or "").strip()
    scene = sop_platform_scene(scene_code)
    if scene is None or scene_code not in SOP_PLATFORM_MODEL_SCENE_CODES:
        return "sceneCode must be one model-selectable code from scene_catalog"
    if scene.decision != decision:
        return f"sceneCode {scene_code} requires decision={scene.decision}"
    if not str(raw.get("sceneEvidence") or "").strip():
        return "sceneEvidence is required"
    try:
        knowledge_id = int(raw.get("knowledgeId") or 0)
        paragraph_no = int(raw.get("knowledgeParagraphNo") or 0)
    except (TypeError, ValueError):
        return "knowledgeId and knowledgeParagraphNo must be integers"
    if knowledge_id == 0:
        if paragraph_no != 0:
            return "knowledgeParagraphNo must be 0 when knowledgeId is 0"
    else:
        mapped_scene_code = SOP_PLATFORM_KNOWLEDGE_SCENE_CODES.get(knowledge_id)
        if mapped_scene_code != scene_code:
            return "knowledgeId does not match sceneCode"
        if paragraph_no <= 0:
            return "knowledgeParagraphNo must be positive when knowledgeId is set"
    messages = raw.get("reply_messages")
    if not isinstance(messages, list):
        return "reply_messages must be a list"
    if decision == "no_send":
        return "no_send reply_messages must be empty" if messages else ""
    if not messages:
        return "send reply_messages must not be empty"
    if len(messages) > 6:
        return "send reply_messages may contain at most six messages"
    for index, candidate in enumerate(messages, start=1):
        if not isinstance(candidate, dict):
            return f"reply message {index} must be an object"
        if candidate.get("order") != index:
            return f"reply message {index} order must be {index}"
        message_type = str(candidate.get("type") or "").strip()
        if message_type not in {"text", "image", "video", "payment_collection"}:
            return f"reply message {index} has an unsupported type"
        content = candidate.get("content") if isinstance(candidate.get("content"), dict) else {}
        if message_type == "text" and not str(content.get("text") or "").strip():
            return f"reply message {index} text is empty"
        if message_type in {"image", "video"} and not str(content.get("url") or "").strip():
            return f"reply message {index} media url is empty"
        if message_type == "payment_collection":
            try:
                amount = int(content.get("amount") or 0)
            except (TypeError, ValueError):
                return f"reply message {index} payment amount must be an integer"
            if amount not in PAYMENT_COLLECTION_ALLOWED_AMOUNTS:
                return f"reply message {index} payment amount must be 10, 20, 30, or 40"
    return ""


def _platform_send_contract_error(
    raw: dict[str, Any],
    *,
    original_messages: list[dict[str, Any]],
) -> str:
    if str(raw.get("decision") or "").strip() != "send":
        return ""
    output_messages = raw.get("reply_messages") if isinstance(raw.get("reply_messages"), list) else []

    def locked_messages(messages: list[dict[str, Any]]) -> list[tuple[str, str]]:
        locked: list[tuple[str, str]] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            message_type = str(message.get("type") or "").strip().lower()
            if message_type == "text":
                continue
            content = message.get("content") if isinstance(message.get("content"), dict) else {}
            locked.append(
                (
                    message_type,
                    json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                )
            )
        return locked

    expected = locked_messages(original_messages)
    actual = locked_messages(output_messages)
    if actual != expected:
        return "send must preserve all platform image/video/link/payment messages in original order and content"
    return ""


def _platform_messages(platform_task: dict[str, Any]) -> list[dict[str, Any]]:
    raw = platform_task.get("message_content")
    if not isinstance(raw, list):
        raw = platform_task.get("messageContent")
    if not isinstance(raw, list):
        return []
    output: list[dict[str, Any]] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            continue
        message_type = str(item.get("type") or "").strip().lower()
        content = item.get("content")
        if message_type == "text":
            text = str(content.get("text") if isinstance(content, dict) else content or "").strip()
            if text:
                if text == "预约卡片":
                    output.append(
                        {
                            "type": "payment_collection",
                            "order": index,
                            "content": {"amount": PAYMENT_COLLECTION_UNIT_AMOUNT, "remark": ""},
                        }
                    )
                else:
                    output.append({"type": "text", "order": index, "content": {"text": text}})
        elif message_type in {"image", "video"}:
            url = str(content.get("url") if isinstance(content, dict) else content or "").strip()
            if url:
                output.append({"type": message_type, "order": index, "content": {"url": url}})
        elif message_type == "link":
            if isinstance(content, dict):
                normalized_content = dict(content)
            else:
                normalized_content = {"url": str(content or "").strip()}
            if str(normalized_content.get("url") or "").strip():
                output.append({"type": "link", "order": index, "content": normalized_content})
    return output


def _platform_duplicate_send_once_key(platform_task: dict[str, Any]) -> str:
    messages = _platform_messages(platform_task)
    if not messages:
        return ""
    identity = _task_identity(platform_task)
    if not identity["corp_id"] or not identity["wechat"] or not (
        identity["external_userid"] or identity["customer_id"]
    ):
        return ""
    scheduled_epoch = _task_scheduled_epoch(platform_task) or time.time()
    scheduled_day = datetime.fromtimestamp(scheduled_epoch, tz=_BEIJING_TZ).strftime("%Y%m%d")
    canonical_messages = json.dumps(messages, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    content_hash = hashlib.sha256(canonical_messages.encode("utf-8")).hexdigest()[:24]
    contact = "|".join(
        [
            identity["corp_id"].lower(),
            identity["wechat"].lower(),
            (identity["external_userid"] or identity["customer_id"]).lower(),
        ]
    )
    task_type = _task_type(platform_task) or "unknown"
    return f"platform_sop_content:{contact}:{scheduled_day}:{task_type}:{content_hash}"


def _platform_contact_lock_key(platform_task: dict[str, Any]) -> str:
    identity = _task_identity(platform_task)
    contact_id = identity.get("external_userid") or identity.get("customer_id")
    if not identity.get("corp_id") or not identity.get("wechat") or not contact_id:
        return ""
    canonical = "|".join(
        [identity["corp_id"].lower(), identity["wechat"].lower(), contact_id.lower()]
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


_VOLATILE_MEDIA_QUERY_KEYS = {
    "authorization",
    "expires",
    "ossaccesskeyid",
    "signature",
    "token",
    "x-expires",
    "x-signature",
}


def _canonical_platform_media_url(url: str) -> str:
    value = unquote(str(url or "").strip())
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        return value
    hostname = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError:
        return value
    netloc = hostname
    if port and not (
        (parsed.scheme.lower() == "http" and port == 80)
        or (parsed.scheme.lower() == "https" and port == 443)
    ):
        netloc = f"{hostname}:{port}"
    query = []
    for key, query_value in parse_qsl(parsed.query, keep_blank_values=True):
        lowered = key.lower()
        if (
            lowered in _VOLATILE_MEDIA_QUERY_KEYS
            or lowered.startswith("x-amz-")
            or lowered.startswith("x-oss-")
        ):
            continue
        query.append((key, query_value))
    return urlunparse(
        (
            parsed.scheme.lower(),
            netloc,
            re.sub(r"/{2,}", "/", unquote(parsed.path or "/")),
            "",
            urlencode(sorted(query)),
            "",
        )
    )


def _platform_media_refs(messages: list[Any]) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        message_type = str(message.get("type") or "").strip().lower()
        if message_type not in {"image", "video"}:
            continue
        content = message.get("content") if isinstance(message.get("content"), dict) else {}
        asset_id = ""
        for key in ("asset_id", "assetId", "media_id", "mediaId", "file_id", "fileId", "id"):
            asset_id = str(content.get(key) or message.get(key) or "").strip()
            if asset_id:
                break
        url = str(content.get("url") or content.get("content") or "").strip()
        canonical_url = _canonical_platform_media_url(url) if url else ""
        if asset_id:
            fingerprint_source = f"{message_type}:asset:{asset_id}"
        elif canonical_url:
            fingerprint_source = f"{message_type}:url:{canonical_url}"
        else:
            continue
        refs.append(
            {
                "type": message_type,
                "asset_id": asset_id,
                "canonical_url": canonical_url,
                "fingerprint": hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest(),
            }
        )
    return refs


def _duplicate_platform_task_reason(
    repository: Any,
    *,
    local_task: dict[str, Any],
    task_id: str,
) -> str:
    if str(local_task.get("dedupe_reason") or "") == "send_once_key":
        return "duplicate_platform_task_content"
    send_once_key = str(local_task.get("send_once_key") or "").strip()
    if not send_once_key or not hasattr(repository, "find_sop_send_task_delivery_duplicate"):
        return ""
    duplicate = repository.find_sop_send_task_delivery_duplicate(
        send_once_key,
        exclude_idempotency_key=f"platform-sop:{task_id}",
    )
    if duplicate:
        local_task["duplicate_of_task_id"] = str(duplicate.get("id") or "")
        return "duplicate_platform_task_content"
    return ""


def _platform_delivery_match(
    messages: list[Any],
    *,
    plan_id: str,
    task_id: str,
    reply_messages: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized_plan = plan_id.strip().lower()
    normalized_task = task_id.strip().lower()
    assistant_messages = [
        item for item in messages if isinstance(item, dict) and _raw_message_role(item) == "assistant"
    ]
    for item in assistant_messages:
        msgid = str(item.get("msgid") or item.get("system_msgid") or "").strip().lower()
        if normalized_plan and normalized_task and normalized_plan in msgid and normalized_task in msgid:
            return {
                "found": True,
                "match_type": "platform_task_msgid",
                "msgid": str(item.get("msgid") or item.get("system_msgid") or ""),
            }

    expected_texts: list[str] = []
    expected_images: list[str] = []
    for message in reply_messages:
        if not isinstance(message, dict):
            continue
        message_type = str(message.get("type") or "").strip().lower()
        content = message.get("content") if isinstance(message.get("content"), dict) else {}
        if message_type == "text":
            text = str(content.get("text") or content.get("content") or "").strip()
            if text:
                expected_texts.append(text)
        elif message_type == "image":
            url = str(content.get("url") or content.get("content") or "").strip()
            if url:
                expected_images.append(url)

    if not expected_texts and not expected_images:
        return {"found": False, "match_type": "none"}

    actual_texts = {_timeline_message_content(item.get("content")).strip() for item in assistant_messages}
    actual_images = {
        _timeline_message_content(item.get("content")).strip()
        for item in assistant_messages
        if str(item.get("msgtype") or item.get("message_type") or item.get("type") or "").strip().lower() == "image"
    }
    text_match = all(text in actual_texts for text in expected_texts)
    image_match = all((url in actual_images or url in actual_texts) for url in expected_images)
    if text_match and image_match:
        return {
            "found": True,
            "match_type": "platform_task_content",
            "text_count": len(expected_texts),
            "image_count": len(expected_images),
        }
    return {
        "found": False,
        "match_type": "none",
        "expected_text_count": len(expected_texts),
        "expected_image_count": len(expected_images),
    }


def _platform_near_duplicate_delivery_match(
    records: list[dict[str, Any]],
    *,
    identity: dict[str, str],
    current_task_id: str,
    reply_messages: list[dict[str, Any]],
) -> dict[str, Any]:
    current_text = _platform_delivery_text(reply_messages)
    current_normalized = _normalize_platform_delivery_text(current_text)
    current_media = _platform_media_refs(reply_messages)
    current_fingerprints = {item["fingerprint"] for item in current_media}
    sent_media: list[dict[str, Any]] = []
    media_match: dict[str, Any] = {}
    text_match: dict[str, Any] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        if not _same_platform_contact(record, identity):
            continue
        if _platform_record_task_id(record) == str(current_task_id).strip():
            continue
        status = str(record.get("task_status") or "").strip()
        if status not in {"sent", "sending"} and not str(record.get("sent_at") or "").strip():
            continue
        sent_messages = _platform_record_sent_messages(record)
        record_media = _platform_media_refs(sent_messages)
        for media in record_media:
            sent_media.append(
                {
                    **media,
                    "task_id": _platform_record_task_id(record),
                    "event_id": str(record.get("event_id") or ""),
                    "status": status,
                    "sent_at": str(record.get("sent_at") or ""),
                }
            )
        duplicate_media = [item for item in record_media if item["fingerprint"] in current_fingerprints]
        if duplicate_media and not media_match:
            media_match = {
                "found": True,
                "match_type": "duplicate_media",
                "duplicate_media": duplicate_media,
                "current_media": current_media,
                "duplicate_event_id": str(record.get("event_id") or ""),
                "duplicate_task_id": _platform_record_task_id(record),
                "duplicate_status": status,
                "duplicate_sent_at": str(record.get("sent_at") or ""),
            }
        if len(current_normalized) < 40:
            continue
        sent_text = _platform_delivery_text(sent_messages)
        sent_normalized = _normalize_platform_delivery_text(sent_text)
        if len(sent_normalized) < 40:
            continue
        ratio = SequenceMatcher(None, current_normalized, sent_normalized).ratio()
        paragraph_match = _platform_paragraph_duplicate_match(current_text, sent_text)
        if (ratio >= 0.92 or paragraph_match.get("matched")) and not text_match:
            text_match = {
                "found": True,
                "match_type": "near_duplicate_text",
                "ratio": round(ratio, 4),
                "paragraph_match": paragraph_match,
                "duplicate_event_id": str(record.get("event_id") or ""),
                "duplicate_task_id": _platform_record_task_id(record),
                "duplicate_status": status,
                "duplicate_sent_at": str(record.get("sent_at") or ""),
            }
    if media_match:
        return {**media_match, "sent_media": sent_media}
    if text_match:
        return {**text_match, "current_media": current_media, "sent_media": sent_media}
    return {
        "found": False,
        "match_type": "text_too_short" if len(current_normalized) < 40 else "none",
        "current_media": current_media,
        "sent_media": sent_media,
    }


def _same_platform_contact(record: dict[str, Any], identity: dict[str, str]) -> bool:
    if str(record.get("corp_id") or "").strip().lower() != str(identity.get("corp_id") or "").strip().lower():
        return False
    if str(record.get("wechat") or "").strip().lower() != str(identity.get("wechat") or "").strip().lower():
        return False
    record_external = str(record.get("external_userid") or "").strip().lower()
    current_external = str(identity.get("external_userid") or "").strip().lower()
    if record_external and current_external:
        return record_external == current_external
    return str(record.get("customer_id") or "").strip() == str(identity.get("customer_id") or "").strip()


def _platform_contact_delivery_guard(
    records: list[dict[str, Any]],
    *,
    identity: dict[str, str],
    current_task_id: str,
    latest_customer_at: Any,
    now_epoch: float | None = None,
) -> dict[str, Any]:
    latest_customer_epoch = _parse_epoch(latest_customer_at)
    all_successful_sends: list[dict[str, Any]] = []
    successful_sends: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict) or not _same_platform_contact(record, identity):
            continue
        if _platform_record_task_id(record) == str(current_task_id).strip():
            continue
        if str(record.get("task_status") or "").strip() != "sent":
            continue
        sent_epoch = _parse_epoch(record.get("sent_at"))
        if not sent_epoch:
            continue
        sent_record = {
            "task_id": _platform_record_task_id(record),
            "sent_at": str(record.get("sent_at") or ""),
            "sent_epoch": sent_epoch,
        }
        all_successful_sends.append(sent_record)
        if not latest_customer_epoch or sent_epoch > latest_customer_epoch:
            successful_sends.append(sent_record)

    all_successful_sends.sort(key=lambda item: float(item["sent_epoch"]))
    successful_sends.sort(key=lambda item: float(item["sent_epoch"]))
    latest_send = all_successful_sends[-1] if all_successful_sends else {}
    reference_epoch = now_epoch if now_epoch is not None else time.time()
    seconds_since_latest_send = (
        max(0.0, reference_epoch - float(latest_send["sent_epoch"]))
        if latest_send
        else None
    )
    reason = ""
    if seconds_since_latest_send is not None and seconds_since_latest_send < 300:
        reason = "platform_contact_send_cooldown"
    elif len(successful_sends) >= 2:
        reason = "platform_contact_send_limit"
    return {
        "blocked": bool(reason),
        "reason": reason or "allowed",
        "successful_send_count_since_last_customer_reply": len(successful_sends),
        "latest_customer_at": str(latest_customer_at or ""),
        "latest_successful_send_at": str(latest_send.get("sent_at") or ""),
        "seconds_since_latest_successful_send": (
            round(seconds_since_latest_send, 3)
            if seconds_since_latest_send is not None
            else None
        ),
        "successful_task_ids": [item["task_id"] for item in successful_sends[-2:]],
        "max_sends_without_reply": 2,
        "cooldown_seconds": 300,
    }


def _platform_record_task_id(record: dict[str, Any]) -> str:
    platform_task = record.get("platform_task") if isinstance(record.get("platform_task"), dict) else {}
    task_id = str(platform_task.get("task_id") or platform_task.get("taskId") or "").strip()
    if task_id:
        return task_id
    event_id = str(record.get("event_id") or "").strip()
    if event_id.startswith("platform_sop_task:"):
        return event_id.split(":", 1)[1]
    return ""


def _platform_record_sent_messages(record: dict[str, Any]) -> list[dict[str, Any]]:
    send_payload = record.get("send_payload") if isinstance(record.get("send_payload"), dict) else {}
    request_payload = send_payload.get("request") if isinstance(send_payload.get("request"), dict) else {}
    request_messages = request_payload.get("reply_messages")
    if isinstance(request_messages, list) and request_messages:
        return [item for item in request_messages if isinstance(item, dict)]
    decision_payload = send_payload.get("decision") if isinstance(send_payload.get("decision"), dict) else {}
    decision_messages = decision_payload.get("reply_messages")
    if isinstance(decision_messages, list) and decision_messages:
        return [item for item in decision_messages if isinstance(item, dict)]
    stored_messages = record.get("reply_messages")
    if isinstance(stored_messages, list):
        return [item for item in stored_messages if isinstance(item, dict)]
    return []


def _platform_delivery_text(messages: list[Any]) -> str:
    parts: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        if str(message.get("type") or "").strip().lower() != "text":
            continue
        content = message.get("content") if isinstance(message.get("content"), dict) else {}
        text = str(content.get("text") or content.get("content") or "").strip()
        if text:
            parts.append(text)
    return "\n".join(parts)


def _normalize_platform_delivery_text(text: str) -> str:
    normalized = str(text or "").lower()
    normalized = re.sub(r"https?://\S+", "", normalized)
    return re.sub(r"[\s\W_]+", "", normalized, flags=re.UNICODE)


def _platform_paragraph_duplicate_match(current_text: str, sent_text: str) -> dict[str, Any]:
    current_parts = _platform_duplicate_paragraphs(current_text)
    sent_parts = _platform_duplicate_paragraphs(sent_text)
    matches: list[dict[str, Any]] = []
    for current in current_parts:
        current_normalized = _normalize_platform_delivery_text(current)
        for sent in sent_parts:
            sent_normalized = _normalize_platform_delivery_text(sent)
            ratio = SequenceMatcher(None, current_normalized, sent_normalized).ratio()
            if ratio >= 0.88:
                matches.append(
                    {
                        "ratio": round(ratio, 4),
                        "current_length": len(current_normalized),
                        "sent_length": len(sent_normalized),
                    }
                )
                break
    if len(matches) >= 2:
        return {"matched": True, "match_type": "multiple_paragraphs", "matches": matches[:5]}
    if matches and max(match["current_length"] for match in matches) >= 80:
        return {"matched": True, "match_type": "long_paragraph", "matches": matches[:5]}
    return {"matched": False, "matches": matches[:5]}


def _platform_duplicate_paragraphs(text: str) -> list[str]:
    chunks = re.split(r"(?:\r?\n)+|[。！？!?；;]", str(text or ""))
    paragraphs: list[str] = []
    for chunk in chunks:
        value = chunk.strip()
        if len(_normalize_platform_delivery_text(value)) >= 18:
            paragraphs.append(value)
    return paragraphs


def _manual_resend_messages(local_task: dict[str, Any], platform_task: dict[str, Any]) -> list[dict[str, Any]]:
    send_payload = local_task.get("send_payload") if isinstance(local_task.get("send_payload"), dict) else {}
    request_payload = send_payload.get("request") if isinstance(send_payload.get("request"), dict) else {}
    request_messages = request_payload.get("reply_messages")
    if isinstance(request_messages, list) and request_messages:
        return request_messages
    decision_payload = send_payload.get("decision") if isinstance(send_payload.get("decision"), dict) else {}
    decision_messages = decision_payload.get("reply_messages")
    if isinstance(decision_messages, list) and decision_messages:
        return decision_messages
    if _dispatch_mode(platform_task) == "ai_service":
        return []
    stored_messages = local_task.get("reply_messages")
    if isinstance(stored_messages, list) and stored_messages:
        return stored_messages
    return _platform_messages(platform_task)


def _task_preflight_no_send_reason(
    platform_task: dict[str, Any],
    *,
    identity: dict[str, str],
    settings: Any,
    dispatch_mode: str | None = None,
) -> str:
    del settings, dispatch_mode
    missing = [key for key in ("corp_id", "customer_id", "external_userid", "user_id", "wechat") if not identity.get(key)]
    if missing:
        return f"invalid_identity:{','.join(missing)}"
    return "invalid_message_content" if _platform_message_error(platform_task) or not _platform_messages(platform_task) else ""


def _preflight_no_send_decision(platform_task: dict[str, Any], *, reason: str) -> dict[str, Any]:
    quiet_first_add = reason.startswith("quiet_hours_") and _task_type(platform_task) == "add_wecom"
    scene_code = "quiet_first_add_backlog" if quiet_first_add else "no_send_invalid_message_content"
    return {
        "decision": "no_send",
        "reason": reason,
        "reason_code": reason,
        "sceneName": sop_platform_scene_name(scene_code),
        "sceneCode": scene_code,
        "knowledgeId": 0,
        "knowledgeParagraphNo": 0,
        "remark": (
            "首加SOP在夜间被拦截，当前任务已消费，原始内容进入次日08:30融合补触达"
            if quiet_first_add
            else f"前置保护命中：{reason}"
        ),
        "reply_messages": [],
    }


def _manual_resend_preflight_block_reason(
    platform_task: dict[str, Any],
    *,
    identity: dict[str, str],
    settings: Any,
) -> str:
    reason = _task_preflight_no_send_reason(
        platform_task,
        identity=identity,
        settings=settings,
    )
    if reason in {"", "stale_task", "pre_cutover_task"}:
        return ""
    return reason


def _quiet_hours_base_summary(platform_task: dict[str, Any], *, settings: Any) -> dict[str, Any]:
    enabled = bool(getattr(settings, "sop_platform_quiet_hours_enabled", True))
    start_hour = _bounded_hour(
        getattr(settings, "sop_platform_quiet_start_hour", 0),
        default=0,
    )
    end_hour = _bounded_hour(
        getattr(settings, "sop_platform_quiet_end_hour", 8),
        default=8,
    )
    scheduled_epoch = _task_scheduled_epoch(platform_task)
    reference_epoch = scheduled_epoch or time.time()
    local_time = datetime.fromtimestamp(reference_epoch, tz=_BEIJING_TZ)
    in_quiet_hours = bool(enabled and _hour_in_window(local_time.hour, start_hour=start_hour, end_hour=end_hour))
    return {
        "enabled": enabled,
        "timezone": "Asia/Shanghai",
        "window": f"{start_hour:02d}:00-{end_hour:02d}:00",
        "scheduled_at": platform_task.get("scheduledAt") or platform_task.get("scheduled_at") or "",
        "reference_source": "scheduled_at" if scheduled_epoch else "processing_time",
        "reference_epoch": reference_epoch,
        "reference_at_beijing": local_time.strftime("%Y-%m-%d %H:%M:%S"),
        "in_quiet_hours": in_quiet_hours,
        "blocked": False,
        "reason": "",
    }


def _quiet_hours_activity(messages: list[Any], *, before_epoch: float) -> dict[str, Any]:
    real_customer_times: list[float] = []
    auto_opening_times: list[float] = []
    assistant_times: list[float] = []
    unknown_time_count = 0
    for item in messages:
        if not isinstance(item, dict):
            continue
        role = _raw_message_role(item)
        if role not in {"customer", "assistant"}:
            continue
        message_epoch = _raw_message_epoch(item)
        if not message_epoch:
            unknown_time_count += 1
            continue
        if message_epoch > before_epoch:
            continue
        if role == "assistant":
            assistant_times.append(message_epoch)
            continue
        content = _timeline_message_content(item.get("content"))
        if is_platform_auto_opening_message(content):
            auto_opening_times.append(message_epoch)
        else:
            real_customer_times.append(message_epoch)

    latest_customer = max(real_customer_times, default=0.0)
    latest_auto_opening = max(auto_opening_times, default=0.0)
    activity_epoch = latest_customer or latest_auto_opening
    assistant_after_customer = bool(
        latest_customer and any(latest_customer < value <= before_epoch for value in assistant_times)
    )
    inactivity_minutes = (
        max(0, int((before_epoch - activity_epoch) // 60))
        if activity_epoch
        else None
    )

    def format_time(value: float) -> str:
        if not value:
            return ""
        return datetime.fromtimestamp(value, tz=_BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")

    return {
        "source": "customer_message" if latest_customer else "platform_auto_opening" if latest_auto_opening else "",
        "activity_epoch": activity_epoch,
        "activity_at_beijing": format_time(activity_epoch),
        "latest_customer_at_beijing": format_time(latest_customer),
        "latest_auto_opening_at_beijing": format_time(latest_auto_opening),
        "assistant_after_latest_customer": assistant_after_customer,
        "customer_pending_reply": bool(latest_customer and not assistant_after_customer),
        "inactivity_minutes": inactivity_minutes,
        "unknown_time_message_count": unknown_time_count,
    }


def _conversation_opening_state(
    messages: list[Any],
    *,
    conversation_added_at: Any,
    now_epoch: float | None = None,
) -> dict[str, Any]:
    real_customer_messages: list[dict[str, Any]] = []
    auto_opening_count = 0
    for item in messages:
        if not isinstance(item, dict) or _raw_message_role(item) != "customer":
            continue
        content = _timeline_message_content(item.get("content"))
        if is_platform_auto_opening_message(content):
            auto_opening_count += 1
            continue
        real_customer_messages.append(item)

    first_added_epoch = _parse_epoch(conversation_added_at)
    reference_epoch = now_epoch if now_epoch is not None else time.time()
    first_added_today: bool | None = None
    if first_added_epoch:
        first_added_today = (
            datetime.fromtimestamp(first_added_epoch, tz=_BEIJING_TZ).date()
            == datetime.fromtimestamp(reference_epoch, tz=_BEIJING_TZ).date()
        )
    latest_real_customer_epoch = max(
        (_raw_message_epoch(item) for item in real_customer_messages),
        default=0.0,
    )
    return {
        "has_real_customer_message": bool(real_customer_messages),
        "real_customer_message_count": len(real_customer_messages),
        "auto_opening_message_count": auto_opening_count,
        "first_added_at_reliable": bool(first_added_epoch),
        "first_added_today": first_added_today,
        "latest_real_customer_at_beijing": (
            datetime.fromtimestamp(latest_real_customer_epoch, tz=_BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
            if latest_real_customer_epoch
            else ""
        ),
    }


def _raw_message_role(item: dict[str, Any]) -> str:
    value = str(
        item.get("direction")
        or item.get("role")
        or item.get("sender_type")
        or item.get("from")
        or ""
    ).strip().lower()
    if value in {"customer", "user", "external"}:
        return "customer"
    if value in {"assistant", "staff", "ai", "agent", "employee", "system"}:
        return "assistant"
    return ""


def _raw_message_epoch(item: dict[str, Any]) -> float:
    for key in ("msgtime", "timestamp", "created_at", "sent_at", "message_time", "time"):
        if item.get(key) not in (None, ""):
            return _parse_epoch(item.get(key))
    return 0.0


def _task_type(task: dict[str, Any]) -> str:
    return str(
        task.get("triggerEvent")
        or task.get("trigger_event")
        or task.get("eventType")
        or task.get("event_type")
        or ""
    ).strip().lower()


def _dispatch_mode(task: dict[str, Any]) -> str:
    value = str(task.get("dispatchMode") or task.get("dispatch_mode") or "").strip().lower()
    return "direct" if value == "direct" else "ai_service"


def _terminal_delivery_failure(exc: Exception) -> dict[str, Any]:
    message = str(exc)
    matched = re.search(r"outreach_system_http_(\d{3})\s*:\s*(.*)", message, flags=re.DOTALL)
    if not matched:
        return {}
    status = int(matched.group(1))
    detail = matched.group(2).strip()
    if status < 400 or status >= 500 or status in {408, 429}:
        return {}
    payload: dict[str, Any] = {}
    try:
        decoded = json.loads(detail)
        payload = decoded if isinstance(decoded, dict) else {}
    except (TypeError, ValueError):
        payload = {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    reason_code = str(data.get("reason_code") or data.get("reason") or "").strip()
    return {
        "kind": "downstream_http_rejection",
        "http_status": status,
        "detail": detail[:2000],
        "reason_code": reason_code,
        "retryable": False,
    }


def _delivery_rejection_is_no_send(failure: dict[str, Any]) -> bool:
    reason_code = str(failure.get("reason_code") or "").strip().lower()
    if reason_code in {
        "target_ai_disabled",
        "customer_deleted",
        "customer_relation_deleted",
        "human_takeover",
        "manual_takeover",
    }:
        return True
    detail = str(failure.get("detail") or "").lower()
    if "outside enabled ai scope" in detail or "manual handoff" in detail or "human takeover" in detail:
        return True
    return False


def _is_human_takeover_status(payload: dict[str, Any]) -> bool:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    takeover = data.get("takeover") if isinstance(data.get("takeover"), dict) else {}
    outreach = data.get("ai_outreach") if isinstance(data.get("ai_outreach"), dict) else {}
    mode = str(takeover.get("mode") or "").strip().lower()
    reason_code = str(outreach.get("reason_code") or takeover.get("reason_code") or "").strip().lower()
    return (
        takeover.get("is_human") is True
        or mode == "human"
        or (
            outreach.get("send_allowed") is False
            and reason_code in {"handoff_human_active", "human_takeover", "manual_takeover"}
        )
    )


def _compact_takeover_status(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    takeover = data.get("takeover") if isinstance(data.get("takeover"), dict) else {}
    outreach = data.get("ai_outreach") if isinstance(data.get("ai_outreach"), dict) else {}
    return {
        "conversation_id": str(data.get("conversation_id") or ""),
        "mode": str(takeover.get("mode") or ""),
        "is_human": takeover.get("is_human"),
        "is_ai": takeover.get("is_ai"),
        "handoff_status": str(takeover.get("handoff_status") or ""),
        "reason_code": str(outreach.get("reason_code") or takeover.get("reason_code") or ""),
        "send_allowed": outreach.get("send_allowed"),
    }


def _decision_is_processing_failure(decision: dict[str, Any]) -> bool:
    reason = str(decision.get("reason") or "").strip().lower()
    reason_code = str(decision.get("reason_code") or "").strip().lower()
    scene_code = str(decision.get("sceneCode") or "").strip().lower()
    if reason.startswith("quiet_hours_") or reason_code.startswith("quiet_hours_"):
        return False
    if (
        reason.startswith("invalid_")
        or reason_code.startswith("invalid_")
        or scene_code == "no_send_invalid_message_content"
    ):
        return True
    return False


def _platform_consume_remark(local_task: dict[str, Any] | None) -> str:
    if not isinstance(local_task, dict):
        return ""
    payload = local_task.get("send_payload") if isinstance(local_task.get("send_payload"), dict) else {}
    decision = payload.get("decision") if isinstance(payload.get("decision"), dict) else {}
    return str(decision.get("remark") or "")[:500]


def _bounded_hour(value: Any, *, default: int) -> int:
    try:
        return max(0, min(23, int(value)))
    except (TypeError, ValueError):
        return default


def _hour_in_window(hour: int, *, start_hour: int, end_hour: int) -> bool:
    if start_hour == end_hour:
        return False
    if start_hour < end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour


def _platform_message_error(platform_task: dict[str, Any]) -> str:
    raw = platform_task.get("message_content")
    if not isinstance(raw, list):
        raw = platform_task.get("messageContent")
    if raw is None:
        return ""
    if not isinstance(raw, list):
        return "message_content_not_list"
    for item in raw:
        if not isinstance(item, dict):
            return "message_not_object"
        message_type = str(item.get("type") or "").strip().lower()
        if message_type not in {"text", "image", "video", "link"}:
            return "unsupported_message_type"
        content = item.get("content")
        if message_type == "text":
            text = str(content.get("text") if isinstance(content, dict) else content or "").strip()
            if not text:
                return "empty_text"
            continue
        if message_type == "link" and isinstance(content, dict):
            url = str(content.get("url") or "").strip()
        else:
            url = str(content.get("url") if isinstance(content, dict) else content or "").strip()
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return "invalid_media_url"
    return ""


def _task_identity(task: dict[str, Any]) -> dict[str, str]:
    external = str(
        task.get("customer_wechat_id")
        or task.get("customerWechatId")
        or task.get("external_userid")
        or task.get("customerWechat")
        or ""
    ).strip()
    return {
        "corp_id": str(task.get("corp_id") or task.get("corpId") or task.get("wecomCorpId") or "").strip(),
        "customer_id": str(task.get("customerId") or task.get("customer_id") or external).strip(),
        "external_userid": external,
        "user_id": str(task.get("user_wechat_id") or task.get("userWechatId") or task.get("user_id") or "").strip(),
        "wechat": str(task.get("user_wechat") or task.get("userWechat") or task.get("wechat") or "").strip(),
    }


def _task_id(task: dict[str, Any]) -> str:
    return str(task.get("task_id") or task.get("taskId") or task.get("id") or "").strip()


def _platform_send_trace_fields(task: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "task_id": _task_id(task),
    }
    for output_key, source_keys in (
        ("run_id", ("runId", "run_id")),
        ("rule_id", ("ruleId", "rule_id")),
        ("rule_name", ("ruleName", "rule_name")),
        ("rule_task_id", ("ruleTaskId", "rule_task_id")),
    ):
        value = _first_present_task_field(task, *source_keys)
        if value is not None:
            fields[output_key] = value
    return fields


def _first_present_task_field(task: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in task and task[key] is not None:
            return task[key]
    return None


def _task_scheduled_epoch(task: dict[str, Any]) -> float:
    return _parse_epoch(
        task.get("scheduledAt")
        or task.get("scheduled_at")
        or task.get("executeTime")
        or task.get("execute_time")
    )


def _parse_epoch(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    if isinstance(value, (int, float)):
        number = float(value)
        return number / 1000 if number > 10_000_000_000 else number
    text = str(value).strip()
    try:
        number = float(text)
        return number / 1000 if number > 10_000_000_000 else number
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        # The SOP platform returns human-readable scheduledAt values in Beijing time.
        parsed = parsed.replace(tzinfo=_BEIJING_TZ)
    return parsed.timestamp()


def _task_timing(
    task: dict[str, Any],
    *,
    conversation_added_at: Any = None,
) -> dict[str, Any]:
    scheduled = _task_scheduled_epoch(task)
    first_add_epoch = _parse_epoch(conversation_added_at)
    return {
        "scheduled_at": task.get("scheduledAt") or task.get("scheduled_at") or "",
        "first_added_at": (
            datetime.fromtimestamp(first_add_epoch, tz=_BEIJING_TZ).isoformat()
            if first_add_epoch
            else ""
        ),
        "first_added_at_source": "conversation.added_at" if first_add_epoch else "",
        "pulled_at": task.get("_aics_pulled_at") or "",
        "lateness_seconds": round(max(0.0, time.time() - scheduled), 3) if scheduled else None,
    }


def _merge_platform_task_logs(
    *,
    platform_items: list[dict[str, Any]],
    local_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    local_by_id = {
        _record_task_id(record): record
        for record in local_records
        if _record_task_id(record)
    }
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for platform_task in platform_items:
        task_id = _task_id(platform_task)
        if not task_id:
            continue
        merged.append(
            _platform_task_log_item(
                platform_task=platform_task,
                local_record=local_by_id.get(task_id, {}),
                platform_visible=True,
            )
        )
        seen.add(task_id)
    for task_id, record in local_by_id.items():
        if task_id in seen:
            continue
        stored_task = record.get("platform_task") if isinstance(record.get("platform_task"), dict) else {}
        merged.append(
            _platform_task_log_item(
                platform_task=stored_task,
                local_record=record,
                platform_visible=False,
            )
        )
    merged.sort(key=lambda item: float(item.pop("_sort_epoch", 0.0)), reverse=True)
    return merged


def _platform_task_log_item(
    *,
    platform_task: dict[str, Any],
    local_record: dict[str, Any],
    platform_visible: bool,
) -> dict[str, Any]:
    task_id = _task_id(platform_task) or _record_task_id(local_record)
    event_status = str(local_record.get("event_status") or "")
    task_status = str(local_record.get("task_status") or "")
    bucket = _platform_task_bucket(event_status=event_status, task_status=task_status, has_local=bool(local_record))
    send_payload = local_record.get("send_payload") if isinstance(local_record.get("send_payload"), dict) else {}
    try:
        platform_terminal_status = int(send_payload.get("platform_terminal_status") or 0)
    except (TypeError, ValueError):
        platform_terminal_status = 0
    if platform_terminal_status not in {30, 40, 70}:
        platform_terminal_status = 0
    decision_payload = send_payload.get("decision") if isinstance(send_payload.get("decision"), dict) else {}
    context_payload = send_payload.get("context") if isinstance(send_payload.get("context"), dict) else {}
    timing_payload = (
        context_payload.get("task_timing")
        if isinstance(context_payload.get("task_timing"), dict)
        else {}
    )
    decision = str(decision_payload.get("decision") or "")
    if not decision and task_status in {"shadow_send", "sending", "sent"}:
        decision = "send"
    if not decision and task_status in {"shadow_no_send", "completed_without_send"}:
        decision = "no_send"
    request_payload = send_payload.get("request") if isinstance(send_payload.get("request"), dict) else {}
    final_messages = request_payload.get("reply_messages") if isinstance(request_payload.get("reply_messages"), list) else []
    if not final_messages:
        final_messages = (
            decision_payload.get("reply_messages")
            if isinstance(decision_payload.get("reply_messages"), list)
            else local_record.get("reply_messages")
            if isinstance(local_record.get("reply_messages"), list)
            else []
        )
    identity = _task_identity(platform_task)
    for key in identity:
        if not identity[key]:
            identity[key] = str(local_record.get(key) or "")
    scheduled_at = platform_task.get("scheduledAt") or platform_task.get("scheduled_at") or ""
    scheduled_epoch = _task_scheduled_epoch(platform_task)
    received_at = str(local_record.get("received_at") or "")
    return {
        "task_id": task_id,
        "bucket": bucket,
        "stage_label": _PLATFORM_TASK_BUCKET_LABELS[bucket],
        "platform_status": str(
            platform_task.get("status")
            or ("10" if platform_visible else platform_terminal_status or "")
        ),
        "platform_terminal_status": platform_terminal_status or None,
        "platform_visible": platform_visible,
        "event_status": event_status,
        "task_status": task_status,
        "decision": decision,
        "decision_reason": str(decision_payload.get("reason") or ""),
        "error": str(local_record.get("task_error") or local_record.get("event_error") or ""),
        "customer_id": identity["customer_id"],
        "external_userid": identity["external_userid"],
        "corp_id": identity["corp_id"],
        "user_id": identity["user_id"],
        "wechat": identity["wechat"],
        "rule_name": str(platform_task.get("ruleName") or platform_task.get("sceneName") or local_record.get("sop_pack_name") or ""),
        "scene": platform_task.get("scene") if isinstance(platform_task.get("scene"), dict) else {},
        "dispatch_mode": _dispatch_mode(platform_task),
        "first_added_at": str(timing_payload.get("first_added_at") or ""),
        "first_added_at_source": str(timing_payload.get("first_added_at_source") or ""),
        "scheduled_at": scheduled_at,
        "pulled_at": str(platform_task.get("_aics_pulled_at") or received_at),
        "updated_at": str(local_record.get("task_updated_at") or local_record.get("event_updated_at") or ""),
        "sent_at": str(local_record.get("sent_at") or ""),
        "lateness_seconds": round(max(0.0, time.time() - scheduled_epoch), 3) if scheduled_epoch else None,
        "original_messages": _platform_messages(platform_task),
        "final_messages": final_messages,
        "send_response": local_record.get("send_response") if isinstance(local_record.get("send_response"), dict) else {},
        "_sort_epoch": max(scheduled_epoch, _parse_epoch(received_at)),
    }


def _record_task_id(record: dict[str, Any]) -> str:
    platform_task = record.get("platform_task") if isinstance(record.get("platform_task"), dict) else {}
    task_id = _task_id(platform_task)
    if task_id:
        return task_id
    event_id = str(record.get("event_id") or "")
    return event_id.split(":", 1)[1] if event_id.startswith("platform_sop_task:") else ""


def _platform_task_bucket(*, event_status: str, task_status: str, has_local: bool) -> str:
    if not has_local:
        return "platform_pending"
    if event_status == "platform_failed" or task_status == "failed":
        return "failed"
    if event_status in {
        "platform_send_uncertain",
        "platform_processing_retry",
        "platform_complete_pending",
        "platform_terminal_pending",
    } or task_status in {"processing_retry"}:
        return "recovery"
    if task_status == "sent":
        return "sent"
    if task_status == "sending":
        return "sending"
    if task_status in {"shadow_no_send", "completed_without_send"}:
        return "judged_no_send"
    if task_status == "shadow_send":
        return "judged_send"
    if task_status == "judging" or event_status in {"platform_judging", "platform_claiming", "platform_processing"}:
        return "judging"
    return "pulled_unjudged"


_PLATFORM_TASK_BUCKET_LABELS = {
    "platform_pending": "平台待拉取",
    "pulled_unjudged": "已拉取待判断",
    "judging": "判断中",
    "judged_send": "已判断发送",
    "judged_no_send": "已判断不发",
    "sending": "发送中",
    "sent": "已发送",
    "failed": "处理失败",
    "recovery": "恢复中",
}


def _timing_summary(values: deque[float]) -> dict[str, float | int]:
    if not values:
        return {"count": 0, "avg": 0.0, "p50": 0.0, "p90": 0.0, "max": 0.0}
    ordered = sorted(values)
    count = len(ordered)

    def percentile(ratio: float) -> float:
        return ordered[min(count - 1, max(0, int((count - 1) * ratio)))]

    return {
        "count": count,
        "avg": round(sum(ordered) / count, 3),
        "p50": round(percentile(0.5), 3),
        "p90": round(percentile(0.9), 3),
        "max": round(ordered[-1], 3),
    }


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _require_platform_status(response: dict[str, Any], expected: int) -> None:
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    try:
        actual = int(data.get("status"))
    except (TypeError, ValueError):
        raise RuntimeError("platform consume response is missing status") from None
    if actual != expected:
        raise RuntimeError(f"platform consume status mismatch: expected {expected}, got {actual}")


_BEIJING_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


def _compact_customer_relation(relation: dict[str, Any]) -> dict[str, Any]:
    return {
        key: relation.get(key)
        for key in ("status", "is_deleted", "deleted_at", "updated_at")
        if relation.get(key) not in (None, "")
    }


def _conversation_timeline(messages: list[Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    previous_epoch = 0.0
    now_epoch = time.time()
    for index, item in enumerate(messages[-80:], start=1):
        if not isinstance(item, dict):
            continue
        raw_direction = str(
            item.get("direction")
            or item.get("role")
            or item.get("sender_type")
            or item.get("from")
            or ""
        ).strip().lower()
        if raw_direction in {"customer", "user", "external"}:
            role = "customer"
        elif raw_direction in {"assistant", "staff", "ai", "agent", "employee", "system"}:
            role = "assistant"
        else:
            role = raw_direction or "unknown"
        message_type = str(
            item.get("msgtype") or item.get("message_type") or item.get("type") or "text"
        ).strip().lower()
        content = _timeline_message_content(item.get("content"))
        raw_time = next(
            (
                item.get(key)
                for key in ("msgtime", "timestamp", "created_at", "sent_at", "message_time", "time")
                if item.get(key) not in (None, "")
            ),
            "",
        )
        epoch = _parse_epoch(raw_time) if raw_time not in (None, "") else 0.0
        timeline_item: dict[str, Any] = {
            "message_ref": f"msg_{index:03d}",
            "role": role,
            "message_type": message_type,
            "content": content[:600],
        }
        if epoch:
            timeline_item.update(
                {
                    "occurred_at_beijing": datetime.fromtimestamp(epoch, tz=_BEIJING_TZ).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                    "time_ago": _human_duration(max(0.0, now_epoch - epoch)),
                }
            )
            if previous_epoch:
                timeline_item["gap_from_previous"] = _human_duration(max(0.0, epoch - previous_epoch))
            previous_epoch = epoch
        elif raw_time not in (None, ""):
            timeline_item["raw_time"] = str(raw_time)
        if any(value not in (None, "") for value in timeline_item.values()):
            output.append(timeline_item)
    return output


def _timeline_message_content(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("text", "content", "transcript", "description", "url", "store_id"):
            text = str(value.get(key) or "").strip()
            if text:
                return text
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))[:600]
    return str(value or "").strip()


def _timeline_structure(timeline: list[Any]) -> dict[str, Any]:
    valid = [item for item in timeline if isinstance(item, dict)]
    latest_customer_index = next(
        (index for index in range(len(valid) - 1, -1, -1) if valid[index].get("role") == "customer"),
        -1,
    )
    assistant_after_latest_customer = bool(
        latest_customer_index >= 0
        and any(item.get("role") == "assistant" for item in valid[latest_customer_index + 1 :])
    )
    return {
        "message_count": len(valid),
        "customer_message_count": sum(item.get("role") == "customer" for item in valid),
        "assistant_message_count": sum(item.get("role") == "assistant" for item in valid),
        "latest_message_ref": valid[-1].get("message_ref") if valid else "",
        "latest_message_role": valid[-1].get("role") if valid else "",
        "latest_customer_message_ref": (
            valid[latest_customer_index].get("message_ref") if latest_customer_index >= 0 else ""
        ),
        "assistant_after_latest_customer": assistant_after_latest_customer,
    }


def _human_duration(seconds: float) -> str:
    total_minutes = max(0, int(seconds // 60))
    if total_minutes < 1:
        return "不到1分钟"
    if total_minutes < 60:
        return f"{total_minutes}分钟"
    total_hours = total_minutes // 60
    remaining_minutes = total_minutes % 60
    if total_hours < 24:
        return f"{total_hours}小时" + (f"{remaining_minutes}分钟" if remaining_minutes else "")
    days = total_hours // 24
    remaining_hours = total_hours % 24
    return f"{days}天" + (f"{remaining_hours}小时" if remaining_hours else "")


def _compact_business_state(context: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(context, dict):
        return {}
    output: dict[str, Any] = {
        "source": context.get("source"),
        "customer": {
            key: context.get("customer", {}).get(key)
            for key in ("id", "name", "kind", "category_id")
            if isinstance(context.get("customer"), dict)
            and context.get("customer", {}).get(key) not in (None, "")
        },
        "appointment": context.get("appointment") if isinstance(context.get("appointment"), dict) else {},
        "orders": [
            {
                key: order.get(key)
                for key in (
                    "id",
                    "order_no",
                    "status",
                    "is_current_order",
                    "fee_required",
                    "fee_paid",
                    "fee_paid_total",
                    "prepay_paid",
                    "paid_protection_status",
                    "created_at",
                    "store_id",
                    "store_name",
                    "appointment_time",
                    "projects",
                )
                if order.get(key) not in (None, "", [], {})
            }
            for order in context.get("orders", [])[:5]
            if isinstance(order, dict)
        ],
    }
    for key in (
        "payment_state",
        "deposit_state",
        "appointment_state",
        "human_takeover",
        "risk_state",
        "complaint_state",
        "refund_state",
        "error",
        "orders_error",
    ):
        if context.get(key) not in (None, "", [], {}):
            output[key] = context.get(key)
    return {key: value for key, value in output.items() if value not in (None, "", [], {})}


def _paid_or_appointment_delivery_conflict(business_state: dict[str, Any]) -> str:
    appointment = (
        business_state.get("appointment")
        if isinstance(business_state.get("appointment"), dict)
        else {}
    )
    appointment_status = str(appointment.get("status") or "").strip().lower()
    if appointment.get("has_active") is True or appointment_status in {
        "confirmed",
        "waiting_schedule",
        "scheduled",
    }:
        return "active_appointment_conflict"

    for order in business_state.get("orders") or []:
        if not isinstance(order, dict):
            continue
        protection_status = str(order.get("paid_protection_status") or "").strip().lower()
        if protection_status in {
            "expired",
            "inactive_order_expired",
            "completed_order_expired",
        }:
            continue
        paid = order.get("prepay_paid")
        try:
            paid_amount = float(paid or 0)
        except (TypeError, ValueError):
            paid_amount = 0.0
        deposit_state = str(order.get("deposit_state") or "").strip().lower()
        if paid_amount > 0 or deposit_state == "paid_by_order":
            return "active_paid_order_conflict"
        if str(order.get("status") or "").strip().lower() in {"waiting_schedule", "scheduled"}:
            return "active_appointment_order_conflict"
    return ""


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _send_content_for_rule_data(messages: list[Any]) -> str:
    parts: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        message_type = str(message.get("type") or "").strip().lower()
        content = message.get("content") if isinstance(message.get("content"), dict) else {}
        if message_type == "text":
            text = str(content.get("text") or content.get("content") or "").strip()
            if text:
                parts.append(text)
        elif message_type in {"image", "video"}:
            url = str(content.get("url") or content.get("content") or "").strip()
            if url:
                parts.append(f"[{message_type}]{url}")
        elif message_type == "payment_collection":
            amount = content.get("amount")
            parts.append(f"[payment_collection]{amount}")
    return "\n".join(parts)[:10000]


def _context_audit(context: dict[str, Any]) -> dict[str, Any]:
    relation = context.get("customer_relation") if isinstance(context.get("customer_relation"), dict) else {}
    customer_context = context.get("business_state") if isinstance(context.get("business_state"), dict) else {}
    quiet_hours = context.get("quiet_hours") if isinstance(context.get("quiet_hours"), dict) else {}
    first_day_route = (
        context.get("first_day_platform_sop_route")
        if isinstance(context.get("first_day_platform_sop_route"), dict)
        else {}
    )
    if not first_day_route and context.get("source") == "first_day_platform_sop_route":
        first_day_route = {
            key: context.get(key)
            for key in (
                "route_checked",
                "opening_state",
                "route_reason",
                "first_add_age_seconds",
                "activity",
                "conversation_error",
            )
            if context.get(key) not in (None, "")
        }
    return {
        "source": str(context.get("source") or ""),
        "routing_decision": str(context.get("routing_decision") or ""),
        "dispatch_mode": str(context.get("dispatch_mode") or ""),
        "conversation_loaded": context.get("conversation_loaded"),
        "conversation_error": str(context.get("conversation_error") or ""),
        "conversation_count": int(context.get("conversation_count") or 0),
        "customer_relation": relation,
        "customer_context_source": customer_context.get("source"),
        "customer_context_error": customer_context.get("error"),
        "task_timing": context.get("task_timing") if isinstance(context.get("task_timing"), dict) else {},
        "quiet_hours": quiet_hours,
        "opening_state": context.get("opening_state") if isinstance(context.get("opening_state"), dict) else {},
        "platform_contact_delivery_guard": (
            context.get("platform_contact_delivery_guard")
            if isinstance(context.get("platform_contact_delivery_guard"), dict)
            else {}
        ),
        "first_day_platform_sop_route": first_day_route,
    }
