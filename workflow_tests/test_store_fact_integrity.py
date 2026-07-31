from __future__ import annotations

import asyncio

import pytest

from app.config import Settings
from app.graph.nodes.action_nodes import _customer_store_lookup, _distance_calculate
from app.graph.nodes.reply_validation import validate_reply_consistency
from app.services.store_fact_integrity import assess_store_fact_integrity
from app.services.store_snapshot_service import StoreSnapshotService


def u(value: str) -> str:
    return value.encode("ascii").decode("unicode_escape")


def corrupted_store_129() -> dict[str, object]:
    return {
        "store_id": "129",
        "store_name": u(r"\u5e7f\u5dde\u756a\u79ba\u5e97"),
        "province": u(r"\u56db\u5ddd\u7701"),
        "city": u(r"\u5357\u5145\u5e02"),
        "district": u(r"\u5357\u90e8\u53bf"),
        "store_address": u(r"\u756a\u79ba\u4e07\u8fbe\u5e7f\u573aB4\u680b"),
        "parking_address": u(
            r"\u5e7f\u4e1c\u7701\u5e7f\u5dde\u5e02\u756a\u79ba\u533a\u5174\u5357\u5927\u9053368\u53f7"
        ),
        "location": "106.064163,31.351917",
        "geocode_formatted_address": u(
            r"\u56db\u5ddd\u7701\u5357\u5145\u5e02\u5357\u90e8\u53bf\u4e07\u8fbe\u5e7f\u573a4\u680b"
        ),
    }


def valid_guangzhou_store() -> dict[str, object]:
    return {
        "store_id": "406",
        "store_name": u(r"\u5e7f\u5dde\u756a\u79ba\u4e8c\u5e97"),
        "province": u(r"\u5e7f\u4e1c\u7701"),
        "city": u(r"\u5e7f\u5dde\u5e02"),
        "district": u(r"\u756a\u79ba\u533a"),
        "store_address": u(
            r"\u5e7f\u4e1c\u7701\u5e7f\u5dde\u5e02\u756a\u79ba\u533a\u5174\u5357\u5927\u9053368\u53f7"
        ),
    }


def test_corrupted_store_region_is_invalid() -> None:
    store = corrupted_store_129()
    result = assess_store_fact_integrity(
        store,
        known_stores=[store, valid_guangzhou_store()],
    )

    assert result["status"] == "invalid"
    assert "primary_text_city_conflict" in result["violations"]
    assert "parking_city_conflict" in result["warnings"]


def test_parking_region_conflict_alone_is_warning() -> None:
    store = {
        "store_id": "1",
        "store_name": u(r"\u5357\u4eac\u79e6\u6dee\u5e97"),
        "province": u(r"\u6c5f\u82cf\u7701"),
        "city": u(r"\u5357\u4eac\u5e02"),
        "district": u(r"\u79e6\u6dee\u533a"),
        "store_address": u(r"\u6c5f\u82cf\u7701\u5357\u4eac\u5e02\u79e6\u6dee\u533a\u4e2d\u5c71\u4e1c\u8def198\u53f7"),
        "parking_address": u(r"\u5b89\u5fbd\u7701\u9a6c\u978d\u5c71\u5e02\u67d0\u505c\u8f66\u573a"),
    }
    result = assess_store_fact_integrity(store, known_stores=[store])

    assert result["status"] == "valid"
    assert "parking_city_conflict" in result["warnings"]
    assert "parking_province_conflict" in result["warnings"]


def test_snapshot_build_excludes_corrupted_store() -> None:
    service = StoreSnapshotService(Settings(geocode_workflow_id=""), platform_client=None)
    snapshot = service._build_snapshot([corrupted_store_129(), valid_guangzhou_store()])

    assert "129" not in snapshot["stores_by_id"]
    assert "406" in snapshot["stores_by_id"]
    assert snapshot["invalid_store_count"] == 1


class _FakeCoze:
    def __init__(self) -> None:
        self.settings = Settings(geocode_workflow_id="", distance_workflow_id="")


class _GeocodeFakeCoze:
    def __init__(self) -> None:
        self.settings = Settings(
            geocode_workflow_id="geocode",
            distance_workflow_id="",
        )

    async def run_workflow(
        self,
        workflow_id: str,
        parameters: dict[str, object],
    ) -> dict[str, object]:
        assert workflow_id == "geocode"
        return {
            "data": {
                "output": {
                    "province": u(r"\u56db\u5ddd\u7701"),
                    "city": u(r"\u5df4\u4e2d\u5e02"),
                    "district": u(r"\u5e73\u660c\u53bf"),
                    "location": "106.293,31.560",
                }
            }
        }


def test_distance_rejects_invalid_candidates_before_ranking() -> None:
    output = asyncio.run(
        _distance_calculate(
            {"origin": u(r"\u56db\u5ddd\u5df4\u4e2d\u5e73\u660c"), "candidate_source": "customer_store_lookup"},
            {},
            _FakeCoze(),  # type: ignore[arg-type]
            {"customer_store_lookup": {"candidate_stores": [corrupted_store_129()]}},
        )
    )

    assert output["status"] == "no_candidate_stores"
    assert output["candidate_stores"] == []
    assert output["filtered_invalid_stores"][0]["store_id"] == "129"


def test_distance_preselects_from_all_candidates_before_ranking() -> None:
    candidates = [
        {
            "store_id": str(index),
            "store_name": f"Store {index}",
            "province": u(r"\u56db\u5ddd\u7701"),
            "city": u(r"\u5df4\u4e2d\u5e02"),
            "district": u(r"\u5df4\u5dde\u533a"),
            "store_address": f"Address {index}",
            "location": f"{103.0 + index * 0.01},30.0",
        }
        for index in range(1, 13)
    ]
    candidates.append(
        {
            "store_id": "99",
            "store_name": "Nearest Store",
            "province": u(r"\u56db\u5ddd\u7701"),
            "city": u(r"\u5df4\u4e2d\u5e02"),
            "district": u(r"\u5df4\u5dde\u533a"),
            "store_address": "Nearest Address",
            "location": "106.294,31.561",
        }
    )

    output = asyncio.run(
        _distance_calculate(
            {
                "origin": u(r"\u56db\u5ddd\u5df4\u4e2d\u5e73\u660c"),
                "candidate_source": "customer_store_lookup",
            },
            {},
            _GeocodeFakeCoze(),  # type: ignore[arg-type]
            {"customer_store_lookup": {"candidate_stores": candidates}},
        )
    )

    assert output["status"] == "ok"
    assert output["candidate_store_count"] == 13
    assert output["ranked_candidate_count"] == 12
    assert output["ranked_stores"][0]["store_id"] == "99"


def test_reply_validation_rejects_invalid_store_card() -> None:
    invalid = dict(corrupted_store_129())
    invalid["store_fact_integrity"] = "invalid"
    invalid["store_fact_integrity_violations"] = ["primary_text_city_conflict"]
    state = {
        "fact_envelope": {
            "structured_facts": {
                "store_facts": [invalid],
                "recommended_store": invalid,
            }
        },
        "customer_store_knowledge": {"stores": [invalid]},
    }

    with pytest.raises(ValueError, match="invalid_store_fact_integrity|unsupported_store_address_message"):
        validate_reply_consistency(
            [{"type": "store_address", "order": 1, "content": {"store_id": "129"}}],
            state,
        )


def test_reply_validation_reassesses_unannotated_invalid_store_card() -> None:
    invalid = corrupted_store_129()
    state = {
        "fact_envelope": {
            "structured_facts": {
                "store_facts": [invalid],
                "recommended_store": invalid,
            }
        },
        "customer_store_knowledge": {"stores": [invalid]},
    }

    with pytest.raises(
        ValueError,
        match="invalid_store_fact_integrity|unsupported_store_address_message",
    ):
        validate_reply_consistency(
            [
                {
                    "type": "store_address",
                    "order": 1,
                    "content": {"store_id": "129"},
                }
            ],
            state,
        )
