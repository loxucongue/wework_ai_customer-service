# 运行版本边界

- status: current
- owner: backend/platform
- last_verified: 2026-08-31 Asia/Shanghai
- source_of_truth: Nginx 有效配置、FastAPI route 表、systemd 单元

## 产品接口

- 唯一客户回复入口：`POST /api/ai/reply/workflow-compatible-v3`。
- 以下入口永久退役并返回 HTTP 410：
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
- 被 V3 import 的 `v2_*` 内部模块：先无行为重命名并完成回归，不能按文件名删除。

## 运行角色

- `ai-paths-v3.service`：V3 回复 API。
- `ai-paths.service`：共享控制面和非 V3 专属 API；在能力迁移前必须保留。
- `ai-paths-workers.service`：第三方 SOP、主动触达及恢复任务；必须保留。
- `ai-paths-frontend.service`：管理页面和 Next API。
- `ai-paths-refactor.service`、`ai-paths-backend.service`：已禁用，不得重新启用。
