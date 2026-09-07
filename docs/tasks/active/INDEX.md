# 活跃任务

| Task ID | Owner | Branch | Base SHA | 独占范围 | 状态 |
| --- | --- | --- | --- | --- | --- |
| appointment-availability-boundary | Codex | codex/appointment-availability-boundary | fafe3dea04722185a38c5321dfe07ae177ea8d7d | V3 预约准入校验、Reply/全局提示词、业务规则及对应最小验证 | active |
| `v3-direct-booking-closing` | Codex | `codex/v3-direct-booking-closing` | `fafe3dea04722185a38c5321dfe07ae177ea8d7d` | `ai_paths/app/services/v3_semantic_router_service.py`、`ai_paths/app/prompts/v3_semantic_router.py`（若无并行占用才修改）、独立 B 单召回辅助模块、`tests/test_v3_closing_catalog_integration.py`、相关评测脚本与任务文档 | 进行中 |
| store-list-delivery | codex | codex/store-list-delivery | fafe3dea04722185a38c5321dfe07ae177ea8d7d | V3 门店目的地解析、事实输出、Reply 校验与门店测试 | active |
开始新任务时，主 Agent 先在本表中新增一行，再创建对应 `<task-id>.md`。同一文件或目录不能被两个活跃任务同时登记。
