from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = ROOT / "ai_paths"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.graph.nodes.action_module_outputs import build_planner_fact_output  # noqa: E402
from app.graph.nodes.action_nodes import (  # noqa: E402
    _distance_origin_from_geocode,
    _parse_location_geocode_output,
    _should_geocode_store_location,
    _store_query_with_geocoded_location,
    _store_query_with_planned_location,
)
from app.graph.nodes.appointment_utils import appointment_query_from_state  # noqa: E402
from app.graph.nodes.reply_context import reply_user_payload_for_model  # noqa: E402
from app.graph.nodes.reply_nodes import _safe_visible_fallback_messages, _store_no_match_reply_needs_fallback  # noqa: E402
from app.graph.nodes.store_context import extract_city, store_query_from_state  # noqa: E402
from app.services.store_query_info import build_store_query_info  # noqa: E402
from app.services.store_service import StoreService  # noqa: E402


class FakePlatformClient:
    available = True

    def __init__(self) -> None:
        self.rows = [
            {
                "id": "12",
                "name": "厦门思明店",
                "city": "厦门",
                "address": "厦门市思明区厦禾路1222号国骏大厦",
                "tencent_address": "厦门市思明区厦禾路1222号国骏大厦",
                "status": 1,
                "shore_show": 1,
                "is_pause": 2,
            },
            {
                "id": "386",
                "name": "厦门百星",
                "city": "厦门",
                "address": "厦门市湖里区枋湖西路189号",
                "tencent_address": "厦门市湖里区枋湖西路189号",
                "status": 1,
                "shore_show": 1,
                "is_pause": 2,
            },
        ]

    def list_stores(self, **_: object) -> list[dict[str, object]]:
        return [self.rows[0]]

    def list_store_options(self, **_: object) -> list[dict[str, object]]:
        return self.rows

    def store_info(self, store_id: str, **_: object) -> dict[str, object]:
        for row in self.rows:
            if row["id"] == store_id:
                return {
                    "name": row["name"],
                    "tencent_address": row["tencent_address"],
                    "tencent_map_store": "https://map.example/store",
                    "parking_info": {},
                }
        return {}


def _customer_context() -> dict[str, object]:
    return {
        "platform_customer_id": "customer_1",
        "customer_add_wechat_id": "wechat_1",
        "request_context": {"user_id": "1", "corp_id": "corp", "wechat": "wx"},
    }


def _store_service() -> StoreService:
    return StoreService(platform_client=FakePlatformClient())  # type: ignore[arg-type]


class StoreDistanceRecommendationTests(unittest.TestCase):
    def test_airport_preference_does_not_create_hardcoded_recommendation(self) -> None:
        result = _store_service().search("厦门机场附近有门店吗", customer_context=_customer_context())

        self.assertTrue(result["stores"])
        self.assertTrue(result["distance_lookup_required"])
        self.assertEqual(result["distance_origin"], "厦门机场")
        self.assertNotIn("recommended_store", result)
        self.assertNotIn("recommendation_reason", result)

    def test_fact_output_exposes_distance_lookup_requirement(self) -> None:
        store_lookup = _store_service().search("厦门机场附近有门店吗", customer_context=_customer_context())
        fact_output = build_planner_fact_output({"store_lookup": store_lookup}, {})
        structured = fact_output["structured_facts"]

        self.assertTrue(structured["store_lookup_status"]["distance_lookup_required"])
        self.assertEqual(structured["store_lookup_status"]["distance_origin"], "厦门机场")
        self.assertEqual(structured["recommended_store"], {})
        self.assertTrue(
            any("distance_lookup_required=厦门机场" in fact for fact in fact_output["facts"]),
            fact_output["facts"],
        )

    def test_appointment_does_not_use_first_candidate_when_distance_is_required(self) -> None:
        store_lookup = _store_service().search("厦门机场附近下午想去", customer_context=_customer_context())
        query = appointment_query_from_state("厦门机场附近下午想去", store_lookup, {}, extract_city)

        self.assertIn("store_id", query["missing"])
        self.assertEqual(query["store_id"], "")
        self.assertEqual(query["store_name"], "")

    def test_store_followup_inherits_city_and_area_from_customer_basic_info(self) -> None:
        query = store_query_from_state(
            "店在哪里呢",
            {
                "customer_basic_info": {
                    "city": "福州",
                    "area_or_landmark": "仓山区",
                },
                "conversation_history": [],
            },
        )

        self.assertIn("福州", query)
        self.assertIn("仓山区", query)
        self.assertIn("店在哪里呢", query)

    def test_store_query_uses_cleaned_planner_distance_origin_when_base_lacks_location(self) -> None:
        query = _store_query_with_planned_location(
            "店在哪里呢",
            planned_query="店在哪里呢",
            planned_distance_origin="福州用户仓山区",
        )

        self.assertEqual(query, "福州仓山区 店在哪里呢")

    def test_chongqing_banan_and_xiamen_haicang_are_area_landmarks(self) -> None:
        banan = build_store_query_info("我在重庆巴南这边")
        haicang = build_store_query_info("厦门 海沧")

        self.assertEqual(banan.city, "重庆")
        self.assertEqual(banan.area_or_landmark, "巴南")
        self.assertEqual(banan.location_granularity, "area_or_landmark")
        self.assertEqual(haicang.city, "厦门")
        self.assertEqual(haicang.area_or_landmark, "海沧")
        self.assertEqual(haicang.location_granularity, "area_or_landmark")

    def test_short_area_fragment_inherits_known_city_without_area_map(self) -> None:
        query = store_query_from_state(
            "海沧",
            {
                "customer_basic_info": {"city": "厦门"},
                "conversation_history": ["小贝: 您在厦门哪个区或附近地标"],
            },
        )

        self.assertEqual(query, "厦门 海沧")

    def test_current_no_match_store_lookup_blocks_historical_store_claim(self) -> None:
        state = {
            "appointment_cache": {"store_name": "厦门湖里百星店"},
            "structured_facts": {
                "store_lookup_status": {
                    "source": "platform_agent.store_index_no_match",
                    "data_authority": "platform",
                    "has_store_facts": False,
                    "missing": [],
                },
                "store_facts": [],
            },
        }

        messages, source = _safe_visible_fallback_messages(state)
        self.assertEqual(source, "deterministic_store_fallback")
        self.assertIn("没查到", messages[0]["content"]["text"])

    def test_area_without_direct_store_is_marked_and_blocks_direct_area_claim(self) -> None:
        result = _store_service().search("厦门 海沧", customer_context=_customer_context())
        fact_output = build_planner_fact_output({"store_lookup": result}, {})
        status = fact_output["structured_facts"]["store_lookup_status"]
        state = {
            "structured_facts": {
                **fact_output["structured_facts"],
                "store_lookup_status": status,
            }
        }

        self.assertEqual(result["city"], "厦门")
        self.assertEqual(result["area_or_landmark"], "海沧")
        self.assertTrue(result["stores"])
        self.assertEqual({store["name"] for store in result["stores"]}, {"厦门思明店", "厦门百星"})
        self.assertEqual(result["candidate_source"], "platform_agent.store_option")
        self.assertFalse(result["area_or_landmark_has_direct_store"])
        self.assertTrue(result["area_or_landmark_direct_store_missing"])

    def test_lng_lat_distance_origin_is_not_prefixed_with_city(self) -> None:
        result = _store_service().search(
            "厦门 海沧",
            customer_context=_customer_context(),
            planner_distance_origin="118.032883,24.484688",
        )

        self.assertEqual(result["planned_distance_origin"], "118.032883,24.484688")
        self.assertEqual(result["distance_origin"], "118.032883,24.484688")

    def test_location_geocode_output_normalizes_query_and_distance_origin(self) -> None:
        raw = {
            "data": {
                "output": [
                    {
                        "district": "湖里区",
                        "formatted_address": "福建省厦门市湖里区萤火虫大厦",
                        "number": "",
                        "street": "",
                        "city": "厦门市",
                        "country": "中国",
                        "level": "兴趣点",
                        "location": "118.152560,24.535127",
                        "province": "福建省",
                        "township": "",
                    }
                ]
            }
        }
        results = _parse_location_geocode_output(raw)
        geocode = {"best": results[0]}

        query = _store_query_with_geocoded_location("萤火虫大厦附近", geocode)
        origin = _distance_origin_from_geocode(geocode)

        self.assertEqual(results[0]["lng"], "118.152560")
        self.assertEqual(results[0]["lat"], "24.535127")
        self.assertIn("厦门", query)
        self.assertIn("湖里区", query)
        self.assertIn("福建省厦门市湖里区萤火虫大厦", query)
        self.assertEqual(origin, "118.152560,24.535127")

    def test_location_geocode_skips_generic_and_named_store_queries(self) -> None:
        self.assertFalse(_should_geocode_store_location("附近有门店吗", raw_query="附近有门店吗"))
        self.assertFalse(_should_geocode_store_location("重庆有店吗", raw_query="重庆有店吗"))
        self.assertFalse(_should_geocode_store_location("厦门湖里百星店地址发我", raw_query="厦门湖里百星店地址发我"))
        self.assertTrue(_should_geocode_store_location("福建省厦门市湖里区萤火虫大厦附近有店吗"))

    def test_area_appointment_does_not_use_historical_store_when_distance_is_unresolved(self) -> None:
        store_lookup = _store_service().search("厦门 海沧 明天下午可以约吗", customer_context=_customer_context())
        state = {
            "appointment_cache": {
                "store_id": "91",
                "store_name": "上海静安店",
            }
        }
        query = appointment_query_from_state("厦门海沧明天下午可以约吗", store_lookup, state, extract_city)

        self.assertIn("store_id", query["missing"])
        self.assertEqual(query["store_id"], "")
        self.assertEqual(query["store_name"], "")

    def test_appointment_uses_current_distance_recommendation_instead_of_historical_store(self) -> None:
        store_lookup = _store_service().search("厦门 海沧 明天下午可以约吗", customer_context=_customer_context())
        store_lookup["recommended_store"] = {
            "id": "12",
            "name": "厦门思明店",
            "address": "厦门市思明区厦禾路1222号国骏大厦",
        }
        state = {
            "appointment_cache": {
                "store_id": "91",
                "store_name": "上海静安店",
            }
        }
        query = appointment_query_from_state("厦门海沧明天下午可以约吗", store_lookup, state, extract_city)

        self.assertEqual(query["store_id"], "12")
        self.assertEqual(query["store_name"], "厦门思明店")
        self.assertNotIn("store_id", query["missing"])

    def test_store_no_match_fallback_returns_visible_no_store_text(self) -> None:
        messages, source = _safe_visible_fallback_messages(
            {
                "normalized_content": "我在新疆",
                "fact_envelope": {
                    "structured_facts": {
                        "store_lookup_status": {
                            "city": "新疆",
                            "source": "platform_agent.store_index_no_match",
                            "data_authority": "platform",
                            "has_store_facts": False,
                            "no_store_match_confirmed": True,
                            "missing": [],
                        },
                        "store_facts": [],
                        "recommended_store": {},
                    }
                },
            }
        )

        self.assertEqual(source, "deterministic_store_fallback")
        self.assertEqual(messages[0]["type"], "text")
        self.assertIn("目前没查到可直接发您的门店", messages[0]["content"]["text"])
        self.assertIn("其他常去地点", messages[0]["content"]["text"])
        self.assertIn("新疆", messages[0]["content"]["text"])

    def test_store_no_match_requires_explicit_no_store_text(self) -> None:
        state = {
            "normalized_content": "我在新疆",
            "fact_envelope": {
                "structured_facts": {
                    "store_lookup_status": {
                        "city": "新疆",
                        "source": "platform_agent.store_index_no_match",
                        "data_authority": "platform",
                        "has_store_facts": False,
                        "no_store_match_confirmed": True,
                        "missing": [],
                    },
                    "store_facts": [],
                    "recommended_store": {},
                }
            },
        }

        self.assertTrue(
            _store_no_match_reply_needs_fallback(
                state,
                [{"type": "text", "order": 1, "content": {"text": "比如乌鲁木齐、喀什或其他区域，您发个大概地标就行"}}],
            )
        )
        self.assertFalse(
            _store_no_match_reply_needs_fallback(
                state,
                [{"type": "text", "order": 1, "content": {"text": "新疆这边目前没查到可直接发您的门店。您有其他常去地点在哪个城市或哪个区？"}}],
            )
        )

    def test_sales_talk_scripts_are_style_only_for_reply_payload(self) -> None:
        payload = reply_user_payload_for_model(
            {
                "fact_envelope": {
                    "structured_facts": {
                        "sales_talk_scripts": [
                            {
                                "matched_question": "我在上海",
                                "business_logic": "城市有店",
                                "sales_script": "可以的 您在哪个区 我给您发最近的门店位置",
                            }
                        ]
                    }
                }
            }
        )

        scripts = payload["fact_envelope"]["structured_facts"]["sales_talk_scripts"]
        self.assertEqual(scripts[0]["source"], "sales_talk_qa")
        self.assertTrue(scripts[0]["style_only"])
        self.assertNotIn("sales_script", scripts[0])
        self.assertNotIn("可以的", str(scripts))


if __name__ == "__main__":
    unittest.main()
