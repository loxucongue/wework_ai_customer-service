# v3-only-consolidation

- status: active
- owner: primary agent
- base_branch: main
- base_sha: 7af3065e
- production_verified_at: 2026-08-31 Asia/Shanghai
- production_releases: shared/worker=0ef5f545; v3=7c0cfc04; frontend=dce86d4b

## 目标

把 V3 回复链、当前 main 的 SOP/平台能力和部署配置收敛到唯一 main，删除真正的产品 V1/V2 入口与实现，保全其他功能，并完成仓库、文档、测试和 worktree 治理。

## 非目标

- 不按文件名批量删除 `v1`/`v2`。
- 不删除历史数据库数据或重写 Git 历史。
- 不在未保全 dirty worktree 前删除工作区。
- 不改变正常销售语义。

## Change contract

- type: architecture_refactor
- scope: routing, runtime composition, repository governance, docs/tests/artifacts
- risk: high；两条生产代码线深度分叉，错误覆盖会丢 V3 或最新 SOP
- validation: route contract、deterministic tests、build、staging/full-chain、production health/callback/worker checks
- rollback: Nginx 备份与服务 symlink 回退到已记录 releases

## 不可破坏合同

- `docs/contracts/RUNTIME_BOUNDARIES.md`
- `docs/contracts/third-party-sop-v3.md`
- AGENTS 中销售接触边界和客户回复阻断规则

## 已确认事实与证据

- main 与 V3 从 `be65e329` 分叉，不能整分支覆盖。
- V3 当前是 sidecar；shared API/worker 仍承载第三方 SOP 等能力。
- 生产旧公开回复入口已 410；旧 refactor/backend services 已 disabled。
- 23 个 worktree 中多个 dirty，尚不可安全删除。

## 已完成

- [x] 生产运行拓扑和 Git 分叉审计
- [x] 关停公开 V1/V2 回复入口，包括 Next 旧代理
- [x] 停止并禁用旧 V2/refactor service
- [x] 建立仓库宪法、文档索引、运行合同和任务交接模板
- [x] 后端旧 V1 路由在代码层固定返回 410，删除两个生产 Next 旧代理
- [x] 删除旧 handoff/V2/SOP 冲突文档、已跟踪测试报告和真实客户会话样本
- [x] 删除服务器 V2 运行目录并 mask 服务；主/V3 release 各保留 3 个
- [x] 部署 main `0ef5f545`，内部与公网旧路由均验证为 410
- [x] 将前端 symlink 从后端历史 release 迁到独立 frontend release 根

## 待办

- [ ] 保全所有 dirty worktree，形成逐项去留清单
- [ ] 从最新 origin/main 建立干净整合工作区
- [ ] 按模块所有权合并 V3 与 main，人工处理入口/配置/storage/deploy
- [ ] 删除产品 V1/V2 route/service，重命名仍被 V3 使用的历史内部模块
- [ ] 重组测试，删除已跟踪报告与敏感样本
- [ ] 将剩余 legacy 文档和测试目录按新结构迁移；对敏感样本做 Git 历史泄露评估
- [ ] 统一 Nginx/systemd/release manifest 并部署同一 main SHA
- [ ] 验收后删除旧远端分支、worktree 和历史 release

## 测试结果

- Nginx config test: passed
- public old reply routes: HTTP 410
- V3 health: HTTP 200
- shared API/worker/frontend: active
- deterministic backend tests: 102 passed（SOP + 初版路由合同）；增强后的 V3-only 路由合同单独 7 passed
- Python compile: passed
- frontend type/lint: blocked by missing local `node_modules` and Windows-incompatible aggregate script；不是有效代码失败结论
- keep-codex-fast report: report-only scan exceeded 5 minutes and was stopped；未修改本机 Codex 状态

## 发布与回滚

- 当前仅做入口 containment；未宣称完成代码线合并。
- Nginx rollback: `/etc/nginx/conf.d/ai-paths.conf.pre-v3-only-20260831`
- shared release: `ai-paths-server-20260831-v3-only-contract-0ef5f545`
- V3 release: `ai-paths-v3-server-20260826-reply-evidence-7c0cfc04`
- shared rollback release: `ai-paths-server-20260831-sop-unopened-ruledata-7af3065e`

## 待沉淀的长期结论

- V3/main 具体文件所有权和最终统一部署形态。
- 历史 `v2_*` 无行为重命名映射。
