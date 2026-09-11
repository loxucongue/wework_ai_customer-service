# 活跃任务

| Task ID | Owner | Branch | Base SHA | 独占范围 | 状态 |
| --- | --- | --- | --- | --- | --- |
| v3-refund-fallback-repair | 当前执行窗口 | `codex/v3-refund-fallback-repair` | `a42b66b4981801f0b921a668584795d8253458cb` | V3 Reply 终止态动作归一、直接回归测试；不改 Prompt、销售策略或兜底文案 | deploying_and_post_release_evaluation |
| `sop-timeout-recovery` | 当前独立执行窗口 | `codex/sop-timeout-recovery` | `31882257f955dcf2c2c366755ebd18534ee9d0a5` | 第三方 SOP worker 并发、只读接口连接超时恢复、失败告警抑制、客户门槛接口错误归因及对应测试/合同 | release_authorized |

开始新任务时，主 Agent 先在本表中新增一行，再创建对应 `<task-id>.md`。同一文件或目录不能被两个活跃任务同时登记。
