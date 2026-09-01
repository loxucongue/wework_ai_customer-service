from __future__ import annotations

from typing import Any

from app.config import get_settings
from app.services.coze_client import CozeClient
from app.services.customer_context import CustomerContextService
from app.services.model_client import ModelClient
from app.services.outreach_system_client import OutreachSystemClient
from app.services.precision_qa_playbook_service import PrecisionQaPlaybookService
from app.services.sales_strategy_service import SalesStrategyService
from app.services.sop_reply_pack_service import SopReplyPackService
from app.services.storage import AppRepository

from app.services.outreach.execution import TaskExecutor
from app.services.outreach.first_day import FirstDayWorkflow
from app.services.outreach.message import MessageGenerator
from app.services.outreach.planning import PlanGenerator


class OutreachService:
    def __init__(
        self,
        *,
        repository: AppRepository,
        model_client: ModelClient,
        system_client: OutreachSystemClient,
        customer_context_service: CustomerContextService | None = None,
        precision_qa_playbook_service: PrecisionQaPlaybookService | None = None,
        sop_reply_pack_service: SopReplyPackService | None = None,
        coze_client: CozeClient | None = None,
        before_send_retry_seconds: int = 60,
        first_day_wechat_allowlist: str | None = None,
        sales_strategy_service: SalesStrategyService | None = None,
    ) -> None:
        allowlist = (
            first_day_wechat_allowlist
            if first_day_wechat_allowlist is not None
            else get_settings().outreach_first_day_wechat_allowlist
        )
        self.repository = repository
        self.model_client = model_client
        self.system_client = system_client
        self.message = MessageGenerator(repository=repository, model_client=model_client)
        self.planning = PlanGenerator(
            repository=repository,
            model_client=model_client,
            system_client=system_client,
            customer_context_service=customer_context_service,
            precision_qa_playbook_service=precision_qa_playbook_service,
            sop_reply_pack_service=sop_reply_pack_service,
            coze_client=coze_client,
            sales_strategy_service=sales_strategy_service,
        )
        self.first_day = FirstDayWorkflow(
            repository=repository,
            model_client=model_client,
            customer_context_service=customer_context_service,
            first_day_wechat_allowlist=allowlist,
            planning=self.planning,
        )
        self.execution = TaskExecutor(
            repository=repository,
            system_client=system_client,
            customer_context_service=customer_context_service,
            before_send_retry_seconds=max(1, int(before_send_retry_seconds)),
            first_day_wechat_allowlist=allowlist,
            planning=self.planning,
            first_day=self.first_day,
            message=self.message,
        )

    @property
    def first_day_wechat_allowlist(self) -> str:
        return self.first_day.first_day_wechat_allowlist

    @first_day_wechat_allowlist.setter
    def first_day_wechat_allowlist(self, value: str) -> None:
        self.first_day.first_day_wechat_allowlist = value
        self.execution.first_day_wechat_allowlist = value

    def list_candidates(self, **kwargs: Any) -> dict[str, Any]:
        return self.planning.list_candidates(**kwargs)

    async def refresh_customer_conversation(self, **kwargs: Any) -> dict[str, Any]:
        return await self.planning.refresh_customer_conversation(**kwargs)

    async def generate_configured_strategy_shadow_plan(self, **kwargs: Any) -> dict[str, Any]:
        return await self.planning.generate_configured_strategy_shadow_plan(**kwargs)

    async def generate_plan(self, **kwargs: Any) -> dict[str, Any]:
        return await self.planning.generate_plan(**kwargs)

    async def ensure_platform_task_plan(self, **kwargs: Any) -> dict[str, Any]:
        return await self.planning.ensure_platform_task_plan(**kwargs)

    async def evaluate_first_day_opened_silence_customers(self, **kwargs: Any) -> dict[str, Any]:
        return await self.first_day.evaluate_first_day_opened_silence_customers(**kwargs)

    def record_closing_sequence_shadow(self, state: dict[str, Any]) -> dict[str, Any]:
        return self.planning.record_closing_sequence_shadow(state)

    def monitor_status(self) -> dict[str, Any]:
        return self.first_day.monitor_status()

    async def execute_due_tasks(self, **kwargs: Any) -> dict[str, Any]:
        return await self.execution.execute_due_tasks(**kwargs)

    async def execute_due_first_day_tasks(self, **kwargs: Any) -> dict[str, Any]:
        return await self.execution.execute_due_first_day_tasks(**kwargs)

    async def execute_task(self, task_id: str) -> dict[str, Any]:
        return await self.execution.execute(task_id)

    def finalize_message_delivery(self, dispatch: dict[str, Any]) -> None:
        self.execution.finalize_message_delivery(dispatch)
