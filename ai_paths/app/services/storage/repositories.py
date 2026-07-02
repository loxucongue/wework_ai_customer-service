from __future__ import annotations

from app.services.storage.conversation_repository import ConversationRepositoryMixin
from app.services.storage.customer_record_admin_repository import CustomerRecordAdminRepositoryMixin
from app.services.storage.memory_repository import MemoryRepositoryMixin
from app.services.storage.outreach_repository import OutreachRepositoryMixin
from app.services.storage.run_repository import RunRepositoryMixin
from app.services.storage.sqlite_store import SQLiteStore
from app.services.storage.sop_event_repository import SopEventRepositoryMixin


class AppRepository(
    ConversationRepositoryMixin,
    CustomerRecordAdminRepositoryMixin,
    MemoryRepositoryMixin,
    OutreachRepositoryMixin,
    RunRepositoryMixin,
    SopEventRepositoryMixin,
):
    def __init__(self, store: SQLiteStore):
        self.store = store
