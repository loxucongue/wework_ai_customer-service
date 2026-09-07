"""Add explicit sequence and script adoption observations.

Revision ID: 20260907_01
Revises: 20260904_01
Create Date: 2026-09-07
"""

from alembic import op
from sqlalchemy import inspect

from app.services.storage.mysql_schema import metadata


revision = "20260907_01"
down_revision = "20260904_01"
branch_labels = None
depends_on = None


_COLUMNS = ("sequence_adopted", "script_adopted", "adoption_detail_observed")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    table_name = "aics_v3_strategy_usage_events"
    existing = {column["name"] for column in inspector.get_columns(table_name)}
    table = metadata.tables[table_name]
    for column_name in _COLUMNS:
        if column_name not in existing:
            op.add_column(table_name, table.c[column_name].copy())


def downgrade() -> None:
    raise RuntimeError("Destructive AICS schema downgrade is intentionally disabled")
