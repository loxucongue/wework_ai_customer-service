"""Present Reply evidence in a sales-readable order without changing authority."""
from __future__ import annotations

import re
from collections.abc import Iterable


_SECTION = re.compile(r"^【([^】]+)】\n([\s\S]*)$")


def organize_reply_context(blocks: Iterable[str]) -> str:
    """Group existing evidence; every source block is retained exactly once."""
    groups: dict[str, list[tuple[str, str]]] = {
        "当前对话": [], "已知客户情况": [], "已交付内容": [],
        "相关事实与执行边界": [], "候选论据与销售方向": [], "输出要求": [],
    }
    for block in blocks:
        value = str(block or "").strip()
        if not value:
            continue
        match = _SECTION.match(value)
        if not match:
            groups["输出要求"].append(("", value))
            continue
        title, body = match.groups()
        if title in {"当前时间", "完整聊天"}:
            group = "当前对话"
        elif "已交付内容" in title:
            group = "已交付内容"
        elif title.startswith(("当前结构事实", "本轮平台结构事件", "已付登记", "上一轮策略状态")):
            group = "已知客户情况"
        elif title.startswith(("销售主线机会", "Router 辅助", "跟进序列与话术", "已发布 AI 销售策略", "本轮租户逼单")):
            group = "候选论据与销售方向"
        elif title.startswith(("输出引用", "本轮客户可见输出上限")):
            group = "输出要求"
        else:
            group = "相关事实与执行边界"
        groups[group].append((title, body.strip() or "无"))
    rendered = []
    for group, items in groups.items():
        if not items:
            continue
        body = "\n\n".join((f"[{title}]\n" if title else "") + value for title, value in items)
        rendered.append(f"【{group}】\n{body}")
    return "\n\n".join(rendered)
