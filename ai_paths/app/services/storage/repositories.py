from __future__ import annotations

from app.services.storage.conversation_repository import ConversationRepositoryMixin
from app.services.storage.customer_record_admin_repository import CustomerRecordAdminRepositoryMixin
from app.services.storage.memory_repository import MemoryRepositoryMixin
from app.services.storage.message_delivery_repository import MessageDeliveryRepositoryMixin
from app.services.storage.outreach_repository import OutreachRepositoryMixin
from app.services.storage.operations_dashboard_repository import OperationsDashboardRepositoryMixin
from app.services.storage.run_repository import RunRepositoryMixin
from app.services.storage.store_base import Store
from app.services.storage.sop_event_repository import SopEventRepositoryMixin
from app.services.storage.strategy_data_repository import StrategyDataRepositoryMixin
from app.services.storage.v3_strategy_analytics_repository import V3StrategyAnalyticsRepositoryMixin


class AppRepository(
    ConversationRepositoryMixin,
    CustomerRecordAdminRepositoryMixin,
    MemoryRepositoryMixin,
    MessageDeliveryRepositoryMixin,
    OutreachRepositoryMixin,
    OperationsDashboardRepositoryMixin,
    RunRepositoryMixin,
    SopEventRepositoryMixin,
    StrategyDataRepositoryMixin,
    V3StrategyAnalyticsRepositoryMixin,
):
    def __init__(self, store: Store):
        self.store = store
