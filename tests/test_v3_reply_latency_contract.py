from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "ai_paths"))

from app.config import Settings  # noqa: E402
from app.graph.nodes.material_selection import parallel_reply_payload  # noqa: E402
from app.schemas import ChatRequest  # noqa: E402
from app.services.follow_knowledge_client import FollowKnowledgeClient  # noqa: E402
from app.services.storage import AppRepository, SQLiteStore  # noqa: E402


def _repository(tmp_path: Path) -> AppRepository:
    settings = Settings().model_copy(
        update={"aics_storage_backend": "sqlite", "db_path": tmp_path / "state.db"}
    )
    store = SQLiteStore(settings)
    store.initialize()
    return AppRepository(store)


def test_prepare_v3_request_persists_ingress_in_one_repository_operation(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    request = ChatRequest(
        content="现在多少钱？",
        customer_id="10001",
        platform_customer_id="10001",
        corp_id="corp-1",
        wechat="SL8003",
        external_userid="external-1",
        user_id="7294",
    )

    result = repository.prepare_v3_request(
        default_conversation_id="conversation-1",
        resolve_existing=True,
        request=request,
        request_id="request-1",
        title="现在多少钱？",
        input_snapshot={"content": request.content},
        interface_version="v3",
        started_at="2026-09-08T12:00:00+00:00",
        http_request_ingress_id="ingress-1",
    )

    assert result["conversation_id"] == "conversation-1"
    assert result["connection_count"] == 1
    run = repository.get_run("request-1")["run"]
    assert run["output_snapshot"]["runtime_phase"] == "request_received"
    assert run["output_snapshot"]["http_request_ingress_id"] == "ingress-1"
    with repository.store.connect() as conn:
        messages = conn.execute(
            "SELECT role, content FROM messages WHERE request_id=?", ("request-1",)
        ).fetchall()
    assert [dict(item) for item in messages] == [{"role": "user", "content": "现在多少钱？"}]


class _CountingKnowledgeClient(FollowKnowledgeClient):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.calls = 0

    async def _request_with_retry(self, path: str, payload: dict[str, object]) -> httpx.Response:
        self.calls += 1
        await asyncio.sleep(0.04)
        request = httpx.Request("POST", f"https://example.test/{path}")
        return httpx.Response(
            200,
            request=request,
            json={"code": 200, "data": {"total": 0, "page": 1, "pageSize": 100, "list": []}},
        )


def test_identical_knowledge_cache_misses_use_singleflight() -> None:
    settings = Settings().model_copy(
        update={
            "follow_knowledge_enabled": True,
            "follow_knowledge_base_url": "https://example.test",
            "follow_knowledge_token": "test-token",
            "follow_knowledge_cache_ttl_seconds": 300,
        }
    )
    client = _CountingKnowledgeClient(settings)

    async def invoke():
        return await asyncio.gather(
            client.query_sequences(page=1, page_size=100),
            client.query_sequences(page=1, page_size=100),
            client.query_sequences(page=1, page_size=100),
        )

    results = asyncio.run(invoke())
    assert client.calls == 1
    assert all(item["status"] == "ok" for item in results)
    assert sum(bool(item.get("singleflight_wait")) for item in results) == 2


def test_reply_payload_does_not_duplicate_large_policy_and_knowledge_objects() -> None:
    recall = {"status": "ok", "candidates": [], "sequence_candidates": []}
    state = {
        "evidence_join": {
            "shared_context": {
                "current_message": {"content": "你好"},
                "conversation": [],
                "authoritative_facts": {},
                "ai_sales_policy": {"large": "x" * 5000},
                "previous_policy_state": {"large": "y" * 5000},
            },
            "sales_recall": recall,
            "knowledge_evidence": recall,
            "semantic_route": {},
            "content_candidates": [],
            "tool_facts": {},
        }
    }

    payload = parallel_reply_payload(state)  # type: ignore[arg-type]
    evidence = payload["evidence"]
    assert "knowledge_evidence" not in evidence
    assert "ai_sales_policy" not in evidence["shared_context"]
    assert "previous_policy_state" not in evidence["shared_context"]
    assert payload["ai_sales_policy"]["large"].startswith("x")
