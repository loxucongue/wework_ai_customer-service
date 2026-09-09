# v3-supervisor-feedback-closure

- status: active
- owner: Codex
- branch: `codex/v3-supervisor-feedback-closure`
- base_branch: `main`
- base_sha: `eae652145a1891e1626b8a1c1395d6b76b47219b`
- candidate_code_sha: `e58dc7c1`（当前代码、日志 UI 与评测锚点；文档提交后 SHA 会前移）
- production_verified_at: `2026-09-09 Asia/Shanghai`
- production_release_before_deploy: `/opt/ai-paths/releases/ai-paths-unified-20260909-sop-alert-attribution-182c9866`
- production_commit_before_deploy: `182c986644a4d6d0db054a4e66d9e0d90e833a19`
- production_services_before_deploy: `ai-paths.service`、`ai-paths-v3.service`、`ai-paths-workers.service` 均为 active；最终发布前仍需再次现场核验

## 目标

- 闭环销售主管反馈：稳定幂等、失败补答代码准备（本次保持关闭）、真人短消息、软拒绝继续推进、主线恢复、效果素材轮换、价格/门店事实边界及端到端性能。
- 保持 V3 Reply 为唯一销售语义决策，Router 与 Reply 全部使用既定 DeepSeek tier，不新增正常链模型节点。
- 通过 L1 确定性、L2 DeepSeek 隔离、L3 模拟生命周期和 L4 发布核验后，合入并部署同一 clean main SHA。

## 非目标

- 不修改聚合平台发送实现；AI 侧只提供稳定消息 ID、幂等结果和重放标记。
- 不承诺 3 秒内完成最终销售回复；不跳过人工接管、明确退订、关系删除和权威交易终态门禁。
- 不编造门店营业时间、楼层、到店指引、二次价格、效果素材或支付事实。
- 不修改 RDS 网络地址；内网切换作为独立运维发布。

## Change contract

- type: V3 可靠性、销售质量、可观测与增量数据库迁移
- scope: V3 请求入口、运行仓储、恢复 Worker、Reply/Router Prompt、素材/话术排序、价格与门店事实校验、管理日志可选字段
- risk: 重放错误导致重复回复；自动补答在客户状态变化后误发；Prompt 精简造成语义回归；素材指纹误去重；迁移元数据锁；Worker 增加 RDS 压力
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
- 明确退订、人工接管、关系删除、权威医疗高风险和具体严重客诉/退款纠纷必须停止 AI 营销；普通拒绝、讲价、没空、考虑、质疑和单句粗口不得永久停止。
- 预约金已由权威平台确认后进入服务阶段；客户自述已付不能当作权威事实。
- 后续如启用自动补答，发送前必须复核最新消息、AI 状态、退订/删除、交易终态和既有送达；发送幂等键固定且可审计。本次发布不启用。
- 客户原文、真实身份、原始模型输出和评测报告只进入 ignored `artifacts/`。

## 已完成

### 持久生成幂等与响应合同

- 以 `sha256(corp_id|wechat|external_userid|msgid)` 形成持久 `generation_key`；相同消息的并发、顺序重试和进程重启后重试只允许一次 Router/Reply。
- 为结果提供稳定 `response_id`，为每条文本或结构消息提供稳定 `client_message_id`；数据库恢复结果标记 `replayed=true`。
- 同步生成进程异常留下的显式过期租约可由下一次相同平台消息请求原子接管并同步重算；复用原 `request_id/response_id`，不创建第二条 run、dispatch 或客户可见自动补答。该能力不依赖、也不等于启用 `V3_REPLY_RECOVERY_ENABLED`。
- 新增迁移 `20260909_01_add_v3_reply_generation.py`，在 `runs` 增加生成与恢复字段、唯一索引和到期扫描索引。

### 可靠失败补答（代码完成、生产保持关闭）

- 技术链最终只返回“您稍等一下”时标记 `fallback_pending`，由 Worker 在约 15 秒、45 秒最多重试两次。
- 发送前重新检查最新客户消息、平台 AI/人工状态、明确退订、关系删除、权威已付/已预约和既有送达。
- 恢复发送使用 `v3-recovery:<original_request_id>` 幂等键；两次失败后转人工复核状态，不无限重试。
- 恢复 Router/Reply 固定使用 DeepSeek，不执行交易、BI、策略回写或 Shadow 计划。
- 本次不启用客户可见补答：共享 `.env` 和 Reply `v3.env` 必须同时保持 `V3_REPLY_RECOVERY_ENABLED=false`。生成所有权、恢复后素材/门店记忆收尾和 delivery finalizer 异常处理完成独立安全验收后再另行启用。

### 真人表达、销售状态与主线

- Router/Reply 静态 Prompt 分别收敛到约 6500/7000 字符预算内；Router 使用 `deepseek-v4-flash`，Reply、完整重试和结构修复使用 `deepseek-chat`。
- Reply 温度由 `V3_REPLY_TEMPERATURE` 控制，候选值为 `0.15`；不再默认只发一条，单条文字目标 20～60 字，异常保护为每轮最多 8 条、文字合计 300 字。
- 客户状态统一为 `continue_sales`、`pause_current_turn`、`hard_stop_marketing`、`post_payment_service`；普通拒绝、讲价、没空、考虑、质疑和单句粗口不永久停销。
- 正常轮必须落实一个 `next_sales_action`；当前问题先答，随后根据真实已送达记录恢复最早缺失的效果、活动、门店、预约或预约金主线。

### 素材、话术、价格和门店事实

- 素材从 URL 去重升级为文件 SHA-256 与感知指纹，结合相关性、部位/人群、近期使用和表达多样性排序；话术增加近期 `script_id`、相同论据与高相似文本惩罚。
- 效果/信任场景有安全、未发真实素材时，同轮“短文字 + 素材”交付，不再询问“要不要看”。
- 增加 268 窄范围校验：脸颊两侧不按左右拆价，不把 268 说成无差别操作全脸，脸与手不能合计一个 268，二次价格不得自动沿用。
- 唯一门店结果必须交付说明和真实门店卡；楼层/房间/详细导航只在权威已付且门店确定后提供；营业时间缺失时不编造，形成结构化 `store_fact_followup`。

### 性能与管理观测

- 上下文、上一轮状态和 SOP 进度并行加载；请求预算区分普通 25 秒与工具 35 秒，并至少为 Reply 保留 10 秒。
- 管理运行日志增加生成状态、稳定响应 ID、重放与恢复状态；旧记录缺字段显示“未记录”，不伪造为恢复次数零。
- 环境模板增加恢复开关、8 条/300 字与 Reply 温度配置。

## 测试结果

- 全仓后端确定性测试：`846 passed, 0 failed`；另有生成租约、人工接管、迁移、素材、门店、价格与安全边界扩大回归 `160 passed`。仅有既有 Authlib/SQLAlchemy 弃用警告，不阻断发布。
- 数据库迁移：单一 Alembic head `20260909_02`，链路为 `20260908_01 → 20260909_01 → 20260909_02`；SQLite/MySQL 兼容测试通过。迁移为兼容字段/新表保留型，不支持破坏性 downgrade。
- 前端：TypeScript、ESLint、Next 生产构建和 tsup 构建通过；共 37 条路由完成构建。
- DeepSeek 广覆盖前序候选 `df79dd5a`：80 条专项中 46 条进入模型、34 条人工接管，46/46 AI 初评通过；120 条生命周期中 42 条可评、78 条人工接管，39/42 AI 初评通过，2 条技术兜底，3 条需复核。两批 P95 为 17.01/16.77 秒，GPT、生产发送和生产写入均为 0。
- 当前代码锚点 8 条定点回归的唯一失败来自评测器未装载真实素材目录；修正后以相同真实身份和线上素材快照重放，成功输出两张效果图，AI 初评、真人表达和硬断言均通过，端到端 13.09 秒，GPT/发送/生产写入均为 0。广覆盖数据属于较早候选，当前定点结果属于增量证据，两者都不是业务金标。

## 待办

- 由销售主管人工复核至少 50 条；AI 初评不得作为真人销售效果的最终金标。
- 业务提供过滤后话术/素材目录版本或 checksum；平台后续补齐门店营业时间和有效到店指引。
- 发布前现场核验 MySQL 索引、表大小和元数据锁风险，备份数据库，再升级至 `20260909_02`。
- 合入最新 clean `main`，部署同一 SHA 到 control/reply/worker；涉及日志字段时同步前端。
- 发布后核验 V3、稳定消息 ID、首批请求耗时、兜底、素材/门店交付、价格事实和退订边界，并确认恢复 Worker 保持关闭。

## 发布与回滚

- 尚未发布。最近现场生产统一 release 为 `ai-paths-unified-20260909-sop-alert-attribution-182c9866`，最终操作前必须重新核验实际指针和服务状态。
- 数据库迁移前做可验证备份；发布 manifest 必须包含 `branch=main`、完整 commit、`dirty=false`、`interface_version=v3` 和角色。
- 本次发布前即保持 `V3_REPLY_RECOVERY_ENABLED=false`；语义或运行异常时恢复发布前统一 release。新增兼容列、索引和内部待办表可以保留。
- 聚合平台重复消费、门店事实缺失和公网 RDS 长尾不是本仓库代码可以单独闭环的问题，发布报告必须单列。

## 待沉淀的长期结论

- AI 侧消息幂等与下游聚合平台实际只发送一次必须分开度量。
- 自动失败补答属于客户可见恢复链，必须有独立开关、持久重试状态和发送前全套复核。
- 价格、门店和素材业务事实必须进入版本化权威配置与窄范围校验，不能依赖模型自由改写。
- 短消息和主动销售是模型表达合同；8 条/300 字只是异常保护，不能演化成固定话术模板或关键词业务分支。
