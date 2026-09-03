"""Add external closing catalog observability fields.

Revision ID: 20260903_02
Revises: 20260903_01
Create Date: 2026-09-03
"""
from sqlalchemy import inspect
from alembic import op

from app.services.storage.mysql_schema import metadata


revision = "20260903_02"
down_revision = "20260903_01"
branch_labels = None
depends_on = None


_COLUMNS = (
    "closing_rule_ids_json",
    "closing_primary_rule_id",
    "closing_sequence_source_id",
    "closing_node_source_id",
    "closing_action_type_id",
    "closing_action_type_name",
    "closing_script_type_id",
    "closing_script_type_name",
    "closing_catalog_checksum",
    "closing_catalog_status",
    "closing_rule_match_status",
    "closing_constraint_status",
    "closing_constraint_reasons_json",
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    table_name = "aics_v3_strategy_usage_events"
    existing = {column["name"] for column in inspector.get_columns(table_name)}
    table = metadata.tables[table_name]
    for column_name in _COLUMNS:
        if column_name not in existing:
            op.add_column(table_name, table.c[column_name].copy())
    inspector = inspect(bind)
    existing_indexes = {index["name"] for index in inspector.get_indexes(table_name)}
    for index_name in (
        "idx_aics_v3_strategy_usage_closing_catalog",
        "idx_aics_v3_strategy_usage_closing_rule",
    ):
        if index_name not in existing_indexes:
            index = next(item for item in table.indexes if item.name == index_name)
            op.create_index(index.name, table_name, [column.name for column in index.columns])


def downgrade() -> None:
    raise RuntimeError("Destructive AICS schema downgrade is intentionally disabled")
