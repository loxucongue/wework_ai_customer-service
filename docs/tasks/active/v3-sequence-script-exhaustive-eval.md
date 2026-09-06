# V3 全序列话术适配与全场景评测

- Type: reply quality prompt change and exhaustive read-only evaluation
- Branch: `codex/v3-sequence-script-exhaustive-eval`
- Base SHA: `82f7645cbc079a7b0a137d8fc25becf1ee6abd25`
- Production baseline: 发布前现场核验；本任务不部署
- Goal: 逐条验证第三方全部跟进序列均能得到同卡点类型话术候选，并由 DeepSeek 判断至少一条候选语义适配；验证真实客户全场景 V3 回复效果、候选采用、真人表达与速度。
- Non-goals: 修改第三方目录；跨卡点类型拼接；提高采用率而虚报来源；发送客户消息；写生产数据库、BI、dispatch、outbox 或 Shadow 计划；启用延时自动发送。
- Owned files: `ai_paths/app/prompts/reply_synthesizer.py`、`ai_paths/app/services/v3_semantic_router_service.py`、相关 Prompt/检索验证、`docs/contracts/sales-strategy.md`；评测脚本和报告只在 ignored `artifacts/`。
- Change contract: 简单问题仍只回复一条；较长回答允许最多两条自然微信，第一条回答/解卡，第二条只交付不同价值或一个下一步，禁止同义重复和拆句刷屏。Top-6 总量不变，同一卡点类型内相同动作或标签最多保留 3 个不同版本；不新增模型调用、不硬编码具体序列或关键词。
- Risks: 平台序列缺少可映射的卡点类型；同类型话术存在旧口径或强推进内容；两条消息产生重复；DeepSeek 评审存在随机性；大样本只读评测消耗时间和配额。
- Validation: 全序列静态类型隔离和候选覆盖；DeepSeek 全序列语义适配评审；真实身份全场景隔离评测；全量确定性回归；模型必须全部为 DeepSeek，发送和生产写入必须为 0。
- Results: 第三方 92 条序列全部获得同类型 Top-6 话术候选并通过 DeepSeek 语义适配；120 条真实身份全链路 0 异常，AI 初评 99.2%、真人表达 97.5%、P50/P95 7166/13599ms，序列/话术候选 18/27、采用 14/14，安全失败和生产写入均为 0；唯一实质失败为既有门店地址交付问题。
- Rollback: 撤销本任务 Prompt 提交即可；无数据库迁移。
