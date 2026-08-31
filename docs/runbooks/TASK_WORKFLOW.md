# 一任务一窗口工作流

## 开始

1. 从最新 `origin/main` 创建干净工作区；先检查所有 dirty 文件。
2. 复制 `docs/tasks/TEMPLATE.md` 为 `docs/tasks/active/<task-id>.md`。
3. 写明 change contract、base SHA、线上基线、范围和不可破坏合同。
4. 新窗口只需指定该任务文件，不复制历史聊天。

## 执行

- 每次重要结论立即写入任务文件的“证据/决策”，不要依赖聊天记忆。
- 需要并行时，主 Agent 分配互斥目录；子 Agent 不提交、不部署，主 Agent 集成。
- 动态事实现场核验；历史文档只能作为线索。
- 运行产物写 `artifacts/<task-id>/`，不进 Git。

## 完成

1. 运行合同测试和任务相关测试，记录命令与结果。
2. 合并到 `main`，确认 `dirty=false`，再构建 release。
3. 部署后验证 V3、API、worker、回调和管理页，并记录回滚点。
4. 将长期规则沉淀到 `contracts/` 或 ADR，更新 `current/PRODUCTION_STATE.md`。
5. 删除活动任务文件；删除已合并的临时分支/worktree；按 TTL 清理 artifacts。

## 磁盘策略

- artifacts、浏览器产物、reports、results：默认保留 7 天。
- 本地部署包：最近 3 个或 14 天，以更小集合为准。
- 生产 releases：至少保留当前和一个已验证回滚版本；其余先归档再删除。
- 不自动清理 dirty worktree；先生成文件、大小、mtime、hash 清单并人工确认。
