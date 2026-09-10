"""Add V3 canonical media aliases and durable response claims."""

from alembic import op
from sqlalchemy import text

from app.services.storage.mysql_schema import metadata

revision = "20260910_01"
down_revision = "20260909_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in ("aics_material_identities", "aics_material_claims"):
        metadata.tables[name].create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    # Never discard delivery memory. Empty local/dry-run schemas are reversible;
    # a deployed rollback retains these additive tables for the next release.
    for name in ("aics_material_claims", "aics_material_identities"):
        if bind.execute(text(f"SELECT COUNT(*) FROM {name}")).scalar():
            raise RuntimeError("Archive and audit material data before destructive downgrade")
    for name in ("aics_material_claims", "aics_material_identities"):
        metadata.tables[name].drop(bind=bind, checkfirst=True)
