from __future__ import annotations

from typing import Iterable


def _normalize_line(value: str) -> str:
    return value.strip()


def section(title: str, body: str) -> str:
    text = body.strip()
    return f"# {title}\n{text}\n\n"


def bullet_lines(items: Iterable[str]) -> str:
    return "\n".join(f"- {_normalize_line(item)}" for item in items if _normalize_line(item))


def numbered_lines(items: Iterable[str]) -> str:
    return "\n".join(f"{index}. {_normalize_line(item)}" for index, item in enumerate(items, start=1) if _normalize_line(item))


def build_node_prompt(
    *,
    role: str,
    input_items: Iterable[str],
    task_items: Iterable[str],
    do_not_items: Iterable[str],
    tool_items: Iterable[str],
    output_schema: str,
    extra_sections: Iterable[tuple[str, str]] = (),
) -> str:
    prompt = ""
    prompt += section("Node Role", role)
    prompt += section("Input", bullet_lines(input_items))
    prompt += section("Task", numbered_lines(task_items))
    prompt += section("Do Not", bullet_lines(do_not_items))
    prompt += section("Tools", bullet_lines(tool_items))
    prompt += section("Output Schema", output_schema)
    for title, body in extra_sections:
        prompt += section(title, body)
    return prompt
