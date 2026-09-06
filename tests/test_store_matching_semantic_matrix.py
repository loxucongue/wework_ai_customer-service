from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ai_paths"))

from app.graph.nodes import action_nodes
from app.graph.nodes.action_module_outputs import build_planner_fact_output
from app.graph.nodes.reply_nodes import _materialize_required_store_delivery
from app.graph.nodes.reply_validation import _validate_store_resolution_contract
from app.prompts.reply_synthesizer import _render_store_resolution_conclusion, _render_tool_facts


def test_reused_store_detail_prompt_requires_one_related_next_step() -> None:
    conclusion = _render_store_resolution_conclusion(
        {
            "status": "reuse_confirmed_store",
            "scope_match_level": "district",
            "exact_scope_has_store": True,
            "already_delivered_store_ids": ["160"],
            "destination_resolution": {
                "request_kind": "store_detail",
                "detail_kind": "parking",
            },
        }
    )

    assert "不得重复发送 store_address" in conclusion
    assert "不得在 text 中复述此前已经交付的完整地址或导航" in conclusion
    assert "回答详情＋预约时间问题＋预约目的" in conclusion
    assert "10元预约金锁定" in conclusion
    assert "不得声称预约或名额已经保留成功" in conclusion
    assert "我先记一下、方便后续登记" in conclusion
    assert "不补道路、门牌和导航描述" in conclusion
    assert "closing_action=none" in conclusion


def _store(
    store_id: str,
    name: str,
    province: str,
    city: str,
    district: str,
) -> dict[str, object]:
    return {
        "store_id": store_id,
        "store_name": name,
        "store_address": f"{province}{city}{district}{name}测试地址",
        "province": province,
        "city": city,
        "district": district,
        "location": "113.000000,28.000000",
        "store_fact_integrity": "valid",
    }


STORES = [
    _store("cs-yuelu", "长沙岳麓店", "湖南省", "长沙市", "岳麓区"),
    _store("cs-yuhua", "长沙雨花店", "湖南省", "长沙市", "雨花区"),
    _store("cs-wangcheng", "长沙望城二店", "湖南省", "长沙市", "望城区"),
    _store("cs-xingsha", "长沙星沙二店", "湖南省", "长沙市", "长沙县"),
    _store("cs-center-a", "长沙中心店", "湖南省", "长沙市", "芙蓉区"),
    _store("cs-center-b", "长沙中心店", "湖南省", "长沙市", "天心区"),
    _store("zz-center", "中心店", "湖南省", "株洲市", "天元区"),
    _store("bj-chaoyang", "北京朝阳店", "北京市", "北京市", "朝阳区"),
    _store("bj-haidian", "北京海淀店", "北京市", "北京市", "海淀区"),
    _store("sy-heping", "沈阳和平店", "辽宁省", "沈阳市", "和平区"),
    _store("sy-shenhe", "沈阳沈河店", "辽宁省", "沈阳市", "沈河区"),
    _store("tj-heping", "天津和平店", "天津市", "天津市", "和平区"),
    _store("cc-chaoyang", "长春朝阳店", "吉林省", "长春市", "朝阳区"),
    _store("jl-changyi", "吉林昌邑店", "吉林省", "吉林市", "昌邑区"),
    _store("jl-fengman", "吉林丰满店", "吉林省", "吉林市", "丰满区"),
    _store("nj-gulou", "南京鼓楼店", "江苏省", "南京市", "鼓楼区"),
    _store("fz-gulou", "福州鼓楼店", "福建省", "福州市", "鼓楼区"),
    _store("kf-gulou", "开封鼓楼店", "河南省", "开封市", "鼓楼区"),
    _store("jz-jingzhou", "荆州古城店", "湖北省", "荆州市", "荆州区"),
    _store("jz-shashi", "荆州沙市店", "湖北省", "荆州市", "沙市区"),
    _store("cd-jinniu", "成都金牛店", "四川省", "成都市", "金牛区"),
    _store("cd-jinjiang", "成都锦江店", "四川省", "成都市", "锦江区"),
]


CITY_SCOPES = [
    ("湖南省", "长沙市", "长沙", {"cs-yuelu", "cs-yuhua", "cs-wangcheng", "cs-xingsha", "cs-center-a", "cs-center-b"}),
    ("湖南省", "株洲市", "株洲", {"zz-center"}),
    ("北京市", "北京市", "北京", {"bj-chaoyang", "bj-haidian"}),
    ("辽宁省", "沈阳市", "沈阳", {"sy-heping", "sy-shenhe"}),
    ("天津市", "天津市", "天津", {"tj-heping"}),
    ("吉林省", "长春市", "长春", {"cc-chaoyang"}),
    ("吉林省", "吉林市", "吉林市", {"jl-changyi", "jl-fengman"}),
    ("江苏省", "南京市", "南京", {"nj-gulou"}),
    ("福建省", "福州市", "福州", {"fz-gulou"}),
    ("河南省", "开封市", "开封", {"kf-gulou"}),
    ("湖北省", "荆州市", "荆州", {"jz-jingzhou", "jz-shashi"}),
    ("四川省", "成都市", "成都", {"cd-jinniu", "cd-jinjiang"}),
]


DISTRICT_SCOPES = [
    ("湖南省", "长沙市", "长沙县", {"cs-xingsha"}),
    ("北京市", "北京市", "朝阳区", {"bj-chaoyang"}),
    ("吉林省", "长春市", "朝阳区", {"cc-chaoyang"}),
    ("辽宁省", "沈阳市", "和平区", {"sy-heping"}),
    ("天津市", "天津市", "和平区", {"tj-heping"}),
    ("江苏省", "南京市", "鼓楼区", {"nj-gulou"}),
    ("福建省", "福州市", "鼓楼区", {"fz-gulou"}),
    ("河南省", "开封市", "鼓楼区", {"kf-gulou"}),
    ("吉林省", "吉林市", "昌邑区", {"jl-changyi"}),
    ("湖北省", "荆州市", "荆州区", {"jz-jingzhou"}),
]


COUNTY_LEVEL_SCOPES = [
    ("四川省", "成都市", "简阳市", "jy-jianyang"),
    ("四川省", "成都市", "都江堰市", "jy-dujiangyan"),
    ("江苏省", "苏州市", "昆山市", "js-kunshan"),
    ("浙江省", "金华市", "义乌市", "zj-yiwu"),
    ("福建省", "泉州市", "晋江市", "fj-jinjiang"),
    ("湖南省", "长沙市", "浏阳市", "hn-liuyang"),
    ("湖南省", "长沙市", "宁乡市", "hn-ningxiang"),
    ("河南省", "郑州市", "新郑市", "hn-xinzheng"),
]


PROVINCE_SCOPES = [
    ("湖南省", "湖南", "长沙市", "株洲市"),
    ("四川省", "四川", "成都市", "绵阳市"),
    ("江苏省", "江苏", "南京市", "苏州市"),
    ("浙江省", "浙江", "杭州市", "金华市"),
    ("福建省", "福建", "福州市", "泉州市"),
    ("河南省", "河南", "郑州市", "开封市"),
]


UNCLEAR_LOCATION_QUERIES = [
    "附近有门店吗",
    "你们店在哪",
    "这边有没有店",
    "离我近的是哪家",
    "发个最近的店",
    "我这里能做吗",
    "周边门店查一下",
    "刚才说的附近店",
    "那边有店吗",
    "本地有门店没",
    "朝阳有门店吗",
    "和平区哪家店",
    "鼓楼店在哪",
    "城关有吗",
    "西湖附近的店",
    "新区有没有店",
    "开发区门店",
    "高新区店址",
    "人民路附近有店吗",
    "万达旁边的店",
    "火车站门店",
    "大学城有店不",
    "老城区门店",
    "市中心最近的店",
    "机场附近哪家",
    "华东有哪些店",
    "南方有多少门店",
    "全国门店都发我",
    "湖南周边门店",
    "长三角有没有店",
    "长沙还是株洲方便",
    "北京和长春的朝阳店",
    "南京鼓楼还是福州鼓楼",
    "沈阳和平和天津和平哪个好",
    "成都重庆各发一家",
    "A城门店",
    "XX区有店吗",
    "我忘了具体哪个区",
    "地址不太确定先看看店",
    "随便发一家门店",
]


SCENARIO_CATEGORY_COUNTS = {
    "city_scope_and_suffix_variants": 120,
    "same_name_district_with_parent": 40,
    "same_name_district_without_parent": 7,
    "county_level_city_and_parent_city": 64,
    "province_or_large_region": 36,
    "missing_or_ambiguous_location": 40,
    "named_store_duplicates": 3,
}


class _NoMapExpectedClient:
    def __init__(self) -> None:
        self.settings = SimpleNamespace(geocode_workflow_id="must-not-run")
        self.calls = 0

    async def run_workflow(self, _workflow_id: str, _parameters: dict[str, object]) -> dict[str, object]:
        self.calls += 1
        raise AssertionError("validated administrative scope must not be narrowed by map geocoding")


class _DestinationModel:
    available = True

    def __init__(self, output: dict[str, object]) -> None:
        self.output = output

    async def chat_json(self, *_args: object, **_kwargs: object) -> dict[str, object]:
        return self.output


def _state(content: str, stores: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "content": content,
        "normalized_content": content,
        "request_context": {"interface_version": "v3"},
        "customer_store_knowledge": {"source": "semantic-matrix", "stores": stores or STORES},
    }


def _candidate_ids(result: dict[str, object]) -> set[str]:
    candidates = result.get("candidate_stores") or []
    return {str(item.get("store_id")) for item in candidates if isinstance(item, dict)}


def _validated_admin_tool(
    *,
    query: str,
    raw_query: str,
    precision: str,
    admin: dict[str, str],
    confidence: str = "high",
) -> dict[str, object]:
    return {
        "query": query,
        "customer_raw_query": raw_query,
        "purpose": "list",
        "request_kind": "list",
        "destination_precision": precision,
        "destination_confidence": confidence,
        "destination_needs_clarification": False,
        "evidence_refs": ["current_message"],
        "expected_admin": admin,
        "semantic_destination_validated": True,
    }


def test_city_semantic_matrix_keeps_every_authorized_store_in_the_city() -> None:
    templates = (
        "{name}",
        "{name}有门店吗？",
        "{name}市有哪些店",
        "帮我找下{name}的店",
        "我在{name}这边",
        "{name}都有哪些门店",
        "离{name}近的门店",
        "麻烦查一下{name}",
        "{name}门店地址",
        "想去{name}看看",
    )
    cases = [
        (
            _validated_admin_tool(
                query=city,
                raw_query=template.format(name=display),
                precision="city",
                admin={"province": province, "city": city},
                confidence="medium" if index % 2 else "high",
            ),
            expected_ids,
            f"{province}/{city}/{template}",
        )
        for province, city, display, expected_ids in CITY_SCOPES
        for index, template in enumerate(templates)
    ]
    client = _NoMapExpectedClient()

    async def run_matrix() -> list[dict[str, object]]:
        return await asyncio.gather(
            *(action_nodes._customer_store_lookup(tool, _state(str(tool["customer_raw_query"])), client) for tool, _, _ in cases)
        )

    results = asyncio.run(run_matrix())

    assert len(cases) == 120
    assert client.calls == 0
    for result, (_, expected_ids, case_name) in zip(results, cases):
        assert result["status"] == "ok", case_name
        assert result["source"] == "customer_scope_model_admin", case_name
        assert _candidate_ids(result) == expected_ids, case_name


def test_same_named_district_matrix_uses_model_parent_region() -> None:
    templates = (
        "{district}",
        "{city}{district}",
        "我在{city}{district}",
        "查下{city}{district}的门店",
    )
    cases: list[tuple[dict[str, object], set[str], str]] = []
    for province, city, district, expected_ids in DISTRICT_SCOPES:
        for template in templates:
            display_city = city.removesuffix("市")
            raw_query = template.format(city=display_city, district=district)
            cases.append(
                (
                    _validated_admin_tool(
                        query=f"{city}{district}",
                        raw_query=raw_query,
                        precision="district",
                        admin={"province": province, "city": city, "district": district},
                    ),
                    expected_ids,
                    f"{province}/{city}/{district}/{template}",
                )
            )
    client = _NoMapExpectedClient()

    async def run_matrix() -> list[dict[str, object]]:
        return await asyncio.gather(
            *(action_nodes._customer_store_lookup(tool, _state(str(tool["customer_raw_query"])), client) for tool, _, _ in cases)
        )

    results = asyncio.run(run_matrix())

    assert len(cases) == 40
    assert client.calls == 0
    for result, (_, expected_ids, case_name) in zip(results, cases):
        assert result["status"] == "ok", case_name
        assert result["resolved_admin_level"] == "district", case_name
        assert _candidate_ids(result) == expected_ids, case_name


@pytest.mark.parametrize(
    ("admin", "expected_ids"),
    [
        ({"province": "北京市", "city": "北京市", "district": "朝阳区"}, {"bj-chaoyang"}),
        ({"province": "吉林省", "city": "长春市", "district": "朝阳区"}, {"cc-chaoyang"}),
        ({"province": "辽宁省", "city": "沈阳市", "district": "和平区"}, {"sy-heping"}),
        ({"province": "天津市", "city": "天津市", "district": "和平区"}, {"tj-heping"}),
        ({"province": "江苏省", "city": "南京市", "district": "鼓楼区"}, {"nj-gulou"}),
        ({"province": "福建省", "city": "福州市", "district": "鼓楼区"}, {"fz-gulou"}),
        ({"province": "河南省", "city": "开封市", "district": "鼓楼区"}, {"kf-gulou"}),
    ],
)
def test_same_named_generic_district_is_disambiguated_by_structured_parent(
    admin: dict[str, str],
    expected_ids: set[str],
) -> None:
    client = _NoMapExpectedClient()
    result = asyncio.run(
        action_nodes._customer_store_lookup(
            _validated_admin_tool(
                query=str(admin["district"]),
                raw_query=f"{admin['district']}有店吗",
                precision="district",
                admin=admin,
            ),
            _state(f"{admin['district']}有店吗"),
            client,
        )
    )

    assert client.calls == 0
    assert _candidate_ids(result) == expected_ids


def test_county_level_city_matrix_does_not_collapse_into_parent_city_or_same_root_store() -> None:
    templates = (
        "{county}",
        "{county}有门店吗",
        "{city}{county}有哪些店",
        "我在{county}这边",
        "帮我查下{county}的门店",
        "{county}店址发一下",
        "离{county}近的店",
        "想去{city}{county}看看",
    )
    stores = [
        _store(store_id, f"{county}中心店", province, city, county)
        for province, city, county, store_id in COUNTY_LEVEL_SCOPES
    ]
    cases = [
        (
            _validated_admin_tool(
                query=county,
                raw_query=template.format(
                    city=city.removesuffix("市"),
                    county=county.removesuffix("市"),
                ),
                precision="district",
                admin={"province": province, "city": city, "county_level_city": county},
            ),
            store_id,
            f"{province}/{city}/{county}/{template}",
        )
        for province, city, county, store_id in COUNTY_LEVEL_SCOPES
        for template in templates
    ]
    client = _NoMapExpectedClient()

    async def run_matrix() -> list[dict[str, object]]:
        return await asyncio.gather(
            *(
                action_nodes._customer_store_lookup(
                    tool,
                    _state(str(tool["customer_raw_query"]), stores),
                    client,
                )
                for tool, _, _ in cases
            )
        )

    results = asyncio.run(run_matrix())

    assert len(cases) == 64
    assert client.calls == 0
    for result, (_, store_id, case_name) in zip(results, cases):
        assert result["status"] == "ok", case_name
        assert result["resolved_admin_level"] == "district", case_name
        assert _candidate_ids(result) == {store_id}, case_name


def test_province_scope_matrix_requests_narrower_location_instead_of_arbitrary_store() -> None:
    templates = (
        "{province}有门店吗",
        "{province}有哪些店",
        "我在{province}",
        "把{province}的门店发我",
        "{province}哪家方便",
        "想看看{province}门店",
    )
    cases: list[tuple[dict[str, object], list[dict[str, object]], str]] = []
    for index, (province, display, city_a, city_b) in enumerate(PROVINCE_SCOPES, start=1):
        stores = [
            _store(f"province-{index}-a", f"{city_a}中心店", province, city_a, "中心区"),
            _store(f"province-{index}-b", f"{city_b}中心店", province, city_b, "中心区"),
        ]
        for template in templates:
            raw_query = template.format(province=display)
            cases.append(
                (
                    _validated_admin_tool(
                        query=province,
                        raw_query=raw_query,
                        precision="province",
                        admin={"province": province},
                    ),
                    stores,
                    f"{province}/{template}",
                )
            )
    client = _NoMapExpectedClient()

    async def run_matrix() -> list[dict[str, object]]:
        return await asyncio.gather(
            *(
                action_nodes._customer_store_lookup(
                    tool,
                    _state(str(tool["customer_raw_query"]), stores),
                    client,
                )
                for tool, stores, _ in cases
            )
        )

    results = asyncio.run(run_matrix())

    assert len(cases) == 36
    assert client.calls == 0
    for result, (_, _, case_name) in zip(results, cases):
        assert result["status"] == "need_location", case_name
        assert result["source"] == "customer_scope_model_admin_province_multiple", case_name
        assert result["candidate_stores"] == [], case_name


@pytest.mark.parametrize("customer_text", UNCLEAR_LOCATION_QUERIES)
def test_missing_ambiguous_or_overbroad_location_matrix_never_guesses_store(customer_text: str) -> None:
    output = {
        "request_kind": "clarify",
        "destination_query": customer_text,
        "destination_precision": "unknown",
        "administrative_context": {},
        "poi_query": "",
        "candidate_interpretations": [],
        "destination_subject": "customer",
        "named_store": "",
        "detail_kind": "none",
        "evidence_refs": ["current_message"],
        "superseded_location_refs": [],
        "confidence": "low",
        "needs_clarification": True,
        "geocode_before_clarification": False,
        "reason": "缺少可唯一确定范围的客户地点证据",
    }
    client = _NoMapExpectedClient()
    result = asyncio.run(
        action_nodes._resolve_customer_store_workflow(
            {"arguments": {"purpose": "store_search"}},
            _state(customer_text),
            client,
            model_client=_DestinationModel(output),
        )
    )

    assert client.calls == 0
    assert result["status"] == "need_location_confirmation"
    assert result["candidate_store_ids"] == []
    assert result["delivery_store_ids"] == []


def test_customer_utterance_matrix_contains_at_least_300_classified_scenarios() -> None:
    assert sum(SCENARIO_CATEGORY_COUNTS.values()) == 310
    assert len(UNCLEAR_LOCATION_QUERIES) == 40


@pytest.mark.parametrize(
    ("named_store", "admin", "expected_ids"),
    [
        (
            "长沙星沙二店",
            {"province": "湖南省", "city": "长沙市"},
            {"cs-xingsha"},
        ),
        (
            "长沙中心店",
            {"province": "湖南省", "city": "长沙市"},
            {"cs-center-a", "cs-center-b"},
        ),
        (
            "中心店",
            {"province": "湖南省", "city": "株洲市"},
            {"zz-center"},
        ),
    ],
)
def test_named_store_duplicates_keep_all_and_only_region_consistent_candidates(
    named_store: str,
    admin: dict[str, str],
    expected_ids: set[str],
) -> None:
    raw_query = f"{named_store}地址发我"
    client = _NoMapExpectedClient()
    result = asyncio.run(
        action_nodes._customer_store_lookup(
            {
                "query": named_store,
                "customer_raw_query": raw_query,
                "purpose": "store_detail",
                "request_kind": "store_detail",
                "destination_precision": "city",
                "destination_confidence": "high",
                "destination_needs_clarification": False,
                "evidence_refs": ["current_message"],
                "expected_admin": admin,
                "named_store": named_store,
                "semantic_destination_validated": True,
            },
            _state(raw_query),
            client,
        )
    )

    assert client.calls == 0
    assert result["status"] == "ok"
    assert result["source"] == "customer_scope_model_named_store"
    assert _candidate_ids(result) == expected_ids


def test_low_confidence_ambiguous_model_result_requests_clarification_without_matching() -> None:
    output = {
        "request_kind": "clarify",
        "destination_query": "朝阳",
        "destination_precision": "district",
        "administrative_context": {"district": "朝阳区"},
        "poi_query": "",
        "candidate_interpretations": [
            {
                "destination_query": "北京市朝阳区",
                "administrative_context": {"province": "北京市", "city": "北京市", "district": "朝阳区"},
                "confidence": "medium",
                "evidence_refs": ["current_message"],
            },
            {
                "destination_query": "长春市朝阳区",
                "administrative_context": {"province": "吉林省", "city": "长春市", "district": "朝阳区"},
                "confidence": "medium",
                "evidence_refs": ["current_message"],
            },
        ],
        "destination_subject": "customer",
        "named_store": "",
        "detail_kind": "none",
        "evidence_refs": ["current_message"],
        "superseded_location_refs": [],
        "confidence": "low",
        "needs_clarification": True,
        "geocode_before_clarification": False,
        "reason": "同名行政区且没有上级城市证据",
    }
    client = _NoMapExpectedClient()

    result = asyncio.run(
        action_nodes._resolve_customer_store_workflow(
            {"arguments": {"purpose": "list"}},
            _state("朝阳有店吗"),
            client,
            model_client=_DestinationModel(output),
        )
    )

    lookup = result["customer_store_lookup"]
    assert client.calls == 0
    assert result["status"] == "need_location_confirmation"
    assert lookup["candidate_stores"] == []
    assert lookup["missing"] == ["confirmed_location"]


def test_city_scope_never_imports_a_store_outside_customer_authorization() -> None:
    authorized = [store for store in STORES if store["store_id"] != "cs-wangcheng"]
    client = _NoMapExpectedClient()
    result = asyncio.run(
        action_nodes._customer_store_lookup(
            _validated_admin_tool(
                query="长沙市",
                raw_query="长沙有门店吗",
                precision="city",
                admin={"province": "湖南省", "city": "长沙市"},
            ),
            _state("长沙有门店吗", authorized),
            client,
        )
    )

    assert client.calls == 0
    assert _candidate_ids(result) == {
        "cs-yuelu",
        "cs-yuhua",
        "cs-xingsha",
        "cs-center-a",
        "cs-center-b",
    }
    assert "cs-wangcheng" not in _candidate_ids(result)


def test_large_city_scope_keeps_all_candidates_and_switches_from_cards_to_text() -> None:
    stores = [
        _store(str(index), f"成都门店{index}", "四川省", "成都市", f"测试区{index}")
        for index in range(1, 9)
    ]
    state = _state("成都有哪些门店", stores)
    output = build_planner_fact_output(
        {
            "customer_store_lookup": {
                "status": "ok",
                "raw_query": "成都有哪些门店",
                "query": "成都市",
                "purpose": "list",
                "resolved_admin_level": "city",
                "scope_match_level": "city",
                "allow_broad_scope_delivery": True,
                "stores": stores,
                "candidate_stores": stores,
                "candidate_store_count": len(stores),
                "missing": [],
            }
        },
        state,
    )
    resolution = output["structured_facts"]["store_resolution_fact"]

    assert resolution["status"] == "send_multiple"
    assert resolution["candidate_store_ids"] == [str(index) for index in range(1, 9)]
    assert resolution["delivery_store_ids"] == []
    assert resolution["delivery_mode"] == "text_store_list"
    assert [item["store_id"] for item in resolution["text_store_summaries"]] == [
        str(index) for index in range(1, 9)
    ]
    assert [item["store_name"] for item in resolution["text_store_summaries"]] == [
        f"成都门店{index}" for index in range(1, 9)
    ]


def test_text_store_list_prompt_and_validation_forbid_store_cards() -> None:
    resolution = {
        "status": "send_multiple",
        "candidate_search_complete": True,
        "visible_candidate_count": 7,
        "candidate_store_ids": [str(index) for index in range(1, 8)],
        "delivery_store_ids": [],
        "delivery_mode": "text_store_list",
        "text_store_summaries": [
            {"store_id": str(index), "store_name": f"长沙门店{index}", "district": f"测试区{index}"}
            for index in range(1, 8)
        ],
    }
    conclusion = _render_store_resolution_conclusion(resolution)
    state = {"fact_envelope": {"structured_facts": {"store_resolution_fact": resolution}}}

    assert "只用一至两条 text" in conclusion
    assert "完整列出所有门店名称和所在区县" in conclusion
    rendered_facts = _render_tool_facts(
        {
            "normalized_tool_facts": {
                "structured_facts": {
                    "store_resolution_fact": resolution,
                    "store_facts": [],
                }
            }
        },
        json_dumps=lambda value: json.dumps(value, ensure_ascii=False),
    )
    assert all(f"长沙门店{index}" in rendered_facts for index in range(1, 8))
    original_messages = [{"type": "text", "content": "门店比较多，我按区域给您列一下。"}]
    materialized, changed = _materialize_required_store_delivery(original_messages, state)
    assert materialized == original_messages
    assert changed is False
    _validate_store_resolution_contract(
        [
            {
                "type": "text",
                "content": "、".join(
                    f"长沙门店{index}（测试区{index}）" for index in range(1, 8)
                ),
            }
        ],
        state,
    )
    with pytest.raises(ValueError, match="incomplete_text_store_list_contract"):
        _validate_store_resolution_contract(
            [
                {
                    "type": "text",
                    "content": "、".join(
                        f"长沙门店{index}（测试区{index}）" for index in range(1, 7)
                    ),
                }
            ],
            state,
        )
    with pytest.raises(ValueError, match="incomplete_text_store_list_contract"):
        _validate_store_resolution_contract(
            [
                {
                    "type": "text",
                    "content": "、".join(
                        f"长沙门店{index}（测试区{index if index != 7 else 6}）"
                        for index in range(1, 8)
                    ),
                }
            ],
            state,
        )
    with pytest.raises(ValueError, match="store_cards_not_allowed_for_text_store_list"):
        _validate_store_resolution_contract(
            [
                {"type": "text", "content": "门店如下"},
                {"type": "store_address", "content": {"store_id": "1"}},
            ],
            state,
        )


def test_four_store_city_delivery_materializes_all_four_cards() -> None:
    resolution = {
        "status": "send_multiple",
        "delivery_store_ids": ["160", "179", "546", "552"],
        "visible_candidate_ids": ["160", "179", "546", "552"],
        "allow_broad_scope_delivery": True,
        "delivery_mode": "send_all_candidates",
    }
    state = {"fact_envelope": {"structured_facts": {"store_resolution_fact": resolution}}}

    messages, changed = _materialize_required_store_delivery(
        [{"type": "text", "content": "长沙目前有4家门店，我都发您。"}],
        state,
    )

    assert changed is True
    assert [
        item["content"]["store_id"]
        for item in messages
        if item["type"] == "store_address"
    ] == ["160", "179", "546", "552"]
