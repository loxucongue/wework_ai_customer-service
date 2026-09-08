# v3-protocol-fast-ignore-outreach-cadence

- status: active
- owner: Codex
- base_branch: main
- base_sha: `73b723354e49c020e09e1b036f9c40bc66bf3e73`
- production_verified_at: `2026-09-08T11:29:15+08:00`
- production_releases: `control/reply/worker=ai-paths-unified-20260908-092900-42b27eae@42b27eaede76b329d86880668e6a20ad9aa0dd85`

## 目标

- 让企业微信固定开场和撤回等协议消息在 AI/人工状态查询前快速结束，同时保留轻量、幂等的运行审计。
- 将沉默唤醒监控调整为 15 秒目标间隔、串行执行、最少 5 秒间隔和最长 60 秒异常退避。
- 用回归测试锁定当前已经上线的不限加微时间、动态序列节点、失败恢复和发送前门禁。

## 非目标

- 不修改销售 Prompt、跟进序列、话术选择、客户可见回复或数据库 schema。
- 不修改普通非沉默 Outreach 的每日频次限制。
- 不重构一般 V3 图后持久化。

## Change contract

- type: runtime latency fix + worker scheduling hardening
- scope: V3 reply protocol fast path、outreach monitor cadence、tests、contracts/current docs
- risk: 协议消息误判会漏掉真实客户消息；扫描过密会放大 RDS/平台压力。
- validation: 协议消息依赖零调用和幂等测试；沉默唤醒动态节点/跨日/恢复/门禁回归；L4 服务与 Worker 三轮现场核验。
- rollback: 恢复发布前 clean release；仅扫描压力异常时先恢复 Worker 60 秒配置。

## 涉及模块与文件所有权

- `ai_paths/app/routers/reply.py`
- `ai_paths/app/chat_runtime.py`
- `ai_paths/app/config.py`
- `ai_paths/app/workers/supervisor.py`
- `tests/` 中协议过滤、运行日志与沉默唤醒相关测试
- 本任务文档、相关接口/架构/current 文档和历史索引

## 不可破坏合同

- V3 是唯一客户回复入口；普通消息必须继续先核对平台 AI/人工状态。
- 协议消息不得调用模型、工具、语音、策略或发送链，但必须保留可审计运行记录和完整 HTTP 耗时。
- 沉默发送前必须继续核对 AI 模式、客户新回复、退订和订单终态；状态未知失败关闭。
- 客户状态按 `corp_id + wechat + external_userid/customer_id` 隔离。

## 已确认事实与证据

- 指定忽略日志 `a71c34bd-1a0b-4444-bb24-bfaa40388d60` 总耗时 15.284 秒，模型调用为 0，但平台 AI/人工状态查询发生在协议过滤之前。
- 最新 main 已取消沉默唤醒的加微首日限制及每日 2 计划/4 任务限制；首日兼容路径会完整物化外部序列节点。
- 当前代码在每轮扫描完成后固定再等待配置间隔；生产最后记录为 60 秒，候选全量扫描约 33.5～35.3 秒。

## 已完成

- 建立独立 worktree、分支和文件所有权登记。
- 现场确认 control、reply、worker 与前端均 active，三个后端角色为同一 clean SHA 且 `NRestarts=0`。
- 协议消息在接管状态、语音、消息协调和模型链之前进入轻量运行审计；协议内容不再写入消息表或触发策略/主动唤醒副作用。
- 协议消息使用平台消息身份生成稳定请求 ID，进程内和进程重启后均保持单条审计。
- 沉默监控使用 15 秒目标起始间隔、最少 5 秒间隔和 15/30/60 秒失败退避；健康状态公开实际配置和运行退避。

## 待办

- 完成测试、合并、发布和 L4 核验。

## 测试结果

- 协议快速路径与沉默门禁专项：32 passed。
- Outreach、V3 生命周期、运行日志和策略 BI 组合回归：98 passed。
- Ruff 与 Python 编译检查通过。
- 60 次完整本地 HTTP 快速路径：P50 81.44ms、P95 135.88ms、max 226.61ms；状态查询、语音、消息协调器、模型调用均为 0；客户消息和策略 usage 写入均为 0。

## 发布与回滚

- pending

## 待沉淀的长期结论

- 协议事件不需要 AI/人工状态授权；只有可能产生客户可见动作的请求才进入该门禁。
- Worker 轮询间隔按两轮开始时间管理，失败采用有界退避，不能在长扫描后无条件再睡完整周期。
