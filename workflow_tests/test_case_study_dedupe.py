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

    def test_selected_effect_asset_image_is_recorded_without_kb_document_id(self) -> None:
        state = {
            "selected_content_ids": ["s10_need_and_case"],
            "evidence_join": {
                "content_candidates": [
                    {
                        "content_id": "s10_need_and_case",
                        "asset_role": "effect_evidence",
                        "messages": [
                            {"type": "image", "content": "https://example.com/fixed-case.jpg"}
                        ],
                    }
                ]
            },
        }
        messages = [{"type": "image", "content": "https://example.com/fixed-case.jpg"}]

        record = _case_image_send_record(state, messages)

        self.assertEqual(record["document_ids"], [])
        self.assertEqual(record["image_urls"], ["https://example.com/fixed-case.jpg"])
        self.assertEqual(record["unmatched_image_urls"], [])
        self.assertEqual(record["selected_effect_asset_ids"], ["s10_need_and_case"])

    def test_memory_store_records_url_only_case_image_event(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = CustomerMemoryStore(Settings(memory_dir=temp_dir))

            result = store.record_case_images_sent(
                "customer-a",
                document_ids=[],
                image_urls=["https://example.com/fixed-case.jpg"],
                request_id="request-a",
            )
            memory = store.load("customer-a")

        self.assertEqual(result["status"], "recorded")
        self.assertEqual(memory["history_events"][0]["event_type"], "case_image_sent")
        self.assertEqual(
            memory["history_events"][0]["facts"]["image_urls"],
            ["https://example.com/fixed-case.jpg"],
        )
        self.assertNotIn("sent_case_document_ids", memory.get("portrait") or {})

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

    def test_v3_delivery_facts_are_shared_but_version_attributed(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = CustomerMemoryStore(Settings(memory_dir=temp_dir))

            store.record_case_images_sent(
                "customer-v3",
                document_ids=["case-1"],
                image_urls=["https://example.com/case-1.jpg"],
                request_id="request-case-v3",
                interface_version="v3",
            )
            store.record_activity_intro_image_sent(
                "customer-v3",
                image_url="https://example.com/activity.jpg",
                request_id="request-activity-v3",
                send_mode="sync",
                interface_version="v3",
            )
            store.record_store_fact(
                "customer-v3",
                store={"store_id": "store-1", "store_name": "测试门店", "city": "上海市"},
                event_type="store_address_sent",
                request_id="request-store-v3",
                interface_version="v3",
            )
            store.record_authoritative_payment_fact(
                "customer-v3",
                deposit_state="paid_by_platform_transfer_event",
                source="platform.unknown_message_transfer",
                request_id="request-payment-v3",
                interface_version="v3",
            )
            memory = store.load("customer-v3")

        events = memory["history_events"]
        self.assertEqual(len(events), 4)
        self.assertTrue(all(event["facts"]["interface_version"] == "v3" for event in events))

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

    def test_planner_allows_teaching_and_cooperation_kbs(self) -> None:
        plan = build_planner_plan_v2(
            {"normalized_content": "教学合作资料"},
            {
                "decision": "need_tools",
                "stage": "S1",
                "sub_rule_id": "S1_TRUST",
                "tool_calls": [
                    {
                        "name": "kb_search",
                        "kb_name": "教学类",
                        "query": "教学流程资料",
                    },
                    {
                        "name": "kb_search",
                        "kb_name": "合作类",
                        "query": "合作活动资料",
                    },
                ],
            },
        )

        self.assertEqual(
            [item["kb_name"] for item in plan["planner_tool_calls"]],
            ["教学类", "合作类"],
        )
        self.assertFalse(
            any(str(item.get("missing") or "").startswith("unsupported_kb") for item in plan["tool_policy_violations"])
        )


if __name__ == "__main__":
    unittest.main()
