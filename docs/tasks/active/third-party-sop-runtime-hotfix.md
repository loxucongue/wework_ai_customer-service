# 第三方 SOP 运行时热修

- 目标：修复第三方 SOP 客户身份参数透传异常，以及合法 `msg_type/content_text/media_url` 消息被误判为无效内容的问题。
- 非目标：不补发、不重开、不修改已经进入终态的历史任务；不改变第三方消费、发送或策略回传协议。
- Base SHA：`73b723354e49c020e09e1b036f9c40bc66bf3e73`
- 生产基线：`42b27eaede76b329d86880668e6a20ad9aa0dd85`，2026-09-08 现场核验 `dirty=false`。
- 涉及模块：`ai_paths/app/services/sop_platform_task_service.py`、第三方 SOP 确定性测试。
- 不可破坏合同：客户边界仍为 `corp_id + wechat + external_userid/customer_id`；终态必须完成 consume 和 service-rule-data；默认测试不发送真实客户消息。
- 风险：身份字段收窄错误会影响会话拉取或发送；内容兼容过宽会放行空内容或非法媒体 URL。
- 验证：实际平台 snake_case 消息预检通过；非法类型、空文本、非法 URL 继续拦截；SOP 合同和身份测试通过；发布后三个服务同一提交且健康。
- 发布：统一发布 control、V3、worker；不手工恢复历史终态任务。
- 回滚：生产提交 `42b27eaede76b329d86880668e6a20ad9aa0dd85`。
