# V3 意图、情绪与意图路由合同

- status: active-production-observation
- owner: reply-runtime
- source_of_truth: `ai_sales_policy_v2.json`、V3 Semantic Router、V3 Reply 与策略 BI 代码

## 产品流程

```text
客户当前消息 + 近聊 + 权威事实 + 上一轮稳定摘要
  → Semantic Router：识别当前问题/卡点、需要查询的事实、知识和逼单候选
  → 必要只读工具：补门店、订单等事实
  → V3 Reply 同一次最终调用：选择唯一主任务，并输出最终意图、情绪、逼单动作和客户回复
  → 代码校验：枚举、证据引用、事实、安全边界、频控和节点来源
  → 返回回复；写入意图、情绪、策略与后续结果事件
```

Semantic Router 的 `current_intent.summary` 是检索与工具规划摘要，不是最终 7 类销售意图。唯一最终意图是 V3 Reply 的 `policy_decision.realtime_intent.type`。情绪和逼单也由这次 Reply 一并判断，不新增独立模型调用。

## 当前实现进度

| 板块 | 当前状态 | 实现机制 | 主要代码位置 |
| --- | --- | --- | --- |
| 意图目录 | 已实现 | 版本化 7 类：事实咨询、表达卡点、推进成交、提交信息、暂缓、明确退出、普通交流；明确退出只认停止后续联系的原话 | `ai_paths/app/policies/ai_sales_policy_v2.json`、`ai_sales_policy_service.py` |
| 检索意图路由 | 已实现 | DeepSeek Semantic Router 根据当前消息和历史选择卡点、事实主题、门店工具及知识候选 | `v3_semantic_router_service.py`、`prompts/v3_semantic_router.py` |
| 最终实时意图 | 已上线，待业务金标验收 | 最终 Reply 在生成回复时同时选择一个主意图和最多 3 个次要意图，并引用真实客户消息 | `prompts/reply_synthesizer.py`、`graph/nodes/reply_nodes.py` |
| 主任务路由 | 已实现 | 收集多信号后只选择一个 `primary_task`；风险、人工接管、明确退出和交易终态优先 | `ai_sales_policy_v2.json`、`reply_nodes.py` |
| 情绪目录 | 已实现 | 8 类情绪均有定义、最低证据和反例；粗口、短句、慢回复等弱信号不得单独升级为愤怒 | `ai_sales_policy_v2.json`、`ai_sales_policy_service.py` |
| 情绪影响回复 | 已上线，持续观察 | Reply 选择情绪和置信度；代码从目录派生 `flow_action`，低置信度愤怒只降压，不暂停营销 | `reply_synthesizer.py`、`reply_nodes.py` |
| 跨轮变化 | 已实现 | 只读取同一销售接触边界上一条稳定事件；下一次真实客户回复回填下一意图、下一情绪和变化 | `chat_runtime.py`、`v3_strategy_analytics_repository.py` |
| BI 查询 | 已实现 | 按意图、情绪和变化聚合，支持时间、企微、卡点、策略等筛选 | `routers/operations_admin.py`、`v3_strategy_analytics_repository.py` |
| 逼单协同 | 已实现 | Router 召回同一来源的规则/策略/节点，Reply 选择；代码校验真实 ID、频控和禁忌。固定 `trigger` 从有效目录选择派生，避免冗余字段误杀正确策略 | `v3_semantic_router_service.py`、`reply_nodes.py` |
| 生产效果证明 | 首版已上线观察 | DeepSeek 隔离评测达到 93.33% 策略结构覆盖并由产品负责人接受为首版门槛；仍未形成 400 条业务确认金标，不能把结构覆盖解释成业务准确率 | ignored `artifacts/`、`docs/current/KNOWN_ISSUES.md` |

当前生产文本模型合同是 Router 使用 `deepseek-v4-flash`，最终回复、结构修复和完整重试使用 `deepseek-chat`，Reply 不启用 GPT 竞速或故障接管。模型轨迹若出现 `gpt-*`，视为配置错误而不是允许的降级。

## 运行合同

- 一轮只有一个主任务；次要意图只用于保留多信号，不能单独触发客户动作。
- 最终意图只能来自 7 类目录，次要意图去重且最多 3 个；有判断时应引用当前或近邻真实客户消息。
- 最终情绪只能来自 8 类目录。情绪只调整措辞、篇幅和销售压力，不得授权门店、价格、预约、付款或效果承诺。
- `hesitant/cold/defensive` 应降压；`impatient` 只有达到中置信度才暂停本轮追加销售；`angry` 只有达到高置信度才暂停本轮销售并交由系统边界处理，低置信度只降压。
- 粗口必须结合指向和语义判断。正向惊叹、口头禅、抱怨自身/家人/第三方及普通讲价都不是愤怒证据。
- 投诉、要求解释、找负责人或转人工不等于明确退出；永久停止营销仍只由明确“别联系、别发”等退订事实触发。
- 客户每次发新消息都重新判断，上一轮意图、情绪和逼单节点只作摘要，不得机械延续。
- 明确退出、投诉/愤怒、健康风险、人工接管和交易终态由代码强校验，不能被模型或业务配置覆盖。
- 缺少分类等观测字段时记录 `decision_status=degraded`；策略扩展不得让正常客户回复因 BI 字段缺失而返回 5xx。
- 当前无可执行逼单序列时，`closing_decision=pause + sequence_key=none` 是合法决策；新卡点可以切换到解卡表达和跟进话术，但不能把该 `switch` 误记为逼单推进。

## 可提供的数据

- 本轮主意图、最多 3 个次要意图、置信度和证据引用。
- 本轮情绪、置信度、表达压力和流程动作。
- 唯一主任务、逼单动作、规则/策略/节点、卡点、跟进序列和采用话术。
- 同一客户下一次真实回复对应的下一意图、下一情绪及 `上一情绪→下一情绪` 变化。
- 送达后的 1h/6h/24h/72h 开口，以及订单支付、排客、到店、完成的时间窗口结果。
- 管理接口：`/admin/v3-strategy-analytics/by-intent`、`by-emotion`、`transitions`、`summary` 和 `failures`。

这些结果是运营观测和时间窗口归因，不表示某个情绪判断或策略直接导致成交；完整聊天原文和模型思维不得进入 BI 表。

## 已知不合理点与后续顺序

1. `current_intent` 同时被用于 Router 的自由文本摘要和 Reply 的 7 类最终意图，名称容易让产品和研发误以为有两个最终意图。后续合同升级时建议将 Router 字段改名为 `retrieval_goal`，但不为改名新增兼容层。
2. 8 类情绪是强制单标签，对很短的“嗯、好、？”只能低置信度归入某类，容易产生伪精确。BI 必须同时展示置信度；是否增加 `uncertain` 应由业务金标混淆矩阵决定。
3. 情绪变化表示两次客户消息的分类变化，不是“客户被本轮回复改善了”的因果结论。看板只能写“后续变化/相关”。
4. DeepSeek 对情绪、退订和 B 单结构的服从可以达到可用水平，但在项目、付款或门店权威事实完全缺失时仍可能补业务口径。运行链必须保证正常场景先提供权威事实；事实服务缺失时不得把 closing catalog 当事实来源，生产放量前仍需真实样本金标和事实缺失专项验收。
5. 代码与 BI 已上线，独立序列模型、话术模型筛选和门店后二次销售语义判断已退出运行链；生产策略已启用，延时节点仍为 shadow。当前外部 B 单目录不可用时按显式配置使用版本化本地目录，不得把该降级来源伪装成平台数据。

## 验收要求

生产策略已于 `main@91ac4016` 启用。后续仍需用业务确认的真实匿名样本分别验证主意图准确率、情绪准确率、压力方向、退订/投诉误推进、证据引用完整率和端到端延迟；未完成金标前只能称“首版上线观察”，不能宣称达到最终业务准确率。
