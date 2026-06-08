from __future__ import annotations

from typing import Any


TYPE_FOLLOWUP_GROUPS = {
    "spot_sparse": ["零散小点", "零散斑点", "小点为主", "点状为主", "小点多"],
    "spot_patchy": ["成片颜色重", "一片一片", "成片", "颜色重一点", "颜色比较重"],
    "tone_dull": ["整体肤色暗沉不均", "整体暗沉", "肤色不均", "暗沉不均", "肤色发暗"],
    "lift_loose": ["脸有点松", "轮廓没以前紧", "松弛为主", "脸有点垮", "下颌线不清晰"],
    "wrinkle_lines": ["法令纹", "嘴角纹", "苹果肌下移", "纹路更明显", "细纹明显"],
    "hydrate_dry": ["干燥缺水", "上妆卡粉", "起皮发干", "皮肤发干", "很缺水"],
    "hydrate_dull": ["肤色发闷没光泽", "没光泽", "肤色发闷", "脸有点发闷"],
    "pore_oily": ["毛孔粗", "出油黑头", "黑头明显", "出油多", "毛孔明显"],
    "acne_marks": ["痘印痘坑", "痘印", "痘坑", "闭口痘痘"],
}


def customer_friendly_type_question(
    content: str,
    *,
    visible_concerns: list[Any] | None = None,
) -> str:
    text = " ".join(
        [
            str(content or "").strip(),
            " ".join(str(item or "").strip() for item in (visible_concerns or []) if str(item or "").strip()),
        ]
    ).strip()
    if not text:
        return ""
    if _contains_any(text, ["祛斑", "淡斑", "黑色素", "色沉", "肤色不均", "暗沉", "斑"]):
        return "你这个更像零散小点、成片颜色重一点，还是整体肤色暗沉不均？"
    if _contains_any(text, ["抗衰", "松弛", "下垂", "法令纹", "轮廓", "提升", "垮"]):
        return "你更像是脸有点松、轮廓没以前紧，还是法令纹、嘴角这些纹路更明显？"
    if _contains_any(text, ["补水", "干燥", "缺水", "卡粉", "起皮", "发干"]):
        return "你更偏干燥缺水、上妆卡粉，还是整体肤色发闷没光泽？"
    if _contains_any(text, ["毛孔", "出油", "黑头", "痘印", "痘坑", "闭口", "痘"]):
        return "你更在意毛孔粗、出油黑头，还是痘印痘坑这些问题？"
    return ""


def customer_friendly_direction_label(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    replacements = [
        ("针对性色素淡化类项目", "淡斑改善方向"),
        ("多维淡斑管理方案", "淡斑改善方向"),
        ("整体肤色改善方案", "整体提亮方向"),
        ("整体肤色改善", "整体提亮方向"),
        ("紧致提升类项目", "紧致提升方向"),
        ("补水修护类项目", "补水修护方向"),
        ("抗衰类项目", "紧致提升方向"),
    ]
    for old, new in replacements:
        if old in text:
            text = text.replace(old, new)
    for suffix in ["类项目", "项目", "方案"]:
        if text.endswith(suffix):
            text = text[: -len(suffix)].rstrip()
    alias_map = {
        "淡斑": "淡斑改善方向",
        "提亮": "整体提亮方向",
        "紧致提升": "紧致提升方向",
        "补水修护": "补水修护方向",
    }
    return alias_map.get(text, text)


def detect_customer_need_type_followup(content: str) -> str:
    text = str(content or "").strip()
    if not text:
        return ""
    for label, phrases in TYPE_FOLLOWUP_GROUPS.items():
        if any(phrase in text for phrase in phrases):
            return label
    return ""


def is_customer_need_type_followup(content: str) -> bool:
    return bool(detect_customer_need_type_followup(content))


def _contains_any(text: str, terms: list[str]) -> bool:
    return any(term in text for term in terms)
