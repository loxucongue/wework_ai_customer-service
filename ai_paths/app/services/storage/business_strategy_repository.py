from __future__ import annotations

from typing import Any

from app.services.storage.serialization import dumps, loads_list, utc_now_iso


class BusinessStrategyRepositoryMixin:
    def list_business_strategy_rules(self, *, enabled_only: bool = True) -> list[dict[str, Any]]:
        where = "WHERE enabled=1" if enabled_only else ""
        with self.store.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT id, category, subtype, title, trigger_examples, decision_goal, answer_goal,
                       tool_guidance, suggested_moves, forbidden_moves, handoff_policy,
                       priority, enabled, updated_at
                FROM business_strategy_rules
                {where}
                ORDER BY priority ASC, category ASC, subtype ASC
                """
            ).fetchall()
        return [self._decode_business_strategy_rule(dict(row)) for row in rows]

    def upsert_business_strategy_rule(self, rule: dict[str, Any]) -> None:
        now = utc_now_iso()
        with self.store.connect() as conn:
            conn.execute(
                """
                INSERT INTO business_strategy_rules
                    (id, category, subtype, title, trigger_examples, decision_goal, answer_goal,
                     tool_guidance, suggested_moves, forbidden_moves, handoff_policy,
                     priority, enabled, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    category=excluded.category,
                    subtype=excluded.subtype,
                    title=excluded.title,
                    trigger_examples=excluded.trigger_examples,
                    decision_goal=excluded.decision_goal,
                    answer_goal=excluded.answer_goal,
                    tool_guidance=excluded.tool_guidance,
                    suggested_moves=excluded.suggested_moves,
                    forbidden_moves=excluded.forbidden_moves,
                    handoff_policy=excluded.handoff_policy,
                    priority=excluded.priority,
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at
                """,
                (
                    str(rule.get("id") or ""),
                    str(rule.get("category") or ""),
                    str(rule.get("subtype") or ""),
                    str(rule.get("title") or ""),
                    dumps(rule.get("trigger_examples") or []),
                    str(rule.get("decision_goal") or ""),
                    str(rule.get("answer_goal") or ""),
                    dumps(rule.get("tool_guidance") or []),
                    dumps(rule.get("suggested_moves") or []),
                    dumps(rule.get("forbidden_moves") or []),
                    str(rule.get("handoff_policy") or ""),
                    int(rule.get("priority") or 50),
                    1 if rule.get("enabled", 1) else 0,
                    now,
                ),
            )

    @staticmethod
    def _decode_business_strategy_rule(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row.get("id") or "",
            "category": row.get("category") or "",
            "subtype": row.get("subtype") or "",
            "title": row.get("title") or "",
            "trigger_examples": loads_list(row.get("trigger_examples")),
            "decision_goal": row.get("decision_goal") or "",
            "answer_goal": row.get("answer_goal") or "",
            "tool_guidance": loads_list(row.get("tool_guidance")),
            "suggested_moves": loads_list(row.get("suggested_moves")),
            "forbidden_moves": loads_list(row.get("forbidden_moves")),
            "handoff_policy": row.get("handoff_policy") or "",
            "priority": int(row.get("priority") or 50),
            "enabled": bool(row.get("enabled")),
            "updated_at": row.get("updated_at") or "",
        }
