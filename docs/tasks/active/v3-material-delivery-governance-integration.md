# V3 素材交付治理集成

- task_id: `v3-material-delivery-governance-integration`
- status: active
- owner: 独立集成窗口
- branch: `codex/v3-material-delivery-governance-integration`
- base_sha: `0a0ec7942a4fa585ff21a95a0a2e34fc64110e19`
- reviewed_candidate: `origin/codex/v3-material-delivery-governance-review@a83a647f2d98ab9835237185fe0946ffd4fa970b`
- reviewed_code_commit: `17b5670d74635ad2d4da05076153d33d0c4dc0bb`
- production_baseline: 不部署；生产迁移、回填和真实链路另立任务
- task_type: 已验收候选集成 + 生命周期收尾

## 任务使命

### 集成 change contract（2026-09-10）

- 类型/范围：选择性集成 `17b5670d` 的代码、测试和三份长期合同；任务生命周期按最新 main 收口。精确文件集为该提交的 30 个文件，加本任务文件、active INDEX、history INDEX，完成时删除三个 active 任务文件。
- 来源：fetch 后 origin/main 为上述 base；冻结 review HEAD 为 `a83a647f2d98ab9835237185fe0946ffd4fa970b`，其父为修订提交，修订提交父为 `83895fe5`；main 新增仅登记文档，无重叠运行代码。未带入 `8d0e39f2`。
- ownership：实现与验收均冻结；本窗口独占集成，无新增运行代码修订权限。其他工作树未提交内容保持原样。
- 风险：共享 SQL guard、MySQL RR 锁与迁移、身份占用和稳定响应事务边界；新 P0/P1 或关键证据回退立即停止合入。
- 验证：独立本机 MySQL 8.4/新数据目录/端口 13389；迁移及重入/降级、SQL guard、并发/回滚/重放/尾部失败，全量确定性后端、变更 Python Ruff、前端锁定安装及正式两阶段构建、schema/diff 审计。
- 回滚：合入前保持 main 不变；代码回退保留非空身份与占用表。生产未触碰，不声明当前生产 release 已现场核验。
- 进度：来源和基线核验通过，修订提交无冲突应用；以下集成验证全部通过，待提交归档及最终 main 同步。

### 集成验证结论（2026-09-10）

- 最终行为审计：代码、tests、projects 和三份长期架构/合同与 `17b5670d` 完全一致；未引入额外运行代码修订，未发现新 P0/P1。任务生命周期保留最新 main 登记，没有应用 `a83a647f` 的旧 active INDEX 删除方式。
- L1 全量确定性后端：`990 passed, 9 warnings in 122.48s`，显式启用隔离 MySQL 专项。24 个变更 Python 文件 Ruff 全过；变更 TSX ESLint、暂存及工作树 diff 检查通过。
- 本机新实例 MySQL `8.4.7`、InnoDB、REPEATABLE-READ、仅 `127.0.0.1:13389`，独立新数据目录。升级基线 `20260909_02`→`20260910_01`、重复升级、空表降级→重升级通过；删除合成实例索引模拟 DDL 中断后 stamp 基线→重入补齐索引通过；非空 downgrade 安全拒绝，占用行数、revision、旧数据哨兵保持。
- 真实事务专项单独保留可见连接证据：10 项通过，包含独立连接跨别名两种顺序、胜方回滚、同响应并发重放、跨接触隔离、新进程读取、旧快照目录变更/rollback 保护、合成目录 apply/reapply/stale-plan/rollback；MySQL trigger 尾部故障证明 run/消息/占用整体回滚，成功时稳定响应、消息 ID 和收尾 pending 同事务保存。
- migration 唯一 head 为 `20260910_01`；两张新增表的列、主键及索引与 MySQL metadata、迁移结果和 SQLite schema 一致，运行 schema 初始化也随真实 MySQL 专项通过。
- 前端使用 `pnpm@9.0.0 --ignore-workspace install --frozen-lockfile`，lockfile 无变化；正式 Next 16.1.1 构建含 TypeScript 和 37 页生成通过，tsup node20 服务器打包通过。现有上层锁文件导致 workspace root 提示，但构建退出码为 0，未修改配置绕过校验。
- 原始证据仅在 ignored `artifacts/material-governance-integration/`；数据库仅合成数据。未调用真实模型、客户发送或第三方生产写；未部署、未执行生产迁移/回填、未做 L2–L4、未证明第三方发送或送达闭环。
- 发布前置：独立授权发布任务现场核验生产 release/服务及 RDS，审计真实目录和历史缺口、准备同步适配与可逆迁移/回填和回滚点，补齐必要 L2–L4；三个后端角色同一 clean main SHA，耦合前端同批。第三方稳定消息 ID 到 dispatch 的权威映射与失败/送达回调仍独立验收。回退代码保留非空身份/占用表。

把已经通过独立代码审查、MySQL 验证、全量后端回归和完整前端构建的素材治理修订候选安全合入最新 main，并正确关闭实现、验收和集成任务。不得重新设计功能，也不得把“代码可集成”扩大解释为“已具备生产发布条件”。

## 唯一允许来源

- 只允许集成验收分支中经验证的修订代码提交 `17b5670d74635ad2d4da05076153d33d0c4dc0bb` 及其对应任务归档结论。
- 原实现候选 `8d0e39f2` 仅为历史来源，不得绕过修订直接合入。
- 集成前必须重新 fetch，确认 reviewed candidate SHA、提交关系、最新 origin/main 和 dirty 状态；若 main 出现新的重叠运行代码，停止并重新评估冲突。
- 不使用整分支覆盖、全局 ours/theirs 或从脏工作树复制文件。生命周期文档冲突必须按当前 active INDEX 逐项收口。

## 集成边界

- 保留已验收的统一素材身份、跨来源合并、未知附件失败关闭、内容/动作隔离、response_committed 同事务占用和准确日志口径。
- 保留验收修正后的 SQL 安全检查；不得弱化真实写入、DDL、多语句、未验证目标和混淆输入的保护。
- 保留 MySQL REPEATABLE-READ 下跨别名并发、回滚、重放和身份隔离行为。
- 不改变销售语义、软承接口径、Reply 唯一决策权、客户接触档案边界或公共回复/发送协议。
- 不执行生产目录同步、历史回填、数据库迁移、真实模型调用、客户发送、第三方生产写或部署。

## 必须重验

- 核对最终差异只包含已验收素材治理行为、必要数据库安全修正、测试、长期合同和生命周期文档，无调试产物、真实数据或无关修改。
- 使用隔离 MySQL 重跑关键迁移、锁定读安全分类、两个独立连接并发占用、胜方回滚、同响应重放和事务尾部失败用例。
- 重跑全量确定性后端测试、所有变更 Python 静态检查、差异检查。
- 使用项目锁定依赖完成正式前端构建；不得只做语法转译。
- 核对 migration head、升级/降级边界和 schema 文件一致。
- 确认日志和合同只宣称 AI 生成侧去重，`response_committed` 不被称为发送或送达。

如果集成仅发生生命周期文档冲突，可以按最新版任务状态自主解决；如果运行代码需要新增修订、MySQL 证据回退、全量测试失败或出现新的 P0/P1，停止合入并回母窗口。

## 完成条件

- 最终 main 只包含 reviewed candidate 的已验收行为，无原候选遗留差异。
- 所有必须重验证据通过，工作区干净，origin/main 与本地完整 SHA 一致。
- `v3-material-delivery-governance`、`v3-material-delivery-governance-review` 和本集成任务从 active 移除，在 history 写一条产品级摘要；长期风险留在相应 current/contract，而不是活动任务。
- 合入后明确记录：未部署、未执行生产迁移/回填、未做 L2–L4、未证明第三方实际发送或送达。

满足以上条件可推送 main；不允许部署。完成后只向母窗口回报 `结论 / 改动 / 证据 / 风险 / 下一步`。
