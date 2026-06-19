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
    last_customer_message_at TEXT NOT NULL DEFAULT '',
    last_staff_message_at TEXT NOT NULL DEFAULT '',
    last_ai_reply_at TEXT NOT NULL DEFAULT '',
    last_manual_takeover_at TEXT NOT NULL DEFAULT '',
    last_outreach_at TEXT NOT NULL DEFAULT '',
    outreach_status TEXT NOT NULL DEFAULT 'none',
    outreach_plan_id TEXT NOT NULL DEFAULT '',
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

CREATE TABLE IF NOT EXISTS outreach_tasks (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    step_index INTEGER NOT NULL DEFAULT 0,
    scheduled_at TEXT NOT NULL,
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
