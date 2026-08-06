# 离线全链路仿真

## 目的

仿真运行时复用当前代码中的 SOP Gate、Planner、工具编排、Reply、校验、SOP Event 和状态持久化。它用于在发布前复现多轮客户场景，不调用生产业务接口，也不发送真实客户消息。

真实模型供应商是唯一允许的外部网络依赖。平台客户、门店、订单、支付、知识库、Coze 工作流和主动发送均由本地适配器提供。

## 隔离边界

每次运行都在 `.tmp_runtime/simulation/<run_id>` 下创建独立状态：

- `state.db`
- `memory/`
- `logs/` 和 `traces/`
- `outbox.json`
- 单场景 `result.json`
- 套件级 `result.json` 和 `report.md`

启动时会拒绝以下配置：

- 非 `sim_` 前缀的客户、企业或客服标识。
- 逃逸出本次运行目录的数据库、画像、日志或门店快照路径。
- 平台、主动发送或 Coze 业务凭证。
- 非 `simulation://` 的业务连接地址。
- 未声明 `simulation_adapter=true` 的业务适配器。

隔离检查失败时直接终止，不降级到生产客户端。

## 运行命令

```powershell
$env:PYTHONPATH = "ai_paths"

# 单场景，使用真实模型并进行独立语义评审
python ai_paths/scripts/run_full_chain_simulation.py `
  --scenario effect_with_case__v01 `
  --attempts 1 `
  --critical-attempts 1

# 按分类运行
python ai_paths/scripts/run_full_chain_simulation.py `
  --category 门店匹配 `
  --concurrency 2

# 全量发布门禁
python ai_paths/scripts/run_full_chain_simulation.py `
  --attempts 3 `
  --critical-attempts 5 `
  --concurrency 2 `
  --baseline .tmp_runtime/simulation/<baseline_run>/result.json

# 只验证结构和运行链路，不调用评审模型
python ai_paths/scripts/run_full_chain_simulation.py `
  --scenario sop_silence_progression__v01 `
  --skip-review
```

并发在运行时硬限制为最多 `2`。

## 场景格式

主数据集位于 `workflow_tests/fixtures/full_chain_simulation_v1.json`。一个场景由以下部分组成：

- `initial`：历史消息、客户事实、可见门店、案例、订单、SOP 进度、故障夹具。
- `timeline`：客户消息、定位卡、未知转账消息、SOP 事件、平台任务或时间推进。
- `followup`：第一轮最终消息进入虚拟历史后的下一轮客户输入。
- `semantic_goal`：独立评审模型使用的业务目标。
- `expected`：消息类型、门店 ID、预约金金额和禁止表达等结构合同。

模型故障通过 `initial.faults` 注入，随后仍走现有生产重试和 fallback：

```json
{
  "faults": {
    "model:planner": [
      {"mode": "timeout"},
      {"mode": "malformed_json"}
    ]
  }
}
```

支持的模型故障包括 `timeout`、`http_429`、`http_502`、`malformed_json` 和 `json_protocol`。

## 判定

硬校验用于确定性事实和结构：

- 客户应回复时不得为空。
- SOP 正在聊天阻断场景不得产生可见消息。
- 门店卡必须来自本场景可见门店范围。
- 同轮最多一个预约金卡，金额必须合法并符合场景合同。
- 必须或禁止的消息类型、门店 ID、金额和固定禁用表达符合场景声明。
- 任何业务写操作只能记录为 `simulation_only`。

语义评审按六项分别打分：当前问题、历史承接、主线推进、成交自然度、真人感和事实安全。供应商失败单独记录在 `provider_incidents` 或 `infrastructure_errors`，不计作业务语义失败。

套件报告还会输出 `review_artifacts`，用于人工 Review：

- 每条轨迹对应的 `request_id`、`event_id` 和运行目录。
- 节点 trace 名称，用来确认 SOP Gate、Planner、Reply 等链路是否实际执行。
- 工具调用名称，用来确认门店、案例、订单、语音等事实是否来自仿真适配器。
- 同步回复数量、虚拟 outbox 批次数和模拟写入次数。

这些字段只用于审查和发布门禁，不参与模型输入，也不改变生产回复。

套件报告同时输出 `coverage.schema_version=offline_simulation_coverage_audit_v1`，用于确认发布前仿真没有漏掉必测业务类别。当前必测类别包括门店、SOP 主线、效果案例、精准问答、项目范围、健康风险、预约金、已付登记、客户异议、明确拒绝、SOP Event、消息归一和模型恢复等。`summary.acceptance.scenario_coverage_complete` 必须为 `true`，否则不能把报告作为行为切换证据。

仿真报告只用于审核和发布门禁，不会自动修改 Prompt、部署或发送客户消息。

## 凌晨报告覆盖

`2026-07-23 22:00` 至 `2026-07-24 09:00` 报告中的主要问题已映射到首版场景：

| 历史问题 | 仿真场景 |
|---|---|
| SOP 重复破冰、跳阶段、沉默触达 | `sop_silence_progression`、`sop_duplicate_avoidance`、`sop_chatting_block` |
| 效果答疑无案例图 | `effect_with_case`、`sop_silence_progression` 的第二轮 |
| 答疑后不回主线 | `one_session_precision`、`price_transparency`、`store_far` |
| 软拒绝送客、明确拒绝仍压单 | `soft_refusal`、`hard_refusal` |
| 痘印痘坑及其他项目范围错误 | `hand_scope`、`unsupported_projects` |
| 门店越权、县镇地名和定位卡 | `province_scope` 至 `location_card` 的门店矩阵 |
| 预约金、转账、已付和重复卡 | `payment_after_activity`、`manual_transfer`、`paid_registration` |
| 未知转账消息 | `unknown_transfer_message` |
| 语音 URL 转写后进入正常回复 | `voice_transcription` |
| 模型超时、429、502和畸形 JSON | `model_failure_recovery` |

数据集中使用合成客户标识、虚构安全 URL 和脱敏电话号码，不保存报告中的昵称、客户 ID、外部用户 ID 或支付凭证。
