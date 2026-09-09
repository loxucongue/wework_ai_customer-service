"""Add durable V3 generation idempotency and recovery state.

Revision ID: 20260909_01
Revises: 20260908_01
Create Date: 2026-09-09
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects.mysql import LONGTEXT, VARCHAR


revision = "20260909_01"
down_revision = "20260908_01"
branch_labels = None
depends_on = None


_COLUMNS = (
    sa.Column("generation_key", VARCHAR(64), nullable=True),
    sa.Column("response_id", VARCHAR(64), nullable=True),
    sa.Column("generation_status", VARCHAR(32), nullable=False, server_default=""),
    sa.Column("recovery_kind", VARCHAR(64), nullable=False, server_default=""),
    sa.Column("recovery_attempts", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("recovery_next_at", VARCHAR(40), nullable=False, server_default=""),
    sa.Column("recovery_dispatch_id", VARCHAR(191), nullable=False, server_default=""),
    sa.Column(
        "recovery_error",
        LONGTEXT(),
        nullable=False,
        server_default=sa.text("('')"),
    ),
)


def _assert_index_compatible(
    *,
    index_name: str,
    columns: tuple[str, ...],
    unique: bool,
    indexes: dict[str, dict],
    uniques: dict[str, dict],
) -> bool:
    existing = uniques.get(index_name) or indexes.get(index_name)
    if existing is None:
        return False
    existing_columns = tuple(existing.get("column_names") or ())
    existing_unique = index_name in uniques or bool(existing.get("unique"))
    if existing_columns != columns or existing_unique != unique:
        raise RuntimeError(
            f"Incompatible existing index {index_name}: "
            f"columns={existing_columns!r}, unique={existing_unique!r}"
        )
    return True


def upgrade() -> None:
    bind = op.get_bind()
    table_name = "aics_runs"
    inspector = inspect(bind)
    existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
    for column in _COLUMNS:
        if column.name not in existing_columns:
            op.add_column(table_name, column.copy())

    inspector = inspect(bind)
    existing_indexes = {
        index["name"]: index
        for index in inspector.get_indexes(table_name)
        if index.get("name")
    }
    existing_uniques = {
        constraint["name"]: constraint
        for constraint in inspector.get_unique_constraints(table_name)
        if constraint.get("name")
    }
    if not _assert_index_compatible(
        index_name="uq_aics_runs_generation_key",
        columns=("generation_key",),
        unique=True,
        indexes=existing_indexes,
        uniques=existing_uniques,
    ):
        op.create_unique_constraint(
            "uq_aics_runs_generation_key",
            table_name,
            ["generation_key"],
        )
    if not _assert_index_compatible(
        index_name="uq_aics_runs_response_id",
        columns=("response_id",),
        unique=True,
        indexes=existing_indexes,
        uniques=existing_uniques,
    ):
        op.create_unique_constraint(
            "uq_aics_runs_response_id",
            table_name,
            ["response_id"],
        )
    if not _assert_index_compatible(
        index_name="idx_aics_runs_generation_recovery",
        columns=("generation_status", "recovery_next_at", "created_at"),
        unique=False,
        indexes=existing_indexes,
        uniques=existing_uniques,
    ):
        op.create_index(
            "idx_aics_runs_generation_recovery",
            table_name,
            ["generation_status", "recovery_next_at", "created_at"],
        )


def downgrade() -> None:
    raise RuntimeError("Destructive AICS schema downgrade is intentionally disabled")
