# V3 真人回复自然度治理

- status: active
- owner: Codex
- base_branch: `origin/main`
- base_sha: `6f5a23ce7cd0cd2bb91a1717434d8f3589c3b375`
- branch: `codex/v3-human-reply-naturalness`
- worktree: `E:\ai_code\vscode_codex\worktrees\v3-human-reply-naturalness`
- exclusive_scope: `ai_paths/app/prompts/reply_sales_prompt_v4.py`、`ai_paths/app/prompts/reply_synthesizer.py`、`ai_paths/app/policies/ai_sales_policy_v2.json`（仅 `normal_conversation`）、`docs/contracts/sales-strategy.md`、`docs/architecture/V3_MODEL_NODES_AND_PROMPTS.md`、本任务直接相关测试与隔离评测脚本、本任务文档、ignored `artifacts/v3-human-reply-naturalness/`
- production_verified_at: 未核验；本任务不涉及生产发布或线上状态判断
- production_releases: 未核验；禁止用文档快照推断当前生产
- data_model_send_authority: 允许隔离调用 DeepSeek；只使用脱敏或虚构输入；零真实客户发送、零生产写入、零 GPT fallback

## 目标

在不改变 V3 Reply 唯一销售决策权、不增加模型节点、不降低事实和交易安全的前提下，让短回应、关系承接、临时不可交流和重复暂缓可以自然短接；当前事实问题优先直接回答；只有时机自然时才衔接相邻销售机会。

## 非目标

- 不处理主动唤醒、第三方 SOP、生产发布或其他模块。
- 不修改 Router 决策权/schema、Reply admission、`next_sales_action` schema、表情渲染、fallback、生产 temperature 或生产配置。
- 不新增文案/情绪模型、风格状态、数据库迁移、发送能力或生产写入。
- 不合并或删除 `codex/v3-emoji-e2e-latency`；其提交 `0102ec498a87809f842c28148ef748a841a7b8f9` 仅作只读取证，不 cherry-pick。

## Change contract

- type: 回复质量、Prompt 和动态上下文治理
- scope: 最终 Reply Prompt、必要的上下文渲染顺序、`normal_conversation` 合同澄清、对应测试和隔离评测
- risk: 软拒绝被过度放松；明确行动请求未当轮完成；主线越级、交易事实或安全边界回归；Prompt 膨胀；实验变量混杂
- validation: L1 确定性合同；相同 DeepSeek、输入与 temperature 的 L2 对照；隔离 L3；销售主管至少 50 条盲审由母窗口/业务完成
- rollback: 仅回退本任务 Prompt、上下文、合同和测试提交；无数据库或不可逆数据变更

## 涉及模块与文件所有权

允许修改：

- `ai_paths/app/prompts/reply_sales_prompt_v4.py`
- `ai_paths/app/prompts/reply_synthesizer.py`
- `ai_paths/app/policies/ai_sales_policy_v2.json`，仅澄清 `normal_conversation`
- `docs/contracts/sales-strategy.md`
- `docs/architecture/V3_MODEL_NODES_AND_PROMPTS.md`
- 与本任务直接相关的测试及必要隔离评测脚本
- `docs/tasks/active/INDEX.md` 与本任务文档
- ignored `artifacts/v3-human-reply-naturalness/`

旧 `v3-emoji-e2e-latency` worktree 当前干净、已提交且被本任务包明确冻结为只读取证；若其恢复并发编辑上述文件，立即停止本任务修改并报告所有权冲突。

## 不可破坏合同

- 正常 V3 仍为一次 Router、必要事实工具、一次 Reply；不得新增模型节点。
- `next_sales_action` 仍必填，记录本轮实际落实动作；`keep_open` 是合法动作。
- 明确退订、医疗风险、严重客诉、退款、已付服务、价格、素材、门店、预约和付款边界零回归。
- `next_missing_stage` 只表示下一项机会与越级边界，不要求本轮机械执行。
- 明确价格、案例、地址、付款入口或预约请求在事实和权限齐全时仍须当轮完成。

## 已确认事实与证据

- 执行前重新 fetch 后最新远端为 `origin/main@6f5a23ce7cd0cd2bb91a1717434d8f3589c3b375`，与任务包规划值一致。
- 当前原窗口位于 dirty `codex/deepseek-reply-evaluation@531ddc7e`，未触碰其用户修改。
- 新 worktree 从干净 `origin/main` 创建；旧 `codex/v3-emoji-e2e-latency@0102ec49` 保留为只读取证。
- `main` worktree 只有未跟踪 `output/`，本任务不清理、不复用。
- 冻结基线 Reply Prompt：`6999` 字符、`62` 行、SHA-256 `3173b03054620b48dfd699a6af3d8ddfd51581e95fb1d8f7bcf0d3f03e18d642`。
- 冻结基线默认 Reply temperature：`0.15`（`Settings.v3_reply_temperature`）；本任务不修改该默认值。
- 冻结基线动态顺序：当前时间 → 输出上限 → 销售动作硬合同 → 完整聊天 → 结构事实 → 执行能力 → 策略/上一状态/逼单候选 → 协议/付款/已付 → 工具事实 → 必须遵守 → Router → 序列话术 → 权威事实 → 素材 → 结构消息 → 缺失权限 → 引用边界。

## 已完成

- 完整读取任务要求指定的宪法、索引、current、任务流程、销售策略、Reply admission 和 V3 模型/Prompt 全景。
- 核验分支、HEAD、dirty、最新 `origin/main`、所有 worktree 及可见 active task 范围。
- 创建并登记独立分支/worktree。
- 冻结 main Prompt、动态区块顺序和默认 Reply temperature，并把基线写入 ignored artifact。
- 建立 60 条脱敏/虚构 L2 矩阵：12 条短回应/关系承接、10 条临时不可交流、12 条首次/重复软拒绝、12 条明确动作、8 条 Router/培训稿污染、6 条硬安全；其中 20 条重复探针、28 条 L3 生命周期标记。
- 在不改变 schema、admission、fallback、模型节点和生产 temperature 的前提下，重构 Reply Prompt 与动态区块顺序，并仅澄清 `normal_conversation` 合同。
- 最终候选 Prompt 为 `6,983` 字符、`72` 行、SHA-256 `963fe12f35f2c0f47eaab5c3bf568f660e44832203d947f3d13b975f99b6164b`；逐字快照和动态顺序已同步到架构文档。
- 完成 L1、L2 和隔离生命周期尾部回放；最终证据来自零模型错误的 `final-l2-v5`，原始模型结果只保存在 ignored `artifacts/v3-human-reply-naturalness/`。

## 待办

- 销售主管对不少于 50 条、隐藏版本标识的客户可见回复做独立盲审；已在 ignored `artifacts/v3-human-reply-naturalness/final-l2-v4/blind_review_50.csv` 生成 50 条随机 A/B 审核包，答案键单独存放为 `blind_review_key.json`。当前环境没有业务盲审人，不能由开发/模型自评替代。
- 如需宣称“全图 L3”，另行在获准的平台/知识只读环境中运行 Router、必要事实工具和 Reply；本任务已完成的是 28 条真实 DeepSeek 输出的隔离持久化/响应/finalization 尾部，不把回放误报为 Router/工具全图。
- 盲审达标后才能把本候选标记为可合并；本任务不合并 `main`、不发布。

## 测试结果

- L1 targeted：`118 passed`。
- L1 全仓：`861 passed, 9 warnings`；warning 为既有 Authlib 与 SQLAlchemy deprecation。
- L2 最终对照：`artifacts/v3-human-reply-naturalness/final-l2-v5/`（ignored）。共 260 次调用，固定 `deepseek-chat`，fallback index 仅 `0`，全部零模型错误。
- L2 主候选（候选 Prompt + 候选顺序）：60/60 可解析且通过，硬安全 6/6，明确动作 12/12，无关销售插入 0/22，内部术语泄漏 0，P50/P95 `1865/2418 ms`。
- L2 Prompt 对照（相同基线顺序）：无关销售插入由基线 `14/22` 降至候选 `2/22`，相对下降 `85.71%`；候选硬安全 6/6、明确动作 12/12、零模型错误。
- L2 顺序对照：同一候选 Prompt 下，无关销售插入由 `2/22` 降至 `0/22`；候选顺序 60/60 通过。
- L2 重复探针：20 个场景各 3 次，共 60 代；硬安全失败 0，schema 波动 2 个场景、动作波动 2 个场景，无重复开场。temperature 0.30 和 0.45 均为 20/20；两者只在代表性子集通过，未证明优于完整 60 场景的默认 0.15，因此生产默认仍为 `0.15`。
- L3 生命周期尾部：`artifacts/v3-human-reply-naturalness/final-l3-replay-v5/`（ignored）。28/28 完成 ChatRuntime、临时 SQLite、HTTP response、durable replay 和 finalization；28/28 durable payload 一致，finalization 28/28，真实消息派发 0，策略外发 0。来源 Reply 均为 L2 `deepseek-chat`；该步新模型调用 0、Router/工具调用 0。
- 销售主管盲审：50 条随机 A/B 审核包已生成，类别分布为短回应 9、临时不可交流 10、软拒绝 8、明确动作 10、Router 污染 7、硬安全 6；尚未由销售主管评分。由于完整 Router/工具 L3 与业务盲审都未完成，候选状态是“L1/L2 与生命周期尾部通过、完整门禁未放行”，不能宣称最终通过或可发布。

## 发布与回滚

- final_main_commit: 不适用；本任务禁止合并 `main`
- deployed: no

## 待沉淀的长期结论

- `keep_open/normal_conversation` 对关系承接、短回应、临时不可交流和重复暂缓的稳定适用边界。
- `next_missing_stage` 作为机会/越级边界而非每轮强制动作的 Prompt 表达。
