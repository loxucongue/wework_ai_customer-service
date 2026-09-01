# AI 销售策略迁移与仓库收口

- status: active
- owner: primary agent
- base_branch: main
- base_sha: 499af0187fa8c46ff2f23d91bf6284a3b5eb1064
- last_verified: 2026-09-01 Asia/Shanghai

## 目标

把 2026-08-31 完成的 AI 回复策略、卡点话术/素材和 shadow 跟进能力迁入最新统一 V3 主分支；验证后合入唯一 `main`，再保全并移除旧 worktree、临时分支和历史目录。

## 已完成

- [x] 将旧开发窗口的有效改动提交到备份分支并生成可验证 Git bundle。
- [x] 以 `main@499af018` 建立干净迁移工作区。
- [x] 按当前 V3 架构迁移策略服务、策略目录、管理页和审计，不覆盖已上线 SOP/支付/门店规则。
- [x] 策略和延时任务默认关闭或 shadow，不真实发送。
- [x] 删除无法在基线收集、引用退役 V1/V2 且内容乱码的旧测试。
- [x] 全量确定性回归通过：1241 passed，2 skipped。

## 待办

- [x] 前端类型检查、Lint 和生产构建。
- [ ] 提交迁移、快进合入并推送 `main`。
- [ ] 把业务交付物移出仓库并保留归档。
- [ ] 为所有 dirty worktree 建立可恢复归档，移除旧 worktree 和临时分支。
- [ ] 最终只保留 `E:/ai_code/vscode_codex/coze_cli_project` 一个 `main` 工作区。

## 发布边界

本任务只收口代码仓库，不自动部署生产。正式启用 AI 策略前必须完成单节点模型效果测试与全链路在线测试；策略目录、延时逼单和跟进任务不得因合并自动转为真实发送。
