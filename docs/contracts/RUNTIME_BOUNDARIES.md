# 运行版本边界

- status: current
- owner: backend/platform
- last_verified: 2026-09-01 Asia/Shanghai
- source_of_truth: 当前 FastAPI route 表与版本化 Nginx 配置；生产 systemd 仍需发布前现场核验

## 产品接口

- 唯一客户回复入口：`POST /api/ai/reply/workflow-compatible-v3`。
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
