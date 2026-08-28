from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections import Counter, deque
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from app.policies.business_rules import sop_platform_business_facts_for_model
from app.services.payment_collection import PAYMENT_COLLECTION_UNIT_AMOUNT
from app.services.sop_execution_service import is_platform_auto_opening_message
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
3. Exact duplicate: no_send only when the full candidate batch has the same
   message types, order, text and media/link URLs as a recently sent full batch.
   Similar topic, similar intent, or non-identical wording is not duplicate.
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


SOP_PLATFORM_BATCH_SYSTEM_PROMPT = """
你是第三方 SOP 待消费消息组的顺序审核节点。平台已经决定触发时间；你只负责按照给定顺序判断哪些旧消息组已经不适合发送，以及最早哪一组现在仍可发送。

严格规则：
1. `pending_groups` 已按计划时间升序排列。必须从第一组开始逐组判断；每轮最多选择一组发送，遇到第一条 `send` 立即停止，禁止评价后续组。比如 1、2 为 `skip`、3 为 `send` 时，只返回 1、2、3 的判断，4 不判断、不返回、继续留待下轮处理。
2. 平台原始 `message_content` 是不可修改的事实载体。你不能改写、删减、合并、重排其中任何文字、价格、项目、邀约、退款说明、图片、视频或链接。
3. 你可以输出一条简短 `transition_text` 承接最新聊天，但只能连接上下文，不能加入任何新的业务事实、数字、价格、项目、承诺、优惠、名额、门店、距离或时间安排。不需要过渡时返回空字符串。
4. 判断消息组的真实目的，不能只按关键词处理。包含价格不等于标准报价；价格可以是回访唤醒内容。只有客户近期已经完成同一沟通目的、内容与最新状态冲突、客户明确拒绝/投诉/健康风险、已付后仍催付，或人工接管等情况才跳过。
5. 客户普通沉默、犹豫或没有主动提问，不是自动跳过理由。客户最后有尚未回答的具体问题且本组不能解决时，应跳过，让普通回复链路处理。
6. 客户确认意向、回复“好”或表示会到店，不等于平台计划的提醒已经发送。只有历史中已有可比的助手/平台消息交付证据，或最新状态使提醒失效时，才能以“已处理/重复”为由跳过。
7. 前一组被跳过后，不得仅因后一组属于相同大类而一并跳过。必须比较后一组是否提供了尚未交付的新信息、不同提醒目的或新的行动价值；有则可以发送，无则继续跳过。
8. 客户明确要求接收某个后续消息组已经包含的内容，是该组仍可发送的直接证据。除非命中删除、人工接管、投诉退款、健康风险、已付冲突等硬边界，不得把客户的明确接收请求解释成困惑、拒绝或重复。
9. 客户提到“第一条、第二条”等顺序时，必须同时核对每组 `sequence_index` 和该组真实 `message_content`，不得只凭语义相近就把后一组内容错认成前一组。
10. `evidence_refs` 只能引用输入中真实存在的 `msg_*` 或 `task:*` 引用。
11. 只返回小写 json，不要 Markdown 或解释。
12. 每个消息组中的价格、项目、邀约、退款说明、预约金和媒体，都是该任务自己的权威原始事实。不得与任何全局活动报价比较，不得因为不同任务价格或品项不同而判定冲突或跳过。

输出：
{
  "evaluations": [
    {
      "task_id": "",
      "decision": "skip | send",
      "reason": "",
      "evidence_refs": []
    }
  ],
  "selected_task_id": "",
  "transition_text": ""
}

若全部不适合发送，所有任务均输出 `skip`，`selected_task_id` 和 `transition_text` 为空。
""".strip()


SOP_TRANSITION_FACT_AUDIT_PROMPT = """
你只审查一条 SOP 过渡句是否加入了新的业务事实。不要评价销售力度、语气或是否应该发送，也不要改写文本。

允许：称呼、承接客户上一句话、说明接下来发送平台原始内容的纯连接表达。
禁止：任何输入证据中没有直接支持的价格、项目、效果、退款、预约金、名额、赠品、门店、距离、营业时间、到店时间、支付或预约事实。

只返回小写 json：
{"status":"pass | fail","reason":""}
""".strip()


def _sop_platform_batch_business_facts_for_model() -> dict[str, Any]:
    source = sop_platform_business_facts_for_model()
    transaction = (
        source.get("transaction_policy")
        if isinstance(source.get("transaction_policy"), dict)
        else {}
    )
    return {
        "version": source.get("version"),
        "scope": "safety_boundaries_only",
        "payment_hard_blocks": transaction.get("payment_hard_blocks") or [],
        "hard_forbidden": source.get("hard_forbidden") or [],
    }


def _sop_platform_decision_model_options(settings: Any) -> dict[str, Any]:
    primary = str(getattr(settings, "sop_platform_decision_model", "") or "").strip()
    api_key = str(getattr(settings, "sop_platform_decision_api_key", "") or "").strip()
    if not primary or not api_key:
        return {}
    return {
        "model_names_override": [primary],
        "api_key_override": api_key,
        "base_url_override": str(getattr(settings, "sop_platform_decision_base_url", "") or "").strip(),
        "request_body_overrides": {"thinking": {"type": "disabled"}},
    }


def _sop_platform_decision_fallback_models(settings: Any) -> list[str]:
    fallback_text = str(getattr(settings, "sop_platform_decision_model_fallbacks", "") or "")
    return list(dict.fromkeys(item.strip() for item in fallback_text.split(",") if item.strip()))


class SopPlatformTaskService:
    RECOVERY_STATUSES = [
        "platform_claiming",
        "platform_judging",
        "platform_processing",
        "platform_processing_retry",
        "platform_send_uncertain",
        "platform_complete_pending",
        "platform_batch_send_retry",
        "platform_batch_consume_pending",
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
        self._reserved_prefix_ids: set[str] = set()
        self._terminal_ids: set[str] = set()
        self._terminal_order: deque[str] = deque()
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
        self._deferred_replay_keys: set[str] = set()

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
        self._restore_reserved_prefix_ids()
        self._refresh_deferred_replay_keys()
        free_slots = max(0, self._queue.maxsize - self._queue.qsize())
        if free_slots <= 0:
            return {
                "pending_count": self._pending_total,
                "enqueued_count": 0,
                "queue_depth": self._queue.qsize(),
                "in_flight_count": len(self._in_flight_ids),
                "error_count": 0,
            }
        started = time.perf_counter()
        self._last_poll_at = utc_now_iso()
        try:
            online_page = await self.platform_client.pending(limit=500)
            online_items = (
                online_page
                if isinstance(online_page, list)
                else online_page.get("items") if isinstance(online_page, dict) else []
            )
            needs_content_lookup = any(
                isinstance(item, dict) and not _platform_messages(item)
                for item in (online_items if isinstance(online_items, list) else [])
            )
            if needs_content_lookup:
                store_visit_page = await self.platform_client.store_visit_pending(limit=500)
            else:
                store_visit_page = {"items": [], "total": 0}
            self._last_poll_error = ""
        except Exception as exc:
            self._last_poll_error = f"{type(exc).__name__}: {exc}"
            self._counters["poll_error"] += 1
            raise
        finally:
            self._observe("pull", time.perf_counter() - started)
        if isinstance(online_page, list):
            online_page = {"items": online_page, "total": len(online_page)}
        if isinstance(store_visit_page, list):
            store_visit_page = {"items": store_visit_page, "total": len(store_visit_page)}
        pages = []
        for biz_type, page in (("online_service", online_page), ("store_visit", store_visit_page)):
            if isinstance(page, list):
                page = {"items": page, "total": len(page)}
            page = dict(page or {})
            page["biz_type"] = biz_type
            pages.append(page)
        incomplete = [
            page
            for page in pages
            if int(page.get("total") or 0)
            > len(page.get("items") if isinstance(page.get("items"), list) else [])
        ]
        # `/pending` is the due-time driver. New tasks may omit message_content and
        # expose their full message groups through `/store-visit-pending` instead.
        # The latter is therefore a content source, not an additional business queue.
        self._pending_total = max(
            len(online_page.get("items") or []),
            int(online_page.get("total") or 0),
        )
        if incomplete:
            self._last_poll_error = "pending_page_incomplete"
            self._counters["pending_page_incomplete"] += 1
            return {
                "pending_count": self._pending_total,
                "enqueued_count": 0,
                "queue_depth": self._queue.qsize(),
                "in_flight_count": len(self._in_flight_ids),
                "error_count": 1,
            }
        tasks, unresolved_content_triggers = _resolve_compatible_pending_tasks(
            online_page.get("items") if isinstance(online_page.get("items"), list) else [],
            store_visit_page.get("items") if isinstance(store_visit_page.get("items"), list) else [],
        )
        quiet_mode = _in_configured_quiet_hours(settings=self.settings)
        if unresolved_content_triggers:
            self._counters["pending_content_lookup_missing"] += len(unresolved_content_triggers)
            logger.warning(
                "Third-party SOP due triggers have no matching full message groups: %s",
                [_task_id(task) for task in unresolved_content_triggers],
            )
            quiet_unresolved_triggers = [
                trigger
                for trigger in unresolved_content_triggers
                if quiet_mode
                or bool(_quiet_hours_base_summary(trigger, settings=self.settings).get("in_quiet_hours"))
            ]
            if quiet_unresolved_triggers:
                for trigger in quiet_unresolved_triggers:
                    trigger["_aics_content_unavailable"] = True
                tasks = _dedupe_tasks([*tasks, *quiet_unresolved_triggers])
        tasks.sort(key=_task_batch_sort_key)
        now_epoch = time.time()
        lags = [max(0.0, now_epoch - value) for value in map(_task_scheduled_epoch, tasks) if value]
        self._oldest_due_lag_seconds = max(lags, default=0.0)
        if self._oldest_due_lag_seconds > 120:
            logger.warning(
                "Third-party SOP queue lag is %.1fs (pending=%s)",
                self._oldest_due_lag_seconds,
                self._pending_total,
            )
        grouped: dict[str, list[dict[str, Any]]] = {}
        for task in tasks:
            grouped.setdefault(_customer_batch_key(task), []).append(task)

        enqueued = 0
        pulled_at = utc_now_iso()
        for batch_key, batch_tasks in grouped.items():
            trigger_tasks = _batch_compat_trigger_tasks({"tasks": batch_tasks})
            trigger_ids = {_task_id(task) for task in trigger_tasks if _task_id(task)}
            if any(
                task_id in self._queued_ids
                or task_id in self._in_flight_ids
                or task_id in self._reserved_prefix_ids
                or task_id in self._terminal_ids
                for task_id in trigger_ids
            ):
                self._counters["duplicate_poll"] += len(batch_tasks)
                continue
            eligible = []
            for task in batch_tasks:
                task_id = _task_id(task)
                if (
                    not task_id
                    or task_id in self._queued_ids
                    or task_id in self._in_flight_ids
                    or task_id in self._reserved_prefix_ids
                    or task_id in self._terminal_ids
                ):
                    self._counters["duplicate_poll"] += 1
                    continue
                eligible.append(task)
            if not eligible:
                continue
            if self._queue.full():
                break
            persisted: list[dict[str, Any]] = []
            for task in eligible:
                task["_aics_pulled_at"] = pulled_at
                try:
                    self._ensure_local_task(task, status="platform_queued")
                except Exception:
                    self._counters["persistence_error"] += 1
                    logger.exception("Unable to persist pulled third-party SOP task: %s", _task_id(task))
                    continue
                persisted.append(task)
            if not persisted:
                continue
            trigger_tasks = _batch_compat_trigger_tasks({"tasks": persisted})
            for trigger_task in trigger_tasks:
                try:
                    self._ensure_local_task(trigger_task, status="platform_waiting_content_resolution")
                except Exception:
                    self._counters["persistence_error"] += 1
                    logger.exception(
                        "Unable to persist third-party SOP compatibility trigger: %s",
                        _task_id(trigger_task),
                    )
            for task in [*persisted, *trigger_tasks]:
                self._queued_ids.add(_task_id(task))
            self._queue.put_nowait(
                {
                    "_aics_customer_batch": True,
                    "batch_key": batch_key,
                    "biz_type": "online_service",
                    "tasks": persisted,
                    "compat_trigger_tasks": trigger_tasks,
                }
            )
            enqueued += len(persisted)
        self._counters["fetched"] += len(tasks)
        self._counters["enqueued"] += enqueued
        if quiet_mode:
            self._counters["quiet_enqueued_for_no_replay"] += enqueued
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
            queue_item = await self._queue.get()
            batch_tasks = _batch_tasks(queue_item)
            trigger_tasks = _batch_compat_trigger_tasks(queue_item)
            task_ids = list(
                dict.fromkeys(
                    _task_id(task)
                    for task in [*batch_tasks, *trigger_tasks]
                    if _task_id(task)
                )
            )
            for task_id in task_ids:
                self._queued_ids.discard(task_id)
                self._in_flight_ids.add(task_id)
            started = time.perf_counter()
            scheduled = min((_task_scheduled_epoch(task) for task in batch_tasks if _task_scheduled_epoch(task)), default=0.0)
            if scheduled:
                self._observe("queue_lag", max(0.0, time.time() - scheduled))
            try:
                if queue_item.get("_aics_customer_batch"):
                    result = await self.process_customer_batch(queue_item)
                else:
                    result = await self.process_task(queue_item)
                self._record_result(result)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._counters["retry"] += 1
                logger.exception("Third-party SOP customer batch failed and remains recoverable: %s", task_ids)
            finally:
                self._observe("task", time.perf_counter() - started)
                for task_id in task_ids:
                    self._in_flight_ids.discard(task_id)
                self._queue.task_done()

    async def _recovery_loop(self) -> None:
        while True:
            try:
                await self.process_deferred_replays()
                await self.process_recoveries()
            except asyncio.CancelledError:
                raise
            except Exception:
                self._counters["recovery_error"] += 1
                logger.exception("Third-party SOP recovery iteration failed")
            await asyncio.sleep(max(1.0, float(self.settings.sop_platform_poll_seconds)))

    async def process_recoveries(self) -> int:
        if _in_configured_quiet_hours(settings=self.settings):
            self._counters["quiet_recovery_blocked"] += 1
            return 0
        events = self.repository.list_sop_events_by_statuses(
            self.RECOVERY_STATUSES,
            limit=self.settings.sop_platform_recovery_batch_size,
            event_type="platform_sop_task",
        )
        orphan_loader = getattr(self.repository, "list_orphaned_platform_sop_events", None)
        orphan_events = (
            orphan_loader(limit=self.settings.sop_platform_recovery_batch_size)
            if callable(orphan_loader)
            else []
        )
        known_event_ids = {str(event.get("event_id") or "") for event in events}
        for orphan in orphan_events:
            event_id = str(orphan.get("event_id") or "")
            if event_id and event_id not in known_event_ids:
                events.append({**orphan, "status": "platform_processing"})
                known_event_ids.add(event_id)
        concurrency = max(1, int(getattr(self.settings, "sop_platform_recovery_concurrency", 2) or 2))
        semaphore = asyncio.Semaphore(concurrency)

        async def recover(event: dict[str, Any]) -> int:
            payload = event.get("raw_payload") if isinstance(event.get("raw_payload"), dict) else {}
            task = payload.get("platform_task") if isinstance(payload.get("platform_task"), dict) else {}
            if not task:
                self.repository.update_sop_event_status(
                    str(event.get("event_id") or ""),
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
        if _in_configured_quiet_hours(settings=self.settings):
            self._counters["quiet_execution_deferred"] += 1
            return {"processed": False, "status": "quiet_deferred", "task_id": task_id}
        lock = self._locks.setdefault(task_id, asyncio.Lock())
        async with lock:
            duplicate_key = _platform_duplicate_send_once_key(platform_task)
            if duplicate_key:
                content_lock = self._locks.setdefault(f"platform-content:{duplicate_key}", asyncio.Lock())
                async with content_lock:
                    return await self._process_locked(platform_task, task_id=task_id, recovery_status=recovery_status)
            return await self._process_locked(platform_task, task_id=task_id, recovery_status=recovery_status)

    async def process_customer_batch(self, batch: dict[str, Any]) -> dict[str, Any]:
        tasks = sorted(_batch_tasks(batch), key=_task_batch_sort_key)
        trigger_tasks = sorted(_batch_compat_trigger_tasks(batch), key=_task_batch_sort_key)
        if not tasks:
            return {"processed": False, "status": "empty_batch", "task_ids": []}
        batch_key = str(batch.get("batch_key") or _customer_batch_key(tasks[0]))
        lock = self._locks.setdefault(f"customer-batch:{batch_key}", asyncio.Lock())
        async with lock:
            return await self._process_customer_batch_locked(
                tasks,
                trigger_tasks=trigger_tasks,
                batch_key=batch_key,
                biz_type=str(batch.get("biz_type") or tasks[0].get("_aics_biz_type") or "online_service"),
            )

    async def _process_customer_batch_locked(
        self,
        tasks: list[dict[str, Any]],
        *,
        trigger_tasks: list[dict[str, Any]],
        batch_key: str,
        biz_type: str,
    ) -> dict[str, Any]:
        identity = _task_identity(tasks[0])
        batch_task_ids = [_task_id(task) for task in tasks]
        batch_run_id = f"{biz_type}:{batch_task_ids[0]}"
        quiet_hours = _quiet_hours_base_summary(tasks[0], settings=self.settings)
        if _in_configured_quiet_hours(settings=self.settings) or quiet_hours.get("in_quiet_hours"):
            for task in [*tasks, *trigger_tasks]:
                self._ensure_local_task(task, status="platform_queued")
            self._counters["quiet_consumed_without_replay"] += len(tasks)
            quiet_hours.update({"blocked": True, "reason": "quiet_hours_no_replay"})
            return await self._consume_batch_without_send(
                tasks,
                trigger_tasks=trigger_tasks,
                reason="quiet_hours_no_replay",
                batch_key=batch_key,
                biz_type=biz_type,
                batch_run_id=batch_run_id,
                decision=_quiet_hours_no_replay_decision(tasks),
                audit_context={"quiet_hours": quiet_hours},
            )
        if not _batch_identity_is_consistent([*tasks, *trigger_tasks], identity=identity):
            raise RuntimeError("platform customer batch contains mixed identities")
        missing = [key for key in ("corp_id", "customer_id", "external_userid", "user_id", "wechat") if not identity[key]]
        if missing:
            raise RuntimeError(f"platform customer batch missing identity: {','.join(missing)}")
        for task in [*tasks, *trigger_tasks]:
            self._ensure_local_task(task, status="platform_queued")

        if batch_key in self._deferred_replay_keys:
            self._counters["deferred_replay_appended"] += len(tasks)
            return await self._consume_batch_without_send(
                tasks,
                trigger_tasks=trigger_tasks,
                reason="deferred_behind_quiet_backlog",
                batch_key=batch_key,
                biz_type=biz_type,
                batch_run_id=batch_run_id,
                decision=_deferred_replay_queue_decision(tasks),
                audit_context={"deferred_replay_queue": {"appended": True}},
            )

        status_response = await self.system_client.conversation_status(**identity)
        status_data = (
            status_response.get("data")
            if isinstance(status_response.get("data"), dict)
            else status_response
        )
        if not isinstance(status_data, dict):
            raise RuntimeError("platform customer conversation status response is invalid")
        ai_auto_reply = _conversation_ai_auto_reply(status_data)
        if ai_auto_reply is None:
            raise RuntimeError("platform customer conversation status is missing ai_auto_reply")
        base_audit_context = {
            "management_mode": "ai" if ai_auto_reply else "human",
            "management_source": "conversation_status.takeover.ai_auto_reply",
            "management_status": _compact_management_status(status_data),
        }
        conversation = await self.system_client.conversation(**identity, limit=50)
        data = conversation.get("data") if isinstance(conversation.get("data"), dict) else conversation
        if not isinstance(data, dict):
            raise RuntimeError("platform customer conversation response is invalid")
        if not isinstance(data.get("customer_relation"), dict):
            raise RuntimeError("platform customer conversation is missing customer_relation")
        relation = data["customer_relation"]
        raw_messages = data.get("messages") if isinstance(data.get("messages"), list) else []
        timeline = _conversation_timeline(raw_messages)
        timeline_structure = _timeline_structure(timeline)
        base_audit_context.update({
            "timeline_structure": timeline_structure,
            "customer_opened": bool(timeline_structure.get("customer_message_count")),
            "customer_relation": _compact_customer_relation(relation),
        })
        if relation.get("is_deleted") is True or str(relation.get("status") or "").lower() == "deleted":
            return await self._consume_batch_without_send(
                tasks,
                trigger_tasks=trigger_tasks,
                reason="customer_relation_deleted",
                batch_key=batch_key,
                biz_type=biz_type,
                batch_run_id=batch_run_id,
                audit_context=base_audit_context,
                content_exhausted=True,
            )
        if ai_auto_reply is False:
            return await self._consume_batch_without_send(
                tasks,
                trigger_tasks=trigger_tasks,
                reason="human_takeover",
                batch_key=batch_key,
                biz_type=biz_type,
                batch_run_id=batch_run_id,
                audit_context=base_audit_context,
                content_exhausted=True,
            )
        context = await self._load_batch_context(
            tasks[0],
            identity=identity,
            relation=relation,
            timeline=timeline,
        )
        same_day_unopened = _is_same_day_unopened(tasks, timeline=timeline)
        context.update(
            {
                **base_audit_context,
                "same_day_unopened": same_day_unopened,
            }
        )
        if same_day_unopened:
            decision = {
                "evaluations": [
                    {
                        "task_id": _task_id(tasks[0]),
                        "decision": "send",
                        "reason": "same_day_unopened_earliest_direct",
                        "evidence_refs": [f"task:{_task_id(tasks[0])}"],
                    }
                ],
                "selected_task_id": _task_id(tasks[0]),
                "transition_text": "",
                "decision_source": "same_day_unopened_direct",
            }
        else:
            decision = await self._decide_customer_batch(tasks, context=context)

        selected_id = str(decision.get("selected_task_id") or "").strip()
        if not selected_id:
            return await self._consume_batch_without_send(
                tasks,
                trigger_tasks=trigger_tasks,
                reason="all_due_groups_filtered",
                batch_key=batch_key,
                biz_type=biz_type,
                batch_run_id=batch_run_id,
                decision=decision,
                audit_context=context,
            )
        selected_index = next((index for index, task in enumerate(tasks) if _task_id(task) == selected_id), -1)
        if selected_index < 0:
            raise RuntimeError("batch model selected an unknown task_id")
        selected_task = tasks[selected_index]
        skipped_prefix = tasks[:selected_index]
        transition_text = str(decision.get("transition_text") or "").strip()
        if transition_text:
            passed = await self._transition_fact_audit(
                transition_text,
                selected_task=selected_task,
                context=context,
            )
            if not passed:
                transition_text = ""
                decision["transition_text"] = ""
                decision["transition_audit"] = "dropped_new_fact_risk"
        return await self._send_selected_batch_task(
            selected_task,
            skipped_prefix=skipped_prefix,
            trigger_tasks=trigger_tasks,
            transition_text=transition_text,
            decision=decision,
            context=context,
            identity=identity,
            batch_key=batch_key,
            biz_type=biz_type,
            batch_run_id=batch_run_id,
            batch_task_ids=batch_task_ids,
        )

    async def _load_batch_context(
        self,
        platform_task: dict[str, Any],
        *,
        identity: dict[str, str],
        relation: dict[str, Any],
        timeline: list[dict[str, Any]],
    ) -> dict[str, Any]:
        request_context = {
            "source_protocol": "third_party_sop_pending_batch",
            **identity,
            "order_id": platform_task.get("orderId") or platform_task.get("order_id"),
            "order_no": platform_task.get("orderNo") or platform_task.get("order_no"),
        }
        customer_context = await asyncio.to_thread(
            self.customer_context_service.load,
            customer_id=identity["customer_id"],
            memory={},
            request_context=request_context,
        )
        return {
            "customer_relation": _compact_customer_relation(relation),
            "conversation_timeline": timeline,
            "timeline_structure": _timeline_structure(timeline),
            "business_state": _compact_business_state(customer_context),
        }

    async def _decide_customer_batch(
        self,
        tasks: list[dict[str, Any]],
        *,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        groups = []
        for sequence_index, task in enumerate(tasks, start=1):
            task_id = _task_id(task)
            groups.append(
                {
                    "sequence_index": sequence_index,
                    "task_id": task_id,
                    "task_ref": f"task:{task_id}",
                    "scheduled_at": task.get("scheduledAt") or task.get("scheduled_at"),
                    "sort_order": task.get("sortOrder") or task.get("sort_order"),
                    "trigger_event": task.get("triggerEvent") or task.get("trigger_event"),
                    "use_ai_copy": _bool(task.get("useAiCopy", task.get("use_ai_copy"))),
                    "scene": task.get("scene") if isinstance(task.get("scene"), dict) else {},
                    "message_content": _platform_messages(task),
                }
            )
        model_input = {
            "latest_context": context,
            "pending_groups": groups,
            "authoritative_business_facts": _sop_platform_batch_business_facts_for_model(),
        }
        model_messages = [
            {"role": "system", "content": SOP_PLATFORM_BATCH_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(model_input, ensure_ascii=False)},
        ]
        deadline = time.monotonic() + max(5.0, float(self.settings.sop_platform_model_timeout_seconds))
        raw = await self._chat_batch_decision_json(model_messages, deadline=deadline)
        error = _batch_decision_error(raw, tasks=tasks, context=context)
        if error:
            raw = await self._chat_batch_decision_json(
                [
                    *model_messages,
                    {"role": "assistant", "content": json.dumps(raw, ensure_ascii=False)},
                    {
                        "role": "user",
                        "content": f"输出结构不合法：{error}。只修结构，严格按任务顺序返回小写 json。",
                    },
                ],
                deadline=deadline,
            )
            error = _batch_decision_error(raw, tasks=tasks, context=context)
        if error:
            raise RuntimeError(f"invalid_sop_platform_batch_model_output: {error}")
        return dict(raw)

    async def _chat_batch_decision_json(
        self,
        messages: list[dict[str, Any]],
        *,
        deadline: float,
    ) -> dict[str, Any]:
        primary_options = _sop_platform_decision_model_options(self.settings)
        if primary_options:
            primary_timeout = max(
                1.0,
                float(getattr(self.settings, "sop_platform_decision_primary_timeout_seconds", 12.0) or 12.0),
            )
            primary_deadline = min(deadline, time.monotonic() + primary_timeout)
            try:
                return await self.model_client.chat_json(
                    messages,
                    tier="balanced",
                    temperature=0.0,
                    deadline_monotonic=primary_deadline,
                    max_parallel_candidates=1,
                    **primary_options,
                )
            except Exception as exc:
                logger.warning("DeepSeek SOP sequence decision failed; trying configured GPT fallbacks: %s", exc)
        fallback_models = _sop_platform_decision_fallback_models(self.settings)
        fallback_options = {"model_names_override": fallback_models} if fallback_models else {}
        return await self.model_client.chat_json(
            messages,
            tier="balanced",
            temperature=0.0,
            deadline_monotonic=deadline,
            max_parallel_candidates=1,
            **fallback_options,
        )

    async def _transition_fact_audit(
        self,
        transition_text: str,
        *,
        selected_task: dict[str, Any],
        context: dict[str, Any],
    ) -> bool:
        if len(transition_text) > 80 or "http://" in transition_text.lower() or "https://" in transition_text.lower():
            return False
        audit_input = {
            "transition_text": transition_text,
            "conversation_timeline": context.get("conversation_timeline") or [],
            "selected_platform_messages": _platform_messages(selected_task),
            "safety_boundaries": _sop_platform_batch_business_facts_for_model(),
        }
        deadline = time.monotonic() + max(5.0, float(self.settings.sop_platform_model_timeout_seconds))
        try:
            result = await self.model_client.chat_json(
                [
                    {"role": "system", "content": SOP_TRANSITION_FACT_AUDIT_PROMPT},
                    {"role": "user", "content": json.dumps(audit_input, ensure_ascii=False)},
                ],
                tier="fast",
                temperature=0.0,
                deadline_monotonic=deadline,
                max_parallel_candidates=1,
            )
        except Exception:
            return False
        return isinstance(result, dict) and str(result.get("status") or "").strip().lower() == "pass"

    async def _consume_batch_without_send(
        self,
        tasks: list[dict[str, Any]],
        *,
        trigger_tasks: list[dict[str, Any]] | None = None,
        reason: str,
        batch_key: str,
        biz_type: str,
        batch_run_id: str,
        decision: dict[str, Any] | None = None,
        audit_context: dict[str, Any] | None = None,
        content_exhausted: bool | None = None,
    ) -> dict[str, Any]:
        trigger_tasks = trigger_tasks or []
        terminal_tasks = _dedupe_tasks([*tasks, *trigger_tasks])
        terminal_ids: list[str] = []
        audit = {
            "audit_schema_version": 2,
            "processing_mode": "customer_batch_sequence",
            "batch_run_id": batch_run_id,
            "batch_key": batch_key,
            "biz_type": biz_type,
            "batch_task_ids": [_task_id(task) for task in tasks],
            "compat_trigger_task_ids": [_task_id(task) for task in trigger_tasks],
            "decision": decision or {"selected_task_id": "", "evaluations": []},
            "reason": reason,
            "content_exhausted": content_exhausted,
            "context": _context_audit(audit_context or {}),
            "consume_results": [],
        }
        if reason in {"quiet_hours_no_replay", "deferred_behind_quiet_backlog"}:
            audit["quiet_hours_archive"] = _quiet_hours_no_replay_archive(
                terminal_tasks,
                settings=self.settings,
            )
            audit["quiet_hours_archive"]["no_replay"] = False
            audit["deferred_replay"] = {
                "status": "pending",
                "queued_at": utc_now_iso(),
                "source_reason": reason,
                "interval_seconds": _deferred_replay_interval_seconds(self.settings),
            }
        if self.settings.sop_platform_shadow_mode:
            for task in terminal_tasks:
                self._mark_local_task(task, status="shadow_no_send", send_payload=audit)
            return {
                "processed": True,
                "status": "shadow_no_send",
                "task_ids": [_task_id(task) for task in terminal_tasks],
                "terminal_task_ids": [_task_id(task) for task in terminal_tasks],
                "decision": audit["decision"],
            }
        run_keys = [f"run:{_task_run_id(task)}" if _task_run_id(task) else "run:unknown" for task in terminal_tasks]
        last_run_indexes = {key: index for index, key in enumerate(run_keys)}
        for index, task in enumerate(terminal_tasks):
            task_id = _task_id(task)
            exhaust_this_task = content_exhausted is True and last_run_indexes[run_keys[index]] == index
            response = await self.platform_client.consume(
                task_id=task_id,
                status=70,
                remark=reason,
                content_exhausted=True if exhaust_this_task else None,
            )
            _require_platform_status(response, 70)
            audit["consume_results"].append(
                {
                    "task_id": task_id,
                    "status": 70,
                    "remark": reason,
                    "content_exhausted": True if exhaust_this_task else None,
                    "response": response,
                }
            )
            self._mark_local_task(task, status="completed_without_send", send_payload=audit)
            self.repository.update_sop_event_status(f"platform_sop_task:{task_id}", status="platform_completed")
            terminal_ids.append(task_id)
        for task in terminal_tasks:
            self._mark_local_task(task, status="completed_without_send", send_payload=audit)
        return {
            "processed": True,
            "status": "completed_without_send",
            "task_ids": [_task_id(task) for task in terminal_tasks],
            "terminal_task_ids": terminal_ids,
            "decision": audit["decision"],
        }

    def _deferred_replay_records(self) -> list[dict[str, Any]]:
        if not bool(getattr(self.settings, "sop_platform_deferred_replay_enabled", True)):
            return []
        day_start, end_at = _deferred_replay_day_bounds()
        start_at = (
            datetime.fromisoformat(day_start) - timedelta(days=1)
        ).isoformat()
        loader = getattr(self.repository, "list_deferred_platform_sop_tasks", None)
        if not callable(loader):
            return []
        rows = loader(start_at=start_at, end_at=end_at, limit=5000)
        records: list[dict[str, Any]] = []
        day_start_epoch = _parse_epoch(day_start)
        for row in rows:
            marker = _deferred_replay_marker(row)
            if not marker and _parse_epoch(row.get("task_created_at")) < day_start_epoch:
                continue
            if not _is_deferred_replay_record(row):
                continue
            if not marker:
                payload = row.get("send_payload") if isinstance(row.get("send_payload"), dict) else {}
                self._mark_deferred_replay(
                    row,
                    status="pending",
                    marker={
                        "status": "pending",
                        "queued_at": utc_now_iso(),
                        "source_reason": str(payload.get("reason") or "quiet_hours_no_replay"),
                        "interval_seconds": _deferred_replay_interval_seconds(self.settings),
                        "bootstrap_legacy_archive": True,
                    },
                )
            records.append(row)
        return records

    def _refresh_deferred_replay_keys(self) -> list[dict[str, Any]]:
        records = self._deferred_replay_records()
        self._deferred_replay_keys = {
            _customer_batch_key(record.get("platform_task") or {})
            for record in records
            if _deferred_replay_record_status(record) in {"pending", "sending", "retry", "blocked"}
            and _customer_batch_key(record.get("platform_task") or {})
        }
        return records

    async def process_deferred_replays(self) -> int:
        if _in_configured_quiet_hours(settings=self.settings):
            return 0
        records = self._refresh_deferred_replay_keys()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            task = record.get("platform_task") if isinstance(record.get("platform_task"), dict) else {}
            key = _customer_batch_key(task)
            if key:
                grouped.setdefault(key, []).append(record)
        concurrency = max(
            1,
            int(getattr(self.settings, "sop_platform_deferred_replay_concurrency", 6) or 6),
        )
        semaphore = asyncio.Semaphore(concurrency)

        async def process_customer(batch_key: str, customer_records: list[dict[str, Any]]) -> int:
            async with semaphore:
                lock = self._locks.setdefault(f"customer-batch:{batch_key}", asyncio.Lock())
                async with lock:
                    return await self._process_deferred_customer_queue(customer_records)

        if not grouped:
            return 0
        return sum(
            await asyncio.gather(
                *(process_customer(key, value) for key, value in grouped.items())
            )
        )

    async def _process_deferred_customer_queue(self, records: list[dict[str, Any]]) -> int:
        ordered = sorted(
            records,
            key=lambda record: _task_batch_sort_key(record.get("platform_task") or {}),
        )
        active = [
            record
            for record in ordered
            if _deferred_replay_record_status(record) in {"pending", "retry", "sending", "blocked"}
        ]
        if not active:
            return 0
        if any(_deferred_replay_record_status(record) == "sending" for record in active):
            return 0
        first_status = _deferred_replay_record_status(active[0])
        if first_status in {"sending", "blocked"}:
            return 0
        last_sent_epoch = max(
            (
                _parse_epoch(record.get("sent_at"))
                for record in ordered
                if _deferred_replay_record_status(record) == "sent"
            ),
            default=0.0,
        )
        if last_sent_epoch and time.time() - last_sent_epoch < _deferred_replay_interval_seconds(self.settings):
            return 0
        selected = active[0]
        retry_at = _parse_epoch(_deferred_replay_marker(selected).get("next_retry_at"))
        if retry_at and retry_at > time.time():
            return 0
        task = selected.get("platform_task") if isinstance(selected.get("platform_task"), dict) else {}
        identity = _task_identity(task)
        if not all(identity.get(key) for key in ("corp_id", "customer_id", "external_userid", "user_id", "wechat")):
            self._mark_deferred_replay(selected, status="blocked", error="missing_identity")
            return 0
        try:
            status_response = await self.system_client.conversation_status(**identity)
        except Exception as exc:
            self._counters["deferred_replay_status_error"] += 1
            logger.warning("Deferred SOP replay status lookup failed: %s", exc)
            return 0
        status_data = status_response.get("data") if isinstance(status_response.get("data"), dict) else status_response
        ai_auto_reply = _conversation_ai_auto_reply(status_data if isinstance(status_data, dict) else {})
        if ai_auto_reply is None:
            return 0
        if ai_auto_reply is False:
            self._skip_deferred_replay_records(active, reason="human_takeover")
            self._counters["deferred_replay_human_skipped"] += len(active)
            return 0

        conversation = await self.system_client.conversation(**identity, limit=50)
        conversation_data = (
            conversation.get("data") if isinstance(conversation.get("data"), dict) else conversation
        )
        if not isinstance(conversation_data, dict):
            return 0
        relation = (
            conversation_data.get("customer_relation")
            if isinstance(conversation_data.get("customer_relation"), dict)
            else {}
        )
        if relation.get("is_deleted") is True or str(relation.get("status") or "").lower() == "deleted":
            self._skip_deferred_replay_records(active, reason="customer_relation_deleted")
            return 0
        timeline = _conversation_timeline(
            conversation_data.get("messages")
            if isinstance(conversation_data.get("messages"), list)
            else []
        )
        active_tasks = [
            record.get("platform_task") if isinstance(record.get("platform_task"), dict) else {}
            for record in active
        ]
        context = await self._load_batch_context(
            active_tasks[0],
            identity=identity,
            relation=relation,
            timeline=timeline,
        )
        context.update(
            {
                "management_mode": "ai",
                "management_source": "conversation_status.takeover.ai_auto_reply",
                "customer_opened": bool(_timeline_structure(timeline).get("customer_message_count")),
                "deferred_replay": True,
            }
        )
        if _is_same_day_unopened(active_tasks, timeline=timeline):
            decision = {
                "evaluations": [
                    {
                        "task_id": _task_id(active_tasks[0]),
                        "decision": "send",
                        "reason": "same_day_unopened_earliest_direct",
                        "evidence_refs": [f"task:{_task_id(active_tasks[0])}"],
                    }
                ],
                "selected_task_id": _task_id(active_tasks[0]),
                "transition_text": "",
                "decision_source": "same_day_unopened_direct",
            }
        else:
            decision = await self._decide_customer_batch(active_tasks, context=context)

        selected_id = str(decision.get("selected_task_id") or "").strip()
        if not selected_id:
            self._skip_deferred_replay_records(
                active,
                reason="all_due_groups_filtered",
                decision=decision,
            )
            return 0
        selected_index = next(
            (index for index, candidate in enumerate(active_tasks) if _task_id(candidate) == selected_id),
            -1,
        )
        if selected_index < 0:
            raise RuntimeError("deferred replay model selected an unknown task_id")
        selected = active[selected_index]
        task = active_tasks[selected_index]
        skipped_prefix = active[:selected_index]
        messages = _platform_messages(task)
        if not messages:
            self._mark_deferred_replay(selected, status="blocked", error="missing_original_messages")
            self._counters["deferred_replay_missing_content"] += 1
            return 0
        transition_text = str(decision.get("transition_text") or "").strip()
        if transition_text:
            passed = await self._transition_fact_audit(
                transition_text,
                selected_task=task,
                context=context,
            )
            if not passed:
                transition_text = ""
        final_messages = list(messages)
        if transition_text:
            final_messages = [{"type": "text", "order": 1, "content": {"text": transition_text}}] + [
                {**message, "order": index + 2}
                for index, message in enumerate(messages)
            ]
        local_task_id = str(selected.get("local_task_id") or "")
        platform_task_id = _task_id(task)
        marker = {
            **_deferred_replay_marker(selected),
            "status": "sending",
            "started_at": utc_now_iso(),
            "platform_task_id": platform_task_id,
            "decision": decision,
            "skipped_prefix_local_task_ids": [
                str(record.get("local_task_id") or "") for record in skipped_prefix
            ],
        }
        self._mark_deferred_replay(selected, status="sending", marker=marker)
        try:
            send_result = await self.system_client.send(
                **identity,
                plan_id=f"platform-sop-deferred-{platform_task_id}",
                task_id=f"platform-sop-deferred-send-{platform_task_id}",
                reply_messages=final_messages,
                source_channel="proactive_message",
                source_kind="sop_platform_deferred_replay",
                source_request_id=f"platform_sop_deferred:{platform_task_id}",
                source_task_id=local_task_id,
                source_context={
                    "sop_send_task_id": local_task_id,
                    "sop_event_id": str(selected.get("event_id") or ""),
                    "platform_task_id": platform_task_id,
                    "deferred_replay": True,
                },
                delivery_idempotency_key=f"sop_platform_deferred:{local_task_id}",
            )
        except Exception as exc:
            self._mark_deferred_replay(
                selected,
                status="retry",
                error=f"{type(exc).__name__}: {exc}",
                marker={
                    **marker,
                    "status": "retry",
                    "next_retry_at": (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat(),
                },
            )
            return 0
        send_data = send_result.get("data") if isinstance(send_result.get("data"), dict) else {}
        delivery_status = str(send_data.get("delivery_status") or "")
        if bool(send_data.get("callback_required")) and delivery_status in {
            "platform_accepted", "submission_unknown", "sending"
        }:
            self._mark_deferred_replay(selected, status="sending", marker=marker, send_response=send_result)
            return 1
        sent_at = utc_now_iso()
        self._mark_deferred_replay(
            selected,
            status="sent",
            marker={**marker, "status": "sent", "sent_at": sent_at},
            send_response=send_result,
            sent_at=sent_at,
        )
        self._skip_deferred_replay_records(
            skipped_prefix,
            reason="filtered_before_selected",
            decision=decision,
        )
        self._counters["deferred_replay_sent"] += 1
        return 1

    def _skip_deferred_replay_records(
        self,
        records: list[dict[str, Any]],
        *,
        reason: str,
        decision: dict[str, Any] | None = None,
    ) -> None:
        for record in records:
            self._mark_deferred_replay(
                record,
                status="skipped",
                error=reason,
                marker={
                    **_deferred_replay_marker(record),
                    "status": "skipped",
                    "skip_reason": reason,
                    "decision": decision or {},
                },
            )

    def _skip_deferred_replay_prefix_by_ids(
        self,
        local_task_ids: list[Any],
        *,
        decision: dict[str, Any] | None = None,
    ) -> None:
        records: list[dict[str, Any]] = []
        for raw_id in local_task_ids:
            local_task_id = str(raw_id or "").strip()
            if not local_task_id:
                continue
            task = self.repository.get_sop_send_task(local_task_id)
            if not task:
                continue
            records.append(
                {
                    "local_task_id": local_task_id,
                    "send_payload": task.get("send_payload") or {},
                    "task_status": task.get("status") or "",
                    "sent_at": task.get("sent_at") or "",
                }
            )
        self._skip_deferred_replay_records(
            records,
            reason="filtered_before_selected",
            decision=decision,
        )

    def _mark_deferred_replay(
        self,
        record: dict[str, Any],
        *,
        status: str,
        error: str = "",
        marker: dict[str, Any] | None = None,
        send_response: dict[str, Any] | None = None,
        sent_at: str = "",
    ) -> None:
        payload = record.get("send_payload") if isinstance(record.get("send_payload"), dict) else {}
        next_marker = marker or {**_deferred_replay_marker(record), "status": status}
        next_payload = {**payload, "deferred_replay": next_marker}
        task_status = {
            "sent": "sent",
            "sending": "deferred_replay_sending",
            "retry": "deferred_replay_retry",
        }.get(status, "completed_without_send")
        self.repository.update_sop_send_task(
            str(record.get("local_task_id") or ""),
            status=task_status,
            send_payload=next_payload,
            send_response=send_response,
            error=error,
            sent_at=sent_at,
        )
        record["task_status"] = task_status
        record["send_payload"] = next_payload
        if sent_at:
            record["sent_at"] = sent_at

    async def _send_selected_batch_task(
        self,
        selected_task: dict[str, Any],
        *,
        skipped_prefix: list[dict[str, Any]],
        trigger_tasks: list[dict[str, Any]],
        transition_text: str,
        decision: dict[str, Any],
        context: dict[str, Any],
        identity: dict[str, str],
        batch_key: str,
        biz_type: str,
        batch_run_id: str,
        batch_task_ids: list[str],
    ) -> dict[str, Any]:
        selected_id = _task_id(selected_task)
        quiet_hours = _quiet_hours_base_summary(selected_task, settings=self.settings)
        if _in_configured_quiet_hours(settings=self.settings) or quiet_hours.get("in_quiet_hours"):
            quiet_tasks = [*skipped_prefix, selected_task]
            self._counters["quiet_consumed_without_replay"] += len(quiet_tasks)
            quiet_hours.update({"blocked": True, "reason": "quiet_hours_no_replay"})
            return await self._consume_batch_without_send(
                quiet_tasks,
                trigger_tasks=trigger_tasks,
                reason="quiet_hours_no_replay",
                batch_key=batch_key,
                biz_type=biz_type,
                batch_run_id=batch_run_id,
                decision=_quiet_hours_no_replay_decision(quiet_tasks),
                audit_context={**context, "quiet_hours": quiet_hours},
            )
        original_messages = _platform_messages(selected_task)
        if not original_messages:
            raise RuntimeError("selected platform task has no sendable original messages")
        final_messages = list(original_messages)
        if transition_text:
            final_messages = [{"type": "text", "order": 1, "content": {"text": transition_text}}] + [
                {**message, "order": index + 2}
                for index, message in enumerate(original_messages)
            ]
        skipped_ids = [_task_id(task) for task in skipped_prefix]
        trigger_ids = [
            task_id
            for task_id in (_task_id(task) for task in trigger_tasks)
            if task_id and task_id not in {*skipped_ids, selected_id}
        ]
        audit = {
            "audit_schema_version": 2,
            "processing_mode": "customer_batch_sequence",
            "batch_run_id": batch_run_id,
            "batch_key": batch_key,
            "biz_type": biz_type,
            "batch_task_ids": batch_task_ids,
            "decision": decision,
            "original_messages": original_messages,
            "transition_text": transition_text,
            "final_messages": final_messages,
            "context": _context_audit(context),
            "skipped_prefix_task_ids": skipped_ids,
            "compat_trigger_task_ids": trigger_ids,
            "consume_results": [],
        }
        if self.settings.sop_platform_shadow_mode:
            for task in skipped_prefix:
                self._mark_local_task(task, status="shadow_no_send", send_payload=audit)
            for task in trigger_tasks:
                self._mark_local_task(task, status="shadow_no_send", send_payload=audit)
            self._mark_local_task(selected_task, status="shadow_send", send_payload=audit)
            return {
                "processed": True,
                "status": "shadow_send",
                "task_id": selected_id,
                "task_ids": [*skipped_ids, selected_id, *trigger_ids],
                "terminal_task_ids": [*skipped_ids, selected_id, *trigger_ids],
                "decision": decision,
                "reply_messages": final_messages,
            }

        claimed = await self.platform_client.consume(task_id=selected_id, status=20)
        _require_platform_status(claimed, 20)
        self.repository.update_sop_event_status(f"platform_sop_task:{selected_id}", status="platform_processing")
        local_task = self.repository.get_sop_send_task_by_idempotency_key(f"platform-sop:{selected_id}")
        local_task_id = str(local_task.get("id") or "")
        self.repository.update_sop_send_task(local_task_id, status="sending", send_payload=audit)
        send_payload = {
            **identity,
            "plan_id": f"platform-sop-{selected_id}",
            "task_id": f"platform-sop-send-{selected_id}",
            "reply_messages": final_messages,
        }
        for task_id in [*skipped_ids, selected_id, *trigger_ids]:
            self._reserved_prefix_ids.add(task_id)
        try:
            send_result = await self.system_client.send(
                **send_payload,
                source_channel="proactive_message",
                source_kind="sop_platform_task",
                source_request_id=f"platform_sop_task:{selected_id}",
                source_task_id=local_task_id,
                source_context={
                    "sop_send_task_id": local_task_id,
                    "sop_event_id": f"platform_sop_task:{selected_id}",
                    "platform_task_id": selected_id,
                    "skipped_prefix_task_ids": skipped_ids,
                    "compat_trigger_task_ids": trigger_ids,
                    "batch_key": batch_key,
                    "biz_type": biz_type,
                },
                delivery_idempotency_key=f"sop_platform_task:{local_task_id}",
            )
        except Exception as exc:
            return self._defer_batch_send_retry(
                selected_task_id=selected_id,
                local_task_id=local_task_id,
                audit=audit,
                error=exc,
            )
        send_data = send_result.get("data") if isinstance(send_result.get("data"), dict) else {}
        delivery_status = str(send_data.get("delivery_status") or "")
        if bool(send_data.get("callback_required")) and delivery_status in {
            "platform_accepted",
            "submission_unknown",
            "sending",
        }:
            self.repository.update_sop_send_task(
                local_task_id,
                status="sending",
                send_payload=audit,
                send_response=send_result,
                error="",
            )
            self.repository.update_sop_event_status(
                f"platform_sop_task:{selected_id}",
                status="platform_delivery_pending",
            )
            return {
                "processed": True,
                "status": "accepted",
                "task_id": selected_id,
                "task_ids": [*skipped_ids, selected_id],
                "terminal_task_ids": [],
                "send_response": send_result,
            }

        if _in_configured_quiet_hours(settings=self.settings):
            self.repository.update_sop_send_task(
                local_task_id,
                status="sent",
                send_payload=audit,
                send_response=send_result,
                sent_at=utc_now_iso(),
            )
            self.repository.update_sop_event_status(
                f"platform_sop_task:{selected_id}",
                status="platform_batch_consume_pending",
            )
            return {
                "processed": True,
                "status": "sent_consume_deferred",
                "task_id": selected_id,
                "task_ids": [*skipped_ids, selected_id],
                "terminal_task_ids": [],
                "reply_messages": final_messages,
                "send_response": send_result,
            }

        terminal_ids = await self._finalize_batch_prefix(
            selected_task_id=selected_id,
            skipped_prefix_task_ids=skipped_ids,
            compat_trigger_task_ids=trigger_ids,
            audit=audit,
        )
        self.repository.update_sop_send_task(
            local_task_id,
            status="sent",
            send_payload=audit,
            send_response=send_result,
            sent_at=utc_now_iso(),
        )
        return {
            "processed": True,
            "status": "sent",
            "task_id": selected_id,
            "task_ids": [*skipped_ids, selected_id],
            "terminal_task_ids": terminal_ids,
            "reply_messages": final_messages,
            "send_response": send_result,
        }

    def _defer_batch_send_retry(
        self,
        *,
        selected_task_id: str,
        local_task_id: str,
        audit: dict[str, Any],
        error: Exception,
    ) -> dict[str, Any]:
        previous = audit.get("delivery_retry") if isinstance(audit.get("delivery_retry"), dict) else {}
        attempt_count = max(0, int(previous.get("attempt_count") or 0)) + 1
        delay_seconds = 0 if attempt_count == 1 else min(300, 15 * (2 ** min(attempt_count - 2, 5)))
        retry_state = {
            "attempt_count": attempt_count,
            "next_retry_at": (datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)).isoformat(),
            "last_failure": _retryable_delivery_failure(error),
            "last_error": f"{type(error).__name__}: {error}",
        }
        retry_audit = {**audit, "delivery_retry": retry_state}
        self.repository.update_sop_send_task(
            local_task_id,
            status="processing_retry",
            send_payload=retry_audit,
            error=retry_state["last_error"],
        )
        self.repository.update_sop_event_status(
            f"platform_sop_task:{selected_task_id}",
            status="platform_batch_send_retry",
            error=retry_state["last_error"],
        )
        self._counters["send_retry_deferred"] += 1
        return {
            "processed": False,
            "status": "processing_retry",
            "task_id": selected_task_id,
            "terminal_task_ids": [],
            "retry": retry_state,
            "error": retry_state["last_error"],
        }

    async def _retry_batch_send(
        self,
        platform_task: dict[str, Any],
        *,
        local_task: dict[str, Any],
    ) -> dict[str, Any]:
        selected_id = _task_id(platform_task)
        identity = _task_identity(platform_task)
        audit = local_task.get("send_payload") if isinstance(local_task.get("send_payload"), dict) else {}
        final_messages = audit.get("final_messages") if isinstance(audit.get("final_messages"), list) else []
        skipped_ids = [
            str(value).strip()
            for value in audit.get("skipped_prefix_task_ids", [])
            if str(value).strip()
        ] if isinstance(audit.get("skipped_prefix_task_ids"), list) else []
        trigger_ids = [
            str(value).strip()
            for value in audit.get("compat_trigger_task_ids", [])
            if str(value).strip()
        ] if isinstance(audit.get("compat_trigger_task_ids"), list) else []
        local_task_id = str(local_task.get("id") or "")
        if not final_messages or not local_task_id:
            raise RuntimeError("batch retry is missing immutable send payload")
        retry_state = audit.get("delivery_retry") if isinstance(audit.get("delivery_retry"), dict) else {}
        next_retry_epoch = _parse_epoch(retry_state.get("next_retry_at"))
        if next_retry_epoch and next_retry_epoch > time.time():
            return {
                "processed": False,
                "status": "retry_waiting",
                "task_id": selected_id,
                "retry": retry_state,
            }
        for task_id in [*skipped_ids, selected_id, *trigger_ids]:
            self._reserved_prefix_ids.add(task_id)
        try:
            send_result = await self.system_client.send(
                **identity,
                plan_id=f"platform-sop-{selected_id}",
                task_id=f"platform-sop-send-{selected_id}",
                reply_messages=final_messages,
                source_channel="proactive_message",
                source_kind="sop_platform_task",
                source_request_id=f"platform_sop_task:{selected_id}",
                source_task_id=local_task_id,
                source_context={
                    "sop_send_task_id": local_task_id,
                    "sop_event_id": f"platform_sop_task:{selected_id}",
                    "platform_task_id": selected_id,
                    "skipped_prefix_task_ids": skipped_ids,
                    "compat_trigger_task_ids": trigger_ids,
                    "batch_key": str(audit.get("batch_key") or ""),
                    "biz_type": str(audit.get("biz_type") or ""),
                },
                delivery_idempotency_key=f"sop_platform_task:{local_task_id}",
            )
        except Exception as exc:
            return self._defer_batch_send_retry(
                selected_task_id=selected_id,
                local_task_id=local_task_id,
                audit=audit,
                error=exc,
            )
        send_data = send_result.get("data") if isinstance(send_result.get("data"), dict) else {}
        delivery_status = str(send_data.get("delivery_status") or "")
        if bool(send_data.get("callback_required")) and delivery_status in {
            "platform_accepted",
            "submission_unknown",
            "sending",
        }:
            self.repository.update_sop_send_task(
                local_task_id,
                status="sending",
                send_payload=audit,
                send_response=send_result,
                error="",
            )
            self.repository.update_sop_event_status(
                f"platform_sop_task:{selected_id}",
                status="platform_delivery_pending",
                error="",
            )
            return {"processed": True, "status": "accepted", "task_id": selected_id}
        if _in_configured_quiet_hours(settings=self.settings):
            self.repository.update_sop_send_task(
                local_task_id,
                status="sent",
                send_payload=audit,
                send_response=send_result,
                error="",
                sent_at=utc_now_iso(),
            )
            self.repository.update_sop_event_status(
                f"platform_sop_task:{selected_id}",
                status="platform_batch_consume_pending",
                error="",
            )
            return {
                "processed": True,
                "status": "sent_consume_deferred",
                "task_id": selected_id,
                "terminal_task_ids": [],
            }

        terminal_ids = await self._finalize_batch_prefix(
            selected_task_id=selected_id,
            skipped_prefix_task_ids=skipped_ids,
            compat_trigger_task_ids=trigger_ids,
            audit=audit,
        )
        self.repository.update_sop_send_task(
            local_task_id,
            status="sent",
            send_payload={
                **audit,
                "delivery_retry": {**retry_state, "resolved_at": utc_now_iso()},
            },
            send_response=send_result,
            error="",
            sent_at=utc_now_iso(),
        )
        return {
            "processed": True,
            "status": "sent",
            "task_id": selected_id,
            "terminal_task_ids": terminal_ids,
        }

    async def _recover_interrupted_batch_send(
        self,
        platform_task: dict[str, Any],
        *,
        local_task: dict[str, Any],
    ) -> dict[str, Any]:
        """Close the crash window between status=20, delivery, and status=30."""
        selected_id = _task_id(platform_task)
        identity = _task_identity(platform_task)
        audit = local_task.get("send_payload") if isinstance(local_task.get("send_payload"), dict) else {}
        final_messages = audit.get("final_messages") if isinstance(audit.get("final_messages"), list) else []
        local_task_id = str(local_task.get("id") or "")
        if not selected_id or not local_task_id or not final_messages:
            raise RuntimeError("interrupted batch send recovery is missing immutable send facts")
        send_payload = {
            **identity,
            "plan_id": f"platform-sop-{selected_id}",
            "task_id": f"platform-sop-send-{selected_id}",
            "reply_messages": final_messages,
        }
        delivery_key = f"sop_platform_task:{local_task_id}"
        dispatch_loader = getattr(self.system_client, "delivery_dispatch", None)
        dispatch = dispatch_loader(delivery_key) if callable(dispatch_loader) else {}
        dispatch_status = str(dispatch.get("status") or "")
        if dispatch_status in {"platform_accepted", "send_succeeded"}:
            return await self._complete_recovered_batch_send(
                selected_id=selected_id,
                local_task_id=local_task_id,
                audit=audit,
                recovery={
                    "status": "confirmed_from_dispatch",
                    "checked_at": utc_now_iso(),
                    "dispatch_id": str(dispatch.get("id") or ""),
                    "dispatch_status": dispatch_status,
                    "system_msgid": str(dispatch.get("system_msgid") or ""),
                },
            )
        if dispatch_status in {"created", "submitting", "submission_unknown", "sending", "submission_failed"}:
            return await self._retry_batch_send(platform_task, local_task=local_task)
        existing_delivery = await self._existing_platform_delivery(
            identity=identity,
            send_payload=send_payload,
        )
        if existing_delivery.get("error"):
            return {
                "processed": False,
                "status": "recovery_waiting",
                "task_id": selected_id,
                "reason": "conversation_delivery_check_failed",
                "delivery_recovery": existing_delivery,
            }
        if not existing_delivery.get("found"):
            return await self._retry_batch_send(platform_task, local_task=local_task)

        recovery = {
            "status": "confirmed_from_conversation",
            "checked_at": utc_now_iso(),
            **existing_delivery,
        }
        return await self._complete_recovered_batch_send(
            selected_id=selected_id,
            local_task_id=local_task_id,
            audit=audit,
            recovery=recovery,
        )

    async def _complete_recovered_batch_send(
        self,
        *,
        selected_id: str,
        local_task_id: str,
        audit: dict[str, Any],
        recovery: dict[str, Any],
    ) -> dict[str, Any]:
        recovered_audit = {**audit, "delivery_recovery": recovery}
        skipped_ids = [
            str(value).strip()
            for value in recovered_audit.get("skipped_prefix_task_ids", [])
            if str(value).strip()
        ] if isinstance(recovered_audit.get("skipped_prefix_task_ids"), list) else []
        trigger_ids = [
            str(value).strip()
            for value in recovered_audit.get("compat_trigger_task_ids", [])
            if str(value).strip()
        ] if isinstance(recovered_audit.get("compat_trigger_task_ids"), list) else []
        terminal_ids = await self._finalize_batch_prefix(
            selected_task_id=selected_id,
            skipped_prefix_task_ids=skipped_ids,
            compat_trigger_task_ids=trigger_ids,
            audit=recovered_audit,
        )
        recovered_response = {
            "code": 0,
            "msg": "delivery_confirmed_after_interrupted_send",
            "data": {
                "send_status": str(recovery.get("status") or "confirmed_delivery"),
                "delivery_status": "delivered",
                "callback_required": False,
                "delivery_recovery": recovery,
            },
        }
        self.repository.update_sop_send_task(
            local_task_id,
            status="sent",
            send_payload=recovered_audit,
            send_response=recovered_response,
            error="",
            sent_at=utc_now_iso(),
        )
        self.repository.update_sop_event_status(
            f"platform_sop_task:{selected_id}",
            status="platform_completed",
            error="",
        )
        self._counters[str(recovery.get("status") or "send_recovered")] += 1
        return {
            "processed": True,
            "status": "sent",
            "task_id": selected_id,
            "terminal_task_ids": terminal_ids,
            "delivery_recovery": recovery,
        }

    def _restore_reserved_prefix_ids(self) -> None:
        events = self.repository.list_sop_events_by_statuses(
            [
                "platform_processing",
                "platform_batch_send_retry",
                "platform_delivery_pending",
                "platform_batch_consume_pending",
            ],
            limit=500,
            event_type="platform_sop_task",
        )
        for event in events:
            event_id = str(event.get("event_id") or "")
            selected_id = event_id.rsplit(":", 1)[-1]
            local_task = self.repository.get_sop_send_task_by_idempotency_key(f"platform-sop:{selected_id}")
            audit = local_task.get("send_payload") if isinstance(local_task.get("send_payload"), dict) else {}
            self._reserved_prefix_ids.add(selected_id)
            for value in audit.get("skipped_prefix_task_ids", []) if isinstance(audit.get("skipped_prefix_task_ids"), list) else []:
                if str(value).strip():
                    self._reserved_prefix_ids.add(str(value).strip())
            for value in audit.get("compat_trigger_task_ids", []) if isinstance(audit.get("compat_trigger_task_ids"), list) else []:
                if str(value).strip():
                    self._reserved_prefix_ids.add(str(value).strip())

    async def _finalize_batch_prefix(
        self,
        *,
        selected_task_id: str,
        skipped_prefix_task_ids: list[str],
        compat_trigger_task_ids: list[str] | None = None,
        audit: dict[str, Any] | None = None,
    ) -> list[str]:
        terminal_ids: list[str] = []
        consume_results = audit.setdefault("consume_results", []) if isinstance(audit, dict) else []
        if not isinstance(consume_results, list):
            consume_results = []
            if isinstance(audit, dict):
                audit["consume_results"] = consume_results
        for task_id in skipped_prefix_task_ids:
            response = await self.platform_client.consume(
                task_id=task_id,
                status=70,
                remark="superseded_by_later_sendable_group",
            )
            _require_platform_status(response, 70)
            consume_results[:] = [
                item
                for item in consume_results
                if not isinstance(item, dict) or str(item.get("task_id") or "") != task_id
            ]
            consume_results.append(
                {
                    "task_id": task_id,
                    "status": 70,
                    "remark": "superseded_by_later_sendable_group",
                    "response": response,
                }
            )
            task = self._platform_task_from_local(task_id)
            if task:
                self._mark_local_task(task, status="completed_without_send", send_payload=audit or {})
            self.repository.update_sop_event_status(f"platform_sop_task:{task_id}", status="platform_completed")
            terminal_ids.append(task_id)
        response = await self.platform_client.consume(task_id=selected_task_id, status=30)
        _require_platform_status(response, 30)
        consume_results[:] = [
            item
            for item in consume_results
            if not isinstance(item, dict) or str(item.get("task_id") or "") != selected_task_id
        ]
        consume_results.append(
            {"task_id": selected_task_id, "status": 30, "remark": "", "response": response}
        )
        self.repository.update_sop_event_status(
            f"platform_sop_task:{selected_task_id}",
            status="platform_completed",
        )
        terminal_ids.append(selected_task_id)
        trigger_ids = compat_trigger_task_ids
        if trigger_ids is None and isinstance(audit, dict):
            raw_trigger_ids = audit.get("compat_trigger_task_ids")
            trigger_ids = raw_trigger_ids if isinstance(raw_trigger_ids, list) else []
        for raw_task_id in trigger_ids or []:
            task_id = str(raw_task_id or "").strip()
            if not task_id or task_id in terminal_ids:
                continue
            response = await self.platform_client.consume(
                task_id=task_id,
                status=70,
                remark="content_resolved_from_store_visit_queue",
            )
            _require_platform_status(response, 70)
            consume_results.append(
                {
                    "task_id": task_id,
                    "status": 70,
                    "remark": "content_resolved_from_store_visit_queue",
                    "response": response,
                }
            )
            self.repository.update_sop_event_status(
                f"platform_sop_task:{task_id}",
                status="platform_completed",
            )
            terminal_ids.append(task_id)
        reserved_prefix_ids = getattr(self, "_reserved_prefix_ids", None)
        if isinstance(reserved_prefix_ids, set):
            for task_id in terminal_ids:
                reserved_prefix_ids.discard(task_id)
        return terminal_ids

    def _platform_task_from_local(self, task_id: str) -> dict[str, Any]:
        event = self.repository.get_sop_event(f"platform_sop_task:{task_id}")
        payload = event.get("raw_payload") if isinstance(event.get("raw_payload"), dict) else {}
        return payload.get("platform_task") if isinstance(payload.get("platform_task"), dict) else {}

    def _mark_local_task(self, platform_task: dict[str, Any], *, status: str, send_payload: dict[str, Any]) -> None:
        task_id = _task_id(platform_task)
        local_task = self.repository.get_sop_send_task_by_idempotency_key(f"platform-sop:{task_id}")
        local_task_id = str(local_task.get("id") or "")
        if local_task_id:
            self.repository.update_sop_send_task(local_task_id, status=status, send_payload=send_payload)

    def runtime_status(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "queue_depth": self._queue.qsize(),
            "queue_capacity": self._queue.maxsize,
            "queued_count": len(self._queued_ids),
            "in_flight_count": len(self._in_flight_ids),
            "reserved_prefix_count": len(self._reserved_prefix_ids),
            "processing_mode": "customer_batch_sequence",
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
        external_userid: str = "",
        wechat: str = "",
        date_from: str = "",
        date_to: str = "",
        refresh_platform: bool = True,
    ) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit or 100), 500))
        platform_page: dict[str, Any] = {"items": [], "total": 0}
        platform_error = ""
        if refresh_platform:
            try:
                online_page, store_visit_page = await asyncio.gather(
                    self.platform_client.pending(limit=safe_limit),
                    self.platform_client.store_visit_pending(limit=safe_limit),
                )
                online_items = online_page.get("items") if isinstance(online_page.get("items"), list) else []
                store_visit_items = (
                    store_visit_page.get("items") if isinstance(store_visit_page.get("items"), list) else []
                )
                platform_page = {
                    "items": [
                        *({**item, "_aics_biz_type": "online_service"} for item in online_items),
                        *({**item, "_aics_biz_type": "store_visit"} for item in store_visit_items),
                    ],
                    "total": int(online_page.get("total") or 0) + int(store_visit_page.get("total") or 0),
                    "online_service_total": int(online_page.get("total") or 0),
                    "store_visit_total": int(store_visit_page.get("total") or 0),
                }
            except Exception as exc:
                platform_error = f"{type(exc).__name__}: {exc}"
        local_records = self.repository.list_platform_sop_task_records(
            limit=safe_limit,
            task_id=task_id,
            customer_id=customer_id,
            external_userid=external_userid,
            wechat=wechat,
            date_from=_admin_date_filter_iso(date_from),
            date_to=_admin_date_filter_iso(date_to),
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
                "recovery": summary["recovery"],
            },
            "platform": {
                "refreshed": refresh_platform,
                "error": platform_error,
                "online_service_total": int(platform_page.get("online_service_total") or 0),
                "store_visit_total": int(platform_page.get("store_visit_total") or 0),
            },
            "worker": self.runtime_status(),
            "items": items,
        }

    async def admin_run_logs(
        self,
        *,
        limit: int = 100,
        status: str = "",
        log_version: str = "",
        biz_type: str = "",
        task_id: str = "",
        customer_id: str = "",
        external_userid: str = "",
        wechat: str = "",
        query: str = "",
        date_from: str = "",
        date_to: str = "",
        refresh_platform: bool = True,
    ) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit or 100), 500))
        task_page = await self.admin_task_logs(
            limit=500,
            task_id=task_id,
            customer_id=customer_id,
            external_userid=external_userid,
            wechat=wechat,
            date_from=date_from,
            date_to=date_to,
            refresh_platform=refresh_platform,
        )
        task_items = task_page.get("items") if isinstance(task_page.get("items"), list) else []
        runs = _merge_platform_task_runs(task_items)
        clean_external = str(external_userid or "").strip().lower()
        clean_wechat = str(wechat or "").strip().lower()
        clean_query = str(query or "").strip().lower()
        from_epoch = _parse_epoch(date_from)
        to_epoch = _parse_epoch(date_to)
        if status:
            runs = [run for run in runs if run["status"] == str(status).strip()]
        if log_version:
            runs = [run for run in runs if run["log_version"] == str(log_version).strip()]
        if biz_type:
            runs = [run for run in runs if run["biz_type"] == str(biz_type).strip()]
        if clean_external:
            runs = [run for run in runs if str(run.get("external_userid") or "").lower() == clean_external]
        if clean_wechat:
            runs = [run for run in runs if str(run.get("wechat") or "").lower() == clean_wechat]
        if clean_query:
            runs = [run for run in runs if clean_query in _platform_run_search_text(run)]
        if from_epoch:
            runs = [run for run in runs if _parse_epoch(run.get("occurred_at")) >= from_epoch]
        if to_epoch:
            runs = [run for run in runs if _parse_epoch(run.get("occurred_at")) <= to_epoch]
        runs = runs[:safe_limit]
        summary = Counter(run["status"] for run in runs)
        versions = Counter(run["log_version"] for run in runs)
        return {
            "schema_version": "sop_platform_run_view_v2",
            "summary": {
                "visible_total": len(runs),
                "pending": summary["pending"],
                "processing": summary["processing"],
                "delivery_pending": summary["delivery_pending"],
                "consume_pending": summary["consume_pending"],
                "completed": summary["completed"],
                "no_send": summary["no_send"],
                "exception": summary["exception"],
                "batch_v2": versions["batch_v2"],
                "legacy_single": versions["legacy_single"],
                "platform_pending": versions["platform_pending"],
            },
            "platform": task_page.get("platform") or {},
            "worker": task_page.get("worker") or {},
            "runs": runs,
        }

    async def admin_resend_task(self, task_id: str) -> dict[str, Any]:
        clean_task_id = str(task_id or "").strip()
        if not clean_task_id:
            raise ValueError("task_id is required")
        lock = self._locks.setdefault(clean_task_id, asyncio.Lock())
        async with lock:
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
        preflight_reason = _task_preflight_no_send_reason(
            platform_task,
            identity=identity,
            settings=self.settings,
        )
        if preflight_reason and preflight_reason != "pre_cutover_task":
            raise RuntimeError(f"task cannot be resent: {preflight_reason}")

        await self._manual_resend_relation_guard(identity)
        messages = _manual_resend_messages(local_task, platform_task)
        decision_reason = "manual_resend"
        context: dict[str, Any] = {
            "source": "manual_resend",
            "original_event_status": event_status,
            "original_task_status": task_status,
        }
        if not messages:
            context = await self._load_context(platform_task, identity=identity)
            decision = await self._decide(platform_task, context=context)
            if decision["decision"] != "send" or not decision["reply_messages"]:
                raise RuntimeError(f"manual resend produced no sendable content: {decision.get('reason') or 'no_send'}")
            messages = decision["reply_messages"]
            decision_reason = f"manual_resend_ai_copy:{decision.get('reason') or ''}"

        send_payload = {
            **identity,
            "plan_id": f"platform-sop-{task_id}",
            "task_id": f"platform-sop-send-{task_id}",
            "reply_messages": messages,
        }
        audit_payload = {
            "decision": {"decision": "send", "reason": decision_reason, "reply_messages": messages},
            "request": send_payload,
            "context": _context_audit(context),
        }
        self.repository.update_sop_send_task(str(local_task.get("id") or ""), status="sending", send_payload=audit_payload)
        started = time.perf_counter()
        send_result = await self.system_client.send(
            **send_payload,
            source_channel="proactive_message",
            source_kind="sop_platform_task",
            source_request_id=event_id,
            source_task_id=str(local_task.get("id") or ""),
            source_context={
                "sop_send_task_id": str(local_task.get("id") or ""),
                "sop_event_id": event_id,
                "platform_task_id": task_id,
            },
            delivery_idempotency_key=f"sop_platform_task:{local_task.get('id')}",
        )
        self._observe("send", time.perf_counter() - started)
        send_data = send_result.get("data") if isinstance(send_result.get("data"), dict) else {}
        delivery_status = str(send_data.get("delivery_status") or "")
        if bool(send_data.get("callback_required")) and delivery_status in {
            "platform_accepted",
            "submission_unknown",
            "sending",
        }:
            self.repository.update_sop_event_status(event_id, status="platform_delivery_pending")
            self.repository.update_sop_send_task(
                str(local_task.get("id") or ""),
                status="sending",
                send_payload=audit_payload,
                send_response=send_result,
                error="",
            )
            return {
                "processed": True,
                "status": "accepted",
                "task_id": task_id,
                "reply_messages": messages,
                "send_response": send_result,
            }
        send_status = str(send_data.get("send_status") or send_result.get("msg") or "")
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
        if event_status != "platform_completed" and not self.settings.sop_platform_shadow_mode:
            self.repository.update_sop_event_status(event_id, status="platform_complete_pending")
            completed = await self.platform_client.consume(task_id=task_id, status=30)
            _require_platform_status(completed, 30)
            self.repository.update_sop_event_status(event_id, status="platform_completed")
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

    def _observe(self, name: str, elapsed_seconds: float) -> None:
        values = self._timings.get(name)
        if values is not None:
            values.append(max(0.0, float(elapsed_seconds)) * 1000)

    def _record_result(self, result: dict[str, Any]) -> None:
        status = str(result.get("status") or "unknown")
        terminal_task_ids = [
            str(value).strip()
            for value in result.get("terminal_task_ids", [])
            if str(value).strip()
        ] if isinstance(result.get("terminal_task_ids"), list) else []
        for task_id in terminal_task_ids:
            self._remember_terminal(task_id)
        if status in {"sent", "completed_without_send", "platform_completed", "shadow_send", "shadow_no_send"}:
            self._remember_terminal(str(result.get("task_id") or ""))
        if status == "sent":
            self._counters["sent"] += 1
        elif status in {"completed_without_send", "shadow_no_send"}:
            self._counters["no_send"] += 1
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
            self._terminal_ids.discard(self._terminal_order.popleft())

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
        if current_status == "platform_completed" and recovery_status != "platform_processing":
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
        local_audit = local_task.get("send_payload") if isinstance(local_task.get("send_payload"), dict) else {}
        if (
            recovery_status == "platform_processing"
            and local_status == "sending"
            and str(local_audit.get("processing_mode") or "") == "customer_batch_sequence"
        ):
            return await self._recover_interrupted_batch_send(platform_task, local_task=local_task)
        if recovery_status == "platform_batch_send_retry":
            return await self._retry_batch_send(platform_task, local_task=local_task)
        if recovery_status == "platform_batch_consume_pending":
            audit = local_task.get("send_payload") if isinstance(local_task.get("send_payload"), dict) else {}
            skipped_ids = [
                str(value).strip()
                for value in audit.get("skipped_prefix_task_ids", [])
                if str(value).strip()
            ] if isinstance(audit.get("skipped_prefix_task_ids"), list) else []
            terminal_ids = await self._finalize_batch_prefix(
                selected_task_id=task_id,
                skipped_prefix_task_ids=skipped_ids,
                audit=audit,
            )
            self.repository.update_sop_event_status(event_id, status="platform_completed", error="")
            return {
                "processed": True,
                "status": "sent",
                "task_id": task_id,
                "task_ids": [*skipped_ids, task_id],
                "terminal_task_ids": terminal_ids,
            }
        if (
            recovery_status != "platform_send_uncertain"
            and (local_status == "sending" or current_status == "platform_delivery_pending")
        ):
            return {
                "processed": False,
                "status": "accepted",
                "task_id": task_id,
                "reason": "awaiting_message_delivery_callback",
            }
        duplicate_reason = _duplicate_platform_task_reason(
            self.repository,
            local_task=local_task,
            task_id=task_id,
        )
        if duplicate_reason:
            decision = {"decision": "no_send", "reason": duplicate_reason, "reply_messages": []}
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
                send_payload={"decision": decision, "context": context},
            )
            self.repository.update_sop_event_status(event_id, status="platform_complete_pending")
            completed = await self.platform_client.consume(task_id=task_id, status=30)
            _require_platform_status(completed, 30)
            self.repository.update_sop_event_status(event_id, status="platform_completed")
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

        if not self.settings.sop_platform_shadow_mode and recovery_status == "platform_complete_pending":
            started = time.perf_counter()
            completed = await self.platform_client.consume(task_id=task_id, status=30)
            self._observe("claim", time.perf_counter() - started)
            _require_platform_status(completed, 30)
            self.repository.update_sop_event_status(event_id, status="platform_completed")
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
        )
        quiet_hours: dict[str, Any] = {}
        if not preflight_reason:
            quiet_hours = await self._quiet_hours_guard(platform_task, identity=identity)
            if quiet_hours.get("blocked"):
                preflight_reason = str(quiet_hours.get("reason") or "quiet_hours_blocked")
        if self.settings.sop_platform_shadow_mode and preflight_reason:
            decision = {"decision": "no_send", "reason": preflight_reason, "reply_messages": []}
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

        use_ai_copy = _bool(platform_task.get("useAiCopy", platform_task.get("use_ai_copy")))
        processing_status = "platform_judging"
        self.repository.update_sop_event_status(event_id, status=processing_status)
        self.repository.update_sop_send_task(
            str(local_task.get("id") or ""),
            status="judging",
            send_payload={
                "platform_task_id": task_id,
                "phase": "loading_latest_context",
            },
        )

        claimed = recovery_status in {
            "platform_processing",
            "platform_processing_retry",
            "platform_send_uncertain",
            "platform_complete_pending",
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
            self.repository.update_sop_event_status(event_id, status="platform_complete_pending")
            completed = await self.platform_client.consume(task_id=task_id, status=30)
            _require_platform_status(completed, 30)
            self.repository.update_sop_event_status(event_id, status="platform_completed")
            return {
                "processed": True,
                "status": "sent",
                "task_id": task_id,
                "platform_response": completed,
            }

        try:
            if preflight_reason:
                context = {
                    "source": "preflight",
                    "task_timing": _task_timing(platform_task),
                    "quiet_hours": quiet_hours,
                }
                decision = {"decision": "no_send", "reason": preflight_reason, "reply_messages": []}
                self._counters[preflight_reason] += 1
            else:
                started = time.perf_counter()
                context = await self._load_context(platform_task, identity=identity)
                self._observe("context", time.perf_counter() - started)
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
                self.repository.update_sop_send_task(
                    str(local_task.get("id") or ""),
                    status="completed_without_send",
                    send_payload={"decision": decision, "context": _context_audit(context)},
                )
            else:
                send_payload = {
                    **identity,
                    "plan_id": f"platform-sop-{task_id}",
                    "task_id": f"platform-sop-send-{task_id}",
                    "reply_messages": decision["reply_messages"],
                }
                existing_delivery = await self._existing_platform_delivery(identity=identity, send_payload=send_payload)
                if existing_delivery.get("found"):
                    duplicate_decision = {
                        "decision": "no_send",
                        "reason": "existing_platform_delivery",
                        "reply_messages": [],
                    }
                    self.repository.update_sop_send_task(
                        str(local_task.get("id") or ""),
                        status="completed_without_send",
                        send_payload={
                            "decision": duplicate_decision,
                            "request": send_payload,
                            "context": {
                                **_context_audit(context),
                                "existing_delivery": existing_delivery,
                            },
                        },
                    )
                    self.repository.update_sop_event_status(event_id, status="platform_complete_pending")
                    completed = await self.platform_client.consume(task_id=task_id, status=30)
                    _require_platform_status(completed, 30)
                    self.repository.update_sop_event_status(event_id, status="platform_completed")
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
                    send_payload={"decision": decision, "request": send_payload, "context": _context_audit(context)},
                )
                started = time.perf_counter()
                send_result = await self.system_client.send(
                    **send_payload,
                    source_channel="proactive_message",
                    source_kind="sop_platform_task",
                    source_request_id=event_id,
                    source_task_id=str(local_task.get("id") or ""),
                    source_context={
                        "sop_send_task_id": str(local_task.get("id") or ""),
                        "sop_event_id": event_id,
                        "platform_task_id": task_id,
                    },
                    delivery_idempotency_key=f"sop_platform_task:{local_task.get('id')}",
                )
                self._observe("send", time.perf_counter() - started)
                send_data = send_result.get("data") if isinstance(send_result.get("data"), dict) else {}
                delivery_status = str(send_data.get("delivery_status") or "")
                if bool(send_data.get("callback_required")) and delivery_status in {
                    "platform_accepted",
                    "submission_unknown",
                    "sending",
                }:
                    self.repository.update_sop_event_status(event_id, status="platform_delivery_pending")
                    self.repository.update_sop_send_task(
                        str(local_task.get("id") or ""),
                        status="sending",
                        send_payload={"decision": decision, "request": send_payload, "context": _context_audit(context)},
                        send_response=send_result,
                        error="",
                    )
                    return {
                        "processed": True,
                        "status": "accepted",
                        "task_id": task_id,
                        "send_response": send_result,
                    }
                send_status = str(send_data.get("send_status") or send_result.get("msg") or "")
                if send_status == "accepted_no_response":
                    send_result = {
                        **send_result,
                        "msg": "accepted_no_response_assumed_sent",
                        "data": {
                            **(send_result.get("data") if isinstance(send_result.get("data"), dict) else {}),
                            "assumed_sent": True,
                        },
                    }
                self.repository.update_sop_send_task(
                    str(local_task.get("id") or ""),
                    status="sent",
                    send_payload={"decision": decision, "request": send_payload, "context": _context_audit(context)},
                    send_response=send_result,
                    sent_at=utc_now_iso(),
                )
            self.repository.update_sop_event_status(event_id, status="platform_complete_pending")
            completed = await self.platform_client.consume(task_id=task_id, status=30)
            _require_platform_status(completed, 30)
            self.repository.update_sop_event_status(event_id, status="platform_completed")
            return {
                "processed": True,
                "status": "sent" if decision["decision"] == "send" else "completed_without_send",
                "task_id": task_id,
                "platform_response": completed,
            }
        except Exception as exc:
            event_after_error = self.repository.get_sop_event(event_id)
            event_status = str(event_after_error.get("status") or "")
            if event_status not in {"platform_send_uncertain", "platform_complete_pending"}:
                self.repository.update_sop_event_status(
                    event_id,
                    status="platform_processing_retry",
                    error=f"{type(exc).__name__}: {exc}",
                )
            if event_status not in {"platform_send_uncertain", "platform_complete_pending"}:
                self.repository.update_sop_send_task(
                    str(local_task.get("id") or ""),
                    status="processing_retry",
                    send_payload={"platform_task_id": task_id},
                    error=f"{type(exc).__name__}: {exc}",
                )
            raise

    async def finalize_message_delivery(self, dispatch: dict[str, Any]) -> None:
        context = dispatch.get("source_context") if isinstance(dispatch.get("source_context"), dict) else {}
        local_task_id = str(context.get("sop_send_task_id") or dispatch.get("source_task_id") or "").strip()
        event_id = str(context.get("sop_event_id") or "").strip()
        platform_task_id = str(context.get("platform_task_id") or dispatch.get("task_id") or "").strip()
        skipped_prefix_task_ids = [
            str(value).strip()
            for value in context.get("skipped_prefix_task_ids", [])
            if str(value).strip()
        ] if isinstance(context.get("skipped_prefix_task_ids"), list) else []
        compat_trigger_task_ids = [
            str(value).strip()
            for value in context.get("compat_trigger_task_ids", [])
            if str(value).strip()
        ] if isinstance(context.get("compat_trigger_task_ids"), list) else []
        if not local_task_id:
            raise ValueError("Platform SOP delivery dispatch is missing sop_send_task_id")
        local_task = (
            self.repository.get_sop_send_task_by_idempotency_key(f"platform-sop:{platform_task_id}")
            if platform_task_id
            else {}
        )
        audit = local_task.get("send_payload") if isinstance(local_task.get("send_payload"), dict) else {}
        event = self.repository.get_sop_event(event_id) if event_id else {}
        raw_payload = event.get("raw_payload") if isinstance(event.get("raw_payload"), dict) else {}
        platform_task = raw_payload.get("platform_task") if isinstance(raw_payload.get("platform_task"), dict) else {}
        decision = audit.get("decision") if isinstance(audit.get("decision"), dict) else {}
        previous_send_response = (
            local_task.get("send_response") if isinstance(local_task.get("send_response"), dict) else {}
        )
        callback_response = {**previous_send_response, "message_delivery": dispatch}
        status = str(dispatch.get("status") or "")
        if context.get("deferred_replay") is True:
            record = {
                "local_task_id": local_task_id,
                "send_payload": audit,
                "task_status": str(local_task.get("status") or ""),
                "sent_at": str(local_task.get("sent_at") or ""),
            }
            marker = _deferred_replay_marker(record)
            if status in {"send_failed", "partial_failed"}:
                self._mark_deferred_replay(
                    record,
                    status="retry",
                    error=str(dispatch.get("error_message") or status),
                    marker={
                        **marker,
                        "status": "retry",
                        "next_retry_at": (
                            datetime.now(timezone.utc) + timedelta(seconds=60)
                        ).isoformat(),
                    },
                    send_response=callback_response,
                )
                return
            if status == "send_succeeded":
                sent_at = str(dispatch.get("confirmed_at") or "") or utc_now_iso()
                self._mark_deferred_replay(
                    record,
                    status="sent",
                    marker={**marker, "status": "sent", "sent_at": sent_at},
                    send_response=callback_response,
                    sent_at=sent_at,
                )
                self._skip_deferred_replay_prefix_by_ids(
                    marker.get("skipped_prefix_local_task_ids")
                    if isinstance(marker.get("skipped_prefix_local_task_ids"), list)
                    else [],
                    decision=marker.get("decision") if isinstance(marker.get("decision"), dict) else {},
                )
                self._counters["deferred_replay_sent"] += 1
            return
        if platform_task and decision and status in {"send_succeeded", "send_failed", "partial_failed"}:
            audit = {
                **audit,
                "rule_data_response": await self._report_rule_data(
                    platform_task,
                    decision=decision,
                    sent=status == "send_succeeded",
                ),
            }
        if status in {"send_failed", "partial_failed"}:
            self.repository.update_sop_send_task(
                local_task_id,
                status="processing_retry",
                send_payload=audit,
                send_response=callback_response,
                error=str(dispatch.get("error_message") or status),
            )
            if event_id:
                self.repository.update_sop_event_status(
                    event_id,
                    status="platform_batch_send_retry",
                    error=str(dispatch.get("error_message") or status),
                )
            return
        if status != "send_succeeded":
            return
        self.repository.update_sop_send_task(
            local_task_id,
            status="sent",
            send_payload=audit,
            send_response=callback_response,
            sent_at=str(dispatch.get("confirmed_at") or "") or utc_now_iso(),
        )
        if event_id:
            self.repository.update_sop_event_status(event_id, status="platform_complete_pending")
        if platform_task_id and _in_configured_quiet_hours(settings=self.settings):
            if event_id:
                self.repository.update_sop_event_status(event_id, status="platform_batch_consume_pending")
            return
        if platform_task_id and not self.settings.sop_platform_shadow_mode:
            await self._finalize_batch_prefix(
                selected_task_id=platform_task_id,
                skipped_prefix_task_ids=skipped_prefix_task_ids,
                compat_trigger_task_ids=compat_trigger_task_ids,
                audit=audit,
            )
            self.repository.update_sop_send_task(
                local_task_id,
                status="sent",
                send_payload=audit,
                send_response=callback_response,
                sent_at=str(dispatch.get("confirmed_at") or "") or utc_now_iso(),
            )
        if event_id:
            self.repository.update_sop_event_status(event_id, status="platform_completed")
        for task_id in [*skipped_prefix_task_ids, platform_task_id, *compat_trigger_task_ids]:
            self._remember_terminal(task_id)

    async def _report_rule_data(
        self,
        platform_task: dict[str, Any],
        *,
        decision: dict[str, Any],
        sent: bool,
    ) -> dict[str, Any]:
        reporter = getattr(self.platform_client, "service_rule_data", None)
        task_id = _task_id(platform_task)
        if not callable(reporter) or not task_id:
            return {
                "rule_data_request": {},
                "rule_data_response": {"skipped": True, "reason": "rule_data_api_unavailable"},
            }
        reply_messages = decision.get("reply_messages") if isinstance(decision.get("reply_messages"), list) else []
        send_texts: list[str] = []
        for item in reply_messages:
            if not isinstance(item, dict) or str(item.get("type") or "") != "text":
                continue
            content = item.get("content")
            send_texts.append(str(content.get("text") or "") if isinstance(content, dict) else str(content or ""))
        send_content = "\n".join(send_texts)
        request = {
            "task_id": task_id,
            "scene_name": str(decision.get("sceneName") or decision.get("scene_name") or "SOP任务"),
            "scene_code": str(decision.get("sceneCode") or decision.get("scene_code") or ""),
            "send_status": 10 if sent else 20,
            "knowledge_id": _int_or_zero(decision.get("knowledgeId") or decision.get("knowledge_id")) or None,
            "knowledge_paragraph_no": (
                _int_or_zero(decision.get("knowledgeParagraphNo") or decision.get("knowledge_paragraph_no")) or None
            ),
            "remark": str(decision.get("remark") or decision.get("reason") or "")[:500],
            "send_content": send_content[:10000],
        }
        try:
            response = await reporter(**request)
        except Exception as exc:
            response = {
                "error": "service_rule_data_failed",
                "exception_type": type(exc).__name__,
                "message": str(exc),
            }
        return {
            "rule_data_request": {
                "taskId": task_id,
                "sceneName": request["scene_name"],
                "sceneCode": request["scene_code"],
                "sendStatus": request["send_status"],
                "knowledgeId": request["knowledge_id"],
                "knowledgeParagraphNo": request["knowledge_paragraph_no"],
                "remark": request["remark"],
                "sendContent": request["send_content"],
            },
            "rule_data_response": response,
        }

    async def _quiet_hours_guard(
        self,
        platform_task: dict[str, Any],
        *,
        identity: dict[str, str],
    ) -> dict[str, Any]:
        summary = _quiet_hours_base_summary(platform_task, settings=self.settings)
        if not summary.get("in_quiet_hours"):
            return summary
        return {
            **summary,
            "blocked": True,
            "reason": "quiet_hours_all_sop_blocked",
        }

    async def _load_context(self, platform_task: dict[str, Any], *, identity: dict[str, str]) -> dict[str, Any]:
        missing = [key for key in ("corp_id", "customer_id", "external_userid", "user_id", "wechat") if not identity[key]]
        if missing:
            raise RuntimeError(f"platform task missing identity: {','.join(missing)}")
        conversation = await self.system_client.conversation(**identity, limit=80)
        data = conversation.get("data") if isinstance(conversation.get("data"), dict) else conversation
        relation = _compact_customer_relation(
            data.get("customer_relation") if isinstance(data.get("customer_relation"), dict) else {}
        )
        messages = data.get("messages") if isinstance(data.get("messages"), list) else []
        timeline = _conversation_timeline(messages[-80:])
        if relation.get("is_deleted") is True or str(relation.get("status") or "").lower() == "deleted":
            return {
                "customer_relation": relation,
                "conversation_timeline": timeline,
                "conversation_count": len(messages),
                "business_state": {"source": "skipped_customer_deleted"},
                "task_timing": _task_timing(platform_task),
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
        customer_context = await asyncio.to_thread(
            self.customer_context_service.load,
            customer_id=identity["customer_id"],
            memory={},
            request_context=request_context,
        )
        return {
            "customer_relation": relation,
            "conversation_timeline": timeline,
            "conversation_count": len(messages),
            "business_state": _compact_business_state(customer_context),
            "task_timing": _task_timing(platform_task),
        }

    async def _decide(self, platform_task: dict[str, Any], *, context: dict[str, Any]) -> dict[str, Any]:
        relation = context.get("customer_relation") if isinstance(context.get("customer_relation"), dict) else {}
        if relation.get("is_deleted") is True or str(relation.get("status") or "").lower() == "deleted":
            return {"decision": "no_send", "reason": "customer_relation_deleted", "reply_messages": []}
        original_messages = _platform_messages(platform_task)
        use_ai_copy = _bool(platform_task.get("useAiCopy", platform_task.get("use_ai_copy")))
        if not original_messages and not use_ai_copy:
            raise RuntimeError("platform task message_content is empty or unsupported")
        if not original_messages and not _has_trusted_ai_copy_source(platform_task):
            return {"decision": "no_send", "reason": "missing_trusted_platform_content", "reply_messages": []}
        material_library = _material_catalog_for_model(self.objection_material_service)
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
                "scene_role": "supporting_context",
                "use_ai_copy": use_ai_copy,
                "message_content": original_messages,
                "message_content_role": "executable_candidate",
                "platform_metadata": {
                    "rule_id": platform_task.get("ruleId") or platform_task.get("rule_id"),
                    "rule_name": platform_task.get("ruleName") or platform_task.get("rule_name"),
                    "scene_id": platform_task.get("sceneId") or platform_task.get("scene_id"),
                },
                "timing": _task_timing(platform_task),
            },
            "latest_context": {
                "customer_relation": context.get("customer_relation") or {},
                "conversation_timeline": context.get("conversation_timeline") or [],
                "timeline_structure": _timeline_structure(context.get("conversation_timeline") or []),
                "business_state": context.get("business_state") or {},
            },
            "material_library": material_library,
            "authoritative_business_facts": _sop_platform_batch_business_facts_for_model(),
            "output_contract": {
                "decision": "send | no_send",
                "reason_code": "required for no_send; optional for send",
                "reason": "string",
                "reply_messages": "send must be non-empty; no_send must be []",
                "first_add_no_send_allowed_reason_codes": sorted(FIRST_ADD_NO_SEND_REASON_CODES),
            },
        }
        messages = [
            {"role": "system", "content": SOP_PLATFORM_TASK_SYSTEM_PROMPT},
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
        error = _decision_error(raw, original_messages=original_messages, use_ai_copy=model_input["task"]["use_ai_copy"])
        policy_error = "" if error else _decision_policy_error(raw, platform_task=platform_task)
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
            error = _decision_error(raw, original_messages=original_messages, use_ai_copy=model_input["task"]["use_ai_copy"])
            policy_error = "" if error else _decision_policy_error(raw, platform_task=platform_task)
        if not error and policy_error:
            repair_messages = [
                *messages,
                {"role": "assistant", "content": json.dumps(raw, ensure_ascii=False)},
                {
                    "role": "user",
                    "content": (
                        f"Decision violates first-add policy: {policy_error}. "
                        "Return valid json only. If this is a first-add no_send, use one allowed reason_code "
                        "with concrete evidence. Similar wording, normal silence, no demand, old resolved context, "
                        "or use_ai_copy=false are not allowed no_send reasons."
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
            error = _decision_error(raw, original_messages=original_messages, use_ai_copy=model_input["task"]["use_ai_copy"])
            policy_error = "" if error else _decision_policy_error(raw, platform_task=platform_task)
        if error:
            raise RuntimeError(f"invalid_sop_platform_model_output: {error}")
        if policy_error:
            if _task_type(platform_task) == "add_wecom" and original_messages:
                return {
                    "decision": "send",
                    "reason": "first_add_default_send_after_invalid_no_send_reason",
                    "reason_code": "send",
                    "reply_messages": original_messages,
                }
            raise RuntimeError(f"invalid_sop_platform_model_output: {policy_error}")
        decision = str(raw.get("decision") or "")
        if decision == "no_send":
            return {
                "decision": decision,
                "reason": str(raw.get("reason") or ""),
                "reason_code": str(raw.get("reason_code") or ""),
                "reply_messages": [],
            }
        output_messages = raw.get("reply_messages") if isinstance(raw.get("reply_messages"), list) else []
        if not model_input["task"]["use_ai_copy"]:
            output_messages = original_messages
        return {
            "decision": decision,
            "reason": str(raw.get("reason") or ""),
            "reason_code": str(raw.get("reason_code") or ""),
            "reply_messages": output_messages,
        }


def _decision_error(raw: Any, *, original_messages: list[dict[str, Any]], use_ai_copy: bool) -> str:
    if not isinstance(raw, dict):
        return "output must be an object"
    unexpected = set(raw).difference({"decision", "reason", "reason_code", "reply_messages"})
    if unexpected:
        return f"unexpected output fields: {','.join(sorted(unexpected))}"
    decision = str(raw.get("decision") or "").strip()
    if decision not in {"send", "no_send"}:
        return "decision must be send or no_send"
    messages = raw.get("reply_messages")
    if not isinstance(messages, list):
        return "reply_messages must be a list"
    if decision == "no_send":
        return "no_send reply_messages must be empty" if messages else ""
    if not messages:
        return "send reply_messages must not be empty"
    if not use_ai_copy:
        return ""
    if not original_messages:
        if len(messages) > 2:
            return "AI copy without message_content may contain at most two text messages"
        for index, candidate in enumerate(messages, start=1):
            if not isinstance(candidate, dict) or candidate.get("type") != "text":
                return f"generated reply message {index} must be text"
            if candidate.get("order") != index:
                return f"generated reply message {index} order must be {index}"
            content = candidate.get("content") if isinstance(candidate.get("content"), dict) else {}
            if not str(content.get("text") or "").strip():
                return f"generated reply message {index} text is empty"
        return ""
    if len(messages) != len(original_messages):
        return "AI copy may not add or remove platform messages"
    for index, (candidate, original) in enumerate(zip(messages, original_messages), start=1):
        if not isinstance(candidate, dict):
            return f"reply message {index} must be an object"
        if candidate.get("type") != original.get("type") or candidate.get("order") != original.get("order"):
            return f"reply message {index} type/order must remain unchanged"
        message_type = str(original.get("type") or "")
        if message_type == "text":
            content = candidate.get("content") if isinstance(candidate.get("content"), dict) else {}
            if not str(content.get("text") or "").strip():
                return f"reply message {index} text is empty"
        elif candidate != original:
            return f"reply message {index} media/link content must remain unchanged"
    return ""


def _decision_policy_error(raw: dict[str, Any], *, platform_task: dict[str, Any]) -> str:
    if str(raw.get("decision") or "").strip() != "no_send":
        return ""
    if _task_type(platform_task) != "add_wecom":
        return ""
    code = str(raw.get("reason_code") or "").strip()
    if code in FIRST_ADD_NO_SEND_REASON_CODES:
        return ""
    return (
        "first-add no_send requires reason_code in "
        + ",".join(sorted(FIRST_ADD_NO_SEND_REASON_CODES))
    )


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
                            "content": {
                                "amount": PAYMENT_COLLECTION_UNIT_AMOUNT,
                                "remark": "",
                            },
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


def _manual_resend_messages(local_task: dict[str, Any], platform_task: dict[str, Any]) -> list[dict[str, Any]]:
    use_ai_copy = _bool(platform_task.get("useAiCopy", platform_task.get("use_ai_copy")))
    send_payload = local_task.get("send_payload") if isinstance(local_task.get("send_payload"), dict) else {}
    request_payload = send_payload.get("request") if isinstance(send_payload.get("request"), dict) else {}
    request_messages = request_payload.get("reply_messages")
    if isinstance(request_messages, list) and request_messages:
        return request_messages
    decision_payload = send_payload.get("decision") if isinstance(send_payload.get("decision"), dict) else {}
    decision_messages = decision_payload.get("reply_messages")
    if isinstance(decision_messages, list) and decision_messages:
        return decision_messages
    if use_ai_copy:
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
) -> str:
    missing = [key for key in ("corp_id", "customer_id", "external_userid", "user_id", "wechat") if not identity[key]]
    if missing:
        return "invalid_identity"
    payload_error = _platform_message_error(platform_task)
    if payload_error:
        return "invalid_message_content"
    messages = _platform_messages(platform_task)
    use_ai_copy = _bool(platform_task.get("useAiCopy", platform_task.get("use_ai_copy")))
    if not messages and not use_ai_copy:
        return "invalid_message_content"
    if not messages and not _has_trusted_ai_copy_source(platform_task):
        return "missing_trusted_platform_content"
    scheduled = _task_scheduled_epoch(platform_task)
    max_age = max(0, int(getattr(settings, "sop_platform_max_task_age_seconds", 21600) or 0))
    if use_ai_copy and scheduled and max_age and time.time() - scheduled > max_age:
        return "stale_task"
    live_not_before = _parse_epoch(getattr(settings, "sop_platform_live_not_before", ""))
    if live_not_before and (not scheduled or scheduled < live_not_before):
        return "pre_cutover_task"
    return ""


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
    processing_time = datetime.fromtimestamp(time.time(), tz=_BEIJING_TZ)
    in_quiet_hours = bool(
        enabled
        and (
            _hour_in_window(local_time.hour, start_hour=start_hour, end_hour=end_hour)
            or _hour_in_window(processing_time.hour, start_hour=start_hour, end_hour=end_hour)
        )
    )
    return {
        "enabled": enabled,
        "timezone": "Asia/Shanghai",
        "window": f"{start_hour:02d}:00-{end_hour:02d}:00",
        "scheduled_at": platform_task.get("scheduledAt") or platform_task.get("scheduled_at") or "",
        "reference_source": "scheduled_at" if scheduled_epoch else "processing_time",
        "reference_epoch": reference_epoch,
        "reference_at_beijing": local_time.strftime("%Y-%m-%d %H:%M:%S"),
        "processing_at_beijing": processing_time.strftime("%Y-%m-%d %H:%M:%S"),
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


def _task_run_id(task: dict[str, Any]) -> str:
    return str(
        task.get("runId")
        or task.get("run_id")
        or task.get("triggerRunId")
        or task.get("trigger_run_id")
        or ""
    ).strip()


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


def _admin_date_filter_iso(value: Any) -> str:
    epoch = _parse_epoch(value)
    if not epoch:
        return ""
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _task_timing(task: dict[str, Any]) -> dict[str, Any]:
    scheduled = _task_scheduled_epoch(task)
    return {
        "scheduled_at": task.get("scheduledAt") or task.get("scheduled_at") or "",
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


def _merge_platform_task_runs(task_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_task_id = {
        str(item.get("task_id") or ""): item
        for item in task_items
        if isinstance(item, dict) and str(item.get("task_id") or "")
    }
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in task_items:
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("task_id") or "")
        version = str(item.get("log_version") or "legacy_single")
        if version == "batch_v2":
            group_id = str(item.get("batch_run_id") or f"batch:{task_id}")
        elif version == "platform_pending":
            group_id = f"pending:{item.get('batch_key') or task_id}"
        else:
            group_id = f"legacy:{task_id}"
        grouped.setdefault(group_id, []).append(item)

    runs: list[dict[str, Any]] = []
    for group_id, group_items in grouped.items():
        representative = max(group_items, key=_platform_log_item_richness)
        version = str(representative.get("log_version") or "legacy_single")
        selected_task_id = str(representative.get("selected_task_id") or "")
        if version == "legacy_single" and str(representative.get("decision") or "") == "send":
            selected_task_id = str(representative.get("task_id") or "")
        batch_task_ids = [
            str(value).strip()
            for value in representative.get("batch_task_ids", [])
            if str(value).strip()
        ] if isinstance(representative.get("batch_task_ids"), list) else []
        if not batch_task_ids:
            batch_task_ids = [
                str(item.get("task_id") or "")
                for item in sorted(group_items, key=_platform_run_task_sort_key)
                if str(item.get("task_id") or "")
            ]
        ordered_items: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for task_id in batch_task_ids:
            candidate = by_task_id.get(task_id)
            if candidate:
                ordered_items.append(candidate)
            else:
                ordered_items.append(
                    {
                        "task_id": task_id,
                        "log_version": version,
                        "stage_label": "历史记录缺失",
                        "original_messages": [],
                    }
                )
            seen_ids.add(task_id)
        for item in sorted(group_items, key=_platform_run_task_sort_key):
            task_id = str(item.get("task_id") or "")
            if task_id and task_id not in seen_ids:
                ordered_items.append(item)
                seen_ids.add(task_id)

        evaluations = representative.get("evaluations") if isinstance(representative.get("evaluations"), list) else []
        evaluation_by_id = {
            str(item.get("task_id") or ""): item
            for item in evaluations
            if isinstance(item, dict) and str(item.get("task_id") or "")
        }
        skipped_ids = {
            str(value).strip()
            for value in representative.get("skipped_prefix_task_ids", [])
            if str(value).strip()
        } if isinstance(representative.get("skipped_prefix_task_ids"), list) else set()
        consume_results = (
            representative.get("consume_results") if isinstance(representative.get("consume_results"), list) else []
        )
        consume_by_id = {
            str(item.get("task_id") or ""): item
            for item in consume_results
            if isinstance(item, dict) and str(item.get("task_id") or "")
        }
        run_tasks = [
            _platform_run_task_item(
                item,
                sequence=index,
                selected_task_id=selected_task_id,
                skipped_ids=skipped_ids,
                evaluation=evaluation_by_id.get(str(item.get("task_id") or ""), {}),
                consume_result=consume_by_id.get(str(item.get("task_id") or ""), {}),
                version=version,
            )
            for index, item in enumerate(ordered_items, start=1)
        ]
        status = _platform_run_status(version=version, representative=representative, tasks=run_tasks)
        selected_task = next(
            (item for item in ordered_items if str(item.get("task_id") or "") == selected_task_id),
            representative,
        )
        context_summary = (
            representative.get("context_summary") if isinstance(representative.get("context_summary"), dict) else {}
        )
        send_response = (
            selected_task.get("send_response") if isinstance(selected_task.get("send_response"), dict) else {}
        )
        delivery_data = send_response.get("data") if isinstance(send_response.get("data"), dict) else {}
        callback = (
            send_response.get("message_delivery")
            if isinstance(send_response.get("message_delivery"), dict)
            else {}
        )
        occurred_at = next(
            (
                str(item.get("scheduled_at") or item.get("pulled_at") or "")
                for item in ordered_items
                if item.get("scheduled_at") or item.get("pulled_at")
            ),
            str(representative.get("updated_at") or ""),
        )
        missing_fields = []
        if version == "legacy_single":
            missing_fields = [
                "客户批次",
                "人工/AI状态来源",
                "是否开口",
                "顺序判断",
                "连续前缀消费",
            ]
        runs.append(
            {
                "run_id": group_id,
                "log_version": version,
                "version_label": _PLATFORM_LOG_VERSION_LABELS[version],
                "batch_key": str(representative.get("batch_key") or ""),
                "biz_type": str(representative.get("biz_type") or "online_service"),
                "customer_id": str(representative.get("customer_id") or ""),
                "external_userid": str(representative.get("external_userid") or ""),
                "corp_id": str(representative.get("corp_id") or ""),
                "user_id": str(representative.get("user_id") or ""),
                "wechat": str(representative.get("wechat") or ""),
                "occurred_at": occurred_at,
                "updated_at": str(representative.get("updated_at") or ""),
                "status": status,
                "status_label": _PLATFORM_RUN_STATUS_LABELS[status],
                "summary_text": _platform_run_summary(
                    version=version,
                    status=status,
                    tasks=run_tasks,
                    selected_task_id=selected_task_id,
                    reason=str(representative.get("batch_reason") or representative.get("decision_reason") or ""),
                ),
                "selected_task_id": selected_task_id,
                "task_count": len(run_tasks),
                "tasks": run_tasks,
                "customer_state": {
                    "management_mode": context_summary.get("management_mode"),
                    "management_source": context_summary.get("management_source"),
                    "customer_opened": context_summary.get("customer_opened"),
                    "same_day_unopened": context_summary.get("same_day_unopened"),
                    "timeline_structure": (
                        context_summary.get("timeline_structure")
                        if isinstance(context_summary.get("timeline_structure"), dict)
                        else {}
                    ),
                },
                "transition_text": str(representative.get("transition_text") or ""),
                "original_messages": (
                    selected_task.get("original_messages")
                    if isinstance(selected_task.get("original_messages"), list)
                    else []
                ),
                "final_messages": (
                    selected_task.get("final_messages")
                    if isinstance(selected_task.get("final_messages"), list)
                    else []
                ),
                "delivery": {
                    "status": str(callback.get("status") or delivery_data.get("delivery_status") or ""),
                    "callback_required": bool(delivery_data.get("callback_required")),
                    "confirmed_at": str(callback.get("confirmed_at") or selected_task.get("sent_at") or ""),
                    "error": str(
                        callback.get("error_message")
                        or selected_task.get("error")
                        or representative.get("error")
                        or ""
                    ),
                    "response": send_response,
                },
                "consume": {
                    "results": consume_results,
                    "completed_count": sum(
                        _int_or_zero(item.get("consume_status")) in {30, 70} for item in run_tasks
                    ),
                    "pending_count": sum(
                        _int_or_zero(item.get("consume_status")) not in {30, 70} for item in run_tasks
                    ),
                },
                "identifiers": _dedupe_identifier_items(
                    [
                        {
                            "key": "run_id",
                            "value": group_id,
                            "source": "运行批次",
                        },
                        {
                            "key": "batch_key",
                            "value": str(representative.get("batch_key") or ""),
                            "source": "运行批次",
                        },
                        {
                            "key": "customer_id",
                            "value": str(representative.get("customer_id") or ""),
                            "source": "客户边界",
                        },
                        {
                            "key": "external_userid",
                            "value": str(representative.get("external_userid") or ""),
                            "source": "客户边界",
                        },
                        {
                            "key": "corp_id",
                            "value": str(representative.get("corp_id") or ""),
                            "source": "客户边界",
                        },
                        {
                            "key": "user_id",
                            "value": str(representative.get("user_id") or ""),
                            "source": "客户边界",
                        },
                        *(
                            identifier
                            for item in ordered_items
                            for identifier in (
                                item.get("identifiers")
                                if isinstance(item.get("identifiers"), list)
                                else []
                            )
                            if isinstance(identifier, dict)
                        ),
                    ]
                ),
                "quiet_hours_archive": (
                    representative.get("quiet_hours_archive")
                    if isinstance(representative.get("quiet_hours_archive"), dict)
                    else {}
                ),
                "missing_fields": missing_fields,
                "raw_debug": {
                    "event_status": representative.get("event_status"),
                    "task_status": representative.get("task_status"),
                    "platform_status": representative.get("platform_status"),
                    "decision": {
                        "selected_task_id": selected_task_id,
                        "evaluations": evaluations,
                    },
                    "send_response": send_response,
                },
            }
        )
    runs.sort(key=lambda run: _parse_epoch(run.get("occurred_at")), reverse=True)
    return runs


def _platform_log_item_richness(item: dict[str, Any]) -> tuple[int, int, float]:
    return (
        1 if str(item.get("task_id") or "") == str(item.get("selected_task_id") or "") else 0,
        len(item.get("evaluations") or []) + len(item.get("consume_results") or []),
        _parse_epoch(item.get("updated_at") or item.get("scheduled_at")),
    )


def _platform_run_task_sort_key(item: dict[str, Any]) -> tuple[float, str]:
    return (_parse_epoch(item.get("scheduled_at")) or float("inf"), str(item.get("task_id") or ""))


def _platform_run_task_item(
    item: dict[str, Any],
    *,
    sequence: int,
    selected_task_id: str,
    skipped_ids: set[str],
    evaluation: dict[str, Any],
    consume_result: dict[str, Any],
    version: str,
) -> dict[str, Any]:
    task_id = str(item.get("task_id") or "")
    if task_id == selected_task_id:
        sequence_state = "selected"
    elif task_id in skipped_ids:
        sequence_state = "skipped"
    elif version == "legacy_single":
        sequence_state = "legacy"
    elif version == "platform_pending":
        sequence_state = "pending"
    else:
        sequence_state = "untouched"
    consume_status = _int_or_zero(consume_result.get("status"))
    if not consume_status and sequence_state == "selected":
        if str(item.get("task_status") or "") == "sending":
            consume_status = 20
        elif str(item.get("task_status") or "") == "sent" and str(item.get("event_status") or "") == "platform_completed":
            consume_status = 30
    if not consume_status and sequence_state == "skipped" and str(item.get("event_status") or "") == "platform_completed":
        consume_status = 70
    decision = str(evaluation.get("decision") or "")
    if not decision:
        decision = "send" if sequence_state == "selected" else "skip" if sequence_state == "skipped" else ""
    if version == "legacy_single":
        decision = str(item.get("decision") or decision)
    return {
        "task_id": task_id,
        "sequence": sequence,
        "sequence_state": sequence_state,
        "decision": decision,
        "reason": str(evaluation.get("reason") or item.get("decision_reason") or ""),
        "evidence_refs": evaluation.get("evidence_refs") if isinstance(evaluation.get("evidence_refs"), list) else [],
        "consume_status": consume_status or None,
        "consume_remark": str(consume_result.get("remark") or ""),
        "rule_name": str(item.get("rule_name") or ""),
        "scheduled_at": item.get("scheduled_at"),
        "platform_status": str(item.get("platform_status") or ""),
        "event_status": str(item.get("event_status") or ""),
        "task_status": str(item.get("task_status") or ""),
        "original_messages": item.get("original_messages") if isinstance(item.get("original_messages"), list) else [],
        "error": str(item.get("error") or ""),
    }


def _platform_run_status(*, version: str, representative: dict[str, Any], tasks: list[dict[str, Any]]) -> str:
    event_statuses = {str(task.get("event_status") or "") for task in tasks}
    task_statuses = {str(task.get("task_status") or "") for task in tasks}
    if any(task.get("error") for task in tasks) or event_statuses.intersection(
        {"platform_batch_send_retry", "platform_processing_retry", "platform_send_uncertain", "platform_failed"}
    ):
        return "exception"
    if "platform_delivery_pending" in event_statuses or "sending" in task_statuses:
        return "delivery_pending"
    if event_statuses.intersection({"platform_batch_consume_pending", "platform_complete_pending"}):
        return "consume_pending"
    if version == "platform_pending":
        return "pending"
    selected_task_id = str(representative.get("selected_task_id") or "")
    if selected_task_id:
        selected = next((task for task in tasks if task.get("task_id") == selected_task_id), {})
        if selected.get("consume_status") == 30 or selected.get("task_status") in {"sent", "shadow_send"}:
            return "completed"
        return "processing"
    if str(representative.get("decision") or "") == "send":
        return "completed" if str(representative.get("task_status") or "") == "sent" else "processing"
    if tasks and all(
        task.get("consume_status") == 70
        or task.get("task_status") in {"completed_without_send", "shadow_no_send"}
        or task.get("decision") in {"no_send", "skip"}
        for task in tasks
    ):
        return "no_send"
    if str(representative.get("bucket") or "") in {"judging", "pulled_unjudged"}:
        return "processing"
    return "pending"


def _platform_run_summary(
    *,
    version: str,
    status: str,
    tasks: list[dict[str, Any]],
    selected_task_id: str,
    reason: str,
) -> str:
    if version == "legacy_single":
        return f"历史单任务：{_PLATFORM_RUN_STATUS_LABELS[status]}"
    if version == "platform_pending":
        return f"{len(tasks)} 条任务等待处理"
    if selected_task_id:
        selected_index = next(
            (index for index, task in enumerate(tasks) if task.get("task_id") == selected_task_id),
            0,
        )
        remaining = max(0, len(tasks) - selected_index - 1)
        return f"跳过 {selected_index} 条，发送第 {selected_index + 1} 条，剩余 {remaining} 条未处理"
    if reason == "human_takeover":
        return f"人工接管，{len(tasks)} 条任务无需发送"
    if reason == "customer_relation_deleted":
        return f"客户关系已失效，{len(tasks)} 条任务无需发送"
    if reason == "quiet_hours_no_replay":
        return f"夜间拦截并记录，{len(tasks)} 条任务不补发"
    return f"{len(tasks)} 条任务均无需发送"


def _platform_run_search_text(run: dict[str, Any]) -> str:
    values = [
        run.get("run_id"),
        run.get("batch_key"),
        run.get("customer_id"),
        run.get("external_userid"),
        run.get("wechat"),
        run.get("summary_text"),
    ]
    values.extend(task.get("task_id") for task in run.get("tasks", []) if isinstance(task, dict))
    values.extend(task.get("rule_name") for task in run.get("tasks", []) if isinstance(task, dict))
    return " ".join(str(value or "").lower() for value in values)


_PLATFORM_LOG_VERSION_LABELS = {
    "batch_v2": "顺序批次",
    "legacy_single": "历史单任务",
    "platform_pending": "平台实时待处理",
}


_PLATFORM_RUN_STATUS_LABELS = {
    "pending": "等待处理",
    "processing": "处理中",
    "delivery_pending": "发送结果待确认",
    "consume_pending": "等待消费回传",
    "completed": "发送完成",
    "no_send": "无需发送",
    "exception": "处理异常",
}


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
    decision_payload = send_payload.get("decision") if isinstance(send_payload.get("decision"), dict) else {}
    decision = str(decision_payload.get("decision") or "")
    if not decision and task_status in {"shadow_send", "sending", "sent"}:
        decision = "send"
    if not decision and task_status in {"shadow_no_send", "completed_without_send"}:
        decision = "no_send"
    request_payload = send_payload.get("request") if isinstance(send_payload.get("request"), dict) else {}
    final_messages = request_payload.get("reply_messages") if isinstance(request_payload.get("reply_messages"), list) else []
    if not final_messages:
        final_messages = (
            send_payload.get("final_messages") if isinstance(send_payload.get("final_messages"), list) else []
        )
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
    batch_key = str(send_payload.get("batch_key") or "")
    processing_mode = str(send_payload.get("processing_mode") or "")
    evaluations = decision_payload.get("evaluations") if isinstance(decision_payload.get("evaluations"), list) else []
    selected_task_id = str(decision_payload.get("selected_task_id") or "")
    audit_schema_version = _int_or_zero(send_payload.get("audit_schema_version"))
    has_batch_audit = bool(batch_key or processing_mode == "customer_batch_sequence" or evaluations)
    pending_without_decision = platform_visible and not decision and task_status in {"", "platform_received", "platform_queued"}
    log_version = "batch_v2" if has_batch_audit else "platform_pending" if pending_without_decision else "legacy_single"
    inferred_batch_key = batch_key or (
        _customer_batch_key(platform_task) if log_version == "platform_pending" and platform_task else ""
    )
    batch_task_ids = [
        str(value).strip()
        for value in send_payload.get("batch_task_ids", [])
        if str(value).strip()
    ] if isinstance(send_payload.get("batch_task_ids"), list) else []
    if not batch_task_ids and evaluations:
        batch_task_ids = [
            str(item.get("task_id") or "").strip()
            for item in evaluations
            if isinstance(item, dict) and str(item.get("task_id") or "").strip()
        ]
    inferred_run_anchor = selected_task_id or (batch_task_ids[0] if batch_task_ids else task_id)
    identifiers = _dedupe_identifier_items(
        [
            {
                "key": "event_id",
                "value": str(local_record.get("event_id") or f"platform_sop_task:{task_id}"),
                "source": "本地事件",
            },
            {
                "key": "local_task_id",
                "value": str(local_record.get("local_task_id") or ""),
                "source": "本地发送任务",
            },
            *_collect_identifier_items(platform_task, source="第三方任务", prefix="platform_task"),
            *_collect_identifier_items(send_payload, source="处理与消费", prefix="send_payload"),
            *_collect_identifier_items(
                local_record.get("send_response") if isinstance(local_record.get("send_response"), dict) else {},
                source="消息发送",
                prefix="send_response",
            ),
        ]
    )
    return {
        "task_id": task_id,
        "bucket": bucket,
        "stage_label": _PLATFORM_TASK_BUCKET_LABELS[bucket],
        "platform_status": str(platform_task.get("status") or ("10" if platform_visible else "")),
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
        "use_ai_copy": _bool(platform_task.get("useAiCopy", platform_task.get("use_ai_copy"))),
        "scheduled_at": scheduled_at,
        "pulled_at": str(platform_task.get("_aics_pulled_at") or received_at),
        "updated_at": str(local_record.get("task_updated_at") or local_record.get("event_updated_at") or ""),
        "sent_at": str(local_record.get("sent_at") or ""),
        "lateness_seconds": round(max(0.0, time.time() - scheduled_epoch), 3) if scheduled_epoch else None,
        "original_messages": _platform_messages(platform_task),
        "final_messages": final_messages,
        "send_response": local_record.get("send_response") if isinstance(local_record.get("send_response"), dict) else {},
        "audit_schema_version": audit_schema_version,
        "log_version": log_version,
        "processing_mode": processing_mode,
        "batch_key": inferred_batch_key,
        "batch_run_id": str(send_payload.get("batch_run_id") or (f"batch:{inferred_run_anchor}" if has_batch_audit else "")),
        "biz_type": str(send_payload.get("biz_type") or platform_task.get("_aics_biz_type") or ""),
        "batch_task_ids": batch_task_ids,
        "selected_task_id": selected_task_id,
        "evaluations": evaluations,
        "transition_text": str(send_payload.get("transition_text") or ""),
        "skipped_prefix_task_ids": (
            [str(value).strip() for value in send_payload.get("skipped_prefix_task_ids", []) if str(value).strip()]
            if isinstance(send_payload.get("skipped_prefix_task_ids"), list)
            else []
        ),
        "consume_results": (
            send_payload.get("consume_results") if isinstance(send_payload.get("consume_results"), list) else []
        ),
        "identifiers": identifiers,
        "quiet_hours_archive": (
            send_payload.get("quiet_hours_archive")
            if isinstance(send_payload.get("quiet_hours_archive"), dict)
            else {}
        ),
        "context_summary": send_payload.get("context") if isinstance(send_payload.get("context"), dict) else {},
        "batch_reason": str(send_payload.get("reason") or ""),
        "_sort_epoch": max(scheduled_epoch, _parse_epoch(received_at)),
    }


def _collect_identifier_items(
    value: Any,
    *,
    source: str,
    prefix: str = "",
) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []

    def visit(current: Any, path: str) -> None:
        if isinstance(current, dict):
            for raw_key, child in current.items():
                key = str(raw_key)
                child_path = f"{path}.{key}" if path else key
                is_identifier = (
                    key.lower() == "id"
                    or key.lower() in {"msgid", "msgids", "system_msgids", "archive_msgid"}
                    or key.lower().endswith("_id")
                    or key.lower().endswith("_ids")
                    or key.endswith("Id")
                    or key.endswith("Ids")
                    or key.endswith("ID")
                    or key.endswith("IDs")
                )
                if is_identifier and not isinstance(child, (dict, list)):
                    text = str(child or "").strip()
                    if text:
                        output.append({"key": child_path, "value": text, "source": source})
                elif is_identifier and isinstance(child, list):
                    for index, item in enumerate(child):
                        if not isinstance(item, (dict, list)) and str(item or "").strip():
                            output.append(
                                {
                                    "key": f"{child_path}[{index}]",
                                    "value": str(item).strip(),
                                    "source": source,
                                }
                            )
                visit(child, child_path)
        elif isinstance(current, list):
            for index, child in enumerate(current):
                visit(child, f"{path}[{index}]")

    visit(value, prefix)
    return output


def _dedupe_identifier_items(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in items:
        key = str(item.get("key") or "").strip()
        value = str(item.get("value") or "").strip()
        source = str(item.get("source") or "").strip()
        if not key or not value:
            continue
        marker = (source, key, value)
        if marker in seen:
            continue
        seen.add(marker)
        output.append({"key": key, "value": value, "source": source})
    return output


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
    if event_status in {"platform_send_uncertain", "platform_processing_retry", "platform_complete_pending", "platform_failed"} or task_status in {"processing_retry"}:
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


def _int_or_zero(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _retryable_delivery_failure(exc: Exception) -> dict[str, Any]:
    message = str(exc)
    matched = re.search(r"outreach_system_http_(\d{3})\s*:\s*(.*)", message, flags=re.DOTALL)
    if matched:
        return {
            "kind": "downstream_http_error",
            "http_status": int(matched.group(1)),
            "detail": matched.group(2).strip()[:2000],
        }
    return {
        "kind": "downstream_send_error",
        "http_status": 0,
        "detail": message[:2000],
    }


def _batch_tasks(value: dict[str, Any]) -> list[dict[str, Any]]:
    if value.get("_aics_customer_batch") and isinstance(value.get("tasks"), list):
        return [dict(item) for item in value["tasks"] if isinstance(item, dict)]
    return [dict(value)] if isinstance(value, dict) else []


def _batch_compat_trigger_tasks(value: dict[str, Any]) -> list[dict[str, Any]]:
    direct = value.get("compat_trigger_tasks")
    if isinstance(direct, list):
        return _dedupe_tasks([dict(item) for item in direct if isinstance(item, dict)])
    raw_tasks = value.get("tasks")
    tasks = (
        [dict(item) for item in raw_tasks if isinstance(item, dict)]
        if isinstance(raw_tasks, list)
        else _batch_tasks(value)
    )
    collected: list[dict[str, Any]] = []
    for task in tasks:
        raw = task.get("_aics_compat_trigger_tasks")
        if isinstance(raw, list):
            collected.extend(dict(item) for item in raw if isinstance(item, dict))
    return _dedupe_tasks(collected)


def _dedupe_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for task in tasks:
        task_id = _task_id(task)
        if not task_id or task_id in seen:
            continue
        seen.add(task_id)
        output.append(task)
    return output


def _compat_contact_key(task: dict[str, Any]) -> str:
    identity = _task_identity(task)
    return "|".join(
        (
            identity["corp_id"].lower(),
            identity["wechat"].lower(),
            identity["external_userid"].lower(),
        )
    )


def _resolve_compatible_pending_tasks(
    online_items: list[dict[str, Any]],
    store_visit_items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve legacy inline tasks and new time-trigger/full-content task pairs."""

    legacy_tasks = [
        {**item, "_aics_biz_type": "online_service", "_aics_content_source": "pending_inline"}
        for item in online_items
        if isinstance(item, dict) and _platform_messages(item)
    ]
    empty_triggers = [
        {**item, "_aics_biz_type": "online_service", "_aics_content_source": "pending_time_trigger"}
        for item in online_items
        if isinstance(item, dict) and not _platform_messages(item)
    ]
    full_content_by_contact: dict[str, list[dict[str, Any]]] = {}
    for item in store_visit_items:
        if not isinstance(item, dict) or not _platform_messages(item):
            continue
        content_task = {
            **item,
            "_aics_biz_type": "online_service",
            "_aics_content_source": "store_visit_pending",
        }
        full_content_by_contact.setdefault(_compat_contact_key(content_task), []).append(content_task)

    triggers_by_contact: dict[str, list[dict[str, Any]]] = {}
    for trigger in empty_triggers:
        triggers_by_contact.setdefault(_compat_contact_key(trigger), []).append(trigger)

    resolved = list(legacy_tasks)
    unresolved: list[dict[str, Any]] = []
    legacy_ids = {_task_id(task) for task in legacy_tasks}
    for contact_key, triggers in triggers_by_contact.items():
        content_tasks = full_content_by_contact.get(contact_key) or []
        if not content_tasks:
            unresolved.extend(triggers)
            continue
        for content_task in content_tasks:
            if _task_id(content_task) in legacy_ids:
                continue
            resolved.append(
                {
                    **content_task,
                    "_aics_compat_trigger_tasks": _dedupe_tasks(triggers),
                }
            )
    return _dedupe_tasks(resolved), _dedupe_tasks(unresolved)


def _task_batch_sort_key(task: dict[str, Any]) -> tuple[float, int, str]:
    try:
        sort_order = int(task.get("sortOrder") or task.get("sort_order") or 0)
    except (TypeError, ValueError):
        sort_order = 0
    return (_task_scheduled_epoch(task) or float("inf"), sort_order, _task_id(task))


def _customer_batch_key(task: dict[str, Any]) -> str:
    identity = _task_identity(task)
    return "|".join(
        (
            str(task.get("_aics_biz_type") or "online_service"),
            identity["corp_id"].lower(),
            identity["wechat"].lower(),
            identity["external_userid"].lower(),
        )
    )


def _batch_identity_is_consistent(tasks: list[dict[str, Any]], *, identity: dict[str, str]) -> bool:
    expected = tuple(identity[key].lower() for key in ("corp_id", "wechat", "external_userid"))
    return all(
        tuple(_task_identity(task)[key].lower() for key in ("corp_id", "wechat", "external_userid"))
        == expected
        for task in tasks
    )


def _in_configured_quiet_hours(*, settings: Any, now: datetime | None = None) -> bool:
    if not bool(getattr(settings, "sop_platform_quiet_hours_enabled", True)):
        return False
    local_now = now.astimezone(_BEIJING_TZ) if now is not None else datetime.now(_BEIJING_TZ)
    start = _bounded_hour(getattr(settings, "sop_platform_quiet_start_hour", 0), default=0)
    end = _bounded_hour(getattr(settings, "sop_platform_quiet_end_hour", 8), default=8)
    if start == end:
        return True
    if start < end:
        return start <= local_now.hour < end
    return local_now.hour >= start or local_now.hour < end


def _quiet_hours_no_replay_decision(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "evaluations": [
            {
                "task_id": _task_id(task),
                "decision": "skip",
                "reason": "quiet_hours_no_replay",
                "evidence_refs": [f"task:{_task_id(task)}"],
            }
            for task in tasks
        ],
        "selected_task_id": "",
        "transition_text": "",
        "decision_source": "deterministic_quiet_hours",
    }


def _quiet_hours_no_replay_archive(tasks: list[dict[str, Any]], *, settings: Any) -> dict[str, Any]:
    start = _bounded_hour(getattr(settings, "sop_platform_quiet_start_hour", 0), default=0)
    end = _bounded_hour(getattr(settings, "sop_platform_quiet_end_hour", 8), default=8)
    ordered_tasks = sorted(tasks, key=_task_batch_sort_key)
    return {
        "recorded_at": utc_now_iso(),
        "timezone": "Asia/Shanghai",
        "window": f"{start:02d}:00-{end:02d}:00",
        "no_replay": True,
        "ordered_groups": [
            {
                "sequence": index,
                "task_id": _task_id(task),
                "scheduled_at": task.get("scheduledAt") or task.get("scheduled_at") or "",
                "sort_order": task.get("sortOrder") or task.get("sort_order"),
                "rule_name": task.get("ruleName") or task.get("sceneName") or "",
                "content_available": bool(_platform_messages(task)),
                "original_messages": _platform_messages(task),
            }
            for index, task in enumerate(ordered_tasks, start=1)
        ],
    }


def _deferred_replay_interval_seconds(settings: Any) -> int:
    return max(
        60,
        int(getattr(settings, "sop_platform_deferred_replay_interval_seconds", 600) or 600),
    )


def _deferred_replay_day_bounds(now: datetime | None = None) -> tuple[str, str]:
    beijing = timezone(timedelta(hours=8))
    current = (now or datetime.now(timezone.utc)).astimezone(beijing)
    local_start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    local_end = local_start + timedelta(days=1)
    return (
        local_start.astimezone(timezone.utc).isoformat(),
        local_end.astimezone(timezone.utc).isoformat(),
    )


def _deferred_replay_marker(record: dict[str, Any]) -> dict[str, Any]:
    payload = record.get("send_payload") if isinstance(record.get("send_payload"), dict) else {}
    marker = payload.get("deferred_replay") if isinstance(payload.get("deferred_replay"), dict) else {}
    return dict(marker)


def _deferred_replay_record_status(record: dict[str, Any]) -> str:
    marker_status = str(_deferred_replay_marker(record).get("status") or "").strip().lower()
    if marker_status:
        return marker_status
    task_status = str(record.get("task_status") or "").strip().lower()
    if task_status == "sent":
        return "sent"
    if task_status == "deferred_replay_sending":
        return "sending"
    if task_status == "deferred_replay_retry":
        return "retry"
    return "pending"


def _is_deferred_replay_record(record: dict[str, Any]) -> bool:
    payload = record.get("send_payload") if isinstance(record.get("send_payload"), dict) else {}
    reason = str(payload.get("reason") or "").strip()
    marker = _deferred_replay_marker(record)
    if marker:
        return str(marker.get("source_reason") or reason) in {
            "quiet_hours_no_replay",
            "deferred_behind_quiet_backlog",
        }
    archive = payload.get("quiet_hours_archive") if isinstance(payload.get("quiet_hours_archive"), dict) else {}
    return reason == "quiet_hours_no_replay" and bool(archive)


def _deferred_replay_queue_decision(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "evaluations": [
            {
                "task_id": _task_id(task),
                "decision": "queued",
                "reason": "deferred_behind_quiet_backlog",
                "evidence_refs": [f"task:{_task_id(task)}"],
            }
            for task in tasks
        ],
        "selected_task_id": "",
        "transition_text": "",
        "decision_source": "deterministic_deferred_replay_queue",
    }


def _conversation_ai_auto_reply(data: dict[str, Any]) -> bool | None:
    candidates = [
        data.get("ai_auto_reply"),
        data.get("aiAutoReply"),
    ]
    for key in ("conversation", "customer", "session", "takeover", "ai_outreach"):
        nested = data.get(key) if isinstance(data.get(key), dict) else {}
        candidates.extend((nested.get("ai_auto_reply"), nested.get("aiAutoReply")))
    for value in candidates:
        if isinstance(value, bool):
            return value
        normalized = str(value or "").strip().lower()
        if normalized in {"1", "true", "yes", "on", "ai"}:
            return True
        if normalized in {"0", "false", "no", "off", "human", "manual"}:
            return False
    return None


def _compact_management_status(data: dict[str, Any]) -> dict[str, Any]:
    takeover = data.get("takeover") if isinstance(data.get("takeover"), dict) else {}
    outreach = data.get("ai_outreach") if isinstance(data.get("ai_outreach"), dict) else {}
    return {
        "conversation_id": str(data.get("conversation_id") or ""),
        "mode": str(takeover.get("mode") or ""),
        "handoff_status": str(takeover.get("handoff_status") or ""),
        "reason_code": str(takeover.get("reason_code") or outreach.get("reason_code") or ""),
        "send_allowed": outreach.get("send_allowed") if isinstance(outreach.get("send_allowed"), bool) else None,
    }


def _is_same_day_unopened(tasks: list[dict[str, Any]], *, timeline: list[dict[str, Any]]) -> bool:
    add_task = next(
        (
            task
            for task in tasks
            if str(task.get("triggerEvent") or task.get("trigger_event") or "").strip().lower()
            == "add_wecom"
        ),
        None,
    )
    if not add_task:
        return False
    add_epoch = _parse_epoch(
        add_task.get("operateTime")
        or add_task.get("operate_time")
        or add_task.get("createTime")
        or add_task.get("create_time")
        or add_task.get("scheduledAt")
        or add_task.get("scheduled_at")
    )
    if not add_epoch:
        return False
    if datetime.fromtimestamp(add_epoch, tz=_BEIJING_TZ).date() != datetime.now(_BEIJING_TZ).date():
        return False
    for item in timeline:
        if item.get("role") != "customer":
            continue
        content = str(item.get("content") or "").strip()
        if content and not is_platform_auto_opening_message(content):
            return False
    return True


def _batch_decision_error(
    raw: Any,
    *,
    tasks: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> str:
    if not isinstance(raw, dict):
        return "output must be an object"
    if set(raw).difference({"evaluations", "selected_task_id", "transition_text"}):
        return "output contains unexpected fields"
    evaluations = raw.get("evaluations")
    if not isinstance(evaluations, list) or not evaluations:
        return "evaluations must be a non-empty list"
    ordered_ids = [_task_id(task) for task in tasks]
    selected_id = str(raw.get("selected_task_id") or "").strip()
    seen_send = False
    valid_refs = {f"task:{task_id}" for task_id in ordered_ids}
    for item in (context or {}).get("conversation_timeline", []):
        if isinstance(item, dict) and str(item.get("message_ref") or "").strip():
            valid_refs.add(str(item["message_ref"]))
    for index, evaluation in enumerate(evaluations):
        if not isinstance(evaluation, dict):
            return f"evaluation {index + 1} must be an object"
        if set(evaluation).difference({"task_id", "decision", "reason", "evidence_refs"}):
            return f"evaluation {index + 1} contains unexpected fields"
        task_id = str(evaluation.get("task_id") or "").strip()
        if index >= len(ordered_ids) or task_id != ordered_ids[index]:
            return "evaluations must follow pending task order without gaps"
        decision = str(evaluation.get("decision") or "").strip()
        if decision not in {"skip", "send"}:
            return f"evaluation {index + 1} decision must be skip or send"
        if not str(evaluation.get("reason") or "").strip():
            return f"evaluation {index + 1} reason is required"
        refs = evaluation.get("evidence_refs")
        if not isinstance(refs, list) or any(str(ref) not in valid_refs for ref in refs):
            return f"evaluation {index + 1} evidence_refs contain unknown references"
        if decision == "send":
            if seen_send or index != len(evaluations) - 1:
                return "the first send decision must end evaluations"
            seen_send = True
            if selected_id != task_id:
                return "selected_task_id must equal the send evaluation task_id"
    if seen_send:
        if not selected_id:
            return "selected_task_id is required when a task is sendable"
    else:
        if selected_id:
            return "selected_task_id must be empty when all tasks are skipped"
        if len(evaluations) != len(tasks):
            return "all tasks must be evaluated when none is sendable"
        if str(raw.get("transition_text") or "").strip():
            return "transition_text must be empty when none is sendable"
    return ""


def _has_trusted_ai_copy_source(task: dict[str, Any]) -> bool:
    scene = task.get("scene") if isinstance(task.get("scene"), dict) else {}
    engine = scene.get("engine") if isinstance(scene.get("engine"), dict) else {}
    return bool(
        str(
            task.get("triggerEvent")
            or task.get("trigger_event")
            or task.get("eventType")
            or task.get("event_type")
            or ""
        ).strip()
    ) or any(
        str(value or "").strip()
        for value in (
            scene.get("sceneDesc"),
            scene.get("knowledgeText"),
            engine.get("generateNote"),
        )
    )


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


def _material_catalog_for_model(service: Any | None) -> list[dict[str, Any]]:
    if service is None:
        return []
    try:
        payload = service.load()
    except Exception as exc:
        logger.warning("Unable to load SOP objection materials: %s: %s", type(exc).__name__, exc)
        return []
    materials = payload.get("materials") if isinstance(payload, dict) else []
    if not isinstance(materials, list):
        return []
    output: list[dict[str, Any]] = []
    for item in materials[:100]:
        if not isinstance(item, dict):
            continue
        output.append(
            {
                "material_id": str(item.get("material_id") or "")[:120],
                "name": str(item.get("name") or "")[:160],
                "category": str(item.get("category") or "")[:120],
                "tags": [str(value)[:80] for value in item.get("tags", [])[:20]],
                "applicable_scenes": [
                    str(value)[:120] for value in item.get("applicable_scenes", [])[:20]
                ],
                "response_approach": str(item.get("response_approach") or "")[:1000],
                "example_contents": [
                    str(value)[:1000] for value in item.get("example_contents", [])[:10]
                ],
            }
        )
    return output


def _context_audit(context: dict[str, Any]) -> dict[str, Any]:
    relation = context.get("customer_relation") if isinstance(context.get("customer_relation"), dict) else {}
    customer_context = context.get("business_state") if isinstance(context.get("business_state"), dict) else {}
    quiet_hours = context.get("quiet_hours") if isinstance(context.get("quiet_hours"), dict) else {}
    timeline_structure = (
        context.get("timeline_structure") if isinstance(context.get("timeline_structure"), dict) else {}
    )
    return {
        "source": str(context.get("source") or ""),
        "management_mode": str(context.get("management_mode") or ""),
        "management_source": str(context.get("management_source") or ""),
        "customer_opened": context.get("customer_opened") if isinstance(context.get("customer_opened"), bool) else None,
        "same_day_unopened": (
            context.get("same_day_unopened") if isinstance(context.get("same_day_unopened"), bool) else None
        ),
        "conversation_count": int(
            context.get("conversation_count") or timeline_structure.get("message_count") or 0
        ),
        "timeline_structure": timeline_structure,
        "customer_relation": relation,
        "customer_context_source": customer_context.get("source"),
        "customer_context_error": customer_context.get("error"),
        "task_timing": context.get("task_timing") if isinstance(context.get("task_timing"), dict) else {},
        "quiet_hours": quiet_hours,
    }
