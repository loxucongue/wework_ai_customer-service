# V3 DeepSeek Reply 稳定性与隔离重测

- status: active
- owner: reply-runtime
- base_branch: main
- base_sha: `8f9e4dcb5fe42590da2b93b58ead2437001e5188`
- production_verified_at: 本任务不部署，生产状态不作变更
- production_releases: 未核验；不在本任务范围

## 目标

- 修复 V3 最终 Reply 因策略合同过严而放大为兜底的问题。
- 将真实样本评测改为 DeepSeek 运行阶段与 DeepSeek 评审阶段彻底分离，输出可定位的失败分类。
- 提交后使用真实客户身份只读重测；不发送、不写生产数据。

## 非目标

- 不部署生产，不修改消息发送、交易、订单写入或外部业务接口。
- 不新增关键词销售规则、第二个选择模型或跨卡点 fallback。
- 不把业务候选存在等同于必须采用。

## Change contract

- type: V3 Reply 稳定性修复与离线评测工具完善
- scope: Reply Prompt、策略一致性校验、失败诊断、DeepSeek 两阶段隔离评测、对应测试和合同
- risk: 放宽冲突校验可能掩盖错误推进；评测节流会增加总耗时；评测工具误接生产写接口会造成数据污染
- validation: 确定性回归、DeepSeek 小批运行门、同批真实身份分层重测、零发送/零生产落库审计
- rollback: 回退本任务提交；生产侧无需回滚，因为本任务不部署

## 涉及模块与文件所有权

- `ai_paths/app/prompts/reply_synthesizer.py`
- `ai_paths/app/graph/state.py`
- `ai_paths/app/graph/nodes/reply_generation.py`
- `ai_paths/app/graph/nodes/reply_nodes.py`
- `ai_paths/scripts/evaluate_v3_full_chain_deepseek.py`
- `tests/test_v3_policy_decision_contract.py` 及本任务新增评测测试
- `docs/contracts/sales-strategy.md`、`docs/contracts/v3-intent-emotion-routing.md`

## 不可破坏合同

- V3 Reply 仍是唯一销售语义决策点；代码不判断普通销售意图。
- 明确退订、投诉/高置信愤怒、健康风险、人工接管和事实/交易边界继续强校验。
- 跟进候选、逼单规则、策略和话术只作参考；采用必须来自本轮真实 ID。
- 测试模型全部为 DeepSeek，禁止 GPT 回退；测试不发送、不写生产数据库、BI、outbox、dispatch 或 Shadow。

## 已确认事实与证据

- 2026-09-04 的 400 条报告中，284 条确定性兜底、95 条门店恢复、8 条策略安全恢复，仅 13 条主模型正常输出。
- 21/400 是完整策略结构覆盖率，不是已覆盖样本的分类准确率；有效策略行的意图/情绪与独立评审一致。
- 13 条主模型结果中只有 1 条带候选，且该样本为明确退出，因此最终序列与话术采用均为 0 是当前样本执行结果的必然产物。
- 原评测把运行模型与 DeepSeek 评审模型串在同一持续批处理中，共享提供方负载；同时丢弃了底层错误明细，无法区分超时、限流、JSON、策略合同或安全冲突。
- 原评测还在进入并发槽前创建全部样本 state，导致每条样本的 150 秒模型轮次预算从排队时开始倒计时；后续样本尚未执行就预算过期。这是批量覆盖率从小探针正常下降到 5.25% 的主要根因。

## 已完成

- 根因取证和旧报告交叉统计。
- 修正 `pause + sequence_key=none` 被误判为非法序列的合同矛盾。
- 允许活动卡点使用 `switch` 切换到解卡路径，同时继续禁止 `advance` 逼单。
- 精简 DeepSeek 必需输出骨架，明确 fallback 的真实目录 ID 约束。
- 增加脱敏的 Reply 失败分类，区分提供方、模型协议、策略合同和策略安全问题。
- 新增 DeepSeek 两阶段真实身份隔离评测工具，运行与评审不再交错争用配额，分层样本最终随机交错。
- 修正模型预算起点：拿到并发槽后才创建 state 和 runtime budget；单条延迟不再包含队列等待。
- 修正 `pause + sequence_key=none`、安全解卡 posture 和 BI 可选字段被过度当成整轮失败的问题。
- DeepSeek 针对性修复现在携带具体事实错误说明，并要求保留未冲突的意图、情绪、卡点和 B 单暂停结构。
- 修正评测脚本读取旧 `script_id` 导致“话术采用永远为 0”的统计错误；实际运行字段为 `selected_script_ids`。
- 评测启动前强制检查 DeepSeek、Relay 和启用状态下的 Follow Knowledge 凭证，缺失即失败，不再生成伪 0 候选报告。
- 修正 DeepSeek 评审门店事实优先级：本轮 `store_resolution_fact` 高于历史订单关联门店。
- 使用真实客户身份和服务器只读事实完成 20 条门禁与 120 条正式隔离重测，全程未发送、未写生产库。

## 待办

- 120 条仍有 5 条最终无可交付回复，集中为档期/预约事实重复违规和 2 条未细分 reply contract 失败；未达到 99% policy 覆盖门槛。
- 120 条没有出现 B 单 `enter/advance`，只能证明 none/pause 安全，不能证明实时 B 单进入与推进效果；需补业务确认的正例样本。
- 外部历史话术存在“诋毁其他技术”“名额有限/锁技师”等缺少本轮事实的高风险表达，需业务清洗或增加发布审核后再追求更高采用率。
- 真实样本分布不足：明确退订仅 1 条，健康/交易各 3 条，未覆盖原计划各类最低数量；当前结果不是 400 条业务金标验收。
- 由于上线门槛未通过，本任务分支暂不合入 `main`、不部署。

## 测试结果

- `python -m py_compile ai_paths/scripts/evaluate_v3_full_chain_deepseek.py`：通过。
- `python -m pytest -q tests/test_store_workflow_boundaries.py tests/test_store_matching_tool_contract.py tests/test_v3_policy_decision_contract.py tests/test_v3_closing_catalog_integration.py tests/test_v3_deepseek_eval_protocol.py`：119 passed。
- `git diff --check`：通过。
- 20 条最终门禁（`778252d2`）：主模型有效 20/20，policy 覆盖 100%，DeepSeek 初评 95%，候选可采用 6，序列/话术采用 2/2，无安全失败、无依据事实或生产写入。
- 120 条正式重测（`8046ff2b`）：主模型有效 112/120，policy 覆盖 93.33%，DeepSeek 初评 94.17%，有效策略行意图/情绪一致率 98.21%/100%，候选可采用 22，序列/话术采用 10/10，P50/P95 10.676s/16.543s，无安全失败、无依据事实或生产写入。
- 120 条失败来源：policy schema 1、门店事实恢复 1、档期事实 3、预约确认事实 1、未细分 reply contract 2；其中 3 条经安全恢复仍有正确客户可见回复，5 条无可交付回复。
- 服务器 V3 环境为两层：基础模型配置来自 `/opt/ai-paths/.env`，Follow Knowledge 租户凭证来自 `/opt/ai-paths-v3/v3.env`；测试只加载第一层会产生无效的 0 候选报告。

## 发布与回滚

- 本任务不部署。仅在验收通过后合入 `main`。

## 待沉淀的长期结论

- 策略覆盖率、检索召回率和条件采用率必须使用不同分母，禁止把 0/全样本直接解释为选择器失效。
- 模型评测与模型评审不得在同一受限流量窗口混跑。
- “完整意图+情绪+B 单结构覆盖”只证明字段存在，不证明 B 单规则已命中；必须单列 enter/advance 正例覆盖率。
- 知识候选采用率不能作为单独成功指标；候选内容本身的事实、合规和竞品表达质量必须先通过发布审核。
