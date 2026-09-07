from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ai_paths"))

from app.config import Settings  # noqa: E402
from app.services.memory_store import CustomerMemoryStore  # noqa: E402


class _Repository:
    def __init__(self) -> None:
        self.loads = 0
        self.saves = 0
        self.memory: dict[str, Any] = {}

    def load_memory(self, _customer_id: str) -> dict[str, Any]:
        self.loads += 1
        return deepcopy(self.memory)

    def save_memory(self, _customer_id: str, memory: dict[str, Any]) -> None:
        self.saves += 1
        self.memory = deepcopy(memory)


def test_write_batch_coalesces_reply_provenance_into_one_load_and_save(tmp_path: Path) -> None:
    repository = _Repository()
    store = CustomerMemoryStore(
        Settings(_env_file=None, memory_dir=tmp_path / "memory"),
        repository,  # type: ignore[arg-type]
    )

    with store.write_batch("customer-1"):
        store.record_reply_model_observation(
            "customer-1",
            request_id="request-1",
            primary_objective="处理距离顾虑",
            customer_friction_observation="客户认为门店太远",
        )
        store.record_follow_knowledge_usage(
            "customer-1",
            request_id="request-1",
            knowledge_use={"sequence_id": "45", "selected_script_ids": ["310"]},
        )

    assert repository.loads == 1
    assert repository.saves == 1
    assert {
        event["event_type"] for event in repository.memory["history_events"]
    } == {"v3_reply_model_observation", "v3_follow_knowledge_usage"}


def test_write_batch_is_scoped_per_store_context(tmp_path: Path) -> None:
    repository = _Repository()
    store = CustomerMemoryStore(
        Settings(_env_file=None, memory_dir=tmp_path / "memory"),
        repository,  # type: ignore[arg-type]
    )

    with store.write_batch("customer-1"):
        store.record_follow_knowledge_match(
            "customer-1",
            request_id="request-1",
            semantic_route={
                "checkpoint": {"primary_code": "cp9"},
                "sequence_match": {"sequence_ids": ["45"]},
            },
        )
    store.record_follow_knowledge_usage(
        "customer-1",
        request_id="request-2",
        knowledge_use={"sequence_id": "45"},
    )

    assert repository.loads == 2
    assert repository.saves == 2
