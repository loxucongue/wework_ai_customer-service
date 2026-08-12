from __future__ import annotations

import asyncio
import json
import re
import time
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.policies.sales_flow import (
    mainline_pack_sort_key,
    mainline_stage_for_event_pack,
    mainline_stage_for_event_values,
    mainline_stage_for_pack,
    precision_qa_for_id,
    precision_qa_index_for_gate,
    sales_mainline_for_model,
)
from app.prompts.global_contract import GLOBAL_STRUCTURED_NODE_CONTRACT
from app.prompts.sop_chat_gate import build_sop_chat_gate_messages, build_sop_chat_gate_repair_messages
from app.chat_request_context import is_isolated_v2_test_request
from app.schemas import ChatRequest
from app.services.customer_payment_state import is_paid_deposit_state, resolved_payment_fact
from app.services.customer_scope import customer_scope_from_identity
from app.services.model_client import ModelClient
from app.services.model_led_objection_playbook_service import ModelLedObjectionPlaybookService
from app.services.sop_event_decision import normalize_event_decision, selected_candidate_packs
from app.services.sop_message_sanitizer import apply_sop_text_adjustments, sanitize_sop_reply_messages
from app.services.sop_reply_pack_service import SopReplyPackService
from app.services.storage.serialization import utc_now_iso
from app.services.trace_logger import compact


FIRST_ADD_NEXT_STEP_LOOKAHEAD_MINUTES = 0
FIRST_ADD_NEXT_STEP_MAX_CANDIDATES = 1


SOP_EVENT_SYSTEM_PROMPT = f"""
{GLOBAL_STRUCTURED_NODE_CONTRACT}

# Role
你是 `/sop/events` 主动触达决策模型。你负责结合完整聊天、已交付证据、合法候选、频率和安全事实，决定本轮是否触达、交付哪一个新价值以及使用多强的行动引导。代码只提供资格、事实和结构保护，不替你决定销售节奏。

固定新客开场由协议直接发送，不由你改写。`mode=platform_actions` 时必须原样转发平台 `message_content` 的类型、顺序、正文和 URL；除有效忙碌保护或硬安全冲突外选择 `send`，并保持 `text_adjustments=[]`、`message_operations=[]`。

# Input Authority
- 最新客户消息与最新会话事实优先。
- `candidate_sops` 已通过到期、客户范围、发送资格、频率和结构过滤；候选顺序只供审计，不是强制主线。
- `mainline_stage_status`、完成记录和发送记录用于识别历史已交付价值、防止重复并核验付款前置，不要求选择最早阶段。
- `customer_fact_snapshot` 只提供支付、门店、订单、发送和风险等权威事实，不是客户心理结论。

# Decision Protocol
1. 先判断现在是否适合主动触达。最新问题待回复、实时聊天、明确工作中、健康风险、投诉退款、强拒绝或要求停止联系时，按事实 defer、skip 或 safety notice，不做营销压单。
2. 识别历史已经交付的地址、效果、活动和排疑价值。客户不需要明确确认某项价值已经接受，但继续质疑时该问题仍然存在。
3. 从所有合法到期候选中选择一个历史未重复、最能降低当前决策不确定性的价值。不得按 raw order、最早阶段或固定流程机械选择。
4. 默认每轮一个价值目标。只有夜间积压且两个资产互补、服务同一个客户目标时才 `merge`；最多两个，不堆地址、效果、活动和付款。
5. 没有合适固定包但存在真实新价值时可 `send_ai_touch`。轻触必须交付新事实、证据价值或降低行动成本，不能只问“考虑得怎么样、还有什么想了解、今天几点来”。没有任何历史未重复的新价值时才 skip/defer。
6. CTA 强度由完整历史决定：证据不足就直接交付证据；缺少会改变下一步的信息只问一个问题；已有到店意向可收敛到日期或时段；活动和成交基础成熟且存在付款行动信号时及时成交；暂停边界不推进。

# Sales Evidence Principles
- 真实案例、门店卡和完整活动资产可直接交付，不先询问客户是否需要。文字负责承接和推进，素材负责证明。
- 同一内容说过一次后，客户沉默时换证据、换价值或降低行动成本，不换句话重复催促。
- 可按当前阻力使用这些权威事实：整体过程约45～50分钟；做完不影响正常工作和生活；完成线上活动登记后可先到店了解和检测，确认适合、满意再操作。它们是可选证据，不是固定模板；45～50分钟不是交通时间，到店了解必须保留活动登记前提。
- 活动报价是预约金卡和催付的硬前置。首次活动介绍不能同轮发卡；订单不是发卡前置。
- 选择包含预约金卡的候选时，必须填写 `payment_readiness_evidence`：`customer_action_ref` 引用最近一条真实客户行动消息，`supporting_value_ref` 引用该消息之前已经交付地址、效果或排疑价值的真实助手消息。你负责判断语义；代码只核验引用、角色和先后顺序。纯沉默、只有助手消息或缺少另一项真实价值时不得选择收款卡。

# Hard Boundaries
- 只选择 `candidate_sops` 中真实 ID；不得选择已完成 ID/类目，不得编造门店、案例、图片、价格、支付、赠品、老师、档期、接送或效果承诺。
- `send` 只选一个包。`merge` 必须正好两个互补包。`send_ai_touch` 和 safety notice 只能输出 text。
- 已付、健康风险、投诉退款、明确拒付或 `payment_collection_gate` 不支持时不得发送预约金卡。`activity_intro_required` 只能用真实活动完成证据解除，不能靠删卡绕过。
- 平台频率证据达到保护条件且没有新客户进展时应 skip/defer。历史累计次数不能永久阻止触达。
- 文本可以为当前聊天自然改写，但不得更改数字、承诺边界或只读结构素材；采用资产后必须完整交付该资产的真实图片或卡片。

# Output
只输出严格 json：
{{
  "decision": "send | merge | send_ai_touch | handoff_or_safety_notice | skip | defer | handoff_to_ai_reply",
  "strategy": "continue_mainline | recover_backlog | soft_touch | safety_notice | conflict_guard | frequency_guard | realtime_handoff",
  "selected_pack_ids": ["send 为1个真实候选；merge 为2个互补且服务同一目标的真实候选"],
  "merge_pack_ids": [],
  "touch_goal": "resume_mainline | soften_objection | collect_info | payment_followup | visit_followup | safety_handoff | none",
  "ai_touch_messages": [{{"type":"text","content":{{"text":"仅触达分支的客户可见短句"}}}}],
  "skip_reason": "",
  "frequency_reason": "",
  "backlog_handling": "none | recover_one | merge_two",
  "suggested_next_window": "",
  "reason": "一句基于证据的内部原因",
  "stage_skip_evidence": [{{"stage_id":"","pack_id":"","evidence":"仅用于历史覆盖和付款前置审计"}}],
  "payment_readiness_evidence": {{"customer_action_ref":"仅发预约金卡时填写最近客户消息ref","supporting_value":"address | effect | objection | none","supporting_value_ref":"行动消息之前的助手消息ref","reason":"模型的简短语义判断"}},
  "contact_availability_decision": {{"status":"available | busy_now | unknown","customer_evidence_ref":"","assistant_acknowledgement_ref":"","reason":""}},
  "text_adjustments": [{{"order":1,"text":"仅改写已有 text"}}],
  "message_operations": [{{"op":"insert_text_after","after_order":1,"text":"只新增无新事实的承接 text"}}]
}}
""".strip()

SOP_EVENT_SYSTEM_PROMPT += """

# Contact Availability Contract
- `recent_conversation` contains the latest 30 messages. Every item has a stable `message_ref`.
- `customer_fact_snapshot` contains durable structured facts only. Do not infer current psychology from old profile summaries.
- `contact_availability_evidence` only describes message order and elapsed time. You must decide whether the customer is currently available.
- If the customer explicitly said they are busy, working, driving, or will talk later, and a later assistant message acknowledged waiting, output `status=busy_now` and cite both message refs.
- Do not reuse an old busy state when any newer customer message exists after the cited acknowledgement. The newest customer message always wins.
- With valid `busy_now`: within 360 minutes choose only `skip` or `defer`; after 360 minutes choose `skip`, `defer`, or one low-pressure text through `send_ai_touch`.
- A busy touch must contain at most one text message. It must not contain an SOP pack, image, video, or payment_collection.
- This availability protection applies in both `first_add_flow` and `platform_actions`. Platform content priority cannot override a valid current busy state.
- When the current customer message asks how to pay or asks for the payment card, treat it as new progress and resume the normal mainline.
- `daily_soft_limit_reached` alone is soft evidence. Combined with valid busy evidence and no new customer progress, follow the busy rules above.

Add this required object to the output JSON:
"contact_availability_decision": {
  "status": "available | busy_now | unknown",
  "customer_evidence_ref": "message_ref or empty",
  "assistant_acknowledgement_ref": "message_ref or empty",
  "reason": "brief evidence-based reason"
}

`strategy` may also be `availability_guard` when the decision is controlled by this contract.
"""


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
        model_led_objection_playbook_service: ModelLedObjectionPlaybookService | None = None,
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
        self.model_led_objection_playbook_service = model_led_objection_playbook_service
        self._event_model_semaphore = asyncio.Semaphore(max(1, int(event_model_max_concurrency or 1)))

    def reply_chain_content_catalog(self) -> dict[str, Any]:
        """Return a metadata-only index; Gate loads bodies through its own boundary."""
        packs = _parallel_candidate_packs(
            _enabled_chat_packs(self.sop_reply_pack_service.load())
        )
        distilled_service = getattr(self, "model_led_objection_playbook_service", None)
        distilled = (
            distilled_service.metadata_index()
            if distilled_service is not None
            else []
        )
        return {
            "schema_version": "reply_chain_content_index_v2",
            "sop_packs": [
                {
                    "content_id": str(pack.get("id") or ""),
                    "content_type": str(pack.get("content_type") or "sop"),
                    "name": str(pack.get("name") or ""),
                    "purpose": str(pack.get("purpose") or ""),
                    "asset_role": str(pack.get("asset_role") or "supporting_content"),
                    "requires_prior_asset_roles": [
                        str(item)
                        for item in pack.get("requires_prior_asset_roles") or []
                        if str(item or "").strip()
                    ],
                    "category": str(pack.get("sop_category") or ""),
                }
                for pack in packs
            ] + deepcopy(distilled),
            "sales_principles": (
                distilled_service.sales_principles()
                if distilled_service is not None
                else []
            ),
        }

    def reply_chain_sop_progress(
        self,
        request: ChatRequest,
        *,
        request_context: dict[str, Any],
    ) -> dict[str, Any]:
        """Return scoped SOP delivery facts without selecting content or writing state."""

        identity = _chat_identity(request, request_context)
        if not _string(identity.get("wechat")):
            return {
                "status": "scope_unavailable",
                "source": "scoped_sop_send_records",
                "reason": "wechat_required_for_sop_scope",
                "completed_pack_ids": [],
                "completed_categories": [],
                "unfinished_sops": [],
            }
        enabled_packs, completed_ids, completed_categories, unfinished = self._reply_chain_sop_progress_parts(
            identity
        )
        return {
            "status": "available",
            "source": "scoped_sop_send_records",
            "enabled_pack_count": len(enabled_packs),
            "completed_pack_ids": sorted(completed_ids),
            "completed_categories": sorted(completed_categories),
            "unfinished_sops": [_sop_progress_summary(pack) for pack in unfinished],
        }

    def _reply_chain_sop_progress_parts(
        self,
        identity: dict[str, str],
    ) -> tuple[list[dict[str, Any]], set[str], set[str], list[dict[str, Any]]]:
        enabled_packs = _enabled_chat_packs(self.sop_reply_pack_service.load())
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
        return enabled_packs, completed_ids, completed_categories, unfinished

    async def evaluate_chat_gate(
        self,
        request: ChatRequest,
        *,
        request_id: str,
        request_context: dict[str, Any],
        record_task: bool = True,
        shared_state: dict[str, Any] | None = None,
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
                test_isolated = is_isolated_v2_test_request(request, request_context)
                if not test_isolated:
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
                    record_task=record_task and not test_isolated,
                )
                return _finish(result, started)
            if request_context.get("skip_sop_gate"):
                result.update({"mode": "skipped", "reason": "skip_sop_gate"})
                return _finish(result, started)
            # The active parallel chain sends every normalized message type to
            # Gate and Tool Planner together. Legacy pre-routing remains only
            # for callers that still use the committing Gate API.
            if record_task:
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
            progress_from_shared = _reply_chain_sop_progress_from_shared_state(shared_state)
            if progress_from_shared:
                enabled_packs = _enabled_chat_packs(self.sop_reply_pack_service.load())
                completed_ids = set(progress_from_shared.get("completed_pack_ids") or [])
                completed_categories = set(progress_from_shared.get("completed_categories") or [])
                unfinished_ids = {
                    _string(item.get("id") or item.get("content_id"))
                    for item in progress_from_shared.get("unfinished_sops") or []
                    if isinstance(item, dict)
                }
                unfinished = [
                    pack for pack in enabled_packs if _string(pack.get("id")) in unfinished_ids
                ]
            else:
                enabled_packs, completed_ids, completed_categories, unfinished = (
                    self._reply_chain_sop_progress_parts(identity)
                )
            parallel_candidate_mode = shared_state is not None and not record_task
            distilled_service = getattr(self, "model_led_objection_playbook_service", None)
            distilled_assets = (
                distilled_service.gate_assets()
                if parallel_candidate_mode and distilled_service is not None
                else []
            )
            if not enabled_packs and not distilled_assets:
                result.update({"mode": "complete", "reason": "no_enabled_sop_packs"})
                return _finish(result, started)
            recent_delivery_evidence = _recent_chat_sop_delivery_evidence(
                self.repository,
                identity,
            )
            result["completed_sop_pack_ids"] = sorted(completed_ids)
            result["completed_sop_categories"] = sorted(completed_categories)
            result["unfinished_count"] = len(unfinished)
            result["sop_progress_evidence"] = progress_from_shared or {
                "status": "available",
                "source": "scoped_sop_send_records",
                "enabled_pack_count": len(enabled_packs),
                "completed_pack_ids": sorted(completed_ids),
                "completed_categories": sorted(completed_categories),
                "unfinished_sops": [_sop_progress_summary(pack) for pack in unfinished],
            }
            candidate_pool = (
                [*_parallel_candidate_packs(enabled_packs), *distilled_assets]
                if parallel_candidate_mode
                else unfinished
            )
            if not candidate_pool:
                result.update({"mode": "complete", "reason": "all_sop_packs_completed"})
                return _finish(result, started)

            customer_memory = {} if shared_state is not None else self._load_chat_customer_memory(identity)
            order_gate = (
                _chat_order_gate_from_shared_state(shared_state)
                if shared_state is not None
                else self._load_chat_order_gate(
                    request=request,
                    request_context=request_context,
                    identity=identity,
                    customer_memory=customer_memory,
                )
            )
            result["order_gate"] = order_gate.get("summary", {})
            if record_task and _apply_chat_order_gate_block(result, order_gate):
                return _finish(result, started)
            selector_input = _chat_selector_input(
                request,
                candidate_pool,
                sop_progress_evidence=result["sop_progress_evidence"],
                recent_delivery_evidence=recent_delivery_evidence,
                customer_memory=customer_memory,
                customer_context=order_gate.get("customer_context", {}),
                shared_context=(shared_state or {}).get("shared_context") if shared_state is not None else None,
                completed_pack_ids=completed_ids,
            )
            if shared_state is not None:
                selector_input["reply_chain_mode"] = "parallel_candidate_only"
                selector_input["content_assets"] = selector_input.pop("unfinished_sops", [])
                selector_input["candidate_boundary"] = {
                    "purpose": "nominate relevant approved content or distilled evidence guidance for final Reply",
                    "route_is_advisory": True,
                    "may_decide_final_customer_reply": False,
                    "may_decide_sales_action": False,
                    "may_plan_tools": False,
                }
            result["selector_input"] = compact(selector_input, max_chars=6000)
            selector_output = await self._select_chat_sop(
                selector_input,
                deadline_monotonic=time.monotonic() + self.chat_gate_total_timeout_seconds,
            )
            result["selector_output"] = selector_output
            result["decision"] = _string(selector_output.get("decision"))
            candidate_packs = _candidate_packs(selector_output, candidate_pool)
            result["selected_pack_ids"] = [_string(item.get("id")) for item in candidate_packs]
            result["frequency_reason"] = _string(selector_output.get("frequency_reason"))
            result["backlog_handling"] = _string(selector_output.get("backlog_handling"))
            result["suggested_next_window"] = _string(selector_output.get("suggested_next_window"))
            result["model_usage"] = dict(self.model_client.last_usage or {})
            if shared_state is not None and not record_task:
                annotations = {
                    _string(item.get("content_id")): item
                    for item in selector_output.get("candidate_assets") or []
                    if isinstance(item, dict) and _string(item.get("content_id"))
                }
                candidate_items = [
                    _parallel_content_candidate(
                        pack,
                        annotations.get(_string(pack.get("id")), {}),
                        completed_pack_ids=completed_ids,
                    )
                    for pack in candidate_packs
                ]
                result.update(
                    {
                        "mode": "candidate_only",
                        "route": "candidate_only",
                        "coverage": "candidate_evidence",
                        "send_sop": bool(candidate_items),
                        "sop_pack_id": "",
                        "sop_pack_name": "",
                        "need_ai_reply": True,
                        "reason": str(selector_output.get("reason") or ""),
                        "reply_messages": [],
                        "candidate_packs": candidate_items,
                        "task": {},
                        "candidate_only": True,
                    }
                )
                return _finish(result, started)
            route = _chat_gate_route(selector_output)
            result["route"] = route
            result["coverage"] = _chat_gate_coverage(selector_output)
            selected_scene_id = _string(
                selector_output.get("selected_scene_id") or selector_output.get("priority_question_id")
            )
            result["selected_scene_id"] = selected_scene_id
            result["priority_question_id"] = selected_scene_id
            result["resume_stage"] = _string(selector_output.get("resume_stage"))
            result["active_task"] = _chat_gate_active_task(selector_output.get("active_task"))
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

            if not record_task:
                result.update(
                    {
                        "mode": route,
                        "send_sop": True,
                        "sop_pack_id": str(selected.get("id") or ""),
                        "sop_pack_name": str(selected.get("name") or ""),
                        "need_ai_reply": True,
                        "reason": str(selector_output.get("reason") or ""),
                        "reply_messages": messages,
                        "task": {},
                        "candidate_only": True,
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
        record_task: bool = True,
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

        task = (
            self._record_chat_gate_task(
                request=request,
                request_id=request_id,
                request_context=request_context,
                identity=identity,
                pack=pack,
                reply_messages=messages,
                trigger_source="platform_auto_opening",
            )
            if record_task
            else {
                "status": "sent",
                "created": False,
                "trigger_source": "platform_auto_opening",
                "test_isolated": True,
            }
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
        model_retry_applied = False
        for attempt in range(2):
            try:
                data = await self.model_client.chat_json(
                    build_sop_chat_gate_messages(selector_input),
                    tier="reply",
                    temperature=0,
                    deadline_monotonic=deadline_monotonic,
                )
                break
            except Exception:
                if attempt > 0:
                    raise
                remaining_seconds = (
                    deadline_monotonic - time.monotonic()
                    if deadline_monotonic is not None
                    else None
                )
                if remaining_seconds is not None and remaining_seconds < 1.0:
                    raise
                model_retry_applied = True
        output = data if isinstance(data, dict) else {}
        parallel_mode = _string(selector_input.get("reply_chain_mode")) == "parallel_candidate_only"
        validator = _parallel_content_gate_output_violations if parallel_mode else _chat_gate_output_violations
        violations = validator(output, selector_input)
        if not violations:
            if model_retry_applied:
                output["model_retry_applied"] = True
            return output
        repaired = await self.model_client.chat_json(
            build_sop_chat_gate_repair_messages(selector_input, output, violations),
            tier="reply",
            temperature=0,
            deadline_monotonic=deadline_monotonic,
        )
        repaired_output = repaired if isinstance(repaired, dict) else {}
        repaired_violations = validator(repaired_output, selector_input)
        if not repaired_violations:
            repaired_output["repair_applied"] = True
            repaired_output["initial_violations"] = violations
            if model_retry_applied:
                repaired_output["model_retry_applied"] = True
            return repaired_output
        if parallel_mode:
            return {
                "candidate_assets": [],
                "reason": "content_gate_invalid_after_repair_continue_reply",
                "initial_violations": violations,
                "repair_violations": repaired_violations,
            }
        return {
            "route": "ai_only",
            "coverage": "none",
            "priority_question_id": "",
            "selected_scene_id": "",
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
                    send_sop = bool(send_sop and messages)
                    result["reply_messages"] = messages
                    result["message_sanitize"] = sanitize_summary
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
                    "上一份 JSON 违反主动 SOP 决策结构合同。只修正枚举、候选包数量、真实候选 ID、"
                    "已完成包幂等、结构消息发送资格和交接资格；"
                    "若候选的 payment_collection_gate 是 paid_skip_card，"
                    "且该阶段仍应触达，保留候选包并用 remove_message 删除每一张受限收款卡，同时改写相关 text；"
                    "activity_intro_required 不能靠删卡绕过，必须选择合法前序候选或拒发。"
                    "不要根据 violations 改写客户心理、强制选择某个阶段或生成固定轻触文案；只修结构与事实冲突。"
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

    def commit_reply_selected_chat_gate_candidate(
        self,
        *,
        state: dict[str, Any],
        reply_messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Record a Gate candidate only after Reply validation selected it."""

        if not state.get("memory_persist_allowed") or state.get("test_isolated"):
            return {"status": "skipped", "reason": "persistence_disabled_or_isolated"}
        gate = state.get("content_gate_result") if isinstance(state.get("content_gate_result"), dict) else {}
        candidate = gate.get("candidate_commit") if isinstance(gate.get("candidate_commit"), dict) else {}
        offered_ids = {
            _string(item)
            for item in candidate.get("sop_pack_ids") or []
            if _string(item)
        }
        selected_ids = {
            _string(item)
            for item in state.get("selected_content_ids") or []
            if _string(item)
        }
        committed_ids = offered_ids & selected_ids
        completed_candidate_ids = {
            _string(item.get("content_id"))
            for item in gate.get("content_candidates") or []
            if isinstance(item, dict)
            and _string(item.get("delivery_status")) == "completed"
            and _string(item.get("content_id"))
        }
        reference_only_ids = committed_ids & completed_candidate_ids
        committed_ids -= reference_only_ids
        if not committed_ids:
            return {
                "status": "skipped",
                "reason": (
                    "selected_candidates_already_completed_reference_only"
                    if reference_only_ids
                    else "reply_did_not_select_sop_candidate"
                ),
                "offered_sop_pack_ids": sorted(offered_ids),
                "reference_only_sop_pack_ids": sorted(reference_only_ids),
            }
        packs_by_id = {
            _string(item.get("id")): item
            for item in _enabled_chat_packs(self.sop_reply_pack_service.load())
            if isinstance(item, dict) and _string(item.get("id"))
        }
        request = ChatRequest(
            content=_string(state.get("content")),
            customer_id=_string(state.get("customer_id")),
            corp_id=_string(state.get("corp_id")),
            conversation_history=list(state.get("conversation_history") or []),
            file_image=state.get("file_image"),
            user_id=state.get("user_id"),
            wechat=state.get("wechat"),
            external_userid=state.get("external_userid"),
            customer_add_wechat_id=state.get("customer_add_wechat_id"),
            confirmed_store_id=state.get("confirmed_store_id"),
            confirmed_store_name=state.get("confirmed_store_name"),
            store_id=state.get("store_id"),
            store_name=state.get("store_name"),
            appointment_id=state.get("appointment_id"),
            appointment_time=state.get("appointment_time"),
            request_context=dict(state.get("request_context") or {}),
        )
        identity = _chat_identity(request, dict(state.get("request_context") or {}))
        records: list[dict[str, Any]] = []
        missing_ids: list[str] = []
        for pack_id in sorted(committed_ids):
            pack = packs_by_id.get(pack_id)
            if not isinstance(pack, dict):
                missing_ids.append(pack_id)
                continue
            records.append(
                self._record_chat_gate_task(
                    request=request,
                    request_id=_string(state.get("request_id")),
                    request_context=dict(state.get("request_context") or {}),
                    identity=identity,
                    pack=pack,
                    reply_messages=reply_messages,
                    trigger_source="parallel_reply_commit",
                    mark_sent=True,
                )
            )
        return {
            "status": "recorded" if records else "skipped",
            "recorded_sop_pack_ids": [
                _string(record.get("sop_pack_id"))
                for record in records
                if _string(record.get("sop_pack_id"))
            ],
            "missing_sop_pack_ids": missing_ids,
            "reference_only_sop_pack_ids": sorted(reference_only_ids),
            "records": records,
        }

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


def _parallel_candidate_packs(packs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply content-owner visibility without interpreting the customer message."""

    return [
        pack
        for pack in packs
        if isinstance(pack, dict) and bool(pack.get("parallel_candidate_enabled", True))
    ]


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
        proactive_asset = bool(pack.get("proactive_candidate_enabled"))
        if not proactive_asset and not _pack_has_scope(pack, "event_first_add"):
            continue
        if proactive_asset:
            pack_id = _string(pack.get("id"))
            if pack_id in completed or _pack_category(pack) in completed_categories:
                continue
            if (
                mainline_stage_for_event_pack(pack) == "activity_and_price"
                and "activity_and_price" in completed_mainline_stages
            ):
                continue
            candidates.append(
                _annotated_first_add_candidate(
                    pack,
                    group="due",
                    reason_hint="platform_triggered_proactive_content_asset",
                    prerequisite_status=_event_pack_prerequisite_status(pack, completed_categories),
                )
            )
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
        if bool(pack.get("proactive_candidate_enabled")):
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
    shared_context: dict[str, Any] | None = None,
    completed_pack_ids: set[str] | None = None,
) -> dict[str, Any]:
    shared = shared_context if isinstance(shared_context, dict) else {}
    if shared:
        current = shared.get("current_message") if isinstance(shared.get("current_message"), dict) else {}
        conversation_evidence = [
            {
                "message_ref": _string(item.get("message_ref")),
                "direction": _string(item.get("role")),
                "content": _string(item.get("content")),
                "sent_at": item.get("sent_at") or item.get("timestamp") or item.get("created_at"),
            }
            for item in shared.get("conversation") or []
            if isinstance(item, dict) and _string(item.get("content"))
        ]
        current_message = _string(current.get("content") or current.get("raw_content"))
        recent_conversation = conversation_evidence
        authoritative = (
            shared.get("authoritative_facts")
            if isinstance(shared.get("authoritative_facts"), dict)
            else {}
        )
        authoritative_context = {
            key: deepcopy(authoritative.get(key))
            for key in (
                "orders_and_payment",
                "visible_store_scope",
                "sent_messages",
                "image_or_transfer_fact",
                "location_card",
            )
            if authoritative.get(key) not in (None, "", [], {})
        }
        progress = authoritative.get("sop_progress") if isinstance(authoritative.get("sop_progress"), dict) else {}
        authoritative_context["content_delivery_progress"] = {
            key: deepcopy(progress.get(key))
            for key in ("status", "source", "completed_pack_ids", "completed_categories")
            if progress.get(key) not in (None, "", [], {})
        }
    else:
        current_message = str(request.content or "").strip()
        conversation_evidence = _chat_conversation_evidence(
            request.conversation_history,
            current_message=current_message,
        )
        recent_conversation = _recent_history(request.conversation_history)
        authoritative_context = {}
    payload = {
        "current_time": deepcopy(shared.get("current_time") or {}),
        "current_message": current_message,
        "recent_conversation": recent_conversation,
        "conversation_evidence": conversation_evidence,
        "authoritative_context": authoritative_context,
        "recent_sop_delivery_evidence": recent_delivery_evidence or [],
        "unfinished_sops": [
            (
                _parallel_sop_asset_summary(pack, completed_pack_ids=completed_pack_ids)
                if shared
                else _sop_summary(
                    pack,
                    customer_memory=customer_memory or {},
                    customer_context=customer_context or {},
                )
            )
            for pack in unfinished_packs
        ],
    }
    if not shared:
        payload["mainline_progress"] = sop_progress_evidence or {}
        payload["mainline"] = sales_mainline_for_model()
        payload["precision_qa_index"] = precision_qa_index_for_gate()
    return payload


def _chat_order_gate_from_shared_state(state: dict[str, Any]) -> dict[str, Any]:
    """Reuse the background order snapshot without adding a second fetch.

    This function only exposes payment/order facts to Gate. It does not decide
    whether the customer should receive a sales message; Reply owns that
    semantic decision and final hard validation still protects paid customers.
    """

    shared = state.get("shared_context") if isinstance(state.get("shared_context"), dict) else {}
    authoritative = (
        shared.get("authoritative_facts")
        if isinstance(shared.get("authoritative_facts"), dict)
        else {}
    )
    customer_context = (
        authoritative.get("orders_and_payment")
        if isinstance(authoritative.get("orders_and_payment"), dict)
        else {}
    )
    payment = (
        customer_context.get("resolved_payment")
        if isinstance(customer_context.get("resolved_payment"), dict)
        else resolved_payment_fact(orders=customer_context.get("orders"))
    )
    summary = {
        "source": str(customer_context.get("source") or "shared_background_context"),
        "order_count": len(customer_context.get("orders") or []) if isinstance(customer_context.get("orders"), list) else 0,
        "order_id": str(payment.get("order_id") or ""),
        "store_id": str(payment.get("store_id") or ""),
        "deposit_state": str(payment.get("deposit_state") or "unknown"),
        "prepay_required": payment.get("prepay_required"),
        "prepay_paid": payment.get("prepay_paid"),
    }
    return {
        "status": "paid" if is_paid_deposit_state(payment.get("deposit_state")) else "unpaid",
        "customer_context": customer_context,
        "payment": payment,
        "summary": summary,
    }


def _chat_conversation_evidence(history: Any, *, current_message: str) -> list[dict[str, str]]:
    items = history[-30:] if isinstance(history, list) else []
    output: list[dict[str, str]] = []
    for position, raw in enumerate(items, start=1):
        text = str(raw or "").strip()
        if not text:
            continue
        direction = "unknown"
        content = text
        for prefix, candidate_direction in (
            ("用户:", "customer"),
            ("客户:", "customer"),
            ("小贝:", "assistant"),
            ("员工:", "assistant"),
            ("AI:", "assistant"),
        ):
            if text.startswith(prefix):
                direction = candidate_direction
                content = text[len(prefix) :].strip()
                break
        output.append(
            {
                "message_ref": f"chat_{position}",
                "direction": direction,
                "content": content[:300],
            }
        )
    if current_message:
        output.append(
            {
                "message_ref": "current_message",
                "direction": "customer",
                "content": current_message[:300],
            }
        )
    return output


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
    for position, item in enumerate(messages[-30:], start=1):
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
                "message_ref": f"conv_{position}",
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
    return [str(item)[:240] for item in history[-30:] if str(item or "").strip()]


def _sop_summary(
    pack: dict[str, Any],
    *,
    customer_memory: dict[str, Any] | None = None,
    customer_context: dict[str, Any] | None = None,
    event_scope: bool = False,
    completed_sop_pack_ids: list[str] | None = None,
    completed_sop_categories: list[str] | None = None,
) -> dict[str, Any]:
    content_type = _string(pack.get("content_type")) or "sop"
    messages = _pack_messages(pack) if content_type != "evidence_strategy" else []
    return {
        "id": str(pack.get("id") or ""),
        "scope": _pack_scope(pack),
        "scopes": _pack_scopes(pack),
        "sop_category": _pack_category(pack),
        "name": str(pack.get("name") or ""),
        "purpose": str(pack.get("purpose") or "")[:240],
        "asset_role": _string(pack.get("asset_role")) or "supporting_content",
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
            require_activity_intro=event_scope,
            completed_sop_pack_ids=completed_sop_pack_ids or [],
            completed_sop_categories=completed_sop_categories or [],
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


def _parallel_sop_asset_summary(
    pack: dict[str, Any],
    *,
    completed_pack_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Expose content evidence without legacy stage/order routing metadata."""

    content_type = _string(pack.get("content_type")) or "sop"
    messages = _pack_messages(pack) if content_type != "evidence_strategy" else []
    return {
        "content_id": _string(pack.get("id")),
        "content_type": content_type,
        "name": _string(pack.get("name")),
        "purpose": _string(pack.get("purpose")),
        "asset_role": _string(pack.get("asset_role")) or "supporting_content",
        "selection_constraints": deepcopy(pack.get("selection_constraints") or {}),
        "requires_prior_asset_roles": [
            _string(item)
            for item in pack.get("requires_prior_asset_roles") or []
            if _string(item)
        ],
        "category": _pack_category(pack),
        "delivery_status": (
            "completed"
            if _string(pack.get("id")) in (completed_pack_ids or set())
            else "available"
        ),
        "approved_points": [
            _packed_text_content(item)
            for item in messages
            if isinstance(item, dict)
            and _string(item.get("type")) == "text"
            and _packed_text_content(item)
        ],
        "media": [
            deepcopy(item)
            for item in messages
            if isinstance(item, dict) and _string(item.get("type")) != "text"
        ],
        "customer_uncertainty": _string(pack.get("customer_uncertainty")),
        "useful_evidence": [_string(item) for item in pack.get("useful_evidence") or [] if _string(item)],
        "reasoning_moves": [_string(item) for item in pack.get("reasoning_moves") or [] if _string(item)],
        "anti_patterns": [_string(item) for item in pack.get("anti_patterns") or [] if _string(item)],
        "render_strategy": _string(pack.get("render_strategy")) or "adaptable",
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
    recent_conversation = _conversation_context(conversation_messages)
    due_count = sum(1 for pack in candidate_packs if _string(pack.get("_candidate_group")) != "next_step")
    next_step_count = sum(1 for pack in candidate_packs if _string(pack.get("_candidate_group")) == "next_step")
    return {
        "mode": "platform_actions" if event_type == "sop_platform_task" else "first_add_flow",
        "event": _event_summary(payload, customer),
        "current_platform_task": {
            "priority": "current_outreach_objective_after_hard_facts",
            "message_content": _platform_task_message_content(payload, customer),
        },
        "recent_conversation": recent_conversation,
        "conversation_activity": conversation_activity,
        "contact_availability_evidence": _contact_availability_evidence(
            recent_conversation,
            conversation_activity,
        ),
        "candidate_policy": {
            "due_candidates": due_count,
            "next_step_candidates": next_step_count,
            "selection_rule": (
                "candidate_sops 均为已到期且结构合法的候选，排列顺序只供审计。"
                "模型应结合完整历史选择一个未重复的新价值，不要求从最早阶段开始。"
                "stage_skip_evidence 只用于覆盖事实与付款前置审计；next_step 不能绕过风险或活动报价前置。"
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
            _sop_summary(
                pack,
                customer_memory=customer_memory,
                customer_context=customer_context,
                event_scope=True,
                completed_sop_pack_ids=completed_sop_pack_ids,
                completed_sop_categories=completed_sop_categories,
            )
            for pack in candidate_packs
        ],
        "merge_options": _merge_options(candidate_packs),
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


def _merge_options(candidate_packs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        (pack for pack in candidate_packs if isinstance(pack, dict)),
        key=mainline_pack_sort_key,
    )
    output: list[dict[str, Any]] = []
    for first_index in range(len(ordered)):
        for second_index in range(first_index + 1, len(ordered)):
            pair = [ordered[first_index], ordered[second_index]]
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
    """Expose only durable facts; current chat owns customer psychology and stage."""

    basic_info = memory.get("basic_info") if isinstance(memory.get("basic_info"), dict) else {}
    history_events = memory.get("history_events") if isinstance(memory.get("history_events"), list) else []
    hard_event_types = {
        "voice_transcript_received",
        "image_facts_received",
        "store_address_sent",
        "store_confirmed",
        "case_image_sent",
        "activity_intro_image_sent",
        "sop_pack_sent",
        "activity_quote_completed",
        "payment_collection_sent",
        "deposit_payment_confirmed",
        "order_created",
        "order_reused",
        "registration_updated",
        "appointment_confirmed",
        "customer_relation_changed",
        "complaint_or_refund_risk",
        "health_risk_state_changed",
        "human_takeover_changed",
    }
    hard_events: list[dict[str, Any]] = []
    for event in history_events[-100:]:
        if not isinstance(event, dict) or _string(event.get("event_type")) not in hard_event_types:
            continue
        hard_events.append(
            _drop_empty(
                {
                    "event_type": _string(event.get("event_type")),
                    "event_time": event.get("event_time") or event.get("time"),
                    "facts": compact(event.get("facts") or {}, max_chars=900),
                    "source": _string(event.get("source")),
                }
            )
        )
    allowed_basic_keys = {
        "city",
        "province",
        "district",
        "area_or_landmark",
        "confirmed_store_id",
        "confirmed_store_name",
        "deposit_state",
        "order_state",
        "registration_state",
        "appointment_state",
    }
    return {
        "customer_fact_snapshot": {
            "basic_facts": {
                key: value
                for key, value in basic_info.items()
                if key in allowed_basic_keys and value not in (None, "", [], {})
            },
            "recent_structured_events": hard_events[-30:],
            "priority": "current_conversation_and_realtime_order_facts_win",
        }
    }


def _contact_availability_evidence(
    recent_conversation: list[dict[str, Any]],
    conversation_activity: dict[str, Any],
) -> dict[str, Any]:
    latest_customer: dict[str, Any] = {}
    latest_assistant: dict[str, Any] = {}
    latest_assistant_index = -1
    for index, item in enumerate(recent_conversation):
        direction = _string(item.get("direction")).lower()
        if direction in {"customer", "user", "external"}:
            latest_customer = item
        elif direction in {"assistant", "staff", "ai", "agent", "employee"}:
            latest_assistant = item
            latest_assistant_index = index
    customer_after_assistant = 0
    if latest_assistant_index >= 0:
        customer_after_assistant = sum(
            1
            for item in recent_conversation[latest_assistant_index + 1 :]
            if _string(item.get("direction")).lower() in {"customer", "user", "external"}
        )
    assistant_elapsed_minutes: dict[str, int] = {}
    event_at = conversation_activity.get("event_at")
    for item in recent_conversation:
        direction = _string(item.get("direction")).lower()
        message_ref = _string(item.get("message_ref"))
        message_time = _parse_prompt_time(item.get("message_time"))
        if direction in {"assistant", "staff", "ai", "agent", "employee"} and message_ref and message_time:
            assistant_elapsed_minutes[message_ref] = _elapsed_minutes(message_time, event_at)
    return _drop_empty(
        {
            "latest_customer_message_ref": _string(latest_customer.get("message_ref")),
            "latest_assistant_message_ref": _string(latest_assistant.get("message_ref")),
            "assistant_waiting_customer": bool(conversation_activity.get("assistant_waiting_customer")),
            "minutes_since_latest_assistant": conversation_activity.get("silence_after_assistant_minutes"),
            "assistant_message_elapsed_minutes": assistant_elapsed_minutes,
            "customer_messages_after_latest_assistant": customer_after_assistant,
            "evidence_policy": "model_decides_semantics_code_validates_references_and_order",
        }
    )


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
    require_activity_intro: bool = False,
    completed_sop_pack_ids: list[str] | None = None,
    completed_sop_categories: list[str] | None = None,
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
    if require_activity_intro and "activity_and_price" not in _completed_mainline_stages(
        set(completed_sop_pack_ids or []),
        set(completed_sop_categories or []),
    ):
        return {
            "has_payment_collection": True,
            "status": "activity_intro_required",
            "amounts": [_payment_message_amount(card) for card in cards],
            "reason": "payment_collection_requires_completed_activity_intro",
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


def _candidate_packs(selector_output: dict[str, Any], packs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return only model-nominated configured packs, without choosing business priority in code."""

    available = {
        _string(pack.get("id")): pack
        for pack in packs
        if isinstance(pack, dict) and _string(pack.get("id"))
    }
    requested_assets = selector_output.get("candidate_assets")
    if isinstance(requested_assets, list):
        candidate_ids = [
            _string(item.get("content_id"))
            for item in requested_assets
            if isinstance(item, dict) and _string(item.get("content_id"))
        ]
    else:
        requested = selector_output.get("candidate_sop_ids")
        candidate_ids = [_string(item) for item in requested or [] if _string(item)]
    primary_id = _string(selector_output.get("sop_pack_id"))
    if primary_id:
        candidate_ids.insert(0, primary_id)
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    limit = 2 if isinstance(requested_assets, list) else 3
    for pack_id in candidate_ids[: max(3, limit + 1)]:
        if pack_id in seen or pack_id not in available:
            continue
        seen.add(pack_id)
        result.append(available[pack_id])
        if len(result) == limit:
            break
    return result


def _parallel_content_candidate(
    pack: dict[str, Any],
    annotation: dict[str, Any],
    *,
    completed_pack_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Expose a configured content asset without turning Gate into a reply author."""

    content_type = _string(pack.get("content_type")) or "sop"
    messages = _pack_messages(pack) if content_type != "evidence_strategy" else []
    approved_points = [
        _packed_text_content(item)
        for item in messages
        if isinstance(item, dict)
        and _string(item.get("type")) == "text"
        and _packed_text_content(item)
    ]
    media = [
        deepcopy(item)
        for item in messages
        if isinstance(item, dict) and _string(item.get("type")) != "text"
    ]
    render_strategy = _string(annotation.get("render_strategy"))
    if render_strategy not in {"adaptable", "verbatim_required"}:
        render_strategy = "adaptable"
    return {
        "content_id": _string(pack.get("id")),
        "content_type": content_type,
        "name": _string(pack.get("name")),
        "purpose": _string(pack.get("purpose")),
        "asset_role": _string(pack.get("asset_role")) or "supporting_content",
        "selection_constraints": deepcopy(pack.get("selection_constraints") or {}),
        "evidence_purpose": _string(annotation.get("evidence_purpose") or pack.get("purpose")),
        "relevance": _string(annotation.get("relevance")) or "supporting",
        "delivery_status": (
            "completed"
            if _string(pack.get("id")) in (completed_pack_ids or set())
            else "available"
        ),
        "render_strategy": render_strategy,
        "fact_refs": [f"content_asset:{_string(pack.get('id'))}"],
        "evidence_refs": [
            _string(item) for item in annotation.get("evidence_refs") or [] if _string(item)
        ],
        "requires_prior_asset_roles": [
            _string(item)
            for item in pack.get("requires_prior_asset_roles") or []
            if _string(item)
        ],
        "approved_points": approved_points,
        "media": media,
        "customer_uncertainty": _string(pack.get("customer_uncertainty")),
        "useful_evidence": [_string(item) for item in pack.get("useful_evidence") or [] if _string(item)],
        "reasoning_moves": [_string(item) for item in pack.get("reasoning_moves") or [] if _string(item)],
        "anti_patterns": [_string(item) for item in pack.get("anti_patterns") or [] if _string(item)],
        # Kept for deterministic structured-delivery validation and SOP completion.
        # Reply may rewrite text but cannot invent or mutate structured media.
        "messages": messages,
        "constraints": {
            "facts_and_media_must_remain_authoritative": True,
            "customer_visible_text_may_be_adapted": render_strategy == "adaptable",
        },
    }


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
    selected_scene_id = _string(
        selector_output.get("selected_scene_id") or selector_output.get("priority_question_id")
    )
    active_task = _chat_gate_active_task(selector_output.get("active_task"))
    packs = {
        _string(item.get("id")): item
        for item in selector_input.get("unfinished_sops") or []
        if isinstance(item, dict) and _string(item.get("id"))
    }
    scene_ids = {
        _string(item.get("scene_id"))
        for item in selector_input.get("precision_qa_index") or []
        if isinstance(item, dict) and _string(item.get("scene_id"))
    }
    violations: list[str] = []
    candidate_ids = [_string(item) for item in selector_output.get("candidate_sop_ids") or [] if _string(item)]
    if len(candidate_ids) > 3:
        violations.append("candidate_sop_ids_exceed_limit")
    if len(candidate_ids) != len(set(candidate_ids)):
        violations.append("candidate_sop_ids_must_be_unique")
    if any(item not in packs for item in candidate_ids):
        violations.append("candidate_sop_id_not_unfinished")
    expected_coverage = {
        "sop_only": "exact",
        "ai_then_sop": "partial",
        "ai_only": "none",
    }[route]
    if coverage != expected_coverage:
        violations.append(f"route_coverage_mismatch:{route}:{coverage}")
    if selected_scene_id and selected_scene_id not in scene_ids:
        if _string(selector_input.get("reply_chain_mode")) == "parallel_candidate_only":
            violations.append("unknown_selected_scene_id")
        elif not precision_qa_for_id(selected_scene_id).get("hard_rule"):
            violations.append("unknown_selected_scene_id")
    if active_task.get("type") == "location_confirmation":
        if active_task.get("required_tool") != "customer_store_lookup" or not active_task.get("query"):
            violations.append("location_confirmation_requires_store_lookup_task")
    if route in {"sop_only", "ai_then_sop"}:
        if not pack_id or pack_id not in packs:
            violations.append("selected_pack_missing_or_not_unfinished")
        elif resume_stage != _string(packs[pack_id].get("mainline_stage")):
            violations.append("resume_stage_must_match_selected_pack")
        if pack_id in packs and _string(selector_input.get("reply_chain_mode")) != "parallel_candidate_only":
            violations.extend(_chat_gate_party_size_violations(selector_output, selector_input, packs[pack_id]))
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


def _parallel_content_gate_output_violations(
    selector_output: dict[str, Any],
    selector_input: dict[str, Any],
) -> list[str]:
    assets = selector_output.get("candidate_assets")
    if not isinstance(assets, list):
        return ["candidate_assets_must_be_list"]
    if len(assets) > 3:
        return ["candidate_assets_exceed_limit"]
    available_assets = {
        _string(item.get("content_id") or item.get("id")): item
        for item in selector_input.get("content_assets") or []
        if isinstance(item, dict) and _string(item.get("content_id") or item.get("id"))
    }
    available_ids = set(available_assets)
    completed_asset_roles = {
        _string(item.get("asset_role"))
        for item in available_assets.values()
        if _string(item.get("delivery_status")) == "completed" and _string(item.get("asset_role"))
    }
    valid_refs = {"current_message"}
    valid_refs.update(
        _string(item.get("message_ref"))
        for item in selector_input.get("conversation_evidence") or []
        if isinstance(item, dict) and _string(item.get("message_ref"))
    )
    violations: list[str] = []
    prohibited_fields = {
        "reply_messages",
        "payment_decision",
        "action",
        "sales_judgment",
        "selected_scene_id",
        "sop_pack_id",
        "tool_calls",
        "commit_actions",
        "route",
        "coverage",
        "resume_stage",
        "active_task",
    }
    for field in sorted(prohibited_fields):
        if selector_output.get(field) not in (None, "", [], {}):
            violations.append(f"parallel_gate_forbidden_field:{field}")
    seen: set[str] = set()
    direct_count = 0
    for index, item in enumerate(assets):
        if not isinstance(item, dict):
            violations.append(f"candidate_asset_not_object:{index}")
            continue
        content_id = _string(item.get("content_id"))
        if not content_id or content_id not in available_ids:
            violations.append(f"candidate_asset_not_available:{content_id or index}")
        if content_id in seen:
            violations.append(f"candidate_asset_duplicate:{content_id}")
        seen.add(content_id)
        if _string(item.get("relevance")) not in {"direct", "supporting"}:
            violations.append(f"candidate_asset_invalid_relevance:{content_id or index}")
        elif _string(item.get("relevance")) == "direct":
            direct_count += 1
        if not _string(item.get("evidence_purpose")):
            violations.append(f"candidate_asset_missing_evidence_purpose:{content_id or index}")
        if _string(item.get("render_strategy")) not in {"adaptable", "verbatim_required"}:
            violations.append(f"candidate_asset_invalid_render_strategy:{content_id or index}")
        refs = [_string(ref) for ref in item.get("evidence_refs") or [] if _string(ref)]
        if not refs or any(ref not in valid_refs for ref in refs):
            violations.append(f"candidate_asset_invalid_evidence_refs:{content_id or index}")
        if "prerequisite_evidence_refs" in item:
            violations.append(f"candidate_asset_forbidden_prerequisite_refs:{content_id or index}")
        required_roles = {
            _string(role)
            for role in (available_assets.get(content_id) or {}).get("requires_prior_asset_roles") or []
            if _string(role)
        }
        missing_roles = required_roles - completed_asset_roles
        if missing_roles:
            violations.append(
                f"candidate_asset_missing_required_role:{content_id or index}:"
                f"{','.join(sorted(missing_roles))}"
            )
        constraints = (
            (available_assets.get(content_id) or {}).get("selection_constraints")
            if isinstance((available_assets.get(content_id) or {}).get("selection_constraints"), dict)
            else {}
        )
        authoritative = (
            selector_input.get("authoritative_context")
            if isinstance(selector_input.get("authoritative_context"), dict)
            else {}
        )
        forbidden_facts = {
            _string(name)
            for name in constraints.get("forbidden_when_authoritative_facts_present") or []
            if _string(name)
        }
        present_forbidden = sorted(
            name
            for name in forbidden_facts
            if authoritative.get(name) not in (None, "", [], {})
        )
        if present_forbidden:
            violations.append(
                f"candidate_asset_conflicts_with_authoritative_fact:{content_id or index}:"
                f"{','.join(present_forbidden)}"
            )
    if direct_count > 1:
        violations.append("candidate_assets_multiple_direct")
    return violations


def _chat_gate_active_task(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    task_type = _string(value.get("type"))
    if task_type not in {"location_confirmation", "store_lookup", "precision_answer", "sop_delivery", "payment", "other"}:
        task_type = ""
    status = _string(value.get("status"))
    if status not in {"pending", "resolved"}:
        status = ""
    return {
        key: item
        for key, item in {
            "type": task_type,
            "status": status,
            "query": _string(value.get("query"))[:240],
            "required_tool": _string(value.get("required_tool"))[:80],
            "customer_evidence_ref": _string(value.get("customer_evidence_ref"))[:120],
            "assistant_evidence_ref": _string(value.get("assistant_evidence_ref"))[:120],
        }.items()
        if item
    }


def _chat_gate_party_size_violations(
    selector_output: dict[str, Any],
    selector_input: dict[str, Any],
    selected_pack: dict[str, Any],
) -> list[str]:
    gate = selected_pack.get("payment_collection_gate") if isinstance(selected_pack.get("payment_collection_gate"), dict) else {}
    amounts = [int(value) for value in gate.get("amounts") or [] if str(value or "").isdigit()]
    amount = max(amounts, default=0)
    if amount <= 10:
        return []
    evidence = selector_output.get("party_size_evidence") if isinstance(selector_output.get("party_size_evidence"), dict) else {}
    try:
        party_size = int(evidence.get("party_size") or 0)
    except (TypeError, ValueError):
        party_size = 0
    evidence_ref = _string(evidence.get("customer_evidence_ref"))
    evidence_quote = _string(evidence.get("evidence_quote"))
    referenced = next(
        (
            item
            for item in selector_input.get("conversation_evidence") or []
            if isinstance(item, dict) and _string(item.get("message_ref")) == evidence_ref
        ),
        None,
    )
    if party_size * 10 != amount:
        return ["multi_person_payment_amount_requires_matching_party_size_evidence"]
    if not referenced or _string(referenced.get("direction")) != "customer":
        return ["multi_person_payment_requires_customer_evidence_ref"]
    if not evidence_quote or evidence_quote not in _string(referenced.get("content")):
        return ["multi_person_payment_evidence_quote_must_match_customer_message"]
    return []


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


def _packed_text_content(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, dict):
        return _string(content.get("text") or content.get("content"))
    return _string(content)


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


def _reply_chain_sop_progress_from_shared_state(
    shared_state: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(shared_state, dict):
        return {}
    shared_context = (
        shared_state.get("shared_context")
        if isinstance(shared_state.get("shared_context"), dict)
        else {}
    )
    authoritative_facts = (
        shared_context.get("authoritative_facts")
        if isinstance(shared_context.get("authoritative_facts"), dict)
        else {}
    )
    progress = (
        authoritative_facts.get("sop_progress")
        if isinstance(authoritative_facts.get("sop_progress"), dict)
        else {}
    )
    if progress.get("status") != "available":
        return {}
    return deepcopy(progress)


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
