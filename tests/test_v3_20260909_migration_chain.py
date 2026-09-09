from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlalchemy.dialects import mysql, sqlite
from sqlalchemy.exc import IntegrityError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VERSIONS_DIR = PROJECT_ROOT / "ai_paths" / "migrations" / "versions"


def _load_revision(filename: str):
    path = VERSIONS_DIR / filename
    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _apply_revision(connection, module) -> None:
    original_op = module.op
    module.op = Operations(MigrationContext.configure(connection))
    try:
        module.upgrade()
    finally:
        module.op = original_op


def test_real_sqlite_alembic_operations_upgrade_from_20260908_01_to_head(
    tmp_path: Path,
) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'migration-chain.db'}")
    generation = _load_revision("20260909_01_add_v3_reply_generation.py")
    work_items = _load_revision("20260909_02_add_internal_work_items.py")
    assert generation.down_revision == "20260908_01"
    assert work_items.down_revision == generation.revision

    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TABLE aics_runs ("
                "request_id VARCHAR(191) PRIMARY KEY, created_at VARCHAR(40) NOT NULL)"
            )
        )
        connection.execute(
            sa.text("CREATE INDEX idx_aics_runs_created_at ON aics_runs(created_at)")
        )
        connection.execute(
            sa.text(
                "CREATE TABLE aics_schema_version (version_num VARCHAR(32) PRIMARY KEY)"
            )
        )
        connection.execute(
            sa.text("INSERT INTO aics_schema_version(version_num) VALUES ('20260908_01')")
        )
        connection.execute(
            sa.text(
                "INSERT INTO aics_runs(request_id, created_at) "
                "VALUES ('legacy-1', '2026-09-08T00:00:00+00:00')"
            )
        )

        _apply_revision(connection, generation)
        connection.execute(
            sa.text(
                "UPDATE aics_schema_version SET version_num=:revision"
            ),
            {"revision": generation.revision},
        )
        _apply_revision(connection, work_items)
        connection.execute(
            sa.text(
                "UPDATE aics_schema_version SET version_num=:revision"
            ),
            {"revision": work_items.revision},
        )

        run_columns = {item["name"]: item for item in inspect(connection).get_columns("aics_runs")}
        assert {
            "generation_key",
            "response_id",
            "generation_status",
            "recovery_kind",
            "recovery_attempts",
            "recovery_next_at",
            "recovery_dispatch_id",
            "recovery_error",
        } <= set(run_columns)
        legacy = connection.execute(
            sa.text(
                "SELECT generation_key, response_id, generation_status, recovery_attempts "
                "FROM aics_runs WHERE request_id='legacy-1'"
            )
        ).mappings().one()
        assert legacy == {
            "generation_key": None,
            "response_id": None,
            "generation_status": "",
            "recovery_attempts": 0,
        }

        run_indexes = {item["name"]: item for item in inspect(connection).get_indexes("aics_runs")}
        assert run_indexes["uq_aics_runs_generation_key"]["unique"]
        assert run_indexes["uq_aics_runs_response_id"]["unique"]
        assert run_indexes["idx_aics_runs_generation_recovery"]["column_names"] == [
            "generation_status",
            "recovery_next_at",
            "created_at",
        ]

        connection.execute(
            sa.text(
                "INSERT INTO aics_runs(request_id, created_at, generation_key, response_id) "
                "VALUES ('new-1', '2026-09-09T00:00:00+00:00', 'key-1', 'response-1')"
            )
        )
        with pytest.raises(IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO aics_runs(request_id, created_at, generation_key, response_id) "
                    "VALUES ('new-2', '2026-09-09T00:00:01+00:00', 'key-1', 'response-2')"
                )
            )

        internal_indexes = {
            item["name"]: item
            for item in inspect(connection).get_indexes("aics_internal_work_items")
        }
        internal_uniques = {
            item["name"]: item
            for item in inspect(connection).get_unique_constraints(
                "aics_internal_work_items"
            )
        }
        assert "uq_aics_internal_work_items_idempotency" in internal_uniques
        assert {
            "idx_aics_internal_work_items_queue",
            "idx_aics_internal_work_items_store_detail",
            "idx_aics_internal_work_items_contact",
        } <= set(internal_indexes)
        assert (
            connection.execute(sa.text("SELECT version_num FROM aics_schema_version"))
            .scalar_one()
            == "20260909_02"
        )

        # Both revisions are deliberately restart-safe after partially completed
        # non-transactional MySQL DDL; SQLite verifies the same inspection path.
        _apply_revision(connection, generation)
        _apply_revision(connection, work_items)


def test_migration_types_keep_mysql_variants_and_compile_for_sqlite() -> None:
    generation = _load_revision("20260909_01_add_v3_reply_generation.py")
    work_items = _load_revision("20260909_02_add_internal_work_items.py")
    generation_types = {column.name: column.type for column in generation._COLUMNS}

    assert generation_types["generation_key"].compile(dialect=mysql.dialect()) == "VARCHAR(64)"
    assert generation_types["generation_key"].compile(dialect=sqlite.dialect()) == "VARCHAR(64)"
    assert generation_types["recovery_error"].compile(dialect=mysql.dialect()) == "LONGTEXT"
    assert generation_types["recovery_error"].compile(dialect=sqlite.dialect()) == "TEXT"
    assert work_items._varchar(191).compile(dialect=mysql.dialect()) == "VARCHAR(191)"
    assert work_items._longtext().compile(dialect=mysql.dialect()) == "LONGTEXT"

