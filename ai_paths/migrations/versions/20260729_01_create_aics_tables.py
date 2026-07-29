"""Create isolated AICS persistence tables.

Revision ID: 20260729_01
Revises:
Create Date: 2026-07-29
"""
from alembic import op

from app.services.storage.mysql_schema import metadata


revision = "20260729_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    for table in metadata.sorted_tables:
        table.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    raise RuntimeError("Destructive AICS schema downgrade is intentionally disabled")
