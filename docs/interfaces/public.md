# 对外暴露接口

本页记录外部系统、前端或运营工具调用 AI Paths 的接口。鉴权 token 不写入文档。

## 产品回复接口

- 唯一产品入口：`POST /api/ai/reply/workflow-compatible-v3`
- Reply 角色内部 FastAPI 路径：`POST /reply/workflow-compatible-v3`；公网 `/api/ai/...` 由 Nginx 转发，调用方不得绕过公网鉴权和路由合同。
- 代码路由：`ai_paths/app/routers/reply.py`
- 鉴权：
  - `Authorization: Bearer <AI_PATHS_API_KEY 或 AI_EXTERNAL_API_KEY>`
  - 或 `X-API-Key`
- 读写性质：会触发 V3 回复链路；可能在本地存储运行记录、消息记录、素材使用记录和必要的异步提交状态。
- 身份入参：托管请求必须分别提供 `corp_id`、接待企微 `wechat`、企微外部联系人 `external_userid`、平台客户 ID `customer_id`（兼容名 `platform_customer_id`）和平台接待人员 ID `user_id`。这些 ID 不得互相补位；缺失或把 `wm...` 外部联系人写入平台客户字段时返回 400。`customer_add_wechat_id` 作为可选关系 ID 独立传递。
- 运行边界：
  - V3 是唯一客户回复产品接口。
  - V1/V2 回复路由不得重新注册。
  - 企业微信固定开场和撤回协议事件在 AI/人工状态、语音和模型链之前快速返回空回复；接口仍返回稳定 `execute_id/trace_id` 并保留轻量审计，但不把协议内容保存为客户消息或触发策略、主动唤醒和交易动作。
  - 真实发送客户消息必须受发送链路和回调合同约束。
  - 接口端到端耗时从请求接收计算到 HTTP 响应完成，包含模型图之后的 run、历史、BI、outbox 和响应组装；不能用模型图耗时代替。

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
- `GET /admin/customer-identities/quality`
- `GET /admin/customer-identities/conflicts`
- `GET /admin/customers/{customer_id}/memory`
- `DELETE /admin/customers/{customer_id}/memory`
- `GET /admin/customer-records`
- `POST /admin/customer-records/clear`

客户身份质量接口只统计已保存映射、历史混用和冲突；冲突接口最多返回 500 条待核对映射。两者均为 Bearer 鉴权后的内部只读接口，不自动改写历史客户数据。

### 运行与消息送达

- `GET /admin/message-deliveries/{dispatch_id}`
- `GET /admin/runs`
- `GET /admin/runs/{request_id}`
- `GET /admin/runs/{request_id}/nodes/{node_id}`
- `GET /admin/operations-dashboard`

管理页面：`/logs`。页面默认展示业务摘要和执行阶段，节点输入、输出、模型、工具与脱敏原始记录只在点击节点后读取。

运行日志接口口径：

- `GET /admin/runs` 默认返回轻量运行摘要和 `business_summary`；可按时间、企微号、客户/会话、运行状态、最终意图、情绪、Router 卡点、决策状态、序列匹配/采用、话术采用和节点失败筛选。即使策略 usage event 未写入，运行记录仍会保留在列表中，缺失维度标记为未记录。
- `GET /admin/runs` 每条记录的 `duration_ms` 对新产生的 V3 请求表示完整服务端接口耗时：从请求进入 Reply 服务开始，到 HTTP 响应最后一段成功发出为止，包含鉴权、请求解析、业务预处理、模型/工具、持久化尾部和响应序列化。历史记录仍沿用当时口径，缺失时管理页显示“未记录”。
- `GET /admin/runs/{request_id}?include_debug=false` 返回 `observability_view`，其中 `decision_summary` 以 V3 Reply 的 `policy_decision` 为准，`checkpoint_summary` 分开呈现 Router 检索卡点和 Reply 最终卡点，`knowledge_match` 分开呈现候选、采用与交付，`workflow_nodes` 呈现固定业务阶段。轻量模式只返回节点标识、状态和耗时，不读取节点原始输入输出。
- `observability_view.customer_identity` 只整理本轮已经留存的身份字段：`request_id`、`conversation_id`、`customer_id`、`customer_add_wechat_id`、`external_userid`、`corp_id`、`user_id` 和 `wechat`。管理页不调用当前平台接口补造历史身份，缺失字段显示“未记录”。
- 旧调用不传 `include_debug` 时继续返回原有完整详情；管理页面默认使用轻量模式。
- `GET /admin/runs/{request_id}/nodes/{node_id}` 仅在用户点击节点时读取该节点现有留存的输入、输出、模型和工具记录，并递归隐藏 token、Authorization、密钥和密码。节点必须同时属于指定请求，禁止跨请求读取。
- 原始节点轨迹沿用现有 14 天保留期，运行和业务摘要沿用现有 90 天保留期。历史字段缺失、轨迹过期和真实零候选必须分别展示；禁止用当前知识目录回填历史正文。

### V3 跟进策略 BI

- `GET /admin/v3-strategy-analytics/summary`
- `GET /admin/v3-strategy-analytics/by-intent`
- `GET /admin/v3-strategy-analytics/by-emotion`
- `GET /admin/v3-strategy-analytics/by-closing`
- `GET /admin/v3-strategy-analytics/by-closing-rule`
- `GET /admin/v3-strategy-analytics/by-checkpoint`
- `GET /admin/v3-strategy-analytics/by-sequence`
- `GET /admin/v3-strategy-analytics/by-script`
- `GET /admin/v3-strategy-analytics/transitions`
- `GET /admin/v3-strategy-analytics/failures`
- `POST /admin/v3-strategy-analytics/outcomes/refresh`
- `by-intent`、`by-emotion`、`by-closing`、`transitions` 属于统一销售决策观测增强接口；部署未包含对应后端版本时，管理页面必须将这些维度标记为暂不可用，不能把缺失数据解释为零。
- 管理页面：`/analytics/sales`；前端通过同源只读聚合代理 `/api/v3-strategy-analytics` 并发读取上述查询接口。单个维度不可用时展示局部空态，`summary` 不可用时展示明确错误，不伪造零值；采用指标没有有效分母时返回并展示不可用，而不是 `0%`。
- 鉴权：`AI_PATHS_API_KEY`。
- 常用筛选：`started_from`、`started_to`、`corp_id`、`wechat`、`checkpoint_code`、`sequence_id`、`script_id`、`action_code`、`fallback_used`、`intent_code`、`emotion_code`、`closing_rule_id`（最终决策保留的主命中规则）、`closing_sequence_key`、`closing_action`、`closing_catalog_status`、`closing_rule_match_status`、`closing_constraint_status`、`decision_status`。
- 页面主链路指标：有效策略决策轮次、卡点识别率、序列候选覆盖、序列正式采用率、话术候选覆盖、话术正式采用率、策略正常完成/降级、意图/情绪/逼单动作分布和真实跨轮变化。7d 排客率不再作为管理页面指标。
- `summary` 的 `checkpoint_turn_count`、`sequence_candidate_turn_count`、`script_candidate_turn_count` 及其采用指标只统计存在策略版本且未被 `not_enabled/system_guard/skipped` 排除的有效决策轮次。候选表示策略提供了可选内容，不表示 Reply 已使用。
- `sequence_adopted`、`script_adopted` 只来自 Reply 最终输出的真实 ID；`adoption_detail_observed` 标记该轮是否有可验证的采用详情。历史记录无法从留存运行快照确认时不进入采用率分母，禁止把 Router 召回 ID 当成正式采用。
- 卡点视图同时返回卡点轮次、序列/话术候选覆盖和正式采用数据，用于区分“业务内容缺失”与“已有候选但未采用”；序列、话术排行只展示可验证的正式采用记录。采用频次用于筛选复盘对象，不等于转化效果。
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

`precision-qa-playbook` 和 `sop-objection-materials` 的独立管理页面已经退役，但接口及底层服务仍保留：自动回复、沉默客户唤醒或 SOP 运行链仍可能读取这些配置，不能按“页面未使用”推断运行能力无用。

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
- `GET /admin/outreach/dashboard`
- `GET /admin/outreach/first-day-runs`
- `GET /admin/outreach/first-day-runs/{workflow_run_id}`

上述 `first-day` 路径是历史兼容名称；产品页面统一称为“千人千面日志”，实际记录的是沉默客户唤醒，当前运行逻辑不限制加微时间。

- `GET /admin/outreach/dashboard` 是只读 BI 聚合接口，支持 `started_from`、`started_to`、`corp_id`、`wechat` 和 `queue_limit`，最长查询 31 天。返回扫描到预约/支付进展的漏斗、成功触达与 24h 开口趋势、未触达原因、当前队列、企微号分布、数据新鲜度和后台配置。队列项同时返回 `customer_id`、`external_userid`、`conversation_id`、`corp_id`、`user_id`、`wechat`、`workflow_run_id`、`plan_id` 和轻量 `task_refs`；会话身份只按完整销售接触边界补齐。管理前端 `/analytics/outreach` 还会并行读取 Worker `/health`，只有配置、阈值、账号范围和计划/执行任务同时一致才显示“运行中”。详细口径见 [主动唤醒 BI 观测合同](../contracts/outreach-analytics.md)。
- `GET /admin/outreach/first-day-settings` 返回的关键运行信息包括启用状态、沉默分钟数、企微范围、启用水位、动态任务来源、每日计划/任务不限制，以及夜间开始、结束、恢复和活跃压缩窗口。Worker `/health` 额外返回目标扫描间隔、连续失败数和下一次等待秒数；正常目标间隔为 15 秒，扫描串行且失败最长退避到 60 秒。历史 `first-day` 路径只为接口兼容。
- 修改设置会影响主动触达候选范围，属于有运行副作用的管理操作；全账号空白名单不等于跳过 AI/人工、安全、退订、订单和最新消息门禁。
- `GET /admin/outreach/first-day-runs` 的每条记录增加可选 `business_summary`，包含最近客户消息摘要、客户当前需要、卡点/主线模式、所选序列和动态任务数。详情按节点展示候选话术、正式采用话术、调度模式和发送结果；历史运行缺少字段时返回空值，不把“未记录”伪装成 0。
- `GET /admin/outreach/first-day-runs/{workflow_run_id}` 增加可选 `observability_view`，按 `decision`、`customer_context`、`materials`、`workflow_nodes` 和 `data_availability` 组织现有留存数据。素材步骤区分候选、可用、计划附加和已发送；节点包含脱敏输入输出、模型、Prompt 版本、耗时和重试信息。该视图只从当时快照派生，不返回素材 URL、不重新查询当前目录、不延长原始数据留存期。

## 健康检查

- `GET /health`
- 用途：发布和运行期健康检查。
- 注意：健康检查返回的 release、role 和服务状态是现场事实，只在检查时刻有效。
- Worker 角色额外返回 `silence_outreach_worker`：包括沉默唤醒启用状态、分钟阈值、全账号/白名单范围、决策模型、启用水位、计划/执行任务存活状态和最近扫描摘要；不包含 token、客户消息或模型原始输出。
