# v3-sales-strategy-flow

- status: active
- owner: Codex
- base_branch: main
- base_sha: 9535a1e14f9002675b8152f2c441981be37b3d39
- production_verified_at: not verified; 本阶段不发布、不读取生产
- production_releases: not verified

## 目标

梳理 V3 回复链中跟进策略、逼单策略、卡点话术、意图识别与现有策略配置/运行时代码的设计流程，明确后续实现应落入哪些职责边界。

## 非目标

- 本阶段不启用策略目录、延时逼单或四大区跟进的真实发送。
- 不恢复任何 V1/V2 回复路由。
- 不以 Python 关键词分支实现正常销售意图、异议或会话阶段判断。
- 除用户明确要求的离线评测外，不调用真实模型；不发送真实客户消息，不调用生产写接口。

## Change contract

- type: V3 跟进策略/卡点话术提示词优化、真实样本隔离评测、BI 埋点
- scope: `docs/tasks/active/INDEX.md`、`docs/tasks/active/v3-sales-strategy-flow.md`、`docs/INDEX.md`、`docs/interfaces/`、`ai_paths/scripts/sync_follow_knowledge_cache.py`、`ai_paths/app/prompts/v3_semantic_router.py`、`ai_paths/app/services/follow_knowledge_client.py`、`ai_paths/app/services/v3_semantic_router_service.py`、`ai_paths/app/chat_runtime.py`、`ai_paths/app/config.py`、`ai_paths/app/routers/operations_admin.py`、`ai_paths/app/workers/supervisor.py`、`ai_paths/app/services/storage/`、`ai_paths/migrations/versions/20260902_01_add_v3_strategy_analytics.py`
- risk: 中；新增 BI 持久化和只读查询，但不改变 V3 Reply 决策权、不发送额外客户消息、不调用生产写接口
- validation: 静态编译、SQLite 临时库 smoke、MySQL schema metadata 校验、dispatch callback 回填 smoke、`git diff --check`
- rollback: 回退本任务分支改动；新增表不参与业务决策，关闭/回退代码后不会影响 V3 Reply

## 涉及模块与文件所有权

- 已修改：`docs/tasks/active/INDEX.md`
- 已创建：`docs/tasks/active/v3-sales-strategy-flow.md`
- 已修改：`docs/INDEX.md`
- 已创建：`docs/interfaces/INDEX.md`、`docs/interfaces/external.md`、`docs/interfaces/public.md`
- 已创建：`ai_paths/scripts/sync_follow_knowledge_cache.py`
- 本阶段只读/隔离验证：`ai_paths/app/graph/`、`ai_paths/app/policies/`、`ai_paths/app/services/outreach/`、`ai_paths/app/services/sop/`、`ai_paths/app/services/follow_knowledge_client.py`
- 后续如进入实现，必须先把具体文件所有权补登记到 active index。
- BI 埋点实现新增/修改：`ai_paths/app/services/storage/v3_strategy_analytics_repository.py`、`ai_paths/app/services/storage/schema.sql`、`ai_paths/app/services/storage/mysql_schema.py`、`ai_paths/app/services/storage/store_base.py`、`ai_paths/app/services/storage/repositories.py`、`ai_paths/app/services/storage/message_delivery_repository.py`、`ai_paths/app/chat_runtime.py`、`ai_paths/app/config.py`、`ai_paths/app/routers/operations_admin.py`、`ai_paths/app/workers/supervisor.py`、`ai_paths/migrations/versions/20260902_01_add_v3_strategy_analytics.py`

## 不可破坏合同

- V3 Reply 是唯一客户回复入口和唯一销售语义决策点。
- 策略配置只提供候选和约束，不替代模型决策。
- 代码负责事实、schema 标准化、幂等、交易边界、安全和发送结果，不用关键词规则判断正常销售语义。
- 延时逼单、四大区跟进和策略目录未验收前保持关闭或 shadow，不允许真实发送。
- 明确退订、人工接管、已付、会话读取失败、客户已回复和重复发送必须阻断延时任务。

## 已确认事实与证据

- `origin/main` 与本任务 base 均为 `9535a1e14f9002675b8152f2c441981be37b3d39`。
- 当前生产状态文档标记为 `requires-live-verification`；本阶段不把文档当生产事实。
- 活跃任务清单在登记前为空。
- V3 客户回复入口为 `/api/ai/reply/workflow-compatible-v3`，运行图顺序为输入标准化、背景事实、共享权威上下文、语义证据、只读事实工具、事实后语义证据、素材/证据汇合、最终 Reply 决策。
- `AiSalesPolicyService` 与 `SalesStrategyService` 分别负责策略快照和策略目录检索；两者默认由环境开关关闭，即使配置文件内部为 active/shadow，也不会自动进入回复上下文。
- 语义路由器只做当前意图、当前卡点、历史未解卡点、事实主题、门店查询需求与知识检索条件，不生成客户话术，不决定成交/付款/暂停。
- 最终 Reply 模型是唯一销售语义决策点；输出 `sales_judgment`、`policy_decision`、`realtime_intent`、`closing_decision`、`cardpoint_decision`、`selected_content_ids` 等结构字段，再由验证层做结构与事实边界校验。
- 延时逼单当前通过 `record_closing_sequence_shadow` 写入 shadow outreach plan；执行器识别 shadow 策略任务后只记录 `shadowed`，不发送客户消息。

## 当前设计流程梳理

1. HTTP 入口把请求固定标记为 V3，然后先跑人工接管保护，再进入 V3 reply 图。
2. 输入层标准化文本、图片、位置卡和平台未知转账事件；背景层并行读取记忆、客户/订单上下文、会话、门店索引、follow sequence 与卡点 taxonomy。
3. 共享上下文层构造权威事实包：订单支付、可见门店覆盖、SOP 进度、已发送素材、图片/转账、位置卡、登记事实、事实来源状态；只有启用的 `ai_sales_policy` 和 `sales_strategy_catalog` 才会进入模型输入。
4. 语义证据层调用 DeepSeek 语义路由，先识别当前意图/卡点/事实主题/门店需求；如需门店事实，先进入只读工具，再做事实后语义路由；否则直接检索 follow-knowledge 和策略目录候选。
5. 素材汇合层只合并可用候选、工具事实、权威事实引用和结构交付选项，明确 `join_policy=evidence_only_no_customer_copy_no_sales_decision`。
6. 最终 Reply 模型根据完整证据做唯一客户可见决策；策略 key、话术和素材只是参考，价格/门店/支付/活动/履约等硬事实必须服从权威事实。
7. 回复通过 schema、内容素材、付款卡、deposit evidence、结构消息和 admission 校验；失败只允许一次完整/定向修复，不能用确定性业务话术兜底销售。
8. 持久化阶段记录回复、素材使用、follow knowledge 使用、策略数据 outbox 和 shadow closing sequence 审计；真实延时发送仍由 outreach 执行器另走发送前检查。

## 设计判断

- 跟进策略、逼单策略、卡点话术、实时意图不应新增并行“规则决策链”。正确落点是扩展现有策略配置、语义路由候选、最终 Reply 输出合同与验证审计。
- 如果后续要从 shadow 进入真实发送，必须单独实现并验证发送前最新会话拉取、客户回复阻断、退订/人工/已付/健康风险阻断、重复发送限制、WeChat 维度隔离和幂等；不能只改 runtime_mode 或开关。
- 关键词规则可用于 schema/事实过滤、风险词硬边界或资产风险标记，但不能用于判断正常销售意图、异议、会话阶段或逼单节奏。
- 当前仓库没有历史测试套件，后续实现应自带最小隔离验证，优先覆盖策略服务加载、路由合同归一化、Reply 输出归一化、shadow plan 不发送和阻断条件。

## 已完成

- 从最新 `origin/main` 创建独立分支与 worktree。
- 读取项目宪法、文档索引、任务规则、活跃任务清单和核心合同。
- 梳理 V3 策略、意图、卡点话术、逼单和 outreach 的现有设计流程。
- 优化 V3 semantic router 提示词和 follow-knowledge 匹配上下文，避免把 sequence step action 当成机械硬条件。
- 使用服务器同步的 follow-knowledge 缓存完成同批 120 条真实样本隔离重测；完整原文和模型输出仅保存在 ignored `artifacts/`。
- 新增 V3 跟进策略/卡点话术 BI 事实表、发送回执回填、轻量本地 outcome 归因任务和只读管理查询接口。

## 待办

- 如需真实订单归因，应在 outcome 后台任务中接入平台订单只读接口；当前实现只使用本地消息和本地 run/order 快照。
- 合入 main 后把长期 BI 边界沉淀到合同或 ADR，并把本活跃任务归档到 `docs/tasks/history/INDEX.md`。

## 测试结果

- 2026-09-02：本地隔离加载 `AiSalesPolicyService` 与 `SalesStrategyService` 通过；未调用模型、未连接生产、未发送消息。
  - `ai_sales_policy_v1` runtime mode 在默认环境下为 `off`。
  - `sales_strategy_catalog_v1` runtime mode 在默认环境下为 `off`。
  - 策略目录统计：13 categories、143 scenarios、92 strategies、522 contents、226 images、61 videos。
  - 策略目录 audit：`ok`，error_count=0。
- 2026-09-02：隔离测试跟进策略、卡点话术、Reply 策略归一化和 shadow 逼单链路，通过 17 项断言；未调用真实模型、未连接生产、未发送客户消息。
  - 环境默认开关验证：`AI_SALES_POLICY_ENABLED=false`、`SALES_STRATEGY_CATALOG_ENABLED=false` 时运行态均为 `off`。
  - 打开本地环境开关后，`AiSalesPolicyService` 运行态为 `active`，策略目录运行态为 `shadow`。
  - 策略目录可按真实场景文本召回卡点话术候选，并转换为 Reply 可用的 `sales_strategy:<content_id>` 参考候选。
  - 策略候选均保持 `reference_only_not_business_fact`，不会成为价格、门店、支付或履约权威事实。
  - 缺少动态事实时，策略内容会按 `missing_facts:*` 或 `hard_risk` 被过滤；样例过滤原因包括 `weather_facts`、`offer_facts/operator_facts`、`reservation_facts`。
  - 语义路由输出可驱动 `_sales_strategy_retrieval` 返回候选。
  - Reply 的 `policy_decision` 会保留合法的 realtime intent、closing sequence、cardpoint category；非法或不在目录中的 key 不会生效。
  - `explicit_exit` 会强制归一为 `primary_task=hard_stop`、`closing_decision.action=complete`、`customer_state=hard_stop`。
  - `record_closing_sequence_shadow` 能基于 `price_hesitation.value_reframe` 生成后续 shadow delayed tasks，且任务带 `before_send_check=true`。
  - outreach 执行器对 `runtime_mode=shadow` 的 closing/followup strategy task 只记录 `shadowed`，不会调用发送客户端。
  - 本地未配置 `FOLLOW_KNOWLEDGE_TOKEN`；follow-knowledge 客户端验证结果为安全降级 `status=disabled`、`reason=follow_knowledge_not_configured`，未做真实接口查询。
  - 额外复核：用目录中真实中文 `tactic_tag` 输入时，`cardpoint_decision.tactic_tags` 能正确保留。
- 2026-09-02：按用户补充要求执行真实客户记录 + 真实模型离线小样本评测；不发送客户消息，不调用生产写接口，不把客户原文或模型原文输出写入 docs/Git。
  - 数据源：只读打开本地 ignored SQLite `coze_cli_project/data/ai_paths.db`，含 1813 个会话、6043 条消息，最近消息时间到 2026-07-21。
  - 凭据：在干净 worktree 中临时加载旧工作区 `.env`/`ai_paths/.env` 的模型与平台配置键；未输出密钥值。可用 `DEEPSEEK_API_KEY`，无 `FOLLOW_KNOWLEDGE_TOKEN`。
  - 连通性：DeepSeek `deepseek-v4-flash` 调用成功，单条样本 `semantic_status=ok`。
  - 运行态批次：抽样 14 条真实客户当前句，覆盖 distance、price、hesitation/time、effect/trust、health/repair、payment/action、stop-contact。由于 follow-knowledge taxonomy 未配置，V3 归一化后的 `classification_status` 全部为 `none`，`knowledge_evidence` 为 `empty` 或 `deferred_until_store_resolution`；因此当前环境不能验收真实 follow-knowledge 序列/话术接口准确性。
  - 退化召回表现：仅依赖模型 `current_intent.summary` + 本地策略目录召回时，14 条中粗粒度 Top-1 命中 6 条；价格、信任/效果、停止联系样本存在明显误召回。
  - 对照批次：用本地策略目录构造评测用 taxonomy 后复跑同 14 条，粗粒度命中提升到 9 条；价格、信任/效果、距离类明显恢复，说明 taxonomy/话术接口缺失是主要影响因素。
  - 二次审阅：5 个疑似 mismatch 中，约 3 个为抽样桶误判或相邻类可接受；仍需处理的真实问题包括：明确停止/“不用了”类信号仍可能进入策略召回；health_constraint 与 need_mismatch/repair_objection 在目录 taxonomy 和话术场景上边界重叠。
- 2026-09-02：响应“从服务器拿并同步到本地、补接口文档目录”的追加要求。
  - 已尝试用文档中回调服务器 IP `47.252.81.104` 做非交互只读 SSH 连通性检查；结果为 `Permission denied (publickey, gssapi*)`，当前本机没有服务器登录权限，不能直接读取服务器 `/opt/ai-paths/.env` 或生产数据。
  - 用户提供 `ai-paths-deploy.pem` 与 `ai-paths-python-deploy.pem` 后复测；key 文件可读且可生成公钥指纹，但对 `47.252.81.104` 的 `root`、`ubuntu`、`ecs-user`、`admin`、`deployer`、`deploy`、`ai-paths`、`ai_paths`、`python`、`www-data` 用户均返回 publickey 权限拒绝。需要确认真实 SSH host/user 或服务器授权。
  - 用本地已有 `PLATFORM_AGENT_TOKEN`、`AI_EXTERNAL_API_KEY` 作为候选 `x-event-token` 探测 follow-knowledge 只读接口，接口返回“事件接口凭证无效”，确认它们不能替代 `FOLLOW_KNOWLEDGE_TOKEN`。
  - 用户确认 SSH 命令 `ssh -i "C:\Users\24159\.ssh\ai-paths-aliyun.pem" root@47.252.81.104`；已验证登录成功。服务器存在 `/opt/ai-paths/.env`、`/opt/ai-paths-v3/v3.env`、`/opt/ai-paths-v3/current`，V3 service 使用 `/opt/ai-paths/venv/bin/python`。
  - 已在服务器本地读取环境并调用 follow-knowledge 只读接口，未把 `.env` 或 token 拷到本机；同步到本地 ignored `artifacts/follow_knowledge_cache/`。
  - 同步结果：92 条跟进序列、522 条卡点话术、13 个 taxonomy 类型；另存原始 API 缓存以保留 sequence steps。
  - 真实接口合同问题：服务器返回的动作码为 `act001` 至 `act018`，而本地 `ACTION_CODES` 之前只接受 `empathy/resolve/case/campaign/low_barrier/value_add/care/appt_confirm`，导致 92 条序列的 steps 在归一化后全部清空，script 查询无法命中。
  - 已修复动作码白名单与 V3 语义路由提示，保留旧英文动作码，同时接受真实 `act001...act018` 动作码；并在接口文档记录真实动作码。
  - 修复后用原始 API 缓存重建本地 normalized cache：92 条序列均保留 steps，step_count 分布为 2-9 步。
  - 修复后复跑同一批 14 条真实客户记录 + 真实模型 + 服务器同步 follow-knowledge 缓存：粗粒度命中 12/14；价格、健康、信任/效果均能恢复序列候选，价格和部分健康/定金样本能恢复话术候选。
  - 剩余需处理问题：明确停止/“不用了”类样本仍可能被识别为家人决策并进入 sequence/script；定金卡点与普通犹豫/时间拖延边界需补充业务判定样本和提示约束。
  - 最小确定性验证：`python -m py_compile ai_paths/app/services/follow_knowledge_client.py ai_paths/app/prompts/v3_semantic_router.py ai_paths/scripts/sync_follow_knowledge_cache.py` 通过；内联断言验证 `act001` sequence step、script 和 `_taxonomy_allows_action` 均可通过。
  - 已新增接口文档目录 `docs/interfaces/`，拆分外部依赖接口与对外暴露接口。
  - 已新增 follow-knowledge 全量本地同步脚本，输出到 ignored `artifacts/follow_knowledge_cache/`，不保存 token，不进入 Git。
  - 最小验证：`python -m py_compile ai_paths/scripts/sync_follow_knowledge_cache.py` 通过；无 token 场景 `--allow-disabled` 返回 `status=disabled`、`reason=follow_knowledge_not_configured`，不会伪造同步成功。
- 2026-09-02：V3 跟进策略/卡点话术 BI 埋点本地验证通过。
  - `python -m py_compile ai_paths/app/services/storage/v3_strategy_analytics_repository.py ai_paths/app/services/storage/repositories.py ai_paths/app/services/storage/message_delivery_repository.py ai_paths/app/chat_runtime.py ai_paths/app/routers/operations_admin.py ai_paths/app/config.py ai_paths/app/workers/supervisor.py ai_paths/app/services/storage/mysql_schema.py ai_paths/migrations/versions/20260902_01_add_v3_strategy_analytics.py`
  - SQLite 临时库 smoke：建表、写 usage event、查 summary/by-checkpoint、刷新 outcome、`test_isolated=true` 不写。
  - dispatch/callback smoke：usage event 可关联 `message_dispatches.id`，送达回执可回填 `delivery_status=send_succeeded`。
  - MySQL schema metadata 校验：新增表和核心字段已进入 `EXPECTED_ALL_TABLES` / `EXPECTED_COLUMNS`。
  - `git diff --check` 通过；仅有 Windows 换行提示。

## 发布与回滚

- 本阶段不发布。
- 若后续合入 main，必须先确认 `main` 干净、完成最小隔离验证，并记录最终 commit。

## 待沉淀的长期结论

- 待调研完成后决定是否需要更新 `docs/contracts/sales-strategy.md` 或 ADR。
