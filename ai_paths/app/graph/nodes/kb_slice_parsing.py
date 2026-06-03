from __future__ import annotations

import re
from typing import Any

from app.graph.nodes.pricing_context import value


def extract_label(content: str, label: str) -> str:
    match = re.search(rf"{re.escape(label)}：([^\n\r]+)", str(content or ""))
    return match.group(1).strip() if match else ""


def extract_label_block(content: str, label: str) -> str:
    """Extract a short labeled block from a KB slice without making business decisions."""
    lines = [line.rstrip() for line in str(content or "").splitlines()]
    labels = {
        "场景类型",
        "检索关键词",
        "客户典型说法",
        "替换词名称",
        "可考虑方向",
        "项目定位",
        "核心逻辑",
        "适用判断",
        "判断逻辑",
        "不适合/慎做",
        "慎做情况",
        "回复要点",
        "可说话术",
        "禁用表达",
        "下一步追问",
        "下一步动作",
        "风险信号",
        "产品参考",
        "可结合产品方向",
        "风险提示",
        "适用情况",
        "不适用情况",
        "价格说明",
        "操作流程",
        "护理建议",
        "需收集信息",
    }
    result: list[str] = []
    capture = False
    for raw in lines:
        line = raw.strip()
        if not line:
            if capture and result:
                break
            continue
        if "：" in line:
            current_label, value_text = line.split("：", 1)
            current_label = current_label.strip()
            if current_label == label:
                capture = True
                if value_text.strip():
                    result.append(value_text.strip())
                continue
            if capture and current_label in labels:
                break
        elif capture:
            if line.startswith("## ") or line == "！！！" or line.startswith("切片") or line.startswith("###"):
                break
            result.append(line.lstrip("- ").strip())
            if len(" ".join(result)) >= 260:
                break
    return " ".join(part for part in result if part).strip(" “”")


def parse_price_kb_content(content: str) -> dict[str, str]:
    mapping = {
        "项目名称": "project_name",
        "日常单次价": "daily_price",
        "新客体验价": "new_price",
        "老客单次价": "old_price",
        "老客推荐卡项": "old_card",
        "活动价": "promo_price",
        "活动适用人群": "promo_target",
        "可赠送福利": "gift_item",
        "福利触发场景": "gift_scene",
        "报价备注": "price_note",
    }
    row: dict[str, str] = {}
    for raw_line in str(content or "").splitlines():
        line = raw_line.strip()
        if "：" not in line:
            continue
        label, value_text = line.split("：", 1)
        key = mapping.get(label.strip())
        if key:
            row[key] = value_text.strip()
    return row


def pricing_rows_from_kb(tool_results: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    items = tool_results.get("project_price", {}).get("items") or []
    for item in items:
        if not isinstance(item, dict):
            continue
        row = parse_price_kb_content(str(item.get("content") or ""))
        if row and any(value(row.get(key)) for key in ["new_price", "promo_price", "daily_price", "old_price"]):
            rows.append(dict(row, _source="project_price_kb"))
    return rows
