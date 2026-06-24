from __future__ import annotations

import unittest

from app.graph.planner.brain_v2 import _planner_message_contract_violations, _planner_payload_for_model
from app.graph.planner.brain_v2_normalizer import build_planner_plan_v2


class PlannerDistanceCandidateTests(unittest.TestCase):
    def test_distance_candidate_expands_to_city_scope(self) -> None:
        state = {
            "normalized_content": "重庆巴南附近哪家店最近",
            "customer_store_knowledge": {
                "stores": [
                    {"store_id": "189", "store_name": "重庆巴南店", "city": "重庆市", "district": "巴南区"},
                    {"store_id": "467", "store_name": "重庆百星渝中店", "city": "重庆市", "district": "渝中区"},
                    {"store_id": "488", "store_name": "重庆百星南坪店", "city": "重庆市", "district": "南岸区"},
                    {"store_id": "11", "store_name": "上海徐汇店", "city": "上海市", "district": "徐汇区"},
                ]
            },
        }
        payload = {
            "decision": "need_tools",
            "stage": "S2",
            "sub_rule_id": "S2_LOCATION_DETAIL",
            "reply_messages": [{"type": "text", "content": {"text": "我帮您核对一下附近门店。"}}],
            "tool_calls": [{"name": "distance_calculate", "origin": "重庆巴南", "candidate_store_ids": ["189"]}],
            "handoff": {"needed": False, "reason": ""},
        }

        plan = build_planner_plan_v2(state, payload)
        tool = plan["planner_tool_calls"][0]

        self.assertEqual(tool["name"], "distance_calculate")
        self.assertEqual(tool["candidate_city"], "重庆市")
        self.assertEqual(tool["candidate_store_ids"], ["189", "467", "488"])
        self.assertEqual(tool["candidate_expanded_from"], ["189"])

    def test_payment_collection_is_added_for_signup_direct_reply(self) -> None:
        plan = build_planner_plan_v2(
            {"normalized_content": "我想报名"},
            {
                "decision": "direct_reply",
                "stage": "S3",
                "sub_rule_id": "S3_PAYMENT_COLLECTION",
                "reply_messages": [{"type": "text", "content": {"text": "我先把10元预约金入口发您。"}}],
                "tool_calls": [],
            },
        )

        self.assertEqual([item["type"] for item in plan["planner_reply_messages"]], ["text", "payment_collection"])

    def test_short_acceptance_after_store_recommendation_does_not_ask_area_again(self) -> None:
        plan = build_planner_plan_v2(
            {
                "normalized_content": "可以",
                "conversation_history": [
                    "用户: 我在重庆",
                    "助手: 重庆有门店哦～您在哪个区？比如渝中、南岸、沙坪坝，我帮您安排就近的～",
                    "用户: 渝中",
                    "助手: 渝中区有门店哦～重庆百星渝中店，您方便到店吗？",
                ],
                "customer_store_knowledge": {
                    "stores": [
                        {"store_id": "467", "store_name": "重庆百星渝中店", "city": "重庆市", "district": "渝中区"}
                    ]
                },
            },
            {
                "decision": "direct_reply",
                "stage": "S2",
                "sub_rule_id": "S2_CITY_ONLY",
                "reply_messages": [{"type": "text", "content": {"text": "好的～您在哪个区？比如渝中、南岸、沙坪坝，我帮您安排就近的门店。"}}],
                "tool_calls": [],
            },
        )

        self.assertEqual(plan["planner_stage"], "S3")
        self.assertEqual(plan["planner_sub_rule_id"], "S3_APPOINTMENT_TIME")
        text = plan["planner_reply_messages"][0]["content"]["text"]
        self.assertIn("重庆百星渝中店", text)
        self.assertIn("今天还是明天", text)
        self.assertNotIn("哪个区", text)

    def test_low_information_opening_suppresses_old_profile_for_planner(self) -> None:
        payload = _planner_payload_for_model(
            {
                "normalized_content": "你好",
                "customer_profile": {"summary": "旧画像"},
                "history_events": [{"event_type": "old"}],
                "conversation_history": ["用户: 之前的历史"],
            }
        )

        self.assertNotIn("customer_profile", payload)
        self.assertNotIn("history_events", payload)
        self.assertNotIn("conversation_history", payload)

    def test_after_sales_effect_feedback_requires_s4(self) -> None:
        violations = _planner_message_contract_violations(
            {"normalized_content": "做完没效果怎么办"},
            {
                "planner_decision": "direct_reply",
                "planner_stage": "S1",
                "planner_sub_rule_id": "S1_PROJECT_DIRECTION",
                "planner_reply_messages": [{"type": "text", "content": {"text": "到店先检测。"}}],
                "planner_tool_calls": [],
                "primary_task": {"type": "project_consult", "subtype": "s1_project_direction"},
            },
        )

        self.assertIn("after_sales_stage_required", {item["missing"] for item in violations})

    def test_parking_question_rejects_case_studies_tool(self) -> None:
        violations = _planner_message_contract_violations(
            {"normalized_content": "重庆巴南店有停车吗"},
            {
                "planner_decision": "need_tools",
                "planner_stage": "S2",
                "planner_sub_rule_id": "S2_PARKING_OR_HOURS",
                "planner_reply_messages": [{"type": "text", "content": {"text": "我帮您核对。"}}],
                "planner_tool_calls": [{"name": "kb_search", "kb_name": "case_studies", "query": "重庆巴南店 停车"}],
                "primary_task": {"type": "store_inquiry", "subtype": "s2_parking_or_hours"},
            },
        )

        self.assertIn("store_detail_fact_tool_required", {item["missing"] for item in violations})

    def test_parking_question_rejects_after_sales_handoff(self) -> None:
        violations = _planner_message_contract_violations(
            {"normalized_content": "重庆巴南店有停车吗"},
            {
                "planner_decision": "need_tools",
                "planner_stage": "S4",
                "planner_sub_rule_id": "S4_COMPLAINT_REFUND",
                "planner_reply_messages": [{"type": "text", "content": {"text": "我让专业同事核对。"}}],
                "planner_tool_calls": [{"name": "professional_assist", "purpose": "misrouted"}],
                "handoff": {"needed": True, "reason": "misrouted"},
                "primary_task": {"type": "after_sales", "subtype": "s4_complaint_refund"},
            },
        )

        self.assertIn("store_detail_stage_required", {item["missing"] for item in violations})

    def test_address_request_without_store_anchor_rejects_distance_tool(self) -> None:
        violations = _planner_message_contract_violations(
            {
                "normalized_content": "发一下地址",
                "customer_store_knowledge": {
                    "stores": [
                        {"store_id": "467", "store_name": "重庆百星渝中店", "city": "重庆市", "district": "渝中区"},
                        {"store_id": "488", "store_name": "重庆百星南坪店", "city": "重庆市", "district": "南岸区"},
                    ]
                },
            },
            {
                "planner_decision": "need_tools",
                "planner_stage": "S2",
                "planner_sub_rule_id": "S2_LOCATION_DETAIL",
                "planner_reply_messages": [{"type": "text", "content": {"text": "我帮您核对。"}}],
                "planner_tool_calls": [{"name": "distance_calculate", "origin": "重庆", "candidate_store_ids": ["467", "488"]}],
                "primary_task": {"type": "store_inquiry", "subtype": "s2_location_detail"},
            },
        )

        self.assertIn("store_detail_anchor_required", {item["missing"] for item in violations})

    def test_address_request_with_unique_region_allows_distance_tool(self) -> None:
        violations = _planner_message_contract_violations(
            {
                "normalized_content": "渝中地址发我",
                "customer_store_knowledge": {
                    "stores": [
                        {"store_id": "467", "store_name": "重庆百星渝中店", "city": "重庆市", "district": "渝中区"},
                        {"store_id": "488", "store_name": "重庆百星南坪店", "city": "重庆市", "district": "南岸区"},
                    ]
                },
            },
            {
                "planner_decision": "need_tools",
                "planner_stage": "S2",
                "planner_sub_rule_id": "S2_LOCATION_DETAIL",
                "planner_reply_messages": [{"type": "text", "content": {"text": "我帮您核对。"}}],
                "planner_tool_calls": [{"name": "distance_calculate", "origin": "重庆渝中", "candidate_store_ids": ["467"]}],
                "primary_task": {"type": "store_inquiry", "subtype": "s2_location_detail"},
            },
        )

        self.assertNotIn("store_detail_anchor_required", {item["missing"] for item in violations})

    def test_available_time_without_store_anchor_is_rejected(self) -> None:
        violations = _planner_message_contract_violations(
            {
                "normalized_content": "我在重庆，明天能去",
                "customer_store_knowledge": {
                    "stores": [
                        {"store_id": "467", "store_name": "重庆百星渝中店", "city": "重庆市", "district": "渝中区"},
                        {"store_id": "488", "store_name": "重庆百星南坪店", "city": "重庆市", "district": "南岸区"},
                    ]
                },
            },
            {
                "planner_decision": "need_tools",
                "planner_stage": "S3",
                "planner_sub_rule_id": "S3_APPOINTMENT_TIME",
                "planner_reply_messages": [{"type": "text", "content": {"text": "我帮您查明天档期。"}}],
                "planner_tool_calls": [{"name": "available_time", "store_id": "467", "date": "2026-06-24"}],
                "primary_task": {"type": "appointment", "subtype": "s3_appointment_time"},
            },
        )

        self.assertIn("appointment_store_anchor_required", {item["missing"] for item in violations})

    def test_available_time_with_unique_region_is_allowed(self) -> None:
        violations = _planner_message_contract_violations(
            {
                "normalized_content": "我在重庆渝中，明天能去",
                "customer_store_knowledge": {
                    "stores": [
                        {"store_id": "467", "store_name": "重庆百星渝中店", "city": "重庆市", "district": "渝中区"},
                        {"store_id": "488", "store_name": "重庆百星南坪店", "city": "重庆市", "district": "南岸区"},
                    ]
                },
            },
            {
                "planner_decision": "need_tools",
                "planner_stage": "S3",
                "planner_sub_rule_id": "S3_APPOINTMENT_TIME",
                "planner_reply_messages": [{"type": "text", "content": {"text": "我帮您查明天档期。"}}],
                "planner_tool_calls": [{"name": "available_time", "store_id": "467", "date": "2026-06-24"}],
                "primary_task": {"type": "appointment", "subtype": "s3_appointment_time"},
            },
        )

        self.assertNotIn("appointment_store_anchor_required", {item["missing"] for item in violations})

    def test_case_effect_request_rejects_after_sales_handoff(self) -> None:
        violations = _planner_message_contract_violations(
            {"normalized_content": "想看做完效果"},
            {
                "planner_decision": "need_tools",
                "planner_stage": "S4",
                "planner_sub_rule_id": "S4_COMPLAINT_REFUND",
                "planner_reply_messages": [{"type": "text", "content": {"text": "我让专业同事核对。"}}],
                "planner_tool_calls": [{"name": "professional_assist", "purpose": "misrouted"}],
                "handoff": {"needed": True, "reason": "misrouted"},
                "primary_task": {"type": "after_sales", "subtype": "s4_complaint_refund"},
            },
        )

        self.assertIn("case_studies_required", {item["missing"] for item in violations})

    def test_case_effect_request_requires_case_studies_tool(self) -> None:
        violations = _planner_message_contract_violations(
            {"normalized_content": "想看做完效果"},
            {
                "planner_decision": "direct_reply",
                "planner_stage": "S1",
                "planner_sub_rule_id": "S1_CASE_REQUEST",
                "planner_reply_messages": [{"type": "text", "content": {"text": "可以。"}}],
                "planner_tool_calls": [],
                "handoff": {"needed": False, "reason": ""},
                "primary_task": {"type": "project_consult", "subtype": "case_request"},
            },
        )

        self.assertIn("case_studies_required", {item["missing"] for item in violations})


if __name__ == "__main__":
    unittest.main()
