# Project Operating Contract

This repository follows one core rule for the customer reply chain:

- The model owns business semantics, customer psychology, and sales rhythm.
- Code owns factual inputs, tool calls, schema normalization, idempotency, safety boundaries, and non-business fallback.

Do not add Python keyword branches that decide normal sales intent, objections, or conversation stage. If a business reply is wrong, first inspect the model prompt, context payload, model choice, and tool facts.

## Testing Modes

Single-node model effect tests are used before online deployment to tune one model node in isolation. They call the Planner or Reply model with controlled context fixtures, so prompt changes can be evaluated without polluting online customer history.

Full-chain online tests are used after deployment to verify the whole runtime: SOP Gate, Planner, tools, Reply, async send, logs, latency, persistence, and real platform integration behavior.

Both modes are required for reply-quality changes. Single-node tests answer “is this model prompt/context good enough?” Full-chain tests answer “does the deployed system work end to end?”

## Long-Term Lessons

- Phenomenon: 客户直接问“效果怎么样”时只收到文字解释，没有同类效果图，即使历史上曾完成需求案例 SOP。
  Root cause: Planner 把 `completed_pack_ids/completed_categories` 或历史文字中的案例话题误当成近期真实图片发送证据，跳过了 `kb_search(case_studies)`。
  Trigger condition: SOP 进度显示案例阶段已完成，但当前近聊和结构化事件没有上一轮真实发送案例图的证据。
  Prevention rule: 是否避免重复发案例图只能参考 `sent_message_summary.case_image_delivery` 或紧邻对话中的真实图片发送事实；SOP 完成、画像阶段、旧话题和“我给您看案例”文字承诺都不能替代图片证据。
  Fix strategy: 将案例图发送时间和数量作为 evidence 提供给 Planner；无权威近期图片证据的效果疑问由 Planner 调用 `kb_search(case_studies)`，最终 Reply 只使用真实 `case_facts` 输出图片。
  Regression check: 所有 SOP 均已完成但没有近期图片发送证据时，“效果怎么样”必须规划 `kb_search(case_studies)` 并在全链路输出真实 image；上一轮确实刚发图后的评价续问允许不重复查询。

- Phenomenon: 客户明确问“怎么预约/发付款卡”，Planner 已决定 `send_now`，但开单接口拒绝或当前订单门店不匹配后，最终回复只剩文字，没有 `payment_collection`。
  Root cause: Planner 事务补丁、normalizer 和 Reply 质量门把匹配有效订单或开单成功当成发预约金卡的结构前置，覆盖了模型的成交决策。
  Trigger condition: `payment_decision.action=send_now/resend`，但 `create_work_order` 返回 rejected/error、缺少 order_id，或只有其他门店的未付订单。
  Prevention rule: 发卡动作与后台订单闭环必须解耦；订单和门店事实只决定能否声称已开单、是否可继续排客，不得否决 Planner 的 send_now/resend。发送频率由模型结合当天、历史和最近进展判断。
  Fix strategy: 删除 active-order 发卡校验和订单 violation；事务 prompt 明确 send_now/resend 必须输出 text + payment_collection，开单失败只记录后台事实。
  Regression check: `work_order.status=rejected/tool_error + payment_decision.action=send_now` 必须保留 10/20/30/40 元卡；已付、当前健康硬风险等安全终态仍不得发卡。

- Phenomenon: 客户已经回复并进入真实聊天，或会话接口拉取失败时，仍收到按固定时间触发的问地址、报价或催付 SOP。
  Root cause: 首次加微事件在会话拉取失败时降级为空历史，并丢弃事件创建后、实际处理前的新消息，发送前也没有真实客户回复的确定性拦截。
  Trigger condition: 固定首次加微事件处理前客户已经发过消息，或会话接口返回 409、超时及其他失败。
  Prevention rule: 固定首次加微 SOP 必须以最新会话拉取成功为前置条件；首次加微后的任何真实客户回复都阻断后续固定 SOP，客户消息时间无法确认时保守阻断。
  Fix strategy: 取消空历史降级，保留事件创建后的新消息，只忽略企微自动加好友开场，并为客户已回复创建可审计的跳过任务。
  Regression check: 客户在事件创建前后回复都不得调用模型或发送；只有 staff、AI、历史 SOP 消息时继续；会话拉取失败和客户消息无可靠时间时均不得发送。
