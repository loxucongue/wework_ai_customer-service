from __future__ import annotations

from app.config import Settings
from app.services.storage.mirrored_store import MirroredStore
from app.services.storage.mysql_store import MySQLStore
from app.services.storage.sqlite_store import SQLiteStore
from app.services.storage.store_base import Store


def build_store(settings: Settings) -> Store:
    backend = settings.aics_storage_backend.strip().lower()
    if backend == "sqlite":
        return SQLiteStore(settings)
    if backend != "mysql":
        raise ValueError("AICS_STORAGE_BACKEND must be 'sqlite' or 'mysql'")
    mysql_store = MySQLStore(settings)
    if settings.aics_sqlite_mirror_enabled:
        return MirroredStore(mysql_store, SQLiteStore(settings))
    return mysql_store

