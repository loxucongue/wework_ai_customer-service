from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = ROOT / "ai_paths"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.services.customer_context import CustomerContextService  # noqa: E402


class CustomerContextPlatformTests(unittest.TestCase):
    def test_preserves_customer_info_when_order_lookup_times_out(self) -> None:
        service = CustomerContextService(platform_client=_TimeoutOrdersPlatformClient())  # type: ignore[arg-type]

        context = service.load(
            customer_id="20612106",
            memory={},
            request_context={
                "user_id": 7294,
                "corp_id": "ww943af61cd5d2afe4",
                "wechat": "CS001",
                "external_userid": "external_1",
            },
        )

        self.assertEqual(context["source"], "platform_agent")
        self.assertEqual(context["platform_customer_id"], "20612106")
        self.assertEqual(context["customer_add_wechat_id"], "19530960")
        self.assertIn("orders_error", context)
        self.assertEqual(context["orders"], [])

    def test_queries_orders_on_every_load_and_marks_saved_current_order(self) -> None:
        client = _CountingOrdersPlatformClient()
        service = CustomerContextService(platform_client=client)  # type: ignore[arg-type]
        request_context = {
            "user_id": 7294,
            "corp_id": "ww943af61cd5d2afe4",
            "wechat": "CS001",
            "external_userid": "external_1",
        }
        memory = {
            "basic_info": {
                "confirmed_store_id": "386",
                "order_state": {"order_id": "order-current", "store_id": "386", "category_id": "10"},
            }
        }

        first = service.load(customer_id="20612106", memory=memory, request_context=request_context)
        second = service.load(customer_id="20612106", memory=memory, request_context=request_context)

        self.assertEqual(client.order_calls, 2)
        self.assertFalse(first["cache"]["orders_hit"])
        self.assertFalse(second["cache"]["orders_hit"])
        self.assertEqual(first["orders"][0]["id"], "order-current")
        self.assertTrue(first["orders"][0]["is_current_order"])


class _TimeoutOrdersPlatformClient:
    available = True

    def get_customer_info(self, **_: object) -> dict[str, object]:
        return {
            "id": 20612106,
            "customer_add_wechat_id": 19530960,
            "kind": 1,
            "name": "测试客户",
        }

    def list_orders(self, **_: object) -> list[dict[str, object]]:
        raise TimeoutError("order lookup timed out")


class _CountingOrdersPlatformClient(_TimeoutOrdersPlatformClient):
    def __init__(self) -> None:
        self.order_calls = 0

    def list_orders(self, **_: object) -> list[dict[str, object]]:
        self.order_calls += 1
        return [
            {
                "id": "order-other",
                "status": 1,
                "store_id": 369,
                "category_id": 10,
                "prepay_required": "10.00",
                "prepay_paid": "10.00",
            },
            {
                "id": "order-current",
                "status": 1,
                "store_id": 386,
                "category_id": 10,
                "prepay_required": "10.00",
                "prepay_paid": "0.00",
            },
        ]


if __name__ == "__main__":
    unittest.main()
