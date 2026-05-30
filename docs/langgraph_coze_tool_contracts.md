# LangGraph 迁移 - Coze 工具层接口约定

本文档记录从 Coze 工作流迁移到本地 FastAPI + LangGraph 后，仍暂时保留在 Coze 内执行的工具型工作流接口。

## 1. 统一知识库查询工作流

- Workflow ID: `7644575365759746083`
- 用途: 根据知识库枚举名和检索 query，在 Coze 内路由到对应知识库并返回检索切片。
- top_k: Coze 工作流内固定为 5，只能手动修改。LangGraph 侧第一版不依赖动态 top_k。

### 请求

```json
{
  "workflow_id": "7644575365759746083",
  "parameters": {
    "kb_name": "after_sales_qa",
    "query": "皮秒 祛斑后 结痂 不能抠痂 护理建议"
  }
}
```

### 响应

```json
{
  "outputList": [
    {
      "output": "知识库切片文本",
      "documentId": "7641972472527716393"
    }
  ]
}
```

LangGraph 侧统一封装为:

```json
{
  "kb_name": "after_sales_qa",
  "items": [
    {
      "content": "知识库切片文本",
      "document_id": "7641972472527716393"
    }
  ]
}
```

### 第一版知识库枚举

| kb_name | 用途 | 调用场景 |
|---|---|---|
| `project_price` | 项目价格知识库/价格补充说明 | 价格咨询兜底、价格文档检索 |
| `project_qa` | 项目原理、适合人群、恢复期、常见问题 | 项目咨询、图片面诊后解释 |
| `competitor_qa` | 竞品话术、比价、别家方案、低价应对 | 竞品应对 |
| `trust_assets` | 资质、授权、品牌背书、溯源、图片资料 | 信任建立、竞品应对需要背书时 |
| `after_sales_qa` | 术后护理、恢复期、反黑、结痂、红肿等 | 售后服务 |

### `project_price` 检索与解析规则

- LangGraph 调用 `project_price` 时，`query` 只传检测到的项目名称，例如 `热玛吉`、`水光`、`皮秒`。
- `project_price` 的职责是做项目名/别名的模糊匹配，不要求 LangGraph 额外拼接 `价格`、`报价`、`当前配置` 等词。
- 价格结果优先级:
  1. `project_price` 返回的完整价格切片，且能解析出明确价格字段。
  2. 价格数据库 CRUD 工作流返回的结构化行。
  3. 本地 Excel 价格表，仅作为开发兜底。
  4. `project_price` 只命中项目/备注但没有明确价格字段时，只承接“看到相关配置”，不报数字。
  5. 全部未命中时，回复不编造价格，并提示继续按项目配置确认。

为了保证第 1 项可用，价格知识库每个可检索切片应尽量包含一条完整价格记录，至少保留:

```text
项目名称：热玛吉
日常单次价：12800
新客体验价：6800
老客单次价：9800
老客推荐卡项：26800年卡
活动价：8800
活动适用人群：老客回馈
可赠送福利：修复套盒1套
福利触发场景：消费满5000
状态：true
报价备注：FLX第五代面部
```

如果 Coze 切片只返回 `报价备注`、`可赠送福利` 等记录后半段，而没有价格字段，LangGraph 不会从其他无关项目中代替报价。

### 暂不单独建库的能力

| 能力 | 第一版处理方式 |
|---|---|
| 活动知识库 | 优先查价格数据库中的活动字段 |
| 到店前常见问题 | 写入 LangGraph 回复节点规则 |
| 报价规则说明 | 写入价格参数提取和价格回复节点规则 |
| 图片面诊皮肤问题知识库 | 先用图片理解结果 + `project_qa` |

## 4. 模型供应商密钥约定

API Key 不写入仓库文件。FastAPI/LangGraph 服务统一从环境变量读取:

| 环境变量 | 用途 |
|---|---|
| `ALIYUN_DASHSCOPE_API_KEY` | 阿里云百炼/Qwen 系列模型调用 |
| `VOLCENGINE_ARK_API_KEY` | 火山引擎 Ark/豆包系列模型调用 |
| `COZE_OAUTH_CLIENT_ID` | Coze OAuth JWT 应用 appid/client_id |
| `COZE_OAUTH_PUBLIC_KEY_ID` | Coze OAuth JWT 公钥 ID |
| `COZE_OAUTH_PRIVATE_KEY_FILE` | Coze OAuth JWT 私钥 PEM 文件路径 |
| `COZE_OAUTH_TOKEN_TTL` | Coze access token 有效期，默认 7200 秒 |

本地开发时使用 `.env` 或系统环境变量注入；部署时使用服务器环境变量或密钥管理服务注入。

## 2. 项目价格数据库 CRUD 工作流

- Workflow ID: `7641872030061117450`
- 用途: 直接作用于 `items_pricing_system` 表，执行项目价格数据增删改查。

### 请求

```json
{
  "workflow_id": "7641872030061117450",
  "parameters": {
    "input": "SELECT * FROM items_pricing_system ORDER BY id"
  }
}
```

### 响应

```json
{
  "code": 0,
  "msg": "",
  "data": "{\"output\":[{\"id\":\"7641891399033471762\",\"project_name\":\"热玛吉\"}]}",
  "execute_id": "7644107677061578761"
}
```

`data` 是 JSON 字符串，需二次解析。查询结果位于 `data.output`。

## 3. 项目价格知识库同步工作流

- Workflow ID: `7644090458134609974`
- 用途: 将项目价格 Word 文档同步写入“项目价格知识库”。
- 注意: Coze 调用统一使用 OAuth JWT 获取 access token，不再使用 SAT/PAT。

### 请求

```json
{
  "workflow_id": "7644090458134609974",
  "parameters": {
    "documentID": "7644124216548278278",
    "documentfile": "https://example.com/pricing.docx?sign=..."
  }
}
```

### 响应

```json
{
  "code": 0,
  "msg": "",
  "data": "{\"output\":\"7644121594772570153\"}",
  "execute_id": "7644121594772570153"
}
```

`data.output` 为新写入文档的 `documentId`。

## Platform Agent Customer/Store/Appointment APIs

These APIs are temporary adapters for the local LangGraph service until the customer system provides dedicated AI-service endpoints.

Environment variables:

| Variable | Purpose |
|---|---|
| `PLATFORM_AGENT_BASE_URL` | Default `https://v2.henm.cn` for test environment |
| `PLATFORM_AGENT_TOKEN` | Platform-agent API token, injected only through local/server env |
| `PLATFORM_AGENT_REQUEST_FROM` | Default `platform_agent` |
| `PLATFORM_AGENT_TIMEOUT_SECONDS` | HTTP timeout |

Current wrapper: `app.services.platform_agent_client.PlatformAgentClient`.

Used APIs:

| Capability | Method + Path | Used by local service |
|---|---|---|
| Customer lookup | `GET /platform_agent/customer/get_customer_info` | Resolve `external_userid` to `customer_id` and `customer_add_wechat_id` |
| Customer orders | `GET /platform_agent/order/index` | Build per-turn appointment/order context |
| Store list | `GET /platform_agent/store/index` | Replace local hard-coded store list when customer context is available |
| Store detail | `GET /platform_agent/store/info` | Address, map URL, parking name/address/link |
| Available times | `GET /platform_agent/order/schedule/available_time` | Reserved for appointment flow; not yet wired into reply path |

Service replacement points:

- `CustomerContextService` now uses platform customer and order APIs when `PLATFORM_AGENT_TOKEN` and `external_userid` are available. It falls back to local memory when unavailable or failed.
- `StoreService` now uses platform store list/detail APIs when customer context contains `customer.id` and `customer.customer_add_wechat_id`. It falls back to local development store records otherwise.

Customer-facing code should not call these APIs directly. All calls should stay behind service wrappers so later customer-system endpoints can replace them without changing graph nodes.
