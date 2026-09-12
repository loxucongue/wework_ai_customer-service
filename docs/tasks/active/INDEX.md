# 活跃任务

| Task ID | Owner | Branch | Base SHA | 独占范围 | 状态 |
| --- | --- | --- | --- | --- | --- |
| v3-refund-fallback-repair | 当前执行窗口 | `codex/v3-refund-fallback-repair` | `a42b66b4981801f0b921a668584795d8253458cb` | V3 Reply 终止态动作归一、直接回归测试；不改 Prompt、销售策略或兜底文案 | deploying_and_post_release_evaluation |
| v3-payment-activity-latency-repair | 当前执行窗口 | `codex/v3-payment-activity-latency-repair` | `9c2c72f954d1382e08ba2e0eb25aaa838f8c722e` | V3 付款结构一致性、活动完整性上下文、Reply 热路径只读性能与对应测试；不改 MytRpc、SOP、发送协议或数据库 schema | stage2_deployed_natural_traffic_monitoring |

开始新任务时，主 Agent 先在本表中新增一行，再创建对应 `<task-id>.md`。同一文件或目录不能被两个活跃任务同时登记。
