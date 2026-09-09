# v3-supervisor-feedback-closure

- status: active
- owner: Codex
- base_branch: main
- base_sha: eae652145a1891e1626b8a1c1395d6b76b47219b
- production_verified_at: 2026-09-09 Asia/Shanghai（开发前待现场复核）
- production_releases: 文档线索为 `ai-paths-unified-20260909-v3-terminal-78622cf4`，发布前以服务器现场为准

## 目标

- 闭环销售主管反馈：稳定幂等、失败自动补答、真人短消息、软拒绝继续推进、主线恢复、效果素材轮换、价格/门店事实边界及端到端性能。
- 保持 V3 Reply 为唯一销售语义决策，Router 与 Reply 全部使用既定 DeepSeek tier，不新增正常链模型节点。
- 通过 L1 确定性、L2 DeepSeek 隔离、L3 模拟生命周期和 L4 发布核验后合入并部署同一 clean main SHA。

## 非目标

- 不修改聚合平台发送实现；AI 侧只提供稳定消息 ID、幂等结果和重放标记。
- 不承诺 3 秒内完成最终销售回复；不跳过人工/退订/关系删除和权威交易终态门禁。
- 不编造门店营业时间、楼层、到店指引、二次价格、效果素材或支付事实。
- 不修改 RDS 网络地址；内网切换作为独立运维发布。

## Change contract

- type: V3 可靠性、销售质量、可观测与增量数据库迁移
- scope: V3 请求入口、运行仓储、恢复 Worker、Reply/Router prompt、素材/话术排序、价格与门店事实校验、管理日志可选字段
- risk: 重放错误导致重复回复；自动补答在客户状态变化后误发；Prompt 精简造成语义回归；素材指纹误去重；迁移兼容；Worker 增加 RDS 压力
- validation: L1 合同测试、SQLite/MySQL 迁移测试、DeepSeek 80 条专项与 120 条 L3 样本、完整 HTTP 性能、零发送/零生产写审计、发布后首批请求只读核验
- rollback: 关闭 `V3_REPLY_RECOVERY_ENABLED`；语义异常关闭新销售节奏开关；运行异常恢复上线前统一 release；新增兼容列保留

## 涉及模块与文件所有权

- 数据链：`ai_paths/app/services/storage/` 中 run/消息/迁移相关文件、`ai_paths/migrations/`、响应 schema 与 workflow 兼容层、恢复服务及其测试。
- 决策链：`ai_paths/app/prompts/`、`ai_paths/app/policies/`、Reply/Router 节点与事实/结构校验及其测试。
- 素材与性能链：素材选择/去重/目录缓存、上下文装载、分段耗时及其测试。
- 主 Agent 独占：`chat_runtime.py`、`runtime_services.py`、worker 编排、环境配置、跨模块集成、任务/合同/接口/当前状态文档、合并与发布。
- 当前活跃索引没有其他任务占用上述文件；所有并行修改按子目录和文件分配，主 Agent 唯一集成。

## 不可破坏合同

- 唯一客户回复入口仍为 `/api/ai/reply/workflow-compatible-v3`，不得恢复 V1/V2。
- 客户边界严格为 `corp_id + wechat + external_userid`；`customer_id` 仅作平台记录标识。
- 明确退订、人工接管、关系删除、权威医疗高风险和具体严重客诉必须停止营销；普通拒绝、讲价、没空、考虑、质疑和单句粗口不得永久停止。
- 预约金已由权威平台确认后进入服务阶段；客户自述已付不能当作权威事实。
- 自动补答发送前必须复核最新消息、AI 状态、退订/删除、交易终态和既有送达；发送幂等键固定且可审计。
- 客户原文、真实身份、原始模型输出和评测报告只进入 ignored `artifacts/`。

## 已确认事实与证据

- 当前基线 `origin/main@eae65214`；生产版本较旧，发布前必须再次现场核验。
- 当前同进程幂等主要依赖内存任务缓存；进程重启后的相同平台消息没有持久 generation key。
- 当前中性兜底“您稍等一下”是终态，尚无可靠自动补答。
- 当前 Reply 静态 Prompt 约 8999 字符、Router 约 8200 字符，Reply 温度为 0；真人表达与多样性主要依赖 Prompt。
- 当前素材按 URL 去重，无法识别同图换 URL；价格配置仍存在“操作全脸”污染语句；门店营业时间/到店指引事实校验需收紧。
- 最新生产样本的长尾主要转移到上下文、门店工具和公网 RDS；核心持久化已缩短，但数据库公网冷连接仍不可由本轮代码完全消除。

## 已完成

- 建立最新 main 的独立 clean worktree 与任务登记。

## 待办

- 实现持久 generation 幂等、稳定响应 ID 和可靠补答。
- 优化上下文/知识/工具预算与完整分段耗时。
- 精简 Prompt，落地销售状态、短消息、多条消息、轻表情和主线动作合同。
- 实现素材内容指纹与轮换、话术重复惩罚、价格/门店事实边界。
- 完成迁移、L1/L2/L3 验证、共享文档、合入 main、生产发布和首批观察。

## 测试结果

- 待执行。

## 发布与回滚

- 待发布；发布前备份 MySQL 与当前 release，迁移后使用同一 clean main SHA 部署 control/reply/worker；如日志字段涉及前端则同步前端。

## 待沉淀的长期结论

- AI 侧消息幂等与下游聚合平台实际只发送一次必须分开度量。
- 自动失败补答属于客户可见恢复链，应有独立开关、持久重试状态和发送前全套复核。
- 价格、门店和素材业务事实必须进入版本化权威配置与窄范围校验，不能依赖模型自由改写。
