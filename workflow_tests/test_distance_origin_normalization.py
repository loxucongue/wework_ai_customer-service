from __future__ import annotations

import unittest

from app.graph.nodes.action_nodes import _administrative_area_origin_candidate, _normalize_distance_origin_from_store_regions


class DistanceOriginNormalizationTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
