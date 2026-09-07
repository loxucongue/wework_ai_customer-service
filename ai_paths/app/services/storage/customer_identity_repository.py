from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.customer_identity import IdentityContractError, customer_identity_from_mapping
from app.services.storage.serialization import dumps, loads_dict, utc_now_iso


class CustomerIdentityRepositoryMixin:
    def customer_identity_quality(self) -> dict[str, Any]:
        tables = ("conversations", "outreach_plans", "first_day_outreach_runs", "sop_send_tasks")
        legacy: dict[str, dict[str, int]] = {}
        with self.store.connect() as conn:
            link_rows = conn.execute(
                "SELECT verification_status, COUNT(*) AS total FROM customer_identity_links GROUP BY verification_status"
            ).fetchall()
            for table in tables:
                row = conn.execute(
                    f"""
                    SELECT COUNT(*) AS total,
                           SUM(CASE WHEN LOWER(customer_id) LIKE 'wm%' OR customer_id=external_userid THEN 1 ELSE 0 END) AS mixed,
                           SUM(CASE WHEN external_userid='' THEN 1 ELSE 0 END) AS missing_external_userid
                    FROM {table}
                    """
                ).fetchone()
                legacy[table] = {
                    "total": int(row["total"] or 0),
                    "mixed": int(row["mixed"] or 0),
                    "missing_external_userid": int(row["missing_external_userid"] or 0),
                }
        return {
            "identity_links": {str(row["verification_status"] or "unknown"): int(row["total"] or 0) for row in link_rows},
            "legacy_tables": legacy,
        }

    def list_customer_identity_conflicts(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self.store.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM customer_identity_links
                WHERE verification_status='conflict'
                ORDER BY last_seen_at DESC
                LIMIT ?
                """,
                (max(1, min(int(limit or 100), 500)),),
            ).fetchall()
        return [{**dict(row), "conflict": loads_dict(row["conflict_json"])} for row in rows]

    def observe_customer_identity(
        self,
        *,
        corp_id: str,
        wechat: str,
        external_userid: str,
        customer_id: str,
        user_id: str = "",
        customer_add_wechat_id: str = "",
        source: str,
        verified: bool = False,
    ) -> dict[str, Any]:
        try:
            identity = customer_identity_from_mapping(
                {
                    "corp_id": corp_id,
                    "wechat": wechat,
                    "external_userid": external_userid,
                    "platform_customer_id": customer_id,
                    "platform_user_id": user_id,
                    "customer_add_wechat_id": customer_add_wechat_id,
                },
                allow_empty_platform_customer_id=True,
            )
        except IdentityContractError as exc:
            return {"status": "rejected", "reason": "identity_contract_violation", "detail": str(exc)}
        if not identity.corp_id or not identity.wechat or not identity.external_userid:
            return {"status": "skipped", "reason": "missing_sales_contact_scope"}

        now = utc_now_iso()
        with self.store.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM customer_identity_links
                WHERE corp_id=? AND LOWER(wechat)=LOWER(?) AND external_userid=?
                LIMIT 1
                """,
                (identity.corp_id, identity.wechat, identity.external_userid),
            ).fetchone()
            if row is None:
                record_id = str(uuid4())
                conn.execute(
                    """
                    INSERT INTO customer_identity_links
                        (id,corp_id,wechat,external_userid,platform_customer_id,platform_user_id,
                         customer_add_wechat_id,platform_customer_id_source,verification_status,
                         conflict_json,first_seen_at,last_seen_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        record_id,
                        identity.corp_id,
                        identity.wechat,
                        identity.external_userid,
                        identity.platform_customer_id,
                        identity.platform_user_id,
                        identity.customer_add_wechat_id,
                        source,
                        "verified" if verified else "observed",
                        "{}",
                        now,
                        now,
                    ),
                )
                return {"status": "created", "id": record_id, **identity.as_dict()}

            existing = dict(row)
            existing_platform_id = str(existing.get("platform_customer_id") or "")
            incoming_platform_id = identity.platform_customer_id
            if existing_platform_id and incoming_platform_id and existing_platform_id != incoming_platform_id:
                conflict = loads_dict(existing.get("conflict_json"))
                candidates = set(str(item) for item in conflict.get("platform_customer_id_candidates") or [] if str(item))
                candidates.update((existing_platform_id, incoming_platform_id))
                conflict.update(
                    {
                        "platform_customer_id_candidates": sorted(candidates),
                        "last_source": source,
                        "last_seen_at": now,
                    }
                )
                existing_verified = str(existing.get("verification_status") or "") == "verified"
                if verified and not existing_verified:
                    conflict["resolved_by"] = "verified_platform_lookup"
                    conflict["previous_platform_customer_id"] = existing_platform_id
                    conn.execute(
                        """
                        UPDATE customer_identity_links
                        SET platform_customer_id=?, platform_user_id=?, customer_add_wechat_id=?,
                            platform_customer_id_source=?, verification_status='verified',
                            conflict_json=?, last_seen_at=?
                        WHERE id=?
                        """,
                        (
                            incoming_platform_id,
                            identity.platform_user_id or str(existing.get("platform_user_id") or ""),
                            identity.customer_add_wechat_id or str(existing.get("customer_add_wechat_id") or ""),
                            source,
                            dumps(conflict),
                            now,
                            existing["id"],
                        ),
                    )
                    return {
                        "status": "resolved_by_verified_source",
                        "id": str(existing["id"]),
                        "platform_customer_id": incoming_platform_id,
                        "conflict": conflict,
                    }
                if existing_verified and not verified:
                    conflict["ignored_unverified_candidate"] = incoming_platform_id
                    conn.execute(
                        """
                        UPDATE customer_identity_links
                        SET conflict_json=?, last_seen_at=?
                        WHERE id=?
                        """,
                        (dumps(conflict), now, existing["id"]),
                    )
                    return {
                        "status": "ignored_unverified_conflict",
                        "id": str(existing["id"]),
                        "platform_customer_id": existing_platform_id,
                        "conflict": conflict,
                    }
                conn.execute(
                    """
                    UPDATE customer_identity_links
                    SET verification_status='conflict', conflict_json=?, last_seen_at=?
                    WHERE id=?
                    """,
                    (dumps(conflict), now, existing["id"]),
                )
                return {"status": "conflict", "id": str(existing["id"]), "conflict": conflict}

            next_platform_id = existing_platform_id or incoming_platform_id
            existing_status = str(existing.get("verification_status") or "observed")
            next_status = "verified" if verified else existing_status
            existing_source = str(existing.get("platform_customer_id_source") or "")
            next_source = source if verified or existing_status != "verified" else existing_source
            conn.execute(
                """
                UPDATE customer_identity_links
                SET platform_customer_id=?, platform_user_id=?, customer_add_wechat_id=?,
                    platform_customer_id_source=?, verification_status=?, last_seen_at=?
                WHERE id=?
                """,
                (
                    next_platform_id,
                    identity.platform_user_id or str(existing.get("platform_user_id") or ""),
                    identity.customer_add_wechat_id or str(existing.get("customer_add_wechat_id") or ""),
                    next_source or existing_source,
                    next_status,
                    now,
                    existing["id"],
                ),
            )
        return {"status": "updated", "id": str(existing["id"]), **identity.as_dict()}
