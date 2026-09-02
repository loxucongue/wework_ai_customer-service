# 文档索引

本页是工程知识的唯一入口。新任务不从旧聊天、旧 handoff、分支名称或文件日期推断当前行为。

## 新任务必读

1. [项目宪法](../AGENTS.md)
2. [系统结构](architecture/SYSTEM.md)
3. [运行边界](contracts/RUNTIME_BOUNDARIES.md)
4. [第三方 SOP V3 合同](contracts/third-party-sop-v3.md)
5. [AI 销售策略运行合同](contracts/sales-strategy.md)
6. [当前生产状态](current/PRODUCTION_STATE.md)
7. [已知问题](current/KNOWN_ISSUES.md)

## 共享任务区

`docs/` 是所有窗口共享的项目记忆，不记录聊天、原始日志、模型输出或测试报告。

- [任务规则](tasks/README.md)
- [活跃任务清单](tasks/active/INDEX.md)
- [历史任务索引](tasks/history/INDEX.md)
- [任务模板](tasks/TEMPLATE.md)

新开发按“一项任务一个新窗口、从最新 `origin/main` 建立临时 `codex/*` 分支、验证后立即合回并删除”的方式执行。

## 专项文档

- [消息送达回调](contracts/message-delivery-callback.md)
- [任务工作流](runbooks/TASK_WORKFLOW.md)
- [生产发布前检查清单](runbooks/PRE_RELEASE_CHECKLIST.md)

## 目录规则

- `architecture/`：稳定组件关系，不记录临时进度。
- `contracts/`：不得被实现随意破坏的协议和业务边界。
- `current/`：现场核验后的动态事实；过期时必须明确标记。
- `tasks/active/`：每个窗口独占一个活跃任务文件，任务、分支和文件所有权必须先登记。
- `tasks/history/`：只保存已完成任务的简短索引，不保存聊天、报告或重复设计文档。
- `runbooks/`：可执行的验证、部署、回滚和事故流程。
- 运行报告、截图、调试 JSON、构建产物和模型输出不得提交到 `docs/`。
- 历史设计、已退役 V1/V2 测试与重复知识文件不留在当前树；需要追溯时使用 Git 历史。
