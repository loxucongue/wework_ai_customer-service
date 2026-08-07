"""Add durable first-day outreach workflow runs.

Revision ID: 20260807_01
Revises: 20260729_01
Create Date: 2026-08-07
"""
from alembic import op

from app.services.storage.mysql_schema import first_day_outreach_runs


revision = "20260807_01"
down_revision = "20260729_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    first_day_outreach_runs.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    raise RuntimeError("Destructive AICS schema downgrade is intentionally disabled")
