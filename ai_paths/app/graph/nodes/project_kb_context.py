from __future__ import annotations

import re
from collections.abc import Callable

from app.graph.nodes.project_kb_parsing import project_slices_from_tool_results
from app.graph.state import AgentState


def business_project_slices(
    project_slices: list[dict[str, str]],
    state: AgentState | None = None,
    *,
    known_visible_concerns_from_state: Callable[[AgentState], list[str]] | None = None,
) -> list[dict[str, str]]:
    business: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    seen_replacements: set[str] = set()
    for item in project_slices:
        scene_type = str(item.get("scene_type") or "").strip()
        replacement = str(item.get("replacement_name") or "").strip()
        if scene_type == "global_compliance_terms" or replacement == "合规项目表达":
            continue
        if replacement and not is_business_project_direction_name(replacement):
            continue
        replacement_key = re.sub(r"\s+", "", replacement)
        if replacement_key and replacement_key in seen_replacements:
            continue
        if state is not None and not project_slice_relevant_to_current_need(
            item,
            state,
            known_visible_concerns_from_state=known_visible_concerns_from_state,
        ):
            continue
        if item.get("direction") or item.get("reply_point") or item.get("say"):
            key = (
                re.sub(r"\s+", "", replacement),
                re.sub(r"\s+", "", str(item.get("direction") or item.get("reply_point") or item.get("say") or ""))[:80],
            )
            if key in seen:
                continue
            seen.add(key)
            if replacement_key:
                seen_replacements.add(replacement_key)
            business.append(item)
    return business


def case_request_lacks_specific_context(
    state: AgentState,
    *,
    known_visible_concerns_from_state: Callable[[AgentState], list[str]] | None = None,
) -> bool:
    intents = {str(item.get("intent") or "") for item in (state.get("intents") or []) if isinstance(item, dict)}
    if "case_request" not in intents:
        return False
    content = str(state.get("normalized_content") or "")
    image_info = state.get("image_info") or {}
    known_visible_items = known_visible_concerns_from_state(state) if known_visible_concerns_from_state else []
    visible_text = " ".join(map(str, list(image_info.get("visible_concerns") or []) + list(known_visible_items)))
    context_text = " ".join(
        part
        for part in [
            content,
            visible_text,
            str(image_info.get("image_desc") or ""),
            _recent_business_context(state),
            _profile_context(state),
        ]
        if part
    )
    specific_terms = [
        "斑",
        "色沉",
        "肤色",
        "暗沉",
        "毛孔",
        "黑头",
        "痘",
        "痘印",
        "痘坑",
        "泛红",
        "敏感",
        "修护",
        "屏障",
        "松弛",
        "抗衰",
        "轮廓",
        "水光",
        "光子",
        "皮秒",
        "热玛吉",
        "超声炮",
    ]
    return not any(term in context_text for term in specific_terms)


def project_slice_relevant_to_current_need(
    item: dict[str, str],
    state: AgentState,
    *,
    known_visible_concerns_from_state: Callable[[AgentState], list[str]] | None = None,
) -> bool:
    content = str(state.get("normalized_content") or "")
    image_info = state.get("image_info") or {}
    known_visible_items = known_visible_concerns_from_state(state) if known_visible_concerns_from_state else []
    known_visible = " ".join(map(str, known_visible_items))
    image_visible = " ".join(map(str, image_info.get("visible_concerns") or []))
    need_text = " ".join(
        part
        for part in [
            content,
            known_visible,
            image_visible,
            str(image_info.get("image_desc") or ""),
            _recent_business_context(state),
            _profile_context(state),
        ]
        if part
    )
    slice_text = " ".join(
        str(item.get(key) or "")
        for key in ("title", "replacement_name", "direction", "reply_point", "say")
    )
    need_groups = [
        (["斑", "点状", "色沉", "肤色不均", "暗沉"], ["斑", "色沉", "色素", "肤色", "淡化", "美白", "暗沉"]),
        (["毛孔", "出油", "黑头"], ["毛孔", "出油", "黑头", "肤质"]),
        (["痘印", "痘坑", "闭口", "痘痘"], ["痘印", "痘坑", "闭口", "痘痘", "肤质"]),
        (["敏感", "泛红", "屏障", "红血丝"], ["敏感", "泛红", "屏障", "修护", "舒缓"]),
        (["松弛", "法令纹", "抗衰", "轮廓"], ["松弛", "提升", "抗衰", "轮廓", "紧致"]),
    ]
    for need_terms, allowed_terms in need_groups:
        if any(term in need_text for term in need_terms):
            return any(term in slice_text for term in allowed_terms)
    return True


def project_direction_name_candidates(name: str) -> list[str]:
    text = str(name or "").strip()
    if not text:
        return []
    parts = re.split(r"\s*(?:/|｜|\||、|，|,|；|;)\s*", text)
    return [part for part in (part.strip() for part in parts) if is_business_project_direction_name(part)]


def _recent_business_context(state: AgentState) -> str:
    history = state.get("conversation_history") or []
    if not isinstance(history, list):
        return ""
    texts: list[str] = []
    for item in history[-8:]:
        if isinstance(item, str):
            texts.append(item)
        elif isinstance(item, dict):
            texts.append(str(item.get("content") or item.get("text") or ""))
    return " ".join(text for text in texts if text)


def _profile_context(state: AgentState) -> str:
    memory_policy = state.get("memory_usage_policy") or {}
    if isinstance(memory_policy, dict) and memory_policy.get("active_profile_memory") is False:
        return ""
    chunks: list[str] = []
    for key in ("portrait", "customer_profile", "profile"):
        value = state.get(key)
        if isinstance(value, dict):
            for field in ("summary", "needs", "pain_points", "projects", "concerns"):
                part = value.get(field)
                if isinstance(part, list):
                    chunks.extend(str(item) for item in part[:6])
                elif part:
                    chunks.append(str(part))
    return " ".join(chunk for chunk in chunks if chunk)


def is_business_project_direction_name(name: str) -> bool:
    text = str(name or "").strip()
    if not text or text in {"合规项目表达", "global_compliance_terms"}:
        return False
    generic_names = {
        "优先级方案",
        "对应方向",
        "对应项目方向",
        "可考虑方向",
        "项目方向",
        "改善方向",
        "方案",
    }
    if text in generic_names:
        return False
    noisy_terms = [
        "项目名自然承接",
        "图片初步分析",
        "初步方向",
        "初步分析",
        "替换词名称",
        "可考虑方向",
        "回复要点",
        "话术",
        "案例",
        "优先级",
        "vs",
        "VS",
    ]
    return not any(term in text for term in noisy_terms)
