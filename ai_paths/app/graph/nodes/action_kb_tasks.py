from __future__ import annotations

from typing import Any

from app.graph.nodes.action_callback_types import ActionCallbacks
from app.graph.state import AgentState
from app.policies.constants import KB_BY_SKILL
from app.services.coze_client import CozeClient


ActionToolTask = tuple[str, dict[str, Any], Any]


def append_kb_and_pricing_tasks(
    *,
    action: dict[str, Any],
    state: AgentState,
    content: str,
    coze_client: CozeClient,
    callbacks: ActionCallbacks,
    tool_tasks: list[ActionToolTask],
) -> None:
    skill = action.get("name")
    kb_name = _kb_name_for_action(skill, state, content, callbacks)
    planned_kb_searches = callbacks.planned_kb_searches(action, state)
    if planned_kb_searches:
        for planned in planned_kb_searches:
            planned_kb = planned.get("kb_name", "")
            planned_query = planned.get("query", "") or callbacks.safe_query_from_state(state, skill)
            call = {
                "name": "coze_kb_search",
                "input": {
                    "kb_name": planned_kb,
                    "query": planned_query,
                    "planned": True,
                    "purpose": planned.get("purpose", ""),
                },
            }
            tool_tasks.append((planned_kb, call, coze_client.search_kb(planned_kb, planned_query)))
    elif kb_name:
        query = callbacks.safe_query_from_state(state, skill)
        call = {"name": "coze_kb_search", "input": {"kb_name": kb_name, "query": query}}
        tool_tasks.append((kb_name, call, coze_client.search_kb(kb_name, query)))

    if skill == "price_consult":
        price_project = callbacks.canonical_price_project(
            callbacks.contextual_price_project(state) or callbacks.extract_project(content)
        )
        if price_project and not callbacks.is_broad_price_category(price_project):
            sql = callbacks.pricing_sql_from_state(state)
            call = {"name": "coze_pricing_db", "input": {"sql": sql}}
            tool_tasks.append(("pricing_db", call, coze_client.query_pricing_db(sql)))


def _kb_name_for_action(skill: Any, state: AgentState, content: str, callbacks: ActionCallbacks) -> str:
    kb_name = KB_BY_SKILL.get(str(skill)) or ""
    if skill == "price_consult":
        price_project = callbacks.canonical_price_project(
            callbacks.contextual_price_project(state) or callbacks.extract_project(content)
        )
        if not price_project or callbacks.is_broad_price_category(price_project):
            return ""
    return kb_name
