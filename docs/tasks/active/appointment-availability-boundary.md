# appointment-availability-boundary

- status: active
- owner: Codex
- base_branch: main
- base_sha: `fafe3dea04722185a38c5321dfe07ae177ea8d7d`
- production_verified_at: `2026-09-07T15:40:27+08:00`
- production_releases: `control/reply/worker=ai-paths-unified-20260907-152202-b4dfc184; frontend=frontend-20260907-b4dfc184`

## 目标

- 允许 V3 Reply 在没有实时排客数据时主动推进预约，并表达预约时间可协调。
- 客户提出具体时段后，允许确认该时间可协调并记录为到店意向。
- 继续阻断虚构门店、具体空位/档期事实，以及未完成却声称已预约、已登记、已安排或可直接到店。

## 非目标

- 不修改外部 API、数据库 schema、发送协议、门店查询或真实预约写入能力。
- 不改变付款、退订、人工接管、健康风险和忙碌暂停边界。

## Change contract

- type: reply quality / factual-boundary bugfix
- scope: 预约准入校验、Reply 与全局提示词、版本化业务规则、最小确定性与单节点验证
- risk: 放宽过度会把“可协调”误说成“已确认”，或绕过门店事实
- validation: 预约/门店确定性合同测试、V3 Reply 相关测试、隔离单节点模型效果验证
- rollback: 回退本任务单一代码提交；生产回滚到发布前统一 release

## 涉及模块与文件所有权

- `ai_paths/app/graph/nodes/reply_validation.py`
- `ai_paths/app/graph/nodes/reply_nodes.py`
- `ai_paths/app/prompts/`
- `ai_paths/app/policies/business_rules.json`
- 本任务新增的最小验证文件及 `docs/tasks/`

## 不可破坏合同

- 门店名称、地址和区域覆盖只能来自权威门店事实。
- “可预约/可协调”不是“已预约/已登记/已安排/已留位/可直接到店”。
- 明确退订立即停止营销；付款、健康风险和人工接管边界不变。
- 不用 Python 关键词判断正常销售意图；代码只校验对外有后果的事实声明。

## 已确认事实与证据

- 业务确认预约与档期默认可人为协调，产品目标是推动预约。
- 选定口径：可主动问时间；客户提出具体时段可答“可以协调/作为到店意向”，但完成态仍需权威事实。
- 生产 `b4dfc184` 的 `available_time_fact_required` 会把“可以约/能预约”和时间附近的“安排”一并当成实时档期声明，已造成合法回复被拦截并进入兜底。
- `origin/main@fafe3dea` 仍包含同一校验和相互冲突的提示词/业务规则。

## 已完成

- 建立独立 worktree 和任务分支。
- 核验开发基线、生产 release、服务角色与活动状态。
- 收窄 `available_time_fact_required`：一般可预约、时间可协调和到店意向不再要求实时空位；具体空位、档期或明确时段可用性继续要求权威事实。
- 修正预约/登记完成态识别：未来协调与意向表达放行；已预约、已登记、已安排、已留位、准时等候和直接到店继续阻断。
- 对齐全局 Reply 合同、Reply 主提示词、修复提示和版本化业务规则，并沉淀长期销售策略合同。
- 新增泛化邀约、指定时间协调、身份回答带历史噪声、具体空位、完成态、直接到店和门店边界的合成回归用例。

## 待办

- 在独立发布验证步骤执行真实单节点模型效果验证。
- 合入干净 `main` 后统一发布 control、reply、worker，并用测试客户完成三条全链路验收。

## 测试结果

- `python -m json.tool ai_paths/app/policies/business_rules.json`：通过。
- `python -m py_compile ...`（本任务修改的 Python 文件）：通过。
- `python -m pytest -q tests/test_v3_policy_decision_contract.py tests/test_store_workflow_boundaries.py`：73 passed。
- 预约、V3 Reply、门店工作流/匹配扩展回归：205 passed。
- 全量 `python -m pytest -q`：372 passed。
- Ruff 对改动文件使用仓库既有 `F841` 例外后通过；无例外运行只报告 `reply_validation.py:589` 的既有未使用变量，本任务未修改该行。

## 发布与回滚

- 本任务当前不自动部署；发布前必须重新核验 clean `main`、统一 SHA 和回滚 release。

## 待沉淀的长期结论

- 预约时间默认可协调；具体空位和预约完成是不同事实层级。
