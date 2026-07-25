# Project Operating Contract

This repository follows one core rule for the customer reply chain:

- The model owns business semantics, customer psychology, and sales rhythm.
- Code owns factual inputs, tool calls, schema normalization, idempotency, safety boundaries, and non-business fallback.

Do not add Python keyword branches that decide normal sales intent, objections, or conversation stage. If a business reply is wrong, first inspect the model prompt, context payload, model choice, and tool facts.

## Testing Modes

Single-node model effect tests are used before online deployment to tune one model node in isolation. They call the Planner or Reply model with controlled context fixtures, so prompt changes can be evaluated without polluting online customer history.

Full-chain online tests are used after deployment to verify the whole runtime: SOP Gate, Planner, tools, Reply, async send, logs, latency, persistence, and real platform integration behavior.

Both modes are required for reply-quality changes. Single-node tests answer “is this model prompt/context good enough?” Full-chain tests answer “does the deployed system work end to end?”

## Branch Policy

- `main` is the only long-lived development and deployment branch for this single-developer project.
- Do not create persistent feature branches for routine work. Use a temporary `codex/*` branch only for an explicitly requested or genuinely high-risk isolated experiment.
- A temporary branch must be merged after verification and then deleted locally and remotely. Do not let an older branch become an alternative source of business rules.
- Before merging a divergent branch, audit its business rules and runtime contracts against current `main`; never merge an older branch wholesale when it would restore superseded prompts, payment rules, SOP routing, or fact handling.
- Production releases must map to a verified commit on `main`.

## Long-Term Lessons

- Phenomenon: 同一企业客户在不同客服 WeChat 账号下共享画像、SOP 进度或发送次数，导致新客服接待时被当成老客、跳过完整 SOP 或错误去重。
  Root cause: 持久化和幂等键只使用 customer_id/external_userid，没有把实际接待账号 WeChat 纳入边界。
  Trigger condition: 同一个客户被分配给多个客服账号，或测试清理只按 customer_id 删除记录。
  Prevention rule: `corp_id + wechat + external_userid/customer_id` 是销售接触档案边界；不同 WeChat 必须视为不同新客，不共享画像、历史事件、SOP 进度、send_once、发送计数或主动触达状态。平台订单仍按客户 ID 实时查询，不从其他账号记忆复制。
  Fix strategy: 统一生成 `sales_contact_key` 供 memory/profile 使用；SOP、并发协调、清理接口和查询均显式带 WeChat。缺 WeChat 时禁止持久化或保守拒绝清理，不能退回裸 customer_id。
  Regression check: 同客户在 A/B 两个 WeChat 的画像和 SOP 各自独立；A 清理不影响 B；A 已完成 SOP 后 B 仍完整按新客发送。

- Phenomenon: 很久以前支付过预约金的客户被永久当成已付客户，导致新一轮接待跳过报价、SOP 和预约金。
  Root cause: 订单 `prepay_paid` 只有布尔式判断，没有当前业务约定的有效时间窗口。
  Trigger condition: 实时订单接口返回三个月以前的已付订单，而平台暂时没有预约金实际收取时间。
  Prevention rule: 暂以订单创建时间作为支付时间代理；三个月内已付按现有已付流程承接，超过三个月只作历史事实并按新客重新成交。时间缺失时保守按三个月内保护。
  Fix strategy: 订单标准化输出 `paid_protection_status` 和时间来源；Planner/Reply 使用该结构事实，代码只执行时间窗口和已付硬边界，不用话术关键词推断。
  Regression check: 三个月内订单跳过新客报价/SOP且不重复收款；三个月外订单允许完整新客流程和新预约金；边界判断可用固定时间测试。

- Phenomenon: 客户直接问“效果怎么样”时只收到文字解释，没有同类效果图，即使历史上曾完成需求案例 SOP。
  Root cause: Planner 把 `completed_pack_ids/completed_categories` 或历史文字中的案例话题误当成近期真实图片发送证据，跳过了 `kb_search(case_studies)`。
  Trigger condition: SOP 进度显示案例阶段已完成，但当前近聊和结构化事件没有上一轮真实发送案例图的证据。
  Prevention rule: 是否避免重复发案例图只能参考 `sent_message_summary.case_image_delivery` 或紧邻对话中的真实图片发送事实；SOP 完成、画像阶段、旧话题和“我给您看案例”文字承诺都不能替代图片证据。
  Fix strategy: 将案例图发送时间和数量作为 evidence 提供给 Planner；无权威近期图片证据的效果疑问由 Planner 调用 `kb_search(case_studies)`，最终 Reply 只使用真实 `case_facts` 输出图片。
  Regression check: 所有 SOP 均已完成但没有近期图片发送证据时，“效果怎么样”必须规划 `kb_search(case_studies)` 并在全链路输出真实 image；上一轮确实刚发图后的评价续问允许不重复查询。

- Phenomenon: 预约金卡曾被“必须先存在同门店同金额订单”的旧规则阻断，导致客户明确要报名或付款时仍被要求重复确认门店，甚至出现“入口没对上”的客户可见话术。
  Root cause: 旧版交易链路把后台订单关联当成了客户付款前置，而当前业务流程已经调整为先完成活动报价和预约金支付，再收姓名电话并创建或关联后台订单。
  Trigger condition: 活动报价已经完成、客户未付且适合成交，但当前没有订单、开单接口失败，或平台辅助字段尚未齐全。
  Prevention rule: 订单不是发送预约金卡的前置。活动报价完成或已铺垫后，Planner 可根据客户意向和成交节奏发送每位 10 元的 `payment_collection`；代码只校验已付、健康风险、投诉退款、明确强拒付、人数上限、金额和结构等硬边界。
  Fix strategy: 支付成功后先收姓名和电话，再尽力创建或关联后台订单。后台关联失败不得要求客户重复付款，不得导致空回复，也不得对客户暴露“入口没对上”等内部状态。旧版订单前置规则标记为 `superseded`，不得恢复。
  Regression check: 无订单但活动报价已完成时允许发送合法预约金卡；已付、健康风险、明确强拒付和人数超过 4 位仍禁止发卡；支付后姓名电话齐全才尝试后台开单或关联。

- Phenomenon: 已向客户发送唯一真实门店卡后，客户直接继续问价格、预约或付款，系统仍要求客户再次明确说“就这家”，导致成交链路停顿。
  Root cause: 开单前置只接受显式门店确认，没有把最近一次单店卡发送事实作为可供模型判断的交易门店锚点。
  Trigger condition: 最近一次门店卡发送批次只有一家真实门店，客户没有切换或反对该店而继续推进成交，但 Planner 无法使用该店开单。
  Prevention rule: 模型根据完整上下文决定客户是否继续沿该店成交；代码只核验最近权威门店卡批次是否唯一且 store_id 真实。多店批次、画像偏好和普通查询候选不得替代交易门店锚点。
  Fix strategy: `sent_message_summary` 输出 `store_anchor_fact`，只描述唯一、多店或证据不完整；Planner 用 `store_binding_decision` 判断客户是明确接受、隐式接受、仍在比较、拒绝或多店未选。Normalizer 只检查该语义决策与订单动作、真实 store_id 是否一致。
  Regression check: 单店卡后继续价格/报名可由模型判断接受并开单；明确换店应绑定新店；最近多店卡、锚点 ID 不一致、旧事件证据不完整和仅有画像偏好时均不得作为隐式接受来源。

- Phenomenon: 客户支付后需要登记和后台关联，但平台客户辅助资料不完整时开单可能失败。
  Root cause: `customer_add_wechat_id/user_id/kind/category_id` 等辅助字段可能尚未同步，后台接口无法立即建立完整订单关联。
  Trigger condition: 客户已通过权威支付事件、成功截图或订单事实确认付款，姓名电话正在补齐，平台辅助资料仍缺失或接口暂时不可用。
  Prevention rule: 支付前不以订单阻断发卡；支付后先收姓名电话，再尽力创建或关联订单。后台失败是内部记录问题，不得改变已付事实或中断客户回复。
  Fix strategy: 开单工具记录 `creation_mode/missing_optional_fields` 和关联状态；Reply 继续收集缺失登记信息，不暴露接口字段、不伪造开单成功。
  Regression check: 支付后姓名电话未齐先收信息；齐全后可尝试部分字段开单；平台拒绝或无 order_id 时仍保持已付状态且客户可见回复正常。

- Phenomenon: 客户已经回复并进入真实聊天，或会话接口拉取失败时，仍收到按固定时间触发的问地址、报价或催付 SOP。
  Root cause: 首次加微事件在会话拉取失败时降级为空历史，并丢弃事件创建后、实际处理前的新消息，发送前也没有真实客户回复的确定性拦截。
  Trigger condition: 固定首次加微事件处理前客户已经发过消息，或会话接口返回 409、超时及其他失败。
  Prevention rule: 固定首次加微 SOP 必须以最新会话拉取成功为前置条件；首次加微后的任何真实客户回复都阻断后续固定 SOP，客户消息时间无法确认时保守阻断。
  Fix strategy: 取消空历史降级，保留事件创建后的新消息，只忽略企微自动加好友开场，并为客户已回复创建可审计的跳过任务。
  Regression check: 客户在事件创建前后回复都不得调用模型或发送；只有 staff、AI、历史 SOP 消息时继续；会话拉取失败和客户消息无可靠时间时均不得发送。
