# DeepSeek AI 回复专项评测

- 类型：evaluation_tooling
- 基线：`main@f3bb12e7fc50f1f1293cf637d7103021942f357c`
- 分支：`codex/deepseek-reply-evaluation`
- 目标：用人工审核的真实会话回放、真实跟进策略和真实卡点话术，评测卡点匹配、压单时机和多问题优先级。
- 非目标：不修改线上 Prompt、业务规则、数据库、接口、功能开关或发送逻辑。
- 模型：Router、Reply、Critic 全部固定为 DeepSeek，不允许 GPT 或其他模型回退。
- 数据：只使用 `source_kind=real_replay` 的 30 条审核案例；策略目录使用版本化的 92 条策略和 522 条话术/素材。
- 副作用：运行在 Simulation 隔离目录和隔离 SQLite；平台写入进入模拟 outbox，不调用真实发送、生产数据库或生产写接口。
- 隐私：原始对话、模型输出和报告只写入 ignored 的 `.tmp_runtime/`，不进入 Git 或 docs。
- 验收：输出三项专项指标、失败案例清单、DeepSeek 实际模型清单和零真实发送证明；专项裁判仅作诊断，争议项交人工审核。
- 回滚：评测工具独立提交；不改变产品运行时，失败时删除该提交即可。

