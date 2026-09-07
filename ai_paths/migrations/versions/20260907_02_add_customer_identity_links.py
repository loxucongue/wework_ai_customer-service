"""Add canonical customer identity links.

Revision ID: 20260907_02
Revises: 20260907_01
Create Date: 2026-09-07
"""

from alembic import op
from sqlalchemy import inspect

from app.services.storage.mysql_schema import metadata


revision = "20260907_02"
down_revision = "20260907_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    table_name = "aics_customer_identity_links"
    if table_name not in inspect(bind).get_table_names():
        metadata.tables[table_name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    raise RuntimeError("Destructive AICS schema downgrade is intentionally disabled")
