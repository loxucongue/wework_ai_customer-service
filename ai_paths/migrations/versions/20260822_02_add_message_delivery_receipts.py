"""Add message dispatch and delivery receipt tables.

Revision ID: 20260822_02
Revises: 20260813_01
Create Date: 2026-08-22
"""
from alembic import op

from app.services.storage.mysql_schema import metadata


revision = "20260822_02"
down_revision = "20260813_01"
branch_labels = None
depends_on = None


_TABLE_NAMES = (
    "aics_message_dispatches",
    "aics_message_dispatch_items",
    "aics_message_delivery_events",
)


def upgrade() -> None:
    bind = op.get_bind()
    for table_name in _TABLE_NAMES:
        metadata.tables[table_name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    raise RuntimeError("Destructive AICS schema downgrade is intentionally disabled")
