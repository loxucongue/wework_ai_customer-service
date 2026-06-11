from __future__ import annotations

from typing import Any

from app.business_strategy import DEFAULT_BUSINESS_STRATEGY_RULES, compact_rules_for_prompt
from app.services.storage.repositories import AppRepository


class BusinessStrategyService:
    """Load business strategy rules from storage, falling back to built-in defaults."""

    def __init__(self, repository: AppRepository | None = None):
        self.repository = repository

    def list_rules(self, *, enabled_only: bool = True) -> list[dict[str, Any]]:
        if self.repository:
            try:
                rules = self.repository.list_business_strategy_rules(enabled_only=enabled_only)
                if rules:
                    return rules
            except Exception:
                pass
        return [dict(rule) for rule in DEFAULT_BUSINESS_STRATEGY_RULES if not enabled_only or rule.get("enabled", 1) != 0]

    def prompt_context(self) -> list[dict[str, Any]]:
        return compact_rules_for_prompt(self.list_rules(enabled_only=True))
