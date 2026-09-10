# 活跃任务

| Task ID | Owner | Branch | Base SHA | 独占范围 | 状态 |
| --- | --- | --- | --- | --- | --- |
| `v3-material-delivery-governance` | 独立执行窗口 | `codex/v3-material-delivery-governance` | `c6df823047738bcef5e5a0646ca87c6242826dae` | 冻结候选 `8d0e39f2`，等待独立验收；原窗口不得继续写入、合并或部署 | candidate_ready_frozen |
| `v3-material-delivery-governance-review` | 独立验收窗口 | `codex/v3-material-delivery-governance-review` | `dafbeee8494c7f5f7de53a6e969f79329968b8d6` | 候选代码审查、MySQL 迁移/并发、完整前端构建、全量回归与集成建议；不部署 | active |
开始新任务时，主 Agent 先在本表中新增一行，再创建对应 `<task-id>.md`。同一文件或目录不能被两个活跃任务同时登记。
