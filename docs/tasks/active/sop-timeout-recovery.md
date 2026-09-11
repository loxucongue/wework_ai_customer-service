# sop-timeout-recovery

- status: release_authorized
- owner: 当前独立执行窗口
- base_branch: main
- base_sha: 31882257f955dcf2c2c366755ebd18534ee9d0a5
- branch: codex/sop-timeout-recovery
- worktree: E:\ai_code\vscode_codex\worktrees\sop-timeout-recovery
- exclusive_scope: `ai_paths/app/config.py`、`ai_paths/app/services/sop_platform_client.py`、`ai_paths/app/services/sop_platform_task_service.py`、`ai_paths/app/services/sop_failure_alert_service.py`、对应 SOP 测试与第三方 SOP 合同
- production_verified_at: 2026-09-11T12:07:00+08:00
- production_releases: control/reply/worker=ai-paths-unified-20260911-refund-fallback-31882257@31882257f955dcf2c2c366755ebd18534ee9d0a5
- data_model_send_authority: 用户已明确授权修复后上线；发布验证只读且不主动发送测试消息

## 目标

- 将第三方 SOP 任务执行并发硬上限降为 4。
- `/pending` 与 `/sop-messages` 首次连接超时后用独立新连接快速重试一次，并记录瞬时超时及恢复。
- 任务发送前的前两次可恢复失败不发业务预警；第三次失败终态消费后只预警一次。
- `/pending` 全局连接超时连续三轮失败才发一次系统预警，恢复后清零。
- 客户状态/会话门槛失败记录安全的接口名、HTTP 状态、超时阶段和异常类型，避免只记录 `RuntimeError`。

## 非目标

- 不延长现有 12 秒超时。
- 不改变三项业务门槛、任务/内容消费状态机、发送幂等或外部接口协议。
- 不处理历史任务，不发送真实消息，不修改生产配置或部署。

## Change contract

- type: 可靠性与可观测性修复
- scope: 第三方 SOP worker 的并发、只读拉取连接重试、失败告警和安全错误归因
- risk: 新连接重试可能造成重复读取；告警门槛可能漏报非瞬时故障；并发下降可能降低积压场景吞吐
- validation: 客户端连接重试单测、连续失败/恢复/告警单测、确定性 SOP 合同测试及相关回归
- rollback: 回退本任务最终提交；无 schema 或数据回滚

## 涉及模块与文件所有权

- `ai_paths/app/config.py`
- `ai_paths/app/services/sop_platform_client.py`
- `ai_paths/app/services/sop_platform_task_service.py`
- `ai_paths/app/services/sop_failure_alert_service.py`
- `tests/test_sop_platform_client_logging.py`
- `tests/test_sop_platform_deterministic_execution.py`
- `tests/test_sop_failure_alert_service.py`
- `docs/contracts/third-party-sop-v3.md`

## 不可破坏合同

- 第三方 SOP 不调用模型；只有未开口、未删除、AI 托管才拉内容并发送。
- 一轮只发送/消费一个内容组；发送接口已调用但结果未知时联合消费任务 `30 + msgId 30`，禁止重发。
- 客户已开口、关系删除、人工接管是无预警业务终态；明确技术/内容失败最终必须告警。
- `/pending` 空轮询不产生任务、消费、回传或告警。

## 已确认事实与证据

- 生产三个后端服务健康，release 为 `25e1ed66`，worker 当前 pending/queue/in-flight 均为 0。
- 生产 `.env` 当前 `SOP_PLATFORM_TASK_CONCURRENCY=8`、恢复并发为 2；代码默认任务并发为 6，持久化阶段上限为 8。
- 当前 `/pending` 和 `/sop-messages` 共用长连接客户端且连接超时不做独立新连接重试。
- 当前可恢复任务结果带 `retry_scheduled=true`，但告警分类未抑制，存在前两次恢复阶段即告警的问题。
- 当前客户门槛并发查询捕获后只保留异常类型，无法判断失败接口和阶段。

## 已完成

- 基线、生产 release、服务健康和生产并发配置只读核验。
- 代码路径与现有有限三次恢复合同核对。
- 任务执行并发默认值和运行硬上限统一为 4，健康状态同时展示配置值与实际值。
- `/pending`、`/sop-messages` 增加一次独立新连接恢复及三类累计计数。
- `/pending` 连续超时告警门槛和恢复清零完成；任务恢复中的 `retry_scheduled` 结果不再提前告警。
- 客户门槛错误改为安全记录接口、阶段、HTTP 状态和异常类型。
- 稳定合同与确定性测试已更新。

## 待办

- 合入最新干净 `main`，构建统一后端 release 并部署；发布后核验健康、有效并发、队列和告警状态。

## 测试结果

- `PYTHONPATH=ai_paths python -m pytest <全部 sop 测试> -q`：122 passed。
- `PYTHONPATH=ai_paths python -m pytest -q`：998 passed，10 skipped；仅既有依赖弃用 warning。
- 重放到最新 `main@31882257` 后再次执行全量回归：1008 passed，12 skipped；仅既有依赖弃用 warning。
- `python -m compileall -q ai_paths/app`：通过。
- `python -m ruff check <本次修改 Python 文件>`：通过。
- `git diff --check`：通过。

## 发布与回滚

- final_main_commit:
- deployed: no

## 待沉淀的长期结论

- 只读接口连接超时的一次新连接恢复及观测字段。
- 瞬时失败与终态失败的告警分界。
