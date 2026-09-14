# 聚合平台主动发送优先级对接说明

## 目标

第三方 SOP 的第一组消息承担首次触达作用。AI 系统确认本次发送的是该 SOP 序列第一组后，在现有主动发送请求顶层增加 `priority`，供聚合平台优先调度整个发送批次。

## 接口

```text
POST /api/v1/platform-agent/ai-outreach/send
```

现有鉴权、地址、请求字段、响应和发送结果回调均不变。

## 新增字段

| 字段 | 类型 | 必填 | 取值 | 说明 |
|---|---|---:|---|---|
| `priority` | string | 否 | `high` | 存在时表示整个消息批次需要优先发送；不传表示现有默认优先级。 |

该字段位于请求顶层，不放入 `reply_messages`。同一批次中的文字、图片、视频和链接必须保持原顺序，并作为一个整体排队发送。

## 请求示例

```json
{
  "corp_id": "ww_example",
  "customer_id": "15200000",
  "external_userid": "wm_example",
  "user_id": "SL1580",
  "wechat": "SL1580",
  "plan_id": "platform-sop-83049",
  "task_id": "platform-sop-send-83049",
  "dispatch_id": "example-dispatch-id",
  "priority": "high",
  "reply_messages": [
    {
      "type": "text",
      "content": "您好，我是这边的客服。"
    },
    {
      "type": "image",
      "content": "https://example.com/activity.png"
    }
  ]
}
```

## AI 系统发送规则

- 仅第三方 SOP `/sop-messages` 序列的第一组实际发送内容传 `priority: "high"`。
- 第二组及后续组不传 `priority`。
- 普通 AI 回复、首日跟进和其他主动触达不传该字段。
- 客户已开口、客户关系已删除、人工接管等业务门禁仍在优先级判断之前执行；被过滤的任务不会调用发送接口。
- 以 `/sop-messages` 当前选中待发送消息组自身的 `sortOrder` 为准；`sortOrder = 1` 判定为该客户全部 SOP 中的第一组。
- 不使用 `/pending` 任务的 `sortOrder`，也不使用 `/sop-messages` 返回列表位置或 `complete` 判断第一组；接口只返回未消费内容，列表第一项可能是后续组。
- 当前选中组缺少 `sortOrder`、值无法解析或 `sortOrder > 1` 时，按普通优先级处理。
- 同一发送批次恢复或重试时复用原请求、`dispatch_id` 和优先级，不得改变优先级或创建重复发送。

## 聚合平台处理要求

- 识别顶层 `priority: "high"`，将该批次排在默认优先级任务之前。
- 高优先级只影响排队顺序，不改变同一客户消息顺序、幂等规则、发送资格和回调协议。
- 同一客户、同一企微账号内必须保持先入先发，不能让高优先级批次越过该客户已经开始执行的更早消息。
- 未识别字段时不得拒绝请求；联调完成前应保持向后兼容。
- 接口响应或回调日志应保留接收到的优先级，便于双方按 `dispatch_id` 排查。

## 联调用例

1. 第一组文字：请求包含 `priority: "high"`，聚合平台进入高优先级队列。
2. 第一组文字加图片：整个批次优先，组内顺序不变。
3. 第二组 SOP：请求不包含 `priority`。
4. 普通主动触达：请求不包含 `priority`。
5. 同一 `dispatch_id` 重试：不创建第二个队列任务，优先级保持不变。
6. 同一客户已有更早消息正在发送：不得通过优先级造成客户会话乱序。

## 待聚合平台确认

- 是否接受字段名 `priority` 和枚举值 `high`。
- 高优先级与默认队列的调度比例及防止普通任务饥饿的策略。
- 是否能在受理响应或查询日志中回显实际采用的优先级。
