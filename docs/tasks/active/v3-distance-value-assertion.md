# v3-distance-value-assertion

- status: active
- owner: Codex
- base_branch: main
- base_sha: 7da2ba3e5e66ab2548812f21aa75f69d899a133c
- production_verified_at: 2026-09-07 Asia/Shanghai（发布前重新现场核验）
- production_releases: 当前文档基线 `production@44fcd568`，现场为准

## 目标

- 允许“我先帮您留着/保留活动名额”作为销售承接，不再被 V3 登记完成事实拦截。
- 距离卡点回复不复述、放大“远、折腾、麻烦”，快速转向技术、效果和案例价值。
- Reply 采用带可用媒体的话术时，同轮直接交付该话术自身的效果素材。
- 复现请求 `316d0656-54ef-4d91-8633-d40dcf95657b`，验证不再兜底后合入 main 并上线。

## 非目标

- 不放开真实“预约成功、已排客、已到账”等交易终态校验。
- 不新增模型调用、数据库、公共接口或关键词销售路由。
- 不改变退订、人工接管、付款卡和门店事实边界。

## Change contract

- type: V3 回复事实边界收窄、Prompt 表达治理和素材交付回归
- scope: Reply admission、登记话术分类、最终 Reply Prompt、定向回归测试与长期合同
- risk: 销售承接话术可能被客户理解为名额已保留；通过保留“预约成功/登记完成/排客完成”边界并监控线上表达控制风险
- validation: L1 确定性合同测试；L2 DeepSeek 指定场景复现；发布后 L4 只读核验
- rollback: 恢复发布前 clean release；无数据库迁移

## 涉及模块与文件所有权

- `ai_paths/app/graph/nodes/reply_admission.py`
- `ai_paths/app/graph/nodes/reply_validation.py`
- `ai_paths/app/graph/nodes/reply_nodes.py`
- `ai_paths/app/prompts/reply_synthesizer.py`
- `tests/test_v3_direct_value_delivery.py`
- `tests/test_store_distance_objection_continuity.py`
- `tests/test_store_workflow_boundaries.py`
- `docs/contracts/sales-strategy.md`

## 不可破坏合同

- V3 Reply 仍是唯一销售语义决策者。
- 图片/视频只能来自本轮允许候选，已发送素材不得重复交付。
- 明确退订、人工接管、健康/投诉风险和真实预约/支付终态仍按现有合同处理。
- reply、control、worker 必须以同一 clean main SHA 发布。

## 已确认事实与证据

- 指定日志正确识别距离卡点，召回 3 条序列和 6 条话术。
- DeepSeek 原始结果选择序列 11、步骤 53、话术 225，但“我先把活动名额给您留着”触发 `registration_confirmation_fact_required`，修复后再次触发并进入兜底。
- 话术 225 存在本轮可发送效果视频；由于整份回复校验失败，最终未交付。

## 已完成

- 线上日志只读根因分析。
- 从最新 `origin/main@7da2ba3e` 建立干净独立 worktree 并登记文件范围。
- 已移除销售口头保留活动名额的完成态识别，保留真实预约/登记终态校验。
- 已更新距离异议表达：轻承接后转技术、效果、案例价值，不复述放大顾虑。
- 已验证采用话术 225 时自动关联并物化该话术自己的图片/视频。

## 待办

- 收窄 V3 登记话术拦截并更新 Prompt。
- 补充确定性与 DeepSeek 指定场景测试。
- review、合入 main、部署并现场核验。

## 测试结果

- L1 定向合同：121 passed。
- L1 全仓：437 passed；仅现有第三方依赖弃用 warning。
- Prompt 长度：8886，低于 9000 字符预算。
- L2 DeepSeek 指定日志复现：待执行。

## 发布与回滚

- 待现场记录。

## 待沉淀的长期结论

- “销售承诺保留活动资格”与“系统已完成预约/登记/排客”必须在合同中分开表达。
- 解卡的共情目标是降低心理阻力，不能通过复述负面词放大卡点。
