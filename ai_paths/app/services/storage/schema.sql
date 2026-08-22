PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    external_userid TEXT NOT NULL DEFAULT '',
    corp_id TEXT NOT NULL DEFAULT '',
    user_id TEXT NOT NULL DEFAULT '',
    wechat TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conversations_updated_at ON conversations(updated_at);
CREATE INDEX IF NOT EXISTS idx_conversations_customer_id ON conversations(customer_id);
CREATE INDEX IF NOT EXISTS idx_conversations_sales_contact
ON conversations(corp_id, wechat, external_userid, customer_id, updated_at);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    request_id TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    file_image TEXT NOT NULL DEFAULT '',
    reply_messages TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation_id ON messages(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_messages_request_id ON messages(request_id);

CREATE TABLE IF NOT EXISTS runs (
    request_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    input_snapshot TEXT NOT NULL DEFAULT '{}',
    output_snapshot TEXT NOT NULL DEFAULT '{}',
    intents TEXT NOT NULL DEFAULT '[]',
    tags TEXT NOT NULL DEFAULT '[]',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    token_usage TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_runs_conversation_id ON runs(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_runs_customer_id ON runs(customer_id, created_at);

CREATE TABLE IF NOT EXISTS node_traces (
    id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    node_name TEXT NOT NULL,
    input_snapshot TEXT NOT NULL DEFAULT '{}',
    output_snapshot TEXT NOT NULL DEFAULT '{}',
    tool_calls TEXT NOT NULL DEFAULT '[]',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY(request_id) REFERENCES runs(request_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_node_traces_request_id ON node_traces(request_id);
CREATE INDEX IF NOT EXISTS idx_node_traces_node_name ON node_traces(node_name);

CREATE TABLE IF NOT EXISTS customer_memory (
    customer_id TEXT PRIMARY KEY,
    portrait TEXT NOT NULL DEFAULT '{}',
    basic_info TEXT NOT NULL DEFAULT '{}',
    lifecycle_stage TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS history_events (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    event_type TEXT NOT NULL DEFAULT '',
    stage TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    facts TEXT NOT NULL DEFAULT '{}',
    impact TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY(customer_id) REFERENCES customer_memory(customer_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_history_events_customer_id ON history_events(customer_id, created_at);
CREATE INDEX IF NOT EXISTS idx_history_events_type ON history_events(event_type);

CREATE TABLE IF NOT EXISTS outreach_plans (
    id TEXT PRIMARY KEY,
    sop_plan_id TEXT NOT NULL DEFAULT '',
    customer_id TEXT NOT NULL,
    corp_id TEXT NOT NULL DEFAULT '',
    user_id TEXT NOT NULL DEFAULT '',
    wechat TEXT NOT NULL DEFAULT '',
    external_userid TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',
    customer_stage TEXT NOT NULL DEFAULT '',
    stall_reason TEXT NOT NULL DEFAULT '',
    customer_psychology TEXT NOT NULL DEFAULT '',
    plan_goal TEXT NOT NULL DEFAULT '',
    source_snapshot TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    paused_at TEXT NOT NULL DEFAULT '',
    cancelled_at TEXT NOT NULL DEFAULT '',
    completed_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_outreach_plans_customer_id ON outreach_plans(customer_id, created_at);
CREATE INDEX IF NOT EXISTS idx_outreach_plans_status ON outreach_plans(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_outreach_plans_sales_contact
ON outreach_plans(corp_id, wechat, external_userid, customer_id, created_at);

CREATE TABLE IF NOT EXISTS outreach_sop_plans (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',
    filters_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_run_at TEXT NOT NULL DEFAULT '',
    last_run_summary_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_outreach_sop_plans_status ON outreach_sop_plans(status, updated_at);

CREATE TABLE IF NOT EXISTS outreach_tasks (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    step_index INTEGER NOT NULL DEFAULT 1,
    scheduled_at TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    intent TEXT NOT NULL DEFAULT '',
    message_goal TEXT NOT NULL DEFAULT '',
    content_sources TEXT NOT NULL DEFAULT '[]',
    reply_messages_json TEXT NOT NULL DEFAULT '[]',
    before_send_check INTEGER NOT NULL DEFAULT 1,
    sent_at TEXT NOT NULL DEFAULT '',
    send_status TEXT NOT NULL DEFAULT '',
    system_msgid TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(plan_id) REFERENCES outreach_plans(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_outreach_tasks_plan_id ON outreach_tasks(plan_id, step_index);
CREATE INDEX IF NOT EXISTS idx_outreach_tasks_due ON outreach_tasks(status, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_outreach_tasks_customer_id ON outreach_tasks(customer_id, scheduled_at);

CREATE TABLE IF NOT EXISTS outreach_events (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL DEFAULT '',
    task_id TEXT NOT NULL DEFAULT '',
    customer_id TEXT NOT NULL,
    event_type TEXT NOT NULL DEFAULT '',
    event_summary TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_outreach_events_plan_id ON outreach_events(plan_id, created_at);
CREATE INDEX IF NOT EXISTS idx_outreach_events_customer_id ON outreach_events(customer_id, created_at);

CREATE TABLE IF NOT EXISTS first_day_outreach_runs (
    workflow_run_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL DEFAULT '',
    first_task_id TEXT NOT NULL DEFAULT '',
    second_task_id TEXT NOT NULL DEFAULT '',
    corp_id TEXT NOT NULL DEFAULT '',
    user_id TEXT NOT NULL DEFAULT '',
    wechat TEXT NOT NULL DEFAULT '',
    customer_id TEXT NOT NULL DEFAULT '',
    external_userid TEXT NOT NULL DEFAULT '',
    trigger_type TEXT NOT NULL DEFAULT '',
    conversation_fingerprint TEXT DEFAULT NULL,
    next_retry_at TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'running',
    reason_code TEXT NOT NULL DEFAULT '',
    final_decision TEXT NOT NULL DEFAULT '',
    first_scene TEXT NOT NULL DEFAULT '',
    second_scene TEXT NOT NULL DEFAULT '',
    model_attempt_count INTEGER NOT NULL DEFAULT 0,
    retry_count INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    input_snapshot_json TEXT NOT NULL DEFAULT '{}',
    workflow_json TEXT NOT NULL DEFAULT '{}',
    final_plan_json TEXT NOT NULL DEFAULT '{}',
    error_node TEXT NOT NULL DEFAULT '',
    error_type TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL DEFAULT '',
    raw_redacted_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_first_day_runs_started ON first_day_outreach_runs(started_at, workflow_run_id);
CREATE INDEX IF NOT EXISTS idx_first_day_runs_status ON first_day_outreach_runs(status, started_at);
CREATE INDEX IF NOT EXISTS idx_first_day_runs_plan ON first_day_outreach_runs(plan_id);
CREATE INDEX IF NOT EXISTS idx_first_day_runs_contact
ON first_day_outreach_runs(corp_id, wechat, external_userid, customer_id, started_at);

CREATE TABLE IF NOT EXISTS sop_events (
    id TEXT NOT NULL DEFAULT '',
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    request_reply INTEGER NOT NULL DEFAULT 0,
    upstream_created_at TEXT NOT NULL DEFAULT '',
    raw_payload_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'accepted',
    error TEXT NOT NULL DEFAULT '',
    retry_count INTEGER NOT NULL DEFAULT 0,
    next_retry_at TEXT NOT NULL DEFAULT '',
    last_retry_error TEXT NOT NULL DEFAULT '',
    received_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sop_events_type ON sop_events(event_type, received_at);
CREATE INDEX IF NOT EXISTS idx_sop_events_status ON sop_events(status, updated_at);

CREATE TABLE IF NOT EXISTS sop_send_tasks (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    send_once_key TEXT NOT NULL DEFAULT '',
    customer_id TEXT NOT NULL DEFAULT '',
    external_userid TEXT NOT NULL DEFAULT '',
    corp_id TEXT NOT NULL DEFAULT '',
    user_id TEXT NOT NULL DEFAULT '',
    wechat TEXT NOT NULL DEFAULT '',
    sop_pack_id TEXT NOT NULL DEFAULT '',
    sop_pack_name TEXT NOT NULL DEFAULT '',
    sop_category TEXT NOT NULL DEFAULT '',
    trigger_source TEXT NOT NULL DEFAULT '',
    reply_messages_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'pending',
    send_payload_json TEXT NOT NULL DEFAULT '{}',
    send_response_json TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    sent_at TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(event_id) REFERENCES sop_events(event_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sop_send_tasks_event_id ON sop_send_tasks(event_id, created_at);
CREATE INDEX IF NOT EXISTS idx_sop_send_tasks_customer ON sop_send_tasks(customer_id, created_at);
CREATE INDEX IF NOT EXISTS idx_sop_send_tasks_pack ON sop_send_tasks(sop_pack_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_sop_send_tasks_sales_contact
ON sop_send_tasks(corp_id, wechat, external_userid, customer_id, status, created_at);

CREATE TABLE IF NOT EXISTS message_dispatches (
    id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    source_channel TEXT NOT NULL DEFAULT '',
    source_kind TEXT NOT NULL DEFAULT '',
    source_request_id TEXT NOT NULL DEFAULT '',
    source_task_id TEXT NOT NULL DEFAULT '',
    conversation_id TEXT NOT NULL DEFAULT '',
    corp_id TEXT NOT NULL DEFAULT '',
    customer_id TEXT NOT NULL DEFAULT '',
    external_userid TEXT NOT NULL DEFAULT '',
    user_id TEXT NOT NULL DEFAULT '',
    wechat TEXT NOT NULL DEFAULT '',
    plan_id TEXT NOT NULL DEFAULT '',
    task_id TEXT NOT NULL DEFAULT '',
    reply_messages_json TEXT NOT NULL DEFAULT '[]',
    source_context_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'created',
    expected_count INTEGER NOT NULL DEFAULT 0,
    succeeded_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    platform_request_id TEXT NOT NULL DEFAULT '',
    system_msgid TEXT NOT NULL DEFAULT '',
    error_code TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    submitted_at TEXT NOT NULL DEFAULT '',
    accepted_at TEXT NOT NULL DEFAULT '',
    confirmed_at TEXT NOT NULL DEFAULT '',
    last_callback_at TEXT NOT NULL DEFAULT '',
    finalized_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_message_dispatches_status ON message_dispatches(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_message_dispatches_task ON message_dispatches(task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_message_dispatches_sales_contact
ON message_dispatches(corp_id, wechat, external_userid, customer_id, created_at);

CREATE TABLE IF NOT EXISTS message_dispatch_items (
    id TEXT PRIMARY KEY,
    dispatch_id TEXT NOT NULL,
    client_message_id TEXT NOT NULL UNIQUE,
    message_index INTEGER NOT NULL DEFAULT 0,
    message_type TEXT NOT NULL DEFAULT '',
    payload_hash TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'created',
    platform_message_id TEXT NOT NULL DEFAULT '',
    error_code TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    sent_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(dispatch_id) REFERENCES message_dispatches(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_message_dispatch_items_dispatch
ON message_dispatch_items(dispatch_id, message_index);
CREATE INDEX IF NOT EXISTS idx_message_dispatch_items_status
ON message_dispatch_items(status, updated_at);

CREATE TABLE IF NOT EXISTS message_delivery_events (
    event_id TEXT PRIMARY KEY,
    dispatch_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT '',
    raw_payload_json TEXT NOT NULL DEFAULT '{}',
    occurred_at TEXT NOT NULL DEFAULT '',
    received_at TEXT NOT NULL,
    FOREIGN KEY(dispatch_id) REFERENCES message_dispatches(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_message_delivery_events_dispatch
ON message_delivery_events(dispatch_id, received_at);
CREATE INDEX IF NOT EXISTS idx_message_delivery_events_status
ON message_delivery_events(status, received_at);
