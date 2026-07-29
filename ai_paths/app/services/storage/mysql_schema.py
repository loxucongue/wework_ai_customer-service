from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Column,
    Computed,
    Double,
    Index,
    Integer,
    MetaData,
    Table,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import LONGTEXT, VARCHAR


TABLE_PREFIX = "aics_"
VERSION_TABLE = "aics_schema_version"
metadata = MetaData()


def _id(name: str, *, primary_key: bool = False, nullable: bool = False) -> Column:
    kwargs = {
        "primary_key": primary_key,
        "nullable": nullable,
    }
    if not primary_key:
        kwargs["server_default"] = ""
    return Column(name, VARCHAR(191), **kwargs)


def _short(name: str, default: str = "", *, nullable: bool = False, length: int = 191) -> Column:
    return Column(name, VARCHAR(length), nullable=nullable, server_default=default)


def _json(name: str, default: str) -> Column:
    escaped = default.replace("'", "''")
    return Column(name, LONGTEXT, nullable=False, server_default=text(f"('{escaped}')"))


def _long(name: str, default: str = "") -> Column:
    escaped = default.replace("'", "''")
    return Column(name, LONGTEXT, nullable=False, server_default=text(f"('{escaped}')"))


def _time(name: str, default: str | None = None) -> Column:
    kwargs = {"nullable": False}
    if default is not None:
        kwargs["server_default"] = default
    return Column(name, VARCHAR(40), **kwargs)


conversations = Table(
    f"{TABLE_PREFIX}conversations",
    metadata,
    _id("id", primary_key=True),
    _id("customer_id"),
    _id("external_userid"),
    _id("corp_id"),
    _id("user_id"),
    _id("wechat"),
    _long("title"),
    _time("created_at"),
    _time("updated_at"),
    Index("idx_aics_conversations_updated_at", "updated_at"),
    Index("idx_aics_conversations_customer_id", "customer_id"),
    Index(
        "idx_aics_conversations_sales_contact",
        "corp_id",
        "wechat",
        "external_userid",
        "customer_id",
        "updated_at",
        mysql_length={
            "corp_id": 64,
            "wechat": 64,
            "external_userid": 64,
            "customer_id": 64,
        },
    ),
)

messages = Table(
    f"{TABLE_PREFIX}messages",
    metadata,
    _id("id", primary_key=True),
    _id("conversation_id"),
    _id("request_id"),
    _short("role", length=32),
    _long("content"),
    _long("file_image"),
    _json("reply_messages", "[]"),
    _time("created_at"),
    Index("idx_aics_messages_conversation_id", "conversation_id", "created_at"),
    Index("idx_aics_messages_request_id", "request_id"),
)

runs = Table(
    f"{TABLE_PREFIX}runs",
    metadata,
    _id("request_id", primary_key=True),
    _id("conversation_id"),
    _id("customer_id"),
    _json("input_snapshot", "{}"),
    _json("output_snapshot", "{}"),
    _json("intents", "[]"),
    _json("tags", "[]"),
    Column("duration_ms", BigInteger, nullable=False, server_default="0"),
    _json("token_usage", "{}"),
    _long("error"),
    _time("created_at"),
    Index("idx_aics_runs_conversation_id", "conversation_id", "created_at"),
    Index("idx_aics_runs_customer_id", "customer_id", "created_at"),
)

node_traces = Table(
    f"{TABLE_PREFIX}node_traces",
    metadata,
    _id("id", primary_key=True),
    _id("request_id"),
    _short("node_name"),
    _json("input_snapshot", "{}"),
    _json("output_snapshot", "{}"),
    _json("tool_calls", "[]"),
    Column("duration_ms", BigInteger, nullable=False, server_default="0"),
    _long("error"),
    _time("created_at"),
    Index("idx_aics_node_traces_request_id", "request_id"),
    Index("idx_aics_node_traces_node_name", "node_name"),
    Index("idx_aics_node_traces_created_at", "created_at"),
)

customer_memory = Table(
    f"{TABLE_PREFIX}customer_memory",
    metadata,
    _id("customer_id", primary_key=True),
    _json("portrait", "{}"),
    _json("basic_info", "{}"),
    _short("lifecycle_stage"),
    _time("updated_at"),
    _time("last_customer_message_at", ""),
    _time("last_staff_message_at", ""),
    _time("last_ai_reply_at", ""),
    _time("last_manual_takeover_at", ""),
    _time("last_outreach_at", ""),
    _short("outreach_status", "none", length=64),
    _id("outreach_plan_id"),
)

history_events = Table(
    f"{TABLE_PREFIX}history_events",
    metadata,
    _id("id", primary_key=True),
    _id("customer_id"),
    _short("event_type"),
    _short("stage"),
    _long("summary"),
    _json("facts", "{}"),
    _long("impact"),
    Column("confidence", Double, nullable=False, server_default="0"),
    _time("created_at"),
    Index("idx_aics_history_events_customer_id", "customer_id", "created_at"),
    Index("idx_aics_history_events_type", "event_type"),
)

outreach_sop_plans = Table(
    f"{TABLE_PREFIX}outreach_sop_plans",
    metadata,
    _id("id", primary_key=True),
    _short("name"),
    _long("description"),
    _short("status", "draft", length=64),
    _json("filters_json", "{}"),
    _time("created_at"),
    _time("updated_at"),
    _time("last_run_at", ""),
    _json("last_run_summary_json", "{}"),
    Index("idx_aics_outreach_sop_plans_status", "status", "updated_at"),
)

outreach_plans = Table(
    f"{TABLE_PREFIX}outreach_plans",
    metadata,
    _id("id", primary_key=True),
    _id("sop_plan_id"),
    _id("customer_id"),
    _id("corp_id"),
    _id("user_id"),
    _id("wechat"),
    _id("external_userid"),
    _short("status", "draft", length=64),
    _short("customer_stage"),
    _long("stall_reason"),
    _long("customer_psychology"),
    _long("plan_goal"),
    _json("source_snapshot", "{}"),
    _time("created_at"),
    _time("updated_at"),
    _time("paused_at", ""),
    _time("cancelled_at", ""),
    _time("completed_at", ""),
    Index("idx_aics_outreach_plans_customer_id", "customer_id", "created_at"),
    Index("idx_aics_outreach_plans_status", "status", "updated_at"),
    Index("idx_aics_outreach_plans_sop_plan_id", "sop_plan_id", "created_at"),
    Index(
        "idx_aics_outreach_plans_sales_contact",
        "corp_id",
        "wechat",
        "external_userid",
        "customer_id",
        "created_at",
        mysql_length={
            "corp_id": 64,
            "wechat": 64,
            "external_userid": 64,
            "customer_id": 64,
        },
    ),
)

outreach_tasks = Table(
    f"{TABLE_PREFIX}outreach_tasks",
    metadata,
    _id("id", primary_key=True),
    _id("plan_id"),
    _id("customer_id"),
    Column("step_index", Integer, nullable=False, server_default="1"),
    _time("scheduled_at", ""),
    _short("status", "pending", length=64),
    _short("intent"),
    _long("message_goal"),
    _json("content_sources", "[]"),
    _json("reply_messages_json", "[]"),
    Column("before_send_check", Integer, nullable=False, server_default="1"),
    _time("sent_at", ""),
    _short("send_status"),
    _id("system_msgid"),
    _long("error_message"),
    _time("created_at"),
    _time("updated_at"),
    Index("idx_aics_outreach_tasks_plan_id", "plan_id", "step_index"),
    Index("idx_aics_outreach_tasks_due", "status", "scheduled_at"),
    Index("idx_aics_outreach_tasks_customer_id", "customer_id", "scheduled_at"),
)

outreach_events = Table(
    f"{TABLE_PREFIX}outreach_events",
    metadata,
    _id("id", primary_key=True),
    _id("plan_id"),
    _id("task_id"),
    _id("customer_id"),
    _short("event_type"),
    _long("event_summary"),
    _json("payload_json", "{}"),
    _time("created_at"),
    Index("idx_aics_outreach_events_plan_id", "plan_id", "created_at"),
    Index("idx_aics_outreach_events_customer_id", "customer_id", "created_at"),
)

sop_events = Table(
    f"{TABLE_PREFIX}sop_events",
    metadata,
    _id("id"),
    _id("event_id", primary_key=True),
    _short("event_type"),
    _short("source"),
    Column("request_reply", Integer, nullable=False, server_default="0"),
    _time("upstream_created_at", ""),
    _json("raw_payload_json", "{}"),
    _short("status", "accepted", length=64),
    _long("error"),
    Column("retry_count", Integer, nullable=False, server_default="0"),
    _time("next_retry_at", ""),
    _long("last_retry_error"),
    _time("received_at"),
    _time("updated_at"),
    Index("idx_aics_sop_events_id", "id"),
    Index("idx_aics_sop_events_type", "event_type", "received_at"),
    Index("idx_aics_sop_events_status", "status", "updated_at"),
    Index("idx_aics_sop_events_retry", "status", "next_retry_at"),
)

sop_send_tasks = Table(
    f"{TABLE_PREFIX}sop_send_tasks",
    metadata,
    _id("id", primary_key=True),
    _id("event_id"),
    _id("idempotency_key"),
    _id("send_once_key"),
    _id("customer_id"),
    _id("external_userid"),
    _id("corp_id"),
    _id("user_id"),
    _id("wechat"),
    _id("sop_pack_id"),
    _short("sop_pack_name"),
    _short("sop_category"),
    _short("trigger_source"),
    _json("reply_messages_json", "[]"),
    _short("status", "pending", length=64),
    _json("send_payload_json", "{}"),
    _json("send_response_json", "{}"),
    _long("error"),
    _time("created_at"),
    _time("updated_at"),
    _time("sent_at", ""),
    Column(
        "active_send_once_key",
        VARCHAR(191),
        Computed(
            "CASE WHEN send_once_key <> '' AND status IN ('pending','sent') "
            "THEN send_once_key ELSE NULL END",
            persisted=True,
        ),
    ),
    UniqueConstraint("idempotency_key", name="uq_aics_sop_send_tasks_idempotency"),
    Index("uq_aics_sop_send_tasks_active_once", "active_send_once_key", unique=True),
    Index("idx_aics_sop_send_tasks_event_id", "event_id", "created_at"),
    Index("idx_aics_sop_send_tasks_customer", "customer_id", "created_at"),
    Index("idx_aics_sop_send_tasks_pack", "sop_pack_id", "status", "created_at"),
    Index("idx_aics_sop_send_tasks_category", "sop_category", "status", "created_at"),
    Index(
        "idx_aics_sop_send_tasks_sales_contact",
        "corp_id",
        "wechat",
        "external_userid",
        "customer_id",
        "status",
        "created_at",
        mysql_length={
            "corp_id": 64,
            "wechat": 64,
            "external_userid": 64,
            "customer_id": 64,
            "status": 32,
        },
    ),
)


EXPECTED_TABLES = tuple(sorted(table.name for table in metadata.tables.values()))
EXPECTED_ALL_TABLES = tuple(sorted((*EXPECTED_TABLES, VERSION_TABLE)))
EXPECTED_COLUMNS = {
    table.name: tuple(column.name for column in table.columns)
    for table in metadata.tables.values()
}
EXPECTED_INDEXES: dict[str, dict[str, tuple[str, ...]]] = {}
for _table in metadata.tables.values():
    _indexes: dict[str, tuple[str, ...]] = {}
    _primary = tuple(column.name for column in _table.primary_key.columns)
    if _primary:
        _indexes["PRIMARY"] = _primary
    for _constraint in _table.constraints:
        if isinstance(_constraint, UniqueConstraint) and _constraint.name:
            _indexes[_constraint.name] = tuple(column.name for column in _constraint.columns)
    for _index in _table.indexes:
        _indexes[_index.name] = tuple(column.name for column in _index.columns)
    EXPECTED_INDEXES[_table.name] = _indexes
