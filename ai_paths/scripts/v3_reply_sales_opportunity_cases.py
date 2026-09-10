"""Synthetic opportunity probes: expected answers stay outside model inputs."""
from __future__ import annotations

from typing import Any

from scripts.v3_reply_naturalness_cases import _case


OFFER_FACTS = (
    "虚构活动权威事实：新客面部斑点改善268元，包含肤况评估、一次面部护理、"
    "护理后注意事项指导；需提前预约；仅限新客；不含手部，不保证一次根除。"
)
OFFER_GROUPS = [
    ["268"], ["评估", "肤况检查", "皮肤检查"], ["一次", "1次"],
    ["指导", "注意事项"], ["新客", "首次", "第一次"], ["提前预约", "预约"],
]


def build_opportunity_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []

    def add(group: str, index: int, current: str, history: list[tuple[str, str]],
            stage: str, actions: tuple[str, ...], groups: list[list[str]],
            *, structures: tuple[str, ...] = (), store: bool = False) -> None:
        case = _case(
            f"advance-{group}-{index:02d}", "sales_opportunity", current, history,
            next_stage=stage, expected_actions=actions,
            allowed_actions=("keep_open", *actions), required_message_types=structures,
            repeat_probe=index == 1, l3=index == 1,
        )
        case.update(
            opportunity_group=group, required_text_groups=groups,
            extra_facts=OFFER_FACTS,
            store_available=store,
            activity_integrity=stage == "activity_offer",
        )
        cases.append(case)

    for i, current in enumerate(["你好", "在吗", "刚加上你", "哈喽", "下午好"], 1):
        add("opening", i, current, [], "effect_evidence", ("ask_missing_fact",),
            [["斑", "肤", "皮肤"], ["？", "?", "吗", "哪"]])

    for i, current in enumerate(["主要脸颊有些斑", "想改善脸上的色沉", "面部斑点有几年了"], 1):
        add("information", i, current, [("assistant", "主要想改善哪个部位？")],
            "effect_evidence", ("deliver_value", "send_effect_material"),
            [["改善", "淡化"], ["斑", "色沉"]])
    for i, current in enumerate(["我在云州市海棠区", "云州海棠，位置已经发给你了"], 4):
        add("information", i, current, [("assistant", "方便说下城市和区吗？")],
            "store", ("send_store",), [["海棠", "示例路"]],
            structures=("store_address",), store=True)

    for i, current in enumerate(["这个改善挺明显", "案例看着不错", "和我的情况挺像", "嗯，这个效果可以", "看完了，还不错"], 1):
        add("effect", i, current, [("assistant", "这组是面部斑点改善参考，个体结果有差异。")],
            "activity_offer", ("explain_activity", "deliver_value"), OFFER_GROUPS)

    for i, current in enumerate(["这个价格能接受", "这样算下来还行", "268可以", "对比下来你们价格合适", "价格没问题"], 1):
        add("price", i, current,
            [("assistant", "效果参考已经看过，这是新客面部268元活动，包含评估、一次护理和后续指导。")],
            "store", ("ask_missing_fact",), [["城市", "哪个市", "哪个区", "哪儿", "哪里"]])

    for i, current in enumerate(["这样解释我就放心了", "没有额外强制消费就好", "那这个顾虑没了", "规则清楚了，可以继续", "明白，不担心这个了"], 1):
        add("resolved", i, current,
            [("assistant", "已介绍效果；您刚问的收费疑问已按权威规则说明，不强制额外消费。")],
            "activity_offer", ("explain_activity", "deliver_value"), OFFER_GROUPS)

    for i, current in enumerate(["都了解了，可以", "那不错", "嗯，行", "这个方案可以", "停车也方便，那挺好"], 1):
        add("booking", i, current,
            [("assistant", "面部效果参考、新客268元完整活动和唯一门店地址都已发您，门店可停车。")],
            "appointment", ("invite_booking",),
            [["哪天", "什么时间", "什么时候", "周末", "工作日", "时段", "日期", "时间"],
             ["？", "?", "吗", "哪", "什么时候"]], store=True)
    return cases


OPPORTUNITY_CASES = build_opportunity_cases()
