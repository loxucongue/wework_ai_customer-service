# sop-new-task-recovery-race

- status: active
- owner: Codex
- base_branch: main
- base_sha: `48a4101613d8765a4bb4bfa8b53ea32b3505220b`
- production_verified_at: `2026-09-10T09:01:40+08:00`
- production_releases: `control/reply/worker=ai-paths-unified-20260910-sop-terminal-9ae26dcd@9ae26dcd830c8a8ba6ca9f79d90b257451115e7b`

## 目标

消除新拉取任务持久化与恢复扫描之间的竞态，确保新任务不会在进入确定性执行前被误判为旧执行链并以 `legacy_execution_disabled` 消费为 70。

## 非目标

- 不重开或补发已经终态的历史任务。
- 不修改客户未开口、关系未删除、AI 托管三项门槛。
- 不修改主动发送、任务消费、msgId 消费或策略回传协议。

## Change contract

- type: 线上并发正确性修复
- scope: 新任务入队预留、确定性恢复标记的持久化顺序及对应回归测试
- risk: 错把真实旧执行链任务放入新确定性链，或任务持久化失败后残留内存占用
- validation: 并发竞态回归、SOP 专项、全仓确定性测试、发布后只读队列与新任务审计
- rollback: `ai-paths-unified-20260910-sop-terminal-9ae26dcd`

## 涉及模块与文件所有权

- `ai_paths/app/services/sop_platform_task_service.py`
- `tests/test_sop_platform_deterministic_execution.py`
- `docs/contracts/third-party-sop-v3.md`
- `docs/current/PRODUCTION_STATE.md`

## 不可破坏合同

- 正常队列与恢复入口都只能进入确定性三门槛路径，不得恢复旧模型路径。
- 已有非确定性执行证据的历史任务仍只隔离，不自动重放。
- 尚未调用主动发送接口时不得消费任何 msgId。
- 同一任务不得由普通队列与恢复线程并发执行。

## 已确认事实与证据

- 2026-09-10 03:53 至 08:53，6 个新任务 `84032/84147/84148/84149/84185/84233` 在创建后约 8 秒内被写为 `legacy_execution_disabled`，均未调用主动发送并已消费任务 70。
- 当前实现先把事件写为可恢复的 `platform_queued`，随后才将任务 ID 加入内存 `_queued_ids`；恢复线程可在两步之间读取任务。
- 恢复线程看到本地任务尚无 `deterministic_customer_gate` 标记时会按旧链隔离，普通队列随后将其按失败终态消费。

## 已完成

- 现场核对 23 条告警、任务终态和当前队列，确认告警未重复投递。
- 新任务在持久化前先加入本实例 `_queued_ids`，持久化失败或本地队列提交失败时释放占用。
- 新任务先持久化 `deterministic_customer_gate` 执行模式，再把事件发布为 `platform_queued`；已有旧执行模式的历史任务不被改写。
- 增加“持久化期间恢复不可抢占”和“执行标记先于可恢复状态”两个确定性回归。

## 待办

- 合并干净 main 并发布三个后端角色。
- 发布后核验实时任务不再误入旧链。

## 测试结果

- SOP 专项：`102 passed`。
- 全仓：首次 `852 passed, 1 failed`，唯一失败为无关的图片指纹异步计时波动；该用例单独连续复跑 `3/3` 通过。
- 全仓复跑：`853 passed`。
- Ruff：变更范围通过。

## 发布与回滚

待执行；回滚点为 `ai-paths-unified-20260910-sop-terminal-9ae26dcd`。

## 待沉淀的长期结论

恢复器可见的持久化状态必须在对应执行模式标记已经落库且普通队列已经取得本实例租约后才能发布。
