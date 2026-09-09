"""Add durable internal work items for missing authoritative facts.

Revision ID: 20260909_02
Revises: 20260909_01
Create Date: 2026-09-09
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects.mysql import LONGTEXT, VARCHAR
from sqlalchemy.types import TypeEngine


revision = "20260909_02"
down_revision = "20260909_01"
branch_labels = None
depends_on = None


TABLE_NAME = "aics_internal_work_items"


def _varchar(length: int) -> TypeEngine:
    """Keep the production MySQL type while allowing SQLite migration tests."""

    return sa.String(length).with_variant(VARCHAR(length), "mysql")


def _longtext() -> TypeEngine:
    return sa.Text().with_variant(LONGTEXT(), "mysql")


INDEX_SPECS = (
    (
        "uq_aics_internal_work_items_idempotency",
        ("idempotency_key",),
        True,
        {},
    ),
    (
        "idx_aics_internal_work_items_queue",
        ("work_type", "status", "last_seen_at"),
        False,
        {},
    ),
    (
        "idx_aics_internal_work_items_store_detail",
        ("store_id", "detail_kind", "status"),
        False,
        {},
    ),
    (
        "idx_aics_internal_work_items_contact",
        ("corp_id", "wechat", "external_userid", "customer_id", "last_seen_at"),
        False,
        {
            "mysql_length": {
                "corp_id": 64,
                "wechat": 64,
                "external_userid": 64,
                "customer_id": 64,
            }
        },
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


def _ensure_indexes(inspector) -> None:
    existing_indexes = {
        index["name"]: index
        for index in inspector.get_indexes(TABLE_NAME)
        if index.get("name")
    }
    existing_uniques = {
        constraint["name"]: constraint
        for constraint in inspector.get_unique_constraints(TABLE_NAME)
        if constraint.get("name")
    }
    for index_name, columns, unique, kwargs in INDEX_SPECS:
        if _assert_index_compatible(
            index_name=index_name,
            columns=columns,
            unique=unique,
            indexes=existing_indexes,
            uniques=existing_uniques,
        ):
            continue
        if unique:
            op.create_unique_constraint(index_name, TABLE_NAME, list(columns))
        else:
            op.create_index(index_name, TABLE_NAME, list(columns), **kwargs)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if TABLE_NAME in inspector.get_table_names():
        required = {
            "id",
            "idempotency_key",
            "work_type",
            "status",
            "store_id",
            "detail_kind",
            "missing_fact_keys_json",
            "source_request_id",
            "conversation_id",
            "corp_id",
            "wechat",
            "external_userid",
            "customer_id",
            "occurrence_count",
            "first_seen_at",
            "last_seen_at",
            "resolved_at",
            "resolved_by",
            "resolution_note",
            "payload_json",
            "created_at",
            "updated_at",
        }
        existing = {column["name"] for column in inspector.get_columns(TABLE_NAME)}
        missing = sorted(required - existing)
        if missing:
            raise RuntimeError(
                f"Existing {TABLE_NAME} is incompatible; missing columns: {missing}"
            )
        _ensure_indexes(inspect(bind))
        return

    op.create_table(
        TABLE_NAME,
        sa.Column("id", _varchar(191), primary_key=True, nullable=False),
        sa.Column("idempotency_key", _varchar(191), nullable=False),
        sa.Column("work_type", _varchar(64), nullable=False),
        sa.Column("status", _varchar(32), nullable=False, server_default="pending"),
        sa.Column("store_id", _varchar(191), nullable=False, server_default=""),
        sa.Column("detail_kind", _varchar(64), nullable=False, server_default=""),
        sa.Column(
            "missing_fact_keys_json",
            _longtext(),
            nullable=False,
            server_default=sa.text("('[]')"),
        ),
        sa.Column("source_request_id", _varchar(191), nullable=False, server_default=""),
        sa.Column("conversation_id", _varchar(191), nullable=False, server_default=""),
        sa.Column("corp_id", _varchar(191), nullable=False, server_default=""),
        sa.Column("wechat", _varchar(191), nullable=False, server_default=""),
        sa.Column("external_userid", _varchar(191), nullable=False, server_default=""),
        sa.Column("customer_id", _varchar(191), nullable=False, server_default=""),
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("first_seen_at", _varchar(40), nullable=False),
        sa.Column("last_seen_at", _varchar(40), nullable=False),
        sa.Column("resolved_at", _varchar(40), nullable=False, server_default=""),
        sa.Column("resolved_by", _varchar(191), nullable=False, server_default=""),
        sa.Column(
            "resolution_note", _longtext(), nullable=False, server_default=sa.text("('')")
        ),
        sa.Column(
            "payload_json", _longtext(), nullable=False, server_default=sa.text("('{}')")
        ),
        sa.Column("created_at", _varchar(40), nullable=False),
        sa.Column("updated_at", _varchar(40), nullable=False),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_aics_internal_work_items_idempotency"
        ),
    )
    _ensure_indexes(inspect(bind))


def downgrade() -> None:
    raise RuntimeError("Destructive AICS schema downgrade is intentionally disabled")
