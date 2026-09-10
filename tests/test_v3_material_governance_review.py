"""Independent acceptance regressions; unresolved failures block integration.

These tests require no database connection, model, or third-party interface.
The locking SELECT is used by save_v3_reply_core on production MySQL.
"""

import pytest
from io import BytesIO
from PIL import Image

from app.services.storage.mysql_store import MySQLStore, _runtime_sql_guard
from app.services.material_identity import fingerprint, prepare_catalog, same_media


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT output_snapshot FROM runs WHERE request_id=? FOR UPDATE",
        "SELECT request_id FROM material_claims WHERE contact_key=? AND canonical_id=? FOR UPDATE",
    ],
)
def test_mysql_locked_reads_remain_allowed_by_runtime_guard(sql):
    # SQL translation is pure; avoid constructing an engine or loading settings.
    store = object.__new__(MySQLStore)
    store.table_prefix = "aics_"
    prepared = store.prepare_sql(sql)
    assert "FOR UPDATE" in prepared
    assert "aics_" in prepared


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE messages SET content=? WHERE id=?",
        "DELETE FROM customer_member_relations WHERE id=?",
        "DROP TABLE aics_material_claims",
    ],
)
def test_runtime_guard_still_rejects_source_writes_and_ddl(sql):
    store = object.__new__(MySQLStore)
    store.table_prefix = "aics_"
    if sql.startswith("UPDATE messages"):
        # This marker names the read-only platform archive, not our own messages.
        sql = sql.replace("messages", "__source_archive_messages__")
    with pytest.raises(RuntimeError):
        store.prepare_sql(sql)


@pytest.mark.parametrize(
    "sql",
    [
        " /* audit */ SeLeCt id FROM aics_runs WHERE id=%s FoR\nUpDaTe; -- end\n",
        "SELECT id FROM aics_runs FOR /* lock */ UPDATE NOWAIT",
        "SELECT id FROM aics_runs FOR UPDATE SKIP LOCKED # end",
        "SELECT id FROM aics_runs FOR SHARE",
        "SELECT 'UPDATE source; DELETE; ''quote''' FROM aics_runs",
        "SELECT `id` FROM `aics_runs` FOR UPDATE",
        "WITH candidates AS (SELECT id FROM aics_runs) SELECT id FROM candidates",
        "SHOW STATUS LIKE 'Ssl_cipher'",
        "EXPLAIN SELECT id FROM aics_material_claims",
        "/* prefix */ UPDATE /* comment */ `aics_runs` SET output_snapshot=%s WHERE request_id=%s",
        "INSERT IGNORE INTO aics_runs (request_id) VALUES (%s)",
        "INSERT INTO aics_runs (request_id) VALUES (%s) ON DUPLICATE KEY UPDATE request_id=VALUES(request_id)",
        "INSERT INTO aics_runs (request_id) SELECT id FROM source_table",
        "DELETE FROM aics_runs WHERE request_id=%s",
        "REPLACE INTO aics_runs (request_id) VALUES (%s)",
    ],
)
def test_runtime_guard_accepts_required_single_statement_shapes(sql):
    _runtime_sql_guard(sql, prefix="aics_")


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE source_table SET id=%s",
        "INSERT INTO source_table (id) VALUES (%s)",
        "DELETE FROM source_table WHERE id=%s",
        "REPLACE INTO source_table (id) VALUES (%s)",
        "SELECT 1; UPDATE source_table SET id=1",
        "UPDATE aics_runs SET id=1; DELETE FROM source_table",
        "SELECT 1; SELECT 2",
        "SELECT 1;;",
        "SELECT 1 /*!50000 INTO OUTFILE '/tmp/x' */",
        "/*! UPDATE source_table SET id=1 */ SELECT 1",
        "SELECT /*+ SET_VAR(sql_mode='') */ 1",
        "SELECT 1 INTO OUTFILE '/tmp/x'",
        "SELECT 1 INTO DUMPFILE '/tmp/x'",
        "SELECT @x:=1",
        "SELECT 'unterminated",
        "SELECT 1 /* unterminated",
        "SELECT 'ambiguous\\' UPDATE source_table SET id=1",
        "UPDATE aics_runs, source_table SET source_table.id=1",
        "UPDATE aics_runs JOIN source_table SET source_table.id=1",
        "UPDATE aics_runs . source_table SET id=1",
        "UPDATE `aics_runs.source_table` SET id=1",
        "UPDATE `aics_runs``source_table` SET id=1",
        "INSERT INTO aics_runs . source_table (id) VALUES (1)",
        "DELETE FROM aics_runs USING aics_runs JOIN source_table",
        "DELETE source_table FROM aics_runs JOIN source_table",
        "WITH c AS (SELECT 1) UPDATE source_table SET id=1",
        "SELECT 1--not-a-comment\n UPDATE source_table SET id=1",
        "SELECT 1 # comment\n UPDATE source_table SET id=1",
        "UPDATE /* ordinary comment */ source_table SET id=1",
        "CALL unverified_procedure()",
        "SET @query='DELETE FROM source_table'",
        "DROP TABLE aics_runs",
        "TRUNCATE aics_runs",
        "",
    ],
)
def test_runtime_guard_rejects_writes_obfuscation_and_multiple_statements(sql):
    with pytest.raises(RuntimeError):
        _runtime_sql_guard(sql, prefix="aics_")


def solid_image(color):
    stream = BytesIO()
    Image.new("RGB", (128, 128), color).save(stream, format="PNG")
    return stream.getvalue()


def test_reused_url_cannot_silently_merge_new_bytes_into_old_identity():
    original = dict(type="image", url="https://example.invalid/reused", bytes=solid_image("red"))
    previous = prepare_catalog([original])
    replacement = dict(original, bytes=solid_image("blue"))
    assert not same_media(fingerprint(original["bytes"], "image"), fingerprint(replacement["bytes"], "image"), "image")
    with pytest.raises(ValueError, match="identity_conflict_requires_override"):
        prepare_catalog([replacement], previous)


def test_byte_less_sync_preserves_manual_override_and_fingerprint():
    original = dict(
        type="image",
        url="https://example.invalid/manual",
        bytes=solid_image("red"),
        canonical_id="manual-split",
        override_reason="operator-reviewed split",
    )
    previous = prepare_catalog([original])
    refreshed = prepare_catalog([dict(type="image", url=original["url"])], previous)
    assert refreshed[0]["canonical_id"] == previous[0]["canonical_id"]
    assert refreshed[0]["fingerprint"] == previous[0]["fingerprint"]
    assert refreshed[0]["override_reason"] == previous[0]["override_reason"]


def test_manual_canonical_ids_have_one_case_on_sqlite_and_mysql():
    with pytest.raises(ValueError, match="canonical_id_invalid"):
        prepare_catalog(
            [
                dict(
                    type="image",
                    url="https://example.invalid/manual-case",
                    canonical_id="MixedCase",
                    override_reason="synthetic case collision",
                )
            ]
        )


def test_perceptual_versions_and_image_video_namespaces_do_not_cross():
    first = fingerprint(solid_image("red"), "image")
    old = dict(first, version="old-fingerprint-algorithm", sha256="different-bytes")
    assert not same_media(first, old, "image")
    rows = prepare_catalog(
        [dict(type=kind, url="https://example.invalid/shared", bytes=solid_image("red")) for kind in ("image", "video")]
    )
    assert len({row["canonical_id"] for row in rows}) == 2


def test_audit_redacts_signed_urls_without_mutating_stable_response():
    import copy
    import json
    from app.services.trace_logger import audit_snapshot

    url = "https://example.invalid/a.png?X-Amz-Signature=synthetic-secret&token=credential#fragment"
    original = {
        "reply_messages": [{"type": "image", "content": url}],
        "bindings": {f"image:{url}": {"canonical_id": "synthetic"}},
        "v3_response_snapshot": "opaque-replay-blob",
        "bytes": b"synthetic-media",
    }
    saved = copy.deepcopy(original)
    audit = json.dumps(audit_snapshot(original))
    assert "synthetic-secret" not in audit and "credential" not in audit
    assert "opaque-replay-blob" not in audit and "synthetic-media" not in audit
    assert original == saved


def test_response_commit_trace_never_names_a_send_or_contains_signed_url(tmp_path):
    import json
    from app.chat_runtime import _record_sent_case_images
    from app.config import Settings
    from app.services.trace_logger import TraceLogger

    url = "https://example.invalid/a.png?sig=synthetic-secret"
    candidate = {
        "content_id": "media:synthetic",
        "asset_role": "effect_evidence",
        "messages": [{"type": "image", "content": url}],
    }
    state = {
        "request_id": "review",
        "material_identity_governed": True,
        "selected_content_ids": [candidate["content_id"]],
        "evidence_join": {"content_candidates": [candidate]},
    }
    _record_sent_case_images(None, state, customer_id="synthetic", reply_messages=candidate["messages"])
    entry = state["trace"][-1]
    assert entry["node"] == "case_image_response_commit"
    assert entry["tool_calls"][0]["name"] == "record_material_response_commit"
    assert entry["output_snapshot"]["status"] == "response_committed"
    assert "synthetic-secret" not in json.dumps(entry)
    logger = TraceLogger(Settings(_env_file=None, AI_PATHS_LOG_DIR=tmp_path))
    logger.log_dir = tmp_path
    assert "synthetic-secret" not in logger.write_run(state).read_text(encoding="utf-8")
