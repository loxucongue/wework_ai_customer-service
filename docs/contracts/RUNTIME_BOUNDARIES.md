# 运行版本边界

- status: current
- owner: backend/platform
- code_verified: 2026-09-10 Asia/Shanghai, `main@0e7f76e5`
- source_of_truth: 当前 FastAPI 路由、运行角色装配和版本化 Nginx 配置
- current_runtime: [生产状态](../current/PRODUCTION_STATE.md)

本文只定义长期运行边界。实际 release、服务健康、环境开关和回滚点属于动态事实，发布前必须按生产状态文档和现场结果重新核验。

## 产品接口

- 唯一客户回复入口是 `POST /api/ai/reply/workflow-compatible-v3`。
- Reply 角色内部路径 `/reply/workflow-compatible-v3` 只供 Nginx 转发，外部调用方不得绕过公网鉴权。
- 企业微信固定加好友开场和消息撤回属于平台协议事件，不是客户销售语义。入口应在 AI/人工状态、语音、连续消息协调和模型链之前识别；命中后只保存轻量幂等审计，不保存为客户消息、不取消主动唤醒、不写策略采用，也不产生客户可见动作。
- 历史 V1/V2 回复路由不得重新注册。公网对已退役路径保持 HTTP 410；应用内部不存在“接收但不执行”的隐式兼容回复链。

退役产品路径包括：

- `/api/ai-paths/chat`
- `/api/ai/chat`
- `/api/ai/chat/workflow-compatible`
- `/api/ai/reply`
- `/api/ai/reply/workflow-compatible`
- `/api/ai/reply/workflow-compatible-v2`
- `/api/ai-paths/refactor-health`

以下版本号不是产品 V1/V2，必须保留：

- `/api/ai/callbacks/v1/message-delivery`：消息送达回调协议。
- `/api/v1/platform-agent/...`：上游平台协议。
- 模型供应商的 `/v1`、`/api/v3` base URL。
- 历史 schema/interface version：仅用于存量数据只读兼容。

## 已删除接口

以下接口不再注册，应用直接返回 404：

- `POST /callbacks/v1/conversation-mode`
- `POST /sop/events`
- `GET /admin/sop-platform-tasks/quiet-backlog`
- `GET /admin/sop-platform-tasks/quiet-backlog/{event_id}`

实时人工接管查询、第三方 SOP 的 `humantakeover` 回传、消息送达回调和 `/admin/sop-events` 历史只读日志不受影响。

## V3 请求终态与幂等

- 只有 `corp_id + wechat + external_userid + msgid` 四项完整时，托管平台消息才使用 `generation_key=sha256(corp_id|wechat|external_userid|msgid)` 作为服务端持久幂等键。相同键只允许创建一个 run、执行一次 Router/Reply 并保存一份最终结果；兼容入口缺少 `msgid` 时不承诺跨进程持久幂等。
- 建立了 `generation_key` 的生成会产生稳定 `response_id`，批次内每条客户可见消息产生稳定 `client_message_id`。已有完整结果时直接重放并标记 `replayed=true`，不得重新生成第二组消息 ID。
- 稳定消息 ID 是提供给聚合平台的去重证据；若下游忽略该 ID 并重复发送，必须结合聚合平台发送日志处理，不能把 AI 内部幂等误称为客户侧已闭环。
- 普通客户消息在同一销售接触边界内先建立幂等任务，再查询实时接待状态。平台明确为人工时返回空且不进入模型；状态未知或查询异常时返回唯一中性文本“您稍等一下”，不得用 HTTP 200 空回复伪装成功。
- 正常业务请求最终只能是：非空业务回复、被更新消息取代、协议事件过滤、明确人工接管，或技术失败兜底。日志必须区分这些终态。

## 可选失败补答

客户可见自动补答是独立可关闭能力，不是启用 V3 的前提。实际开关值只记录在生产状态中。

- 只有正常销售链因技术异常返回“您稍等一下”时，才可进入 `fallback_pending`；业务事实缺失不属于技术失败。
- 若启用补答，Worker 最多按配置重试；发送前必须重新确认没有更新客户消息、仍为 AI 接待、没有明确退订/人工接管/关系删除、没有权威交易终态，且相同恢复消息尚未成功送达。
- 补答通过 `message_dispatches` 发送，幂等键为 `v3-recovery:<original_request_id>`；达到最大尝试次数后转内部复核，不得无限重试。
- 恢复链不得执行交易写入、策略回写、BI 或主动唤醒计划；只生成经过发送前门禁的客户可见候选。
- 关闭恢复能力必须立即停止新的自动补答，但不能影响正常 V3 回复和已保存审计。

状态门禁和销售模型使用独立预算。性能优化不得把未知接待状态推断为 AI，也不得通过过短超时批量制造兜底。

## 运行角色

| 服务 | 角色 | 必须承担 | 不得承担 |
| --- | --- | --- | --- |
| `ai-paths-v3.service` | `reply` | V3 回复 API、同步保存客户可见结果和可靠收尾状态 | 第三方 SOP 拉取、策略数据回传 Worker |
| `ai-paths.service` | `control` | 共享控制面、回调和非 Reply 管理接口 | 后台 Worker 混装 |
| `ai-paths-workers.service` | `worker` | 第三方 SOP、主动触达、可靠收尾和按开关启用的恢复任务 | 对外暴露产品回复入口 |
| `ai-paths-frontend.service` | frontend | 管理页面和 Next API | 客户回复决策 |

- 后端三个角色发布时必须使用同一个已验证、干净的 `main` SHA。
- Reply 和 Control 必须设置 `AI_PATHS_BACKGROUND_WORKERS_ENABLED=false`；Worker 必须设置为 `true`。
- 旧环境值 `primary`、`workers`、`model_led_sales_brain_v3` 仅作迁移读取，新部署不得继续使用。
- 已退役的 refactor/backend/V2 service 不得重新创建或启用。
- 各角色只装配自身需要的重型依赖。Worker 缺少自身必需上游地址或令牌时应拒绝启动；Reply 不得因 Worker 专属凭据缺失而失败。

## 发布核验

发布清单至少记录：`branch=main`、完整 commit、`dirty=false`、`service_role`、`interface_version=v3` 和可恢复 release。发布后分别验证 Reply、Control、Worker、消息回调和管理页；动态结果写入生产状态，不回填到本合同。
