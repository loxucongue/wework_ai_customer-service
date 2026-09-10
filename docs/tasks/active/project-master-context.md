# project-master-context

- status: active
- owner: Codex
- base_branch: main
- base_sha: `6f6fb4a9e1fded159a93c03867f227adb3b0f5bf`
- production_verified_at: not-required（文档与协作治理任务，不发布生产）
- branch: `codex/project-master-context`
- worktree: `E:\ai_code\vscode_codex\worktrees\project-master-context`

## 目标

1. 将长期聊天中已经确认、但尚未形成统一入口的产品决策和协作知识收敛到公共文档。
2. 建立一个给项目母窗口直接读取的当前上下文，避免从历史聊天重新推断目标、边界和进度。
3. 建立母窗口讨论/审批、子窗口独立执行、集成窗口统一审核合并的协作合同。
4. 创建新的项目母任务，并以公共文档而不是本窗口历史作为启动上下文。

## 非目标

- 不把聊天逐字稿、真实客户日志、模型原始输出、Token、密钥或一次性测试产物写入 Git。
- 不修改 V3 运行代码、Prompt、数据库、配置或生产服务。
- 不在文档中复制可由现行合同链接表达的全部实现细节。

## Change contract

- type: 项目知识与多窗口协作治理
- scope: `AGENTS.md`、`docs/INDEX.md`、`docs/current/MASTER_CONTEXT.md`、`docs/tasks/PARENT_CHILD_WORKFLOW.md`、相关任务协作文档
- risk: 把过期讨论误写成当前事实；母窗口与执行窗口职责重叠；复制敏感客户或生产信息；文档重复膨胀
- validation: 与当前合同/代码状态交叉核对、Markdown 链接、敏感信息、重复事实和 `git diff --check`
- rollback: 回退文档提交；不涉及数据或生产回滚

## 文件所有权

- 本任务独占上述文档范围。
- 其他工作区和用户未提交内容只读识别，不修改、不清理、不合并。

## 不可破坏合同

- `main` 和现行代码仍是实现事实；母窗口摘要不能覆盖专项合同。
- 母窗口不直接承载多个实现任务；每个子窗口仍需独立 task、branch/worktree、所有权和验证。
- 客户原文、原始日志、模型输出和测试报告不得进入公共文档。
- 动态生产状态必须现场核验，不能把聊天记忆当作当前线上事实。

## 已完成

- 从最新 `origin/main@6f6fb4a9` 创建独立分支和 worktree。
- 登记任务、分支、base SHA 和独占范围。
- 新增 `docs/current/MASTER_CONTEXT.md`，收敛产品北极星、已确认业务决策、当前优先级、外部依赖和母窗口启动指令。
- 新增 `docs/tasks/PARENT_CHILD_WORKFLOW.md`，固定母窗口、执行子窗口、评测窗口和集成窗口的职责、任务包、权限与回报格式。
- 更新项目宪法、文档索引、任务说明和执行工作流，使所有新窗口先读取母窗口上下文。
- 纠正主动唤醒看板合同中的结果解释边界，避免把通用订单终态阻断误写为预约/支付归因能力。

## 待办

- 提交、合入 main，并创建新的项目母任务。

## 测试结果

- 与 `DEVELOPMENT_STATUS`、`KNOWN_ISSUES`、销售策略、Reply 质量门、主动唤醒和生产状态文档交叉核对通过。
- Markdown 相对链接检查通过，缺失链接 `0`。
- `git diff --check` 通过。
- 变更文档敏感信息扫描通过；未写入 Token、密钥、服务器地址、真实客户身份、原始日志或模型输出。

## 发布与回滚

- 仅文档合入 main，不部署生产。
