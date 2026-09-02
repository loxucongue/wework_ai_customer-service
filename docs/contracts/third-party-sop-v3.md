# 第三方 SOP V3 合同

- status: current
- owner: SOP/platform integration
- last_verified: 2026-08-31 Asia/Shanghai
- source_of_truth: 当前 main SOP 代码与生产日志；动态状态必须现场核验

## 消费状态

- `20`：任务已占用，等待发送结果；不是终态。
- `30`：消息真实发送完成；消费终态。
- `70`：无需发送；消费终态。
- `contentExhausted`：内容序列是否耗尽，与单任务是否终态分开。

所有拉取并进入终态的任务都必须完成回传。等待送达回调时不能提前回传 `30`；发送失败不能伪造 `30`。接口失败必须保存请求、响应、错误和时间，并由恢复任务重试。

## 两段式任务来源

第三方 SOP 是一条业务链，不是两个独立产品队列：

```text
/event/trigger/pending              到期触发节点（新任务可能没有消息内容）
/event/trigger/store-visit-pending  对应的实际发送内容
→ 按客户、接待 WeChat 和平台运行标识配对
→ 判断/发送 → consume → service-rule-data → 送达确认
```

- 触发节点决定任务何时到期；不得因为其内容为空而丢弃或直接消费。
- 内容节点提供可发送消息；配对成功后必须保留触发节点与内容节点的关联审计。
- 任一来源读取失败时，本轮不发送、不消费；保留失败事实，等待下次轮询恢复。
- 夜间命中禁止发送时，所有关联任务直接回传 `70` 和 `sop_no_send_quiet_hours`，不创建次日融合或延迟重放任务。

## 两类独立回传

1. 消费接口 `/event/trigger/consume`：记录占用和终态消费状态。
2. 策略数据接口 `/event/trigger/service-rule-data`：记录本次处理场景。

消息送达回调只提供真实送达事实，不能替代以上两个接口。

## 策略场景字段

`service-rule-data` 必须发送 `sceneCode`、`sceneName`、`remark`，不得把平台原始 scene 或模型文本直接当枚举值。当前枚举：

| sceneCode | sceneName | remark | 终态 |
|---|---|---|---|
| `sop_sent` | SOP发送成功 | SOP消息已发送 | 30 |
| `sop_send_failed` | SOP发送失败 | SOP消息发送失败 | 失败事实 |
| `humantakeover` | 人工接管 | 当前会话由人工接待 | 70 |
| `customer_deleted` | 客户删除 | 客户关系已删除 | 70 |
| `sop_no_send_all_filtered` | 暂无合适内容 | 当前没有适合发送的SOP内容 | 70 |
| `sop_no_send_skipped_prefix` | 前序内容已覆盖 | 前序SOP内容已处理，由后续可发送任务承接 | 非最终跳过 |
| `sop_no_send_compat_resolved` | 兼容任务已处理 | 任务内容已从待消费内容队列处理 | 兼容口径 |
| `sop_no_send_duplicate` | 内容重复 | SOP内容已发送，不重复触达 | 70 |
| `sop_no_send_invalid_content` | 平台内容无效 | 平台任务没有合法消息内容 | 70 |
| `sop_no_send_quiet_hours` | 夜间禁止 | 夜间SOP不发送 | 70 |

## 未开口规则

当权威会话事实确认客户未开口时，SOP 内容直接发送，不调用模型决定是否发送。会话拉取失败或客户消息时间不可靠时不得把客户当作未开口。

## 日志审计

管理页日志版本为 `sop_platform_run_view_v3`，历史 V2 只做展示兼容。任务审计必须区分模型决策、提交发送、真实送达、消费回传和策略数据回传，并保留完整业务 JSON；不得记录 token、鉴权头或密钥。
