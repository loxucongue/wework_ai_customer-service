from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable

from app.graph.nodes.price_question_frames import build_price_question_frame, extract_customer_seen_price_digits
from app.graph.nodes.result_compaction import price_question_without_explicit_project
from app.graph.nodes.sales_talk_kb_parsing import first_sales_talk_slice
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


def _row_price_facts(name: str, row: dict[str, Any], content: str) -> list[str]:
    facts: list[str] = []
    note = str(row.get("price_note") or "").strip()
    range_match = re.search(r"参考价[:：]?([0-9]+(?:\.[0-9]+)?-[0-9]+(?:\.[0-9]+)?)", note)
    if range_match:
        facts.append(f"{name}参考价：{range_match.group(1)}")
    text = str(content or "")
    prefer_start_price = (
        str(row.get("_source") or "") == "local_pricing_rules"
        and not any(term in text for term in ["新客价", "新客", "活动价", "活动", "老客", "复购", "单次", "一次"])
    )
    for key, label in [
        ("new_price", "新客体验价"),
        ("promo_price", "活动价"),
        ("daily_price", "日常单次价"),
        ("old_price", "老客单次价"),
        ("old_card", "老客推荐卡项"),
    ]:
        value = str(row.get(key) or "").strip()
        if value and value not in {"0", "0.00", "None", "null"}:
            if prefer_start_price and key == "new_price":
                facts.append(f"{name}起步参考价：{value}")
                continue
            facts.append(f"{name}{label}：{value}")
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


def _apply_price_frame_guidance(
    facts: list[str],
    reply_points: list[str],
    frame: Any,
) -> None:
    if not frame:
        return
    facts.insert(0, f"本轮必须先正面回答客户原问题：{frame.answer_first}")
    reply_points.insert(0, frame.must_answer)
    reply_points.insert(1, frame.reply_point)
    reply_points.insert(2, "不要把本轮追问改成泛泛询问项目、城市、门店或预约时间。")


def price_skill_output(
    content: str,
    tool_results: dict[str, Any],
    state: AgentState | None,
    callbacks: PriceSkillCallbacks,
) -> dict[str, Any]:
    sales_talk = first_sales_talk_slice(tool_results)
    kb_rows = callbacks.pricing_rows_from_kb(tool_results)
    kb_items = tool_results.get("project_price", {}).get("items", [])
    explicit_project = callbacks.canonical_price_project(callbacks.extract_project(content))
    digits = extract_customer_seen_price_digits(content) or callbacks.extract_price_digits(content)
    frame = build_price_question_frame(content, digits)
    if callbacks.ad_price_without_explicit_project(state, explicit_project) or price_question_without_explicit_project(state):
        facts = ["本轮是在核对价格口径，但当前没有足够明确的项目事实，不能拿相似商品价代替。"]
        if digits:
            facts.append("本轮提到的价格数字：" + "、".join(digits[:3]))
        reply_points: list[str] = []
        missing_slots = ["项目名称或广告截图"]
        suggested_next_step = "核对价格对应项目和收费口径"
        if frame:
            _apply_price_frame_guidance(facts, reply_points, frame)
            if frame.missing_slot:
                missing_slots = [frame.missing_slot]
            else:
                missing_slots = []
            if frame.suggested_next_step:
                suggested_next_step = frame.suggested_next_step
        else:
            reply_points.append("先承接本轮提到的价格数字或收费问题，再说明需要核对对应项目、包含项、尾款和是否有额外项目；不要引用知识库相似命中的商品价格，也不要确认未核实的广告价存在。")
        _inject_sales_talk_guidance(facts, reply_points, sales_talk)
        return {
            "skill": "price_consult",
            "intent": "price_inquiry",
            "facts": facts,
            "reply_points": reply_points,
            "missing_slots": missing_slots,
            "risk_flags": callbacks.price_risk_terms(content),
            "suggested_next_step": suggested_next_step,
            "confidence": 0.74 if frame else 0.7,
        }
    project = callbacks.canonical_price_project(explicit_project or callbacks.contextual_price_project(state or {}))
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
    local_rows = callbacks.pricing_rows(tool_results)
    local_first = _should_prefer_local_pricing(content, project)
    source_rows = local_rows if local_first else (kb_rows or local_rows)
    if local_first and not source_rows:
        source_rows = kb_rows
    rows = callbacks.filter_pricing_rows_for_project(source_rows, project)
    facts: list[str] = []
    reply_points: list[str] = []
    missing_slots: list[str] = []

    if rows:
        row = rows[0]
        name = _customer_facing_price_name(content, project, row)
        price_bits = callbacks.price_bits(row)
        facts.extend(_row_price_facts(name, row, content) or price_bits)
        has_price = bool(price_bits)
        reply_points.extend(
            _price_reply_instructions(
                has_objection=callbacks.has_price_objection(content),
                has_price=has_price,
                project=project or name,
            )
        )
        if frame:
            _apply_price_frame_guidance(facts, reply_points, frame)
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
        if frame:
            _apply_price_frame_guidance(facts, reply_points, frame)
    else:
        facts.append("暂未查到明确可引用价格")
        reply_points.extend(
            _price_reply_instructions(
                has_objection=callbacks.has_price_objection(content),
                has_price=False,
                project=project,
            )
        )
        if frame:
            _apply_price_frame_guidance(facts, reply_points, frame)

    if frame and frame.missing_slot:
        missing_slots.append(frame.missing_slot)
    if not project and not rows and not missing_slots and not frame:
        missing_slots.append("项目名称")
    _inject_sales_talk_guidance(facts, reply_points, sales_talk)

    return {
        "skill": "price_consult",
        "intent": "price_inquiry",
        "facts": facts,
        "reply_points": reply_points,
        "missing_slots": list(dict.fromkeys(item for item in missing_slots if item)),
        "risk_flags": callbacks.price_risk_terms(content),
        "suggested_next_step": frame.suggested_next_step if frame else ("预算异议承接" if callbacks.has_price_objection(content) else "确认项目配置" if project or rows else "补充项目名称"),
        "confidence": 0.8 if rows and frame else 0.78 if rows else 0.68 if frame else 0.62,
    }


def _inject_sales_talk_guidance(
    facts: list[str],
    reply_points: list[str],
    sales_talk: dict[str, str],
) -> None:
    if not sales_talk:
        return
    if sales_talk.get("scene_type"):
        facts.append(f"销售话术场景：{sales_talk['scene_type']}")
    if sales_talk.get("target"):
        facts.append(f"承接目标：{sales_talk['target']}")
    if sales_talk.get("sample_reply"):
        insert_at = 0
        if reply_points and any(term in reply_points[0] for term in ["必须", "先回答", "本轮"]):
            insert_at = min(3, len(reply_points))
        reply_points.insert(insert_at, f"优先参考这种价格承接节奏：{sales_talk['sample_reply']}")
    if sales_talk.get("next_step"):
        reply_points.append(f"下一步建议：{sales_talk['next_step']}")
    if sales_talk.get("forbidden"):
        facts.append(f"禁用表达：{sales_talk['forbidden']}")


def _customer_facing_price_name(content: str, project: str, row: dict[str, Any]) -> str:
    text = str(content or "")
    project_code = str(row.get("project_code") or project or "").upper()
    if any(term in text for term in ["黑色素", "色素沉着", "色沉"]):
        return "黑色素这个情况"
    if any(term in text for term in ["祛斑", "淡斑", "斑点", "斑"]):
        return "祛斑这个情况"
    if any(term in text for term in ["暗沉", "肤色不均", "提亮"]):
        return "肤色改善这个情况"
    if any(term in text for term in ["补水", "干", "缺水", "卡粉"]):
        return "补水护理这个情况"
    if any(term in text for term in ["抗衰", "紧致", "松弛", "皱纹", "细纹"]):
        return "抗衰紧致这个情况"
    if any(term in text for term in ["塑形", "轮廓", "下颌线"]):
        return "轮廓塑形这个情况"
    if project_code == "S10":
        return "祛斑这个情况"
    if project_code == "S10N":
        return "补水护理这个情况"
    if project_code in {"K10", "K10F"}:
        return "抗衰紧致这个情况"
    if project_code == "M10":
        return "轮廓塑形这个情况"
    return "你这个情况"


def _should_prefer_local_pricing(content: str, project: str) -> bool:
    text = str(content or "")
    normalized = str(project or "").upper()
    if normalized in {"S10", "S10N", "K10", "K10F", "M10", "F10"}:
        return True
    return any(
        term in text
        for term in [
            "祛斑",
            "淡斑",
            "黑色素",
            "色素",
            "色沉",
            "肤色不均",
            "抗衰",
            "紧致",
            "松弛",
            "补水",
            "缺水",
            "干燥",
            "塑形",
            "轮廓",
        ]
    )
