from __future__ import annotations

from app.services.storage.conversation_repository import ConversationRepositoryMixin
from app.services.storage.memory_repository import MemoryRepositoryMixin
from app.services.storage.run_repository import RunRepositoryMixin
from app.services.storage.sqlite_store import SQLiteStore


class AppRepository(ConversationRepositoryMixin, MemoryRepositoryMixin, RunRepositoryMixin):
    def __init__(self, store: SQLiteStore):
        self.store = store
