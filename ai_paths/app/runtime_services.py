from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.chat_runtime import ChatRuntime
from app.config import Settings
from app.graph.graph_builder import build_reply_graphs
from app.services.ai_sales_policy_service import AiSalesPolicyService
from app.services.async_reply_delivery import AsyncReplyDeliveryFinalizer
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
from app.services.sop.delivery_compatibility import SopDeliveryCompatibilityService
from app.services.sop_objection_material_service import SopObjectionMaterialService
from app.services.sop_platform_client import SopPlatformClient
from app.services.sop_platform_task_service import SopPlatformTaskService
from app.services.sop_reply_pack_service import SopReplyPackService
from app.services.storage import AppRepository, build_store
from app.services.store_service import StoreService
from app.services.store_snapshot_service import StoreSnapshotService
from app.services.trace_logger import TraceLogger
from app.services.v3_semantic_router_service import V3SemanticRouterService
from app.services.v3_strategy_outcome_service import PlatformOrderOutcomeProvider
from app.services.v3_sop_execution_service import SopExecutionService as V3SopExecutionService
from app.services.voice_transcription import DoubaoAsrClient


@dataclass(frozen=True)
class ReplyServices:
    storage_store: Any
    repository: AppRepository
    chat_runtime: ChatRuntime
    platform_voice_batch_coordinator: PlatformVoiceBatchCoordinator
    voice_transcription_client: DoubaoAsrClient
    service_rule_data_service: ServiceRuleDataService
    _closers: tuple[Any, ...]
    _platform_agent_client: PlatformAgentClient

    async def aclose(self) -> None:
        await _close_runtime(self.storage_store, self._platform_agent_client, self._closers)


@dataclass(frozen=True)
class ControlServices:
    storage_store: Any
    repository: AppRepository
    async_reply_delivery_finalizer: AsyncReplyDeliveryFinalizer
    message_delivery_service: MessageDeliveryService
    outreach_service: OutreachService
    sop_delivery_compatibility_service: SopDeliveryCompatibilityService
    sop_platform_task_service: SopPlatformTaskService
    memory_store: CustomerMemoryStore
    ai_sales_policy_service: AiSalesPolicyService
    precision_qa_playbook_service: PrecisionQaPlaybookService
    sales_strategy_service: SalesStrategyService
    store_snapshot_service: StoreSnapshotService
    trace_logger: TraceLogger
    sop_reply_pack_service: SopReplyPackService
    sop_objection_material_service: SopObjectionMaterialService
    strategy_outcome_provider: PlatformOrderOutcomeProvider
    _closers: tuple[Any, ...]
    _platform_agent_client: PlatformAgentClient

    async def aclose(self) -> None:
        await _close_runtime(self.storage_store, self._platform_agent_client, self._closers)


@dataclass(frozen=True)
class WorkerServices:
    storage_store: Any
    repository: AppRepository
    outreach_service: OutreachService
    service_rule_data_service: ServiceRuleDataService
    sop_platform_task_service: SopPlatformTaskService
    store_snapshot_service: StoreSnapshotService
    strategy_outcome_provider: PlatformOrderOutcomeProvider
    _closers: tuple[Any, ...]
    _platform_agent_client: PlatformAgentClient

    async def aclose(self) -> None:
        await _close_runtime(self.storage_store, self._platform_agent_client, self._closers)


def build_reply_services(settings: Settings) -> ReplyServices:
    storage_store, repository = _build_repository(settings)
    trace_logger = TraceLogger(settings)
    message_delivery_service = MessageDeliveryService(settings, repository)
    coze_client = CozeClient(settings)
    model_client = ModelClient(settings)
    memory_store = CustomerMemoryStore(settings, repository)
    platform_agent_client = PlatformAgentClient(settings)
    outreach_send_client = OutreachSendClient(settings, delivery_service=message_delivery_service)
    outreach_system_client = OutreachSystemClient(settings, delivery_service=message_delivery_service)
    voice_transcription_client = DoubaoAsrClient(settings)
    platform_voice_batch_coordinator = PlatformVoiceBatchCoordinator(settings)
    platform_reply_coordinator = PlatformReplyCoordinator(settings)
    ai_sales_policy_service = AiSalesPolicyService(settings)
    sales_strategy_service = SalesStrategyService(settings)
    customer_context_service = CustomerContextService(platform_agent_client)
    store_snapshot_service = StoreSnapshotService(settings, platform_agent_client)
    customer_store_knowledge_service = CustomerStoreKnowledgeService(
        platform_agent_client, store_snapshot_service
    )
    store_service = StoreService(platform_agent_client)
    sop_reply_pack_service = SopReplyPackService(settings)
    precision_qa_playbook_service = PrecisionQaPlaybookService(settings)
    model_led_objection_playbook_service = ModelLedObjectionPlaybookService(
        settings.model_led_objection_playbook_path
    )
    follow_knowledge_client = FollowKnowledgeClient(settings)
    semantic_fallback_client = ModelClient(
        settings.model_copy(
            update={
                "model_fast": "gpt-5.4-mini",
                "model_fast_fallbacks": "gpt-5.4",
                "model_emergency_fallbacks": "",
                "model_hedge_max_parallel": 1,
            }
        )
    )
    deepseek_semantic_client = DeepSeekSemanticClient(settings, semantic_fallback_client)
    v3_semantic_router_service = V3SemanticRouterService(
        semantic_client=deepseek_semantic_client,
        knowledge_client=follow_knowledge_client,
        script_threshold=settings.deepseek_semantic_script_threshold,
        max_scripts=settings.deepseek_semantic_max_scripts,
    )
    outreach_service = _build_outreach_service(
        settings=settings,
        repository=repository,
        model_client=model_client,
        system_client=outreach_system_client,
        customer_context_service=customer_context_service,
        precision_qa_playbook_service=precision_qa_playbook_service,
        sop_reply_pack_service=sop_reply_pack_service,
        coze_client=coze_client,
        sales_strategy_service=sales_strategy_service,
    )
    v3_sop_execution_service = V3SopExecutionService(
        repository=repository,
        sop_reply_pack_service=sop_reply_pack_service,
        model_client=model_client,
        memory_store=memory_store,
        customer_context_service=customer_context_service,
        chat_gate_total_timeout_seconds=settings.sop_chat_gate_total_timeout_seconds,
        model_led_objection_playbook_service=model_led_objection_playbook_service,
    )
    service_rule_data_service = _build_service_rule_data_writer(settings, repository)
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
    return ReplyServices(
        storage_store=storage_store,
        repository=repository,
        chat_runtime=chat_runtime,
        platform_voice_batch_coordinator=platform_voice_batch_coordinator,
        voice_transcription_client=voice_transcription_client,
        service_rule_data_service=service_rule_data_service,
        _closers=(
            model_client,
            coze_client,
            follow_knowledge_client,
            deepseek_semantic_client,
            semantic_fallback_client,
            voice_transcription_client,
            platform_voice_batch_coordinator,
            outreach_send_client,
            outreach_system_client,
        ),
        _platform_agent_client=platform_agent_client,
    )


def build_control_services(settings: Settings) -> ControlServices:
    storage_store, repository = _build_repository(settings)
    trace_logger = TraceLogger(settings)
    message_delivery_service = MessageDeliveryService(settings, repository)
    memory_store = CustomerMemoryStore(settings, repository)
    async_reply_delivery_finalizer = AsyncReplyDeliveryFinalizer(repository, memory_store)
    model_client = ModelClient(settings)
    coze_client = CozeClient(settings)
    platform_agent_client = PlatformAgentClient(settings)
    outreach_system_client = OutreachSystemClient(settings, delivery_service=message_delivery_service)
    ai_sales_policy_service = AiSalesPolicyService(settings)
    sales_strategy_service = SalesStrategyService(settings)
    customer_context_service = CustomerContextService(platform_agent_client)
    store_snapshot_service = StoreSnapshotService(settings, platform_agent_client)
    sop_reply_pack_service = SopReplyPackService(settings)
    precision_qa_playbook_service = PrecisionQaPlaybookService(settings)
    sop_objection_material_service = SopObjectionMaterialService(settings.sop_objection_materials_path)
    outreach_service = _build_outreach_service(
        settings=settings,
        repository=repository,
        model_client=model_client,
        system_client=outreach_system_client,
        customer_context_service=customer_context_service,
        precision_qa_playbook_service=precision_qa_playbook_service,
        sop_reply_pack_service=sop_reply_pack_service,
        coze_client=coze_client,
        sales_strategy_service=sales_strategy_service,
    )
    sop_platform_client = SopPlatformClient(settings)
    sop_platform_task_service = SopPlatformTaskService(
        settings=settings,
        repository=repository,
        platform_client=sop_platform_client,
        system_client=outreach_system_client,
        model_client=model_client,
        customer_context_service=customer_context_service,
        objection_material_service=sop_objection_material_service,
    )
    return ControlServices(
        storage_store=storage_store,
        repository=repository,
        async_reply_delivery_finalizer=async_reply_delivery_finalizer,
        message_delivery_service=message_delivery_service,
        outreach_service=outreach_service,
        sop_delivery_compatibility_service=SopDeliveryCompatibilityService(
            repository=repository, memory_store=memory_store
        ),
        sop_platform_task_service=sop_platform_task_service,
        memory_store=memory_store,
        ai_sales_policy_service=ai_sales_policy_service,
        precision_qa_playbook_service=precision_qa_playbook_service,
        sales_strategy_service=sales_strategy_service,
        store_snapshot_service=store_snapshot_service,
        trace_logger=trace_logger,
        sop_reply_pack_service=sop_reply_pack_service,
        sop_objection_material_service=sop_objection_material_service,
        strategy_outcome_provider=PlatformOrderOutcomeProvider(
            platform_agent_client,
            enabled=settings.v3_strategy_analytics_platform_order_enabled,
            request_timeout_seconds=settings.v3_strategy_analytics_outcome_timeout_seconds,
            max_retries=settings.v3_strategy_analytics_outcome_max_retries,
            retry_base_seconds=settings.v3_strategy_analytics_outcome_retry_base_seconds,
        ),
        _closers=(model_client, coze_client, outreach_system_client, sop_platform_client),
        _platform_agent_client=platform_agent_client,
    )


def build_worker_services(settings: Settings) -> WorkerServices:
    storage_store, repository = _build_repository(settings)
    message_delivery_service = MessageDeliveryService(settings, repository)
    model_client = ModelClient(settings)
    coze_client = CozeClient(settings)
    platform_agent_client = PlatformAgentClient(settings)
    outreach_system_client = OutreachSystemClient(settings, delivery_service=message_delivery_service)
    sales_strategy_service = SalesStrategyService(settings)
    customer_context_service = CustomerContextService(platform_agent_client)
    store_snapshot_service = StoreSnapshotService(settings, platform_agent_client)
    sop_reply_pack_service = SopReplyPackService(settings)
    precision_qa_playbook_service = PrecisionQaPlaybookService(settings)
    sop_objection_material_service = SopObjectionMaterialService(settings.sop_objection_materials_path)
    outreach_service = _build_outreach_service(
        settings=settings,
        repository=repository,
        model_client=model_client,
        system_client=outreach_system_client,
        customer_context_service=customer_context_service,
        precision_qa_playbook_service=precision_qa_playbook_service,
        sop_reply_pack_service=sop_reply_pack_service,
        coze_client=coze_client,
        sales_strategy_service=sales_strategy_service,
    )
    sop_platform_client = SopPlatformClient(settings)
    service_rule_data_service = _build_service_rule_data_worker(settings, repository)
    return WorkerServices(
        storage_store=storage_store,
        repository=repository,
        outreach_service=outreach_service,
        service_rule_data_service=service_rule_data_service,
        sop_platform_task_service=SopPlatformTaskService(
            settings=settings,
            repository=repository,
            platform_client=sop_platform_client,
            system_client=outreach_system_client,
            model_client=model_client,
            customer_context_service=customer_context_service,
            objection_material_service=sop_objection_material_service,
        ),
        store_snapshot_service=store_snapshot_service,
        strategy_outcome_provider=PlatformOrderOutcomeProvider(
            platform_agent_client,
            enabled=settings.v3_strategy_analytics_platform_order_enabled,
            request_timeout_seconds=settings.v3_strategy_analytics_outcome_timeout_seconds,
            max_retries=settings.v3_strategy_analytics_outcome_max_retries,
            retry_base_seconds=settings.v3_strategy_analytics_outcome_retry_base_seconds,
        ),
        _closers=(
            model_client,
            coze_client,
            outreach_system_client,
            sop_platform_client,
            service_rule_data_service.client,
        ),
        _platform_agent_client=platform_agent_client,
    )


def _build_repository(settings: Settings) -> tuple[Any, AppRepository]:
    storage_store = build_store(settings)
    return storage_store, AppRepository(storage_store)


def _build_outreach_service(
    *,
    settings: Settings,
    repository: AppRepository,
    model_client: ModelClient,
    system_client: OutreachSystemClient,
    customer_context_service: CustomerContextService,
    precision_qa_playbook_service: PrecisionQaPlaybookService,
    sop_reply_pack_service: SopReplyPackService,
    coze_client: CozeClient,
    sales_strategy_service: SalesStrategyService,
) -> OutreachService:
    return OutreachService(
        repository=repository,
        model_client=model_client,
        system_client=system_client,
        customer_context_service=customer_context_service,
        precision_qa_playbook_service=precision_qa_playbook_service,
        sop_reply_pack_service=sop_reply_pack_service,
        coze_client=coze_client,
        before_send_retry_seconds=settings.outreach_before_send_retry_seconds,
        sales_strategy_service=sales_strategy_service,
    )


def _build_service_rule_data_writer(settings: Settings, repository: AppRepository) -> ServiceRuleDataService:
    return ServiceRuleDataService(
        repository=repository,
        client=None,
        enabled=settings.service_rule_data_enabled,
        poll_seconds=settings.service_rule_data_poll_seconds,
        batch_size=settings.service_rule_data_batch_size,
        max_attempts=settings.service_rule_data_max_attempts,
        retry_base_seconds=settings.service_rule_data_retry_base_seconds,
    )


def _build_service_rule_data_worker(settings: Settings, repository: AppRepository) -> ServiceRuleDataService:
    client = ServiceRuleDataClient(settings) if settings.service_rule_data_enabled else None
    return ServiceRuleDataService(
        repository=repository,
        client=client,
        enabled=settings.service_rule_data_enabled,
        poll_seconds=settings.service_rule_data_poll_seconds,
        batch_size=settings.service_rule_data_batch_size,
        max_attempts=settings.service_rule_data_max_attempts,
        retry_base_seconds=settings.service_rule_data_retry_base_seconds,
    )


async def _close_runtime(storage_store: Any, platform_agent_client: PlatformAgentClient, closers: tuple[Any, ...]) -> None:
    seen: set[int] = set()
    for client in closers:
        if client is None or id(client) in seen:
            continue
        seen.add(id(client))
        close = getattr(client, "aclose", None)
        if close is not None:
            await close()
    platform_agent_client.close()
    storage_store.close()
