# 一任务一窗口工作流

母窗口、执行子窗口和集成窗口的职责、任务包和回报格式见 [母窗口与子窗口协作规范](../tasks/PARENT_CHILD_WORKFLOW.md)。本页只描述单个执行任务从开始到关闭的工程流程。

## 开始

1. 先同步并核验最新 `origin/main`，检查目标工作区、其他 worktree 和所有 dirty 文件；用户已有改动不得擅自清理或覆盖。
2. 例行、低风险工作可直接基于最新干净 `main`；用户明确要求、并行开发、高风险变更或需要隔离验证时，创建 `codex/<task-id>` 分支和独立 worktree。不得从 detached HEAD、旧功能分支或 dirty 工作区开始。
3. 复制 `docs/tasks/TEMPLATE.md` 为 `docs/tasks/active/<task-id>.md`，并由主 Agent 登记到 `docs/tasks/active/INDEX.md`。登记实际 branch/worktree；直接基于 main 时也要明确写出。
4. 写明 change contract、base SHA、线上基线、范围、独占文件和不可破坏合同。
5. 新窗口读取母窗口上下文和该任务文件，不复制历史聊天。

## 执行

- 每次重要结论立即写入任务文件的“证据/决策”，不要依赖聊天记忆。
- 需要并行时，母窗口或任务主 Agent 分配互斥文件或目录；任务内子 Agent 不提交、不部署，指定集成窗口是该批次唯一集成人。
- 动态事实现场核验；历史文档只能作为线索。
- 运行产物写 `artifacts/<task-id>/`，不进 Git。

## 完成

1. 按本次改动建立并执行最小验证，记录命令、范围与结果；不复用或提交历史测试资产。
2. 临时任务分支经主 Agent 审核后合并到干净 `main`；直接基于 main 的任务也必须确认提交完整且 `dirty=false`，再构建 release。
3. 部署后验证 V3、API、worker、回调和管理页，并记录回滚点。
4. 将长期规则沉淀到 `contracts/` 或 ADR，更新 `current/PRODUCTION_STATE.md`。
5. 在 `docs/tasks/history/INDEX.md` 增加一行任务、主提交和长期结论链接；删除活动任务文件；删除已合并的临时分支/worktree；按 TTL 清理 artifacts。

## 磁盘策略

- artifacts、浏览器产物、reports、results：默认保留 7 天。
- 本地部署包：最近 3 个或 14 天，以更小集合为准。
- 生产 releases：至少保留当前和一个已验证回滚版本；其余先归档再删除。
- 不自动清理 dirty worktree；先生成文件、大小、mtime、hash 清单并人工确认。
