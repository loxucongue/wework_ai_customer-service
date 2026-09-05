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
| 2026-09-03 | `store-matching-tool-contract` | `aab97716` | 门店匹配工具每次调用均保留本轮 `delivery_store_ids`，并收紧客户门店范围隔离、地址消歧和历史门店详情复用；确定性测试 25/25、233 店名称与完整地址 466/466、地址尾部 232/232、行政区矩阵 354/354 通过；未执行真实模型及部署后验证。 |
| 2026-09-03 | `store-matching-core-rebuild` | `cc4c47f3` | V3 门店工具完成语义目的地解析、受限地图校验、客户可见门店距离排序和独立交付合同；122 条真实只读矩阵中 114 条交付、7 条合理澄清、1 条无可见候选，0 错误、0 无锚点误发、0 回复/发送/写入；已部署并完成 12 条高风险样本复验。 |
| 2026-09-03 | `integrate-pending-v3` | `b32b8cc9` | 统一合入 V3 意图/情绪/逼单决策合同、跨轮 BI 埋点与只读订单归因，并择优吸收门店 WIP 的纯符号承接、门店卡文字包装和未核验接待承诺拦截；策略默认关闭、延时任务保持 shadow，117 条后端测试及前端生产构建通过，生产启用仍受 400 条人工金标与迁移门槛约束。 |
| 2026-09-03 | `v3-closing-catalog-integration` | `b8d2a156` | 租户逼单规则与策略目录并行融入现有 V3 Router→Reply 决策，按节点话术类型联查真实已发布话术，不增加模型调用；补齐来源、约束、主规则和策略节点 BI 观测，延时节点仍仅 shadow。 |
| 2026-09-04 | `local-closing-catalog-intent-audit` | `8029f076` | 新增 5 条逼单规则、5 套策略和 13 条话术的版本化本地目录，支持外部优先、仅本地和外部失败转本地三种来源模式；本地稳定 ID、类型关联和 checksum 不伪装平台数据，并形成意图、情绪、路由与 BI 的当前实现合同。 |
| 2026-09-04 | `v3-emotion-intent-completion` | `51dc9ec4` | 补齐 7 类意图和 8 类情绪的最低证据边界，明确爆粗不等于愤怒、投诉不等于退订；按置信度派生降压/暂停动作，统一本地或外部 B 单目录，并避免冗余 trigger 字段误杀有效策略。DeepSeek 隔离测试覆盖意图、情绪、卡点、退订和预约金，原始输出仅保存在 ignored artifacts。 |
| 2026-09-04 | `v3-real-identity-full-chain-eval` | `d6d72ac1` | 以 400 条真实身份会话完成全 DeepSeek 只读隔离评测：AI 初评通过率 53.8%、完整策略覆盖率 5.2%，序列/话术候选 88/97 但采用均为 0，判定候选版本不满足上线门槛；426 个评测请求生产库反查命中 0，脱敏报告仅保存于 ignored artifacts。 |
| 2026-09-05 | `v3-deepseek-reply-stability` | `91ac4016` | 修复批量评测预算起点、策略结构误兜底、知识采用统计和定向事实修复，治理 Router/Reply Prompt；DeepSeek 120 条策略覆盖 93.33%、初评 94.17%、序列/话术采用 8/8，经产品确认后部署，意图/情绪/本轮 B 单启用，延时节点保持 shadow。 |
| 2026-09-05 | `v3-closing-workbook-catalog` | `5486e82d` | 将业务《真人成交版》工作簿编译为 9 条规则、16 套策略、37 个节点和 42 条话术的版本化临时 B 单目录，保持 `external_then_local` 自动切换和延时节点 shadow；DeepSeek 真实身份隔离评测 60 条初评通过 93.3%、安全失败和生产写入均为 0，已发布到生产。 |
| 2026-09-05 | `v3-deepseek-human-reply` | `5b90e79e` | V3 最终 Reply、结构修复与完整重试统一为 `deepseek-chat` 且禁止 GPT fallback；连续消息与 12 条可见历史收敛、门店按需加载、真人表达及询价/投诉/广告事实边界、SOP 客户回复关联和多门店 3 卡合同完成修复。187 条回归通过，40 条真实身份 DeepSeek 初评/真人表达 97.5%、安全与生产写入均为 0；已全量发布，回滚点为 `5486e82d`。 |
| 2026-09-05 | `silence-outreach-ai-only` | `ce4eae8d` | 已开口沉默唤醒开放到全部企微账号，阈值 1 分钟且不限制加微时间；计划前和发送前均要求平台明确 AI 模式，人工/未知失败关闭，并以启用水位阻断历史积压、限制平台同步补偿重试。199 条回归及线上配置/worker/零误发审计通过，部署 release `ai-paths-unified-20260905-175600-ce4eae8d`。 |
| 2026-09-05 | `silence-outreach-console` | `fd8fb7bd` | 管理页产品语义统一为“沉默客户唤醒”，增加规则摘要、运行概览和中文阻断原因并完成桌面/手机端重构；线上确认全账号、1 分钟、不限加微时间、仅 AI 门禁均进入 worker，启用后 40 条真实判断全部被人工、未开口或关系失效条件安全阻断，0 计划、0 发送；前端 release `frontend-20260905-fd8fb7bd`。 |
