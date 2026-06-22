from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = ROOT / "ai_paths"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.graph.planner.tool_policy import enforce_required_tools  # noqa: E402
from app.graph.nodes.store_context import current_real_store_from_state  # noqa: E402
from app.graph.nodes.reply_nodes import _ensure_book_order_for_created_appointment  # noqa: E402
from app.services.appointment_opening_service import AppointmentOpeningService  # noqa: E402


class AppointmentContactFlowTests(unittest.TestCase):
    def test_plain_assistant_store_id_history_is_current_store(self) -> None:
        store = current_real_store_from_state({"conversation_history": ["助手: 13"]})

        self.assertEqual(store["id"], "13")

    def test_contact_info_followup_only_requires_appointment_create(self) -> None:
        state = {
            "normalized_content": "罗学聪 19976988097",
            "customer_basic_info": {
                "preferred_store_id": "13",
                "preferred_store_name": "南京秦淮轻医美店",
            },
            "history_events": [
                {
                    "event_type": "deposit_explained",
                    "summary": "已向客户解释10元预约金。",
                    "facts": {"text": "您把姓名电话发我，我帮您登记。"},
                }
            ],
        }
        tasks = [
            {
                "type": "appointment",
                "subtype": "contact_info_supplied",
                "sop_stage": "S3_PRICE_CLOSE",
                "policy_hint": "SF9_APPOINTMENT_CONTACT_INFO",
            }
        ]

        tools = enforce_required_tools(
            state,
            tasks,
            [{"name": "appointment_create", "purpose": "create appointment deposit order"}],
        )

        names = [tool["name"] for tool in tools]
        self.assertIn("appointment_create", names)
        self.assertNotIn("store_lookup", names)
        self.assertNotIn("available_time", names)

    def test_name_phone_after_booking_context_can_create_dry_run_order(self) -> None:
        service = AppointmentOpeningService(platform_client=_FakePlatformClient())  # type: ignore[arg-type]
        state = {
            "normalized_content": "罗学聪 19976988097",
            "request_context": {
                "customer_id": "20615704",
                "customer_add_wechat_id": "20615704",
                "user_id": "7294",
                "appointment_opening_dry_run": True,
            },
            "customer_context": {
                "customer": {"id": "20615704", "customer_add_wechat_id": "20615704"},
                "request_context": {},
            },
            "history_events": [
                {
                    "event_type": "deposit_explained",
                    "summary": "已向客户解释10元预约金。",
                    "facts": {"text": "您把姓名电话发我，我帮您登记。"},
                }
            ],
        }
        appointment_query = {
            "store_id": "13",
            "store_name": "南京秦淮轻医美店",
            "date": "",
            "time": "",
        }

        result = service.maybe_open(
            content="罗学聪 19976988097",
            state=state,
            appointment_query=appointment_query,
            available_time={},
        )

        self.assertEqual(result["status"], "dry_run_created")
        self.assertEqual(result["order_id"], "dry_run_order")
        self.assertEqual(result["facts"]["customer_name"], "罗学聪")
        self.assertEqual(result["facts"]["customer_phone"], "19976988097")

    def test_created_appointment_adds_book_order_message(self) -> None:
        state = {
            "tool_results": {
                "appointment_opening": {
                    "status": "dry_run_created",
                    "order_id": "dry_run_order",
                }
            }
        }
        messages = [{"type": "text", "order": 1, "content": "收到，我帮您登记好了。"}]

        output = _ensure_book_order_for_created_appointment(state, messages)

        self.assertEqual(output[-1]["type"], "book_order")
        self.assertEqual(output[-1]["content"]["order_id"], "dry_run_order")
        self.assertTrue(state["postprocess_changed"])


class _FakePlatformClient:
    available = True

    def category_prepay(self, **_: object) -> dict[str, object]:
        return {"prepay": []}


if __name__ == "__main__":
    unittest.main()
