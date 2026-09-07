# v3-latency-outreach-recovery

- status: active
- owner: Codex
- base_branch: main
- base_sha: 7e10bb31b775d4243f90f3739e0a79bb2c61f7e4
- production_verified_at: 2026-09-07 23:20 Asia/Shanghai
- production_releases: control/reply/worker=20260907-221233-e738330c@e738330c4e68a832d97de4ec4af0ae4c54495aea

## 目标

- 修复 V3 请求真实 HTTP 耗时统计丢失，并缩短回复后的同步持久化耗时。
- 修复主动唤醒计划生成空值崩溃、失败不可重试和权威指纹永久拦截。
- 完成确定性与隔离回归后合入 `main`，以同一干净 SHA 发布 reply、control、worker。

## 非目标

- 不改变 V3 销售语义决策、回复模型、主动唤醒发送前安全校验和夜间规则。
- 不启用延时逼单自动发送，不重放已有失败 outbox，不新增第二套任务系统。

## Change contract

- type: bugfix / performance / production release
- scope: V3 run 持久化、HTTP timing、主动唤醒 plan generation 与 retry/dedup
- risk: 持久化批量化可能影响日志完整性；失败恢复错误可能重复生成任务
- validation: 仓储合同、主动唤醒失败恢复、跨企微隔离、幂等、V3 接口和性能回归
- rollback: 恢复发布前 release；代码回滚不要求回滚数据结构

## 涉及模块与文件所有权

- `ai_paths/app/services/storage/run_repository.py`
- `ai_paths/app/services/memory_store.py`
- `ai_paths/app/chat_runtime.py`
- `ai_paths/app/services/outreach/planning.py`
- `ai_paths/app/services/outreach/first_day.py`
- 上述模块直接对应的 `workflow_tests/`
- `docs/current/`、相关合同、任务索引与历史摘要

## 不可破坏合同

- 只允许 V3 客户回复链；V3 Reply 仍是唯一销售语义决策者。
- 客户状态按 `corp_id + wechat + external_userid/customer_id` 隔离。
- 主动唤醒发送前仍检查 AI 接待、客户新消息、退订/人工接管、订单终态和夜间窗口。
- 一次失败不得造成重复发送；失败恢复前必须重新读取权威会话。
- 日志和 BI 故障不得导致客户正常回复失败。

## 已确认事实与证据

- 指定 V3 请求约 75 秒返回，主模型约 3.7 秒；主要耗时在图前数据库读取与图后逐条持久化。
- `save_run` 覆盖 `output_snapshot` 时丢失入口计时字段，导致页面耗时少记约 17 秒。
- 指定客户在 1 分钟阈值后进入主动唤醒，选择序列 45，但 `plan_generation` 发生空值属性错误。
- 失败 run 没有计划或任务，后续扫描仍被权威 fingerprint 判重永久拦截。

## 已完成

- 完成日志、生产配置、数据库延迟和失败 run 的只读取证。
- 保留 HTTP ingress 元数据并批量写入节点轨迹。
- 将同一回复产生的客户记忆事实合并为一次读取和一次持久化。
- 对跟进序列和话术目录做结构保护；目录异常时保留序列节点计划并记录明确原因。
- 计划生成异常增加一次有界重试；权威指纹指向无计划的可恢复失败 run 时复用原 run。

## 待办

- 实现最小修复与测试。
- 合入 main、发布并现场验证。

## 测试结果

- `python -m pytest -q`: 444 passed。
- 改动范围 Ruff：通过（`first_day.py` 的既有重导出 unused-import 不在本任务清理）。
- `git diff --check` 与修改文件编译：通过。
- L2 模拟持久化：HTTP timing、批量 trace、客户记忆合并、计划目录空值、失败恢复与 fingerprint 幂等均有确定性断言。

## 发布与回滚

- pending

## 待沉淀的长期结论

- pending
