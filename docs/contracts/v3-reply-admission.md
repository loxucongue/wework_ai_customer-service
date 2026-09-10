# V3 Reply 运行时质量门合同

- status: current
- owner: reply-runtime / product
- code_verified: 2026-09-10 Asia/Shanghai, `main@0e7f76e5`
- source_of_truth: `ai_paths/app/graph/nodes/reply_admission.py` 及其直接调用的事实校验器

## 1. 适用范围

本合同描述 `POST /api/ai/reply/workflow-compatible-v3` 的模型候选在发送前经过
`validate_model_led_reply_admission()` 时，哪些问题会拒绝候选，并在本轮剩余预算允许时最多触发一次修复。
Prompt 中的销售原则、离线业务评测、展示长度/表情限制和 BI 观察项不是同一层质量门。

原则仍是：Reply 决定客户心理、销售语义和表达；代码只比较模型输出与本轮已经确定的结构、
来源、事实和阶段边界。当前实现有少数窄范围客户可见文本校验，见第 5 节治理风险。

## 2. 当前实际质量门

| 类别 | 当前会拒绝的输出 | 代表 reason code | 主要实现 |
| --- | --- | --- | --- |
| 消息结构 | 结构素材没有任何可读文字；客户可见占位城市或示例地址 | `structured_delivery_requires_text_message`、`customer_visible_placeholder_fact` | `reply_admission.py`、`reply_validation.py` |
| 素材来源与交付 | 采用未入选的 `content_id`；图片/视频不在本轮真实候选；声明采用但缺少候选要求的结构媒体；文字承诺发效果图却没有真实图片 | `selected_content_id_not_nominated`、`unsupported_parallel_media_fact`、`selected_content_delivery_missing:*`、`case_image_structure_required_when_reply_promises_delivery` | `reply_admission.py`、`reply_validation.py` |
| 收款 | 已权威支付或客户本轮声称已付仍发卡；缺少更早活动/价格交付证据；金额、同行人数或单轮卡数冲突 | `payment_collection_blocked_by_paid_deposit_context`、`payment_collection_blocked_by_customer_paid_claim`、`payment_collection_requires_prior_activity_evidence`、`invalid_parallel_payment_collection_amount` | `reply_validation.py` |
| 门店结构与来源 | 门店 ID 不在本轮工具结果；澄清、无候选或文字清单模式却发卡；单店/多店数量不符；文字承诺发地址但没有相应卡；卡片和文字门店不一致 | `unsupported_store_address_message`、`store_cards_not_allowed_for_resolution_status:*`、`store_resolution_send_single_contract_violation`、`store_address_text_without_card`、`store_address_text_card_mismatch` | `reply_validation.py` |
| 门店/预约事实 | 无权威预约事实却声称已约好；无营业时间事实却给具体营业时段；无可接待事实却声称有空位、可直接到店或已安排 | `appointment_confirmation_fact_required`、`business_hours_fact_required`、`store_availability_fact_required` | `reply_validation.py` |
| 客户身份 | 客户可见文字谎称“我是真人/不是机器人/人工客服” | `customer_visible_false_human_identity_claim` | `sales_fact_validation.py` |
| 价格事实 | 把 268 说成无差别全脸；左右脸颊拆成两价；脸和手共用一个 268 或表达含糊；二次价格擅自沿用 268 | `offer_268_full_face_claim_conflict`、`offer_bilateral_cheek_split_price_conflict`、`offer_face_hand_total_268_conflict`、`offer_face_hand_price_scope_ambiguous`、`offer_repeat_visit_268_unverified` | `sales_fact_validation.py` |
| 主线结构动作 | 正常轮没有 `next_sales_action`；暂停轮推进预约/付款；硬停止没有 stop；已付后仍走销售；动作不在本轮 `allowed_next_sales_action_types` | `next_sales_action_required`、`paused_turn_cannot_advance_transaction`、`hard_stop_requires_stop_action`、`post_payment_requires_service_action`、`next_sales_action_exceeds_delivered_mainline:*` | `reply_admission.py` |
| 客户可见提前邀约 | `invite_booking` 尚未开放，却在文字中询问工作日/周末、到店日期或声称帮客户预约 | `next_sales_action_exceeds_delivered_mainline:visible_invite_booking:*` | `reply_admission.py` |
| 门店跨轮连续性 | 当前轮无门店需求却带回旧门店；完整无候选后继续追问同区域细地址；同城推荐终态后的距离卡点继续追问/发店或重复强化负面顾虑 | `stale_historical_store_topic_leak`、`store_scope_confirmed_same_region_requery`、`terminal_store_distance_objection_same_city_requery`、`terminal_store_distance_objection_restates_negative` | `reply_admission.py` |

同一候选可同时产生多个 reason code；修复输入必须一次携带全部违规，不能只修第一项。

## 3. 明确不是此质量门的判断

- 普通询价、质疑、讲价、粗口、忙碌或软拒绝是否继续营销，由 Reply 结合策略判断；不能只因关键词拦截。
- 是否采用某一条序列、话术或素材属于 Reply 决策；代码只校验模型已经声明采用的 ID 和实际交付是否一致。
- 一轮有几个自然问题、是否以问号结尾、语气是否足够像真人，属于 Prompt 和业务评测；展示层仍有 8 条/300 字及表情上限，但不属于本函数。
- 已确认门店后是否应继续推进预约，先由主线事实计算出允许动作，再由 Reply 选择；代码不凭客户关键词自行选择推进动作。
- 知识候选、B 单目录和情绪标签不能授权价格、门店、预约、营业时间、支付或交易完成事实。

## 4. 独立硬停止保护

明确退订、人工接管、关系删除、医疗高风险、具体严重客诉/退款纠纷和权威交易终态还有独立运行保护，
不完全依赖本 admission 函数。明确退订不得被一次修复降级；不得同时发送素材、门店卡、收款卡或执行写动作。
普通软拒绝不能伪装成永久停止。

## 5. 当前治理风险

`_validate_customer_visible_mainline_boundary()`、`_validate_completed_store_scope_requery()` 和
`_validate_terminal_store_distance_objection()` 会在已有结构化阶段/门店终态前提下，用窄范围正则核对客户可见文字。
它们用于防止“动作标签合法、实际文案却越级”或无效重复，并不是新的意图分类器；但实现仍与
“正常销售语义归模型”存在张力。后续治理应优先把这类约束收敛成更明确的模型输出字段和结构事实，
在同批真实状态组合测试证明效果不下降后再移除文本正则，不能先删保护再观察线上事故。

## 6. 修复与失败

- 没有任何可校验 JSON 时，允许在剩余预算内完整重跑 Reply 一次。
- 已有 JSON 时，只把全部 reason code、合法 ID、结构选项和权威事实交给 targeted repair；修复器不是第二销售大脑。
- 修复仍失败时，不再进行第三次销售模型调用；只能做确定性局部清理和复验，最终不可用时返回“您稍等一下”。
- BI 观测字段缺失不得单独让客户请求失败；运行必需字段、事实和结构字段除外。

动态 reason code 到修复指令的映射见
`ai_paths/app/graph/nodes/reply_nodes.py::_reply_repair_hint`；完整模型节点和 Prompt 见
[V3 大模型节点与 Prompt 全景](../architecture/V3_MODEL_NODES_AND_PROMPTS.md)。

## 7. 变更和验证要求

新增、删除或改变任何 admission 条件时，必须同时：

1. 更新本合同及对应 reason code。
2. 增加“应拦截”和“不得拦截”两类确定性测试。
3. 对客户可见文本正则补反例，证明不会把普通销售语义变成关键词路由。
4. 涉及主线、门店、价格、付款或素材时，按完整结构消息和生命周期状态组合执行 L3；部署后再做 L4。

主要回归入口：`tests/test_v3_quality_gate_whitelist.py`、
`tests/test_v3_mainline_action_admission.py`、`tests/test_store_distance_objection_continuity.py`、
`tests/test_v3_sales_delivery_safety_regressions.py` 和 `tests/test_store_workflow_boundaries.py`。
