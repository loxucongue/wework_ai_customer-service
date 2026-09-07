# 系统结构

- status: current-code
- owner: project
- last_verified: 2026-09-06 Asia/Shanghai at `main@007bf2c8`
- source_of_truth: 当前 `main` 代码树；精确版本以 `git rev-parse HEAD` 为准

## 代码结构

同一个 `main` 提交构建三个运行角色：

```text
外部平台 / 管理端
        |
      Nginx
  +-----+------------------+
  |                        |
V3 reply                 control API
客户回复                 管理、回调、SOP 控制面
  |                        |
  +----------共享存储-------+
                           |
                         workers
                SOP、outreach、恢复与 outbox
```

- 客户回复产品接口只保留 V3；应用内不再注册旧 V1/V2 路由，公网由 Nginx 对退役地址固定返回 410。
- control、reply、worker 是三个独立进程角色，必须来自同一个 `main` SHA。角色分别使用
  `AI_PATHS_SERVICE_ROLE=control|reply|worker`；只有 worker 允许
  `AI_PATHS_BACKGROUND_WORKERS_ENABLED=true`。
- 路由、生命周期和依赖装配均按角色隔离。`runtime_services.py` 只提供 `build_reply_services`、`build_control_services`、`build_worker_services` 三个直接工厂；每个工厂只创建本角色实际使用的客户端与服务。
- 第三方协议路径中出现 `v1` 不代表产品 V1，不能按名称删除。
- 历史接口/schema 版本号只用于读取旧审计或兼容第三方协议，不构成第二套产品运行时。

## 仓库结构

```text
ai_paths/app/main.py              FastAPI 生命周期、路由、worker 编排
ai_paths/app/runtime_services.py  Reply/Control/Worker 三个直接服务工厂
ai_paths/app/runtime_roles.py     运行角色标准化与旧环境值只读兼容
ai_paths/app/routers/             按角色收口实际暴露的 FastAPI 路由
ai_paths/app/graph/               唯一 V3 回复图
ai_paths/app/services/outreach/   跟进计划、首日流程、消息生成、任务执行
ai_paths/app/services/sop/        SOP 公共执行与送达兼容能力
ai_paths/app/services/            其余平台、存储、发送与策略服务
ai_paths/app/policies/            版本化运行策略
ai_paths/scripts/                 运维、迁移和配置编译脚本
projects/                         管理前端
config/                           部署时读取的业务配置
deploy/                           受版本控制的服务和 Nginx 模板
docs/                             当前架构、合同、运行手册和现场状态
```

日志、验证结果、构建包、数据库、门店快照和上传文件都属于运行数据，必须写入 Git 忽略目录，不能作为代码事实来源。

## V3 回复链与完整请求生命周期

```text
公网请求 / 连续消息挤占
  → 真实客户可见历史 + 已送达结构消息 + 稳定客户状态
  → authoritative context
  → Semantic Router（一次：卡点、工具需求、检索条件）
  → 确定性 Top-K（跟进、话术和 B 单目录）
  → 必要的 read-only facts
  → material selection
  → V3 Reply（一次：意图、情绪、逼单、最终回复）
  → 事实、安全和结构消息校验
  → run / memory / BI / outbox 持久化与发送审计
  → HTTP 返回
  → 送达回执与后续客户/订单窗口归因
```

Semantic Router 只提供分类、检索查询和只读工具需求，不决定客户可见销售动作。代码从外部已发布跟进/话术/逼单目录做稳定、可审计的候选裁剪；门店事实补齐后不再重跑销售语义。Reply 是唯一销售语义与客户可见动作决策节点；代码负责权威事实、工具、schema、幂等、交易边界、安全和发送结果。

文本链当前固定由 `deepseek-v4-flash` 承担 Semantic Router、`deepseek-chat` 承担最终 Reply 及其唯一一次结构修复/完整重试；Reply tier 不继承全局 GPT 应急候选。连续客户消息按原始顺序作为本轮正文输入，内部合并说明只留审计；最终 Reply 只读取最近 12 条真实客户可见消息，排除未交付草稿、覆盖请求和内部 trace。

生产质量不能只看模型图。门店卡、付款卡和素材等结构化消息也属于客户已看到的历史，必须和文本一起参与去重与本轮判断；图执行后的历史事件、BI、outbox、响应组装也属于接口端到端耗时。内存历史保存使用批量幂等写入降低数据库往返，但仍须从请求接收到 HTTP 返回持续观察尾部耗时。

门店目录只在 Router 明确要求门店事实后加载；城市/区县列表候选不超过 6 家且没有重复交付时可以全部交付门店卡，超过 6 家或本轮候选混有已送达卡片时改用完整“门店名 + 区县”文字清单，不能删掉重复卡后误报门店总数。精确位置的最近门店排序仍使用有限候选，不把全部远近门店当作同级推荐。历史同时保留最近实际门店卡交付和最近真实区域推荐依据，停车、营业时间或楼层等详情查询不得覆盖推荐终态。客户已经收到当前城市的最终推荐后表达距离顾虑，未给出新城市时不再追问同城更细位置、不重新查店或发卡，而是使用安全的距离卡点话术承接；没有公里数或路线事实时不得客观断言远近。客户明确再次索要地址/导航时可重发，新城市则正常重新匹配。未预约且没有新卡点时，答清门店详情后应自然连接预约登记，而不是只问没有说明用途的到店时间。

完整验证层级见 [V3 Reply 质量与全链路评测规范](../standards/V3_REPLY_EVALUATION.md)。

当前部署只有一个业务知识租户，所有 `slXXXX` 企微号共用实例级 Follow Knowledge 凭证与业务知识缓存。共享知识不改变客户数据边界：记忆、策略状态、发送频次和订单仍按 `corp_id + wechat + external_userid/customer_id` 隔离。

## Outreach

```text
计划：客户关系与上下文 → 模型决策 → 任务物化 → 持久化
执行：任务认领 → 发送资格检查 → 消息生成 → 平台提交 → 送达终态
```

`OutreachService` 是唯一公开门面。计划与执行入口只负责编排阶段；已开口沉默唤醒、普通跟进、逼单和四大区策略继续使用各自开关。历史内部标识 `first_day_opened_silence` 仅为持久化兼容，当前沉默唤醒不再限制加微时间：客户已开口、最新一条为销售/AI 发送且超过配置阈值时才进入候选。计划生成前和每次真实发送前均以平台 `conversation/status` 明确返回 AI 模式为正向授权；人工、未知或状态接口失败时一律不发送。全账号启用使用空企微白名单，并通过部署启用时间水位阻断历史沉默积压。

沉默唤醒使用独立的 `OUTREACH_DECISION_MODEL` 模型配置，计划分析、话术生成和合同修复不继承 worker 的通用强模型或应急 fallback。当前默认使用 `deepseek-chat` 且 fallback 为空；worker 健康检查公开启用状态、阈值、账号范围、模型、后台任务和最近扫描结果，但不返回凭证或客户内容。沉默分钟数是进入候选的资格阈值，不是固定发送 SLA；实际发送还要经过候选排队、多节点计划生成和发送前二次门禁。

沉默计划的素材链分为模型上下文和执行上下文：场景分析模型只读取去 URL 的真实素材目录、来源、用途和本轮可发送状态；写作与审核模型读取所选来源下的全部图片/视频候选及逐步 `delivery_contract`，明确本步媒体是否会随任务发送。模型只能引用稳定素材 ID，任务物化阶段再从内部原始目录解析 URL。近期已发送素材不可重复选择；没有实际媒体的步骤必须生成独立成立的文字，禁止出现“您看下这个参考”等悬空指代。`effect_proof` 场景必须绑定真实可发送的图片或视频，不能创建空素材计划。

沉默唤醒日志的只读观测链按“客户上下文 → 场景判断 → 素材候选 → 来源/素材采用 → 计划任务 → 发送结果”展示。列表只返回轻量业务摘要；详情从已有运行快照和任务记录派生业务视图，不重新查询当前素材目录冒充历史事实，也不增加计划链写入或数据库表。

主动唤醒 BI 复用同一批运行、计划、任务、发送和客户回复事实，形成“扫描 → 符合条件 → 计划 → 真实触达 → 24h 客户开口 → 7d 预约/支付进展”漏斗。页面同时读取 Worker 健康状态核对配置是否真正生效；聚合查询只读，不触发计划或发送。稳定口径见 [主动唤醒 BI 观测合同](../contracts/outreach-analytics.md)。

当前生产阈值和开关属于动态事实，见 [生产状态](../current/PRODUCTION_STATE.md)；本节只定义机制。

## 第三方 SOP

Worker 中只保留第三方 SOP 两段式链路：`pending` 提供触发时间节点，
`store-visit-pending` 提供实际发送内容。两者配对后共同进入判断、发送、消费、
策略数据回传和送达确认。旧 `/sop/events` 接收器、旧事件模型重试、夜间次日融合
及延迟重放已删除；历史 `sop_events` 和 `sop_send_tasks` 只用于审计、客户数据清理
以及已有 `source_kind=sop_event` 派发的终态兼容。

单任务处理固定为：本地任务状态 → 既有终态/重复处理 → 发送前事实准备 → 判断与发送。无内容触发节点不得单独消费；任一侧读取失败都等待恢复。

## 发布要求

- `main` 是唯一长期开发和发布分支。
- 生产 release 必须映射到已验证的 `main` commit。
- 策略目录、延时逼单和多步骤跟进必须通过独立开关启用；代码合并不得自动改变发送行为。
- 生产拓扑和数据库状态是动态事实，发布前必须重新读取服务器，不得以本页代替现场核验。
