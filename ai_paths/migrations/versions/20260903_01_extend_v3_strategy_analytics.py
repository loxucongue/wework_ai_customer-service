"""Extend V3 strategy decision and outcome analytics.

Revision ID: 20260903_01
Revises: 20260902_01
Create Date: 2026-09-03
"""
from sqlalchemy import inspect
from alembic import op

from app.services.storage.mysql_schema import metadata


revision = "20260903_01"
down_revision = "20260902_01"
branch_labels = None
depends_on = None


_COLUMNS = {
    "aics_v3_strategy_usage_events": (
        "policy_version",
        "decision_status",
        "intent_confidence",
        "intent_secondary_json",
        "emotion_confidence",
        "emotion_pressure",
        "emotion_flow_action",
        "closing_action",
        "closing_node_key",
        "closing_trigger",
        "closing_customer_state",
        "closing_pressure",
        "cardpoint_category_key",
        "cardpoint_state",
        "decision_reasons_json",
        "decision_evidence_refs_json",
        "customer_turn_eligible",
    ),
    "aics_v3_strategy_outcome_events": (
        "next_usage_event_id",
        "next_intent_code",
        "next_emotion_code",
        "emotion_transition",
        "attribution_anchor_source",
        "order_source",
        "order_query_status",
        "order_query_error",
        "order_last_refreshed_at",
        "order_state_after_14d",
        "order_state_after_30d",
    ),
}

_INDEXES = {
    "aics_v3_strategy_usage_events": (
        "idx_aics_v3_strategy_usage_intent",
        "idx_aics_v3_strategy_usage_emotion",
        "idx_aics_v3_strategy_usage_closing",
        "idx_aics_v3_strategy_usage_decision",
    ),
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    for table_name, column_names in _COLUMNS.items():
        existing = {column["name"] for column in inspector.get_columns(table_name)}
        table = metadata.tables[table_name]
        for column_name in column_names:
            if column_name not in existing:
                op.add_column(table_name, table.c[column_name].copy())
    op.execute(
        """
        UPDATE aics_v3_strategy_usage_events SET customer_turn_eligible=0
        WHERE reply_source IN (
            'ignored_platform_auto_message', 'platform_recalled_message',
            'platform_superseded', 'platform_filtered'
        )
        """
    )
    inspector = inspect(bind)
    for table_name, index_names in _INDEXES.items():
        existing = {index["name"] for index in inspector.get_indexes(table_name)}
        table = metadata.tables[table_name]
        for index_name in index_names:
            if index_name in existing:
                continue
            index = next(item for item in table.indexes if item.name == index_name)
            op.create_index(index.name, table_name, [column.name for column in index.columns])


def downgrade() -> None:
    raise RuntimeError("Destructive AICS schema downgrade is intentionally disabled")
