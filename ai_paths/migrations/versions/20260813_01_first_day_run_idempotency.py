"""Add first-day outreach run idempotency and retry scheduling.

Revision ID: 20260813_01
Revises: 20260807_01
Create Date: 2026-08-13
"""
from alembic import op
import json
import sqlalchemy as sa


revision = "20260813_01"
down_revision = "20260807_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "aics_first_day_outreach_runs",
        sa.Column("conversation_fingerprint", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "aics_first_day_outreach_runs",
        sa.Column("next_retry_at", sa.String(length=64), nullable=False, server_default=""),
    )
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT workflow_run_id, corp_id, wechat, external_userid, customer_id,
                   trigger_type, input_snapshot_json
            FROM aics_first_day_outreach_runs
            ORDER BY started_at DESC, workflow_run_id DESC
            """
        )
    ).mappings()
    claimed: set[tuple[str, str, str, str, str, str]] = set()
    for row in rows:
        raw_snapshot = row.get("input_snapshot_json")
        if isinstance(raw_snapshot, dict):
            snapshot = raw_snapshot
        else:
            try:
                snapshot = json.loads(str(raw_snapshot or "{}"))
            except (TypeError, ValueError):
                continue
        trigger = snapshot.get("trigger_context") if isinstance(snapshot, dict) else {}
        fingerprint = str(
            trigger.get("conversation_fingerprint") if isinstance(trigger, dict) else ""
        ).strip()
        key = (
            str(row.get("corp_id") or ""),
            str(row.get("wechat") or "").lower(),
            str(row.get("external_userid") or ""),
            str(row.get("customer_id") or ""),
            str(row.get("trigger_type") or ""),
            fingerprint,
        )
        if not fingerprint or key in claimed:
            continue
        bind.execute(
            sa.text(
                """
                UPDATE aics_first_day_outreach_runs
                SET conversation_fingerprint=:fingerprint
                WHERE workflow_run_id=:workflow_run_id
                """
            ),
            {
                "fingerprint": fingerprint,
                "workflow_run_id": row["workflow_run_id"],
            },
        )
        claimed.add(key)
    op.create_index(
        "idx_aics_first_day_runs_contact_fingerprint",
        "aics_first_day_outreach_runs",
        [
            "corp_id",
            "wechat",
            "external_userid",
            "customer_id",
            "trigger_type",
            "conversation_fingerprint",
        ],
        unique=True,
        mysql_length={
            "corp_id": 48,
            "wechat": 48,
            "external_userid": 48,
            "customer_id": 48,
        },
    )


def downgrade() -> None:
    raise RuntimeError("Destructive AICS schema downgrade is intentionally disabled")
