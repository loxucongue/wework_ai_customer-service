# 已完成任务索引

这里只记录可追溯摘要；聊天、运行报告和测试输出不归档。需要完整细节时查对应 Git 提交。

| 完成日期 | Task ID | Main commit | 长期结论 |
| --- | --- | --- | --- |
| 2026-09-02 | `runtime-factories` | `5c81f302` | Reply、Control、Worker 改为直接服务工厂；见 [运行边界](../../contracts/RUNTIME_BOUNDARIES.md)。 |
| 2026-09-02 | `v3-sales-strategy-flow` | `fc52a70f` | V3 跟进策略/卡点话术接入提示词优化、follow-knowledge 本地同步、真实样本评测、BI usage/outcome 埋点和只读分析接口；原始客户记录和评测输出仅保存在 ignored artifacts。 |
| 2026-09-02 | `store-workflow-accuracy` | `8ed24b85` | V3 门店匹配工作流收敛区县/POI 候选、降低片段 geocode 误冲突、城市无本地店不再直接退到省级列表；严格矩阵 31/31 通过，报告在 ignored artifacts。 |
| 2026-09-02 | `store-workflow-full-coverage` | `07c14dc0` | V3 门店链路补强短地名无父级锚点确认、结构化位置文本兜底、导航/地址重发复用最近门店事实；DeepSeek + 门店库组合矩阵 61/61 通过，报告在 ignored artifacts。 |
| 2026-09-02 | `store-reply-output-coverage` | `63ad425a` | V3 最终 Reply 门店输出补强：无本地门店不被距离排序覆盖成发卡、结构化地址输入必走门店工具、Reply 校验读取 joined facts、模型失败时仅对已核验门店卡做最小文本包装；隔离真实 V3 Reply 矩阵 62/62 通过，报告在 ignored artifacts。 |
| 2026-09-02 | `store-output-exhaustive-eval` | `bc124ebe` | V3 门店最终回复扩大到 160 条真实链路矩阵，补强泛地标/短区县/同名区域/地址与 geocode 行政区冲突/省份级追问边界；服务器隔离全量 160/160 通过，平均 9318ms、P95 13181ms，报告在 ignored artifacts。 |
| 2026-09-03 | `store-output-adversarial-eval` | `e9054758` | V3 门店工作流补强唯一门店文本引用、原始地址尾部匹配、无本地门店/需补位置输出边界、到店时间失败恢复和未确认预约承诺拦截；服务器隔离扩展矩阵 121/121 通过，233 家门店直接候选覆盖 925/925 通过，报告在 ignored artifacts。 |
