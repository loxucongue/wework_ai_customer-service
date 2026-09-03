# v3-closing-catalog-integration

- status: active
- owner: Codex
- base_branch: main
- base_sha: 780d343747afa84f93c623d571e7747093e8daaf
- production_verified_at: 未核验；本任务不发布
- production_releases: 未核验；本任务不接触生产

## 目标

- 将租户逼单规则与逼单策略只读目录融入现有 V3 同轮销售决策，不新增模型调用。
- V3 Reply 继续作为唯一销售语义决策点；规则和策略只提供候选、约束与证据。
- 使用节点话术类型联查真实已发布话术，并补齐规则、策略、节点、类型与采用结果的 BI 可观测字段。
- 使用符合外部接口字段的模拟目录完成确定性测试；真实模型效果评测作为上线前独立门槛，产物只写 ignored `artifacts/`。

## 非目标

- 不发送客户消息，不启用延时逼单真实触达。
- 不调用生产写接口，不把测试 token、客户原文或模型输出写入 Git/docs。
- 不新增关键词业务规则引擎，不以本地演示规则替代租户空规则。
- 不新增或改写业务话术正文，只验证已有话术的类型关联与候选使用。

## Change contract

- type: feature + data contract + observability
- scope: Follow Knowledge 只读适配、Semantic Router 候选生成、V3 Reply 选择校验、现有 BI 表增量字段和查询、模拟评测
- risk: 外部目录延迟/不可用、规则与策略缺少稳定关联、模型误推进、BI 维度漂移
- validation: schema/缓存/候选/安全边界/幂等/迁移测试；模拟目录隔离模型评测；确认不增加模型调用
- rollback: 关闭 `AI_SALES_POLICY_ENABLED`；回滚本任务 main commit；新增兼容列可保留

## 涉及模块与文件所有权

- `ai_paths/app/services/follow_knowledge_client.py`
- `ai_paths/app/services/v3_semantic_router_service.py`
- `ai_paths/app/prompts/v3_semantic_router.py`
- `ai_paths/app/graph/nodes/reply_context.py`
- `ai_paths/app/graph/nodes/reply_nodes.py`
- `ai_paths/app/services/storage/v3_strategy_analytics_repository.py`
- 相关 schema、migration、router、测试和销售策略/接口合同文档

## 不可破坏合同

- V3 Reply 是唯一最终销售语义决策点；Semantic Router 只提供证据与候选。
- 一轮最多一个销售推进动作；新卡点、明确退订、人工接管和交易终态必须阻断推进。
- 外部接口失败不能导致客户回复失败；成功返回空规则表示租户未配置，不能静默回退本地演示规则。
- 延时节点仅 shadow，不进入真实发送适配器。
- BI 不存完整客户原文或模型思维过程；销售接触边界不能跨 WeChat。

## 已确认事实与证据

- 两个外部接口均为只读租户目录；逼单规则提供触发和全局约束，逼单序列提供场景与节点。
- 当前测试租户规则接口成功但 `triggers=[]`；策略接口有 2 条序列、4 个节点。
- 当前接口没有规则到策略的稳定外键，节点仅有动作/话术类型 ID 和名称；适配层必须保留来源 ID 与目录校验状态。
- 当前 V3 已有 Semantic Router 与 Reply 模型调用，本任务不得再增加独立逼单模型节点。

## 已完成

- 已核验 base main 干净并与 `origin/main` 一致。
- 已建立独立分支/worktree并登记文件所有权。
- 已接入 closing-rule / closing-sequence，只读并行加载、single-flight、短失败缓存与进程内 last-known-good。
- Router 在既有模型调用中召回规则/策略；门店工具后在既有 post-store 调用中重算，不新增决策节点。
- 已按节点 followCheckpointTypeId 联查真实话术；纯逼单场景不新增 selector 模型调用，Reply 保持唯一最终选择。
- 已补齐规则证据、卡点未解决、频控、节点时机/类型、话术关联和 shadow 安全校验。
- 已扩展 BI usage 字段、规则聚合接口和幂等保护；重放不覆盖送达事实。

## 待办

- 更新历史摘要并合并 main。

## 测试结果

- `python -m pytest -q tests/test_v3_closing_catalog_integration.py tests/test_v3_strategy_analytics_storage.py tests/test_v3_strategy_analytics_api.py`：40 passed。
- `python -m pytest -q`：139 passed，只有既有 `cozepy/Authlib` 弃用警告。
- `python -m compileall -q ai_paths/app ai_paths/migrations/versions`：通过。
- 对本任务所有变更 Python 文件执行 Ruff：通过；全仓 Ruff 仍有 53 个既有、与本任务无关的 unused 告警，未扩大范围顺手修改。
- Alembic 迁移链：`20260903_02` 为唯一 head；SQLite 旧 schema 自动补列/索引与 MySQL metadata 均由回归测试覆盖。
- 覆盖模拟外部合同、缓存并发/LKG、空规则、证据、门店后重算、节点类型话术联查、无额外模型 selector、卡点阻断、限频、话术关联、usage 幂等和 BI 分组。
- 未调用真实模型、未发送消息、未调用生产写接口；运行产物不进入 Git/docs。

## 发布与回滚

- 本任务完成后只合入 main，不自动发布；上线前仍需现场核验、迁移备份和发布清单。

## 待沉淀的长期结论

- 外部逼单目录的稳定标识、空目录语义、缓存降级和 BI 口径。
- 上游仍需补共同 publishVersion、tenantKey、稳定 code、组合规则 group/operator、标准 timing 和 taboo 类型；当前实现对缺失能力 fail closed。
