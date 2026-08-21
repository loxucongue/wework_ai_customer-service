"""Add durable strategy-data callback outbox.

Revision ID: 20260821_01
Revises: 20260807_01
Create Date: 2026-08-21
"""
from alembic import op

from app.services.storage.mysql_schema import strategy_data_outbox


revision = "20260821_01"
down_revision = "20260807_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    strategy_data_outbox.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    raise RuntimeError("Destructive AICS schema downgrade is intentionally disabled")
