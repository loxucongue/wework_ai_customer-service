# outreach-customer-log-followup

- status: active
- owner: Codex
- branch: `codex/outreach-customer-log-followup`
- base_sha: `6b963402f26f40f6e5f0aea569df41479eaf3d4b`
- production_baseline: `ai-paths-unified-20260908-135308-883b183b`

## 目标

- 客户列表直接展示客户、加微关系、外部联系人、接待人员、企微和企业等可用身份。
- 将真实计划与重复扫描/未建计划评估明确分离，避免误认为同一客户被重复创建计划。
- 正确展示文本和结构化任务内容，消除 `[object Object]`，并提供可读的任务详情。

## 非目标

- 不修改沉默唤醒候选、计划生成、任务调度、发送或安全门禁。
- 不回写历史数据，不新增事实表，不补造历史缺失身份。
- 不放宽 `corp_id + wechat + external_userid/customer_id` 销售接触边界。

## Change contract

- type: 生产缺陷修复与只读观测优化。
- scope: `outreach_repository.py`、`outreach_admin.py`、对应 schema/API 测试、`/logs/outreach` 页面与管理代理类型。
- risk: 历史任务结构兼容、重复评估聚合误吞真实计划、身份字段误关联、详情查询性能回退。
- validation: 指定生产客户只读取证、后端合同测试、全仓回归、前端类型/Lint/构建、桌面与手机浏览器验收、发布后只读核验。
- rollback: 回滚后端与前端到当前 `883b183b/e7db3e09` release；无数据库迁移。

## 待办

- 合入 main、发布并更新生产状态。

## 已核对事实

- 指定客户在 `2026-09-08 06:30～09:10 +08:00` 实际有 3 个计划、4 个任务和 4 次平台消息发送。
- 客户在 `06:57:18`、`08:21:59`、`08:45:52` 分别开口，因此形成 3 个不同的沉默周期；不是同一周期重复建计划。
- 页面原先混入的另外 6 条是 `outreach_cycle_completed_without_new_customer_reply` 扫描/评估记录，不是计划。
- 任务消息正文存放在 `reply_messages[].content.text/url`；旧页面直接字符串化对象，因而显示 `[object Object]`。

## 验证证据

- 后端客户日志与接口定向测试：6 个通过（在干净基线执行）。
- 前端 TypeScript、ESLint、Next.js Webpack 生产构建、服务端 bundle 通过。
- Playwright 桌面与 390px 手机视口通过；手机页面无横向溢出，浏览器控制台 0 error / 0 warning。
- 页面默认仅列 3 个真实计划，6 次未建计划评估折叠汇总；任务详情可展示文本、图片链接、任务 ID 与平台消息 ID。
