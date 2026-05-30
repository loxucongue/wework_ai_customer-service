# AI客服公网接口对接文档

## 1. 当前可用地址

轻量服务器测试环境：

```http
GET  http://47.252.81.104/api/ai-paths/health
POST http://47.252.81.104/api/ai-paths/chat
```

说明：

- `/health` 用于健康检查，不需要请求体。
- `/chat` 用于生成“小贝”的企微客服回复。
- 当前为 HTTP 公网 IP。正式接入公司系统前，建议绑定域名并配置 HTTPS。

## 2. 鉴权方式

`/api/ai-paths/chat` 必须带服务密钥：

```http
Authorization: Bearer <AI_PATHS_API_KEY>
Content-Type: application/json; charset=utf-8
```

注意：

- `AI_PATHS_API_KEY` 存在服务器 `/opt/ai-paths/.env`。
- 不要把密钥写死在前端浏览器代码里。
- 如果由公司后端调用，建议由后端保存密钥并转发请求。

## 3. 请求体

```json
{
  "content": "客户当前消息",
  "customer_id": "客户系统ID或会话ID",
  "corp_id": "企微企业ID",
  "user_id": 7294,
  "wechat": "员工企微账号",
  "external_userid": "企微外部联系人ID",
  "customer_add_wechat_id": "客户添加企微关系ID",
  "conversation_history": [
    "用户: 你好，我脸上有斑",
    "小贝: 可以发张自然光照片，我帮你先看方向"
  ],
  "file_image": "图片公网URL，可选",
  "confirmed_store_id": "已确认门店ID，可选",
  "confirmed_store_name": "已确认门店名称，可选",
  "store_id": "本轮指定门店ID，可选",
  "store_name": "本轮指定门店名称，可选",
  "appointment_id": "已知预约ID，可选",
  "appointment_time": "已知预约时间，可选",
  "request_context": {
    "source": "company_chat_system"
  }
}
```

## 4. 字段说明

| 字段 | 必填 | 类型 | 说明 |
|---|---:|---|---|
| `content` | 是 | string | 客户当前输入文本。图片消息可传 `[图片]`，同时传 `file_image`。 |
| `customer_id` | 是 | string | 客户系统 ID 或对话 ID。用于本地记忆、画像、事件沉淀，也用于客户系统查询兜底。 |
| `corp_id` | 是 | string | 企微企业 ID。会透传给客户系统接口。 |
| `user_id` | 是 | number/string | 当前承接员工 ID。会透传给客户系统接口。 |
| `wechat` | 是 | string | 当前承接员工企微账号。会透传给客户系统接口。 |
| `external_userid` | 推荐 | string | 企微外部联系人 ID。用于查询客户系统中的客户信息。 |
| `customer_add_wechat_id` | 推荐 | string/number | 客户添加企微关系 ID。用于门店列表等客户相关接口。 |
| `conversation_history` | 否 | string[] | 最近 10 条左右对话，建议由调用方传入，帮助多轮承接。 |
| `file_image` | 否 | string | 客户上传图片的公网可访问 URL。 |
| `confirmed_store_id` | 否 | string/number | 多轮中已确认的门店 ID，优先用于预约时间查询。 |
| `confirmed_store_name` | 否 | string | 多轮中已确认的门店名称。 |
| `store_id` | 否 | string/number | 本轮指定门店 ID。 |
| `store_name` | 否 | string | 本轮指定门店名称。 |
| `appointment_id` | 否 | string/number | 调用方已知的当前预约 ID。 |
| `appointment_time` | 否 | string | 调用方已知的当前预约时间。 |
| `request_context` | 否 | object | 调用方自定义上下文，会被合并进内部请求上下文。 |

## 5. 响应体

```json
{
  "request_id": "ef6cefa9-e6ac-4b6c-a918-daa0fa0b6141",
  "reply_messages": [
    {
      "type": "text",
      "order": 1,
      "content": "你好呀～我是小贝！上海目前有3家门店..."
    }
  ],
  "scene": "S3_deep_consult",
  "intent": "store_inquiry",
  "subflow": "SF6_store_match",
  "trace_url": "logs/runs/xxx.json",
  "meta": {
    "intents": [],
    "tool_result_keys": [],
    "customer_context": {},
    "total_tokens": 0,
    "duration_ms": 0
  }
}
```

字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `request_id` | string | 本轮请求 ID，可用于查日志。 |
| `reply_messages` | array | 需要发送给客户的消息数组。当前支持 `text` 和 `image`。 |
| `scene` | string | 主场景。 |
| `intent` | string | 主意图。 |
| `subflow` | string | 主技能/子流程映射。 |
| `trace_url` | string | 本地日志相对路径。 |
| `meta` | object | 调试信息。生产侧可不展示给客户。 |

## 6. 系统数据接口入参来源检查

当前代码中，客户系统数据查询已经改为变量入参，不再依赖 Coze 测试时的固定客户。

| 内部能力 | 实际调用接口 | 关键入参来源 |
|---|---|---|
| 查询客户信息 | `GET /platform_agent/customer/get_customer_info` | `user_id`、`corp_id`、`wechat`、`external_userid` 全部来自 `/chat` 请求。 |
| 查询客户订单/预约上下文 | `GET /platform_agent/order/index` | `customer_id` 优先来自客户信息接口返回的客户 ID；如果没有，则使用 `/chat.customer_id`。`user_id/corp_id/wechat` 来自请求上下文。 |
| 查询客户相关门店列表 | `GET /platform_agent/store/index` | `customer_id` 来自客户信息或 `/chat.customer_id`；`customer_add_wechat_id` 来自客户信息或 `/chat.customer_add_wechat_id`。 |
| 查询全部门店选项 | `GET /platform_agent/option?option=store` | 当缺少客户相关门店入参时兜底使用；`user_id/corp_id/wechat` 来自请求上下文。 |
| 查询门店详情 | `GET /platform_agent/store/info` | `id` 来自门店匹配结果、`store_id` 或 `confirmed_store_id`。 |
| 查询可预约时间 | `GET /platform_agent/order/schedule/available_time` | `store_id` 来自 `confirmed_store_id`、`store_id` 或门店匹配结果；`date` 来自客户当前消息时间解析；`user_id/corp_id/wechat` 来自请求上下文。 |
| 项目价格知识库 | Coze 统一知识库检索工作流 | `kb_name=project_price`，`query` 使用项目名称或客户当前价格问题提取结果。 |
| 项目价格数据库 | Coze 价格数据库工作流 | SQL 由项目名称生成，不使用固定客户 ID。 |

## 7. 关于默认 ID

当前后端代码已取消内置测试默认值：

- 不再内置 `user_id=7294`
- 不再内置 `corp_id=ww916da62a08044243`
- 不再内置 `wechat=yzm-yibingwen`

如果调用方没有传这些字段：

- 客户系统接口可能无法稳定查询。
- 后端只会在服务器环境变量显式配置了 `PLATFORM_AGENT_DEFAULT_USER_ID / PLATFORM_AGENT_DEFAULT_CORP_ID / PLATFORM_AGENT_DEFAULT_WECHAT` 时才使用兜底值。
- 生产接入建议每次请求都传真实值。

前端测试代理也已取消代码里的固定默认值。若本地测试需要默认员工信息，可在前端 `.env` 中显式配置：

```env
DEFAULT_USER_ID=7294
DEFAULT_CORP_ID=ww916da62a08044243
DEFAULT_WECHAT=yzm-yibingwen
```

## 8. 调用示例

```bash
curl -X POST "http://47.252.81.104/api/ai-paths/chat" \
  -H "Authorization: Bearer <AI_PATHS_API_KEY>" \
  -H "Content-Type: application/json; charset=utf-8" \
  -d '{
    "content": "你好，我脸上有斑，还有上海有门店吗？",
    "customer_id": "customer_123",
    "corp_id": "ww916da62a08044243",
    "user_id": 7294,
    "wechat": "yzm-yibingwen",
    "external_userid": "wmeERVIgAAmeVlaJ_YvK0exNEUMwPxTw",
    "customer_add_wechat_id": 11796210,
    "conversation_history": []
  }'
```

## 9. 本次公网测试结果

测试文件：

```text
logs/public_api_lifecycle_test_20260529_002.json
```

测试覆盖：

| 轮次 | 客户消息 | 命中能力 | 结果 |
|---:|---|---|---|
| 1 | 你好，我脸上有斑，还有上海有门店吗？ | 门店查询 + 项目咨询 | 成功查询上海门店并回答斑点改善方向。 |
| 2 | 我在浦东，想知道光子嫩肤大概多少钱，也想看看适不适合我 | 价格咨询 + 项目咨询 | 成功查询 `project_price`、价格数据库和项目知识库。 |
| 3 | 别家说光子一次就能明显淡掉，还比你们便宜，你们靠谱吗？ | 信任建立 + 竞品应对 | 成功调用 `trust_assets` 和 `competitor_qa`。 |
| 4 | 那上海这边周六下午能约吗？ | 门店匹配 + 可约时间 | 成功查询上海浦东二店可约时间，同时提醒已有预约记录。 |
| 5 | 我之前是不是已经有预约了？如果有就告诉我是哪家店几点 | 预约/订单上下文 | 成功读取客户上下文并回答已有预约信息。 |

质量备注：

- 公网接口、鉴权、中文 UTF-8、系统数据查询均已打通。
- 目前 `meta.total_tokens` 仍可能为空，原因是部分模型/工具调用没有统一回填 token 统计；这属于后续日志统计优化项。
- 第 4 轮已有预约提醒是有价值的，但后续仍需控制提醒频率，避免多轮重复打扰。
