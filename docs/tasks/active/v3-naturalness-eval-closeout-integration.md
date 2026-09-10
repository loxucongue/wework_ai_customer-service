# V3 自然度评测资产选择性集成与关闭

- status: active
- owner: integration child window
- base_branch: `origin/main`
- base_sha: `afb261c0480811c89fd58f98c3c778169303f287`
- branch: `codex/v3-naturalness-eval-closeout-integration`
- worktree: `E:\ai_code\vscode_codex\worktrees\v3-naturalness-eval-closeout-integration`
- source_evidence: `codex/v3-human-reply-naturalness@ecb2f149f9cd30e46c5091e5d1ffac0828af62e5`
- production_verified_at: 不适用；本任务不判断或修改线上状态

## 背景

`v3-human-reply-naturalness` 已完成受控消融，没有找到同时降低无关插入且不损伤主动销售的 Prompt 候选。正式 Reply 行为已恢复为当前 `main`，失败 Prompt 只能保留为分支取证。该分支仍沉淀了可复用的隔离评测能力和已确认的销售性软承接合同，需要从干净 `main` 选择性集成并关闭原任务。

## 目标与预期效果

- 保留可复用的 V3 自然度完整图 HTTP、幂等重放、finalization 和受控消融能力。
- 保留“销售性软承接不等于交易完成态”的长期产品合同。
- `main` 的正式 Reply Prompt、上下文、模型参数和运行行为保持不变。
- 原自然度任务进入 history，active ownership 被释放。

## 非目标

- 不继续优化或评测新的 Prompt 候选。
- 不直接合并、rebase 或整分支 cherry-pick 来源分支。
- 不修改 Reply、Router、素材、付款、门店、数据库、生产配置或模型参数。
- 不调用真实模型，不执行生产写入、客户发送或部署。
- 不在本任务修复 `s10_activity_intro` 混入 `payment_collection`；该问题另行立项。

## 模块边界与 ownership

- 允许：V3 自然度评测脚本、其直接确定性测试、销售策略合同中的软承接边界、原任务关闭及 history/index 文档。
- 禁止：`ai_paths/app/prompts/`、`ai_paths/app/graph/`、`ai_paths/app/services/`、`ai_paths/app/policies/`、迁移、生产状态和部署文件。
- 执行窗口先只读比较来源分支与最新 `main`，在本文件补充最终精确文件集后即可在上述范围内继续；无需为范围内每个文件再次请求母窗口。
- 若发现必须跨越禁止范围、碰撞其他 active task 或改变运行行为，立即停止并回报。

## 不可破坏合同

- V3 Reply 仍是唯一销售语义决策点，不新增模型节点或业务 fallback。
- “这个活动给你留着”“给您安排上”等是允许的销售性软承接；明确预约、登记、锁定、排客或到账完成态仍以权威事实为准。
- 原始模型输出、完整日志、CSV 和评测报告只保存在 ignored `artifacts/`，不得进入 Git。
- 来源分支中的失败 Prompt 提交不得成为 `main` 产品行为。

## 验收

- 最终 diff 只包含评测基础设施、直接测试、确认后的销售合同和任务关闭文档。
- 与 `origin/main` 比较，正式 Prompt、动态上下文、运行配置和产品代码零差异。
- 消融工具必须显式接收 baseline ref，并在报告中保存运行开始时解析的完整 SHA。
- 相关测试和全量确定性测试通过，`git diff --check` 通过，工作区干净。
- 在 `docs/tasks/history/INDEX.md` 添加原任务一行摘要，从 active INDEX 移除原任务并删除其 active 文件；本集成任务在完成验收后按相同规范关闭。
- 只提交并推送集成候选，等待母窗口复审；不得自行合入 `main` 或部署。

## 回滚

本任务不含运行时行为和数据迁移。若选择性提取不干净或测试失败，放弃集成候选即可，`main` 保持不变。

## 执行窗口只读取证与 change contract

- 现场基线：`origin/main@aeec0653a3a0a46e92f54dc92ed4cb20389679c2`，独立分支和 worktree 初始均 clean；来源只读提交 `ecb2f149f9cd30e46c5091e5d1ffac0828af62e5` 可达且不合并、不 rebase、不 cherry-pick。
- 类型：无产品行为变化的评测基础设施选择性集成与任务关闭。
- 最小文件集：`ai_paths/scripts/evaluate_v3_naturalness_full_graph.py`、`ai_paths/scripts/evaluate_v3_reply_naturalness_ablation.py`、`tests/test_v3_human_reply_naturalness.py` 中 3 个直接测试、`docs/contracts/sales-strategy.md` 中 2 条软承接边界、`docs/tasks/active/INDEX.md`、`docs/tasks/history/INDEX.md`，以及删除原任务和本集成任务的 active 文档。
- 冲突检查：active INDEX 中原自然度任务已冻结为只读取证，本集成任务独占上述评测/合同/关闭范围；没有其他 active ownership。禁止路径 `ai_paths/app/prompts/`、`ai_paths/app/graph/`、`ai_paths/app/services/`、`ai_paths/app/policies/`、生产配置、迁移和部署文件均不修改。
- 风险：来源分支包含失败 Prompt 历史，不能按提交整合；只按最终树逐文件/逐段提取，并以 `git diff --name-only` 和正式 Reply 文件零差异证明隔离。评测脚本会导入生产组件但只供显式隔离运行，不改变运行入口或线上副作用。
- 验证：相关 Ruff/pytest、全量确定性测试、`git diff --check`；确认消融 CLI 强制 `--baseline-ref` 且报告含完整 `baseline_sha`；确认最终 diff 无正式 Reply、Router、配置、数据库或部署文件。
- 回滚：候选不合入 main 即可完整回滚；没有数据库、配置或生产状态变更。
