# 第三方 SOP V3 合同

- status: current
- owner: SOP/platform integration
- last_verified: 2026-09-08 Asia/Shanghai
- source_of_truth: 当前 main SOP 代码、第三方 `/event/trigger/consume` 合同与生产日志

## 任务与内容是两个消费对象

- 到期任务来自 `/event/trigger/pending`，主键是 `taskId`，任务终态是 `30` 或 `70`。
- 待发送内容来自 `/event/trigger/sop-messages`，主键是返回项 `id`，消费回写字段是 `messages[].msgId`。
- 两者使用同一个 `/event/trigger/consume` 接口，但语义和字段独立。
- `contentExhausted` 是作废同行程剩余等待时间任务的独立开关；当前执行链不推断、不主动提交。

正常执行链不使用任务状态 `20`。任务可以从 `10` 直接进入 `30` 或 `70`：

- 主动发送接口正常返回：任务 `status=30`，并在同一次请求中只回写本次发送的一个 `msgId,status=30`。
- 没有调用成功主动发送接口：任务 `status=70`，不传 `messages`，不得改变任何待发送内容状态。
- 当前 AICS 不向 `messages` 写 `40` 或 `70`。

## 确定性执行门槛

每条到期任务只做三个发送资格判断：

1. 权威会话中客户从未开口。
2. 客户关系未删除。
3. `ai_auto_reply=true`，当前由 AI 托管。

只有三项同时成立，才允许调用 `/event/trigger/sop-messages`。任一项不成立、字段缺失或查询失败，均不得查询/发送内容，任务回传 `70` 并回传策略数据。

当前第三方 SOP 路径不调用模型，不加载客户/订单业务上下文，不生成过渡语，不改写内容，也不做夜间、过期、卡点或销售阶段判断。

## 内容选择与发送

```text
/event/trigger/pending
→ 查询 conversation/status 与 conversation
→ 三项门槛通过
→ /event/trigger/sop-messages(eventLogId)
→ 选择 nextGroup（或列表第一条未发送组）
→ 原样发送该组 message_content 的全部合法消息
→ /event/trigger/consume(taskId=30, messages=[当前 msgId=30])
→ /event/trigger/service-rule-data
```

- 一轮只发送一组，只消费该组的 `msgId`。
- 即使接口返回还有其他组，也不设置 `contentExhausted`，不消费其他 `msgId`。
- 不联动消费历史兼容任务；每个 `/pending` 任务必须独立判断、独立发送、独立回传。
- 主动发送使用基于 `msgId` 的稳定幂等键；不同到期任务再次取得同一内容组时不得重复产生客户消息。
- 主动发送接口正常返回即视为本次发送完成，包括返回“平台已受理/提交结果待回调”；不等待消息送达回调再消费。
- 主动发送接口直接报错属于未成功调用：任务回传 `70`，不传 `messages`。
- 管理页人工补发当前关闭；终态任务不能通过补发从 `70/30` 改为另一终态，也不能绕过显式 `msgId` 联合消费约束。

## 不发送与失败

以下情况统一消费任务 `70`、不消费内容，并回传对应策略数据：

- 客户已经开口。
- 客户关系已删除。
- 人工接管或 `ai_auto_reply=false`。
- 客户状态、会话或内容接口异常/字段不完整。
- 身份字段或 `eventLogId` 缺失。
- `/sop-messages` 没有下一组、`msgId` 缺失或内容不合法。
- 主动发送接口直接报错。
- 其他明确导致本任务未调用成功主动发送接口的处理失败。

客户关系已删除和人工接管继续作为非预警终态；其他未发送失败按现有钉钉告警合同预警。告警失败不改变任务或内容消费结果。

## 恢复与回调

- 发送接口调用事实、选中的 `msgId` 和原始消息必须在任务/内容联合消费前持久化。
- 发送已经发生而 `/consume` 失败时，只重试同一个 `taskId=30 + msgId=30` 请求，禁止重发客户消息。
- 任务消费成功而策略数据失败时，只重试 `service-rule-data`。
- 消息送达回调仅补充审计，不再反向改变本执行链已经提交的任务或内容状态。

## 两类独立回传

1. `/event/trigger/consume`：同时承载任务终态和可选的本次内容结果。
2. `/event/trigger/service-rule-data`：记录本任务的处理场景和结果。

两者必须分别记录请求、响应、异常与时间，凭证、鉴权头和客户消息正文不得进入告警。
消息送达回调只提供真实送达事实，不能替代以上两个接口。

## 策略场景字段

`service-rule-data` 必须发送 `sceneCode`、`sceneName`、`remark`，不得把平台原始 scene 或模型文本直接当枚举值。当前枚举：

| sceneCode | sceneName | remark | 终态 |
|---|---|---|---|
| `sop_sent` | SOP发送成功 | SOP消息已发送 | 30 |
| `sop_send_failed` | SOP发送失败 | SOP消息发送失败 | 70，不消费内容 |
| `humantakeover` | 人工接管 | 当前会话由人工接待 | 70，不消费内容，不预警 |
| `customer_deleted` | 客户删除 | 客户关系已删除 | 70，不消费内容，不预警 |
| `sop_no_send_all_filtered` | 暂无合适内容 | 当前没有适合发送的SOP内容 | 70，不消费内容 |
| `sop_no_send_invalid_content` | 平台内容无效 | 平台任务没有合法消息内容 | 70，不消费内容 |

## 历史恢复隔离与告警可用性

- 只有本合同产生并持久化了 `deterministic_customer_gate` 或 `deterministic_task_no_send` 标记的任务允许自动恢复。
- 旧执行模式遗留任务写入本地 `platform_legacy_quarantined` 后停止自动恢复；该动作不调用主动发送、不调用 `/consume`、不改变任何 `msgId`，并在 worker 重启后继续保留隔离。
- 钉钉失败告警重试使用独立后台循环。告警机器人停用、网络失败或业务错误不得阻塞 SOP 轮询、门槛判断、发送、消费或恢复；告警事件继续持久化并退避重试。

## 未开口规则

当权威会话事实确认客户未开口时，SOP 内容直接发送，不调用模型决定是否发送。会话拉取失败或客户消息时间不可靠时不得把客户当作未开口。

任务计划时间只用于排序和审计，不构成首次发送的失效窗口。即使任务已逾期，也必须重新执行未开口、未删除、AI 托管三个实时门槛；不能仅因超过固定分钟数消费为 `70`。发送接口结果不确定时的恢复等待仍保留，避免未知结果下重复主动发送。

所有任务统一执行“未开口、未删除、AI 托管”三项门槛，不再按任务类型进入模型判断。企微自动开场白不算真实客户回复；其余真实客户消息均视为已经开口。会话响应缺少客户关系或消息列表、请求失败时，任务消费 `70` 且不查询或消费消息内容。

客户真实回复需要回写策略数据时，只能在同一 `corp_id + wechat + external_userid` 边界内，关联回复时间之前最近一条真实 `status=sent` 平台 SOP 任务。`platform_customer_id` 必须来自任务显式字段或客户资料查询，不得使用 `external_userid` 补位。Shadow、未发送、失败和 `completed_without_send` 均不得关联；无匹配任务属于正常 `skipped`，不记录告警。平台任务 ID 从平台事件或 `platform-sop:<task_id>` 幂等键解析，重复回复回写仍须幂等。

## 日志审计

管理页日志版本为 `sop_platform_run_view_v3`，历史 V2 只做展示兼容。任务审计必须区分确定性门槛、提交发送、消息内容消费、任务消费、送达回调和策略数据回传；不得记录 token、鉴权头或密钥。

## 运行监控口径

- `/admin/sop-platform-dashboard` 是只读 MySQL 聚合接口；默认管理页不因查看看板而访问第三方 pending 接口。
- `/analytics/sop` 是管理 BI；`/logs/sop-platform` 是批次与任务证据；`/logs/sop` 是当前第三方任务和历史接收链共用的底层事件审计。三者不得混为同一业务口径。
- 平台任务、本地任务、客户和真实发送消息必须分别计数，不能把批次数、人数和消息条数相加或互相替代。
- “发送完成”只按主动发送接口正常返回、并使用同一请求回传唯一 `msgId=30` 的任务计数；会话归档可能包含人工消息，不能据此反推 SOP 已发送。
- “无需发送”与“异常/未完成”分开统计；原因缺失显示为未记录，不猜测业务状态。
- 队列、执行中和平台待处理数从 worker 进程 `/health` 获取；控制进程没有后台 worker，不能用其状态判断任务服务关闭。
