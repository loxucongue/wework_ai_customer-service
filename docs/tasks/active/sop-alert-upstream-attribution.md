# sop-alert-upstream-attribution

- status: active
- owner: Codex
- base_branch: main
- base_sha: `8fddcc5e5ebb82409bec2b2e54062931d8474392`
- production_verified_at: 2026-09-09 14:30 Asia/Shanghai
- production_release: `ai-paths-unified-20260909-sop-deterministic-af3c515d@af3c515dc2491347cf170d9abdcba9c875882288`

## 目标

- 上游企微聚合平台明确拒绝消息发送时，告警责任归为“企微聚合平台消息发送”。
- 告警保留上游安全的错误码并给出可读原因，已知 `account_unassigned` 明确说明账号映射缺失。
- 接待企微从第三方任务已有 `user_wechat_id/user_wechat/wecom_account` 字段正确展示。

## 非目标

- 不改变第三方任务发送、消费、恢复或顺序阻断逻辑。
- 不重发或消费现有任务，不修改聚合平台账号映射。
- 不把我方网络超时、本地配置或运行异常错误归给聚合平台。

## Change contract

- type: observability correctness
- scope: `sop_platform_task_service.py`、`sop_failure_alert_service.py`、对应测试与第三方 SOP 合同
- risk: 上游响应与本地故障边界识别错误；告警泄露上游响应中的非必要字段
- validation: 覆盖明确上游拒绝、HTTP 错误、连接超时、本地异常、企微字段回填及告警文本
- rollback: `ai-paths-unified-20260909-sop-deterministic-af3c515d`

## 已确认事实

- 任务 83049 三项业务门禁通过，第三方任务提供接待企微 `SL0069` 和 `msgId=38790`。
- 聚合平台权威状态返回 `send_allowed=false`、`reason_code=account_unassigned`，说明账号没有客服用户或超级客服 AI 映射。
- 该任务未发送、未消费任务与内容，后续任务未越过；现有告警错误显示“我方主动发送链路/接待企微未提供”。

## 待办

- 合并、发布和现场验证。

## 已完成

- 上游 HTTP/业务错误提取安全错误码，并优先使用本轮权威发送资格码补全原因。
- 聚合平台明确拒绝改归“企微聚合平台消息发送”，已知错误码转换为可执行中文原因。
- 接待企微兼容第三方任务的 `user_wechat_id/user_wechat/wecom_account` 字段。

## 测试结果

- SOP 告警、失败终态与确定性执行专项：54 passed。
- 全量：632 passed，1 条第三方依赖弃用警告。
- Ruff、compileall、diff check：通过。
