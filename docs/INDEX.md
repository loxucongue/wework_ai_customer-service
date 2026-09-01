# 文档索引

本页是工程知识的唯一入口。新任务不从旧聊天、旧 handoff、分支名称或文件日期推断当前行为。

## 新任务必读

1. [项目宪法](../AGENTS.md)
2. [系统结构](architecture/SYSTEM.md)
3. [运行边界](contracts/RUNTIME_BOUNDARIES.md)
4. [第三方 SOP V3 合同](contracts/third-party-sop-v3.md)
5. [AI 销售策略运行合同](AI_SALES_STRATEGY_RUNTIME_AND_TEST_CONTRACT.md)
6. [当前生产状态](current/PRODUCTION_STATE.md)
7. [已知问题](current/KNOWN_ISSUES.md)

当前没有未完成的仓库迁移任务。新开发应按“一项任务一个新窗口、从最新 `origin/main` 建立临时 `codex/*` 分支、验证后立即合回并删除”的方式执行。

## 专项文档

- [AI 策略数据源与发布](AI_SALES_POLICY_INTEGRATION.md)
- [消息送达回调](platform_message_delivery_callback_integration.md)
- [MySQL 切换手册](aics_mysql_cutover_runbook.md)
- [任务工作流](runbooks/TASK_WORKFLOW.md)
- [质量门禁与测试治理](quality/QUALITY_GATES.md)
- [生产发布前检查清单](runbooks/PRE_RELEASE_CHECKLIST.md)

## 目录规则

- `architecture/`：稳定组件关系，不记录临时进度。
- `contracts/`：不得被实现随意破坏的协议和业务边界。
- `current/`：现场核验后的动态事实；过期时必须明确标记。
- `tasks/active/`：只保留一个当前任务，完成后删除并把长期结论沉淀到合同。
- `runbooks/`：可执行的测试、部署、回滚和事故流程。
- 运行报告、截图、调试 JSON、构建产物和模型输出不得提交到 `docs/`。
- 历史设计、已退役 V1/V2 测试与重复知识文件不留在当前树；需要追溯时使用 Git 历史。
