# outreach-customer-log

- status: ready_for_review
- owner: Codex
- base_branch: main
- base_sha: `73b723354e49c020e09e1b036f9c40bc66bf3e73`
- production_baseline: pending verification before release

## 目标

将千人千面沉默唤醒日志改为按销售接触档案聚合的只读管理页：先查客户，再查其自动沉默唤醒计划和任务执行证据。

## 非目标

- 不改变计划生成、模型选择、发送、SOP 或 worker 执行逻辑。
- 不回写历史数据，不新增业务事实表。
- 不把不同 `corp_id + wechat + external_userid/customer_id` 的客户合并。

## Change contract

- type: 只读观测与前端重构。
- scope: 自动沉默计划读模型、管理 API、Next 代理、`/logs/outreach` 页面与旧链接跳转。
- risk: 读模型口径错误、跨企微身份混合、前端把任务状态误表示为实际发送。
- validation: 后端确定性查询/API 测试、前端类型/Lint/生产构建、浏览器验收、发布后只读请求和服务健康核验。
- rollback: 回滚到发布前 clean main release；本次不含迁移和数据写入。

## 事实口径

- 自动来源：首日加微沉默、`followup_strategy`、`closing_sequence`、显式 `auto_approved`。
- 手工计划不纳入；首日未建计划、阻断与失败运行需按客户显示且与同一运行事件去重。
- 任务已处理包含已发送、消费/无需发送与失败；三者单独统计，发送只以主动发送成功事实计算。

## 待办

- 审核分支并确认是否合并、部署。
- 发布后核验新页面只读请求、旧链接跳转、V3 服务和 worker 健康。

## 已完成

- 实现自动沉默触达的客户聚合、客户时间线和计划任务详情只读接口。
- 自动来源限定为首日沉默、自动跟进、自动成交和 `auto_approved`；手工计划排除。
- 实现 30 天默认、90 天上限、客户身份隔离、无计划事件去重和实际发送凭据校验。
- 页面迁移到 `/logs/outreach`，旧 `/logs/outreach-first-day` 永久跳转；导航和入口已更新。

## 验证证据

- `python -m pytest tests/test_outreach_customer_log.py tests/test_outreach_customer_log_api.py tests/test_outreach_dashboard_metrics.py`：7 passed。
- 前端 TypeScript、ESLint 和生产构建通过。
- 浏览器已验证桌面/移动布局、筛选、客户展开、计划详情、规则展开保存和旧地址跳转；验收使用本地脱敏模拟数据，不调用生产写接口。
