from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from app.config import Settings
from app.graph.nodes.reply_validation import _validate_parallel_media_facts, _validate_structured_delivery_promises
from app.services.material_identity import (
    contact_key,
    fingerprint,
    govern_candidates,
    media_aliases,
    prepare_catalog,
    same_media,
)
from app.services.storage.repositories import AppRepository
from app.services.storage.sqlite_store import SQLiteStore


def repo(path):
    store = SQLiteStore(Settings(_env_file=None, AI_PATHS_DB_PATH=path))
    store.initialize()
    return AppRepository(store)


def picture(*, variant=False, size=(160, 160), fmt="PNG", quality=95):
    image = Image.new("RGB", (160, 160), (172, 150, 130))
    draw = ImageDraw.Draw(image)
    for y in range(160):
        draw.line((0, y, 159, y), fill=(80 + y // 2, 70 + y // 3, 65 + y // 4))
    draw.ellipse((25, 23, 115, 140), fill=(191, 160, 133))
    draw.rectangle((38, 40, 70, 55), fill=(77, 60, 53))
    if variant:
        draw.rectangle((100, 80, 120, 100), fill=(70, 50, 40))
    stream = BytesIO()
    image.resize(size, Image.Resampling.LANCZOS).save(stream, format=fmt, quality=quality)
    return stream.getvalue()


def state(**kwargs):
    return {"corp_id": "demo-corp", "wechat": "demo-wechat", "external_userid": "demo-external", **kwargs}


def candidate(url, content_id="effect", role="effect_evidence", **metadata):
    return {
        "content_id": content_id,
        "asset_role": role,
        "reference_text": "内容参考",
        "messages": [{"type": "image", "content": url, **metadata}],
    }


def register(repository, url, payload, **kwargs):
    plan = prepare_catalog([{"type": "image", "url": url, "bytes": payload, **kwargs}], repository.material_catalog())
    return repository.apply_material_catalog(plan)


def claim(repository, context, governed, request="request-1", fail=False):
    messages = [
        {**item["messages"][0], "client_message_id": f"{request}-{index}"}
        for index, item in enumerate(governed["candidates"])
        if item.get("canonical_id")
    ]
    with repository.store.connect() as conn:
        repository.claim_materials_in_connection(
            conn, scope=contact_key(context), request_id=request, messages=messages, bindings=governed["bindings"]
        )
        if fail:
            raise RuntimeError("synthetic transaction failure")
    return messages


def test_cross_source_reencode_restart_and_contact_isolation(tmp_path):
    database = tmp_path / "test.sqlite"
    repository = repo(database)
    first, second = "https://example.invalid/one?sig=a", "https://example.invalid/new?sig=b"
    register(repository, first, picture())
    register(repository, second, picture(fmt="JPEG", size=(320, 320)))
    candidates = [candidate(first), candidate(second, "follow", "sales_reference")]
    governed = govern_candidates(candidates, repository=repository, state=state())
    entities = [item for item in governed["candidates"] if item.get("canonical_id")]
    assert len(entities) == 1
    assert {p["asset_role"] for p in entities[0]["material_provenance"]} == {"effect_evidence", "sales_reference"}
    claim(repository, state(), governed)
    reopened = repo(database)
    assert not govern_candidates(candidates, repository=reopened, state=state())["bindings"]
    assert govern_candidates(candidates, repository=reopened, state=state(wechat="other"))["bindings"]
    assert govern_candidates(candidates, repository=reopened, state=state(corp_id="other"))["bindings"]
    assert govern_candidates(candidates, repository=reopened, state=state(external_userid="other"))["bindings"]


def test_unknown_keeps_text_then_sync_recovers_without_poisoning_memory(tmp_path):
    repository = repo(tmp_path / "test.sqlite")
    url = "https://example.invalid/unknown"
    pending = govern_candidates([candidate(url)], repository=repository, state=state())
    assert not pending["bindings"]
    assert set(pending["audit"]["pending"].values()) == {"identity_unknown"}
    assert pending["candidates"][0]["reference_text"] == "内容参考"
    assert pending["candidates"][0]["messages"] == []
    _validate_structured_delivery_promises(
        [{"type": "text", "content": "这个活动给你留着"}], {"material_identity_governed": True}
    )
    with pytest.raises(ValueError, match="structure_required"):
        _validate_structured_delivery_promises(
            [{"type": "text", "content": "图片已经发给您了"}], {"material_identity_governed": True}
        )
    with pytest.raises(ValueError, match="identity_unavailable"):
        _validate_parallel_media_facts(
            candidate(url)["messages"],
            {
                "material_identity_governed": True,
                "fact_envelope": {"structured_facts": {"case_facts": [{"image_url": url}]}},
            },
        )
    register(repository, url, picture())
    assert govern_candidates([candidate(url)], repository=repository, state=state())["bindings"]
    with repository.store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM material_claims").fetchone()["n"] == 0


def test_stable_file_id_signature_change_needs_no_network(tmp_path, monkeypatch):
    repository = repo(tmp_path / "test.sqlite")

    def no_network(*args, **kwargs):
        raise AssertionError("hot path attempted network")

    monkeypatch.setattr("httpx.AsyncClient", no_network)
    first = candidate("https://example.invalid/a?sig=one", file_id=17, file_namespace="follow_knowledge")
    second = candidate("https://other.invalid/new?sig=two", file_id=17, file_namespace="follow_knowledge")
    a = govern_candidates([first], repository=repository, state=state())
    b = govern_candidates([second], repository=repository, state=state())
    assert next(iter(a["bindings"].values()))["canonical_id"] == next(iter(b["bindings"].values()))["canonical_id"]
    claim(repository, state(), a)
    assert not govern_candidates([second], repository=repository, state=state())["bindings"]


def test_near_images_and_video_do_not_merge():
    base = fingerprint(picture(), "image")
    assert same_media(base, fingerprint(picture(fmt="JPEG", size=(320, 320)), "image"), "image")
    assert not same_media(base, fingerprint(picture(variant=True), "image"), "image")
    rows = prepare_catalog(
        [
            {"type": "image", "url": "https://example.invalid/image", "bytes": picture()},
            {"type": "video", "url": "https://example.invalid/video", "bytes": picture()},
        ]
    )
    assert rows[0]["canonical_id"] != rows[1]["canonical_id"]


def test_distinct_new_material_remains_available_after_nearby_material_claim(tmp_path):
    repository = repo(tmp_path / "distinct.sqlite")
    first, fresh = "https://example.invalid/previous", "https://example.invalid/fresh"
    register(repository, first, picture())
    register(repository, fresh, picture(variant=True))
    before = govern_candidates([candidate(first), candidate(fresh, "new")], repository=repository, state=state())
    assert len(before["bindings"]) == 2
    with pytest.raises(ValueError, match="duplicate_material"):
        _validate_parallel_media_facts(
            candidate(first)["messages"] * 2,
            {"material_identity_governed": True, "material_identity_bindings": before["bindings"]},
        )
    claim(repository, state(), govern_candidates([candidate(first)], repository=repository, state=state()))
    after = govern_candidates([candidate(first), candidate(fresh, "new")], repository=repository, state=state())
    assert list(after["bindings"]) == [f"image:{fresh}"]


def test_failed_transaction_replay_and_concurrency(tmp_path):
    repository = repo(tmp_path / "test.sqlite")
    url = "https://example.invalid/image"
    register(repository, url, picture())
    governed = govern_candidates([candidate(url, role="sales_reference")], repository=repository, state=state())
    with pytest.raises(RuntimeError):
        claim(repository, state(), governed, fail=True)
    assert govern_candidates([candidate(url)], repository=repository, state=state())["bindings"]

    def attempt(request):
        try:
            claim(repository, state(), governed, request=request)
            return request
        except ValueError as exc:
            assert "already_reserved" in str(exc)
            return ""

    with ThreadPoolExecutor(max_workers=2) as pool:
        winners = [r for r in pool.map(attempt, ["a", "b"]) if r]
    assert len(winners) == 1
    claim(repository, state(), governed, request=winners[0])
    with repository.store.connect() as conn:
        rows = conn.execute("SELECT * FROM material_claims").fetchall()
    assert len(rows) == 1 and rows[0]["asset_role"] == "sales_reference"
    assert rows[0]["status"] == "response_committed"


def test_override_and_guarded_catalog_rollback(tmp_path):
    repository = repo(tmp_path / "test.sqlite")
    url = "https://example.invalid/image"
    undo = register(repository, url, picture())
    repository.rollback_material_catalog(undo)
    assert repository.material_catalog() == []
    register(repository, url, picture())
    override = register(
        repository, url, picture(), canonical_id="operator-split", override_reason="synthetic visual collision"
    )
    assert repository.material_catalog()[0]["canonical_id"] == "operator-split"
    repository.rollback_material_catalog(override)
    assert repository.material_catalog()[0]["canonical_id"] != "operator-split"


def test_unsupported_format_and_untrusted_file_id():
    with pytest.raises(ValueError, match="unsupported_format"):
        fingerprint(b"not an image", "image")
    assert len(media_aliases({"type": "image", "url": "https://example.invalid/x", "file_id": 12})) == 1


def test_join_unknown_media_does_not_fail_text_flow(tmp_path):
    from app.graph.nodes.material_selection import create_evidence_join_node
    from app.services.trace_logger import TraceLogger

    settings = Settings(_env_file=None, AI_PATHS_DB_PATH=tmp_path / "db")
    node = create_evidence_join_node(trace_logger=TraceLogger(settings), repository=repo(tmp_path / "db"))
    output = asyncio.run(
        node(state(content_gate_result={"content_candidates": [candidate("https://example.invalid/unknown")]}))
    )
    assert output["material_identity_governed"]
    assert output["evidence_join"]["content_candidates"][0]["reference_text"] == "内容参考"


@pytest.mark.parametrize("quality", [75, 85, 95])
@pytest.mark.parametrize("size", [(80, 80), (160, 160), (320, 320)])
def test_common_jpeg_resize_is_same_entity(quality, size):
    assert same_media(
        fingerprint(picture(), "image"), fingerprint(picture(fmt="JPEG", size=size, quality=quality), "image"), "image"
    )


def test_real_process_restart_keeps_claim(tmp_path):
    import os
    import subprocess
    import sys

    database = tmp_path / "restart.sqlite"
    repository = repo(database)
    url = "https://example.invalid/process"
    register(repository, url, picture())
    governed = govern_candidates([candidate(url)], repository=repository, state=state())
    claim(repository, state(), governed)
    canonical = next(iter(governed["bindings"].values()))["canonical_id"]
    code = "from pathlib import Path; from app.config import Settings; from app.services.storage import AppRepository,SQLiteStore; import sys; r=AppRepository(SQLiteStore(Settings(_env_file=None,AI_PATHS_DB_PATH=Path(sys.argv[1])))); assert r.material_claimed_ids(sys.argv[2],[sys.argv[3]])=={sys.argv[3]}"
    subprocess.run(
        [sys.executable, "-c", code, str(database), contact_key(state()), canonical],
        check=True,
        env=os.environ.copy(),
        timeout=15,
    )


def test_split_uncommitted_collision_preserves_original_claim_and_rejects_stale_input(tmp_path):
    repository = repo(tmp_path / "split.sqlite")
    a, b = "https://example.invalid/a", "https://example.invalid/b"
    register(repository, a, picture())
    register(repository, b, picture())
    stale = govern_candidates([candidate(b)], repository=repository, state=state())
    claim(repository, state(), govern_candidates([candidate(a)], repository=repository, state=state()))
    undo = register(
        repository, b, picture(variant=True), canonical_id="reviewed-split", override_reason="synthetic false match"
    )
    assert govern_candidates([candidate(b)], repository=repository, state=state())["bindings"]
    assert not govern_candidates([candidate(a)], repository=repository, state=state())["bindings"]
    with pytest.raises(ValueError, match="changed_before_commit"):
        claim(repository, state(), stale, request="stale")
    repository.rollback_material_catalog(undo)
    repository.rollback_material_catalog(undo)
    with pytest.raises(ValueError, match="audited_migration"):
        register(repository, a, picture(), canonical_id="unsafe-release", override_reason="cannot erase claim")


def test_registry_failure_keeps_text_and_invalid_file_id_is_unknown(tmp_path):
    class Broken:
        def resolve_material_messages(self, messages):
            raise TimeoutError("synthetic outage")

    result = govern_candidates([candidate("https://example.invalid/a")], repository=Broken(), state=state())
    assert result["candidates"][0]["reference_text"] == "内容参考"
    assert set(result["audit"]["pending"].values()) == {"identity_registry_unavailable"}
    with pytest.raises(ValueError, match="identity_unknown"):
        prepare_catalog(
            [
                {
                    "type": "image",
                    "url": "https://example.invalid/a",
                    "file_id": "invalid",
                    "file_namespace": "follow_knowledge",
                }
            ]
        )


def test_sync_dry_run_apply_idempotence_and_interrupted_rollback(tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "ai_paths/scripts/sync_material_identities.py"
    manifest, database, undo = tmp_path / "manifest.json", tmp_path / "dry.sqlite", tmp_path / "undo.json"
    manifest.write_text(
        json.dumps(
            [{"type": "image", "url": "https://example.invalid/a", "file_id": 10, "file_namespace": "follow_knowledge"}]
        ),
        encoding="utf-8",
    )
    args = [sys.executable, str(script), "--database", str(database)]
    dry = subprocess.run([*args, "--manifest", str(manifest)], check=True, capture_output=True, text=True, timeout=15)
    assert json.loads(dry.stdout)["status"] == "dry_run"
    assert not database.exists()
    subprocess.run(
        [*args, "--manifest", str(manifest), "--apply", "--undo", str(undo)],
        check=True,
        capture_output=True,
        timeout=15,
    )
    repository = repo(database)
    before = repository.material_catalog()
    repository.apply_material_catalog(before, expected_before=before)
    assert repository.material_catalog() == before
    for _ in range(2):
        subprocess.run([*args, "--rollback", str(undo)], check=True, capture_output=True, timeout=15)
    assert not repository.material_catalog()


def test_response_commit_does_not_write_legacy_sent_memory():
    from app.chat_runtime import _record_sent_case_images, _record_activity_intro_image

    class NeverWrite:
        def __getattr__(self, name):
            raise AssertionError(f"legacy sent-memory accessed: {name}")

    context = state(material_identity_governed=True)
    _record_sent_case_images(NeverWrite(), context, customer_id="demo", reply_messages=[])
    _record_activity_intro_image(NeverWrite(), context, customer_id="demo", reply_messages=[], send_mode="direct")
    assert context["case_image_send_record"]["status"] == "skipped"
    assert context["activity_intro_image_send_record"]["status"] == "skipped"


def test_core_response_and_claim_commit_or_rollback_together(tmp_path):
    import sqlite3

    repository = repo(tmp_path / "core.sqlite")
    url = "https://example.invalid/core"
    register(repository, url, picture())
    governed = govern_candidates([candidate(url)], repository=repository, state=state())
    with repository.store.connect() as conn:
        conn.execute(
            "INSERT INTO conversations (id, customer_id, created_at, updated_at) VALUES ('conversation', 'demo', 'now', 'now')"
        )
        conn.execute(
            "CREATE TRIGGER synthetic_failure BEFORE INSERT ON messages BEGIN SELECT RAISE(ABORT, 'synthetic storage failure'); END"
        )
    context = state(
        request_id="core-request",
        response_id="core-response",
        material_identity_governed=True,
        material_identity_bindings=governed["bindings"],
    )
    messages = candidate(url)["messages"]

    def save():
        repository.save_v3_reply_core(
            conversation_id="conversation",
            final_state=context,
            reply_messages=messages,
            token_usage={},
            deferred_payload={},
        )

    with pytest.raises(sqlite3.IntegrityError, match="synthetic storage failure"):
        save()
    with repository.store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM material_claims").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"] == 0
        conn.execute("DROP TRIGGER synthetic_failure")
    save()
    save()
    with repository.store.connect() as conn:
        row = conn.execute("SELECT * FROM material_claims").fetchone()
        assert row["status"] == "response_committed"
        assert row["client_message_id"] == messages[0]["client_message_id"]
        assert conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"] == 1


def test_turn_budget_is_bounded_and_no_scope_never_reserves(tmp_path):
    from app.services.material_identity import MAX_TURN_MEDIA

    class Observed:
        def resolve_material_messages(self, messages):
            assert len(messages) <= MAX_TURN_MEDIA
            return {}

    candidates = [candidate(f"https://example.invalid/{index}") for index in range(1000)]
    output = govern_candidates(candidates, repository=Observed(), state={})
    assert len(output["candidates"]) <= MAX_TURN_MEDIA
    assert not output["bindings"]


def test_sync_audit_distinguishes_pending_reasons(tmp_path, monkeypatch):
    from scripts.sync_material_identities import build_plan
    from pathlib import Path

    def failed(path, *args, **kwargs):
        if str(path).endswith("timeout"):
            raise TimeoutError("synthetic")
        raise OSError("synthetic")

    monkeypatch.setattr(Path, "open", failed)
    _, failures = build_plan(
        [
            {"type": "image", "url": "https://example.invalid/1"},
            {"type": "image", "url": "https://example.invalid/2", "path": "timeout"},
            {"type": "image", "url": "https://example.invalid/3", "path": "missing"},
            {"type": "image", "url": "https://example.invalid/4", "bytes": b"unsupported"},
        ],
        [],
    )
    assert [item["reason"] for item in failures] == [
        "identity_unknown",
        "download_timeout",
        "bytes_unavailable",
        "unsupported_format",
    ]


def test_frozen_sync_plan_rejects_drift_resumes_and_protected_rollback(tmp_path):
    from scripts.sync_material_identities import apply_frozen_plan, make_frozen_plan, rollback_frozen_plan

    repository = repo(tmp_path / "frozen.sqlite")
    records = [
        {"type": "image", "file_id": index, "file_namespace": "follow_knowledge", "source": "synthetic"}
        for index in range(1, 4)
    ]
    plan = make_frozen_plan(records, repository.material_catalog(), repository, batch_size=1)
    progress = tmp_path / "progress.json"
    applied = apply_frozen_plan(repository, plan, progress)
    assert applied["completed_batches"] == [0, 1, 2]
    assert apply_frozen_plan(repository, plan, progress) == applied
    assert len(repository.material_catalog()) == 3

    # A catalog change outside the frozen plan prevents a fresh apply.
    other = repo(tmp_path / "drift.sqlite")
    other.apply_material_catalog(
        prepare_catalog([{"type": "video", "url": "https://example.invalid/drift", "bytes": b"synthetic-video"}])
    )
    with pytest.raises(ValueError, match="catalog_changed_since_plan"):
        apply_frozen_plan(
            other, dict(plan, target=f"sqlite:{other.store.db_path.resolve()}"), tmp_path / "drift-progress.json"
        )

    rollback_frozen_plan(repository, plan, progress)
    assert repository.material_catalog() == []
    assert rollback_frozen_plan(repository, plan, progress)["completed_batches"] == []


def test_frozen_sync_rollback_refuses_response_committed_claim(tmp_path):
    from scripts.sync_material_identities import apply_frozen_plan, make_frozen_plan, rollback_frozen_plan

    repository = repo(tmp_path / "claimed.sqlite")
    records = [{"type": "image", "file_id": 99, "file_namespace": "follow_knowledge"}]
    plan = make_frozen_plan(records, [], repository, batch_size=10)
    progress = tmp_path / "claimed-progress.json"
    apply_frozen_plan(repository, plan, progress)
    row = repository.material_catalog()[0]
    with repository.store.connect() as conn:
        conn.execute(
            "INSERT INTO material_claims VALUES (?, ?, ?, ?, ?, ?, 'response_committed', ?)",
            ("contact", row["canonical_id"], "request", "message", row["alias_key"], "effect_evidence", "now"),
        )
    with pytest.raises(ValueError, match="catalog_rollback_has_delivery_claims"):
        rollback_frozen_plan(repository, plan, progress)


def test_frozen_sync_applies_exact_evidence_claim_and_never_auto_releases_it(tmp_path):
    from scripts.sync_material_identities import apply_frozen_plan, make_frozen_plan, rollback_frozen_plan

    repository = repo(tmp_path / "historical.sqlite")
    records = [
        {
            "type": "image",
            "url": "https://example.invalid/historical",
            "file_id": 100,
            "file_namespace": "follow_knowledge",
        }
    ]
    identity_plan = make_frozen_plan(records, [], repository, batch_size=10)
    identity = identity_plan["batches"][0]["rows"][0]
    claims = [
        {
            "contact_key": "synthetic-contact",
            "canonical_id": identity["canonical_id"],
            "request_id": "historical-request",
            "client_message_id": "historical-evidence-id",
            "alias_key": identity["alias_key"],
            "asset_role": "historical_exact_output",
        }
    ]
    plan = make_frozen_plan(records, [], repository, batch_size=10, claims=claims)
    progress_path = tmp_path / "historical-progress.json"
    applied = apply_frozen_plan(repository, plan, progress_path)
    assert applied["completed_claim_batches"] == [0]
    with repository.store.connect() as conn:
        claim = conn.execute("SELECT * FROM material_claims").fetchone()
    assert claim["status"] == "response_committed"
    assert claim["asset_role"] == "historical_exact_output"
    with pytest.raises(ValueError, match="catalog_rollback_has_delivery_claims"):
        rollback_frozen_plan(repository, plan, progress_path)


def test_frozen_sync_snapshot_classification_is_bound_and_requires_role_coverage(tmp_path):
    from scripts.sync_material_identities import make_frozen_plan

    repository = repo(tmp_path / "classification.sqlite")
    records = [{"type": "image", "file_id": 1, "file_namespace": "follow_knowledge"}]
    report = {
        "directory_checksum": "catalog-v1",
        "media_references": 2,
        "verified_references": 1,
        "pending_references": 1,
        "roles": {"effect": 1, "activity": 1},
        "verified_roles": {"effect": 1, "activity": 1},
    }
    plan = make_frozen_plan(
        records,
        [],
        repository,
        10,
        source_checksum="catalog-v1",
        snapshot_report=report,
    )
    assert plan["summary"]["pending"] == 1
    with pytest.raises(ValueError, match="snapshot_role_without_verified_material"):
        make_frozen_plan(
            records,
            [],
            repository,
            10,
            source_checksum="catalog-v1",
            snapshot_report={**report, "verified_roles": {"effect": 1}},
        )
