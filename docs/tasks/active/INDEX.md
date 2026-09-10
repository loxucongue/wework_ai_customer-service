# 活跃任务

| Task ID | Owner | Branch | Base SHA | 独占范围 | 状态 |
| --- | --- | --- | --- | --- | --- |
| `sop-new-task-recovery-race` | Codex | `main` | `48a4101613d8765a4bb4bfa8b53ea32b3505220b` | `sop_platform_task_service.py`、SOP 确定性测试、第三方 SOP 合同与生产状态 | active |
开始新任务时，主 Agent 先在本表中新增一行，再创建对应 `<task-id>.md`。同一文件或目录不能被两个活跃任务同时登记。
