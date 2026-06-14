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

CREATE TABLE IF NOT EXISTS project_catalog (
    project_code TEXT PRIMARY KEY,
    project_name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    suitable_for TEXT NOT NULL DEFAULT '',
    contraindications TEXT NOT NULL DEFAULT '',
    effects TEXT NOT NULL DEFAULT '[]',
    duration TEXT NOT NULL DEFAULT '',
    original_price INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS project_pricing_rules (
    rule_id TEXT PRIMARY KEY,
    project_code TEXT NOT NULL,
    project_name TEXT NOT NULL,
    quote_type TEXT NOT NULL DEFAULT '',
    body_scope TEXT NOT NULL DEFAULT '',
    customer_segment TEXT NOT NULL DEFAULT '',
    prepay_amount INTEGER NOT NULL DEFAULT 0,
    tail_amount INTEGER NOT NULL DEFAULT 0,
    total_price INTEGER NOT NULL DEFAULT 0,
    display_price TEXT NOT NULL DEFAULT '',
    original_price INTEGER NOT NULL DEFAULT 0,
    min_quote INTEGER NOT NULL DEFAULT 0,
    conditions TEXT NOT NULL DEFAULT '',
    rule_note TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(project_code) REFERENCES project_catalog(project_code)
);

CREATE INDEX IF NOT EXISTS idx_project_pricing_project_code ON project_pricing_rules(project_code);
CREATE INDEX IF NOT EXISTS idx_project_pricing_enabled ON project_pricing_rules(enabled);

INSERT OR IGNORE INTO project_catalog
    (project_code, project_name, category, description, suitable_for, contraindications, effects, duration, original_price)
VALUES
    ('S10', 'S10 淡斑套餐', '单部位体验', '第三代操作斑点技术，围绕淡斑、美白嫩肤、收缩毛孔、痘印、细纹等方向承接', '关注老年斑、遗传斑、黑色素、斑点、肤色不均等基础改善方向的客户', '孕期、哺乳期、皮肤破损或明显不适期需谨慎', '["操作斑点","检测皮肤","基础清洁","肌肤补水"]', '到店整体约40-60分钟，具体以门店安排为准', 1980);

INSERT OR IGNORE INTO project_pricing_rules
    (rule_id, project_code, project_name, quote_type, body_scope, customer_segment, prepay_amount, tail_amount, total_price, display_price, original_price, min_quote, conditions, rule_note)
VALUES
    ('S10_ANNIVERSARY_NEW_268', 'S10', 'S10 淡斑套餐', '周年庆活动价', '单部位体验', '新客/线上报名客户', 10, 258, 268, '新客周年庆活动价268元，线上预约金10元，到店抵扣10元，做付258元；不做退还10元', 1980, 268, '仅限线上报名客户；限30名；套餐包括操作斑点、检测皮肤、基础清洁、肌肤补水；线下客人未预定到店按原价1980元', '当前只有周年庆活动，不得生成其他活动名'),
    ('S10_OLD_GT_1000_680', 'S10', 'S10 淡斑套餐', '老客报价', '单部位体验', '老客上一次订单超过1000元', 0, 680, 680, '老客上一次订单超过1000元，S10报价680元', 1980, 680, '需核对客户历史订单金额后确认', '没有订单金额事实时，不要直接判断客户老客档位'),
    ('S10_OLD_LE_1000_520', 'S10', 'S10 淡斑套餐', '老客报价', '单部位体验', '老客上一次订单不超过1000元', 0, 520, 520, '老客上一次订单不超过1000元，S10报价520元', 1980, 520, '需核对客户历史订单金额后确认', '没有订单金额事实时，不要直接判断客户老客档位');

