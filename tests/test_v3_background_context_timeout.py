from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ai_paths"))

from app.config import Settings  # noqa: E402
from app.graph.nodes import layer_nodes  # noqa: E402
from app.graph.nodes.authoritative_context import create_shared_context_node  # noqa: E402
from app.services.trace_logger import TraceLogger  # noqa: E402


class _SlowMemory:
    def load(self, _customer_id: str) -> dict[str, object]:
        time.sleep(0.15)
        return {"portrait": {"late": True}}


class _ParallelMemory:
    def load(self, _customer_id: str) -> dict[str, object]:
        time.sleep(0.05)
        return {}


class _MustNotReloadSop:
    def reply_chain_content_catalog(self) -> dict[str, object]:
        return {"schema_version": "reply_chain_content_index_v2", "sop_packs": []}

    def reply_chain_sop_progress(self, *_args, **_kwargs):
        raise AssertionError("preloaded SOP progress must be reused")

    def reply_chain_available_assets(self) -> list[dict[str, object]]:
        return []


def test_background_memory_read_has_a_hard_wait_budget(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(layer_nodes, "BACKGROUND_EXTERNAL_TIMEOUT_SECONDS", 0.02)
    settings = Settings().model_copy(update={"trace_log_dir": tmp_path / "trace"})
    node = layer_nodes.create_background_context_layer(
        trace_logger=TraceLogger(settings),
        memory_store=_SlowMemory(),  # type: ignore[arg-type]
        customer_context_service=None,
        customer_store_knowledge_service=None,
    )
    state = {
        "customer_id": "customer-1",
        "user_id": 88,
        "wechat": "SL8003",
        "sales_contact_key": "corp-1|SL8003|external-1",
        "request_context": {},
        "trace": [],
    }

    async def invoke():
        started = time.perf_counter()
        result = await node(state)  # type: ignore[arg-type]
        return result, time.perf_counter() - started

    result, elapsed = asyncio.run(invoke())

    assert elapsed < 0.1
    assert result["memory_scope_status"] == "timeout"
    assert result["memory_error"] == "timeout_after_0.02s"


def test_sop_progress_loads_in_parallel_and_is_reused(tmp_path: Path) -> None:
    settings = Settings().model_copy(update={"trace_log_dir": tmp_path / "trace"})
    trace_logger = TraceLogger(settings)

    def load_sop(_state):
        time.sleep(0.05)
        return {
            "status": "ok",
            "source": "test",
            "completed_pack_ids": ["pack-1"],
            "completed_categories": ["effect"],
            "unfinished_sops": [],
        }

    background = layer_nodes.create_background_context_layer(
        trace_logger=trace_logger,
        memory_store=_ParallelMemory(),  # type: ignore[arg-type]
        customer_context_service=None,
        customer_store_knowledge_service=None,
        sop_progress_loader=load_sop,
    )
    state = {
        "request_id": "request-1",
        "customer_id": "customer-1",
        "platform_customer_id": "customer-1",
        "corp_id": "corp-1",
        "external_userid": "external-1",
        "user_id": 88,
        "wechat": "SL8003",
        "sales_contact_key": "corp-1|SL8003|external-1",
        "request_context": {},
        "trace": [],
    }

    async def invoke():
        started = time.perf_counter()
        result = await background(state)  # type: ignore[arg-type]
        elapsed = time.perf_counter() - started
        joined_state = {**state, **result}
        shared = await create_shared_context_node(
            trace_logger=trace_logger,
            sop_execution_service=_MustNotReloadSop(),  # type: ignore[arg-type]
        )(joined_state)  # type: ignore[arg-type]
        return result, shared, elapsed

    result, shared, elapsed = asyncio.run(invoke())

    assert elapsed < 0.09
    assert result["preloaded_sop_progress"]["completed_pack_ids"] == ["pack-1"]
    assert (
        shared["shared_context"]["authoritative_facts"]["sop_progress"]["completed_pack_ids"]
        == ["pack-1"]
    )
