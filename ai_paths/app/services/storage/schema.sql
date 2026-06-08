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
    project_name_internal TEXT NOT NULL DEFAULT '',
    project_name_display TEXT NOT NULL DEFAULT '',
    project_group TEXT NOT NULL DEFAULT '',
    service_scope_default TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    suitable_for TEXT NOT NULL DEFAULT '[]',
    contraindications TEXT NOT NULL DEFAULT '[]',
    effects TEXT NOT NULL DEFAULT '[]',
    single_session_duration_min INTEGER NOT NULL DEFAULT 0,
    course_cycle_desc TEXT NOT NULL DEFAULT '',
    status INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pricing_rules (
    rule_id TEXT PRIMARY KEY,
    project_code TEXT NOT NULL,
    service_scope TEXT NOT NULL DEFAULT '',
    customer_type TEXT NOT NULL DEFAULT '',
    price_scene TEXT NOT NULL DEFAULT '',
    channel_type TEXT NOT NULL DEFAULT '',
    trigger_condition TEXT NOT NULL DEFAULT '',
    deposit_amount REAL NOT NULL DEFAULT 0,
    tail_amount REAL NOT NULL DEFAULT 0,
    total_amount REAL NOT NULL DEFAULT 0,
    list_price REAL NOT NULL DEFAULT 0,
    is_single_session_price INTEGER NOT NULL DEFAULT 1,
    is_package_price INTEGER NOT NULL DEFAULT 0,
    includes_desc TEXT NOT NULL DEFAULT '',
    excludes_desc TEXT NOT NULL DEFAULT '',
    price_label TEXT NOT NULL DEFAULT '',
    explain_short TEXT NOT NULL DEFAULT '',
    explain_long TEXT NOT NULL DEFAULT '',
    priority INTEGER NOT NULL DEFAULT 100,
    status INTEGER NOT NULL DEFAULT 1,
    valid_from TEXT NOT NULL DEFAULT '',
    valid_to TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(project_code) REFERENCES project_catalog(project_code)
);

CREATE INDEX IF NOT EXISTS idx_pricing_rules_project ON pricing_rules(project_code, status, priority);
CREATE INDEX IF NOT EXISTS idx_pricing_rules_scene ON pricing_rules(price_scene, customer_type, service_scope);

CREATE TABLE IF NOT EXISTS pricing_question_rules (
    question_type TEXT PRIMARY KEY,
    question_examples TEXT NOT NULL DEFAULT '[]',
    required_rule_fields TEXT NOT NULL DEFAULT '[]',
    answer_strategy TEXT NOT NULL DEFAULT '',
    must_explain_fields TEXT NOT NULL DEFAULT '[]',
    must_not_say_fields TEXT NOT NULL DEFAULT '[]',
    followup_strategy TEXT NOT NULL DEFAULT '',
    status INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS term_rewrite_rules (
    rule_id TEXT PRIMARY KEY,
    raw_term TEXT NOT NULL DEFAULT '',
    replacement_term TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    usage_note TEXT NOT NULL DEFAULT '',
    status INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_term_rewrite_rules_term ON term_rewrite_rules(raw_term, status);

CREATE TABLE IF NOT EXISTS kb_sync_records (
    sync_id TEXT PRIMARY KEY,
    kb_name TEXT NOT NULL,
    document_id TEXT NOT NULL DEFAULT '',
    source_type TEXT NOT NULL DEFAULT '',
    source_version TEXT NOT NULL DEFAULT '',
    document_url TEXT NOT NULL DEFAULT '',
    sync_status TEXT NOT NULL DEFAULT '',
    response_payload TEXT NOT NULL DEFAULT '{}',
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

DELETE FROM pricing_rules
WHERE rule_id NOT IN (
    'K10F_PRICE_RANGE',
    'S10_PRICE_RANGE',
    'K10_PRICE_RANGE',
    'S10N_PRICE_RANGE',
    'M10_PRICE_RANGE'
);

DELETE FROM project_catalog
WHERE project_code NOT IN ('K10F', 'S10', 'K10', 'S10N', 'M10');

INSERT OR REPLACE INTO project_catalog (
    project_code, project_name_internal, project_name_display, project_group, service_scope_default,
    category, description, suitable_for, contraindications, effects, single_session_duration_min, course_cycle_desc
) VALUES
('K10F', 'K10-全脸', 'K10全脸护理', 'K10', '全脸', '抗衰', 'K10全脸护理，全面改善。', '["全脸需要改善的客户"]', '["皮肤过敏期", "近期做过其他医美项目"]', '["全脸紧致", "整体提升", "焕发光彩"]', 0, '3个月'),
('S10', 'S10 抗衰套餐', 'S10抗衰套餐', 'S10', '套餐', '美白', 'S10抗衰套餐，深层修复肌肤。', '["有抗衰需求的客户"]', '["孕妇", "哺乳期女性"]', '["紧致肌肤", "淡化细纹", "提升弹性"]', 0, '3个月'),
('K10', 'K10-局部', 'K10局部护理', 'K10', '局部', '抗衰', 'K10局部护理，针对局部问题。', '["局部需要改善的客户"]', '["皮肤过敏期"]', '["局部紧致", "精准护理", "改善细纹"]', 0, '2个月'),
('S10N', 'S10N 补水护理', 'S10N补水护理', 'S10N', '护理', '美白', 'S10N补水护理，深层补水。', '["干燥缺水肌肤"]', '["皮肤破损者"]', '["深层补水", "锁水保湿", "提亮肤色"]', 0, '1个月'),
('M10', 'M10 塑形项目', 'M10塑形项目', 'M10', '塑形', '塑形', 'M10塑形项目，打造轮廓。', '["需要塑形的客户"]', '["严重疾病患者"]', '["轮廓塑形", "提升线条", "紧致肌肤"]', 0, '3个月');

INSERT OR REPLACE INTO pricing_rules (
    rule_id, project_code, service_scope, customer_type, price_scene, channel_type, trigger_condition,
    deposit_amount, tail_amount, total_amount, list_price, is_single_session_price, is_package_price,
    includes_desc, price_label, explain_short, explain_long, priority
) VALUES
('K10F_PRICE_RANGE', 'K10F', '全脸', '新客', '正式报价', '统一报价', '价格区间1400-2000，原价2000', 0, 0, 1400, 2000, 1, 0, 'K10全脸护理，具体配置到店确认', '参考价1400-2000', 'K10-全脸参考价1400-2000，原价2000。', 'K10-全脸属于全脸护理，参考价1400-2000，原价2000；具体执行价结合皮肤状态和门店配置确认。', 10),
('S10_PRICE_RANGE', 'S10', '套餐', '新客', '正式报价', '统一报价', '价格区间280-880，原价880', 0, 0, 280, 880, 1, 0, 'S10抗衰套餐，具体配置到店确认', '参考价280-880', 'S10抗衰套餐参考价280-880，原价880。', 'S10抗衰套餐用于深层修复肌肤，参考价280-880，原价880；具体执行价结合皮肤状态和门店配置确认。', 10),
('K10_PRICE_RANGE', 'K10', '局部', '新客', '正式报价', '统一报价', '价格区间350-680，原价680', 0, 0, 350, 680, 1, 0, 'K10局部护理，具体配置到店确认', '参考价350-680', 'K10-局部参考价350-680，原价680。', 'K10-局部用于局部护理，参考价350-680，原价680；具体执行价结合局部问题和门店配置确认。', 10),
('S10N_PRICE_RANGE', 'S10N', '护理', '新客', '正式报价', '统一报价', '价格区间180-380，原价380', 0, 0, 180, 380, 1, 0, 'S10N补水护理，具体配置到店确认', '参考价180-380', 'S10N补水护理参考价180-380，原价380。', 'S10N补水护理适合干燥缺水肌肤，参考价180-380，原价380；具体执行价结合皮肤状态和门店配置确认。', 10),
('M10_PRICE_RANGE', 'M10', '塑形', '新客', '正式报价', '统一报价', '价格区间400-1200，原价1200', 0, 0, 400, 1200, 1, 0, 'M10塑形项目，具体配置到店确认', '参考价400-1200', 'M10塑形项目参考价400-1200，原价1200。', 'M10塑形项目用于轮廓塑形和线条提升，参考价400-1200，原价1200；具体执行价结合部位和门店配置确认。', 10);

INSERT OR IGNORE INTO pricing_question_rules (
    question_type, question_examples, required_rule_fields, answer_strategy, must_explain_fields, must_not_say_fields, followup_strategy
) VALUES
('confirm_price', '["确定268吗", "这个价格准吗", "就是这个价吗"]', '["total_amount", "price_label", "trigger_condition"]', '先确认当前可引用价格，再说明适用条件和到店检测边界。', '["价格标签", "适用场景"]', '["最低价", "永久有效"]', '如客户未说明项目或活动来源，只追问一个来源。'),
('single_fee', '["是一次的费用吗", "一次多少钱", "是一只还是一双"]', '["is_single_session_price", "service_scope", "includes_desc"]', '先回答是否单次/部位口径，再解释局部、全脸或单部位范围。', '["服务范围", "是否单次"]', '["包全部", "永久有效"]', '必要时确认客户问的是局部还是全脸。'),
('price_conflict', '["为什么还有380", "为什么广告199你说380", "价格怎么不一样"]', '["price_scene", "service_scope", "trigger_condition"]', '先解释价格不同通常来自项目、部位、活动口径或预约方式不同，再把当前客户对应口径讲清楚。', '["差异原因", "当前口径"]', '["别人错了", "随便改价"]', '让客户发广告或说明项目，只问一个。'),
('deposit_question', '["定金10元", "10元是什么", "不交定金可以吗"]', '["deposit_amount", "tail_amount", "total_amount"]', '说明10元通常是预约/留位口径，和尾款一起构成总价；不直接承诺退款或已预约。', '["定金", "尾款", "总价"]', '["一定退", "不交也一样"]', '如涉及退款或订单，交给专业同事核对。'),
('hidden_fee_concern', '["到店会不会加钱", "有没有其他收费", "是不是一次费用"]', '["includes_desc", "excludes_desc", "total_amount"]', '先承接担心，再说明会按项目、部位、次数、尾款逐项核对，不把局部价说成全脸价。', '["包含项", "尾款", "范围"]', '["绝不加钱", "没有任何其他费用"]', '需要客户提供广告截图或项目名核对。'),
('times_question', '["要做多少次", "一次能好吗", "做几次有效果"]', '["course_cycle_desc"]', '说明次数和基础情况、范围、深浅有关，可先按项目经验给方向，但不承诺一次完成。', '["疗程节奏", "个体差异"]', '["一次根治", "一定见效"]', '结合照片或到店检测确认。');
