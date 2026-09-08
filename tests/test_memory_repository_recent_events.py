from __future__ import annotations

from pathlib import Path

from app.config import Settings
from app.services.storage import AppRepository, SQLiteStore


def _repository(tmp_path: Path) -> AppRepository:
    settings = Settings().model_copy(
        update={"aics_storage_backend": "sqlite", "db_path": tmp_path / "state.db"}
    )
    store = SQLiteStore(settings)
    store.initialize()
    return AppRepository(store)


def _events(count: int, *, prefix: str = "event") -> list[dict[str, object]]:
    return [
        {
            "event_id": f"{prefix}-{index:03d}",
            "event_type": "delivery_fact",
            "facts": {"index": index},
            "event_time": f"2026-09-08T00:{index // 60:02d}:{index % 60:02d}+00:00",
        }
        for index in range(count)
    ]


def test_load_memory_returns_latest_one_hundred_events_in_time_order(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.save_memory("customer-1", {"history_events": _events(105)})

    memory = repository.load_memory("customer-1")

    assert memory is not None
    loaded = memory["history_events"]
    assert len(loaded) == 100
    assert loaded[0]["event_id"] == "event-005"
    assert loaded[-1]["event_id"] == "event-104"


def test_load_memories_returns_latest_events_for_each_customer(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.save_memory("customer-1", {"history_events": _events(105)})
    repository.save_memory("customer-2", {"history_events": _events(102, prefix="second")})

    memories = repository.load_memories(["customer-1", "customer-2"])

    first = memories["customer-1"]["history_events"]
    second = memories["customer-2"]["history_events"]
    assert (first[0]["event_id"], first[-1]["event_id"]) == ("event-005", "event-104")
    assert (second[0]["event_id"], second[-1]["event_id"]) == ("second-002", "second-101")
