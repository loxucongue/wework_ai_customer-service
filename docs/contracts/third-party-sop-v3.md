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
