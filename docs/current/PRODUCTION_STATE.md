# 生产状态

- status: verified-snapshot
- owner: operations
- verified_at: `2026-09-06T16:04:08+08:00`
- source_of_truth: 服务器现场核验；本页只在上述时刻有效

## 当前后端 release

- release: `ai-paths-unified-20260906-154524-007bf2c8`
- git commit: `007bf2c88c1405bc74c4e13320b0e27f667673c4`
- branch contract: `main`
- dirty: `false`
- config revision: `74eee9d04bedb99c0dc25ef2aaefab2cd96d2d8ef40fdf500a8ff838fd4ae4c0`
- database backend: MySQL

| 角色 | Unit | 现场状态 | 现场健康信息 |
| --- | --- | --- | --- |
| control | `ai-paths.service` | active/running | `/health` 返回 `service_role=control`，后台 worker 关闭 |
| reply | `ai-paths-v3.service` | active/running | `/health` 返回 `service_role=reply`，与当前 release/commit 一致 |
| worker | `ai-paths-workers.service` | active/running | `/health` 返回 `service_role=worker`，后台 worker 已启用 |
| 管理前端 | `ai-paths-frontend.service` | active/running | 本机监听 5000，`/logs` 返回 HTTP 200 |

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
- Reply 健康信息显示策略数据 outbox：`sent=1994`、`pending=13`、`dead=16`。
- 策略数据外发当前 `delivery_enabled=false`；恢复前必须对 dead/pending 做专项审计，不能直接批量重放。
- 平台订单异步归因开关未在本次安全配置核验中发现显式启用值；在下一次归因或发布任务中重新确认，不把未知写成已启用。

## 回滚状态

- 当前 `/opt/ai-paths/previous` symlink 不存在，不能把它当作可直接执行的回滚点。
- 上一 release 目录仍存在：`ai-paths-unified-20260906-153201-d085b01c`，manifest 显示 clean commit `d085b01cba6fbe3fff2006a618496f4b1bb5a3a3`。
- 该目录只是回滚候选；执行回滚前仍需重新核验依赖、配置、数据库兼容和三个角色健康。下一次发布必须先恢复明确的 `previous` 指针或记录等价的可执行回滚步骤。

每次发布任务都必须重新记录 main SHA、三个角色 release/健康、数据库、worker/outbox、Nginx 和回滚点。超过核验时间后，本页只能作为线索，不能替代现场事实。
