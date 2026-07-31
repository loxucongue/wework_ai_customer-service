from __future__ import annotations

import unittest

from app.config import Settings
from app.services.store_snapshot_service import (
    StoreSnapshotService,
    _store_option_is_recommendable,
    geocode_query_candidates,
    geocode_region_conflicts,
    parse_geocode_workflow_response,
    parse_region,
)


def u(value: str) -> str:
    return value.encode("ascii").decode("unicode_escape")


class StoreSnapshotGeocodeTests(unittest.TestCase):
    def test_platform_inactive_or_hidden_store_is_not_recommendable(self) -> None:
        self.assertFalse(_store_option_is_recommendable({"status": 0, "shore_show": 1}))
        self.assertFalse(_store_option_is_recommendable({"status": 1, "shore_show": 2}))
        self.assertTrue(_store_option_is_recommendable({"status": 1, "shore_show": 1}))
        self.assertTrue(_store_option_is_recommendable({"id": "fixture"}))

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

    def test_parse_geocode_list_never_skips_the_first_item(self) -> None:
        raw = {
            "code": 0,
            "data": [
                {},
                {
                    "province": u(r"\u5e7f\u4e1c\u7701"),
                    "city": u(r"\u5e7f\u5dde\u5e02"),
                    "location": "113.1,23.1",
                },
            ],
        }

        self.assertEqual(parse_geocode_workflow_response(raw), {})

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

    def test_incomplete_address_uses_parking_region_before_raw_poi(self) -> None:
        service = StoreSnapshotService(Settings(geocode_workflow_id=""), platform_client=None)
        calls: list[str] = []

        def geocode(address: str) -> dict[str, str]:
            calls.append(address)
            if address.startswith(u(r"\u5e7f\u4e1c\u7701\u5e7f\u5dde\u5e02")):
                return {
                    "province": u(r"\u5e7f\u4e1c\u7701"),
                    "city": u(r"\u5e7f\u5dde\u5e02"),
                    "district": u(r"\u756a\u79ba\u533a"),
                    "formatted_address": u(r"\u5e7f\u4e1c\u7701\u5e7f\u5dde\u5e02\u756a\u79ba\u533a\u756a\u79ba\u4e07\u8fbe\u5e7f\u573aB4\u680b"),
                    "location": "113.350056,23.006945",
                }
            return {
                "province": u(r"\u56db\u5ddd\u7701"),
                "city": u(r"\u5357\u5145\u5e02"),
                "district": u(r"\u5357\u90e8\u53bf"),
            }

        service._geocode_store_address = geocode  # type: ignore[method-assign]
        store = service._store_from_row(
            {
                "id": "129",
                "name": u(r"\u5e7f\u5dde\u756a\u79ba\u5e97"),
                "status": 1,
                "shore_show": 1,
            },
            detail={
                "tencent_address": u(r"\u756a\u79ba\u4e07\u8fbe\u5e7f\u573aB4\u680b"),
                "parking_info": {
                    "park_address": u(
                        r"\u5e7f\u4e1c\u7701\u5e7f\u5dde\u5e02\u756a\u79ba\u533a\u5174\u5357\u5927\u9053368\u53f7"
                    )
                },
            },
            detail_source="test",
        )

        self.assertTrue(calls[0].startswith(u(r"\u5e7f\u4e1c\u7701\u5e7f\u5dde\u5e02")))
        self.assertEqual(store["province"], u(r"\u5e7f\u4e1c\u7701"))
        self.assertEqual(store["city"], u(r"\u5e7f\u5dde\u5e02"))
        self.assertEqual(store["district"], u(r"\u756a\u79ba\u533a"))
        self.assertEqual(store["location"], "113.350056,23.006945")

    def test_store_name_disambiguates_address_without_region(self) -> None:
        candidates = geocode_query_candidates(
            store_name=u(r"\u6210\u90fd\u9752\u7f8a\u5e97"),
            address=u(r"\u4e8c\u73af\u8def\u897f\u4e00\u6bb5155\u53f7\u5929\u7965\u5e7f\u573a4\u680b"),
            parking_address="",
        )

        self.assertEqual(
            candidates[0],
            u(r"\u6210\u90fd\u9752\u7f8a\u5e97 \u4e8c\u73af\u8def\u897f\u4e00\u6bb5155\u53f7\u5929\u7965\u5e7f\u573a4\u680b"),
        )

    def test_geocode_conflicting_with_explicit_city_is_rejected(self) -> None:
        conflicts = geocode_region_conflicts(
            {
                "province": u(r"\u5b81\u590f\u56de\u65cf\u81ea\u6cbb\u533a"),
                "city": u(r"\u94f6\u5ddd\u5e02"),
                "district": u(r"\u91d1\u51e4\u533a"),
            },
            address_region=(
                "",
                u(r"\u592a\u539f\u5e02"),
                u(r"\u674f\u82b1\u5cad\u533a"),
            ),
            parking_region=(
                u(r"\u5c71\u897f\u7701"),
                u(r"\u592a\u539f\u5e02"),
                u(r"\u674f\u82b1\u5cad\u533a"),
            ),
        )

        self.assertIn(
            u(r"\u0070\u0061\u0072\u006b\u0069\u006e\u0067\u005f\u0063\u0069\u0074\u0079\u003a\u592a\u539f\u5e02\u0021\u003d\u94f6\u5ddd\u5e02"),
            conflicts,
        )

    def test_complete_parking_region_resolves_county_level_city_shorthand(self) -> None:
        conflicts = geocode_region_conflicts(
            {
                "province": u(r"\u798f\u5efa\u7701"),
                "city": u(r"\u6cc9\u5dde\u5e02"),
                "district": u(r"\u664b\u6c5f\u5e02"),
            },
            address_region=(
                "",
                u(r"\u664b\u6c5f\u5e02"),
                u(r"\u6885\u5cad\u8857\u9053"),
            ),
            parking_region=(
                u(r"\u798f\u5efa\u7701"),
                u(r"\u6cc9\u5dde\u5e02"),
                u(r"\u664b\u6c5f\u5e02"),
            ),
        )

        self.assertEqual(conflicts, [])

    def test_complete_parking_region_resolves_composite_city_shorthand(self) -> None:
        conflicts = geocode_region_conflicts(
            {
                "province": u(r"\u56db\u5ddd\u7701"),
                "city": u(r"\u6210\u90fd\u5e02"),
                "district": u(r"\u7b80\u9633\u5e02"),
            },
            address_region=(
                u(r"\u56db\u5ddd\u7701"),
                u(r"\u6210\u90fd\u7b80\u9633\u5e02"),
                u(r"\u77f3\u6865\u9547"),
            ),
            parking_region=(
                u(r"\u56db\u5ddd\u7701"),
                u(r"\u6210\u90fd\u5e02"),
                u(r"\u7b80\u9633\u5e02"),
            ),
        )

        self.assertEqual(conflicts, [])

    def test_same_city_functional_district_alias_is_accepted(self) -> None:
        conflicts = geocode_region_conflicts(
            {
                "province": u(r"\u91cd\u5e86\u5e02"),
                "city": u(r"\u91cd\u5e86\u5e02"),
                "district": u(r"\u4e24\u6c5f\u65b0\u533a"),
            },
            address_region=(
                u(r"\u91cd\u5e86\u5e02"),
                u(r"\u91cd\u5e86\u5e02"),
                u(r"\u5317\u90e8\u65b0\u533a"),
            ),
            parking_region=(
                u(r"\u91cd\u5e86\u5e02"),
                u(r"\u91cd\u5e86\u5e02"),
                u(r"\u6e1d\u5317\u533a"),
            ),
        )

        self.assertEqual(conflicts, [])

    def test_same_city_match_outweighs_malformed_province_text(self) -> None:
        conflicts = geocode_region_conflicts(
            {
                "province": u(r"\u5e7f\u4e1c\u7701"),
                "city": u(r"\u5e7f\u5dde\u5e02"),
                "district": u(r"\u82b1\u90fd\u533a"),
            },
            address_region=(
                u(r"\u5e7f\u5dde\u7701"),
                u(r"\u5e7f\u5dde\u5e02"),
                u(r"\u82b1\u90fd\u533a"),
            ),
            parking_region=(
                u(r"\u4e00\u4e1c\u7701"),
                u(r"\u5e7f\u5dde\u5e02"),
                u(r"\u82b1\u90fd\u533a"),
            ),
        )

        self.assertEqual(conflicts, [])

    def test_long_autonomous_prefecture_name_allows_one_source_typo(self) -> None:
        conflicts = geocode_region_conflicts(
            {
                "province": u(r"\u4e91\u5357\u7701"),
                "city": u(r"\u7ea2\u6cb3\u54c8\u5c3c\u65cf\u5f5d\u65cf\u81ea\u6cbb\u5dde"),
                "district": u(r"\u8499\u81ea\u5e02"),
            },
            address_region=(
                u(r"\u4e91\u5357\u7701"),
                u(r"\u8499\u81ea\u5e02"),
                "",
            ),
            parking_region=(
                u(r"\u4e91\u5357\u7701"),
                u(r"\u7ea2\u6cb3\u54c8\u5c3c\u65cf\u84b8\u65cf\u81ea\u6cbb\u5dde"),
                u(r"\u8499\u81ea\u5e02"),
            ),
        )

        self.assertEqual(conflicts, [])


if __name__ == "__main__":
    unittest.main()
