from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from app.services.storage.memory_repository import MemoryRepositoryMixin


class _Connection:
    def __init__(self) -> None:
        self.execute_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.executemany_calls: list[tuple[str, list[tuple[Any, ...]]]] = []

    def execute(self, sql: str, params: tuple[Any, ...]) -> None:
        self.execute_calls.append((sql, params))

    def executemany(self, sql: str, params: list[tuple[Any, ...]]) -> None:
        self.executemany_calls.append((sql, params))


class _Store:
    def __init__(self) -> None:
        self.connection = _Connection()

    @contextmanager
    def connect(self) -> Iterator[_Connection]:
        yield self.connection


class _Repository(MemoryRepositoryMixin):
    def __init__(self) -> None:
        self.store = _Store()


def test_save_memory_batches_history_event_inserts() -> None:
    repository = _Repository()

    repository.save_memory(
        "customer-1",
        {
            "history_events": [
                {"event_id": "event-1", "event_type": "first", "facts": {"value": 1}},
                {"event_id": "event-2", "event_type": "second", "facts": {"value": 2}},
            ]
        },
    )

    connection = repository.store.connection
    assert len(connection.execute_calls) == 1
    assert len(connection.executemany_calls) == 1
    _, rows = connection.executemany_calls[0]
    assert [row[0] for row in rows] == ["event-1", "event-2"]
