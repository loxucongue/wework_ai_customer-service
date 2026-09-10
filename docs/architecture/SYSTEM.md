# 系统结构

- status: current-code
- owner: project
- verified_at: `2026-09-10`
- code_baseline: `origin/main@0e7f76e5a1e8d81ad87ec8e57eb97ea46165694f`
- source_of_truth: 当前 `main` 代码树；动态部署事实见 [生产状态](../current/PRODUCTION_STATE.md)

## 1. 运行拓扑

Reply、Control、Worker 三个后端角色必须由同一个干净 `main` commit 构建；管理前端也必须来自干净 `main`，与后端存在耦合改动时同批发布，否则可以独立发布兼容版本：

```text
企业微信聚合平台 / 管理员
              │
            Nginx
      ┌───────┼────────┐
      │       │        │
 V3 Reply   Control   Frontend
 客户回复    管理/回调   管理页面
      └───────┼────────┘
              │
          共享 MySQL
              │
            Worker
   SOP / 主动唤醒 / 可靠收尾 / outbox
```

- `ai-paths-v3.service`：唯一产品 V3 Reply API。
- `ai-paths.service`：共享控制面、管理接口和回调；不是产品 V1/V2。
- `ai-paths-workers.service`：第三方 SOP、主动唤醒、可靠收尾和 outbox。
- `ai-paths-frontend.service`：管理前端。
- reply、control、worker 必须来自同一 clean `main` SHA；旧产品 V1/V2 路由不得恢复。

稳定角色和路由边界见 [运行版本边界](../contracts/RUNTIME_BOUNDARIES.md)。

## 2. 仓库职责

```text
ai_paths/app/main.py              生命周期、路由与 worker 编排
ai_paths/app/runtime_services.py  Reply / Control / Worker 依赖装配
ai_paths/app/runtime_roles.py     运行角色标准化
ai_paths/app/routers/             FastAPI 路由
ai_paths/app/graph/               唯一 V3 回复图
ai_paths/app/prompts/             当前与兼容 Prompt
ai_paths/app/policies/            版本化销售策略与临时业务配置
ai_paths/app/services/            平台、存储、工具、发送和策略服务
ai_paths/app/services/outreach/   主动唤醒计划与执行
ai_paths/app/services/sop/        SOP 公共能力
ai_paths/scripts/                 迁移、发布与运维脚本
projects/                         管理前端
config/                           部署时读取的业务配置
deploy/                           服务和 Nginx 模板
docs/                             当前架构、合同、接口、运行手册和现场状态
artifacts/                        ignored 评测与运行产物
```

`main.py` 不构造重复平台客户端；重型依赖集中在 `runtime_services.py` 并按角色装配。

## 3. V3 请求生命周期

```text
公网 V3 请求
  → 请求标准化与完整接口计时
  → 固定开场/撤回协议消息快速过滤
  → 持久 generation 幂等与连续消息挤占
  → 人工/AI 接待状态
  → 真实客户可见历史、结构消息、稳定状态与权威事实
  → 条件图片理解
  → Semantic Router：卡点候选、事实主题、门店计划、知识/B单召回条件
  → 确定性序列和话术 Top-K
  → 条件门店/订单等只读事实工具
  → 确定性补召回、素材去重与 evidence join
  → V3 Reply：唯一销售语义决策与客户可见回复
  → 事实、ID、结构、安全、主线和交易边界校验
  → 必要时最多一次 Reply 完整重试或定向修复
  → 核心结果事务、结构消息组装与 HTTP 返回
  → Worker 按 request_id 幂等补齐 trace / memory / BI / Shadow / outbox
  → 发送回执与后续客户/订单时间窗口归因
```

完整在线模型、逐字 Prompt、动态上下文和虚构贯穿例子见
[V3 大模型节点与 Prompt 全景](V3_MODEL_NODES_AND_PROMPTS.md)。

关键职责只有两条：

- 模型负责语义、客户心理、销售节奏和自然表达。
- 代码负责权威事实、工具、schema、合法 ID、权限、幂等、安全、交易与发送结果。

Router、序列、话术、B 单目录和门店工具都只是 Reply 的证据；不能成为第二销售决策者。

## 4. 知识与策略

- Follow Knowledge 是当前单一业务知识租户的只读目录，可在全部 `slXXXX` 企微号间共享缓存。
- 客户聊天、订单、画像、发送次数、SOP 和主动触达状态绝不随知识缓存共享。
- 普通跟进按当前卡点类型取池，tag/action 用于排序，不因动作标签错位把可用话术筛成零。
- B 单规则回答“是否具备推进资格”，策略回答“采用什么节奏”，节点话术类型提供表达候选；最终动作仍由 Reply 决定。
- 外部目录优先；显式配置允许在外部未配置、异常或空目录时使用版本化 provisional 本地 JSON。两种来源不得同轮混用。
- 延时 B 单和四大区节点只允许 Shadow，不因目录存在自动发送。

详细合同见 [AI 销售策略运行合同](../contracts/sales-strategy.md)。

## 5. 门店与交易事实

- Router 只有在当前任务需要门店事实时才请求门店工具；门店索引不在每轮预加载。
- 地点解析模型只解析目的地；受限地图和门店目录决定候选；Reply 只使用本轮真实门店 ID。
- 最近实际门店卡和最近区域推荐依据是两份事实：前者防重复发送，后者决定是否需要重查。
- 订单、支付、预约完成、营业时间、楼层/到店指引和付款卡均受独立权威事实与结构合同保护。
- 发门店卡不等于预约；客户口头称已付不等于权威已付；目录话术不授权动态事实。

## 6. 持久化、幂等与观测

- 平台 `msgid` 按销售接触边界派生 `generation_key`；相同消息并发、顺序重试或进程重启后只允许一个生成结果。
- `response_id` 和每条 `client_message_id` 在重放时稳定；下游聚合平台仍必须据此做发送幂等。
- 客户可见回复、运行终态和必要发送事实同步保存；完整轨迹、画像观察、BI、Shadow 和策略回传由持久收尾 Worker 重试补齐。
- 日志必须区分候选、Reply 采用、实际发送和送达，不把缺失历史字段展示成零。
- 性能同时记录完整 HTTP、入口事务、Router、知识/工具、Reply、核心持久化和异步收尾；模型图耗时不能冒充端到端耗时。
- BI 只做观测和时间窗口归因，不保存模型思维，不宣称策略直接导致成交。

## 7. 主动唤醒

```text
候选扫描
  → 已真实开口且达到沉默资格
  → 平台明确 AI 接待
  → 客户/订单/退订/关系状态
  → DeepSeek 选择真实跟进来源并形成计划
  → 按真实序列节点物化任务
  → 每个任务发送前再次复核
  → 平台发送、回执、客户开口与订单归因
```

沉默阈值只表示候选资格，不是发送 SLA。历史物理表和接口中的 `first_day_*` 仅为兼容名称，当前不限制加微时间。运行开关与现场值只写在 [生产状态](../current/PRODUCTION_STATE.md)，稳定观测口径见 [主动唤醒合同](../contracts/outreach-analytics.md)。

## 8. 第三方 SOP

第三方 SOP 当前不调用模型：

```text
/event/trigger/pending
  → 会话未开口 + 关系未删除 + AI 托管
  → /event/trigger/sop-messages
  → 原样发送第一组合法内容
  → /event/trigger/consume
  → /event/trigger/service-rule-data
```

任务和内容是两个消费对象；发送调用结果未知、平台明确拒绝、内容无效、客户已开口等终态必须按唯一合同分别处理。完整状态机和运维核验只认
[第三方 SOP V3 合同](../contracts/third-party-sop-v3.md)。

## 9. 客户隔离边界

销售接触状态严格按 `corp_id + wechat + external_userid` 隔离。`platform_customer_id`（旧字段名可能是 `customer_id`）只是独立的平台客户记录 ID，不能替代 `external_userid`；客户加微关系 ID、接待人员 ID 和企微号同样不可互相补位。详见 [客户身份合同](../contracts/customer-identity.md)。

## 10. 文档与动态事实

- 架构和合同只写稳定机制；开关、release、队列、水位和回滚点只写在 `docs/current/` 并标核验时间。
- 客户原文、模型输出、截图和评测明细只进入 ignored `artifacts/`。
- 一次发布历史只在 [任务历史索引](../tasks/history/INDEX.md)留一行；Git 是详细追溯来源。
- 涉及线上前必须重新核验 [生产状态](../current/PRODUCTION_STATE.md)，不能依赖旧快照。
