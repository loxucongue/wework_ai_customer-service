# 生产状态

- status: verified-snapshot
- owner: operations
- verified_at: `2026-09-06T23:22:08+08:00`
- source_of_truth: 服务器现场核验；本页只在上述时刻有效

## 当前后端 release

- release: `ai-paths-unified-20260906-231341-8f911bea`
- git commit: `8f911bea69268180349fcc992bca7e86f7bee974`
- branch contract: `main`
- dirty: `false`
- config revision: `74eee9d04bedb99c0dc25ef2aaefab2cd96d2d8ef40fdf500a8ff838fd4ae4c0`
- database backend: MySQL

| 角色 | Unit | 现场状态 | 现场健康信息 |
| --- | --- | --- | --- |
| control | `ai-paths.service` | active/running | `/health` 返回 `service_role=control`，后台 worker 关闭 |
| reply | `ai-paths-v3.service` | active/running | `/health` 返回 `service_role=reply`，与当前 release/commit 一致 |
| worker | `ai-paths-workers.service` | active/running | `/health` 返回 `service_role=worker`，后台 worker 已启用 |
| 管理前端 | `ai-paths-frontend.service` | active/running | `frontend-20260906-231341-8f911bea`，本机监听 5000，`/logs` 返回 HTTP 200 |

三个后端角色均由同一 clean main SHA 构建。V3 Reply 进程的有效覆盖配置为 `MODEL_REPLY=deepseek-chat`、Reply fallback 为空；共享基础环境仍保留其他角色的全局模型默认值，不能据此推断 V3 Reply 使用 GPT，实际模型仍应以每次 run trace 为准。

## 已核验开关

- `AI_SALES_POLICY_ENABLED=true`
- `FOLLOW_KNOWLEDGE_ENABLED=true`
- `AI_CLOSING_CATALOG_SOURCE=external_then_local`
- `OUTREACH_FIRST_DAY_SILENCE_ENABLED=true`
- `OUTREACH_FIRST_DAY_SILENCE_MINUTES=1`
- 企微 allowlist 为空，表示全部企微号进入候选。
- 启用水位：`2026-09-05T09:41:20+00:00`；水位前历史沉默不补发。
- 当前代码仍要求沉默计划前和每次发送前由平台明确确认 AI 模式；人工、未知或状态查询失败均阻断。

## Worker 与 outbox

- 第三方 SOP worker 正常运行，现场 `queue_depth=0`、`pending_total=0`、`in_flight_count=0`。
- Reply 健康信息显示策略数据 outbox：`sent=1994`、`pending=14`、`dead=16`。
- 策略数据外发当前 `delivery_enabled=false`；恢复前必须对 dead/pending 做专项审计，不能直接批量重放。
- 平台订单异步归因开关未在本次安全配置核验中发现显式启用值；在下一次归因或发布任务中重新确认，不把未知写成已启用。

## 回滚状态

- 后端 `/opt/ai-paths/previous` 与 Reply `/opt/ai-paths-v3/previous` 均指向已验证的上一 clean release `ai-paths-unified-20260906-154524-007bf2c8@007bf2c8`。
- 前端 `/opt/ai-paths-frontend/previous` 指向 `frontend-20260906-154524-007bf2c8`；环境与 unit 备份位于权限受限的 `/opt/ai-paths/backups/pre-8f911bea-20260906-231341/`。
- 本次无数据库迁移。回滚仍应同时恢复三个后端角色、前端、release 环境标识并重新核验健康。

## 本次发布观察

- V3 唯一回复路由为 `/reply/workflow-compatible-v3`，未注册产品 V1/V2 回复路由。
- Reply 进程实际环境为 `MODEL_REPLY=deepseek-chat`；共享基础环境中的其他角色模型值不代表 Reply 实际模型。
- Nginx 配置检查通过，四个 service 均 `NRestarts=0`。
- MySQL/RDS 在发布前后均有间歇性连接超时；当前三个角色健康、SOP 队列和 pending 为 0，但该外部连接风险需继续处理，详见 `KNOWN_ISSUES.md`。

每次发布任务都必须重新记录 main SHA、三个角色 release/健康、数据库、worker/outbox、Nginx 和回滚点。超过核验时间后，本页只能作为线索，不能替代现场事实。
