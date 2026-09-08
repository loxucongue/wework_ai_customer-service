# 文档索引

本页是工程知识的唯一入口。新任务不从旧聊天、旧 handoff、分支名称或文件日期推断当前行为。

## 新任务必读

1. [项目宪法](../AGENTS.md)
2. [产品背景与统一目标](background/PRODUCT_CONTEXT.md)
3. [当前开发进度](current/DEVELOPMENT_STATUS.md)
4. [系统结构](architecture/SYSTEM.md)
5. [运行边界](contracts/RUNTIME_BOUNDARIES.md)
6. 与本任务相关的合同和[接口文档](interfaces/INDEX.md)
7. [当前生产状态](current/PRODUCTION_STATE.md)
8. [已知问题](current/KNOWN_ISSUES.md)
9. [活跃任务清单](tasks/active/INDEX.md)及自己的任务文档
10. [访问提示](current/ACCESS_HINTS.md)

## 文档类别

- `background/`：产品背景、业务目标和统一术语；帮助不了解项目的人先理解“为什么做”。
- `architecture/`：组件、数据流和运行机制；说明“系统如何连接”。
- `contracts/`：不可被实现随意破坏的产品、数据和安全边界。
- `standards/`：开发、评测和验收口径；说明“怎样才算完成”。
- `interfaces/`：外部依赖接口和项目对外接口，不保存凭证。
- `current/`：当前进度、生产现场事实和已知问题；动态内容必须注明核验时间。
- `tasks/active/`：当前窗口任务、分支、文件所有权、证据和待办。
- `tasks/history/`：已完成任务的一行索引，详细过程回查 Git。
- `runbooks/`：部署、验证、回滚和事故操作流程。

## 共享任务区

`docs/` 是所有窗口共享的项目记忆，不记录聊天、原始日志、模型输出或测试报告。

- [任务规则](tasks/README.md)
- [活跃任务清单](tasks/active/INDEX.md)
- [历史任务索引](tasks/history/INDEX.md)
- [任务模板](tasks/TEMPLATE.md)
- [接口文档索引](interfaces/INDEX.md)
- [外部依赖接口](interfaces/external.md)
- [对外暴露接口](interfaces/public.md)

新开发按“一项任务一个新窗口、从最新 `origin/main` 建立临时 `codex/*` 分支、验证后立即合回并删除”的方式执行。

## 专项文档

- [V3 Reply 质量与全链路评测规范](standards/V3_REPLY_EVALUATION.md)
- [AI 销售策略运行合同](contracts/sales-strategy.md)
- [V3 意图、情绪与路由合同](contracts/v3-intent-emotion-routing.md)
- [消息送达回调](contracts/message-delivery-callback.md)
- [客户身份合同](contracts/customer-identity.md)
- [第三方 SOP V3 合同](contracts/third-party-sop-v3.md)
- [第三方 SOP 单任务执行与失败预警](runbooks/THIRD_PARTY_SOP_SINGLE_TASK_EXECUTION.md)
- [主动唤醒 BI 观测合同](contracts/outreach-analytics.md)
- [任务工作流](runbooks/TASK_WORKFLOW.md)
- [生产发布前检查清单](runbooks/PRE_RELEASE_CHECKLIST.md)

## 目录规则

- `architecture/`：稳定组件关系，不记录临时进度。
- `background/`：稳定业务背景和产品目标，不记录上线快照。
- `contracts/`：不得被实现随意破坏的协议和业务边界。
- `standards/`：开发与验收的统一定义，不保存一次性测试结果。
- `interfaces/`：外部依赖接口和项目对外暴露接口的稳定索引，不保存 token、原始日志或动态生产状态。
- `current/`：现场核验后的动态事实；过期时必须明确标记。
- `tasks/active/`：每个窗口独占一个活跃任务文件，任务、分支和文件所有权必须先登记。
- `tasks/history/`：只保存已完成任务的简短索引，不保存聊天、报告或重复设计文档。
- `runbooks/`：可执行的验证、部署、回滚和事故流程。
- 运行报告、截图、调试 JSON、构建产物和模型输出不得提交到 `docs/`。
- 历史设计、已退役 V1/V2 测试与重复知识文件不留在当前树；需要追溯时使用 Git 历史。
