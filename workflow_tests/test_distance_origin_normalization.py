from __future__ import annotations

import unittest

from app.graph.nodes.action_nodes import (
    _administrative_area_origin_candidate,
    _distance_candidate_stores,
    _geocode_for_query_scope,
    _haversine_km,
    _geocode_has_unconflicted_location,
    _normalize_known_landmark_origin,
    _normalize_distance_origin_from_store_regions,
    _store_lookup_item,
    _stores_for_geocode,
    _stores_for_text_query,
)


class DistanceOriginNormalizationTests(unittest.TestCase):
    def test_haversine_distance_is_calculated_in_process(self) -> None:
        distance_km = _haversine_km(
            (106.540603, 29.402348),
            (106.545937, 29.392449),
        )

        self.assertGreater(distance_km, 1.0)
        self.assertLess(distance_km, 2.0)

    def test_city_and_district_short_name_use_customer_store_region(self) -> None:
        state = {
            "customer_store_knowledge": {
                "stores": [
                    {
                        "store_id": "528",
                        "store_name": "重庆江北店",
                        "province": "重庆市",
                        "city": "重庆市",
                        "district": "江北区",
                    },
                    {
                        "store_id": "189",
                        "store_name": "重庆巴南店",
                        "province": "重庆市",
                        "city": "重庆市",
                        "district": "巴南区",
                    },
                ]
            }
        }

        self.assertEqual(_normalize_distance_origin_from_store_regions("重庆江北附近", state), "重庆市江北区")

    def test_ambiguous_district_keeps_original_text(self) -> None:
        state = {
            "customer_store_knowledge": {
                "stores": [
                    {"province": "重庆市", "city": "重庆市", "district": "江北区"},
                    {"province": "江苏省", "city": "南京市", "district": "江北区"},
                ]
            }
        }

        self.assertEqual(_normalize_distance_origin_from_store_regions("江北附近", state), "江北附近")

    def test_city_area_without_scope_district_builds_admin_candidate(self) -> None:
        state = {
            "customer_store_knowledge": {
                "stores": [
                    {
                        "store_id": "189",
                        "store_name": "重庆巴南店",
                        "province": "重庆市",
                        "city": "重庆市",
                        "district": "巴南区",
                    }
                ]
            }
        }

        self.assertEqual(
            _administrative_area_origin_candidate("重庆江北附近哪家近", state),
            {"origin": "重庆市江北区", "area": "江北"},
        )

    def test_landmark_does_not_build_admin_area_candidate(self) -> None:
        state = {
            "customer_store_knowledge": {
                "stores": [
                    {
                        "store_id": "227",
                        "store_name": "厦门湖里店",
                        "province": "福建省",
                        "city": "厦门市",
                        "district": "湖里区",
                    }
                ]
            }
        }

        self.assertEqual(_administrative_area_origin_candidate("厦门机场附近哪家近", state), {})

    def test_xiamen_airport_origin_uses_known_landmark_alias(self) -> None:
        self.assertEqual(_normalize_known_landmark_origin("厦门机场"), "厦门高崎国际机场")
        self.assertEqual(_normalize_known_landmark_origin("厦门高崎国际机场"), "厦门高崎国际机场")

    def test_geocode_with_location_and_empty_district_is_unconflicted(self) -> None:
        self.assertTrue(_geocode_has_unconflicted_location({"location": "106.551787,29.562680", "district": ""}))
        self.assertFalse(_geocode_has_unconflicted_location({"location": "107.371860,29.739957", "district": "涪陵区"}))

    def test_store_lookup_city_candidates_feed_distance_source(self) -> None:
        stores = [
            {
                "store_id": "227",
                "store_name": "厦门湖里店",
                "province": "福建省",
                "city": "厦门市",
                "district": "湖里区",
                "store_address": "厦门市湖里区",
            },
            {
                "store_id": "386",
                "store_name": "厦门思明店",
                "province": "福建省",
                "city": "厦门市",
                "district": "思明区",
                "store_address": "厦门市思明区",
            },
            {
                "store_id": "467",
                "store_name": "重庆渝中店",
                "province": "重庆市",
                "city": "重庆市",
                "district": "渝中区",
                "store_address": "重庆市渝中区",
            },
        ]
        lookup_candidates = [_store_lookup_item(store) for store in _stores_for_text_query("厦门机场", stores, "nearby_candidates")]
        candidates = _distance_candidate_stores(
            {"name": "distance_calculate", "candidate_source": "customer_store_lookup"},
            {"customer_store_knowledge": {"stores": stores}},
            {"customer_store_lookup": {"candidate_stores": lookup_candidates}},
        )

        self.assertEqual([item["store_id"] for item in candidates], ["227", "386"])

    def test_city_query_does_not_inherit_default_geocode_district(self) -> None:
        scoped = _geocode_for_query_scope(
            "\u8346\u5dde\u5e02\u6709\u95e8\u5e97\u5417",
            {
                "province": "\u6e56\u5317\u7701",
                "city": "\u8346\u5dde\u5e02",
                "district": "\u8346\u5dde\u533a",
                "location": "112.190000,30.350000",
            },
        )

        self.assertEqual(scoped.get("city"), "\u8346\u5dde\u5e02")
        self.assertNotIn("district", scoped)

    def test_district_query_keeps_explicit_geocode_district(self) -> None:
        scoped = _geocode_for_query_scope(
            "\u756a\u79ba\u533a",
            {
                "province": "\u5e7f\u4e1c\u7701",
                "city": "\u5e7f\u5dde\u5e02",
                "district": "\u756a\u79ba\u533a",
                "location": "113.380000,22.940000",
            },
        )

        self.assertEqual(scoped.get("district"), "\u756a\u79ba\u533a")

    def test_nearby_geocode_prefers_district_matches_before_city_scope(self) -> None:
        stores = [
            {
                "store_id": "528",
                "store_name": "\u91cd\u5e86\u4e07\u5dde\u4e8c\u5e97",
                "province": "\u91cd\u5e86\u5e02",
                "city": "\u91cd\u5e86\u5e02",
                "district": "\u4e07\u5dde\u533a",
            },
            {
                "store_id": "467",
                "store_name": "\u91cd\u5e86\u767e\u661f\u6e1d\u4e2d\u5e97",
                "province": "\u91cd\u5e86\u5e02",
                "city": "\u91cd\u5e86\u5e02",
                "district": "\u6e1d\u4e2d\u533a",
            },
            {
                "store_id": "205",
                "store_name": "\u91cd\u5e86\u4e5d\u9f99\u5761\u5e97",
                "province": "\u91cd\u5e86\u5e02",
                "city": "\u91cd\u5e86\u5e02",
                "district": "\u4e5d\u9f99\u5761\u533a",
            },
        ]

        matches = _stores_for_geocode(
            {
                "province": "\u91cd\u5e86\u5e02",
                "city": "\u91cd\u5e86\u5e02",
                "district": "\u6e1d\u4e2d\u533a",
            },
            stores,
            "nearby_candidates",
        )

        self.assertEqual([item["store_id"] for item in matches], ["467"])


if __name__ == "__main__":
    unittest.main()
