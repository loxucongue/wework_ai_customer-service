"""Add durable V3 generation idempotency and recovery state.

Revision ID: 20260909_01
Revises: 20260908_01
Create Date: 2026-09-09
"""

from alembic import op
from sqlalchemy import inspect

from app.services.storage.mysql_schema import metadata


revision = "20260909_01"
down_revision = "20260908_01"
branch_labels = None
depends_on = None


_COLUMNS = (
    "generation_key",
    "response_id",
    "generation_status",
    "recovery_kind",
    "recovery_attempts",
    "recovery_next_at",
    "recovery_dispatch_id",
    "recovery_error",
)


def upgrade() -> None:
    bind = op.get_bind()
    table_name = "aics_runs"
    table = metadata.tables[table_name]
    inspector = inspect(bind)
    existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
    for column_name in _COLUMNS:
        if column_name not in existing_columns:
            op.add_column(table_name, table.c[column_name].copy())

    inspector = inspect(bind)
    existing_indexes = {index["name"] for index in inspector.get_indexes(table_name)}
    existing_uniques = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints(table_name)
        if constraint.get("name")
    }
    if "uq_aics_runs_generation_key" not in existing_indexes | existing_uniques:
        op.create_unique_constraint(
            "uq_aics_runs_generation_key",
            table_name,
            ["generation_key"],
        )
    if "uq_aics_runs_response_id" not in existing_indexes | existing_uniques:
        op.create_unique_constraint(
            "uq_aics_runs_response_id",
            table_name,
            ["response_id"],
        )
    if "idx_aics_runs_generation_recovery" not in existing_indexes:
        op.create_index(
            "idx_aics_runs_generation_recovery",
            table_name,
            ["generation_status", "recovery_next_at", "created_at"],
        )


def downgrade() -> None:
    raise RuntimeError("Destructive AICS schema downgrade is intentionally disabled")
