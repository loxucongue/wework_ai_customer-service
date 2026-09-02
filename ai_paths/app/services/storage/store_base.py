from __future__ import annotations

import re
from contextlib import AbstractContextManager
from typing import Any, Protocol


LOGICAL_TABLES = (
    "conversations",
    "messages",
    "runs",
    "node_traces",
    "customer_memory",
    "history_events",
    "outreach_sop_plans",
    "outreach_plans",
    "outreach_tasks",
    "outreach_events",
    "first_day_outreach_runs",
    "sop_events",
    "sop_send_tasks",
    "strategy_data_outbox",
    "message_dispatches",
    "message_dispatch_items",
    "message_delivery_events",
    "v3_strategy_usage_events",
    "v3_strategy_outcome_events",
)


class Store(Protocol):
    dialect: str

    def initialize(self) -> None: ...

    def connect(self) -> AbstractContextManager[Any]: ...

    def json_text(self, column: str, path: str) -> str: ...

    def close(self) -> None: ...


def map_logical_tables(sql: str, *, prefix: str) -> str:
    if not prefix:
        return sql
    patterns = [
        (
            re.compile(rf"(?<![A-Za-z0-9_]){re.escape(table)}(?![A-Za-z0-9_])"),
            f"{prefix}{table}",
        )
        for table in sorted(LOGICAL_TABLES, key=len, reverse=True)
    ]

    def rewrite(fragment: str) -> str:
        output = fragment
        for pattern, replacement in patterns:
            output = pattern.sub(replacement, output)
        return output

    output: list[str] = []
    unquoted: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(sql):
        char = sql[index]
        if quote:
            output.append(char)
            if char == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    output.append(sql[index + 1])
                    index += 1
                else:
                    quote = None
        elif char in {"'", '"'}:
            output.append(rewrite("".join(unquoted)))
            unquoted = []
            quote = char
            output.append(char)
        else:
            unquoted.append(char)
        index += 1
    output.append(rewrite("".join(unquoted)))
    return "".join(output)


def scalar(row: Any, default: Any = 0) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return next(iter(row.values()), default)
    try:
        return row[0]
    except (IndexError, KeyError, TypeError):
        return default
