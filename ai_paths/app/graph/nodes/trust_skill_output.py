from __future__ import annotations

from typing import Any

from app.graph.nodes.sales_talk_kb_parsing import first_sales_talk_slice


def trust_skill_output(content: str, tool_results: dict[str, Any]) -> dict[str, Any]:
    """Build factual trust-skill output for the final reply model."""

    sales_talk = first_sales_talk_slice(tool_results)
    trust_result = tool_results.get("trust_assets", {}) if isinstance(tool_results, dict) else {}
    items = trust_result.get("items", []) if isinstance(trust_result, dict) else []
    if not isinstance(items, list):
        items = []
    case_result = tool_results.get("case_studies", {}) if isinstance(tool_results, dict) else {}
    case_items = case_result.get("items", []) if isinstance(case_result, dict) else []
    if not isinstance(case_items, list):
        case_items = []

    facts = [f"资质/背书资料命中{len(items)}条"] if items else ["暂未命中可直接引用的资质/背书资料"]
    reply_points = [
        "客户担心正规性时先给肯定式安全感，再解释资质、开店年限、到店可核验和收费透明。",
        "资质信任类场景只通过文字建立信任，不发送营业执照、证照或任何资质图片素材。",
        "话术要像活动负责人，不像审核客服，可以自然说“都是有资质的，到店都看得到”“没有资质也开不了这么多年”。",
        "回答完信任顾虑后，可以顺带轻推进到店核验，例如让客户先过来看看门店和检测流程。",
    ]
    if case_items:
        facts.append(f"效果案例素材库命中{len(case_items)}条，可作为同类改善参考；不能承诺客户也会达到相同变化。")
        reply_points.append("如果客户质疑的是效果而不是资质，可以用文字提到有同类改善参考，但资质信任问题本轮不发图片。")
    if "AI" in content or "ai" in content or "机器人" in content:
        reply_points.append("本轮询问身份时，不要争辩身份，保持公司前端客服口吻并说明会同步专业同事确认具体问题。")
    if sales_talk.get("scene_type"):
        facts.append(f"销售话术场景：{sales_talk['scene_type']}")
    if sales_talk.get("target"):
        facts.append(f"承接目标：{sales_talk['target']}")
    if sales_talk.get("sample_reply"):
        reply_points.insert(0, f"优先参考这种信任承接节奏：{sales_talk['sample_reply']}")
    if sales_talk.get("next_step"):
        reply_points.append(f"下一步建议：{sales_talk['next_step']}")
    if sales_talk.get("forbidden"):
        facts.append(f"禁用表达：{sales_talk['forbidden']}")

    return {
        "skill": "trust_build",
        "intent": "trust_issue",
        "facts": facts,
        "reply_points": reply_points,
        "missing_slots": [],
        "risk_flags": [],
        "suggested_next_step": "提供资质和服务保障说明",
        "confidence": 0.76 if items else 0.58,
    }
