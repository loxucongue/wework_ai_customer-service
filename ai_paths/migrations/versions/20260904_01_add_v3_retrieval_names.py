"""Add external closing names and retrieval mode to V3 analytics.

Revision ID: 20260904_01
Revises: 20260903_02
Create Date: 2026-09-04
"""

from alembic import op
from sqlalchemy import inspect

from app.services.storage.mysql_schema import metadata


revision = "20260904_01"
down_revision = "20260903_02"
branch_labels = None
depends_on = None


_COLUMNS = (
    "closing_primary_rule_name",
    "closing_sequence_name",
    "closing_node_name",
    "retrieval_mode",
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
    index_name = "idx_aics_v3_strategy_usage_retrieval"
    if index_name not in existing_indexes:
        index = next(item for item in table.indexes if item.name == index_name)
        op.create_index(index.name, table_name, [column.name for column in index.columns])


def downgrade() -> None:
    raise RuntimeError("Destructive AICS schema downgrade is intentionally disabled")
