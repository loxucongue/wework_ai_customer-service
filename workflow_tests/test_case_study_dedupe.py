from __future__ import annotations

import unittest

from app.chat_runtime import _case_image_send_record
from app.graph.nodes.action_module_outputs import build_planner_fact_output
from app.graph.nodes.action_nodes import _filter_case_studies_by_sent_documents
from app.graph.planner.brain_v2_normalizer import build_planner_plan_v2


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
