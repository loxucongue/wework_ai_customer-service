# v3-prompt-doc-governance

- status: active
- owner: Codex
- base_branch: main
- base_sha: `0e7f76e5a1e8d81ad87ec8e57eb97ea46165694f`
- production_verified_at: `2026-09-10T09:38:20+08:00`（只读核验；本任务不发布生产）
- production_releases: 只引用 `docs/current/PRODUCTION_STATE.md` 的最近核验，不在本任务现场变更

## 目标

1. 以当前 `main` 代码为唯一实现事实，校正文档索引、背景、架构、合同、接口、当前状态和已知问题。
2. 删除已被当前文档取代、内容重复或已经退役且不应继续作为现行依据的文档；保留 Git 历史作为追溯入口。
3. 新增一份面向不了解项目的 V3 大模型节点与 Prompt 全景说明，完整记录节点目的、实际调用条件、模型、静态 Prompt、动态上下文、上下游、输出合同、失败处理和贯穿示例。

## 非目标

- 不修改 V3 代码、Prompt、配置、数据库、前端或生产服务。
- 不把客户原文、真实 token、服务器密钥、原始模型输出或测试报告写入文档。
- 不把未在线调用的历史 Prompt 描述为现行节点。

## Change contract

- type: 文档治理与实现反向说明
- scope: `docs/**`
- risk: 错删仍被入口或合同引用的文档；把历史设计误写成当前行为；复制 Prompt 时遗漏动态上下文或泄露凭证
- validation: 当前代码符号/调用图核对、文档链接检查、敏感信息扫描、删除对象反向引用检查、`git diff --check`
- rollback: 回退本任务文档提交；无数据或生产回滚

## 涉及模块与文件所有权

- 主 Agent 独占 `AGENTS.md`、`docs/INDEX.md`、`docs/architecture/**`、`docs/background/**`、`docs/current/**`、`docs/adr/**`、`docs/standards/**`、`docs/contracts/v3-reply-admission.md`、`docs/runbooks/PRE_RELEASE_CHECKLIST.md`、`docs/tasks/TEMPLATE.md`、本任务文档和最终集成。
- `docs_inventory_audit` Agent 独占本任务内的 `docs/contracts/**`、`docs/interfaces/**`、`docs/runbooks/**`、`docs/tasks/README.md` 文档治理；不得修改主 Agent 文件。
- 其余并行 Agent 只读审计 Prompt 与调用图，不修改文件。
- 代码、配置和运行产物仅作为只读证据。

## 不可破坏合同

- V3 Reply 是唯一客户可见销售语义决策者；Router、视觉、门店解析、召回和恢复不得被描述为第二销售大脑。
- 模型负责语义、心理和表达；代码负责事实、工具、schema、幂等、安全与发送边界。
- 当前在线节点与 Shadow/后台节点必须分开说明；调用条件和模型配置不能混写。
- 文档不得保存真实客户信息、凭证或未经脱敏的模型输入输出。

## 已确认事实与证据

- 独立 worktree 基于最新 `origin/main@0e7f76e5`，初始 clean。
- 原工作区 `codex/deepseek-reply-evaluation` 有用户未提交文档及本地产物，本任务不读取为权威、不修改、不清理。
- `docs/tasks/active/INDEX.md` 在任务开始前为空，无其他文档所有权冲突。

## 已完成

- 已登记任务、分支、base SHA 和 `docs/**` 独占范围。
- 逐份盘点当前 27 个 Markdown 文档，以当前代码、服务器只读现场和历史 Git 记录区分稳定合同、动态事实与历史摘要。
- 重写文档索引、系统架构、运行边界、产品背景、当前开发状态、生产状态和已知问题；校正 V3、SOP、主动唤醒、回调、策略、身份、发布与评测口径。
- 删除已被现行合同吸收的 `docs/contracts/v3-intent-emotion-routing.md` 和重复的 `docs/runbooks/THIRD_PARTY_SOP_SINGLE_TASK_EXECUTION.md`，全仓没有残留引用。
- 新增 `docs/architecture/V3_MODEL_NODES_AND_PROMPTS.md`：列出同步 V3 Reply 全部在线模型用途、模型 tier、调用条件、逐字静态 Prompt、动态 Context、上下游、预算、重试/修复、退役节点、风险和一条完整虚构贯穿例子。
- 修复最终审计发现的身份边界、前后端发布口径、发送/送达统计、正常 V3 返回与主动发送边界、幂等前提、门店状态枚举和示例客户状态等文档矛盾。

## 待办

- 无。

## 测试结果

- V3 Prompt、质量门、主线动作、销售结构交付、门店和策略分析专项：`175 passed`。
- Global Contract、Router、门店目的地解析和最终 Reply 四个静态 Prompt 与源码逐字一致，字符数、行数和 SHA-256 一致；Vision 模板展开后逐字一致。
- Prompt 文档 12 个 JSON 代码块全部可解析；所有 Markdown 本地链接有效；两个删除文件全仓无残留引用。
- 修改内容未发现真实 token、私钥内容或客户身份/会话数据；`git diff --check` 通过。

## 发布与回滚

- 合入干净 `main`；本任务只修改文档，不构建 release、不重启服务、不部署生产。回滚只需回退文档提交。

## 待沉淀的长期结论

- V3 模型节点全景固定维护在 `docs/architecture/V3_MODEL_NODES_AND_PROMPTS.md`；任一在线 Prompt、模型 tier 或动态 Context 构造变化必须同步更新指纹与例子。
- `docs/current/` 只保留带核验时间的最新动态事实；历史发布只留任务索引一行并回查 Git。
