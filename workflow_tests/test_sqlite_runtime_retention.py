from __future__ import annotations

from pathlib import Path

from app.config import Settings
from app.services.storage import AppRepository, SQLiteStore


def test_sqlite_runtime_retention_removes_only_expired_rows(tmp_path: Path) -> None:
    store = SQLiteStore(Settings(AI_PATHS_DB_PATH=tmp_path / "retention.db"))
    store.initialize()
    repository = AppRepository(store)

    with store.connect() as connection:
        connection.execute(
            """
            INSERT INTO conversations
                (id, customer_id, title, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "conversation",
                "customer",
                "retention test",
                "2000-01-01T00:00:00+00:00",
                "2000-01-01T00:00:00+00:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO runs (request_id, conversation_id, customer_id, created_at)
            VALUES (?, ?, ?, ?)
            """,
            ("old-run", "conversation", "customer", "2000-01-01T00:00:00+00:00"),
        )
        connection.execute(
            """
            INSERT INTO runs (request_id, conversation_id, customer_id, created_at)
            VALUES (?, ?, ?, ?)
            """,
            ("new-run", "conversation", "customer", "2999-01-01T00:00:00+00:00"),
        )
        connection.execute(
            """
            INSERT INTO node_traces (id, request_id, node_name, created_at)
            VALUES (?, ?, ?, ?)
            """,
            ("old-trace", "old-run", "planner", "2000-01-01T00:00:00+00:00"),
        )
        connection.execute(
            """
            INSERT INTO node_traces (id, request_id, node_name, created_at)
            VALUES (?, ?, ?, ?)
            """,
            ("new-trace", "new-run", "planner", "2999-01-01T00:00:00+00:00"),
        )

    result = repository.prune_runtime_history(trace_days=14, run_days=90)
    assert result == {"node_traces": 1, "runs": 1}

    with store.connect() as connection:
        assert connection.execute(
            "SELECT request_id FROM runs WHERE request_id='old-run'"
        ).fetchone() is None
        assert connection.execute(
            "SELECT request_id FROM runs WHERE request_id='new-run'"
        ).fetchone()
        assert connection.execute(
            "SELECT id FROM node_traces WHERE id='old-trace'"
        ).fetchone() is None
        assert connection.execute(
            "SELECT id FROM node_traces WHERE id='new-trace'"
        ).fetchone()
