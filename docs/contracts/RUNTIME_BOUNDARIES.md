# 运行版本边界

- status: current
- owner: backend/platform
- last_verified: 2026-09-06 Asia/Shanghai at `main@007bf2c8`
- source_of_truth: 当前 FastAPI route 表与版本化 Nginx 配置；生产 systemd 仍需发布前现场核验

## 产品接口

- 唯一客户回复入口：`POST /api/ai/reply/workflow-compatible-v3`。
- 企业微信固定加好友开场和消息撤回属于平台协议事件，不是客户销售语义。入口在 AI/人工状态查询、语音、连续消息协调和模型链之前识别；命中后只保存轻量、幂等的运行审计，不保存客户消息、不取消主动唤醒计划、不写策略采用或产生客户可见动作。
- 以下入口不再注册到 FastAPI；公网 Nginx 永久返回 HTTP 410：
  - `/api/ai-paths/chat`
  - `/api/ai/chat`
  - `/api/ai/chat/workflow-compatible`
  - `/api/ai/reply`
  - `/api/ai/reply/workflow-compatible`
  - `/api/ai/reply/workflow-compatible-v2`
  - `/api/ai-paths/refactor-health`

## 必须保留的非产品版本号

- `/api/ai/callbacks/v1/message-delivery`：消息送达回调协议。
- `/api/v1/platform-agent/...`：上游平台协议。
- 模型供应商的 `/v1`、`/api/v3` base URL。
- 历史 schema/interface version：允许只读兼容。
- 旧审计记录中的 `v1/v2/legacy` schema 值：只读兼容，不能用来重新启用旧运行链。

## 已删除接口

以下接口不再注册，应用直接返回 404，不保留接受后不执行的兼容层：

- `POST /callbacks/v1/conversation-mode`
- `POST /sop/events`
- `GET /admin/sop-platform-tasks/quiet-backlog`
- `GET /admin/sop-platform-tasks/quiet-backlog/{event_id}`

实时人工接管查询、第三方 SOP 的 `humantakeover` 策略回传、消息送达回调和
`/admin/sop-events` 历史只读日志不受影响。

V3 普通客户消息按接待边界和平台 `msgid` 先进入单次幂等任务，再查询实时人工接管状态：平台明确为人工时返回空且不进入模型；状态接口异常或状态未知时只返回中性“您稍等一下”，不得以 HTTP 200 空回复伪装成功。同一 `msgid` 的平台重试共享同一状态查询和同一运行结果。

## 运行角色

- `ai-paths-v3.service`：V3 回复 API，必须设置
  `AI_PATHS_SERVICE_ROLE=reply`、`AI_PATHS_BACKGROUND_WORKERS_ENABLED=false`。
- `ai-paths.service`：共享控制面和非 V3 专属 API，必须设置
  `AI_PATHS_SERVICE_ROLE=control`、`AI_PATHS_BACKGROUND_WORKERS_ENABLED=false`；在能力迁移前必须保留。
- `ai-paths-workers.service`：第三方 SOP、主动触达及恢复任务，必须设置
  `AI_PATHS_SERVICE_ROLE=worker`、`AI_PATHS_BACKGROUND_WORKERS_ENABLED=true`；必须保留。
- `ai-paths-frontend.service`：管理页面和 Next API。
- 已退役的 refactor/backend/V2 service 不属于当前仓库部署模板，不得重新创建或启用。

应用按角色只暴露所需路由并只构造所需的重型依赖。旧环境值 `primary`、`workers`、
`model_led_sales_brain_v3` 仅用于迁移兼容；新部署和文档不得继续使用。旧
`primary + workers=true` 组合会被配置校验拒绝，避免控制面与 worker 再次混装。

依赖装配也按角色分开：Reply 只同步保存客户可见回复和持久收尾状态，不创建第三方 SOP
拉取或策略数据回传客户端；Control 只保留回调与管理依赖；Worker 补齐 Reply 的非关键
观测并创建策略数据 outbox，同时负责第三方 SOP 拉取和策略数据实际回传。启用的 Worker 能力缺少自身必需的上游地址或令牌时必须拒绝
启动；Reply 不因 Worker 专属凭据缺失而失败。
