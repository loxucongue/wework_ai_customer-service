# v3-direct-booking-closing

- status: active
- owner: Codex
- base_branch: main
- base_sha: `fafe3dea04722185a38c5321dfe07ae177ea8d7d`
- production_baseline: `b4dfc184ea635afeb8f62dd881df30b1946dd016`

## 目标

- 修复请求 `22ae8140-f3dd-4c4c-b6d8-93bc2469a5aa` 中已配置逼单规则未召回、Reply 无合法策略节点且未生成预约金卡的问题。
- 保持普通跟进序列/卡点话术与逼单规则/策略/话术两条链路语义分离。
- 不增加模型调用，不用代码替代 Reply 的最终销售决策。

## 非目标

- 不改第三方接口、数据库 schema、公共 V3 接口和发送协议。
- 不把“怎么预约”等短语写成代码内置业务关键词；只依据当前租户已配置目录做透明候选召回。
- 不修改并行 `appointment-availability-boundary` 任务占用的 Reply、全局 Prompt、业务规则和预约校验文件。

## Change contract

- type: urgent reply quality / closing retrieval bugfix
- scope: 逼单目录候选召回、真实 ID 与证据传递、指定日志隔离重放、成交与安全边界回归
- risk: 候选召回过宽可能让 Reply 看到不适用策略；必须限制为已配置规则，保留 Reply 最终决策，并由现有约束阻断误推进
- validation: 确定性目录召回、指定日志 DeepSeek 隔离重放、成交/卡点/退订矩阵、全量回归、生产发布检查
- rollback: 恢复生产 release `b4dfc184`

## 文件所有权与并行边界

- 本任务独占：`ai_paths/app/services/v3_semantic_router_service.py`、`tests/test_v3_closing_catalog_integration.py`、本任务文档。
- 并行任务占用 `reply_nodes.py`、`reply_validation.py`、`business_rules.json`、`global_contract.py`、`reply_synthesizer.py` 及其测试；本任务不修改这些文件。

## 不可破坏合同

- V3 Reply 仍是唯一销售语义决策者；Router 和代码只提供候选与真实目录证据。
- 明确退订、投诉/愤怒、健康风险、人工接管和交易终态不得因候选召回而推进。
- 付款卡仍必须满足已有活动铺垫、销售承接、当前行动信号和合法结构校验。

## 已确认事实

- 指定请求当前消息是“怎么预约”，普通卡点为 none，因此普通跟进序列和卡点话术为 0 是正确的。
- 当前本地逼单目录正常加载，包含精确适用规则 `local:rule:explicit_registration_or_payment_query` 和策略 `local:sequence:direct_deposit_entry`。
- Router 未返回逼单候选；Reply 原始输出 `advance + sequence_key=none`，被校验降级为 pause，同时 `reply_action=none`，造成决策、回复与动作不一致。

## 完成情况

- [x] 对当前租户已配置的独立规则做规范化整句精确匹配，只恢复真实规则和关联策略候选，不替 Reply 选择节点或付款动作。
- [x] 模型明确返回 `blocked` 时不覆盖；包含业务短语的更长否定句不会误命中。
- [x] 无客户证据的历史卡点直接丢弃，避免污染当前轮并导致 Router 无意义降级。
- [x] 补充确定性和安全边界测试。
- [ ] 合并预约可协调边界提交后执行全量回归与 DeepSeek 指定日志隔离重放。
- [ ] 合入 main、发布并完成生产只读核验。

## 测试证据

- `ruff check ai_paths/app/services/v3_semantic_router_service.py tests/test_v3_closing_catalog_integration.py`：通过。
- `pytest -q tests/test_v3_closing_catalog_integration.py`：32 passed。
- `pytest -q tests/test_v3_closing_catalog_integration.py tests/test_v3_policy_decision_contract.py`：64 passed。
