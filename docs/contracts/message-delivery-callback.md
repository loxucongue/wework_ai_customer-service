# 聚合平台消息发送结果回调接入说明

## 1. 改造目标

AI 系统目前通过两个业务入口触发消息发送：

1. AI 实时回复完成后的异步发送。
2. 主动触达、SOP 任务使用的主动发送。

两条链路最终都调用聚合平台现有接口：

```text
POST /api/v1/platform-agent/ai-outreach/send
```

HTTP `2xx` 或请求读取超时，只能说明聚合平台已经接收或可能接收请求，不能证明消息已经成功发到客户会话。此次改造增加独立发送批次 ID、逐条消息 ID 和发送结果回调，以实际发送结果作为最终状态。

## 2. 聚合平台需要修改的内容

### 2.1 接收新增字段

现有发送请求字段保持不变，新增以下字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `dispatch_id` | string | 是 | AI 系统生成的发送批次 ID，同一幂等任务重试时保持不变 |
| `callback_url` | string | 是 | 聚合平台发送最终结果的回调地址 |
| `reply_messages[].client_message_id` | string | 是 | 批次内每条消息的唯一 ID，必须原样回传 |

示例：

```json
{
  "corp_id": "ww943af61cd5d2afe4",
  "customer_id": "22099221",
  "external_userid": "wmanzqsqaatm5tcgp35grtrye55g8i5g",
  "user_id": "7294",
  "wechat": "DY258",
  "plan_id": "",
  "task_id": "task-20260822-0001",
  "dispatch_id": "8f5b48c4-ef47-49df-b40b-7a91afc27b25",
  "callback_url": "http://47.252.81.104/api/ai/callbacks/v1/message-delivery",
  "reply_messages": [
    {
      "type": "text",
      "content": "客户可见文字",
      "client_message_id": "8f5b48c4-ef47-49df-b40b-7a91afc27b25:1"
    },
    {
      "type": "image",
      "content": "https://example.com/image.jpg",
      "client_message_id": "8f5b48c4-ef47-49df-b40b-7a91afc27b25:2"
    }
  ]
}
```

聚合平台不得重新生成或改变 `dispatch_id`、`client_message_id`。

### 2.2 发送接口受理响应

现有响应可以继续兼容，但建议返回聚合平台请求 ID：

```json
{
  "code": 0,
  "msg": "accepted",
  "data": {
    "platform_request_id": "platform-send-20260822-0001",
    "send_status": "accepted"
  }
}
```

此响应只代表任务被受理。AI 系统不会据此把消息标记为最终发送成功。

### 2.3 调用发送结果回调

回调接口：

```text
POST http://47.252.81.104/api/ai/callbacks/v1/message-delivery
Content-Type: application/json; charset=utf-8
X-Callback-Token: <双方约定的回调 Token>
```

当前回调入口仅允许聚合平台现有出口 IP `120.26.43.96`、`121.199.0.182` 访问；如回调任务使用其他出口 IP，聚合平台需提前提供。当前服务器只开放 HTTP，正式开启强制回执前应补 HTTPS；联调阶段仍需同时使用来源 IP 白名单和 Token。

批次全部成功：

```json
{
  "event_id": "delivery-event-20260822-0001",
  "dispatch_id": "8f5b48c4-ef47-49df-b40b-7a91afc27b25",
  "task_id": "task-20260822-0001",
  "status": "send_succeeded",
  "occurred_at": "2026-08-22T15:30:12+08:00",
  "platform_request_id": "platform-send-20260822-0001",
  "system_msgid": "system-message-batch-id",
  "items": [
    {
      "client_message_id": "8f5b48c4-ef47-49df-b40b-7a91afc27b25:1",
      "platform_message_id": "platform-message-1",
      "status": "send_succeeded",
      "sent_at": "2026-08-22T15:30:11+08:00"
    },
    {
      "client_message_id": "8f5b48c4-ef47-49df-b40b-7a91afc27b25:2",
      "platform_message_id": "platform-message-2",
      "status": "send_succeeded",
      "sent_at": "2026-08-22T15:30:12+08:00"
    }
  ]
}
```

批次全部失败：

```json
{
  "event_id": "delivery-event-20260822-0002",
  "dispatch_id": "8f5b48c4-ef47-49df-b40b-7a91afc27b25",
  "task_id": "task-20260822-0001",
  "status": "send_failed",
  "occurred_at": "2026-08-22T15:30:12+08:00",
  "retryable": true,
  "error_code": "DEVICE_OFFLINE",
  "error_message": "target device is offline"
}
```

部分成功时，`items` 必须完整列出每条消息的结果：

```json
{
  "event_id": "delivery-event-20260822-0003",
  "dispatch_id": "8f5b48c4-ef47-49df-b40b-7a91afc27b25",
  "task_id": "task-20260822-0001",
  "status": "partial_failed",
  "occurred_at": "2026-08-22T15:30:12+08:00",
  "items": [
    {
      "client_message_id": "8f5b48c4-ef47-49df-b40b-7a91afc27b25:1",
      "platform_message_id": "platform-message-1",
      "status": "send_succeeded",
      "sent_at": "2026-08-22T15:30:11+08:00"
    },
    {
      "client_message_id": "8f5b48c4-ef47-49df-b40b-7a91afc27b25:2",
      "status": "send_failed",
      "error_code": "MEDIA_DOWNLOAD_FAILED",
      "error_message": "image download failed"
    }
  ]
}
```

可选的发送中事件：

```json
{
  "event_id": "delivery-event-20260822-0004",
  "dispatch_id": "8f5b48c4-ef47-49df-b40b-7a91afc27b25",
  "task_id": "task-20260822-0001",
  "status": "sending",
  "occurred_at": "2026-08-22T15:30:10+08:00"
}
```

### 2.4 回调状态定义

| 状态 | 含义 | 是否最终状态 |
|---|---|---:|
| `sending` | 聚合平台正在执行发送 | 否 |
| `send_succeeded` | 批次或消息已实际发送成功 | 是 |
| `send_failed` | 批次或消息发送失败 | 是 |
| `partial_failed` | 同一批次中部分成功、部分失败 | 是 |

禁止把“任务入队”“设备已接收任务”“接口调用成功”上报为 `send_succeeded`。成功必须来自实际发送执行结果。

## 3. 幂等与重试要求

1. `event_id` 是回调事件的全局唯一 ID。同一个事件重试必须使用同一个 `event_id`。
2. AI 系统对重复 `event_id` 返回 `200`，并在响应中给出 `duplicate: true`。
3. 聚合平台收到非 `2xx`、连接失败或读取超时时，应指数退避重试，建议至少重试 24 小时。
4. 建议重试间隔：`10s、30s、1m、5m、15m、30m、1h`，之后每小时一次。
5. 同一 `client_message_id` 已进入最终状态后，不得回报相反的最终状态。
6. `task_id` 有值时必须与原始发送请求一致。

成功响应：

```json
{
  "code": 0,
  "msg": "ok",
  "data": {
    "event_id": "delivery-event-20260822-0001",
    "dispatch_id": "8f5b48c4-ef47-49df-b40b-7a91afc27b25",
    "duplicate": false,
    "status": "send_succeeded",
    "finalized": true
  }
}
```

错误响应：

| HTTP 状态 | 含义 | 平台动作 |
|---:|---|---|
| `400/422` | JSON 或字段不合法 | 修正请求后重试 |
| `401` | Token 错误 | 停止重试并告警配置问题 |
| `404` | `dispatch_id` 不存在 | 暂停该事件并告警双方排查环境/数据 |
| `409` | 任务 ID、消息 ID或最终状态冲突 | 停止自动重试并告警 |
| `500/503` | AI 服务暂时失败 | 保持同一 `event_id` 重试 |

## 4. AI 系统状态规则

- `platform_accepted`：聚合平台 HTTP 接口已受理，仍不是发送成功。
- `submission_unknown`：调用聚合平台时读取超时，不确认是否已受理，等待回调。
- `send_succeeded`：收到实际成功回调后，才记录客户可见消息、素材发送证据和任务成功。
- `send_failed`：收到失败回调后，任务进入失败状态。
- `partial_failed`：批次部分成功，保存逐条结果，不把整个任务当成功。

回执记录与当前 AI 存储后端一致，聚合平台不依赖数据库类型：

SQLite 逻辑表：

```text
message_dispatches
message_dispatch_items
message_delivery_events
```

切换到 MySQL 后对应隔离表：

```text
aics_message_dispatches
aics_message_dispatch_items
aics_message_delivery_events
```

AI 管理查询接口：

```text
GET /admin/message-deliveries/{dispatch_id}
Authorization: Bearer <AI_PATHS_API_KEY>
```

## 5. 双方联调与启用顺序

1. AI 服务先部署新版本并创建回执表，但保持：

   ```env
   MESSAGE_DELIVERY_CALLBACK_REQUIRED=false
   MESSAGE_DELIVERY_CALLBACK_PUBLIC_URL=
   MESSAGE_DELIVERY_CALLBACK_TOKEN=
   ```

   URL 或 Token 未完整配置时，AI 服务不会向旧发送请求增加回执字段，此阶段对现有发送协议完全兼容。

2. 双方配置同一个回调 Token，并为聚合平台开放 HTTPS 回调地址。

   先保持 `MESSAGE_DELIVERY_CALLBACK_REQUIRED=false` 做影子联调：AI 系统记录回调结果，但仍沿用原有 HTTP 受理即完成的业务状态，不重复写客户消息、SOP 进度或素材记录。

3. 聚合平台支持新增请求字段和结果回调，使用测试客户完成：
   - 单条文字成功。
   - 文字加图片全部成功。
   - 全部失败。
   - 部分失败。
   - 同一事件重复回调。
   - 发送接口读取超时后仍能回调最终结果。

4. AI 数据库与聚合平台日志逐条核对 `dispatch_id/client_message_id`。

5. 联调通过后，AI 服务开启：

   ```env
   MESSAGE_DELIVERY_CALLBACK_REQUIRED=true
   MESSAGE_DELIVERY_CALLBACK_PUBLIC_URL=http://47.252.81.104/api/ai/callbacks/v1/message-delivery
   MESSAGE_DELIVERY_CALLBACK_TOKEN=<双方约定的高强度随机 Token>
   ```

6. 开启后监控待回执超过 10 分钟、失败率、部分失败率和回调重试量。

## 6. 聚合平台交付清单

- [ ] 解析并持久化 `dispatch_id`。
- [ ] 原样保留每条 `client_message_id`。
- [ ] 发送接口返回 `platform_request_id`。
- [ ] 从实际发送执行结果触发回调。
- [ ] 支持成功、失败、部分失败和可选发送中状态。
- [ ] 使用 `X-Callback-Token`。
- [ ] 使用稳定、全局唯一且重试不变的 `event_id`。
- [ ] 对非 `2xx` 回调执行持久化重试。
- [ ] 保留 `dispatch_id/client_message_id/platform_message_id` 的排查日志。
- [ ] 完成六类联调场景后再通知 AI 服务开启强制回执模式。
