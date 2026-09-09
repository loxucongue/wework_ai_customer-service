# 第三方 SOP 恢复与告警去重修复

- 目标：阻止已成功发送任务被恢复线程重复执行或降级为失败；保证同一告警只投递一次。
- 非目标：不修改客户开口/删除/人工接管业务门禁，不修改第三方消费协议、数据库 schema 或消息内容。
- Base SHA：`343f9d3586dcfd7f3000b978c4f5b5774ff70562`
- 生产基线：`ai-paths-unified-20260908-v3-latency-7b1c01f7` / `7b1c01f7877321b3eb729cefbed91f0cc09ba6c8`
- 独占模块：`ai_paths/app/services/sop_platform_task_service.py`、`ai_paths/app/services/sop_failure_alert_service.py`、`ai_paths/app/services/storage/sop_event_repository.py`、对应 SOP 测试。
- 不可破坏合同：未发送任务不得越过；发送成功后只恢复消费/回传，不得再次主动发送；业务已承接状态不告警；默认测试不得触发生产写入。
- 风险：成功证据判定过宽可能隐藏真实发送失败；告警抢占租约错误可能导致告警卡住。
- 验证：并发恢复回归、发送态不可降级、告警原子抢占/过期恢复、SOP 合同测试和全仓确定性测试。
- 发布：验证后合并最新 `main`，三个后端角色使用同一 clean SHA；重点验证 worker、V3、共享控制面和 SOP 健康信息。
- 回滚：发布前线上统一 release `ai-paths-unified-20260908-v3-latency-7b1c01f7`。

## 状态

- 已完成：事故取证；恢复线程跳过当前排队/执行任务；`platform_processing` 恢复改走同一客户锁和确定性路径；锁内重读完成态；成功发送证据不可被恢复失败降级；告警投递增加两分钟原子租约并兼容崩溃后回收；本地业务已承接事实可抑制包装后的误告警。
- 测试证据：SOP 专项 89 条通过；全仓 622 条通过；变更文件 Ruff 和 Python 编译通过。测试只使用合成数据，未调用生产写接口或发送客户消息。
- 待办：提交、合并、发布和线上观察。
