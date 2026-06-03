from __future__ import annotations

from typing import Any

from app.graph.nodes.after_sales_skill_output import (
    AfterSalesSkillCallbacks,
    after_sales_skill_output as after_sales_skill_output_from_module,
)
from app.graph.nodes.basic_skill_output import BasicSkillCallbacks, basic_skill_output as basic_skill_output_from_module
from app.graph.nodes.common import dedupe_strings, intent_for_skill, json_dumps
from app.graph.nodes.competitor_skill_output import (
    CompetitorSkillCallbacks,
    competitor_skill_output as competitor_skill_output_from_module,
)
from app.graph.nodes.image_info import (
    has_image_concern,
    known_visible_concerns_from_state,
)
from app.graph.nodes.legacy_flow_utils import extract_price_digits, parking_text
from app.graph.nodes.legacy_project_context import (
    LegacyProjectContextCallbacks,
    contextual_price_project,
)
from app.graph.nodes.legacy_qa_slice_context import (
    clean_after_sales_text,
    clean_competitor_text,
    competitor_default_reply,
    competitor_risk_terms,
    competitor_scenario,
    competitor_slice_matches,
    first_after_sales_slice,
    first_competitor_slice,
    split_collect_items,
)
from app.graph.nodes.legacy_skill_dispatch import LegacySkillDispatchCallbacks, skill_output as skill_output_from_dispatch
from app.graph.nodes.price_skill_output import PriceSkillCallbacks, price_skill_output as price_skill_output_from_module
from app.graph.nodes.pricing_context import (
    canonical_price_project,
    extract_project,
    filter_pricing_rows_for_project,
    is_broad_price_category,
    price_bits,
    price_risk_terms,
    pricing_rows,
)
from app.graph.nodes.project_kb_context import (
    business_project_slices,
    case_request_lacks_specific_context,
    project_direction_name_candidates,
    project_slices_from_tool_results,
)
from app.graph.nodes.project_skill_output import ProjectSkillCallbacks, project_skill_output as project_skill_output_from_module
from app.graph.nodes.result_compaction import ad_price_without_explicit_project
from app.graph.nodes.store_context import extract_city
from app.graph.nodes.store_skill_output import StoreSkillCallbacks, store_skill_output as store_skill_output_from_module
from app.graph.nodes.trust_skill_output import trust_skill_output as trust_skill_output_from_module
from app.graph.state import AgentState


def _legacy_project_context_callbacks() -> LegacyProjectContextCallbacks:
    from app.graph.nodes.intent_signals import recent_conversation_text

    return LegacyProjectContextCallbacks(
        business_project_slices=business_project_slices,
        canonical_price_project=canonical_price_project,
        dedupe_strings=dedupe_strings,
        extract_project=extract_project,
        has_image_concern=has_image_concern,
        project_direction_name_candidates=project_direction_name_candidates,
        project_slices_from_tool_results=project_slices_from_tool_results,
        recent_conversation_text=recent_conversation_text,
    )


def _contextual_price_project(state: AgentState) -> str:
    return contextual_price_project(state, callbacks=_legacy_project_context_callbacks())


def _after_sales_skill_output(content: str, tool_results: dict[str, Any]) -> dict[str, Any]:
    return after_sales_skill_output_from_module(
        content,
        tool_results,
        AfterSalesSkillCallbacks(
            first_after_sales_slice=first_after_sales_slice,
            clean_after_sales_text=clean_after_sales_text,
            split_collect_items=split_collect_items,
        ),
    )


def _competitor_skill_output(content: str, tool_results: dict[str, Any]) -> dict[str, Any]:
    return competitor_skill_output_from_module(
        content,
        tool_results,
        CompetitorSkillCallbacks(
            first_competitor_slice=first_competitor_slice,
            competitor_scenario=competitor_scenario,
            extract_project=extract_project,
            extract_price_digits=extract_price_digits,
            competitor_slice_matches=competitor_slice_matches,
            clean_competitor_text=clean_competitor_text,
            competitor_default_reply=competitor_default_reply,
            split_collect_items=split_collect_items,
            competitor_risk_terms=competitor_risk_terms,
        ),
    )


def _store_skill_output(content: str, tool_results: dict[str, Any]) -> dict[str, Any]:
    return store_skill_output_from_module(
        content,
        tool_results,
        StoreSkillCallbacks(
            extract_city=extract_city,
            parking_text=parking_text,
        ),
    )


def _price_skill_output(content: str, tool_results: dict[str, Any], state: AgentState | None = None) -> dict[str, Any]:
    return price_skill_output_from_module(
        content,
        tool_results,
        state,
        PriceSkillCallbacks(
            ad_price_without_explicit_project=ad_price_without_explicit_project,
            canonical_price_project=canonical_price_project,
            contextual_price_project=_contextual_price_project,
            extract_price_digits=extract_price_digits,
            extract_project=extract_project,
            filter_pricing_rows_for_project=filter_pricing_rows_for_project,
            has_price_objection=_has_price_objection,
            is_broad_price_category=is_broad_price_category,
            price_bits=price_bits,
            price_risk_terms=price_risk_terms,
            pricing_rows=pricing_rows,
            pricing_rows_from_kb=_pricing_rows_from_kb,
        ),
    )


def _project_skill_output(content: str, tool_results: dict[str, Any], state: AgentState) -> dict[str, Any]:
    return project_skill_output_from_module(
        content,
        tool_results,
        state,
        ProjectSkillCallbacks(
            business_project_slices=business_project_slices,
            case_request_lacks_specific_context=case_request_lacks_specific_context,
            dedupe_strings=dedupe_strings,
            has_image_concern=has_image_concern,
            known_visible_concerns_from_state=known_visible_concerns_from_state,
            project_direction_name_candidates=project_direction_name_candidates,
            project_slices_from_tool_results=project_slices_from_tool_results,
        ),
    )


def _basic_skill_output(
    skill: str,
    reply_points: list[str],
    *,
    suggested_next_step: str = "",
    facts: list[str] | None = None,
    risk_flags: list[str] | None = None,
) -> dict[str, Any]:
    return basic_skill_output_from_module(
        skill,
        reply_points,
        BasicSkillCallbacks(intent_for_skill=intent_for_skill),
        suggested_next_step=suggested_next_step,
        facts=facts,
        risk_flags=risk_flags,
    )


def skill_output(skill: str, content: str, tool_results: dict[str, Any], state: AgentState) -> dict[str, Any]:
    return skill_output_from_dispatch(
        skill,
        content,
        tool_results,
        state,
        LegacySkillDispatchCallbacks(
            price_skill_output=_price_skill_output,
            trust_skill_output=trust_skill_output_from_module,
            project_skill_output=_project_skill_output,
            competitor_skill_output=_competitor_skill_output,
            after_sales_skill_output=_after_sales_skill_output,
            store_skill_output=_store_skill_output,
            basic_skill_output=_basic_skill_output,
            json_dumps=json_dumps,
        ),
    )


def _has_price_objection(content: str) -> bool:
    from app.graph.nodes.intent_signals import has_price_objection

    return has_price_objection(content)


def _pricing_rows_from_kb(output: str) -> list[dict[str, Any]]:
    from app.graph.nodes.kb_slice_parsing import pricing_rows_from_kb

    return pricing_rows_from_kb(output)
