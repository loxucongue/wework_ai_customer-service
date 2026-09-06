# shared-project-status-sync

- status: active
- owner: Codex 主窗口
- base_branch: main
- base_sha: `007bf2c88c1405bc74c4e13320b0e27f667673c4`
- production_verified_at: `2026-09-06T16:04:08+08:00`
- production_releases: `control/reply/worker=ai-paths-unified-20260906-154524-007bf2c8@007bf2c8`

## 目标

- 将当前 V3 开发目标、已上线能力、销售策略链路、数据观测、沉默唤醒、近期门店回复修复和已知测试缺口同步到共享文档。
- 让新窗口只需从 `docs/INDEX.md`、当前状态、合同、接口与本任务文档即可获得一致上下文。
- 明确哪些事实已上线、哪些仍是 shadow/临时配置、哪些结论必须再次验证。

## 非目标

- 不修改 V3 Reply、Router、门店、策略、SOP、outreach 或前端代码。
- 不执行客户消息发送、生产数据写入或模型效果评测。
- 不把客户原文、请求日志、模型输出、Token、密码、服务器私钥路径或运行报告写入 Git。

## Change contract

- type: documentation / shared project state
- scope: 共享文档索引、背景与架构、规范合同、接口说明、动态状态和任务状态
- risk: 把历史记录误写成当前生产事实；文档重复或口径冲突；泄露运行凭证或客户信息
- validation: 现场核验生产 release；逐项对照当前 main 代码与近期合并记录；检查链接、敏感信息和 Git diff
- rollback: 纯文档提交，可整体 revert；不涉及数据库或运行时回滚

## 涉及模块与文件所有权

- 独占：`docs/INDEX.md`
- 独占：`docs/current/`
- 独占：`docs/architecture/`
- 独占：`docs/contracts/`
- 独占：`docs/interfaces/`
- 独占：`docs/standards/`
- 独占：`docs/runbooks/PRE_RELEASE_CHECKLIST.md`
- 独占：`docs/tasks/active/`
- 独占：`docs/tasks/history/INDEX.md`

## 不可破坏合同

- V3 是唯一产品回复入口；V1/V2 不得恢复。
- V3 Reply 是唯一销售语义决策点，Router、门店工具、跟进/B 单目录只提供事实与候选。
- 不同企微号可以共享同一租户业务知识，但客户状态必须按 `corp_id + wechat + external_userid/customer_id` 隔离。
- 延时 B 单保持 shadow；沉默唤醒真实发送必须同时通过 AI 接管、退订、订单、最新消息和发送前复核。
- 文档不得替代生产现场事实；原始客户数据和运行产物不进入 Git。

## 已确认事实与证据

- `origin/main` 与本任务 base 均为 `007bf2c88c1405bc74c4e13320b0e27f667673c4`。
- 当前主分支已包含 V3 DeepSeek Reply、统一意图/情绪/B 单决策、跟进与话术召回、门店工作流、BI 埋点与日志页、沉默唤醒和近期门店回复修复。
- 既往“全场景评测”主要覆盖模型决策图，并未完整覆盖生产历史持久化、结构化消息重复、HTTP 返回尾部耗时与细粒度业务状态组合；后续不得把它表述为生产全生命周期验收。
- 生产三个后端角色均 active/running，健康检查返回同一 clean main SHA；管理前端 active，`/logs` 返回 200。
- 生产使用 MySQL；V3 Reply 有效覆盖为 `deepseek-chat` 且无 Reply fallback；AI 销售策略、Follow Knowledge、全账号 1 分钟沉默唤醒已启用。
- 第三方 SOP worker 队列无积压；策略数据 outbox 为 sent 1994、pending 13、dead 16，外发关闭。
- `/opt/ai-paths/previous` symlink 不存在；上一 clean release 目录存在但仍需在回滚前重新验证。

## 已完成

- 已创建独立任务分支和 worktree。
- 已登记文档独占范围。
- 已新增产品背景、当前开发进度和 V3 全链路评测规范入口。
- 已更新系统架构、销售策略、意图情绪、接口、生产状态、已知问题和发布清单。

## 待办

- 校验链接、敏感信息与文档一致性。
- 合入 main 后关闭任务并写历史摘要。

## 测试结果

- 生产现场只读核验：control、reply、worker 和前端均 active；三个后端 `/health` 返回同一 clean release/commit；worker 队列深度为 0。
- 版本化策略核验：7 类意图、8 类情绪、`silent_tasks_mode=shadow`；临时 B 单目录为 9 条规则、16 套策略、37 个节点和 42 条话术。
- 文档校验：全部本地 Markdown 链接存在；`git diff --check` 通过；敏感 token、私钥、客户原文和真实客户标识扫描无命中；旧 3 张门店卡和错误“全链路”口径扫描无命中。
- 本任务为纯文档变更，未运行模型、未发送消息、未写生产数据，也不需要运行代码测试套件。

## 发布与回滚

- 本任务只更新 Git 共享文档，不部署运行时服务。
- 合并后新窗口以新的 main SHA 为文档基线。

## 待沉淀的长期结论

- 全链路评测必须同时覆盖模型决策、结构化消息、历史已送达状态、持久化尾部和 HTTP 生命周期，且按客户业务状态组合验收。
