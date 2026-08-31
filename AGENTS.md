# Project Operating Contract

本文件是仓库级最高协作约定。保持短小、稳定；动态状态写入 `docs/current/`，单次任务写入 `docs/tasks/active/`。

## 1. 产品边界

- 客户回复链只允许产品接口 V3。V1/V2 回复路由必须关闭，新代码不得恢复。
- `ai-paths.service` 当前仍承载共享控制面，`ai-paths-workers.service` 承载 SOP 等后台任务；它们不是“产品 V1/V2”，不得仅因端口或历史名称而停用。
- URL 中的第三方协议版本、持久化 schema 版本，以及 `brain_v2`、`store_resolution_v2` 等内部模块名，不等于产品 V2。删除前必须沿 import、路由、进程和线上调用链证明其已无用途。
- 消息送达回调与 SOP 消费/策略数据回传是不同协议，分别审计，不能互相推断。
- 第三方 SOP 当前合同见 `docs/contracts/third-party-sop-v3.md`。

## 2. 模型与代码职责

- 模型负责业务语义、客户心理和销售节奏。
- 代码负责事实输入、工具调用、schema 标准化、幂等、安全边界和非业务降级。
- 不用 Python 关键词分支判断正常销售意图、异议或会话阶段。回复不正确时，先检查 prompt、上下文、模型选择和工具事实。

## 3. 唯一事实来源

- `main` 是唯一长期开发和生产分支；生产 release 必须映射到一个已验证、干净的 `main` commit。
- 禁止从 detached HEAD、dirty worktree、旧功能分支直接部署。
- 旧分支只能作为取证来源；禁止整分支覆盖或使用全局 `ours/theirs` 合并。
- 动态事实（线上 release、服务状态、数据库模式、队列状态）必须现场核验；文档中的时间戳只表示最后一次验证。
- 原始日志、截图、模型输出、客户会话和测试报告不进入 `docs/` 或 Git。

## 4. 一任务一窗口

每个 Codex 窗口只处理一个明确任务。新窗口启动顺序：

1. 读取本文件。
2. 读取 `docs/INDEX.md`。
3. 读取相关合同/架构文档。
4. 读取唯一的 `docs/tasks/active/<task-id>.md`。
5. 核验当前分支、HEAD、dirty 状态；涉及线上时核验实际 release 和服务。

推荐的新窗口指令：

> 读取 AGENTS.md、docs/INDEX.md 和 docs/tasks/active/<task-id>.md；先核实 main、dirty 状态和生产 release，再继续。

活动任务必须记录：目标、非目标、base SHA、生产基线、涉及模块、不可破坏合同、完成/待办、测试证据、发布和回滚点。完成后把长期结论写入合同或 ADR，并删除活动任务文件；Git 历史就是任务档案。

## 5. 修改与协作

- 非平凡修改先声明 change contract：类型、范围、风险、验证、回滚。
- 主 Agent 是唯一集成人；并行 Agent 必须按文件或目录独占，修改前登记 ownership，不得交叉编辑。
- 保留用户已有修改；不对不明确的 dirty worktree 执行 reset、清理或删除。
- 例行工作直接基于最新 `main`。临时 `codex/*` 分支仅用于明确要求或高风险隔离，验证后合并并删除本地/远端分支和 worktree。
- 不新增无必要抽象、依赖、兼容层或第二套业务规则。

## 6. 测试和发布

- 不删除确定性回归测试来“清理仓库”。应删除缓存、报告和运行产物，并按职责重组测试。
- 默认测试不得调用真实模型、发送真实客户消息或调用生产写接口。
- 回复质量改动需要单节点模型效果测试和部署后的全链路验证；确定性合同测试必须先通过。
- 发布清单至少包含：`branch=main`、完整 commit SHA、`dirty=false`、`service_role`、`interface_version=v3`。
- 发布前保存可恢复版本；发布后验证 V3、共享控制面、worker、回调和管理页；失败立即回滚到已记录 release。

## 7. 文件与磁盘治理

- 规范目录见 `docs/INDEX.md`；业务知识放 `resources/knowledge/`，不混入工程文档。
- 运行产物统一写入 ignored 的 `artifacts/`，建议 7 天 TTL。
- 本地部署包保留最近 3 个或 14 天；合并后的临时 worktree 立即移除。
- 删除 material 数据前先生成清单并确认不被服务器或未提交工作引用；优先可恢复归档。
- 发现真实客户数据进入 Git 时，先从当前树移除，再做历史泄露评估和密钥/标识处置。

## 8. 不可破坏的长期边界

- 销售接触档案边界是 `corp_id + wechat + external_userid/customer_id`；不同接待 WeChat 不共享画像、SOP 进度、发送次数或主动触达状态。
- 固定首次加微 SOP 必须以最新会话拉取成功为前提；首次加微后的真实客户回复必须阻断固定 SOP。时间无法确认时保守阻断。
- 第三方 SOP 任务终态必须回传策略数据；失败要保留尝试并恢复，不能伪造完成。
