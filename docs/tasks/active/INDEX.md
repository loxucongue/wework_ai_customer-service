# 活跃任务

| Task ID | Owner | Branch | Base SHA | 独占范围 | 状态 |
| --- | --- | --- | --- | --- | --- |
| `v3-material-delivery-governance` | 独立执行窗口 | `codex/v3-material-delivery-governance` | `c6df823047738bcef5e5a0646ca87c6242826dae` | 冻结候选 `8d0e39f2`，等待独立验收；原窗口不得继续写入、合并或部署 | candidate_ready_frozen |
| `v3-material-delivery-governance-review` | 独立验收窗口 | `codex/v3-material-delivery-governance-review` | `dafbeee8494c7f5f7de53a6e969f79329968b8d6` | 冻结验收候选 `a83a647f`，原窗口不得继续写入、合并或部署 | review_passed_frozen |
| `v3-material-delivery-governance-integration` | 独立集成窗口 | `codex/v3-material-delivery-governance-integration` | `83895fe548415afce9167f83617f4ee63a3bc1dd` | 仅集成已验收候选、重跑关键证据并关闭任务文档；不迁移生产、不部署 | active |
开始新任务时，主 Agent 先在本表中新增一行，再创建对应 `<task-id>.md`。同一文件或目录不能被两个活跃任务同时登记。
