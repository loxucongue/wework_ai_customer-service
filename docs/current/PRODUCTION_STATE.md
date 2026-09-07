# 生产状态

- status: verified-snapshot
- owner: operations
- verified_at: `2026-09-07T10:26:14+08:00`
- source_of_truth: 服务器现场核验；本页只在上述时刻有效

## 当前后端 release

- release: `ai-paths-unified-20260907-102203-753c3324`
- git commit: `753c33245276b5eb8f6b6e9a00a32e2debd12470`
- branch contract: `main`
- dirty: `false`
- config revision: `74eee9d04bedb99c0dc25ef2aaefab2cd96d2d8ef40fdf500a8ff838fd4ae4c0`
- database backend: MySQL

| 角色 | Unit | 现场状态 | 现场健康信息 |
| --- | --- | --- | --- |
| control | `ai-paths.service` | active/running | `/health` 返回 `service_role=control`，后台 worker 关闭 |
| reply | `ai-paths-v3.service` | active/running | `/health` 返回 `service_role=reply`，与当前 release/commit 一致 |
| worker | `ai-paths-workers.service` | active/running | `/health` 返回 `service_role=worker`，后台 worker 已启用 |
| 管理前端 | `ai-paths-frontend.service` | active/running | `frontend-20260907-102203-753c3324`；`/logs/outreach-first-day` 返回 HTTP 200，展示沉默唤醒业务与素材链路 |

三个后端角色均由同一 clean main SHA 构建。V3 Reply 进程的有效覆盖配置为 `MODEL_REPLY=deepseek-chat`、Reply fallback 为空；沉默唤醒由独立 `OUTREACH_DECISION_MODEL=deepseek-chat` 配置控制且 fallback 为空，不再继承 worker 的通用 GPT tier。共享基础环境仍保留其他角色的全局模型默认值，实际模型应以每次 run trace 为准。

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
- Reply 健康信息显示策略数据 outbox：`sent=1994`、`pending=20`、`dead=16`。
- 策略数据外发当前 `delivery_enabled=false`；恢复前必须对 dead/pending 做专项审计，不能直接批量重放。
- 平台订单异步归因开关未在本次安全配置核验中发现显式启用值；在下一次归因或发布任务中重新确认，不把未知写成已启用。

## 回滚状态

- 后端 `/opt/ai-paths/previous` 与 Reply `/opt/ai-paths-v3/previous` 均指向上线前 clean release `ai-paths-unified-20260907-095632-8d333d06`。
- 前端 `/opt/ai-paths-frontend/previous` 指向 `frontend-20260907-095632-8d333d06`；本次环境与 release 指针备份位于权限受限的 `/opt/ai-paths/backups/pre-753c3324/`。
- 数据库已迁移到 `20260907_01`，只增加序列采用、话术采用和采用详情已观测三个字段；旧代码会忽略这些字段，不需要破坏性降级。
- 回滚仍应同时恢复三个后端角色、前端和 release 环境标识，并重新核验健康。

## 本次发布观察

- V3 唯一回复路由为 `/reply/workflow-compatible-v3`，未注册产品 V1/V2 回复路由。
- Reply 进程 `/proc/<pid>/environ` 已现场确认 `MODEL_REPLY=deepseek-chat`、`AI_SALES_POLICY_ENABLED=true`；共享基础环境中的其他角色模型值不代表 Reply 实际模型。
- Nginx 配置检查通过，四个 service 均 `NRestarts=0`。
- Worker 健康信息已显示沉默唤醒为全账号、1 分钟、`deepseek-chat`、无 fallback，计划扫描和发送执行任务均存活。发布后首批 9 个判断产生 2 个计划并各成功发送第一步，5 个明确人工模式被阻断，1 个重复指纹被阻断，1 个场景合同失败进入有限重试；留存模型均为 DeepSeek，GPT 和“不支持模型”错误为 0。
- 指定问题客户已经生成两步计划并成功发送第一步；第二步保持 pending，仅在客户继续沉默且发送前平台仍明确为 AI 时执行。1 分钟是进入候选阈值，不是固定发送时刻；首批多节点计划生成和平台/RDS 调用仍有约 1～3 分钟延迟，详见 `KNOWN_ISSUES.md`。
- 已采用的话术若带有本轮相关、安全且未发送的效果图/视频，会把媒体作为客户可见结构消息直接交付，不再先问“要不要发效果图”；活动价格等已有权威价值也应直接回答。代码只补齐模型已经采用的话术自身媒体，不跨话术或跨主题替模型做销售选择。
- 指定问题场景 DeepSeek 隔离复现 2/2 通过；20 条真实身份只读矩阵 AI 初评通过率和真人表达通过率均为 95%，许可式素材追问为 0，策略适用样本中的序列/话术采用为 6/6，生产发送和关键写入均为 0。确定性回归为 326 条全部通过。
- 销售策略 BI 已改为“有效决策 → 卡点 → 序列/话术候选 → Reply 正式采用”的管理链路，移除 7d 排客率。候选和正式采用使用独立字段，历史回填 110 条均有运行快照证据、0 条无法确认；有效客户轮次中旧序列字段的 9 条有 5 条只是候选，正式采用为 4 条，话术正式采用为 2 条。
- 30 天线上口径为 213 个客户、279 个真实 V3 轮次、23 个有效策略决策；卡点识别率 `9/23=39.1%`、序列正式采用率 `4/9=44.4%`、话术正式采用率 `2/10=20.0%`、正常决策 `16/23=69.6%`。整页接口实测 `4.21s`，9 个并发只读视图无错误。
- 桌面和 390px 手机真实浏览器验收通过：无控制台错误、无横向溢出、7d 排客率未出现；334 条后端回归、前端类型/Lint/生产构建通过。
- MySQL/RDS 在发布前后均有间歇性连接超时；当前三个角色健康、SOP 队列和 pending 为 0，但该外部连接风险需继续处理，详见 `KNOWN_ISSUES.md`。
- 沉默计划模型现在接收去 URL 的素材来源、用途与本轮可发送状态；写作和审核共享逐步媒体交付合同。当前生产目录共 68 张图片、0 个视频；无媒体步骤禁止生成悬空看图表达，`effect_proof` 必须绑定真实可发媒体。
- 千人千面日志列表和详情接口已现场返回 `business_summary`、`observability_view`、10 个模型/修复节点及素材摘要；前端桌面、手机和节点抽屉已验收。
- 生产根分区使用率为 97%、剩余约 1.4 GB；这是当前最高运维风险。本次前端 release 通过只读硬链接复用未变更的依赖文件，仅新增构建产物，并已清理自身 `/tmp`；后续仍必须按保留规范先归档再清理，同时保留当前与已验证回滚版本。

每次发布任务都必须重新记录 main SHA、三个角色 release/健康、数据库、worker/outbox、Nginx 和回滚点。超过核验时间后，本页只能作为线索，不能替代现场事实。
