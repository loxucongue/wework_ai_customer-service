# V3 可信黄金集使用边界

`workflow_tests/fixtures/v3_trusted_golden_set_v1.json` 是离线评测标准，不是线上话术库。

## 允许用途

- 校准离线 Critic。
- 评估 Gate Recall、False Nomination、Reply Adoption、False Adoption 和 Delivery Completion。
- 评估当前问题解决、自然推进、错误暂停、错误挽回和事实安全。
- 对比不同 Prompt、模型和 Gate `top_k` 的离线结果。

## 禁止用途

- 不得导入运行时 Prompt、Shared Context、Gate、Tool Planner、Reply 或事实审计。
- 不得按客户关键词检索相似黄金案例并复制答案。
- 不得使用参考回复做文本相似度评分。
- Critic 不得进入线上链路，不得修改、阻断或补写客户回复。
- 单条失败不得直接转化为新的 Python 业务分支或场景补丁。

## 评分方式

评分依据是每条轨迹中的客户目标、必答点、可接受动作集合、素材召回与交付要求、禁止动作和事实边界。参考回复只说明一种可接受方向，不是唯一答案。

正式分区为 15 条 Critic 校准样本和 35 条保留评测样本。修改 Prompt 或 Critic 时不得查看并针对保留集逐条调参。
