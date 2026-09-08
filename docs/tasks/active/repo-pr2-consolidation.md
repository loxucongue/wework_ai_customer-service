# repo-pr2-consolidation

- status: active
- owner: Codex
- branch: `codex/repo-pr2-consolidation`
- base_sha: `a6a5e902d7f24c9da77df527154f6a3ceb025414`
- production_baseline: `ai-paths-unified-20260908-144742-18123aa1`

## 目标

- 审查并安全整合 PR #2 的首日沉默订单读取边界与失败任务状态修复。
- 盘点所有本地/远端分支、提交和 worktree 未提交内容，确认是否已进入 `main`。
- 只合入已完成、可验证且不与最新版合同冲突的内容，组合验证后统一发布。

## 非目标

- 不把过期评测文档或运行产物当成功能提交合入。
- 不擅自合并来源、完成度或业务意图不明的未提交代码。
- 不改变第三方 SOP 严格顺序、V3 Reply、模型选择或客户发送合同。

## Change contract

- type: 代码审查、分支收敛与生产发布。
- scope: PR #2 明确列出的 outreach 文件；共享任务、合同、生产状态和历史索引。
- risk: 首日沉默跳过订单读取后向交易终态客户触达；失败任务状态聚合错误；其他 worktree 未提交代码被误合并；并发 main 更新造成发布 SHA 漏功能。
- validation: 逐提交 diff 与调用链审查、全仓回归、前端类型/Lint/生产构建、生产只读健康/队列/页面验证。
- rollback: 恢复发布前后端及前端 release；本次预期无数据库迁移。

## 当前盘点

- PR #2 唯一提交：`159b4193`、`21571f73`。
- `codex/deepseek-reply-evaluation` 有 1 个未合并的旧文档提交及未提交文档/运行产物，需逐项判断，不整分支合并。
- `codex/sop-opened-customer-guard` 无独有提交，但存在未提交 `sop_platform_task_service.py`，需审查来源和完成度。
- `main` 工作区的 `output/` 为用户已有未跟踪内容，保持不动。
