# 已知问题

- status: active
- source_of_truth: 当前代码与发布任务现场核验

1. 生产尚未确认是否已使用同一 `main` SHA 构建 reply、control 和 worker；下一次发布前必须现场核验并记录回滚点。
2. AI 销售策略、延时逼单和四大区跟进尚未完成业务人工验收，继续保持关闭或 shadow；不得以策略目录存在为由启用真实发送。
3. 历史测试套件与评测资产已从仓库移除。后续功能变更必须由对应任务自行建立最小、隔离的验证，再决定是否发布。
4. `chat_runtime.py`、`reply_nodes.py`、`reply_validation.py` 和 `sop_platform_task_service.py` 仍是超大核心模块。新功能必须落到已有职责边界内；没有明确重构任务和回归证据时，不得顺手重写或横向搬动这些模块。
