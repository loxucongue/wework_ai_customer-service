from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.chat_runtime import ChatRuntime
from app.config import Settings
from app.graph.graph_builder import build_reply_graphs
from app.services.ai_sales_policy_service import AiSalesPolicyService
from app.services.coze_client import CozeClient
from app.services.customer_context import CustomerContextService
from app.services.customer_store_knowledge import CustomerStoreKnowledgeService
from app.services.deepseek_semantic_client import DeepSeekSemanticClient
from app.services.follow_knowledge_client import FollowKnowledgeClient
from app.services.memory_store import CustomerMemoryStore
from app.services.message_delivery import MessageDeliveryService
from app.services.model_client import ModelClient
from app.services.model_led_objection_playbook_service import ModelLedObjectionPlaybookService
from app.services.outreach_send_client import OutreachSendClient
from app.services.outreach_service import OutreachService
from app.services.outreach_system_client import OutreachSystemClient
from app.services.platform_agent_client import PlatformAgentClient
from app.services.platform_reply_coordinator import PlatformReplyCoordinator
from app.services.platform_voice_batch import PlatformVoiceBatchCoordinator
from app.services.precision_qa_playbook_service import PrecisionQaPlaybookService
from app.services.sales_strategy_service import SalesStrategyService
from app.services.service_rule_data_client import ServiceRuleDataClient
from app.services.service_rule_data_service import ServiceRuleDataService
from app.services.sop_event_service import SopEventService
from app.services.sop_execution_service import SopExecutionService
from app.services.sop_objection_material_service import SopObjectionMaterialService
from app.services.sop_platform_client import SopPlatformClient
from app.services.sop_platform_task_service import SopPlatformTaskService
from app.services.sop_reply_pack_service import SopReplyPackService
from app.services.storage import AppRepository, build_store
from app.services.store_service import StoreService
from app.services.store_snapshot_service import StoreSnapshotService
from app.services.trace_logger import TraceLogger
from app.services.v3_evaluation_service import V3EvaluationService
from app.services.v3_semantic_router_service import V3SemanticRouterService
from app.services.v3_sop_execution_service import SopExecutionService as V3SopExecutionService
from app.services.voice_transcription import DoubaoAsrClient
from app.runtime_roles import RuntimeRole


@dataclass(frozen=True)
class RuntimeServices:
    storage_store: Any
    repository: AppRepository
    v3_evaluation_service: V3EvaluationService
    trace_logger: TraceLogger
    message_delivery_service: MessageDeliveryService
    conversation_mode_relay_service: Any
    service_rule_data_service: ServiceRuleDataService
    service_rule_data_client: ServiceRuleDataClient
    coze_client: CozeClient
    voice_transcription_client: DoubaoAsrClient
    model_client: ModelClient
    ai_sales_policy_service: AiSalesPolicyService
    sales_strategy_service: SalesStrategyService
    memory_store: CustomerMemoryStore
    platform_agent_client: PlatformAgentClient
    outreach_send_client: OutreachSendClient
    outreach_system_client: OutreachSystemClient
    sop_platform_client: SopPlatformClient
    platform_reply_coordinator: PlatformReplyCoordinator
    platform_voice_batch_coordinator: PlatformVoiceBatchCoordinator
    customer_context_service: CustomerContextService
    store_snapshot_service: StoreSnapshotService
    customer_store_knowledge_service: CustomerStoreKnowledgeService
    store_service: StoreService
    sop_reply_pack_service: SopReplyPackService
    precision_qa_playbook_service: PrecisionQaPlaybookService
    sop_objection_material_service: SopObjectionMaterialService
    model_led_objection_playbook_service: ModelLedObjectionPlaybookService
    follow_knowledge_client: FollowKnowledgeClient
    deepseek_semantic_client: DeepSeekSemanticClient
    deepseek_semantic_fallback_client: ModelClient
    v3_semantic_router_service: V3SemanticRouterService
    outreach_service: OutreachService
    sop_execution_service: SopExecutionService
    v3_sop_execution_service: V3SopExecutionService
    sop_event_service: SopEventService
    sop_platform_task_service: SopPlatformTaskService
    reply_graphs: Any
    chat_runtime: ChatRuntime

    async def aclose(self) -> None:
        seen: set[int] = set()
        for client in (
            self.platform_voice_batch_coordinator,
            self.model_client,
            self.coze_client,
            self.follow_knowledge_client,
            self.deepseek_semantic_client,
            self.deepseek_semantic_fallback_client,
            self.voice_transcription_client,
            self.outreach_send_client,
            self.outreach_system_client,
            self.conversation_mode_relay_service,
            self.sop_platform_client,
            self.service_rule_data_client,
        ):
            if client is None or id(client) in seen:
                continue
            seen.add(id(client))
            close = getattr(client, "aclose", None)
            if close is not None:
                await close()
        if self.platform_agent_client is not None:
            self.platform_agent_client.close()
        self.storage_store.close()


def build_runtime_services(settings: Settings) -> RuntimeServices:
    # Imports kept local to make the runtime dependency graph explicit without
    # forcing route modules to know how infrastructure clients are assembled.
    from app.services.conversation_mode_relay import ConversationModeRelayService

    role = settings.runtime_role
    storage_store = build_store(settings)
    repository = AppRepository(storage_store)
    v3_evaluation_service = V3EvaluationService(settings.v3_evaluation_dir)
    trace_logger = TraceLogger(settings)
    message_delivery_service = MessageDeliveryService(settings, repository)
    conversation_mode_relay_service = (
        ConversationModeRelayService(settings) if role is RuntimeRole.CONTROL else None
    )
    service_rule_data_client = ServiceRuleDataClient(settings)
    service_rule_data_service = ServiceRuleDataService(
        repository=repository,
        client=service_rule_data_client,
        poll_seconds=settings.service_rule_data_poll_seconds,
        batch_size=settings.service_rule_data_batch_size,
        max_attempts=settings.service_rule_data_max_attempts,
        retry_base_seconds=settings.service_rule_data_retry_base_seconds,
    )
    coze_client = CozeClient(settings)
    voice_transcription_client = DoubaoAsrClient(settings)
    model_client = ModelClient(settings)
    ai_sales_policy_service = AiSalesPolicyService(settings)
    sales_strategy_service = SalesStrategyService(settings)
    memory_store = CustomerMemoryStore(settings, repository)
    platform_agent_client = PlatformAgentClient(settings)
    outreach_send_client = OutreachSendClient(settings, delivery_service=message_delivery_service)
    outreach_system_client = OutreachSystemClient(settings, delivery_service=message_delivery_service)
    sop_platform_client = SopPlatformClient(settings) if role is not RuntimeRole.REPLY else None
    platform_reply_coordinator = PlatformReplyCoordinator(settings)
    platform_voice_batch_coordinator = PlatformVoiceBatchCoordinator(settings)
    customer_context_service = CustomerContextService(platform_agent_client)
    store_snapshot_service = StoreSnapshotService(settings, platform_agent_client)
    customer_store_knowledge_service = CustomerStoreKnowledgeService(platform_agent_client, store_snapshot_service)
    store_service = StoreService(platform_agent_client)
    sop_reply_pack_service = SopReplyPackService(settings)
    precision_qa_playbook_service = PrecisionQaPlaybookService(settings)
    sop_objection_material_service = (
        SopObjectionMaterialService(settings.sop_objection_materials_path)
        if role is not RuntimeRole.REPLY
        else None
    )
    model_led_objection_playbook_service = ModelLedObjectionPlaybookService(
        settings.model_led_objection_playbook_path
    )
    follow_knowledge_client = None
    deepseek_semantic_fallback_client = None
    deepseek_semantic_client = None
    v3_semantic_router_service = None
    if role is RuntimeRole.REPLY:
        follow_knowledge_client = FollowKnowledgeClient(settings)
        deepseek_semantic_fallback_client = ModelClient(
            settings.model_copy(
                update={
                    "model_fast": "gpt-5.4-mini",
                    "model_fast_fallbacks": "gpt-5.4",
                    "model_emergency_fallbacks": "",
                    "model_hedge_max_parallel": 1,
                }
            )
        )
        deepseek_semantic_client = DeepSeekSemanticClient(settings, deepseek_semantic_fallback_client)
        v3_semantic_router_service = V3SemanticRouterService(
            semantic_client=deepseek_semantic_client,
            knowledge_client=follow_knowledge_client,
            script_threshold=settings.deepseek_semantic_script_threshold,
            max_scripts=settings.deepseek_semantic_max_scripts,
        )
    outreach_service = OutreachService(
        repository=repository,
        model_client=model_client,
        system_client=outreach_system_client,
        customer_context_service=customer_context_service,
        precision_qa_playbook_service=precision_qa_playbook_service,
        sop_reply_pack_service=sop_reply_pack_service,
        coze_client=coze_client,
        before_send_retry_seconds=settings.outreach_before_send_retry_seconds,
        sales_strategy_service=sales_strategy_service,
    )
    sop_execution_service = None
    if role is not RuntimeRole.REPLY:
        sop_execution_service = SopExecutionService(
            repository=repository,
            sop_reply_pack_service=sop_reply_pack_service,
            model_client=model_client,
            memory_store=memory_store,
            customer_context_service=customer_context_service,
            event_model_retry_attempts=settings.sop_event_model_retry_attempts,
            event_model_retry_delay_seconds=settings.sop_event_model_retry_delay_seconds,
            event_model_attempt_timeout_seconds=settings.sop_event_model_attempt_timeout_seconds,
            event_model_total_timeout_seconds=settings.sop_event_model_total_timeout_seconds,
            chat_gate_total_timeout_seconds=settings.sop_chat_gate_total_timeout_seconds,
            event_model_max_concurrency=settings.sop_event_model_max_concurrency,
            model_semantic_routing_enabled=settings.reply_model_semantic_routing_enabled,
            event_schema_only_normalizer_enabled=settings.sop_event_schema_only_normalizer_enabled,
            governance_shadow_mode=settings.reply_governance_shadow_mode,
        )
    v3_sop_execution_service = None
    if role is RuntimeRole.REPLY:
        v3_sop_execution_service = V3SopExecutionService(
            repository=repository,
            sop_reply_pack_service=sop_reply_pack_service,
            model_client=model_client,
            memory_store=memory_store,
            customer_context_service=customer_context_service,
            event_model_retry_attempts=settings.sop_event_model_retry_attempts,
            event_model_retry_delay_seconds=settings.sop_event_model_retry_delay_seconds,
            event_model_attempt_timeout_seconds=settings.sop_event_model_attempt_timeout_seconds,
            event_model_total_timeout_seconds=settings.sop_event_model_total_timeout_seconds,
            chat_gate_total_timeout_seconds=settings.sop_chat_gate_total_timeout_seconds,
            event_model_max_concurrency=settings.sop_event_model_max_concurrency,
            model_led_objection_playbook_service=model_led_objection_playbook_service,
        )
    sop_event_service = None
    sop_platform_task_service = None
    if role is not RuntimeRole.REPLY:
        sop_event_service = SopEventService(
            repository=repository,
            sop_reply_pack_service=sop_reply_pack_service,
            outreach_send_client=outreach_send_client,
            sop_execution_service=sop_execution_service,
            memory_store=memory_store,
            customer_context_service=customer_context_service,
            personalized_outreach_service=outreach_service,
            daily_touch_soft_limit=settings.sop_event_daily_touch_soft_limit,
            default_identity={
                "corp_id": settings.platform_agent_default_corp_id,
                "user_id": settings.platform_agent_default_user_id,
                "wechat": settings.platform_agent_default_wechat,
            },
            persistent_retry_attempts=settings.sop_event_persistent_retry_attempts,
            persistent_retry_base_delay_seconds=settings.sop_event_persistent_retry_base_delay_seconds,
            persistent_retry_max_delay_seconds=settings.sop_event_persistent_retry_max_delay_seconds,
            retry_batch_size=settings.sop_event_retry_batch_size,
            quiet_backlog_fusion_enabled=settings.sop_quiet_backlog_fusion_enabled,
            quiet_backlog_fusion_time=settings.sop_quiet_backlog_fusion_time,
            quiet_backlog_fusion_batch_size=settings.sop_quiet_backlog_fusion_batch_size,
            quiet_backlog_fusion_model=settings.sop_quiet_backlog_fusion_model,
            quiet_backlog_fusion_timeout_seconds=settings.sop_quiet_backlog_fusion_timeout_seconds,
        )
        sop_platform_task_service = SopPlatformTaskService(
            settings=settings,
            repository=repository,
            platform_client=sop_platform_client,
            system_client=outreach_system_client,
            model_client=model_client,
            customer_context_service=customer_context_service,
            objection_material_service=sop_objection_material_service,
        )
    reply_graphs = None
    chat_runtime = None
    if role is RuntimeRole.REPLY:
        reply_graphs = build_reply_graphs(
            coze_client,
            trace_logger,
            model_client,
            memory_store,
            customer_context_service,
            customer_store_knowledge_service,
            store_service,
            outreach_send_client,
            platform_agent_client,
            v3_sop_execution_service,
            v3_semantic_router_service,
            sales_strategy_service,
        )
        chat_runtime = ChatRuntime(
            full_graph=reply_graphs.full_graph,
            commit_graph=reply_graphs.commit_graph,
            trace_logger=trace_logger,
            repository=repository,
            outreach_send_client=outreach_send_client,
            outreach_system_client=outreach_system_client,
            memory_store=memory_store,
            platform_reply_coordinator=platform_reply_coordinator,
            sop_execution_service=v3_sop_execution_service,
            service_rule_data_service=service_rule_data_service,
            ai_sales_policy_service=ai_sales_policy_service,
            sales_strategy_service=sales_strategy_service,
            outreach_service=outreach_service,
            settings=settings,
        )
    return RuntimeServices(
        **{
            name: value
            for name, value in locals().items()
            if name in RuntimeServices.__dataclass_fields__
        }
    )
