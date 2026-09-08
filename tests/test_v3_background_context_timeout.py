from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ai_paths"))

from app.config import Settings  # noqa: E402
from app.graph.nodes import layer_nodes  # noqa: E402
from app.services.trace_logger import TraceLogger  # noqa: E402


class _SlowMemory:
    def load(self, _customer_id: str) -> dict[str, object]:
        time.sleep(0.15)
        return {"portrait": {"late": True}}


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
