# V3 退款场景兜底修复

- 类型：运行时结构一致性修复
- 基线：`origin/main@a42b66b4981801f0b921a668584795d8253458cb`
- 分支：`codex/v3-refund-fallback-repair`
- 目标：当 Reply 已将具体投诉/退款场景判定为 `hard_stop_marketing` 时，避免内部销售动作枚举冲突导致合法客户回复被拒绝并落入统一兜底。
- 非目标：不修改统一兜底“您稍等一下”，不修改 Reply Prompt、销售语义、软拒绝处理、主动营销节奏、素材、主线、SOP、配置、数据库或部署状态。
- 根因证据：隔离全图回放中，主回复已生成退款核实话术，但 `customer_state=hard_stop_marketing` 与 `next_sales_action.type=keep_open` 冲突；admission 返回 `hard_stop_requires_stop_action`，修复失败后进入 `failure_fallback`。
- Change contract：仅在模型自己的结构化 `hard_stop_marketing` 决策已经成立时，把非空且非 `stop` 的内部动作枚举归一为 `stop`；客户文字、事实、客户心理判断及销售场景选择不由代码改写。
- 最小文件集：`ai_paths/app/graph/nodes/reply_generation.py`、直接确定性测试、本任务文档与 active INDEX。
- 风险：不得把普通咨询或软拒绝归为停止；以非 hard-stop 负例及可见回复不变断言覆盖。修复不持久化 stop-contact，退款与明确退订边界维持现有合同。
- 验证：直接单元测试、V3 Reply 相关确定性测试、全量确定性测试；使用同一虚构退款场景做隔离全图复测，硬阻断发送。
- 发布：产品负责人于 `2026-09-11` 明确授权候选合入 `main`、部署后端并继续 V3 全场景隔离评测；不授权客户测试发送、生产数据库写入、配置/开关调整或前端改动。
- 回滚：撤销本任务候选提交即可；无数据迁移或外部副作用。

## 发布与部署后验收

- 合入前要求：`origin/main` 仍为基线，候选可 fast-forward；main 树干净，完整 SHA 写入 release manifest。
- 部署范围：三个后端角色使用同一 clean main SHA；无迁移、配置、模型参数或前端变化。
- 回滚点：部署前现场 current release；切换前验证 previous 指针，并保存本次发布清单。
- 部署后检查：control/reply/worker 的 release、role、V3 health、Nginx、回调/管理页可用性、重启次数和错误日志。
- 全场景评测：使用虚构身份、隔离 SQLite、合成外部事实、真实 DeepSeek；HTTP 重放和 finalization 进入完整 L3，发送及策略外发必须为零。产品效果按现行主动销售口径人工解释，不把正常价值推进误报为缺陷。
- 停止/回滚阈值：发布身份不一致、健康失败、迁移变化、持续 5xx、真实发送/生产写风险或退款场景再次落 failure fallback。

## 完成证据

- 状态：`ready_for_review`
- 直接相关测试：`106 passed`。
- 全量确定性测试：`1002 passed, 12 skipped`；跳过项为既有环境型测试。
- 静态检查：修改文件 `ruff` 通过，`git diff --check` 通过。
- 隔离 L3：虚构退款场景连续 `3/3` 为 `main_model`，`0` 次 targeted repair，`0` 次 failure fallback；HTTP 重放和 finalization 均通过。
- 性能：完整隔离 HTTP 生命周期 `4919/5076/8456 ms`（min/P50/max），首次 HTTP `4688/4840/8186 ms`；每轮仅一次 Router 和一次 Reply 模型调用。
- 副作用：`message_dispatches=0`、`strategy_data_outbox=0`；生产数据库未写入，未发送客户消息，未部署或重启服务。
- 边界复核：普通 `continue_sales` 与 `pause_current_turn` 动作不改写；客户可见退款回复不改写；本修复不创建持久 stop-contact。
- 原始输出：仅保存在 ignored `artifacts/v3-refund-fallback-repair/`，不进入 Git。
