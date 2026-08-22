# 聚合平台人工/AI状态切换事件转发接入说明

## 1. 功能边界

聚合平台监控到同一客户会话从 AI 托管切换为人工接管，或从人工恢复为 AI 托管后，调用 AI 服务提供的事件入口。AI 服务只做鉴权、字段校验和同步转发，再调用聚合平台提供的策略回写接口。

AI 服务不保存当前人工/AI状态，不修改客户画像、SOP进度或回复链路，也不根据聊天内容推断接管状态。

## 2. 聚合平台调用 AI 服务

接口：

```text
POST http://47.252.81.104/api/ai/callbacks/v1/conversation-mode
Content-Type: application/json; charset=utf-8
X-Callback-Token: <双方约定的回调 Token>
```

当前入口只允许聚合平台出口 IP `120.26.43.96`、`121.199.0.182`。如果实际回调使用其他出口 IP，需要在联调前提供。

请求示例：

```json
{
  "event_id": "mode-event-20260822-0001",
  "event_type": "conversation_mode_changed",
  "occurred_at": "2026-08-22T16:30:00+08:00",
  "sequence": 1024,
  "corp_id": "ww943af61cd5d2afe4",
  "wechat": "DY258",
  "external_userid": "wmanzqsqaatm5tcgp35grtrye55g8i5g",
  "customer_id": "22099221",
  "conversation_id": "ww:dy258:wmanzqsqaatm5tcgp35grtrye55g8i5g",
  "from_mode": "ai",
  "to_mode": "human",
  "reason_code": "manual_takeover",
  "operator_id": "123213123",
  "operator_name": "luo"
}
```

字段说明：

| 字段 | 必填 | 说明 |
|---|---:|---|
| `event_id` | 是 | 全局唯一事件 ID；同一事件重试时必须保持不变 |
| `event_type` | 是 | 固定为 `conversation_mode_changed` |
| `occurred_at` | 是 | 状态实际切换时间，ISO 8601 格式并携带时区 |
| `sequence` | 否 | 聚合平台会话事件序号；有条件时建议提供 |
| `corp_id` | 是 | 企业微信 CorpID |
| `wechat` | 是 | 当前接待企微账号，区分大小写 |
| `external_userid` | 是 | 企业微信外部联系人 ID |
| `customer_id` | 是 | 聚合平台客户 ID |
| `conversation_id` | 否 | 聚合平台会话 ID |
| `from_mode` | 是 | 原状态，只允许 `ai` 或 `human` |
| `to_mode` | 是 | 新状态，只允许 `ai` 或 `human`，且必须与原状态不同 |
| `reason_code` | 否 | 切换原因，例如 `manual_takeover`、`manual_release` |
| `operator_id` | 否 | 操作人员 ID |
| `operator_name` | 否 | 操作人员名称 |

AI 服务会把通过校验的 JSON 原样转发给策略回写接口，不增加业务判断。

## 3. 策略回写接口要求

聚合平台需要向 AI 服务提供策略回写接口的完整 URL 和 `X-Agent-Token`。AI 服务调用方式：

```text
POST <CONVERSATION_MODE_WRITEBACK_URL>
Content-Type: application/json; charset=utf-8
X-Agent-Token: <CONVERSATION_MODE_WRITEBACK_TOKEN>
X-Idempotency-Key: <event_id>
```

请求正文与第 2 节的状态切换事件一致。策略回写接口必须按 `event_id` 幂等，避免“策略已写入但响应丢失”后发生重复事件。

建议成功响应：

```json
{
  "code": 0,
  "msg": "ok",
  "data": {
    "event_id": "mode-event-20260822-0001",
    "changed": true
  }
}
```

AI 服务将 HTTP `2xx` 且响应 `code` 为 `0`、`"0"` 或缺省视为成功。HTTP 错误或非零 `code` 均视为策略回写失败。

## 4. AI 服务返回

转发成功：

```json
{
  "code": 0,
  "msg": "ok",
  "data": {
    "event_id": "mode-event-20260822-0001",
    "forwarded": true,
    "writeback_http_status": 200,
    "writeback_response": {
      "code": 0,
      "msg": "ok"
    }
  }
}
```

错误处理：

| HTTP 状态 | 含义 | 聚合平台动作 |
|---:|---|---|
| `401` | 回调 Token 错误 | 停止重试并检查配置 |
| `422` | 请求字段或状态切换不合法 | 修正数据后重试 |
| `502` | 策略回写接口拒绝或网络错误 | 使用原 `event_id` 重试 |
| `503` | AI 服务尚未配置策略回写接口 | 暂停发送并通知双方配置 |
| `504` | 策略回写接口超时 | 使用原 `event_id` 重试 |

AI 服务不保存事件，因此聚合平台负责持久化失败重试。同一事件重试时必须复用原 `event_id`，不得生成新 ID。

## 5. AI 服务配置

```env
CONVERSATION_MODE_CALLBACK_TOKEN=<聚合平台调用 AI 服务使用的 Token>
CONVERSATION_MODE_WRITEBACK_URL=<聚合平台策略回写完整 URL>
CONVERSATION_MODE_WRITEBACK_TOKEN=<AI 服务调用策略接口使用的 X-Agent-Token>
CONVERSATION_MODE_WRITEBACK_TIMEOUT_SECONDS=10
```

如果 `CONVERSATION_MODE_CALLBACK_TOKEN` 为空，入口临时回退使用 `MESSAGE_DELIVERY_CALLBACK_TOKEN`。如果 `CONVERSATION_MODE_WRITEBACK_TOKEN` 为空，临时回退使用 `OUTREACH_SYSTEM_TOKEN`。正式环境建议使用独立 Token。

## 6. 联调清单

- [ ] AI 切人工事件转发成功。
- [ ] 人工切 AI 事件转发成功。
- [ ] 中文操作人和原因编码正确。
- [ ] 缺少必填字段返回 `422`。
- [ ] `from_mode` 与 `to_mode` 相同返回 `422`。
- [ ] 错误 Token 返回 `401`。
- [ ] 策略接口超时后聚合平台复用原 `event_id` 重试。
- [ ] 重复 `event_id` 不在策略系统生成重复事件。
- [ ] 确认实际回调出口 IP 已加入白名单。
