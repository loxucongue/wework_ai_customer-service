from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.graph.state import AgentState


@dataclass(frozen=True)
class PriceSkillCallbacks:
    ad_price_without_explicit_project: Callable[..., bool]
    canonical_price_project: Callable[[str], str]
    contextual_price_project: Callable[[AgentState], str]
    extract_price_digits: Callable[[str], list[str]]
    extract_project: Callable[[str], str]
    filter_pricing_rows_for_project: Callable[[list[dict[str, Any]], str], list[dict[str, Any]]]
    has_price_objection: Callable[[str], bool]
    is_broad_price_category: Callable[[str], bool]
    price_bits: Callable[[dict[str, Any]], list[str]]
    price_risk_terms: Callable[[str], list[str]]
    pricing_rows: Callable[[dict[str, Any]], list[dict[str, Any]]]
    pricing_rows_from_kb: Callable[[dict[str, Any]], list[dict[str, Any]]]


def _row_price_facts(name: str, row: dict[str, Any]) -> list[str]:
    facts: list[str] = []
    for key, label in [
        ("new_price", "新客体验价"),
        ("promo_price", "活动价"),
        ("daily_price", "日常单次价"),
        ("old_price", "老客单次价"),
        ("old_card", "老客推荐卡项"),
    ]:
        value = str(row.get(key) or "").strip()
        if value and value not in {"0", "0.00", "None", "null"}:
            facts.append(f"{name}{label}：{value}")
    note = str(row.get("price_note") or "").strip()
    if note:
        facts.append(f"{name}报价备注：{note}")
    return facts


def _price_reply_instructions(*, has_objection: bool, has_price: bool, project: str) -> list[str]:
    instructions = [
        "最终回复必须由模型自然生成，不要照搬事实摘要。",
        "有明确价格事实时可以引用；没有明确价格事实时不能编造数字或拿其他项目价格替代。",
    ]
    if has_objection:
        instructions.append("本轮有预算压力或议价倾向，先承接预算顾虑，再基于已知价格解释可参考项；不能承诺底价、改价或同价。")
    elif has_price:
        instructions.append("本轮询问价格时，优先回答最相关的价格事实，再简短说明具体配置仍要结合项目和门店。")
    elif project:
        instructions.append("已识别项目但无明确价格事实，说明暂未查到可直接引用价格，并给出下一步核对方向。")
    else:
        instructions.append("项目不明确时，只问一个必要澄清问题。")
    return instructions


def price_skill_output(
    content: str,
    tool_results: dict[str, Any],
    state: AgentState | None,
    callbacks: PriceSkillCallbacks,
) -> dict[str, Any]:
    kb_rows = callbacks.pricing_rows_from_kb(tool_results)
    kb_items = tool_results.get("project_price", {}).get("items", [])
    project = callbacks.canonical_price_project(callbacks.contextual_price_project(state or {}) or callbacks.extract_project(content))
    if callbacks.ad_price_without_explicit_project(state, project):
        digits = callbacks.extract_price_digits(content)
        facts = ["本轮涉及广告/活动价格，但没有明确项目名或广告截图，不能拿相似商品价代替。"]
        if digits:
            facts.append("本轮提到的价格数字：" + "、".join(digits[:3]))
        return {
            "skill": "price_consult",
            "intent": "price_inquiry",
            "facts": facts,
            "reply_points": [
                "先承接本轮提到的广告价或预约金数字，再说明需要核对广告对应项目、包含项、尾款和是否有额外项目；不要引用知识库相似命中的商品价格，也不要确认未核实的广告价存在。",
            ],
            "missing_slots": ["广告截图或项目名称"],
            "risk_flags": callbacks.price_risk_terms(content),
            "suggested_next_step": "核对广告价对应项目和收费口径",
            "confidence": 0.7,
        }
    if callbacks.is_broad_price_category(project):
        return {
            "skill": "price_consult",
            "intent": "price_inquiry",
            "facts": ["本轮询问的是斑点/色沉等大类改善价格，不能拿具体商品价替代。"],
            "reply_points": [
                "斑点/色沉类价格不能只按大类报固定价，要看斑型、范围、深浅、次数和最终配置；可以先按预算范围沟通，但不要引用不相关项目价格。",
            ],
            "missing_slots": ["斑型或照片", "预算范围"],
            "risk_flags": callbacks.price_risk_terms(content),
            "suggested_next_step": "先确认斑型范围或预算，再匹配具体配置",
            "confidence": 0.7,
        }
    rows = callbacks.filter_pricing_rows_for_project(kb_rows or callbacks.pricing_rows(tool_results), project)
    facts: list[str] = []
    reply_points: list[str] = []
    missing_slots: list[str] = []

    if rows:
        row = rows[0]
        name = str(row.get("project_name") or project or "相关项目")
        price_bits = callbacks.price_bits(row)
        facts.extend(_row_price_facts(name, row) or price_bits)
        has_price = bool(price_bits)
        reply_points.extend(
            _price_reply_instructions(
                has_objection=callbacks.has_price_objection(content),
                has_price=has_price,
                project=project or name,
            )
        )
        if project and project not in name and row.get("_source") == "local_xlsx":
            reply_points.append("本次命中可能是相关配置，不是客户所问项目的精确价格；如需精确价格必须说明这个边界。")
        if not has_price:
            reply_points.append("查到项目记录但价格字段不完整，最终回复不能报数字。")
    elif kb_items:
        facts.append(f"价格知识库命中{len(kb_items)}条结果")
        reply_points.extend(
            _price_reply_instructions(
                has_objection=callbacks.has_price_objection(content),
                has_price=False,
                project=project,
            )
        )
    else:
        facts.append("暂未查到明确可引用价格")
        reply_points.extend(
            _price_reply_instructions(
                has_objection=callbacks.has_price_objection(content),
                has_price=False,
                project=project,
            )
        )

    if not project and not rows:
        missing_slots.append("项目名称")

    return {
        "skill": "price_consult",
        "intent": "price_inquiry",
        "facts": facts,
        "reply_points": reply_points,
        "missing_slots": missing_slots,
        "risk_flags": callbacks.price_risk_terms(content),
        "suggested_next_step": "预算异议承接" if callbacks.has_price_objection(content) else "确认项目配置" if project or rows else "补充项目名称",
        "confidence": 0.78 if rows else 0.62,
    }
