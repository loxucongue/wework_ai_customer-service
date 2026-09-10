# 文档索引

本页是工程共享知识的唯一入口。代码是运行事实，`current/` 是带核验时间的现场快照，Git 是历史；不要从旧聊天、分支名或文件日期推断当前行为。

## 新任务阅读顺序

1. [项目宪法](../AGENTS.md)
2. [项目母窗口上下文](current/MASTER_CONTEXT.md)
3. [产品背景与统一目标](background/PRODUCT_CONTEXT.md)
4. [当前开发进度](current/DEVELOPMENT_STATUS.md)
5. [系统结构](architecture/SYSTEM.md)
6. [运行版本边界](contracts/RUNTIME_BOUNDARIES.md)
7. 与任务相关的合同和[接口文档](interfaces/INDEX.md)
8. 涉及线上时读取并重新核验[生产状态](current/PRODUCTION_STATE.md)
9. [已知问题](current/KNOWN_ISSUES.md)
10. [活跃任务清单](tasks/active/INDEX.md)及本窗口唯一任务文档
11. 需要服务器入口时读取[访问提示](current/ACCESS_HINTS.md)

## 产品、架构与决策

- [产品背景与统一目标](background/PRODUCT_CONTEXT.md)
- [系统结构](architecture/SYSTEM.md)
- [V3 大模型节点与 Prompt 全景](architecture/V3_MODEL_NODES_AND_PROMPTS.md)：当前全部在线模型、逐字 Prompt、动态上下文、重试/修复和完整虚构例子
- [ADR-0001：V3-only 与单一 main](adr/0001-v3-only-single-main.md)

## 稳定合同

- [运行版本边界](contracts/RUNTIME_BOUNDARIES.md)
- [V3 Reply 运行时质量门](contracts/v3-reply-admission.md)
- [AI 销售策略：意图、情绪、卡点、跟进与 B 单](contracts/sales-strategy.md)
- [客户身份](contracts/customer-identity.md)
- [消息送达回调](contracts/message-delivery-callback.md)
- [第三方 SOP V3](contracts/third-party-sop-v3.md)
- [主动唤醒 BI 观测](contracts/outreach-analytics.md)

## 接口

- [接口文档索引](interfaces/INDEX.md)
- [AI Paths 调用的外部依赖](interfaces/external.md)
- [外部系统和管理端调用 AI Paths](interfaces/public.md)

接口文档不保存 token、生产白名单或动态目录内容；精确 schema 以当前代码和专项合同为准。

## 当前状态

- [项目母窗口上下文](current/MASTER_CONTEXT.md)：已确认产品决策、当前优先级、外部依赖和母窗口启动指令
- [当前开发进度](current/DEVELOPMENT_STATUS.md)
- [当前生产状态](current/PRODUCTION_STATE.md)
- [当前已知问题](current/KNOWN_ISSUES.md)
- [访问提示](current/ACCESS_HINTS.md)

`current/` 只保留最新事实，不堆发布流水。任何现场值超过核验时间后都必须重新检查。

## 标准与运行手册

- [V3 Reply 质量与全链路评测规范](standards/V3_REPLY_EVALUATION.md)
- [任务工作流](runbooks/TASK_WORKFLOW.md)
- [生产发布前检查清单](runbooks/PRE_RELEASE_CHECKLIST.md)

第三方 SOP 的状态机与运维核验已统一到其合同，不再维护第二份单任务手册。

## 任务协作

- [任务规则](tasks/README.md)
- [母窗口与子窗口协作规范](tasks/PARENT_CHILD_WORKFLOW.md)
- [活跃任务清单](tasks/active/INDEX.md)
- [任务模板](tasks/TEMPLATE.md)
- [历史任务索引](tasks/history/INDEX.md)

`docs/` 是所有窗口共享的项目记忆，不是运行产物目录：

- 产品背景、架构、合同、接口和标准只写稳定结论。
- 生产 release、开关、队列和回滚点只写最新核验快照。
- 活跃任务先登记目标、base、分支/worktree 和文件所有权，完成后删除任务文档并留一行历史摘要。
- 客户原文、原始日志、截图、模型输出、调试 JSON、测试报告和构建产物只进入 ignored `artifacts/`。
- 已退役设计和重复文档从当前树删除；需要追溯时使用 Git。
