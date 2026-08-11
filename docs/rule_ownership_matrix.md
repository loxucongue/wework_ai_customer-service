# Reply Chain Rule Ownership Matrix

本文件是 `codex/reply-chain-refactor` 的规则迁移基线，不是新的业务事实来源。它用于确认重构只移动职责，不遗漏规则，也不把业务语义转移到代码。

状态：`active` 表示有效；`merged` 表示并入更高层合同；`superseded` 表示不得恢复。规则类型统一为 `hard_law | business_fact | sales_principle | content_asset | deprecated`。

| rule_id | source | business meaning | current owner | target owner | fact dependencies | type | migration status | regression tests |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| constitution_model_semantics | AGENTS.md | 模型判断客户语义、心理和销售节奏。 | Planner、Reply、SOP 模型 | Gate 提供素材，Reply 最终判断；Tool Planner 不接管语义 | 完整带时间聊天、权威事实 | hard_law | active | Prompt 合同、仿真宪法检查 |
| code_fact_boundaries | AGENTS.md | 代码负责事实、工具、schema、幂等、安全和兜底。 | Normalizer、Validation、Tool、Runtime | 预路由、只读工具执行、Validation、Commit | 工具和状态事实 | hard_law | active | 确定性校验、隔离测试 |
| full_chat_priority | 重构讨论 | 最新消息和完整近期聊天优先于旧画像摘要。 | Planner/Reply payload | Shared Context Builder | 完整带时间聊天、current_time | business_fact | active | Shared Context、历史顺序仿真 |
| no_soft_profile_authority | 现有上下文规则 | 旧画像策略不能压过当前聊天。 | Payload pruning | Shared Context Builder | 画像、聊天、事件 | sales_principle | active | 上下文测试 |
| gate_not_business_brain | 重构方案 | Gate 只提名内容证据，复杂状态与最终动作属于 Reply。 | SOP Gate、Planner、Reply | Gate + Reply | 完整聊天、Gate 证据、工具事实 | hard_law | active | Gate 合同、Reply 场景 |
| sop_mainline_progression | SOP 主线 | 主动触达正常推进最早未完成阶段，但上下文可证明覆盖或不适合时允许调整。 | SOP Event | SOP Event | SOP 进度、发送事实、聊天 | sales_principle | active | sop_event_flow、仿真 |
| precision_answer_then_mainline | 104 条精准话术蒸馏 | 先解决客户当前问题，再由 Reply 判断是否以及如何继续推进。 | 旧精准回复运行时目录 | model_led_sales_principles；成品回复仅作离线样本 | 当前消息、完整历史、权威事实 | sales_principle | active | 精准回复、Reply 模型场景 |
| appointment_blocker_reply_ownership | 预约卡点话术库 | 卡点成品回复不进入新链路常驻 Prompt；其证据用途、销售原理和反面模式由 Reply 使用。 | Gate 场景索引、Reply 候选库 | model_led_sales_principles + Reply | 当前消息、完整聊天、权威交易和风险事实 | sales_principle | active | precision_qa_runtime_contract、Prompt 合同、预约卡点仿真 |
| offer_activity_facts | business_rules.json | 当前活动价格、预约金、尾款、退款和赠送事实以配置为准。 | 业务规则、Prompt、SOP | Shared Facts + Reply；Validation 校验金额 | 活动配置、支付状态 | business_fact | active | Prompt、支付、模型矩阵 |
| project_scope_boundary | 业务规则和精准回复 | 可预约项目和不可预约项目必须按当前配置说明。 | 业务规则、精准回复、Reply | Gate 选素材，Reply 最终回答，Validation 阻止错误收费 | 当前消息、图片事实、活动配置 | business_fact | active | 精准回复、Reply 输出策略 |
| effect_case_image_evidence | AGENTS.md | 客户问效果且无近期真实发图证据时必须查真实案例；SOP 完成或文字承诺不等于已发图。 | Planner、KB、Reply | Tool Planner 规划 KB，Reply 使用真实案例事实 | case_image_delivery、KB facts | business_fact | active | 案例图回归、模型矩阵 |
| store_visible_scope_only | 门店事实规则 | 门店卡只能来自客户可见范围。 | Store tools、Validation | Read-only Tool Executor + Validation | visible_store_scope、store_resolution_fact | hard_law | active | 门店可见范围、定位卡测试 |
| store_candidate_count_rule | 已确认业务规则 | 可见候选 1 到 3 家可全部发送，超过 3 家再补区域或定位。 | Planner/Reply | Reply 使用工具事实决定表达 | 可见候选 | business_fact | active | 门店仿真 |
| store_after_card_mainline | Reply 策略 | 发真实门店卡后不反复纠结方便与否，处理当前异议后由 Reply 自主选择下一维度。 | Reply Prompt | Reply | 最近门店卡、聊天 | sales_principle | active | 门店异议仿真 |
| location_detail_disclosure | 地址政策 | 可发公开地址；详细到店指引必须有权威事实，不能编造。 | Reply、Validation | Reply + Validation | 门店详情、登记事实 | business_fact | active | 地址详情测试 |
| payment_no_order_precondition | AGENTS.md | 活动铺垫完成后可发预约金卡，订单不是前置。 | 业务规则、Planner/Reply | Reply + Validation，支付后再关联订单 | 活动完成、支付、风险 | business_fact | active | 无订单发卡回归 |
| payment_deposit_evidence_gate | 已确认成交规则 | 首次活动接触不得同轮发卡；后续发卡必须有更早活动介绍、另一把已承接销售钥匙、当前行动信号且无硬禁区。 | 业务规则、Reply、Validation | Reply 判断证据语义；Validation 只校验引用来源、角色、时间顺序和结构一致性 | 完整聊天、SOP 交付、当前客户消息 | hard_law | active | 首次询价禁卡、预约金证据、引用真实性测试 |
| payment_after_paid_registration | 业务规则 | 已付后收姓名、电话和到店意向，普通流程不查档期、不排客。 | Planner/Reply/Tool | Reply + Commit 延后写 | deposit_state、registration_state | business_fact | active | 事务流程测试 |
| unknown_message_transfer_paid | 业务规则 | 平台未知消息类型是权威转账成功事件。 | 输入归一 | 预路由/输入归一 | msgtype、结构事件 | hard_law | active | 未知转账测试 |
| one_payment_card_per_turn | 回复结构规则 | 同轮最多一张预约金卡。 | Reply Validation | Validation | reply_messages | hard_law | active | Reply 输出策略 |
| health_risk_priority | 风险规则 | 当前过敏、发炎或破损阻止营销推进和发卡。 | 风险事实、Planner、Reply | Shared Facts + Reply + Validation | 图片、当前消息、risk_hold | hard_law | active | 健康风险仿真 |
| explicit_reject_no_payment | 业务规则 | 模型引用当前客户原文认定明确拒绝、投诉或退款后，代码阻止付款结构。 | Planner/Reply/SOP Event | Reply 语义判断 + Validation 事实引用校验 | 当前消息、近期历史 | hard_law | active | 拒绝场景仿真 |
| human_wechat_style | Reply 风格规则 | 回复短、直接、亲切，每轮最多一个自然动作，禁止机器人句式。 | Reply Prompt、软质量门 | Reply | 当前消息、历史、素材、工具事实 | sales_principle | active | Reply 模型评审 |
| sop_platform_task_passthrough | SOP 协议规则 | `sop_platform_task` 的 message_content 原样转发，不经模型改写。 | SOP service | 协议预路由/SOP service | event payload | hard_law | active | sop_platform_task_flow |
| model_failure_neutral_fallback | Runtime 规则 | 模型和修复均失败时返回中性等待文案，不得空回复。 | Runtime、Reply | Runtime fallback | 重试和失败 trace | hard_law | active | 超时和空回复测试 |
| old_order_paid_window | AGENTS.md | 三个月内已付保护；更早历史不作为当前已付状态。 | Order normalization | Shared Facts + Validation | 订单时间和状态 | hard_law | active | 订单生命周期测试 |
| order_required_before_payment_card | 历史旧规则 | 发卡前必须有订单的规则已废弃，不得恢复。 | 历史文档 | None | n/a | deprecated | superseded | 无订单发卡回归 |
| conversion_stage | legacy conversion_psychology | 固定成交阶段不得进入新 Reply。 | 旧串行 Planner/Reply | None | n/a | deprecated | superseded | parallel_excludes_conversion_stage |
| customer_type | legacy conversion_psychology | 固定客户类型不得进入新 Shared Context 或 Reply。 | 旧串行 Planner/Reply | None | n/a | deprecated | superseded | parallel_excludes_customer_type |
| fixed_mainline_next_step | legacy conversion_psychology | 固定下一步不得覆盖当前消息。 | 旧串行 Planner/Reply | None | n/a | deprecated | superseded | parallel_excludes_fixed_mainline |

Review 要求：

- 移动任何 active 规则时必须更新 target owner 和 regression tests。
- 结构重构不能顺带修改客户可见业务口径。
- `hard_law/business_fact` 可进入硬校验或权威事实；`sales_principle/content_asset` 只能由模型结合上下文判断或采用，不能触发 Python 业务分支。
- 每个实施阶段都要同时做结构 review 和业务规则保护 review。
