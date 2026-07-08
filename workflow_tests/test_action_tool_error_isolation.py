from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from app.config import Settings
from app.graph.nodes.action_nodes import create_execute_actions_node
from app.services.trace_logger import TraceLogger


class _FailingCozeClient:
    def __init__(self) -> None:
        self.settings = Settings(geocode_workflow_id="geocode-test")

    async def run_workflow(self, workflow_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("coze unavailable")


class ActionToolErrorIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_customer_store_lookup_error_is_recorded_without_breaking_graph(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            node = create_execute_actions_node(
                coze_client=_FailingCozeClient(),
                trace_logger=TraceLogger(Settings(trace_log_dir=Path(tmpdir))),
                store_service=None,
                appointment_query_from_state=lambda _content, _store_lookup, _state: {},
            )
            state: dict[str, Any] = {
                "request_id": "test-store-tool-error",
                "trace": [],
                "errors": [],
                "normalized_content": "厦门有门店吗",
                "planner_tool_calls": [
                    {"name": "customer_store_lookup", "purpose": "existence", "query": "厦门"}
                ],
                "required_tools": [
                    {"name": "customer_store_lookup", "purpose": "existence", "query": "厦门"}
                ],
            }

            output = await node(state)

        result = output["tool_results"]["customer_store_lookup"]
        self.assertEqual(result["status"], "tool_error")
        self.assertIn("RuntimeError: coze unavailable", result["error"])
        structured = output["fact_envelope"]["structured_facts"]
        self.assertEqual(structured["tool_errors"][0]["tool"], "customer_store_lookup")


if __name__ == "__main__":
    unittest.main()
