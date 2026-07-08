from __future__ import annotations

import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.chat_runtime import _activity_intro_image_record_plan, _case_image_send_record
from app.config import Settings
from app.graph.nodes import action_module_outputs
from app.graph.nodes.action_module_outputs import build_planner_fact_output
from app.graph.nodes.action_nodes import _filter_case_studies_by_sent_documents
from app.graph.planner.brain_v2_normalizer import build_planner_plan_v2
from app.services.memory_store import CustomerMemoryStore


class CaseStudyDedupeTests(unittest.TestCase):
    def test_filters_sent_case_document_ids_before_fact_output(self) -> None:
        tool_results = {
            "case_studies": {
                "items": [
                    {
                        "content": '<img src="https://example.com/a.png"> description: A',
                        "document_id": "doc-a",
                    },
                    {
                        "content": '<img src="https://example.com/b.png"> description: B',
                        "document_id": "doc-b",
                    },
                ]
            }
        }
        state = {"customer_profile": {"sent_case_document_ids": ["doc-a"]}}
        tool_calls: list[dict] = []

        _filter_case_studies_by_sent_documents(tool_results, state, tool_calls)
        fact_output = build_planner_fact_output(tool_results, state)
        case_facts = fact_output["fact_envelope"]["structured_facts"]["case_facts"]

        self.assertEqual([item["document_id"] for item in tool_results["case_studies"]["items"]], ["doc-b"])
        self.assertEqual(tool_results["case_studies"]["case_studies_filter"]["filtered_document_ids"], ["doc-a"])
        self.assertEqual(case_facts[0]["document_id"], "doc-b")
        self.assertEqual(case_facts[0]["image_url"], "https://example.com/b.png")

    def test_empty_case_studies_use_configured_case_image_pool(self) -> None:
        tool_results = {
            "case_studies": {
                "items": [],
                "case_studies_filter": {"filtered_document_ids": []},
            }
        }
        state = {"customer_profile": {"sent_case_document_ids": ["configured_case_image_1"]}}

        with patch.object(
            action_module_outputs,
            "load_business_rules",
            return_value={
                "offer": {
                    "case_image_fallback_urls": [
                        "https://example.com/case-a.jpg",
                        "https://example.com/case-b.jpg",
                    ]
                }
            },
        ):
            fact_output = build_planner_fact_output(tool_results, state)

        case_facts = fact_output["fact_envelope"]["structured_facts"]["case_facts"]
        self.assertEqual(case_facts[0]["source"], "configured_case_image_pool")
        self.assertEqual(case_facts[0]["status"], "fallback_case_image")
        self.assertEqual(case_facts[0]["document_id"], "configured_case_image_2")
        self.assertEqual(case_facts[0]["image_url"], "https://example.com/case-b.jpg")

    def test_final_image_message_maps_back_to_case_document_id(self) -> None:
        state = {
            "fact_envelope": {
                "structured_facts": {
                    "case_facts": [
                        {
                            "document_id": "doc-b",
                            "image_url": "https://example.com/b.png",
                        }
                    ]
                }
            }
        }
        messages = [{"type": "image", "order": 1, "content": {"url": "https://example.com/b.png"}}]

        record = _case_image_send_record(state, messages)

        self.assertEqual(record["document_ids"], ["doc-b"])
        self.assertEqual(record["unmatched_image_urls"], [])

    def test_activity_intro_image_message_maps_to_activity_record(self) -> None:
        state = {"business_rules": {"offer": {"activity_intro_image_url": "https://example.com/activity.jpg"}}}
        messages = [{"type": "image", "order": 1, "content": {"url": "https://example.com/activity.jpg"}}]

        record = _activity_intro_image_record_plan(state, messages, send_mode="async")

        self.assertEqual(record["image_url"], "https://example.com/activity.jpg")
        self.assertEqual(record["send_mode"], "async")

    def test_memory_store_records_activity_intro_image_sent_event(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = CustomerMemoryStore(Settings(memory_dir=temp_dir))

            result = store.record_activity_intro_image_sent(
                "customer-a",
                image_url="https://example.com/activity.jpg",
                request_id="request-a",
                send_mode="async",
            )
            memory = store.load("customer-a")

        self.assertEqual(result["status"], "recorded")
        self.assertEqual(memory["history_events"][0]["event_type"], "activity_intro_image_sent")
        self.assertEqual(memory["history_events"][0]["facts"]["image_url"], "https://example.com/activity.jpg")
        self.assertEqual(memory["history_events"][0]["facts"]["send_mode"], "async")

    def test_planner_rejects_sales_talk_as_selectable_kb(self) -> None:
        plan = build_planner_plan_v2(
            {"normalized_content": "compare"},
            {
                "decision": "need_tools",
                "stage": "S1",
                "sub_rule_id": "S1_GREETING",
                "reply_messages": [{"type": "text", "content": {"text": "checking"}}],
                "tool_calls": [
                    {
                        "name": "kb_search",
                        "kb_name": "sales_talk_qa",
                        "query": "compare",
                    }
                ],
            },
        )

        self.assertEqual(plan["planner_tool_calls"], [])
        self.assertIn("unsupported_kb:sales_talk_qa", {item["missing"] for item in plan["tool_policy_violations"]})


if __name__ == "__main__":
    unittest.main()
