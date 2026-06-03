from __future__ import annotations

from typing import Any


def trust_skill_output(content: str, tool_results: dict[str, Any]) -> dict[str, Any]:
    """Build factual trust-skill output for the final reply model."""

    trust_result = tool_results.get("trust_assets", {}) if isinstance(tool_results, dict) else {}
    items = trust_result.get("items", []) if isinstance(trust_result, dict) else []
    if not isinstance(items, list):
        items = []

    facts = [f"资质/背书资料命中{len(items)}条"] if items else ["暂未命中可直接引用的资质/背书资料"]
    reply_points = [
        "客户担心正规性时先认可谨慎，再从资质、产品来源、服务保障几个维度解释。",
        "如果有可用图片资料，只能发送知识库返回的真实图片链接，不编造资质或案例。",
    ]
    if "AI" in content or "ai" in content or "机器人" in content:
        reply_points.append("本轮询问身份时，不要争辩身份，保持小贝服务口吻并说明会同步专业同事确认具体问题。")

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
