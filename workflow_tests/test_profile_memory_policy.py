from __future__ import annotations

import unittest
from typing import Any

from app.graph.nodes.profile_nodes import create_profile_event_extractor_node


class ProfileMemoryPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_platform_request_skips_profile_persistence(self) -> None:
        memory_store = _MemoryStore()
        node = create_profile_event_extractor_node(
            trace_logger=_TraceLogger(),
            memory_store=memory_store,
            model_client=None,
            compact_memory=lambda value: value,
        )

        output = await node(
            {
                "request_id": "req-1",
                "customer_id": "debug-customer",
                "normalized_content": "你好",
                "request_context": {"memory_persist_allowed": False},
                "trace": [],
            }
        )

        self.assertEqual(output["profile_extraction_skipped"], "memory_persist_not_allowed")
        self.assertEqual(memory_store.saved, [])

    async def test_persisted_profile_node_records_facts_without_calling_model(self) -> None:
        memory_store = _MemoryStore()
        model = _UnexpectedModel()
        node = create_profile_event_extractor_node(
            trace_logger=_TraceLogger(),
            memory_store=memory_store,
            model_client=model,
            compact_memory=lambda value: value,
        )

        output = await node(
            {
                "request_id": "req-voice",
                "sales_contact_key": "corp:wechat:customer",
                "normalized_content": "我现在在上班",
                "request_context": {
                    "memory_persist_allowed": True,
                    "msgid": "voice-1",
                    "voice_transcription": {"status": "ok"},
                },
                "customer_context": {"orders": []},
                "trace": [],
            }
        )

        self.assertEqual(model.calls, 0)
        self.assertEqual(output["profile_extraction_skipped"], "soft_profile_model_retired")
        events = memory_store.saved[0]["event_updates"]
        self.assertEqual(events[0]["event_type"], "voice_transcript_received")
        self.assertEqual(events[0]["facts"]["transcript"], "我现在在上班")
        self.assertNotIn("summary", events[0])
        self.assertNotIn("impact", events[0])


class _MemoryStore:
    def __init__(self) -> None:
        self.saved: list[dict[str, Any]] = []

    def save_update(self, customer_id: str, *, profile_update: dict[str, Any], event_updates: list[dict[str, Any]]) -> dict[str, Any]:
        self.saved.append(
            {
                "customer_id": customer_id,
                "profile_update": profile_update,
                "event_updates": event_updates,
            }
        )
        return {}


class _UnexpectedModel:
    available = True

    def __init__(self) -> None:
        self.calls = 0

    async def chat_json(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        raise AssertionError("profile model must not be called")


class _TraceLogger:
    class _Span:
        def __init__(self) -> None:
            self.entry: dict[str, Any] = {}

        def __enter__(self) -> dict[str, Any]:
            return {"entry": self.entry}

        def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
            return None

    def node(self, state: dict[str, Any], name: str, input_snapshot: dict[str, Any]) -> "_TraceLogger._Span":
        return self._Span()


if __name__ == "__main__":
    unittest.main()
