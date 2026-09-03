# integrate-pending-v3

## 目标

- 基于最新干净 `origin/main`，整合尚未进入 main 的统一销售决策/BI 埋点能力。
- 复核并择优整合暂停 WIP 中仍有效的门店任意输入加固，避免覆盖 main 已合入的门店修复。
- 保持销售策略默认关闭、延时任务 shadow；代码合入不等于功能启用或生产发布。

## 非目标

- 不启用未通过 400 条人工金标门槛的销售策略。
- 不发送客户消息，不调用生产写接口，不执行生产迁移或部署。
- 不重复合并已进入 main 的 BI、DeepSeek 评测和门店匹配提交。

## Base 与来源

- base SHA: `3252f021597ab09da8dc27cd2d8da0ca913cb903`
- branch: `codex/integrate-pending-v3`
- 销售决策来源：`codex/v3-sales-decision-observability@a858624d`
- 门店 WIP 来源：`codex/store-any-input-hardening@a27e6dbb`

## 独占范围

- `ai_paths/app/graph/`、`ai_paths/app/prompts/` 中本任务涉及的 Reply/门店合同。
- V3 策略、埋点、归因、管理接口、迁移和相关存储实现。
- `tests/test_v3_*`、`tests/test_store_workflow_boundaries.py`。
- 本任务相关合同、接口、任务和历史文档。

## Change contract

- 类型：跨分支集成、默认关闭的新策略能力、兼容性迁移、确定性回归。
- 风险：旧分支覆盖 main 新修复、策略误启用、MySQL schema 不兼容、门店事实被销售文案覆盖。
- 验证：逐文件冲突审查、核心 pytest、Ruff/compile、迁移检查、前端生产构建。
- 回滚：回滚本集成提交；运行期继续以 `AI_SALES_POLICY_ENABLED=false` 为安全边界。

## 待办

- [x] 建立干净集成分支并登记 ownership。
- [ ] 整合统一销售决策与 BI 埋点。
- [ ] 复核并整合门店任意输入 WIP。
- [ ] 完成确定性、迁移和前端验证。
- [ ] 更新长期合同、历史摘要并合入 main。

