# V3 沉默计划素材上下文与日志体验

- Goal: 让沉默唤醒计划模型看到本轮真实可用素材并保证文案、附件一致；将主动触达日志改造成业务可读、节点可下钻的操作页面。
- Non-goals: 不改变 V3 在线回复决策，不新增自动发送场景，不放宽 AI/人工、退订、频控和发送前复核边界，不新增模型调用。
- Base SHA: `461d126fd26f51d684e22a9225cacbd71fd0ac92`.
- Production baseline: backend/reply/worker `f8d63e46b883112f786231313dd1f9120a9c8854`; silence outreach enabled for all WeChat accounts, AI mode only, 1-minute eligibility threshold.
- Ownership: `ai_paths/app/services/outreach/**`, related outreach repository/admin read APIs, the existing outreach log frontend page/components, focused backend/frontend tests, relevant contracts/current docs.
- Invariants: customer state remains isolated by `corp_id + wechat + external_userid/customer_id`; only real resolved asset IDs/URLs may enter executable plans; missing assets must not be described as attached; delayed sends still recheck AI/manual and stop-contact state.
- Change contract: customer-facing automation plus admin UX; medium/high risk. Validate deterministic asset contracts, DeepSeek isolated plan generation, no-send tests, API contracts, frontend type/lint/build, and Playwright desktop/mobile flows. Roll back to the production release above on send/runtime regression; UI can roll back independently.
- Status: active — inspecting the current planning inputs, asset resolution path, log data contract and existing page.
