# 活跃任务

| Task ID | Owner | Branch | Base SHA | 独占范围 | 状态 |
| --- | --- | --- | --- | --- | --- |
| v3-reply-effectiveness-optimization | 当前执行窗口 | `codex/v3-reply-effectiveness-optimization` | `93a99f5c` | 最终只保留评测设施与规范；正式 Reply 行为保持 main | ready_for_closeout_no_winner |
| customer-reception-state | 当前执行窗口 | `codex/customer-reception-state` | `e2faaccd` | 独立控制面接待状态接口、专用授权、状态存储/迁移和直接测试；不接管现有消费者 | release_authorized_pending_identity_configuration |
| v3-refund-fallback-repair | 当前执行窗口 | `codex/v3-refund-fallback-repair` | `a42b66b4981801f0b921a668584795d8253458cb` | V3 Reply 终止态动作归一、直接回归测试；不改 Prompt、销售策略或兜底文案 | deploying_and_post_release_evaluation |
| v3-payment-activity-latency-repair | 当前执行窗口 | `codex/v3-payment-activity-latency-repair` | `9c2c72f954d1382e08ba2e0eb25aaa838f8c722e` | 保留付款结构、热路径性能和自然流量监测；Prompt/上下文及效果测试交接给效果优化任务 | stage2_deployed_natural_traffic_monitoring |

开始新任务时，主 Agent 先在本表中新增一行，再创建对应 `<task-id>.md`。同一文件或目录不能被两个活跃任务同时登记。
