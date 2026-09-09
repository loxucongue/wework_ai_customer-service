# sop-model-state-fix

- status: active
- owner: Codex
- base_branch: main
- base_sha: eae652145a1891e1626b8a1c1395d6b76b47219b
- production_verified_at: 2026-09-09 11:39 Asia/Shanghai
- production_releases: control/worker/reply=ai-paths-unified-20260909-v3-terminal-78622cf4@78622cf42fdf632e47d8f7e4d9e379a7bc3c6b9c

## 目标

- 确保第三方 SOP 在主动发送成功后不再进入模型或被降级为异常状态。
- 管理页以明确的主动发送成功证据展示“发送完成”，消费状态继续独立展示，避免历史污染状态误报为发送失败。
- 第三方未生成 pending 任务时保持无操作，不生成、不消费、不告警。

## 非目标

- 不补发或改写任何历史客户消息。
- 不人工生成第三方任务，不修改第三方 SOP 平台数据。
- 不改变客户开口、客户删除、人工接管三项业务门禁。

## Change contract

- type: correctness / state-machine hardening
- scope: 第三方 SOP 执行恢复、管理页状态派生、确定性回归测试与合同
- risk: 把真实发送失败误判为发送成功，或使待恢复任务失去恢复机会
- validation: 确定性测试证明恢复不调用模型；只有平台已接受且存在消息 ID 等明确发送证据才修正“发送完成”展示；无 pending 时无副作用
- rollback: 回滚至生产 release `ai-paths-unified-20260909-v3-terminal-78622cf4`

## 涉及模块与文件所有权

- `ai_paths/app/services/sop_platform_task_service.py`
- `ai_paths/app/services/storage/sop_event_repository.py`
- `ai_paths/app/services/storage/operations_dashboard_repository.py`
- 第三方 SOP 相关临时测试
- `docs/contracts/third-party-sop-v3.md`
- 本任务文件与活跃任务索引

## 不可破坏合同

- 第三方 SOP 正常路径不调用模型。
- 主动发送接口正常返回后只消费当前任务与当前 `msgId`。
- 无明确发送证据不得显示或写入成功；已存在成功证据不得降级。
- 第三方未生成 pending 任务时我方不补任务、不消费、不告警。

## 已确认事实与证据

- 客户 15215324 的任务 82716/82717/82718 均存在主动发送成功、平台 consume 200 和策略回传 200 证据。
- 三条记录随后被旧恢复路径的模型 503 污染为 `processing_retry`，当前已隔离为 `platform_legacy_quarantined`。
- 第三方当前 pending 无该客户任务，但 eventLogId 1692346 仍有 16 组未发送内容；该缺口发生在第三方任务生成之前。

## 已完成

- 生产取证与问题边界确认。
- 确定性恢复改为复用正常队列的客户锁与状态机，切断旧单任务模型入口。
- 管理页以主动发送成功证据覆盖旧恢复污染的错误状态，原错误保留在原始审计。
- 空 `pending` 无操作边界已补充确定性回归测试。

## 待办

- 合并 main 并统一部署验证。

## 测试结果

- 第三方 SOP 定向测试：94 passed。
- 全量测试：629 passed，1 条第三方依赖弃用警告。
- Ruff 与 Python compileall：通过。

## 发布与回滚

- 当前生产回滚点：`ai-paths-unified-20260909-v3-terminal-78622cf4`。

## 待沉淀的长期结论

- 无 pending 任务与 pending 任务缺内容是两种不同边界，前者无操作，后者仍属于第三方任务内容故障。
