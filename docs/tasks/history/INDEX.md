# 已完成任务索引

这里只记录可追溯摘要；聊天、运行报告和测试输出不归档。需要完整细节时查对应 Git 提交。

| 完成日期 | Task ID | Main commit | 长期结论 |
| --- | --- | --- | --- |
| 2026-09-02 | `runtime-factories` | `5c81f302` | Reply、Control、Worker 改为直接服务工厂；见 [运行边界](../../contracts/RUNTIME_BOUNDARIES.md)。 |
| 2026-09-02 | `v3-sales-strategy-flow` | `fc52a70f` | V3 跟进策略/卡点话术接入提示词优化、follow-knowledge 本地同步、真实样本评测、BI usage/outcome 埋点和只读分析接口；原始客户记录和评测输出仅保存在 ignored artifacts。 |
| 2026-09-02 | `store-workflow-accuracy` | `8ed24b85` | V3 门店匹配工作流收敛区县/POI 候选、降低片段 geocode 误冲突、城市无本地店不再直接退到省级列表；严格矩阵 31/31 通过，报告在 ignored artifacts。 |
| 2026-09-02 | `store-workflow-full-coverage` | `07c14dc0` | V3 门店链路补强短地名无父级锚点确认、结构化位置文本兜底、导航/地址重发复用最近门店事实；DeepSeek + 门店库组合矩阵 61/61 通过，报告在 ignored artifacts。 |
