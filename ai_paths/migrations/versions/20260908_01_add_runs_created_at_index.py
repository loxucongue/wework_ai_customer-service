"""Index recent run scans used by durable V3 reply finalization.

Revision ID: 20260908_01
Revises: 20260907_02
Create Date: 2026-09-08
"""

from alembic import op
from sqlalchemy import inspect


revision = "20260908_01"
down_revision = "20260907_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    table_name = "aics_runs"
    index_name = "idx_aics_runs_created_at"
    existing = {item["name"] for item in inspect(bind).get_indexes(table_name)}
    if index_name not in existing:
        op.create_index(index_name, table_name, ["created_at"])


def downgrade() -> None:
    raise RuntimeError("Destructive AICS schema downgrade is intentionally disabled")
