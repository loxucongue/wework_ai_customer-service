from __future__ import annotations

import unittest

from app.graph.nodes.action_nodes import _normalize_distance_origin_from_store_regions


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


if __name__ == "__main__":
    unittest.main()
