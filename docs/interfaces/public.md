# 对外暴露接口

本页记录外部系统、前端或运营工具调用 AI Paths 的接口。鉴权 token 不写入文档。

## 产品回复接口

- 唯一产品入口：`POST /api/ai/reply/workflow-compatible-v3`
- 代码路由：`ai_paths/app/routers/reply.py`
- 鉴权：
  - `Authorization: Bearer <AI_PATHS_API_KEY 或 AI_EXTERNAL_API_KEY>`
  - 或 `X-API-Key`
- 读写性质：会触发 V3 回复链路；可能在本地存储运行记录、消息记录、素材使用记录和必要的异步提交状态。
- 运行边界：
  - V3 是唯一客户回复产品接口。
  - V1/V2 回复路由不得重新注册。
  - 真实发送客户消息必须受发送链路和回调合同约束。

## 消息送达回调

- 接口：`POST /api/ai/callbacks/v1/message-delivery`
- 代码路由：`ai_paths/app/routers/callbacks.py`
- 合同文档：`docs/contracts/message-delivery-callback.md`
- 鉴权：回调 token。
- 读写性质：写入送达状态，并可能触发对应发送链路的终态处理。
- 运行边界：
  - 只记录真实送达事实。
  - 不能替代 SOP 消费接口或策略数据回传接口。

## 管理和诊断接口

这些接口受 `AI_PATHS_API_KEY` 保护，主要用于运营、诊断和只读观察；写接口必须单独审计。

### 客户与会话

- `GET /admin/conversations`
- `GET /admin/conversations/{conversation_id}`
- `GET /admin/customers/{customer_id}/memory`
- `DELETE /admin/customers/{customer_id}/memory`
- `GET /admin/customer-records`
- `POST /admin/customer-records/clear`

### 运行与消息送达

- `GET /admin/message-deliveries/{dispatch_id}`
- `GET /admin/runs`
- `GET /admin/runs/{request_id}`
- `GET /admin/operations-dashboard`

### V3 跟进策略 BI

- `GET /admin/v3-strategy-analytics/summary`
- `GET /admin/v3-strategy-analytics/by-intent`
- `GET /admin/v3-strategy-analytics/by-emotion`
- `GET /admin/v3-strategy-analytics/by-closing`
- `GET /admin/v3-strategy-analytics/by-checkpoint`
- `GET /admin/v3-strategy-analytics/by-sequence`
- `GET /admin/v3-strategy-analytics/by-script`
- `GET /admin/v3-strategy-analytics/transitions`
- `GET /admin/v3-strategy-analytics/failures`
- `POST /admin/v3-strategy-analytics/outcomes/refresh`
- `by-intent`、`by-emotion`、`by-closing`、`transitions` 属于统一销售决策观测增强接口；部署未包含对应后端版本时，管理页面必须将这些维度标记为暂不可用，不能把缺失数据解释为零。
- 管理页面：`/analytics/sales`；前端通过同源只读聚合代理 `/api/v3-strategy-analytics` 读取上述查询接口。代理先读取现有核心接口，仅在 `summary` 声明统一决策指标后再读取意图、情绪、逼单和跨轮变化，避免向旧后端持续请求不存在的接口。单个维度不可用时展示局部空态，`summary` 不可用时展示明确错误，不伪造零值。
- 鉴权：`AI_PATHS_API_KEY`。
- 常用筛选：`started_from`、`started_to`、`corp_id`、`wechat`、`checkpoint_code`、`sequence_id`、`script_id`、`action_code`、`fallback_used`、`intent_code`、`emotion_code`、`closing_sequence_key`、`closing_action`、`decision_status`。
- 指标：使用与采用、策略决策覆盖/降级、发送成功、客户 24h 开口、72h 支付、7d 排客、意图/情绪/逼单分布、真实跨轮情绪变化、selector empty/error、taxonomy fallback、退订误推进、新卡点未暂停、订单查询成功率和订单窗口可归因率。
- 数据边界：不返回完整客户聊天原文；只返回 ID、分类、策略、话术、发送状态和归因窗口结果。
- 归因口径：时间窗口统计，不声明强因果。客户开口只来自已标记为真实客户轮次的后续 V3 消息；平台自动消息、撤回、去重/覆盖和隔离评测消息不计入。启用平台订单归因后，支付、排客、到店和完成优先使用平台只读订单状态。送达未知不计算开口/订单窗口；订单接口成功但基线不足与查询失败分别统计，均不能写成未成交。
- `transitions` 只返回已有下一次真实 V3 客户回复的变化，不把“尚未回复”伪装成空意图/空情绪迁移。
- `failures` 不把所有 `adopted=false` 当失败；明确退订、系统拦截、无卡点和无需匹配不会淹没真实 selector、策略结构或送达失败。

### 策略、门店和 playbook

- `POST /admin/store-snapshot/refresh`
- `GET /admin/precision-qa-playbook`
- `PUT /admin/precision-qa-playbook`
- `GET /admin/ai-sales-policy`
- `GET /admin/ai-sales-strategy-catalog`

`precision-qa-playbook` 和 `sop-objection-materials` 的独立管理页面已经退役，但接口及底层服务仍保留：自动回复、首日触达或 SOP 运行链仍可能读取这些配置，不能按“页面未使用”推断运行能力无用。

### SOP 与 outreach

- `GET /admin/sop-reply-packs`
- `PUT /admin/sop-reply-packs`
- `GET /admin/sop-objection-materials`
- `PUT /admin/sop-objection-materials`
- `GET /admin/sop-events`
- `GET /admin/sop-events/{event_id}`
- `GET /admin/sop-platform-tasks`
- `GET /admin/sop-platform-runs`
- `POST /admin/sop-platform-tasks/{task_id}/resend`
- `GET /admin/outreach/first-day-settings`
- `PUT /admin/outreach/first-day-settings`
- `GET /admin/outreach/first-day-runs`
- `GET /admin/outreach/first-day-runs/{workflow_run_id}`

## 健康检查

- `GET /health`
- 用途：发布和运行期健康检查。
- 注意：健康检查返回的 release、role 和服务状态是现场事实，只在检查时刻有效。
