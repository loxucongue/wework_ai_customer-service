from __future__ import annotations

import hashlib
import json

from app.reception_state import ReceptionConflict, ReceptionNotification
from app.services.storage.serialization import loads_dict, utc_now_iso


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


class ReceptionStateService:
    def __init__(self, store, bindings: list[dict[str, str]]):
        self.store = store
        self.bindings = bindings

    def _wechat(self, event: ReceptionNotification) -> str:
        matches = [item for item in self.bindings
                   if item.get("corp_id") == event.wecom_corp_id
                   and item.get("employee_wechat_id") == event.employee_wechat_id]
        if len(matches) != 1:
            raise ReceptionConflict("identity_mapping_conflict")
        wechat = matches[0].get("wechat", "")
        if not wechat or wechat != wechat.strip():
            raise ReceptionConflict("identity_mapping_conflict")
        reverse = [item for item in self.bindings
                   if item.get("corp_id") == event.wecom_corp_id
                   and item.get("wechat", "").casefold() == wechat.casefold()]
        if len(reverse) != 1:
            raise ReceptionConflict("identity_mapping_conflict")
        return wechat

    def apply(self, event: ReceptionNotification) -> dict:
        wechat = self._wechat(event)
        contact_key = digest([event.wecom_corp_id, wechat.casefold(), event.customer_external_user_id])
        event_key = digest(event.event_id)  # Global, case-sensitive event identity.
        request_hash = digest(event.model_dump())
        # occurred_at/event_id audit the notification, not the versioned business snapshot.
        snapshot = event.model_dump(exclude={"event_id", "occurred_at", "state_version"})
        snapshot_hash = digest(snapshot)
        now = utc_now_iso()
        mysql = self.store.dialect == "mysql"
        lock = " FOR UPDATE" if mysql else ""
        with self.store.connect() as conn:
            if not mysql:
                conn.execute("BEGIN IMMEDIATE")
            # Event row serializes duplicate delivery, including across different contacts.
            conn.execute(
                "INSERT INTO reception_events (event_key,contact_key,request_hash,payload_json,created_at) "
                "VALUES (?,?,?,?,?) ON CONFLICT(event_key) DO NOTHING",
                (event_key, contact_key, request_hash, event.model_dump_json(), now),
            )
            saved_event = dict(conn.execute(
                "SELECT * FROM reception_events WHERE event_key=?" + lock, (event_key,),
            ).fetchone())
            if saved_event["request_hash"] != request_hash:
                raise ReceptionConflict("event_id_conflict")
            conn.execute(
                "INSERT INTO reception_states (contact_key,version,snapshot_hash,snapshot_json,"
                "invalidated_through_version,updated_at) VALUES (?,0,'','{}',0,?) "
                "ON CONFLICT(contact_key) DO NOTHING", (contact_key, now),
            )
            state = dict(conn.execute(
                "SELECT * FROM reception_states WHERE contact_key=?" + lock, (contact_key,),
            ).fetchone())
            version = int(state["version"])
            current = loads_dict(state["snapshot_json"])
            result = "applied"
            if saved_event["processed"]:
                result = "duplicate"
            elif event.state_version < version:
                result = "stale_ignored"
            elif event.state_version == version:
                if state["snapshot_hash"] != snapshot_hash:
                    raise ReceptionConflict("state_version_conflict")
                result = "duplicate"
            else:
                # Only an existing authoritative customer/relationship mapping can bind state.
                links = conn.execute(
                    "SELECT * FROM customer_identity_links WHERE corp_id=? "
                    "AND LOWER(wechat)=LOWER(?) AND external_userid=?" + lock,
                    (event.wecom_corp_id, wechat, event.customer_external_user_id),
                ).fetchall()
                if len(links) != 1:
                    raise ReceptionConflict("identity_mapping_conflict")
                link = dict(links[0])
                if (link["verification_status"] != "verified"
                        or link["corp_id"] != event.wecom_corp_id
                        or str(link["wechat"]).casefold() != wechat.casefold()
                        or link["external_userid"] != event.customer_external_user_id
                        or str(link["platform_customer_id"]) != str(event.customer_id)
                        or str(link["customer_add_wechat_id"]) != str(event.customer_add_wechat_id)):
                    raise ReceptionConflict("relationship_binding_conflict")
                relation = str(event.customer_add_wechat_id)
                old_relation = str(current.get("customer_add_wechat_id", ""))
                if old_relation == relation and current.get("data", {}).get("is_deleted") and not event.data.is_deleted:
                    raise ReceptionConflict("deleted_relationship_cannot_revive")
                retired = conn.execute(
                    "SELECT relation_id FROM reception_relations WHERE contact_key=? AND relation_id=?",
                    (contact_key, relation),
                ).fetchone()
                if retired and old_relation != relation:
                    raise ReceptionConflict("retired_relationship_conflict")
                conn.execute(
                    "INSERT INTO reception_relations (contact_key,relation_id) VALUES (?,?) "
                    "ON CONFLICT(contact_key,relation_id) DO NOTHING", (contact_key, relation),
                )
                # Durable invalidation fence, committed atomically; future consumers use it.
                conn.execute(
                    "UPDATE reception_states SET version=?,snapshot_hash=?,snapshot_json=?,"
                    "invalidated_through_version=?,updated_at=? WHERE contact_key=?",
                    (event.state_version, snapshot_hash, json.dumps(snapshot, ensure_ascii=False),
                     max(version, int(state["invalidated_through_version"])), now, contact_key),
                )
                current, version = snapshot, event.state_version
            conn.execute(
                "UPDATE reception_events SET processed=1 WHERE event_key=?", (event_key,),
            )
        # Return only after the store context commits successfully.
        return {"event_id": event.event_id, "result": result, "current_state_version": version,
                "requested_ai_version": current.get("data", {}).get("ai_version"),
                "effective_ai_version": "v3", "version_switch_enabled": False}
