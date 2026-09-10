"""Add V3 canonical media aliases and durable response claims."""

from alembic import op
from sqlalchemy import inspect, text

from app.services.storage.mysql_schema import metadata

revision = "20260910_01"
down_revision = "20260909_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    for name in ("aics_material_identities", "aics_material_claims"):
        table = metadata.tables[name]
        table.create(bind=bind, checkfirst=True)
        inspector = inspect(bind)
        if {column["name"] for column in inspector.get_columns(name)} != set(table.columns.keys()):
            raise RuntimeError(f"Incompatible material table columns: {name}")
        if tuple(inspector.get_pk_constraint(name)["constrained_columns"]) != tuple(table.primary_key.columns.keys()):
            raise RuntimeError(f"Incompatible material primary key: {name}")
        indexes = {index["name"]: index for index in inspector.get_indexes(name)}
        # CREATE TABLE and CREATE INDEX are separate MySQL DDL commits. Resume
        # an interrupted upgrade by adding missing indexes, never guessing that
        # a table's existence proves that its indexes were created correctly.
        for index in table.indexes:
            existing = indexes.get(index.name)
            if existing and (
                tuple(existing["column_names"]) != tuple(column.name for column in index.columns)
                or bool(existing["unique"]) != bool(index.unique)
            ):
                raise RuntimeError(f"Incompatible material index: {index.name}")
            index.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    # Never discard delivery memory. Empty local/dry-run schemas are reversible;
    # a deployed rollback retains these additive tables for the next release.
    for name in ("aics_material_claims", "aics_material_identities"):
        if bind.execute(text(f"SELECT COUNT(*) FROM {name}")).scalar():
            raise RuntimeError("Archive and audit material data before destructive downgrade")
    for name in ("aics_material_claims", "aics_material_identities"):
        metadata.tables[name].drop(bind=bind, checkfirst=True)
