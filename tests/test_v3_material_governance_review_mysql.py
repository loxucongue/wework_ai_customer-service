"""Opt-in acceptance tests against an explicitly provisioned loopback MySQL 8.4.

Set AI_PATHS_REVIEW_MYSQL_PORT to the disposable instance's port (>=10000).
Use only the synthetic review account; migrate wecom_cs before running. Tests
never load environment files or use production connection settings.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import os
import json
import subprocess
import sys
from threading import Barrier, Event
from uuid import uuid4

import pymysql
import pytest

from app.config import Settings
from app.services.material_identity import contact_key, govern_candidates, prepare_catalog
from app.services.storage.mysql_store import MySQLStore
from app.services.storage.repositories import AppRepository
from app.services.v3_reply_recovery import decode_v3_recovery_payload


@pytest.fixture
def mysql():
    port = os.environ.get("AI_PATHS_REVIEW_MYSQL_PORT")
    if not port:
        pytest.skip("requires explicit disposable loopback MySQL 8.4")
    assert 10000 <= int(port) <= 65535
    store = MySQLStore(
        Settings(
            _env_file=None,
            AICS_MYSQL_HOST="127.0.0.1",
            AICS_MYSQL_PORT=int(port),
            AICS_MYSQL_USER="review",
            AICS_MYSQL_PASSWORD="review-isolated-only",
            AICS_MYSQL_DATABASE="wecom_cs",
            AICS_MYSQL_SSL_REQUIRED=False,
        )
    )
    with store.connect() as conn:
        row = conn.execute("SELECT VERSION() AS version, @@transaction_isolation AS isolation").fetchone()
        assert row["version"].startswith("8.4.") and row["isolation"] == "REPEATABLE-READ"
    store.initialize()
    yield AppRepository(store)
    store.close()


def media(repository):
    tag = uuid4().hex
    urls = [f"https://example.invalid/{tag}/{i}?sig=synthetic-{tag}" for i in range(2)]
    records = [dict(type="image", url=u, file_id=int(tag, 16), file_namespace="follow_knowledge") for u in urls]
    repository.apply_material_catalog(prepare_catalog(records, repository.material_catalog()))
    context = dict(corp_id="review-corp", wechat="review-wechat", external_userid=tag)
    governed = [
        govern_candidates(
            [dict(content_id=f"source-{i}", asset_role="effect_evidence", messages=[dict(type="image", content=u)])],
            repository=repository,
            state=context,
        )
        for i, u in enumerate(urls)
    ]
    return context, governed


def messages(governed, request):
    return [
        dict(**entity["messages"][0], client_message_id=f"{request}-{index}")
        for index, entity in enumerate(governed["candidates"])
        if entity.get("canonical_id")
    ]


def claim(repository, conn, context, governed, request):
    repository.claim_materials_in_connection(
        conn,
        scope=contact_key(context),
        request_id=request,
        messages=messages(governed, request),
        bindings=governed["bindings"],
    )


@pytest.mark.parametrize("first", [0, 1])
@pytest.mark.parametrize("rollback", [False, True])
def test_cross_alias_connections_commit_order_and_rollback(mysql, first, rollback):
    context, governed = media(mysql)
    gate, acquired, peer_started = Barrier(2), Event(), Event()
    connection_ids = set()

    def worker(index):
        try:
            with mysql.store.connect() as conn:
                connection_ids.add(conn.execute("SELECT CONNECTION_ID() AS id").fetchone()["id"])
                # Establish an old consistent snapshot on BOTH connections.
                conn.execute("SELECT COUNT(*) FROM material_claims").fetchone()
                gate.wait(timeout=10)
                if index == first:
                    claim(mysql, conn, context, governed[index], f"{context['external_userid']}-{index}")
                    acquired.set()
                    assert peer_started.wait(10)
                    if rollback:
                        raise RuntimeError("synthetic transaction rollback")
                else:
                    assert acquired.wait(10)
                    peer_started.set()
                    claim(mysql, conn, context, governed[index], f"{context['external_userid']}-{index}")
            return "committed"
        except ValueError as exc:
            assert str(exc) == "material_already_reserved_by_other_response"
            return "conflict"
        except RuntimeError as exc:
            assert str(exc) == "synthetic transaction rollback"
            return "rolled_back"

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(worker, range(2)))
    assert len(connection_ids) == 2
    assert sorted(result) == sorted(["committed", "rolled_back" if rollback else "conflict"])
    winner = result.index("committed")
    with mysql.store.connect() as conn:
        claim(mysql, conn, context, governed[winner], f"{context['external_userid']}-{winner}")
        rows = conn.execute("SELECT * FROM material_claims WHERE contact_key=?", (contact_key(context),)).fetchall()
        assert len(rows) == 1 and rows[0]["status"] == "response_committed"
    print("independent connection IDs", sorted(connection_ids), "outcome", result)


def test_same_response_concurrent_replay(mysql):
    context, governed = media(mysql)
    gate = Barrier(2)
    request = uuid4().hex

    def replay(_):
        with mysql.store.connect() as conn:
            conn.execute("SELECT COUNT(*) FROM material_claims").fetchone()
            gate.wait(timeout=10)
            claim(mysql, conn, context, governed[0], request)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(replay, range(2)))
    with mysql.store.connect() as conn:
        assert (
            conn.execute("SELECT COUNT(*) AS n FROM material_claims WHERE request_id=?", (request,)).fetchone()["n"]
            == 1
        )


def test_core_tail_failure_rolls_back_response_claim_and_job(mysql):
    context, governed = media(mysql)
    request = uuid4().hex
    context.update(
        request_id=request,
        response_id=f"response-{request}",
        generation_key=f"generation-{request}",
        material_identity_governed=True,
        material_identity_bindings=governed[0]["bindings"],
    )
    with mysql.store.connect() as conn:
        conn.execute(
            "INSERT INTO conversations (id,customer_id,created_at,updated_at) VALUES (?, 'synthetic','now','now')",
            (request,),
        )
    # Trigger only this synthetic request, through this disposable instance's admin.
    admin = pymysql.connect(
        host="127.0.0.1", port=mysql.store.settings.aics_mysql_port, user="root", database="wecom_cs", autocommit=True
    )
    trigger = f"review_{request}"
    with admin.cursor() as cursor:
        cursor.execute(
            f"CREATE TRIGGER {trigger} BEFORE INSERT ON aics_messages FOR EACH ROW "
            f"BEGIN IF NEW.request_id='{request}' THEN SIGNAL SQLSTATE '45000' "
            "SET MESSAGE_TEXT='synthetic-tail-failure'; END IF; END"
        )

    def save():
        return mysql.save_v3_reply_core(
            conversation_id=request,
            final_state=context,
            reply_messages=messages(governed[0], request),
            token_usage={},
            deferred_payload={"synthetic": True},
        )

    try:
        with pytest.raises(Exception, match="synthetic-tail-failure"):
            save()
        with mysql.store.connect() as conn:
            for table in ("runs", "messages", "material_claims"):
                assert not conn.execute(f"SELECT * FROM {table} WHERE request_id=?", (request,)).fetchone()
    finally:
        with admin.cursor() as cursor:
            cursor.execute(f"DROP TRIGGER {trigger}")
        admin.close()
    result = save()
    assert result["statement_count"] == 7
    save()
    with mysql.store.connect() as conn:
        for table in ("runs", "messages", "material_claims"):
            assert (
                conn.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE request_id=?", (request,)).fetchone()["n"] == 1
            )
        row = conn.execute("SELECT client_message_id FROM material_claims WHERE request_id=?", (request,)).fetchone()
        assert row["client_message_id"] == context["reply_messages"][0]["client_message_id"]
        assert (
            '"pending"'
            in conn.execute("SELECT output_snapshot FROM runs WHERE request_id=?", (request,)).fetchone()[
                "output_snapshot"
            ]
        )
        stored = json.loads(
            conn.execute("SELECT output_snapshot FROM runs WHERE request_id=?", (request,)).fetchone()[
                "output_snapshot"
            ]
        )
        assert "?sig=synthetic-" in stored["reply_messages"][0]["content"]
        assert stored["v3_response_snapshot"]
        replay = decode_v3_recovery_payload(stored["v3_response_snapshot"])
        assert replay["reply_messages"][0]["content"] == stored["reply_messages"][0]["content"]
        assert stored["performance"]["database"]["statement_count"] == 7
    assert "?sig=synthetic-" not in json.dumps(mysql.get_run(request))


@pytest.mark.parametrize("operation", ["reassign", "rollback"])
def test_catalog_stale_snapshot_cannot_reassign_claimed_alias(mysql, operation):
    context, governed = media(mysql)
    alias = next(iter(governed[1]["bindings"].values()))["alias_key"]
    existing = next(r for r in mysql.material_catalog() if r["alias_key"] == alias)
    changed = dict(existing, canonical_id=uuid4().hex, override_reason="synthetic split")
    # Start the catalog transaction BEFORE the concurrent claim commits. This
    # reproduces a stale RR snapshot without mocking any SQL/locking behavior.
    original_connect = mysql.store.connect

    @contextmanager
    def old_snapshot():
        with original_connect() as conn:
            conn.execute("SELECT COUNT(*) FROM material_claims").fetchone()
            with original_connect() as other:
                claim(mysql, other, context, governed[1], uuid4().hex)
            yield conn

    mysql.store.connect = old_snapshot
    try:
        with pytest.raises(ValueError, match="audited_migration|delivery_claims"):
            if operation == "reassign":
                mysql.apply_material_catalog([changed])
            else:
                mysql.rollback_material_catalog({"before": [{"alias_key": alias, "absent": True}], "after": [existing]})
    finally:
        mysql.store.connect = original_connect
    assert next(r for r in mysql.material_catalog() if r["alias_key"] == alias) == existing


def test_catalog_apply_reapply_stale_plan_and_rollback(mysql):
    tag = uuid4().hex
    plan = prepare_catalog([dict(type="video", url=f"https://example.invalid/{tag}", bytes=tag.encode())])
    before = [dict(alias_key=row["alias_key"], absent=True) for row in plan]
    # Undo after interruption before apply is safe and repeatable.
    undo = dict(before=before, after=plan)
    mysql.rollback_material_catalog(undo)
    mysql.apply_material_catalog(plan, expected_before=before)
    mysql.apply_material_catalog(plan, expected_before=plan)
    with pytest.raises(ValueError, match="changed_since_plan"):
        mysql.apply_material_catalog(plan, expected_before=before)
    mysql.rollback_material_catalog(undo)
    mysql.rollback_material_catalog(undo)


def test_restarted_process_and_contact_isolation(mysql):
    context, governed = media(mysql)
    with mysql.store.connect() as conn:
        claim(mysql, conn, context, governed[0], uuid4().hex)
    canonical = next(iter(governed[0]["bindings"].values()))["canonical_id"]
    assert mysql.material_claimed_ids(contact_key(context), [canonical]) == {canonical}
    for key in ("corp_id", "wechat", "external_userid"):
        other = dict(context, **{key: uuid4().hex})
        assert not mysql.material_claimed_ids(contact_key(other), [canonical])
    script = (
        "from app.config import Settings; from app.services.storage.mysql_store import MySQLStore; "
        "from app.services.storage.repositories import AppRepository; import sys; "
        "s=Settings(_env_file=None,AICS_MYSQL_HOST='127.0.0.1',AICS_MYSQL_PORT=int(sys.argv[1]),"
        "AICS_MYSQL_USER='review',AICS_MYSQL_PASSWORD='review-isolated-only',"
        "AICS_MYSQL_DATABASE='wecom_cs',AICS_MYSQL_SSL_REQUIRED=False); "
        "r=AppRepository(MySQLStore(s)); assert r.material_claimed_ids(sys.argv[2],[sys.argv[3]])=={sys.argv[3]}"
    )
    subprocess.run(
        [sys.executable, "-c", script, str(mysql.store.settings.aics_mysql_port), contact_key(context), canonical],
        check=True,
        timeout=20,
    )
