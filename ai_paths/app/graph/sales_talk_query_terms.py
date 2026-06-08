from __future__ import annotations

from typing import Iterable


SALES_TALK_QUERY_MAP = {
    "祛斑": ["祛斑", "淡斑", "色沉", "效果对比", "案例", "到店承接"],
    "淡斑": ["淡斑", "祛斑", "色沉", "效果对比", "案例", "到店承接"],
    "黑色素": ["黑色素", "色沉", "淡斑", "祛斑", "效果对比", "案例承接"],
    "色沉": ["色沉", "淡斑", "祛斑", "肤色不均", "效果对比", "案例承接"],
    "暗沉": ["暗沉", "提亮", "肤色不均", "效果对比", "案例承接"],
    "抗衰": ["抗衰", "紧致提升", "松弛", "法令纹", "轮廓", "效果对比"],
    "松弛": ["松弛", "抗衰", "紧致提升", "轮廓", "法令纹", "效果对比"],
    "提升": ["提升", "紧致提升", "抗衰", "轮廓", "法令纹", "效果对比"],
    "补水": ["补水", "保湿", "干燥缺水", "肤质改善", "效果对比"],
    "毛孔": ["毛孔", "出油", "黑头", "肤质改善", "效果对比"],
}


def expand_sales_talk_query_terms(need_hint: str, base_terms: Iterable[str] | None = None) -> list[str]:
    result: list[str] = []
    for value in base_terms or []:
        term = str(value or "").strip()
        if term and term not in result:
            result.append(term)
    hint = str(need_hint or "").strip()
    if hint:
        if hint not in result:
            result.append(hint)
        for mapped in SALES_TALK_QUERY_MAP.get(hint, []):
            if mapped not in result:
                result.append(mapped)
    return result
