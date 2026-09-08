# 第三方 SOP 单任务执行手册

- status: current
- owner: SOP/platform integration
- last_verified: 2026-09-08 Asia/Shanghai
- implementation: `ai_paths/app/services/sop_platform_task_service.py`
- contract: `docs/contracts/third-party-sop-v3.md`

## 单任务流程

| 顺序 | 调用/动作 | 成功结果 | 失败结果 |
|---:|---|---|---|
| 1 | `POST /event/trigger/pending` | 取得到期任务 `taskId/eventLogId` | 没有具体任务ID时只记录系统异常；已取得的任务继续逐条处理 |
| 2 | 持久化原始任务 | 建立任务幂等与审计 | 暂时保留在上游 pending，恢复后继续 |
| 3 | 并行查询 `conversation/status` 与 `conversation` | 取得 `ai_auto_reply`、客户关系和消息列表 | 接口/字段失败：任务与内容均不消费，恢复并预警 |
| 4 | 判断未开口、未删除、AI托管 | 三项全真才继续 | 客户开口/删除/人工：任务70；字段缺失或不合法：任务不消费并预警 |
| 5 | `POST /event/trigger/sop-messages` | 按 `eventLogId` 取得 `nextGroup` | 任务与内容均不消费，恢复并预警 |
| 6 | 校验第一组 | 必须有 `id/msgId` 和至少一条合法消息 | 任务与内容均不消费，恢复并预警 |
| 7 | `POST /api/v1/platform-agent/ai-outreach/send` | 原样发送整组内容；接口正常返回即进入成功路径 | 接口直接报错：任务与内容均不消费；超时只查证据，不重发 |
| 8 | 保存调用事实 | 保存 `taskId`、`msgId`、幂等键、消息快照和响应 | 保存完成前不得做联合消费 |
| 9 | `POST /event/trigger/consume` | `taskId=30` 且 `messages=[{msgId,status:30}]` | 保持完成待回传，只重试消费，不重发 |
| 10 | `POST /event/trigger/service-rule-data` | 完成任务 | 保持策略回传待恢复，不重发、不重复消费其他内容 |

## 消费请求模板

成功发送第一组：

```json
{
  "taskId": 101,
  "status": 30,
  "remark": "",
  "messages": [
    {"msgId": 701, "status": 30, "remark": ""}
  ]
}
```

业务已承接（仅客户已开口、客户删除、人工接管）：

```json
{
  "taskId": 101,
  "status": 70,
  "remark": "customer_already_opened"
}
```

技术失败不调用 `/consume`。业务终态请求不得出现 `messages`，成功请求只能出现本轮实际调用发送接口的一条内容组结果。

## 运维核对

- 看到任务30时，消费审计中必须同时有且仅有一个 `messages[].msgId=30`。
- 看到任务70时，请求中必须没有 `messages`，原因只能是客户已开口、客户删除或人工接管。
- `/sop-messages` 的调用时间必须晚于三项客户门槛查询。
- 正常执行日志不应出现模型调用、过渡语或AI改写内容。
- 不允许把同一客户的其他兼容任务随当前任务一并消费；后续任务必须回到 pending 后独立执行。
- 发送接口已正常返回但消费/策略回传失败时，不得再次调用主动发送接口。
- 管理页人工补发在本合同下关闭，避免终态迁移和隐式内容消费。
- 客户已开口、客户删除和人工接管不发钉钉失败预警。
- 参数、资格数据、内容、接口和发送失败保持未消费；每个任务最多预警一次，并标记失败类型与责任方向。
