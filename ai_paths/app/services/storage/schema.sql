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
    ('S10', 'S10', '单部位体验', '针对色素、肤色不均等基础改善方向', '关注斑点、色沉、肤色不均的客户', '孕期、哺乳期、皮肤破损或明显不适期需谨慎', '["淡化色素","改善肤色不均","提亮肤色"]', '按到店检测和皮肤状态确认', 1980),
    ('S10N', 'S10N', '单部位体验', '补水与肤质管理方向', '关注干燥、缺水、肤色暗沉的客户', '皮肤破损或明显不适期需谨慎', '["补水管理","提亮肤色","改善干燥"]', '按到店检测和皮肤状态确认', 1980),
    ('K10', 'K10', '单部位体验及全脸', '局部或全脸综合改善方向', '关注毛孔、痘印、紧致、全脸改善的客户', '皮肤过敏期、近期强刺激操作后需谨慎', '["局部改善","全脸管理","肤质提升"]', '按到店检测和皮肤状态确认', 1980),
    ('M10', 'M10', '单部位体验', '轮廓和紧致管理方向', '关注轮廓、线条、紧致的客户', '严重基础疾病或不适期需谨慎', '["轮廓管理","紧致提升","线条改善"]', '按到店检测和皮肤状态确认', 1980),
    ('OTHER', '其他品相', '单部位体验', '其他品相按公司活动通知和到店检测确认', '需要其他改善方向的客户', '按到店检测和专业评估确认', '["按需匹配"]', '按到店检测和皮肤状态确认', 0);

INSERT OR IGNORE INTO project_pricing_rules
    (rule_id, project_code, project_name, quote_type, body_scope, customer_segment, prepay_amount, tail_amount, total_price, display_price, original_price, min_quote, conditions, rule_note)
VALUES
    ('S10_FIRST_268', 'S10', 'S10', '首次报价', '单部位体验', '新客', 0, 268, 268, '268', 1980, 268, '首次咨询常规活动报价', '到店检测后按实际情况确认配置'),
    ('S10_BIG_238', 'S10', 'S10', '大型活动', '单部位体验', '新客', 0, 238, 238, '238', 1980, 238, '公司统一通知的大型活动', '大型活动价格以公司通知为准'),
    ('S10_OLD_LOCAL_680', 'S10', 'S10', '老客报价', '局部', '老客首次消费<=1000', 0, 680, 680, '680', 1980, 680, '老客局部报价', ''),
    ('S10_OLD_LOCAL_520', 'S10', 'S10', '老客报价', '局部', '老客首次消费>1000', 0, 520, 520, '520', 1980, 520, '老客局部报价', ''),
    ('S10N_FIRST_199', 'S10N', 'S10N', '首次报价', '单部位体验', '新客', 10, 189, 199, '定金10，到店尾款189，总价199', 1980, 199, '首次报价且加微当天', '当天加微首次报价最低报价199'),
    ('S10N_BIG_179', 'S10N', 'S10N', '大型活动报价', '单部位体验', '新客', 10, 169, 179, '定金10，到店尾款169，总价179', 1980, 179, '大型活动价格以公司通知为准', ''),
    ('S10N_OLD_LOCAL_220', 'S10N', 'S10N', '老客报价', '局部', '老客首次消费<=800', 0, 220, 220, '220', 1980, 220, '老客局部报价', ''),
    ('S10N_OLD_LOCAL_320', 'S10N', 'S10N', '老客报价', '局部', '老客首次消费>800', 0, 320, 320, '320', 1980, 320, '老客局部报价', ''),
    ('K10_FIRST_LOCAL_380', 'K10', 'K10', '首次报价', '局部', '新客', 10, 370, 380, '定金10，到店尾款370，总价380', 1980, 380, '首次报价且加微当天', '当天加微首次报价最低380；车费报销30后，合计报价金额不得低于350'),
    ('K10_FIRST_FULL_1580', 'K10', 'K10', '首次报价', '全脸', '新客', 10, 1570, 1580, '定金10，到店尾款1570，总价1580', 1980, 1580, '首次报价且加微当天', '当天加微首次报价最低1580'),
    ('K10_BIG_LOCAL_301', 'K10', 'K10', '大型活动', '局部六选一', '新客', 0, 301, 301, '301', 1980, 301, '大型活动局部六选一', ''),
    ('K10_BIG_FULL_1280', 'K10', 'K10', '大型活动', '全脸', '新客', 10, 1270, 1280, '定金10，到店尾款1270，总价1280', 1980, 1280, '大型活动全脸', ''),
    ('K10_OLD_LOCAL_420', 'K10', 'K10', '老客报价', '局部', '老客', 0, 420, 420, '420', 1980, 420, '老客局部报价', ''),
    ('K10_OLD_FULL_980', 'K10', 'K10', '老客报价', '全脸', '老客', 0, 980, 980, '980', 1980, 980, '老客全脸报价', ''),
    ('M10_FIRST_458', 'M10', 'M10', '首次报价', '单部位体验', '新客', 0, 458, 458, '458', 1980, 458, '首次报价且加微当天', ''),
    ('M10_BIG_420', 'M10', 'M10', '大型活动价', '单部位体验', '新客', 0, 420, 420, '420', 1980, 420, '大型活动价格以公司通知为准', ''),
    ('M10_OLD_788', 'M10', 'M10', '老客报价', '单部位体验', '老客首次消费<=800', 0, 788, 788, '788', 1980, 788, '老客报价', ''),
    ('M10_OLD_980', 'M10', 'M10', '老客报价', '单部位体验', '老客首次消费>800', 0, 980, 980, '980', 1980, 980, '老客报价', ''),
    ('OTHER_FIRST_NOTICE', 'OTHER', '其他品相', '首次报价', '单部位体验', '新客', 0, 0, 0, '按公司统一通知', 0, 0, '按公司统一通知', ''),
    ('OTHER_BIG_NOTICE', 'OTHER', '其他品相', '大型活动价', '单部位体验', '新客', 0, 0, 0, '按公司统一通知', 0, 0, '按公司统一通知', ''),
    ('OTHER_OLD_DISCOUNT', 'OTHER', '其他品相', '老客报价', '单部位体验', '老客', 0, 0, 0, '新客首次体验价的20%-40%', 0, 0, '老客按新客首次体验价比例报价', '');

