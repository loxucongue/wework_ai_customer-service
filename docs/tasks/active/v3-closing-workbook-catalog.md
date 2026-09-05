# v3-closing-workbook-catalog

- status: active
- owner: Codex
- base_branch: main
- base_sha: b15be3bac3c8fab6026235d2a178db0cdc64b318
- production_verified_at: pending; deploy 前现场核验
- production_releases: pending; 不以历史聊天替代现场事实

## 目标

- 将业务提供的《逼单规则策略话术业务收集_真人成交版.xlsx》编译为版本化本地逼单目录。
- 保持 `external_then_local` 合同：第三方目录可用时自动优先外部，本地目录仅在接口未配置、异常或为空时使用。
- 使用 DeepSeek 做隔离效果测试，验证规则、策略、节点、话术候选和最终采用，不发送、不回写生产数据。

## 非目标

- 不修改业务源 Excel 的字段、内容和格式。
- 不新增第二套逼单决策引擎，不改变 V3 Reply 唯一销售语义决策权。
- 不启用延时自动发送，不把评测原文、模型输出或报告提交到 Git。

## Change contract

- type: 业务配置更新、目录合同校验、模型效果验证；达标后可发布。
- scope: 本地逼单规则、策略、节点和话术目录；相关加载/校验测试与稳定合同文档。
- risk: Excel 行之间关联不完整、临时配置产生过强推进、话术含未授权事实、外部切换时字段语义不一致。
- validation: Excel 结构审计、目录确定性校验、现有回归、DeepSeek 同样本隔离测试、安全阻断与零写入审计。
- rollback: 恢复上一版 `ai_closing_catalog_v1.json`；运行时可将 `AI_CLOSING_CATALOG_SOURCE=external` 或回滚到发布前 release。

## 涉及模块与文件所有权

- `ai_paths/app/policies/ai_closing_catalog_v1.json`
- 逼单目录加载与校验相关测试（按实际文件最小修改）
- `docs/contracts/sales-strategy.md`
- `docs/interfaces/external.md`
- `docs/tasks/active/v3-closing-workbook-catalog.md`
- `docs/tasks/active/INDEX.md`、完成时 `docs/tasks/history/INDEX.md`

## 不可破坏合同

- Reply 是唯一销售决策点；目录只提供规则、节奏和表达候选。
- 规则、策略、节点和话术必须同源，逼单话术不得跨 `followCheckpointTypeId` 放宽。
- 明确退订、投诉/高置信愤怒、健康风险、人工接管、交易终态和新卡点不得被临时目录绕过。
- 本地话术不得作为价格、门店、预约、支付、活动、名额或效果事实来源。
- 延时节点保持 shadow，禁止自动发送。

## 已确认事实与证据

- `origin/main=b15be3bac3c8fab6026235d2a178db0cdc64b318`，新 worktree 从该提交创建。
- 当前活动任务索引无其他文件占用。
- 现有运行合同已支持 `external|local|external_then_local`，无需新建切换机制。

## 已完成

- 创建独立分支与 worktree。
- 登记任务、分支、base SHA 和独占范围。

## 待办

- 审计 Excel 并映射到现有目录 schema。
- 更新本地目录、测试和合同。
- 执行确定性回归与 DeepSeek 隔离评测。
- 审查、合并；若达到发布门槛则现场核验后发布。

## 测试结果

- pending

## 发布与回滚

- pending

## 待沉淀的长期结论

- 业务正式导入第三方接口后，外部目录成为在线权威来源；本地临时目录保留为显式降级，不得与外部目录混用。
