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


def organize_rendered_reply_context(rendered: str) -> str:
    """Organize a persisted renderer result without splitting paragraphs in a section."""
    rendered = _compact_persisted_knowledge_candidates(str(rendered or ""))
    starts = list(re.finditer(r"(?m)^【[^】]+】\n", str(rendered or "")))
    if not starts:
        return str(rendered or "").strip()
    blocks: list[str] = []
    prefix = rendered[: starts[0].start()].strip()
    if prefix:
        blocks.append(prefix)
    for index, start in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(rendered)
        blocks.append(rendered[start.start() : end].strip())
    return organize_reply_context(blocks)


def _compact_persisted_knowledge_candidates(rendered: str, *, limit: int = 3) -> str:
    """Make historical renderer input match the current three-angle candidate budget."""
    lines = rendered.splitlines()
    starts = [index for index, line in enumerate(lines) if line.startswith("话术ID=")]
    if len(starts) <= limit:
        return rendered
    chosen: set[int] = set()
    seen: set[tuple[str, str]] = set()
    for index in starts:
        line = lines[index]
        checkpoint = re.search(r"｜卡点=([^｜]*)", line)
        action = re.search(r"｜动作=([^｜]*)", line)
        angle = (checkpoint.group(1) if checkpoint else "", action.group(1) if action else "")
        if angle in seen:
            continue
        seen.add(angle)
        chosen.add(index)
        if len(chosen) >= limit:
            break
    remove: set[int] = set()
    for position, start in enumerate(starts):
        if start in chosen:
            continue
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        remove.update(range(start, end))
    return "\n".join(line for index, line in enumerate(lines) if index not in remove)
