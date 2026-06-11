from __future__ import annotations

from typing import Any

from app.graph.signals.dispute import is_soft_fee_concern
from app.graph.nodes.common import dedupe_strings


def intent_level(intents: set[Any], content: str) -> str:
    appointment_signal = bool(intents & {"appointment_confirm", "appointment_intent"}) or any(
        term in content for term in ["想约", "预约", "到店", "过去看看"]
    )
    if appointment_signal and not is_soft_fee_concern(content):
        return "strong"
    if intents & {"price_inquiry", "campaign_inquiry", "image_inquiry", "project_inquiry", "store_inquiry"}:
        return "medium"
    if intents & {"trust_issue", "competitor_compare"}:
        return "medium"
    return "weak"


def decision_stage(intents: set[Any], content: str) -> str:
    appointment_signal = bool(intents & {"appointment_confirm", "appointment_intent"}) or any(
        term in content for term in ["想约", "预约", "到店", "过去看看"]
    )
    if appointment_signal and not is_soft_fee_concern(content):
        return "考虑到店"
    if intents & {"competitor_compare"}:
        return "对比中"
    if intents & {"price_inquiry", "campaign_inquiry"}:
        return "预算评估中"
    if intents & {"image_inquiry"}:
        return "看图评估中"
    return "了解中"


def profile_summary(needs: list[str], pain_points: list[str], projects: list[str], concerns: list[str]) -> str:
    parts: list[str] = []
    if pain_points:
        parts.append(f"关注{'、'.join(dedupe_strings(pain_points)[:3])}")
    if needs:
        parts.append(f"希望{'、'.join(dedupe_strings(needs)[:3])}")
    if projects:
        parts.append(f"提到项目{'、'.join(dedupe_strings(projects)[:3])}")
    if concerns:
        parts.append(f"顾虑{'、'.join(dedupe_strings(concerns)[:2])}")
    return "，".join(parts) + "。" if parts else ""
