# 外部依赖接口

本页记录 AI Paths 主动调用的外部接口。所有 token 只允许来自运行环境变量或服务器本地 `.env`，不得写入 Git。

- code_verified: 2026-09-10 Asia/Shanghai, `main@0e7f76e5`

## Follow Knowledge：跟进序列与卡点话术

- 代码客户端：`ai_paths/app/services/follow_knowledge_client.py`
- 配置：
  - `FOLLOW_KNOWLEDGE_ENABLED`
  - `FOLLOW_KNOWLEDGE_BASE_URL`
  - `FOLLOW_KNOWLEDGE_TOKEN`
  - `FOLLOW_KNOWLEDGE_TIMEOUT_SECONDS`
  - `FOLLOW_KNOWLEDGE_CACHE_TTL_SECONDS`
  - `AI_CLOSING_CATALOG_SOURCE=external|local|external_then_local`
  - `AI_CLOSING_CATALOG_LOCAL_PATH`
- 鉴权 header：`x-event-token`
- 读写性质：只读查询。
- 接口：
  - `POST /event/trigger/follow-sequence`：查询已发布跟进序列。
  - `POST /event/trigger/follow-script`：查询已发布卡点话术。
  - `POST /event/follow/closing-rule`：查询租户启用的逼单触发规则、AI 确认要求、频次/间隔、前置项与禁忌。
  - `POST /event/follow/closing-sequence`：查询租户启用的逼单策略与节点；节点的 `followCheckpointTypeId` 用于联查 `follow-script` 话术类型。
- 动作码由平台目录负责定义和命名。AI Paths 不维护第二份 `act001/act002/...` 业务含义表；客户端接受本地规范动作和格式合法的 `actNNN`，展示名称直接使用同轮目录返回值。新增平台动作不得因本地有限枚举而被静默丢弃。
- 运行边界：
  - 该接口只提供 V3 Reply 的参考候选，不替代最终销售语义决策。
  - 话术不是价格、门店、支付、活动、名额、履约或安全事实的权威来源。
  - 普通跟进序列和卡点话术在未配置 token 时仍安全降级为 `follow_knowledge_not_configured`，不能伪造候选。
  - 逼单目录在业务接口未就绪期间可显式使用版本化本地 JSON。`external_then_local` 模式下，外部规则与策略正常且非空时优先外部；接口未配置、异常或成功返回空目录时使用本地目录。`local` 模式完全不请求两个逼单接口，适合联调和隔离评测。
  - 本地目录来源固定标记为 `local_closing_catalog`，使用独立稳定 key 和 checksum，不伪装成平台数字 ID；本地话术仍不是门店、预约、价格或交易事实来源。
  - 本地临时逼单目录必须是仓库内版本化 JSON，来源、稳定 key 和 checksum 可审计。具体版本与条数属于动态开发状态，不固化在接口合同；金额、有效期、退款、门店、交通、档期和支付入口仍必须由本轮权威事实支持。
  - 业务将同批内容导入第三方后无需发布新代码：保持 `AI_CLOSING_CATALOG_SOURCE=external_then_local`，两个外部逼单接口同时返回正常且非空目录时优先外部；切换后必须核对来源、checksum、稳定 ID 和节点话术类型关联。
  - `combined` 规则在上游未提供组合分组及 AND/OR 关系前只记录、不可执行。
  - 普通跟进序列、taxonomy/话术、逼单规则和逼单策略是 V3 Reply 的唯一在线业务知识来源；本地同步目录不参与线上降级。
  - 候选节点话术按真实 `followCheckpointTypeId` 加入现有话术检索批次；返回话术类型不一致时丢弃。纯逼单话术候选直接交给最终 Reply 选择，不增加独立 selector 模型调用。
  - 普通卡点话术按当前 `checkpoint_type_id` 一次查询同类已发布内容；tag 和 action 只参与本地相关度排序、覆盖加权及审计，不再作为硬过滤条件。候选经过动作/标签多样性限制、重复去除和字符预算后最多向 Reply 提供 6 个段落，不跨卡点类型。逼单话术仍严格匹配节点 `followCheckpointTypeId`，不允许跨类型放宽。
  - 沉默唤醒另按已选跟进序列逐节点生成任务。节点话术最多保留 6 条渐进候选：先同卡点同动作，再同卡点其他动作；平台二级标签缺失时允许补充全目录语义相关候选。跨类型候选只用于回答客户当前原话，不能改变卡点或序列，最终由 DeepSeek 选择真实话术 ID，代码校验 ID、素材、安全与发送资格。
  - 进程内使用 single-flight、短失败缓存与 last-known-good；陈旧快照标记 `freshness_status=stale`。该能力不能替代跨重启的持久快照。
  - 当前部署是一实例一个知识租户、一个 `FOLLOW_KNOWLEDGE_TOKEN`；所有 `slXXXX` 是该租户下不同企微号，可以共享只含业务知识的目录缓存。客户聊天、订单、记忆、限频和策略状态不得进入共享缓存，仍按 `corp_id + wechat + external_userid` 隔离；平台客户 ID 独立用于第三方平台查询。
  - `corp_id` 不是 Follow Knowledge 租户 ID，不用于选择 token。未来出现第二个业务知识租户时采用独立服务实例和 token，再按实际部署需求评审路由方案。
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
  - 已开口沉默计划生成前必须调用 `conversation/status`；只有明确 `takeover.mode=ai`/`ai_auto_reply=true` 且未禁止 AI 主动触达时才可继续。
  - 每次真实发送前必须再次检查最新会话和 `conversation/status`；人工接管、状态未知、退订、已付、客户已回复等均阻断发送。
  - `conversation/status` 不可用或字段不足时失败关闭并延后重试，不能推断为 AI 模式。
  - shadow 策略任务只记录 `shadowed`，不得调用真实发送。

## 第三方 SOP 平台

- 权威合同：[第三方 SOP V3 合同](../contracts/third-party-sop-v3.md)
- 配置：
  - `SOP_PLATFORM_BASE_URL`
  - `SOP_PLATFORM_TOKEN`
  - `SERVICE_RULE_DATA_BASE_URL`
  - `SERVICE_RULE_DATA_TOKEN`
  - `SOP_FAILURE_ALERT_ENABLED`
  - `SOP_FAILURE_ALERT_WEBHOOK_URL`
  - `SOP_FAILURE_ALERT_SIGNING_SECRET`
  - `SOP_FAILURE_ALERT_TIMEOUT_SECONDS`
  - `SOP_FAILURE_ALERT_RETRY_BATCH_SIZE`
- 读写性质：混合；消费任务、发送、终态回传与策略数据回传均有外部状态影响。
- 运行边界：
  - SOP 任务终态必须回传策略数据。
  - 消息送达回调不能替代 SOP 消费或策略数据回传。
  - 内容缺失、无效及平台明确拒绝等失败终态需要预警；客户关系已删除、客户已开口和人工接管属于无预警业务终态。发送接口已调用但结果未知按幂等合同停止重发并标记待确认，不得误报成明确发送失败。预警失败不改变任务状态。

### 钉钉自定义机器人：SOP 失败预警

- 代码客户端：`ai_paths/app/services/dingtalk_robot_client.py`
- 读写性质：发送群机器人 Markdown 消息。
- 鉴权：Webhook access token 与加签 secret 仅来自服务器环境变量；签名参数为毫秒时间戳和 HMAC-SHA256 结果。
- 运行边界：不得记录或回传 Webhook、access token、加签 secret；告警内容不得包含客户消息正文和完整外部联系人标识。

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
  - 语义路由模型每轮只调用一次，只产生卡点、工具需求和检索条件，不生成客户话术。
  - 门店工具补齐事实后不再调用第二次销售语义模型；最终 Reply 直接消费真实门店事实。
  - V3 Reply 模型是唯一销售语义决策点。
  - Reply 角色使用独立进程环境覆盖全局模型默认值；最终 Reply、full retry 和 targeted repair 必须使用同一 Reply tier。具体生产模型和 fallback 是动态运行事实，以角色环境、生产状态和 run trace 为准，不能从其他角色的全局默认值推断。

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
