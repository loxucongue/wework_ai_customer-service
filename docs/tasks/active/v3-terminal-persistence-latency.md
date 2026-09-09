# V3 提前结束持久化与审计耗时优化

- status: active
- owner: Codex
- branch: `codex/v3-terminal-persistence-latency`
- base_sha: `343f9d3586dcfd7f3000b978c4f5b5774ff70562`
- production_baseline: `7b1c01f7877321b3eb729cefbed91f0cc09ba6c8`
- production_release: `ai-paths-unified-20260908-v3-latency-7b1c01f7`

## 目标

- 将人工接管、消息挤占、撤回、过滤和失败兜底的必要数据合并为单事务。
- 保留客户消息、请求幂等、主动唤醒取消和最小运行审计。
- 把身份补充、SOP 回传、完整轨迹和 BI 补充移入现有可靠收尾 Worker。
- 补齐提前结束路径、数据库和 HTTP 完整阶段耗时。

## 非目标

- 不修改销售 Prompt、DeepSeek 配置或 V3 公共接口。
- 不新增数据库表或恢复 V1/V2。
- 不改变客户身份隔离、人工接管、明确退订及主动唤醒发送前门禁。
- 不在本次代码发布中切换数据库网络地址或关闭连接健康检查。

## 独占范围

- `ai_paths/app/chat_runtime.py`
- `ai_paths/app/services/storage/` 中 V3 run、会话、主动唤醒和策略事件持久化
- `ai_paths/app/services/v3_reply_finalization_service.py`
- `ai_paths/app/services/run_observability.py`
- 对应测试、运行边界与当前状态文档

## 不可破坏合同

- 销售接触边界始终为 `corp_id + wechat + external_userid/customer_id`。
- 真实客户开口必须阻断未发送的主动唤醒任务。
- 人工模式不得调用 Router、知识、工具或 Reply。
- 异步收尾必须持久、幂等、失败可重试，不使用临时进程内任务代替。
- 正常客户回复和核心幂等结果仍需在 HTTP 返回前可靠保存。

## 现场证据

- 指定人工接管请求总耗时 `10069ms`，平台状态查询 `705ms`，模型调用 `0`，其余约 `9364ms` 为未分段的同步审计与持久化。
- 当日 5 条人工接管空回复 P50 `10640ms`、最大 `18564ms`，平台状态查询约 `705–1718ms`。
- 生产服务器到 MySQL 同连接简单查询约 `211ms/条`；独立连接、健康检查与提交约 `937ms/次`，首个冷连接约 `3521ms`。

## 验证与发布

- 延迟注入、事务回滚、幂等、跨企微隔离、唤醒取消、异步恢复及 MySQL/SQLite 测试。
- DeepSeek 真实身份只读复测，不发送客户消息、不写生产策略数据。
- 验收通过后合入 clean main，control/reply/worker 使用同一 SHA 全量发布。
- 回滚到发布前现场记录的 `ai-paths-unified-20260908-v3-latency-7b1c01f7`。

## 已完成实现

- 人工接管、协调器提前过滤及状态失败兜底改用 `save_v3_terminal_no_reply`，在一次连接和一次事务内保存入口、终态、主动唤醒取消、最小 BI 与持久化收尾任务。
- 普通 V3 入口把客户消息和主动唤醒取消合并到同一事务；取消逐阶段 `update_run_progress` 数据库写入，客户身份观察移入收尾 Worker。
- 无客户可见回复的正常终态也使用 `save_v3_reply_core` 创建可恢复收尾任务，不再同步逐项保存轨迹、BI 和策略审计。
- 收尾 Worker 保持串行限流，批量领取任务并用一次事务批量标记完成/失败；失败继续退避重试。
- run 输出增加数据库连接数、SQL 数量和入口/核心/终态持久化耗时；人工接管节点保留真实状态查询耗时。

## 当前测试证据

- 全仓确定性回归：`620 passed`。
- 新增 100ms 连接延迟注入：人工接管同步数据库连接 `1` 次，模型调用 `0`。
- 事务中途注入 BI 失败：run、消息和取消操作全部回滚，无部分终态。
- 相同客户不同企微号：只取消当前 `corp_id + wechat + external_userid/customer_id` 范围的计划。
- 收尾 Worker：人工接管空回复的身份观察与允许空回复策略回传可恢复完成。
- 变更范围 Ruff、Python 编译与 `git diff --check` 通过。
