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
- 客户状态严格按 `corp_id + wechat + external_userid` 隔离；平台客户 ID 不参与替代。
- control、reply、worker 必须来自同一 clean `main` SHA；延时逼单保持 Shadow。
- 未提交客户数据、日志、模型输出和本地产物不得进入 Git。

## 已确认事实与证据

- `origin/main` 与本地 `main` 当前均为 `c39a237235a5d57f3f7638040a28d91ac0247c8d`。
- 预约时间边界和直接预约收口两个远端分支已经是 `main` 祖先，无独有提交。
- DeepSeek 评测、身份合同修复和服务器内存待办分支共存在 4 个相对 `main` 的独有提交，待逐项审计。
- 旧 DeepSeek 工作区存在 2 个未提交文档改动及本地产物，保持原样。
- 身份分支与最新版的真实冲突只有 `runtime_services.py` 和活跃任务索引；装配冲突已按最新版保留 Follow Knowledge 客户端，同时把 repository 注入客户身份服务。
- 生产最近 200 条运行输入中，200 条均有独立平台客户 ID 与外部联系人 ID且二者无混用；199 条具备完整五类托管身份，唯一缺 `user_id` 的请求发生于 2026-09-05。新合同会对这类不完整请求返回 400，避免带着不明平台接待身份继续调用。

## 已完成

- 读取项目宪法、架构、运行边界、任务流程、发布清单和生产快照。
- 建立最新 `origin/main` 上的独立 clean worktree。
- 逐提交整合身份合同修复 2 个提交；保留最新版主动唤醒的 Follow Knowledge 装配。
- 整合生产内存与发布构建治理待办，保留最新版已知问题编号和现场事实。
- 判定旧 DeepSeek 评测提交只包含已过期的活跃任务占位；其实际目标已由后续 400 条真实身份评测和 DeepSeek 稳定性任务完成，不恢复旧任务文件。

## 待办

- 完成身份合同代码审查与接口/架构文档收敛。
- 执行验证、合入 main、推送并现场发布。

## 测试结果

- 后端完整回归：`422 passed`。
- Python 编译与 `git diff --check`：通过。
- Alembic 迁移链：单一 head `20260907_02`。
- 前端：TypeScript、变更文件 ESLint、Next 生产构建和 server bundle 通过。

## 发布与回滚

- 发布前重新现场核验，不以当前文档快照替代生产事实。

## 待沉淀的长期结论

- 分支收敛后记录保留、被替代和未完成分支的判定依据，避免后续窗口重复合并。
