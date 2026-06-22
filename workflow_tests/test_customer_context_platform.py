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


if __name__ == "__main__":
    unittest.main()
