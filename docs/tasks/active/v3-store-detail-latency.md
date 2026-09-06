# V3 store detail latency and duplicate delivery

- Type: production reply correctness and latency hotfix
- Base SHA: `8a120f249142d50659eff07c70076d177b20bd4c`
- Production baseline: `ai-paths-unified-20260906-145203-8a120f24`, clean `main`
- Evidence request: `caaca684-1429-4a6e-b0a4-e1c4c612022b`
- Goal: 已发送门店卡后，客户只问停车、营业时间等非地址详情时不重复发卡；答清后自然推进一个相关下一步；降低同步记忆写入造成的回复延迟。
- Non-goal: 改变门店事实来源、自动预约/付款权限、增加模型调用或发送测试客户消息。
- Ownership: `action_module_outputs.py`、Reply 门店合同与 Prompt、memory repository 及对应测试。
- Root cause: 门店 `reuse_confirmed_store` 状态只剩消费合同、生产端不再生成；硬交付约束覆盖了 Prompt 的“不重复发卡”提示。`save_memory` 每次逐条重放最多 100 条 history event，同一回复又连续保存多次，造成数百次数据库往返。
- Risk: 去重过宽会挡住客户明确索要地址/导航；批量写入必须同时兼容 SQLite/MySQL/Mirrored store。
- Completed:
  - 非地址门店详情在同卡已真实发送时转换为 `reuse_confirmed_store`；明确索要地址/导航仍允许重发。
  - Reply 只回答停车等权威详情，不重复门店卡或完整地址，并自然推进一个到店相关问题。
  - `history_events` 从逐条插入改为单次批量插入，保留原幂等语义。
- Validation:
  - 定向门店、Prompt、存储回归：126 passed。
  - 全仓确定性回归：281 passed。
  - DeepSeek 真实日志隔离重放：仅一条或两条不同价值的 text，无 `store_address`，不复述完整地址，继续询问工作日/周末到店偏好；端到端 11.685s；DeepSeek-only；生产写入和发送尝试 0。
- Rollback: production release `ai-paths-unified-20260906-145203-8a120f24`.
