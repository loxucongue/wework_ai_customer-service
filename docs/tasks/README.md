# 任务文档规则

`docs/tasks/` 只管理任务边界和可复用结论，不保存聊天记录、原始日志、截图、模型输出或测试报告。

```text
docs/tasks/
├── TEMPLATE.md          新任务模板
├── PARENT_CHILD_WORKFLOW.md  母窗口讨论、子窗口执行与统一集成规范
├── active/
│   ├── INDEX.md         活跃任务与文件所有权清单
│   └── <task-id>.md     单个窗口独占的任务说明
└── history/
    └── INDEX.md         已完成任务的简短可追溯索引
```

规则：

1. 项目母窗口只讨论产品、拆分任务和验收结论，不直接堆叠实现；每个开发、诊断、评测、集成或发布子窗口对应一个 `<task-id>.md`。详见 [母子窗口协作规范](PARENT_CHILD_WORKFLOW.md)。
2. 例行低风险任务可直接基于最新干净 `main`；用户明确要求、并行或高风险任务使用 `codex/<task-id>` 独立分支和 worktree。
3. 开始前由主 Agent 在 `active/INDEX.md` 登记任务、负责人、实际分支/worktree 和独占文件范围；并行任务不得编辑同一业务文件。
4. 任务文档只记录目标、边界、决定、验证和待办。动态线上事实仍需在发布前现场核验。
5. 临时分支只作为隔离载体，完成后由指定集成窗口审核并合入 `main`；禁止从临时分支、detached HEAD 或 dirty worktree 直接部署。
6. 合并后，把长期规则写进 `contracts/` 或 ADR；在 `history/INDEX.md` 留一行任务和提交，然后删除对应活跃任务文件。
7. 历史索引不是第二套 Git：需要代码差异、完整过程或旧实现时，直接查 Git 提交。
