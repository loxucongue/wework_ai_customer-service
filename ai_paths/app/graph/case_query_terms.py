from __future__ import annotations

from typing import Iterable


CASE_QUERY_TERM_MAP = {
    "祛斑": ["祛斑", "淡斑", "斑点", "色沉", "肤色改善", "面部", "脸部", "面颊"],
    "淡斑": ["淡斑", "祛斑", "斑点", "色沉", "肤色改善", "面部", "脸部", "面颊"],
    "黑色素": ["黑色素", "色沉", "淡斑", "祛斑", "肤色改善", "面部", "脸部", "面颊"],
    "色沉": ["色沉", "黑色素", "淡斑", "祛斑", "肤色改善", "面部", "脸部", "面颊"],
    "暗沉": ["暗沉", "提亮", "肤色不均", "肤色改善", "面部", "脸部", "面颊"],
    "抗衰": ["抗衰", "紧致", "提升", "松弛", "法令纹", "轮廓", "面部", "脸部"],
    "松弛": ["抗衰", "紧致", "提升", "松弛", "下垂", "轮廓", "面部", "脸部"],
    "提升": ["抗衰", "紧致", "提升", "轮廓", "法令纹", "下颌线", "面部", "脸部"],
    "补水": ["补水", "保湿", "干燥", "缺水", "肤质改善", "面部", "脸部"],
    "毛孔": ["毛孔", "出油", "黑头", "肤质改善", "面部", "脸部"],
}

CASE_QUERY_FALLBACK_MAP = {
    "祛斑": [["面部", "淡斑", "提亮", "案例", "前后对比"], ["色沉", "肤色改善", "案例", "效果"]],
    "淡斑": [["面部", "淡斑", "提亮", "案例", "前后对比"], ["色沉", "肤色改善", "案例", "效果"]],
    "黑色素": [["面部", "淡斑", "提亮", "案例", "前后对比"], ["色沉", "肤色改善", "案例", "效果"]],
    "色沉": [["面部", "淡斑", "提亮", "案例", "前后对比"], ["色沉", "肤色改善", "案例", "效果"]],
    "暗沉": [["面部", "提亮", "肤色改善", "案例", "前后对比"], ["暗沉", "肤色改善", "案例", "效果"]],
    "抗衰": [["面部", "紧致", "提升", "案例", "前后对比"], ["轮廓", "法令纹", "提升", "案例", "效果"]],
    "松弛": [["面部", "紧致", "提升", "案例", "前后对比"], ["轮廓", "下垂", "提升", "案例", "效果"]],
    "提升": [["面部", "紧致", "提升", "案例", "前后对比"], ["轮廓", "法令纹", "提升", "案例", "效果"]],
    "补水": [["面部", "补水", "保湿", "案例", "前后对比"], ["肤质改善", "干燥", "补水", "案例"]],
    "毛孔": [["面部", "毛孔", "肤质改善", "案例", "前后对比"], ["出油", "黑头", "毛孔", "案例"]],
}


def expand_case_query_terms(need_hint: str, base_terms: Iterable[str] | None = None) -> list[str]:
    result: list[str] = []
    for value in base_terms or []:
        term = str(value or "").strip()
        if term and term not in result:
            result.append(term)
    hint = str(need_hint or "").strip()
    if hint:
        if hint not in result:
            result.append(hint)
        for mapped in CASE_QUERY_TERM_MAP.get(hint, []):
            if mapped not in result:
                result.append(mapped)
    return result


def build_case_query_candidates(
    need_hint: str,
    *,
    base_terms: Iterable[str] | None = None,
    body_part: str = "",
    face_hint: bool = False,
) -> list[str]:
    primary_terms = expand_case_query_terms(need_hint, base_terms)
    if body_part and body_part not in {"无", "未知"} and body_part not in primary_terms:
        primary_terms.append(body_part)
    if face_hint:
        for term in ["面部", "脸部", "面颊"]:
            if term not in primary_terms:
                primary_terms.append(term)
    for term in ["案例", "效果", "前后对比", "改善参考"]:
        if term not in primary_terms:
            primary_terms.append(term)

    candidates: list[str] = [" ".join(primary_terms[:12]).strip()]
    for fallback_terms in CASE_QUERY_FALLBACK_MAP.get(str(need_hint or "").strip(), []):
        merged = list(primary_terms[:4])
        for term in fallback_terms:
            if term not in merged:
                merged.append(term)
        candidate = " ".join(merged[:10]).strip()
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates[:3]
