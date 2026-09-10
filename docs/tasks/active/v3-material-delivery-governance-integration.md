# V3 素材交付治理集成

- task_id: `v3-material-delivery-governance-integration`
- status: active
- owner: 独立集成窗口
- branch: `codex/v3-material-delivery-governance-integration`
- base_sha: `83895fe548415afce9167f83617f4ee63a3bc1dd`
- reviewed_candidate: `origin/codex/v3-material-delivery-governance-review@a83a647f2d98ab9835237185fe0946ffd4fa970b`
- reviewed_code_commit: `17b5670d74635ad2d4da05076153d33d0c4dc0bb`
- production_baseline: 不部署；生产迁移、回填和真实链路另立任务
- task_type: 已验收候选集成 + 生命周期收尾

## 任务使命

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
