"""Add V3 strategy usage and outcome analytics tables.

Revision ID: 20260902_01
Revises: 20260822_02
Create Date: 2026-09-02
"""
from alembic import op

from app.services.storage.mysql_schema import metadata


revision = "20260902_01"
down_revision = "20260822_02"
branch_labels = None
depends_on = None


_TABLE_NAMES = (
    "aics_v3_strategy_usage_events",
    "aics_v3_strategy_outcome_events",
)


def upgrade() -> None:
    bind = op.get_bind()
    for table_name in _TABLE_NAMES:
        metadata.tables[table_name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    raise RuntimeError("Destructive AICS schema downgrade is intentionally disabled")
