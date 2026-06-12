from __future__ import annotations

import re
from typing import Any

from app.policies.constants import PRICE_PROJECT_ALIASES, PROJECT_KEYWORDS


def is_broad_price_category(project: str) -> bool:
    return str(project or "").strip() in {
        "淡斑",
        "祛斑",
        "斑点",
        "色沉",
        "肤色不均",
        "毛孔",
        "痘印",
        "痘坑",
        "抗衰",
        "紧致",
    }


def pricing_sql_for_project(project: str) -> str:
    # Historical compatibility only. Runtime pricing now uses pricing_rules.
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


def value(raw_value: Any) -> str:
    text = str(raw_value or "").strip()
    if not text or text in {"0", "0.00", "None", "null"}:
        return ""
    if re.fullmatch(r"\d+(\.0+)?", text):
        text = text.split(".")[0]
    return f"{text}元" if text.isdigit() else text
