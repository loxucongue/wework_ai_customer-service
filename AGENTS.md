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
