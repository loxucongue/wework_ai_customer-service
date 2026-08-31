# 文档索引

本页是工程知识的唯一入口。不要以旧 handoff、聊天记录或文件名日期判断当前行为。

## 新窗口阅读路由

1. 仓库宪法：[`AGENTS.md`](../AGENTS.md)
2. 系统结构：[`architecture/SYSTEM.md`](architecture/SYSTEM.md)
3. V3 迁移方案：[`architecture/V3_CONSOLIDATION_PLAN.md`](architecture/V3_CONSOLIDATION_PLAN.md)
4. 运行边界：[`contracts/RUNTIME_BOUNDARIES.md`](contracts/RUNTIME_BOUNDARIES.md)
5. 第三方 SOP：[`contracts/third-party-sop-v3.md`](contracts/third-party-sop-v3.md)
6. 线上状态：[`current/PRODUCTION_STATE.md`](current/PRODUCTION_STATE.md)
7. 已知问题：[`current/KNOWN_ISSUES.md`](current/KNOWN_ISSUES.md)
8. 当前任务：[`tasks/active/v3-only-consolidation.md`](tasks/active/v3-only-consolidation.md)

## 目录职责

- `architecture/`：稳定的组件关系和数据流。
- `contracts/`：不得被实现随意破坏的接口与业务边界。
- `adr/`：不可逆或影响范围大的架构决策及原因。
- `runbooks/`：可执行的开发、测试、部署、回滚和事故流程。
- `current/`：带验证时间的动态状态，不是永久事实。
- `tasks/active/`：当前任务的最小交接；完成后删除并沉淀长期结论。
- `testing/`：测试策略和场景说明，不存运行报告。

## 文档规则

- 当前事实只能有一个 canonical 文档；旧日期快照不得与当前合同并存。
- 每份合同/current 文档标注 `status`、`owner`、`last_verified`、`source_of_truth`。
- 测试报告、截图、日志、调试 JSON 和模型输出写入 ignored 的 `artifacts/`。
- 业务知识素材迁移到 `resources/knowledge/` 后由检索链管理。
