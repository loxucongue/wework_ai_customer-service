# V3 沉默计划素材上下文与日志体验

- Goal: 让沉默唤醒计划模型看到本轮真实可用素材并保证文案、附件一致；将主动触达日志改造成业务可读、节点可下钻的操作页面。
- Non-goals: 不改变 V3 在线回复决策，不新增自动发送场景，不放宽 AI/人工、退订、频控和发送前复核边界，不新增模型调用。
- Base SHA: `461d126fd26f51d684e22a9225cacbd71fd0ac92`.
- Production baseline: backend/reply/worker `f8d63e46b883112f786231313dd1f9120a9c8854`; silence outreach enabled for all WeChat accounts, AI mode only, 1-minute eligibility threshold.
- Ownership: `ai_paths/app/services/outreach/**`, related outreach repository/admin read APIs, the existing outreach log frontend page/components, focused backend/frontend tests, relevant contracts/current docs.
- Invariants: customer state remains isolated by `corp_id + wechat + external_userid/customer_id`; only real resolved asset IDs/URLs may enter executable plans; missing assets must not be described as attached; delayed sends still recheck AI/manual and stop-contact state.
- Change contract: customer-facing automation plus admin UX; medium/high risk. Validate deterministic asset contracts, DeepSeek isolated plan generation, no-send tests, API contracts, frontend type/lint/build, and Playwright desktop/mobile flows. Roll back to the production release above on send/runtime regression; UI can roll back independently.
- Implemented:
  - 场景分析收到去 URL 的全部真实素材目录、来源和本轮可发送状态；写作/审核收到逐步媒体交付合同。
  - 效果证明必须绑定真实图片或视频；无媒体步骤不得产生悬空看图话术；执行仍由内部原始目录解析 URL。
  - 日志页改为业务全景、客户上下文、执行节点、发送记录和折叠原始记录；候选、采用、任务和发送可连贯追踪。
- Validation:
  - 全量后端回归 337/337；聚焦素材/沉默门禁回归 21/21；Python 编译、前端 TypeScript、ESLint 和生产构建通过。
  - DeepSeek 隔离双节点验证通过：有素材时保留真实素材 ID，无素材悬空指代被审核为 repair；模型 2/2 为 `deepseek-chat`，无生产写入和发送。
  - Playwright 桌面、节点抽屉和 390px 手机视图通过，控制台错误 0。
- Status: validated — pending merge to main and production deployment.
