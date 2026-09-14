"""Reception notifications, durable snapshots and relationship history."""
from alembic import op
from sqlalchemy import inspect, text

from app.services.storage.mysql_schema import metadata

revision = "20260914_01"
down_revision = "20260910_01"
branch_labels = None
depends_on = None
TABLES = ("aics_reception_states", "aics_reception_events", "aics_reception_relations")


def upgrade() -> None:
    bind = op.get_bind()
    for name in TABLES:
        table = metadata.tables[name]
        table.create(bind, checkfirst=True)
        inspector = inspect(bind)
        if {item["name"] for item in inspector.get_columns(name)} != set(table.columns.keys()):
            raise RuntimeError("Incompatible reception table columns")
        if tuple(inspector.get_pk_constraint(name)["constrained_columns"]) != tuple(table.primary_key.columns.keys()):
            raise RuntimeError("Incompatible reception primary key")
        for column in inspector.get_columns(name):
            expected = table.columns[column["name"]]
            actual_type = str(column["type"].compile(dialect=bind.dialect)).upper()
            expected_type = str(expected.type.compile(dialect=bind.dialect)).upper()
            if actual_type != expected_type or column["nullable"] != expected.nullable:
                raise RuntimeError("Incompatible reception column type")
        if bind.dialect.name == "mysql":
            if inspector.get_table_options(name).get("mysql_engine", "").lower() != "innodb":
                raise RuntimeError("Reception state requires transactional InnoDB")


def downgrade() -> None:
    bind = op.get_bind()
    existing = [name for name in TABLES if inspect(bind).has_table(name)]
    for name in existing:
        if bind.execute(text(f"SELECT COUNT(*) FROM {name}")).scalar():
            raise RuntimeError("Retain reception events/state on application rollback; nonempty downgrade forbidden")
    for name in reversed(existing):
        metadata.tables[name].drop(bind)
