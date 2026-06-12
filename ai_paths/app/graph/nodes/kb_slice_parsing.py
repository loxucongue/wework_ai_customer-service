from __future__ import annotations

import re
from typing import Any


KNOWN_LABELS = {
    "场景类型",
    "检索关键词",
    "客户典型说法",
    "替换名称",
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


def extract_label(content: str, label: str) -> str:
    match = re.search(rf"^{re.escape(label)}[：:]\s*(.+)$", str(content or ""), flags=re.MULTILINE)
    return match.group(1).strip() if match else ""


def extract_label_block(content: str, label: str) -> str:
    """Extract a short labeled block from a KB slice without making business decisions."""
    lines = [line.rstrip() for line in str(content or "").splitlines()]
    result: list[str] = []
    capture = False
    for raw in lines:
        line = raw.strip()
        if not line:
            if capture and result:
                break
            continue
        label_match = re.match(r"^([^：:]{1,30})[：:]\s*(.*)$", line)
        if label_match:
            current_label = label_match.group(1).strip()
            value_text = label_match.group(2).strip()
            if current_label == label:
                capture = True
                if value_text:
                    result.append(value_text)
                continue
            if capture and current_label in KNOWN_LABELS:
                break
        elif capture:
            if line.startswith("## ") or line.startswith("切片") or line.startswith("###"):
                break
            result.append(line.lstrip("- ").strip())
            if len(" ".join(result)) >= 260:
                break
    return " ".join(part for part in result if part).strip(" “”")


def parse_price_kb_content(content: str) -> dict[str, str]:
    del content
    return {}


def pricing_rows_from_kb(tool_results: dict[str, Any]) -> list[dict[str, Any]]:
    del tool_results
    return []
