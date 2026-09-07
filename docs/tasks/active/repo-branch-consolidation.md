# repo-branch-consolidation

- status: active
- owner: Codex
- base_branch: main
- base_sha: `c39a237235a5d57f3f7638040a28d91ac0247c8d`
- production_verified_at: `2026-09-07T19:49:47+08:00`（文档快照，发布前重新现场核验）
- production_releases: `control/reply/worker=ai-paths-unified-20260907-190313-0873b27d@0873b27da16a0fbb26197722d6632866181a32b0`

## 目标

- 审计所有本地 worktree、本地与远端 `codex/*` 分支和相对 `main` 的独有提交。
- 只按提交合入已经完成、仍适用于最新架构且可验证的改动；确认已合入或已被新版替代的分支不重复合并。
- 完成回归后合入并推送 clean `main`，再以同一 SHA 发布生产。

## 非目标

- 不整分支覆盖最新版代码，不使用全局 `ours/theirs` 解决冲突。
- 不擅自提交、清理或删除其他 dirty worktree 的未提交内容和本地产物。
- 不把仅有任务占位、错字或未完成设计的提交伪装成可发布功能。

## Change contract

- type: repository consolidation / release
- scope: 分支与提交审计；经审计可发布的代码、迁移、前端和合同；任务与生产状态文档
- risk: 旧分支基线落后，可能覆盖最新 Reply、outreach、身份边界或管理页；数据库迁移可能要求先备份并执行
- validation: 提交级等价检查、冲突检查、相关后端测试、前端类型/Lint/生产构建、迁移校验、发布后四角色与 V3/管理页核验
- rollback: 保留当前生产 release 与数据库备份；运行异常恢复上一 clean release；新增兼容字段按迁移合同保留

## 涉及模块与文件所有权

- 本任务独占分支/提交整合、发布记录及上述任务文档。
- 代码文件只有在确认某个独有提交可合入后才按该提交原子整合；不会修改其他 dirty worktree。

## 不可破坏合同

- V3 是唯一客户回复入口，Reply 是唯一销售语义决策者。
- 客户状态严格按 `corp_id + wechat + external_userid/customer_id` 隔离。
- control、reply、worker 必须来自同一 clean `main` SHA；延时逼单保持 Shadow。
- 未提交客户数据、日志、模型输出和本地产物不得进入 Git。

## 已确认事实与证据

- `origin/main` 与本地 `main` 当前均为 `c39a237235a5d57f3f7638040a28d91ac0247c8d`。
- 预约时间边界和直接预约收口两个远端分支已经是 `main` 祖先，无独有提交。
- DeepSeek 评测、身份合同修复和服务器内存待办分支共存在 4 个相对 `main` 的独有提交，待逐项审计。
- 旧 DeepSeek 工作区存在 2 个未提交文档改动及本地产物，保持原样。

## 已完成

- 读取项目宪法、架构、运行边界、任务流程、发布清单和生产快照。
- 建立最新 `origin/main` 上的独立 clean worktree。

## 待办

- 完成 4 个独有提交的代码、合同、迁移和测试证据审计。
- 整合可发布改动，处理或上报语义冲突。
- 执行验证、合入 main、推送并现场发布。

## 测试结果

- 待执行。

## 发布与回滚

- 发布前重新现场核验，不以当前文档快照替代生产事实。

## 待沉淀的长期结论

- 分支收敛后记录保留、被替代和未完成分支的判定依据，避免后续窗口重复合并。
