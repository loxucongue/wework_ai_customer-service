# AI Paths 服务器联调接口说明

## 目标

对话系统通过服务器公网接口调用 AI Paths，让“小贝”生成企微客服回复。

建议外部只访问 Nginx/Caddy 暴露的 HTTPS 接口，FastAPI 服务只监听服务器本机 `127.0.0.1:8000`。

推荐链路：

```text
对话系统
  -> https://<域名或公网IP>/api/chat
  -> Nginx/Caddy 反代
  -> http://127.0.0.1:8000/chat
  -> LangGraph + Coze 工具层 + 企微平台接口
```

## 接口

```http
POST /chat
Authorization: Bearer <AI_PATHS_API_KEY>
Content-Type: application/json; charset=utf-8
```

如果走前端项目代理，则路径通常是：

```http
POST /api/chat
```

## 请求体

```json
{
  "content": "客户当前消息",
  "customer_id": "客户系统ID或会话ID",
  "corp_id": "ww916da62a08044243",
  "user_id": 7294,
  "wechat": "yzm-yibingwen",
  "external_userid": "企微外部联系人ID",
  "customer_add_wechat_id": "客户添加企微关系ID，可选",
  "conversation_history": [
    "用户: 你们重庆有门店吗",
    "小贝: 重庆这边有渝北、南岸、渝中门店"
  ],
  "file_image": "图片公网URL，可选",
  "confirmed_store_id": "已确认门店ID，可选",
  "confirmed_store_name": "已确认门店名称，可选",
  "store_id": "本轮指定门店ID，可选",
  "store_name": "本轮指定门店名，可选",
  "appointment_id": "已有预约ID，可选",
  "appointment_time": "已有预约时间，可选",
  "request_context": {}
}
```

### 关键字段说明

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `content` | 条件必填 | 客户文字消息；如果只有图片，可传 `"[图片]"` |
| `file_image` | 否 | 图片公网 URL；有图片时传 |
| `customer_id` | 是 | AI 记忆和客户上下文主键；建议传客户系统 ID |
| `corp_id` | 是 | 企微企业 ID |
| `user_id` | 是 | 承接员工 ID，用于企微平台接口 |
| `wechat` | 是 | 承接员工企微账号 |
| `external_userid` | 推荐 | 企微外部联系人 ID，用于查客户信息 |
| `customer_add_wechat_id` | 推荐 | 如果对方系统已有，传入后门店/客户接口更稳定 |
| `conversation_history` | 推荐 | 最近 6-10 条文本历史 |
| `confirmed_store_id` | 否 | 多轮已确认门店时传，优先用于查可约时间 |
| `confirmed_store_name` | 否 | 已确认门店名，用于回复和门店承接 |
| `store_id` | 否 | 本轮明确指定门店时传 |
| `appointment_id` | 否 | 已有预约 ID |
| `appointment_time` | 否 | 已有预约时间 |
| `request_context` | 否 | 临时扩展字段，后续系统字段可先放这里 |

## 响应体

```json
{
  "request_id": "uuid",
  "reply_messages": [
    {
      "type": "text",
      "order": 1,
      "content": "重庆这边有渝北、南岸、渝中门店，你看哪家更方便？"
    }
  ],
  "scene": "S4_appointment_negotiating",
  "intent": "store_inquiry",
  "subflow": "SF6_store_match",
  "trace_url": "logs/runs/<request_id>.json",
  "meta": {
    "intents": [],
    "tool_result_keys": [],
    "token_usage": {}
  }
}
```

对话系统只需要使用 `reply_messages` 发给客户。`meta` 用于调试，不建议展示给客户。

## 鉴权

服务器环境变量：

```bash
AI_PATHS_API_KEY=<自定义强随机token>
```

启用后，调用方必须传：

```http
Authorization: Bearer <AI_PATHS_API_KEY>
```

如果 `AI_PATHS_API_KEY` 为空，本地开发模式不会强制鉴权。服务器联调建议必须配置。

## Nginx 反代示例

```nginx
server {
    listen 80;
    server_name <your-domain-or-ip>;

    location /api/chat {
        proxy_pass http://127.0.0.1:8000/chat;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 180s;
        proxy_send_timeout 180s;
    }

    location /health {
        proxy_pass http://127.0.0.1:8000/health;
    }
}
```

## 服务器环境变量

至少需要：

```bash
AI_PATHS_API_KEY=
ALIYUN_DASHSCOPE_API_KEY=
VOLCENGINE_ARK_API_KEY=
COZE_OAUTH_CLIENT_ID=
COZE_OAUTH_PUBLIC_KEY_ID=
COZE_OAUTH_PRIVATE_KEY_FILE=
PLATFORM_AGENT_TOKEN=
PLATFORM_AGENT_DEFAULT_USER_ID=7294
PLATFORM_AGENT_DEFAULT_CORP_ID=ww916da62a08044243
PLATFORM_AGENT_DEFAULT_WECHAT=yzm-yibingwen
```

## 迁移到公司服务器时的影响

代码层面的核心接口可以保持不变。主要变化在：

- 域名/IP 从轻量服务器换成公司服务域名。
- 环境变量重新配置。
- Coze OAuth 私钥路径改为公司服务器路径。
- 日志目录、进程管理、备份策略按公司规范调整。
- 如果公司服务器能直连客户系统，后续可逐步替换 Coze/企微平台工具包装层。

## 联调注意事项

- 请求体必须使用 UTF-8 JSON，避免中文变成 `????`。
- 调用超时建议设置 `120-180s`，当前多模型链路可能较慢。
- 每次请求返回 `request_id`，排查问题时用它查 `logs/runs/<request_id>.json`。
- 外部系统不要直接展示 `meta`、`trace_url`、调试标签。
