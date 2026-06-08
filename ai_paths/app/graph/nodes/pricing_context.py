from __future__ import annotations

import re
from typing import Any

from app.policies.constants import PRICE_PROJECT_ALIASES, PROJECT_KEYWORDS


def price_point_from_row(name: str, row: dict[str, Any], *, requested_project: str = "", source: str = "") -> str:
    prefix = ""
    if requested_project and requested_project not in name and source == "local_xlsx":
        if requires_exact_price(requested_project):
            return f"当前价格表没看到{requested_project}单项，先不拿其他淡斑产品价格代替报价。"
        prefix = f"当前价格表没看到{requested_project}单项，淡斑相关配置里，"
    new_price = value(row.get("new_price"))
    promo_price = value(row.get("promo_price"))
    daily_price = value(row.get("daily_price"))
    note = str(row.get("price_note") or "")
    range_match = re.search(r"参考价[:：]?([0-9]+(?:\.[0-9]+)?-[0-9]+(?:\.[0-9]+)?)", note)
    if range_match:
        return f"{prefix}{name}可以先按参考价{range_match.group(1)}做预算参考。"
    if new_price and promo_price:
        return f"{prefix}{name}可以先按新客体验价{new_price}、活动价{promo_price}做预算参考。"
    if new_price:
        return f"{prefix}{name}可以先按新客体验价{new_price}做预算参考。"
    if promo_price:
        return f"{name}可以先按活动价{promo_price}做预算参考。"
    if daily_price:
        return f"{name}日常单次价是{daily_price}，可以先作为预算参考。"
    return f"{name}价格需要结合当前配置确认。"


def filter_pricing_rows_for_project(rows: list[dict[str, Any]], project: str) -> list[dict[str, Any]]:
    project = canonical_price_project(project)
    if is_broad_price_category(project):
        return []
    if not project or not requires_exact_price(project):
        return rows
    aliases = price_project_aliases(project)
    filtered: list[dict[str, Any]] = []
    for row in rows:
        haystack = " ".join(str(row.get(key) or "") for key in ["project_name", "price_note", "promo_target", "gift_scene"])
        if any(alias and alias in haystack for alias in aliases):
            filtered.append(row)
    return filtered


def price_project_aliases(project: str) -> list[str]:
    aliases = {
        "光子嫩肤": ["光子嫩肤", "光子"],
        "光子": ["光子", "光子嫩肤"],
        "皮秒": ["皮秒"],
        "热玛吉": ["热玛吉"],
        "超声炮": ["超声炮"],
    }
    return aliases.get(project, [project])


def pricing_rows(tool_results: dict[str, Any]) -> list[dict[str, Any]]:
    local_rows = tool_results.get("pricing_local", {}).get("rows") or []
    if local_rows:
        return [dict(row, _source=row.get("_source") or "local_pricing_rules") for row in local_rows if isinstance(row, dict)]
    rows = tool_results.get("pricing_db", {}).get("rows") or []
    return [dict(row, _source="coze_db") for row in rows if isinstance(row, dict)]


def requires_exact_price(project: str) -> bool:
    return project in {"皮秒", "光子", "光子嫩肤", "热玛吉", "超声炮"}


def is_broad_price_category(project: str) -> bool:
    return str(project or "").strip() in {"淡斑", "祛斑", "斑", "色沉", "肤色不均", "毛孔", "痘印", "痘坑", "抗衰", "紧致"}


def pricing_sql_for_project(project: str) -> str:
    if not project:
        return "SELECT * FROM items_pricing_system WHERE 1=0"
    escaped = project.replace("'", "''")
    return f"SELECT * FROM items_pricing_system WHERE project_name LIKE '%{escaped}%' AND status='true' ORDER BY id LIMIT 10"


def extract_project(content: str) -> str:
    for word in PROJECT_KEYWORDS:
        if word in content:
            return word
    return ""


def canonical_price_project(project: str) -> str:
    project = str(project or "").strip()
    return PRICE_PROJECT_ALIASES.get(project, project)


def price_bits(row: dict[str, Any]) -> list[str]:
    name = row.get("project_name", "相关项目")
    result = []
    for key, label in [("new_price", "新客体验价"), ("promo_price", "活动价"), ("daily_price", "日常单次价"), ("old_price", "老客单次价")]:
        item_value = value(row.get(key))
        if item_value:
            result.append(f"{name}{label}{item_value}")
    return result


def price_fact_for_brief(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_name": row.get("project_name") or row.get("price_note") or "相关项目",
        "new_price": value(row.get("new_price")),
        "promo_price": value(row.get("promo_price")),
        "daily_price": value(row.get("daily_price")),
        "old_price": value(row.get("old_price")),
        "old_card": str(row.get("old_card") or "").strip(),
        "promo_target": str(row.get("promo_target") or "").strip(),
        "price_note": str(row.get("price_note") or "").strip(),
        "source": row.get("_source") or "",
    }


def price_risk_terms(content: str) -> list[str]:
    terms = []
    for word in ["底价", "最低价", "再便宜", "便宜点", "太贵", "贵了", "预算不够", "别家", "同价", "活动价", "套餐", "半脸", "未成年"]:
        if word in content:
            terms.append(word)
    return terms


def value(raw_value: Any) -> str:
    text = str(raw_value or "").strip()
    if not text or text in {"0", "0.00", "None", "null"}:
        return ""
    if re.fullmatch(r"\d+(\.0+)?", text):
        text = text.split(".")[0]
    return f"{text}元" if text.isdigit() else text
