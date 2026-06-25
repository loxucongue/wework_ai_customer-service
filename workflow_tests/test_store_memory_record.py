from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.chat_runtime import _record_visible_store_facts
from app.config import Settings
from app.services.memory_store import CustomerMemoryStore


class StoreMemoryRecordTests(unittest.TestCase):
    def test_store_address_message_records_preferred_store_and_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            memory_store = CustomerMemoryStore(Settings(memory_dir=Path(directory)))
            state = {
                "request_id": "req-store-card",
                "customer_store_knowledge": {
                    "stores": [
                        {
                            "store_id": "221",
                            "store_name": "南昌高新店",
                            "province": "江西省",
                            "city": "南昌市",
                            "district": "青山湖区",
                            "store_address": "江西省南昌市青山湖区解放东路2226号",
                            "business_hours": "09:00-19:00",
                            "parking_name": "门店附近停车场",
                            "map_url": "https://example.test/map/221",
                        }
                    ]
                },
                "trace": [],
            }

            _record_visible_store_facts(
                memory_store,
                state,
                customer_id="customer-1",
                reply_messages=[{"type": "store_address", "content": {"store_id": "221"}}],
            )

            memory = memory_store.load("customer-1")
            self.assertEqual(memory["basic_info"]["city"], "南昌市")
            self.assertEqual(memory["basic_info"]["preferred_store_id"], "221")
            self.assertEqual(memory["basic_info"]["preferred_store_name"], "南昌高新店")
            self.assertEqual(memory["history_events"][-1]["event_type"], "store_address_sent")
            self.assertEqual(memory["history_events"][-1]["facts"]["store_id"], "221")
            self.assertEqual(state["store_fact_memory_record"]["status"], "recorded")

    def test_single_lookup_candidate_records_store_match(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            memory_store = CustomerMemoryStore(Settings(memory_dir=Path(directory)))
            state = {
                "request_id": "req-store-match",
                "tool_results": {
                    "customer_store_lookup": {
                        "status": "ok",
                        "candidate_stores": [
                            {
                                "store_id": "221",
                                "store_name": "南昌高新店",
                                "city": "南昌市",
                                "district": "青山湖区",
                                "store_address": "江西省南昌市青山湖区解放东路2226号",
                            }
                        ],
                    }
                },
                "trace": [],
            }

            _record_visible_store_facts(
                memory_store,
                state,
                customer_id="customer-2",
                reply_messages=[{"type": "text", "content": {"text": "这家比较方便。"}}],
            )

            memory = memory_store.load("customer-2")
            self.assertEqual(memory["basic_info"]["preferred_store_id"], "221")
            self.assertEqual(memory["history_events"][-1]["event_type"], "store_matched")

    def test_empty_memory_update_does_not_create_memory_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            memory_store = CustomerMemoryStore(Settings(memory_dir=Path(directory)))

            memory = memory_store.save_update("debug-customer", profile_update={}, event_updates=[])

            self.assertEqual(memory["customer_id"], "debug-customer")
            self.assertFalse((Path(directory) / "debug-customer.json").exists())


if __name__ == "__main__":
    unittest.main()
