from __future__ import annotations

import unittest

from app.config import Settings
from app.services.store_snapshot_service import StoreSnapshotService, parse_geocode_workflow_response, parse_region


def u(value: str) -> str:
    return value.encode("ascii").decode("unicode_escape")


class StoreSnapshotGeocodeTests(unittest.TestCase):
    def test_parse_geocode_data_list_response(self) -> None:
        raw = {
            "code": 0,
            "data": [
                {
                    "district": u(r"\u91d1\u57ce\u6c5f\u533a"),
                    "formatted_address": u(r"\u5e7f\u897f\u58ee\u65cf\u81ea\u6cbb\u533a\u6cb3\u6c60\u5e02\u91d1\u57ce\u6c5f\u533a\u91d1\u57ce\u4e2d\u8def437\u53f7"),
                    "city": u(r"\u6cb3\u6c60\u5e02"),
                    "location": "108.053148,24.695629",
                    "province": u(r"\u5e7f\u897f\u58ee\u65cf\u81ea\u6cbb\u533a"),
                }
            ],
        }

        parsed = parse_geocode_workflow_response(raw)

        self.assertEqual(parsed["province"], u(r"\u5e7f\u897f\u58ee\u65cf\u81ea\u6cbb\u533a"))
        self.assertEqual(parsed["city"], u(r"\u6cb3\u6c60\u5e02"))
        self.assertEqual(parsed["district"], u(r"\u91d1\u57ce\u6c5f\u533a"))
        self.assertEqual(parsed["location"], "108.053148,24.695629")

    def test_store_from_row_uses_geocode_region(self) -> None:
        service = StoreSnapshotService(Settings(geocode_workflow_id=""), platform_client=None)
        service._geocode_store_address = lambda address: {  # type: ignore[method-assign]
            "province": u(r"\u5e7f\u897f\u58ee\u65cf\u81ea\u6cbb\u533a"),
            "city": u(r"\u6cb3\u6c60\u5e02"),
            "district": u(r"\u91d1\u57ce\u6c5f\u533a"),
            "formatted_address": u(r"\u5e7f\u897f\u58ee\u65cf\u81ea\u6cbb\u533a\u6cb3\u6c60\u5e02\u91d1\u57ce\u6c5f\u533a\u91d1\u57ce\u4e2d\u8def437\u53f7"),
            "location": "108.053148,24.695629",
            "level": u(r"\u95e8\u5740"),
        }

        store = service._store_from_row(
            {"id": "1", "name": "HC Store", "address": u(r"\u91d1\u57ce\u4e2d\u8def437\u53f7")},
            detail={},
            detail_source="test",
        )

        self.assertEqual(store["province"], u(r"\u5e7f\u897f\u58ee\u65cf\u81ea\u6cbb\u533a"))
        self.assertEqual(store["city"], u(r"\u6cb3\u6c60\u5e02"))
        self.assertEqual(store["district"], u(r"\u91d1\u57ce\u6c5f\u533a"))
        self.assertEqual(store["location"], "108.053148,24.695629")
        self.assertEqual(store["geocode_source"], "poi_to_geocode")

    def test_parse_region_uses_township_when_district_missing(self) -> None:
        province, city, district = parse_region(
            u(r"\u5e7f\u4e1c\u7701\u4e1c\u839e\u5e02\u5357\u57ce\u8857\u9053UCC\u5bf0\u5b87\u6c47\u91d1\u4e2d\u5fc38\u53f7\u697c")
        )

        self.assertEqual(province, u(r"\u5e7f\u4e1c\u7701"))
        self.assertEqual(city, u(r"\u4e1c\u839e\u5e02"))
        self.assertEqual(district, u(r"\u5357\u57ce\u8857\u9053"))

    def test_parse_region_accepts_one_char_district(self) -> None:
        province, city, district = parse_region(
            u(r"\u4e2d\u5c71\u5e02\u4e1c\u533a\u4e2d\u5c71\u4e09\u8def16\u53f7\u4e4b\u4e8c\u5229\u548c\u5546\u4e1a\u4e2d\u5fc3")
        )

        self.assertEqual(province, "")
        self.assertEqual(city, u(r"\u4e2d\u5c71\u5e02"))
        self.assertEqual(district, u(r"\u4e1c\u533a"))


if __name__ == "__main__":
    unittest.main()
