from __future__ import annotations

from typing import Any


STRUCTURED_PAYMENT_STATES = {"deposit_paid", "required_unpaid", "payment_failed"}


def build_payment_turn_evidence(
    *,
    deposit_state: str,
    payment_evidence: dict[str, Any],
    blocked_actions: list[str],
) -> dict[str, Any]:
    if not isinstance(payment_evidence, dict):
        payment_evidence = {}
    state = str(deposit_state or "unknown").strip()
    output = dict(payment_evidence)
    if state in STRUCTURED_PAYMENT_STATES:
        output["structured_payment_state"] = state
    elif state == "payment_link_sent":
        output["link_sent_evidence"] = True
    if "payment_collection" in blocked_actions:
        output["blocked_payment_collection_by_structure"] = True
    if output:
        output["source_policy"] = "evidence_only_planner_decides_payment_state"
    return _drop_empty(output)


def _drop_empty(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, "", [], {})}
