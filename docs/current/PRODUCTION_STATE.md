# 生产状态

- status: verified-snapshot
- owner: operations
- verified_at: `2026-09-07T00:42:27+08:00`
- source_of_truth: 服务器现场核验；本页只在上述时刻有效

## 当前后端 release

- release: `ai-paths-unified-20260907-003744-5ad40a66`
- git commit: `5ad40a665713228a7a01ffef3ddc353bc22dbd07`
- branch contract: `main`
- dirty: `false`
- config revision: `74eee9d04bedb99c0dc25ef2aaefab2cd96d2d8ef40fdf500a8ff838fd4ae4c0`
- database backend: MySQL

| 角色 | Unit | 现场状态 | 现场健康信息 |
| --- | --- | --- | --- |
| control | `ai-paths.service` | active/running | `/health` 返回 `service_role=control`，后台 worker 关闭 |
| reply | `ai-paths-v3.service` | active/running | `/health` 返回 `service_role=reply`，与当前 release/commit 一致 |
| worker | `ai-paths-workers.service` | active/running | `/health` 返回 `service_role=worker`，后台 worker 已启用 |
| 管理前端 | `ai-paths-frontend.service` | active/running | 继续使用 `frontend-20260907-bi-4a926b66`，本次后端变更不包含前端差异；`/analytics/sales` 返回 HTTP 200 |

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

- 后端 `/opt/ai-paths/previous` 与 Reply `/opt/ai-paths-v3/previous` 均指向上线前 clean release `ai-paths-unified-20260907-bi-4a926b66`。
- 前端 `/opt/ai-paths-frontend/previous` 指向 `frontend-20260907-bi-54a58a58`；环境与 unit 备份位于权限受限的 `/opt/ai-paths/backups/pre-4a926b66/`。
- 本次后端环境备份位于 `/opt/ai-paths/backups/pre-5ad40a66/`。
- 本次无数据库迁移。回滚仍应同时恢复三个后端角色、前端、release 环境标识并重新核验健康。

## 本次发布观察

- V3 唯一回复路由为 `/reply/workflow-compatible-v3`，未注册产品 V1/V2 回复路由。
- Reply 进程 `/proc/<pid>/environ` 已现场确认 `MODEL_REPLY=deepseek-chat`、`AI_SALES_POLICY_ENABLED=true`；共享基础环境中的其他角色模型值不代表 Reply 实际模型。
- Nginx 配置检查通过，四个 service 均 `NRestarts=0`。
- 已采用的话术若带有本轮相关、安全且未发送的效果图/视频，会把媒体作为客户可见结构消息直接交付，不再先问“要不要发效果图”；活动价格等已有权威价值也应直接回答。代码只补齐模型已经采用的话术自身媒体，不跨话术或跨主题替模型做销售选择。
- 指定问题场景 DeepSeek 隔离复现 2/2 通过；20 条真实身份只读矩阵 AI 初评通过率和真人表达通过率均为 95%，许可式素材追问为 0，策略适用样本中的序列/话术采用为 6/6，生产发送和关键写入均为 0。确定性回归为 326 条全部通过。
- 销售策略 BI 的 9 个只读视图改为同批并发；发布后整页接口三次实测 `4.57s`、`4.20s`、`10.21s`，较发布前约 `23s` 改善，但 MySQL 链路仍存在周期性约 10 秒抖动。
- MySQL/RDS 在发布前后均有间歇性连接超时；当前三个角色健康、SOP 队列和 pending 为 0，但该外部连接风险需继续处理，详见 `KNOWN_ISSUES.md`。
- 生产根分区使用率已升至 96%、剩余约 1.7 GB；这是当前最高运维风险。本次只清理自身 `/tmp` 评测/部署临时文件，不删除历史 release；后续必须按保留规范先归档再清理，同时保留当前与已验证回滚版本。

每次发布任务都必须重新记录 main SHA、三个角色 release/健康、数据库、worker/outbox、Nginx 和回滚点。超过核验时间后，本页只能作为线索，不能替代现场事实。
