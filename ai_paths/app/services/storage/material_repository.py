from __future__ import annotations

from typing import Any

from app.services.material_identity import MAX_CATALOG_ITEMS, MAX_TURN_MEDIA, media_aliases, prepare_catalog
from app.services.storage.serialization import dumps, loads_dict, utc_now_iso


class MaterialRepositoryMixin:
    def material_catalog(self) -> list[dict[str, Any]]:
        with self.store.connect() as conn:
            rows = conn.execute("SELECT * FROM material_identities LIMIT ?", (MAX_CATALOG_ITEMS + 1,)).fetchall()
        if len(rows) > MAX_CATALOG_ITEMS:
            raise ValueError("catalog_limit_exceeded")
        return [_decode(row) for row in rows]

    def apply_material_catalog(
        self, rows: list[dict[str, Any]], *, expected_before: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        """Atomically apply a bounded plan; return exact undo data for offline rollback.

        Claimed aliases cannot be reassigned by ordinary sync. Such a split must
        preserve the original delivery provenance and is an explicit override.
        """
        if len(rows) > MAX_CATALOG_ITEMS:
            raise ValueError("catalog_limit_exceeded")
        before = []
        after = []
        with self.store.connect() as conn:
            for index, row in enumerate(rows):
                conn.execute(
                    "UPDATE material_identities SET canonical_id=canonical_id WHERE alias_key=?", (row["alias_key"],)
                )
                old = conn.execute(
                    "SELECT * FROM material_identities WHERE alias_key=?", (row["alias_key"],)
                ).fetchone()
                prior = _decode(old) if old else {"alias_key": row["alias_key"], "absent": True}
                if expected_before is not None and prior != expected_before[index]:
                    raise ValueError("catalog_changed_since_plan")
                if old and old["canonical_id"] != row["canonical_id"]:
                    if not row.get("override_reason"):
                        raise ValueError("identity_reassignment_requires_override")
                    claims = conn.execute(
                        "SELECT 1 FROM material_claims WHERE alias_key=? LIMIT 1", (row["alias_key"],)
                    ).fetchone()
                    if claims:
                        raise ValueError("claimed_identity_reassignment_requires_audited_migration")
                before.append(prior)
                conn.execute(
                    """INSERT INTO material_identities
                    (alias_key, canonical_id, media_type, fingerprint_json, provenance_json, override_reason)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(alias_key) DO UPDATE SET canonical_id=excluded.canonical_id,
                    media_type=excluded.media_type, fingerprint_json=excluded.fingerprint_json,
                    provenance_json=excluded.provenance_json, override_reason=excluded.override_reason""",
                    (
                        row["alias_key"],
                        row["canonical_id"],
                        row["media_type"],
                        dumps(row.get("fingerprint") or {}),
                        dumps(row.get("provenance") or {}),
                        str(row.get("override_reason") or ""),
                    ),
                )
                after.append(row)
            count = conn.execute("SELECT COUNT(*) AS n FROM material_identities").fetchone()["n"]
            if count > MAX_CATALOG_ITEMS:
                raise ValueError("catalog_limit_exceeded")
        return {"before": before, "after": after}

    def rollback_material_catalog(self, undo: dict[str, Any]) -> None:
        with self.store.connect() as conn:
            for expected, prior in zip(reversed(undo["after"]), reversed(undo["before"])):
                conn.execute(
                    "UPDATE material_identities SET canonical_id=canonical_id WHERE alias_key=?",
                    (expected["alias_key"],),
                )
                row = conn.execute(
                    "SELECT * FROM material_identities WHERE alias_key=?", (expected["alias_key"],)
                ).fetchone()
                if (not row and prior.get("absent")) or (row and _decode(row) == prior):
                    continue  # Interrupted before commit, or already rolled back.
                if not row or _decode(row) != expected:
                    raise ValueError("catalog_changed_since_import")
                if conn.execute(
                    "SELECT 1 FROM material_claims WHERE alias_key=? LIMIT 1", (row["alias_key"],)
                ).fetchone():
                    raise ValueError("catalog_rollback_has_delivery_claims")
                conn.execute("DELETE FROM material_identities WHERE alias_key=?", (expected["alias_key"],))
                if not prior.get("absent"):
                    conn.execute(
                        "INSERT INTO material_identities VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            prior["alias_key"],
                            prior["canonical_id"],
                            prior["media_type"],
                            dumps(prior["fingerprint"]),
                            dumps(prior["provenance"]),
                            prior["override_reason"],
                        ),
                    )

    def resolve_material_messages(self, messages: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        if len(messages) > MAX_TURN_MEDIA:
            raise ValueError("turn_media_limit_exceeded")
        keys = list(dict.fromkeys(key for message in messages for key in media_aliases(message)))
        if not keys:
            return {}
        with self.store.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM material_identities WHERE alias_key IN ({','.join('?' for _ in keys)})", keys
            ).fetchall()
        result = {row["alias_key"]: _decode(row) for row in rows}
        # A trusted stable upstream file ID is sufficient without byte access.
        # Do not persist/interpret a URL-only item as a physical identity.
        for message in messages:
            aliases = media_aliases(message)
            if (
                not aliases
                or message.get("file_namespace") != "follow_knowledge"
                or not str(message.get("file_id") or "").isdecimal()
                or int(message["file_id"]) <= 0
            ):
                continue
            known = [result[key] for key in aliases if key in result]
            if len({row["canonical_id"] for row in known}) > 1:
                for key in aliases:
                    result.pop(key, None)
                continue
            if all(key in result for key in aliases):
                continue
            plan = prepare_catalog([message], known)
            # Insert only: a concurrent sync or resolver wins the unique alias.
            with self.store.connect() as conn:
                for row in plan:
                    conn.execute(
                        "INSERT OR IGNORE INTO material_identities VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            row["alias_key"],
                            row["canonical_id"],
                            row["media_type"],
                            dumps(row["fingerprint"]),
                            dumps(row["provenance"]),
                            row["override_reason"],
                        ),
                    )
                found = conn.execute(
                    f"SELECT * FROM material_identities WHERE alias_key IN ({','.join('?' for _ in aliases)})", aliases
                ).fetchall()
                if conn.execute("SELECT COUNT(*) AS n FROM material_identities").fetchone()["n"] > MAX_CATALOG_ITEMS:
                    raise ValueError("catalog_limit_exceeded")
                if len({row["canonical_id"] for row in found}) == 1:
                    result.update({row["alias_key"]: _decode(row) for row in found})
                else:
                    for key in aliases:
                        result.pop(key, None)
        return result

    def material_claimed_ids(self, scope: str, canonical_ids: list[str]) -> set[str]:
        ids = list(dict.fromkeys(canonical_ids))
        if not scope or not ids:
            return set()
        if len(ids) > MAX_TURN_MEDIA:
            raise ValueError("turn_media_limit_exceeded")
        with self.store.connect() as conn:
            rows = conn.execute(
                f"SELECT canonical_id FROM material_claims WHERE contact_key=? AND canonical_id IN ({','.join('?' for _ in ids)})",
                [scope, *ids],
            ).fetchall()
        return {row["canonical_id"] for row in rows}

    def claim_materials_in_connection(
        self, conn: Any, *, scope: str, request_id: str, messages: list[dict[str, Any]], bindings: dict[str, Any]
    ) -> None:
        from app.services.material_identity import media_url

        seen = set()
        for message in messages:
            if message.get("type") not in {"image", "video"}:
                continue
            binding = bindings.get(f"{message['type']}:{media_url(message)}") or {}
            canonical = str(binding.get("canonical_id") or "")
            if not scope or not canonical:
                raise ValueError("material_identity_required_at_commit")
            if canonical in seen:
                raise ValueError("duplicate_material_in_response")
            seen.add(canonical)
            # Serialize against directory overrides and reject stale Reply input.
            conn.execute(
                "UPDATE material_identities SET canonical_id=canonical_id WHERE alias_key=?", (binding["alias_key"],)
            )
            identity = conn.execute(
                "SELECT canonical_id FROM material_identities WHERE alias_key=?", (binding["alias_key"],)
            ).fetchone()
            if not identity or identity["canonical_id"] != canonical:
                raise ValueError("material_identity_changed_before_commit")
            conn.execute(
                """INSERT OR IGNORE INTO material_claims
                (contact_key, canonical_id, request_id, client_message_id, alias_key, asset_role, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'response_committed', ?)""",
                (
                    scope,
                    canonical,
                    request_id,
                    str(message.get("client_message_id") or ""),
                    binding["alias_key"],
                    str(binding.get("asset_role") or ""),
                    utc_now_iso(),
                ),
            )
            row = conn.execute(
                "SELECT request_id, client_message_id FROM material_claims WHERE contact_key=? AND canonical_id=?",
                (scope, canonical),
            ).fetchone()
            if row["request_id"] != request_id or row["client_message_id"] != str(
                message.get("client_message_id") or ""
            ):
                raise ValueError("material_already_reserved_by_other_response")


def _decode(row: Any) -> dict[str, Any]:
    return {
        "alias_key": row["alias_key"],
        "canonical_id": row["canonical_id"],
        "media_type": row["media_type"],
        "fingerprint": loads_dict(row["fingerprint_json"]),
        "provenance": loads_dict(row["provenance_json"]),
        "override_reason": row["override_reason"],
    }
