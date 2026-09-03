# 外部依赖接口

本页记录 AI Paths 主动调用的外部接口。所有 token 只允许来自运行环境变量或服务器本地 `.env`，不得写入 Git。

## Follow Knowledge：跟进序列与卡点话术

- 代码客户端：`ai_paths/app/services/follow_knowledge_client.py`
- 配置：
  - `FOLLOW_KNOWLEDGE_ENABLED`
  - `FOLLOW_KNOWLEDGE_BASE_URL`
  - `FOLLOW_KNOWLEDGE_TOKEN`
  - `FOLLOW_KNOWLEDGE_TIMEOUT_SECONDS`
  - `FOLLOW_KNOWLEDGE_CACHE_TTL_SECONDS`
- 鉴权 header：`x-event-token`
- 读写性质：只读查询。
- 接口：
  - `POST /event/trigger/follow-sequence`：查询已发布跟进序列。
  - `POST /event/trigger/follow-script`：查询已发布卡点话术。
  - `POST /event/follow/closing-rule`：查询租户启用的逼单触发规则、AI 确认要求、频次/间隔、前置项与禁忌。
  - `POST /event/follow/closing-sequence`：查询租户启用的逼单策略与节点；节点的 `followCheckpointTypeId` 用于联查 `follow-script` 话术类型。
- 已观察到的动作码：
  - `act001` 效果案例
  - `act002` 活动邀约
  - `act003` 需求唤起
  - `act004` 信任背书
  - `act005` 解决疑虑
  - `act006` 价值补充
  - `act007` 到店指引
  - `act008` 预约确认
  - `act009` 适用性判断
  - `act010` 项目说明
  - `act011` 需求挖掘
  - `act012` 关怀回访
  - `act013` 共情引导
  - `act014` 稀缺促单
  - `act015` 低门槛邀请
  - `act016` 预期管理
  - `act017` 项目说明 S10N
  - `act018` 项目说明 眼袋 m10
- 运行边界：
  - 该接口只提供 V3 Reply 的参考候选，不替代最终销售语义决策。
  - 话术不是价格、门店、支付、活动、名额、履约或安全事实的权威来源。
  - 未配置 token 时必须安全降级为 `follow_knowledge_not_configured`，不能伪造候选。
  - 逼单规则与策略并行读取并生成本地 checksum；成功空规则表示租户未配置，禁止回退本地演示策略。
  - `combined` 规则在上游未提供组合分组及 AND/OR 关系前只记录、不可执行。
  - 候选节点话术按真实 `followCheckpointTypeId` 加入现有话术检索批次；返回话术类型不一致时丢弃。纯逼单话术候选直接交给最终 Reply 选择，不增加独立 selector 模型调用。
  - 进程内使用 single-flight、短失败缓存与 last-known-good；陈旧快照标记 `freshness_status=stale`。该能力不能替代跨重启的持久快照。
  - 当前配置仍是一实例一个 `FOLLOW_KNOWLEDGE_TOKEN`。多租户共实例部署前必须提供 corp/tenant 到 token 的显式绑定和按租户隔离的缓存键，不能共享默认 token。
  - 当前两个逼单接口没有共同 `publishVersion`，数字 ID 也没有不可复用保证；跨接口 checksum 只能审计本次组合，不能证明上游原子发布。上游应补 `tenantKey`、共同版本、稳定 code、标准 timing、组合分组及 taboo 类型。
- 本地同步：
  - 脚本：`python ai_paths/scripts/sync_follow_knowledge_cache.py --env-file <server-or-local-env>`
  - 输出：`artifacts/follow_knowledge_cache/`
  - 产物：`latest_sequences.json`、`latest_scripts.json`、`latest_taxonomy.json`、`latest_raw_api.json`、`latest_manifest.json`
  - 说明：`latest_raw_api.json` 保留接口原始 sequence steps 和 script 字段，用于发现本地归一化与真实接口合同不一致的问题。
  - 约束：同步产物属于本地运行产物，不进入 Git；manifest 不包含 token。

## Platform Agent：客户、订单、门店和支付工具

- 代码客户端：`ai_paths/app/services/platform_agent_client.py`
- 配置：
  - `PLATFORM_AGENT_BASE_URL`
  - `PLATFORM_AGENT_TOKEN`
  - `PLATFORM_AGENT_REQUEST_FROM`
  - `PLATFORM_AGENT_TIMEOUT_SECONDS`
  - `V3_STRATEGY_ANALYTICS_OUTCOME_MAX_CONCURRENCY`
  - `V3_STRATEGY_ANALYTICS_OUTCOME_TIMEOUT_SECONDS`
  - `V3_STRATEGY_ANALYTICS_OUTCOME_MAX_RETRIES`
  - `V3_STRATEGY_ANALYTICS_OUTCOME_RETRY_BASE_SECONDS`
- 鉴权 header：
  - `token`
  - `Request-From`
- 读写性质：混合；读取客户、订单、门店信息，也包含创建/修改工单、预约、取消、收款等写操作。
- 主要接口：
  - `GET /platform_agent/customer/get_customer_info`
  - `GET /platform_agent/order/index`
  - `GET /platform_agent/store/index`
  - `GET /platform_agent/option`
  - `GET /platform_agent/store/info`
  - `GET /platform_agent/order/schedule/available_time`
  - `GET /platform_agent/order/check_customer`
  - `GET /platform_agent/category/get_prepay`
  - `GET /platform_agent/union/my_collection`
  - `POST /platform_agent/pay/prepay`
  - `POST /platform_agent/order/create_work`
  - `POST /platform_agent/order/modify`
  - `POST /platform_agent/order/schedule/order_plan`
  - `POST /platform_agent/order/schedule/change_plan_time`
  - `POST /platform_agent/order/schedule/cancel_plan`
  - `POST /platform_agent/customer/add_mobile`
- 运行边界：
  - 写接口只能由 V3 Reply 明确授权后的工具链调用，并受事实、schema、幂等和安全校验约束。
  - 普通销售意图不得由 Python 关键词规则决定是否调用写接口。
  - V3 策略 outcome worker 只允许调用 `GET /platform_agent/order/index`；按销售接触边界并发查询并缓存原始订单，再按每条事件的基线订单和送达锚点筛选。并发、单次超时、最大重试和指数退避均受独立配置限制；查询失败、基线不足或状态未知时不得记为未成交。

## Outreach System / Send：聊天记录与主动触达

- 代码客户端：
  - `ai_paths/app/services/outreach_system_client.py`
  - `ai_paths/app/services/outreach_send_client.py`
- 配置：
  - `OUTREACH_SYSTEM_BASE_URL`
  - `OUTREACH_SYSTEM_TOKEN`
  - `OUTREACH_SEND_BASE_URL`
  - `OUTREACH_SEND_AGENT_TOKEN`
- 鉴权 header：
  - `X-Agent-Token`
- 读写性质：混合；聊天记录和状态为只读，发送为外部副作用。
- 主要接口：
  - `GET /api/v1/platform-agent/ai-outreach/conversation`
  - `GET /api/v1/platform-agent/ai-outreach/conversation/status`
  - `POST /api/v1/platform-agent/ai-outreach/send`
- 运行边界：
  - 真实发送必须经过发送前最新会话检查、退订/人工/已付/客户已回复等阻断。
  - shadow 策略任务只记录 `shadowed`，不得调用真实发送。

## 第三方 SOP 平台

- 合同文档：`docs/contracts/third-party-sop-v3.md`
- 配置：
  - `SOP_PLATFORM_BASE_URL`
  - `SOP_PLATFORM_TOKEN`
  - `SERVICE_RULE_DATA_BASE_URL`
  - `SERVICE_RULE_DATA_TOKEN`
- 读写性质：混合；消费任务、发送、终态回传与策略数据回传均有外部状态影响。
- 运行边界：
  - SOP 任务终态必须回传策略数据。
  - 消息送达回调不能替代 SOP 消费或策略数据回传。

## 模型供应商

- 代码客户端：
  - `ai_paths/app/services/model_client.py`
  - `ai_paths/app/services/deepseek_semantic_client.py`
- 配置示例：
  - `DEEPSEEK_API_KEY`
  - `DEEPSEEK_API_BASE_URL`
  - `DEEPSEEK_SEMANTIC_MODEL`
  - `MODEL_RELAY_API_KEY`
  - `ALIYUN_DASHSCOPE_API_KEY`
  - `VOLCENGINE_ARK_API_KEY`
  - `CLAUDE_RELAY_API_KEY`
- 读写性质：模型推理调用；不得向模型提交超过业务需要的客户数据。
- 运行边界：
  - 语义路由模型只产生证据和候选，不生成客户话术。
  - V3 Reply 模型是唯一销售语义决策点。

## Coze / 门店快照

- 代码客户端：
  - `ai_paths/app/services/coze_client.py`
  - `ai_paths/app/services/store_snapshot_service.py`
- 配置：
  - `COZE_API_BASE`
  - `COZE_OAUTH_CLIENT_ID`
  - `COZE_OAUTH_PUBLIC_KEY_ID`
  - `COZE_OAUTH_PRIVATE_KEY_FILE`
- 读写性质：工作流/知识库调用和本地快照刷新。
- 运行边界：
  - 本地快照不是生产动态事实；生产发布或验证前必须现场核验。
