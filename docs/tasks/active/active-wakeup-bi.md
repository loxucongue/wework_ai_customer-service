# active-wakeup-bi

- status: active
- owner: Codex
- base_branch: main
- base_sha: 58b3ce2e9c1db124714684880680e2be2bad02f7
- production_verified_at: 待本次发布前现场复核；文档快照为 2026-09-07T11:39:01+08:00
- production_releases: 文档快照 `backend/reply/worker=ai-paths-unified-20260907-113657-7e795d47`，`frontend=frontend-20260907-113657-7e795d47`

## 目标

- 按已确认原型实现主动唤醒 BI 全览和客户明细。
- 使用真实计划、任务、发送、客户回复和交易结果数据，清晰展示从扫描到预约/付定金的漏斗。
- 展示当前配置是否生效、未触达原因、队列状态、计划步骤、素材、运行记录和客户上下文。
- 验证后合入 main，并部署同一 clean SHA 的 control/reply/worker/frontend。

## 非目标

- 不改变主动唤醒计划生成、发送资格和客户状态判断逻辑。
- 不新增模型调用，不启用新的自动发送策略，不改变 V3 Reply。
- 不把完整客户原文、模型思维过程或凭证写入新的 BI 表。

## Change contract

- type: feature + observability + frontend
- scope: 主动唤醒只读聚合接口、管理页、客户明细、配置状态、测试和文档
- risk: 历史字段缺失导致口径误报；聚合查询影响 MySQL；前后端不同 SHA；管理页泄露客户或凭证数据
- validation: 后端确定性测试、只读聚合查询性能、前端类型/Lint/生产构建、真实浏览器视觉和交互、生产只读接口与服务健康检查
- rollback: 恢复发布前 backend/reply/worker/frontend release；本次原则上不新增破坏性数据迁移

## 涉及模块与文件所有权

- `ai_paths/app/routers/outreach_admin.py`
- 主动唤醒只读 BI 聚合服务及对应测试
- `projects/src/app/analytics/outreach/`
- `projects/src/app/api/outreach/dashboard/`
- `projects/src/components/outreach/`
- 管理后台导航中主动唤醒入口
- 本任务相关 contracts/current 文档

## 不可破坏合同

- 客户数据按 `corp_id + wechat + external_userid/customer_id` 隔离。
- 只处理平台明确为 AI 接待的客户；人工、未知或查询失败均不能触达。
- 1 分钟是候选资格阈值，不得在看板中误报为固定发送 SLA。
- 退订、客户已回复、已预约/已支付和人工接待必须正确进入阻断口径。
- 看板查询只读，不触发扫描、计划生成、发送、重试或第三方平台写操作。

## 已确认事实与证据

- 当前生产文档快照显示全账号启用、仅 AI 接待、沉默阈值 1 分钟。
- 现有 outreach repository、计划运行快照和任务记录已保存计划、发送与客户状态，无需建立第二套业务事实表。
- 已确认视觉原型位于 `product-design-prototypes/active-wakeup-dashboard-20260907`，设计 QA 已通过。

## 已完成

- 从最新 `origin/main@58b3ce2e` 创建独立 worktree 与分支。
- 登记文件所有权与变更合同。
- 新增只读主动唤醒聚合接口，复用运行、计划、任务、事件和客户入站消息计算六步漏斗、趋势、阻断原因、队列和账号分布。
- 新增 `/analytics/outreach` 管理看板，并接入 Worker 健康状态核对配置是否真正生效。
- 完成客户明细下钻：生命周期、两步计划、素材、原因、取消条件、模型节点、事件时间线和最近客户可见对话。
- 补充主动唤醒 BI 稳定口径、接口和架构文档；无数据库迁移、无模型调用、无发送逻辑变更。

## 待办

- 合入 main、部署并完成生产只读接口、页面和服务健康核验。
- 更新生产状态和历史摘要，关闭任务。

## 测试结果

- 后端完整回归：`344 passed`。
- 前端：TypeScript、ESLint、Next.js 生产构建通过，新增 `/analytics/outreach` 与 `/api/outreach/dashboard` 路由。
- 浏览器：1487×1058 桌面、390×844 手机、配置弹窗和客户明细抽屉验收通过。
- 视觉对比遵循已确认原型；浏览器验收期间修复配置弹窗小高度溢出和素材链接重复。
- `git diff --check` 通过；没有数据库迁移和生产写操作。

## 发布与回滚

- 待发布前现场复核。

## 待沉淀的长期结论

- 主动唤醒 BI 的漏斗、阻断原因、计划状态和时间口径。
