# fix-v3-store-matching

- status: active
- owner: Codex `/root`
- base_branch: main
- base_sha: `2ae193ee4e5c3475f66586809cec8f42815cf02c`
- production_verified_at: `2026-09-06 13:35 Asia/Shanghai`
- production_releases: `ai-paths-v3.service=ai-paths-unified-20260905-175600-ce4eae8d@ce4eae8d579f89925d81fa2549b4b796df635488`; control/worker 同 release

## 目标

修复 V3 城市、区县和门店名称同根时，派生地理编码文本把城市级查询误缩为单门店的问题；让模型负责地点语义，代码仅按结构化行政区、客户可见范围、门店事实和坐标生成候选。对重名、泛名、精确、不精确和权限隔离场景做大规模确定性仿真。

## 非目标

- 不修改销售回复策略、SOP、订单、支付、发送或生产配置。
- 不让模型决定门店权限、门店 ID、距离或事实字段。
- 自动化验证不调用真实模型、不发送客户消息、不调用生产写接口；线上验证仅在发布步骤执行无客户发送的受控检查。

## Change contract

- type: V3 store matching bugfix, delivery contract update, verified production release
- scope: 门店匹配、交付事实、回复提示/校验及对应测试；必要时调整业务规则文字；组合仿真写入 ignored `artifacts/`
- risk: 过度收紧文本匹配可能影响明确门店名、完整地址和 POI；过度信任模型行政区可能放大错误结构化输出
- validation: 300+ 分类场景矩阵；现有门店函数定向回放；同名/泛名/精确/模糊/权限组合仿真；全仓测试；部署后 V3、control、worker、回调、管理页和退役路由检查
- rollback: 发布前记录当前 release；失败时切回该 release 并恢复三个服务角色

## 涉及模块与文件所有权

- 独占：`ai_paths/app/graph/nodes/action_nodes.py`
- 独占：`tests/test_store_matching_tool_contract.py`
- 独占：`tests/test_store_matching_semantic_matrix.py`
- 独占：`ai_paths/app/graph/nodes/action_module_outputs.py`
- 独占：`ai_paths/app/prompts/reply_synthesizer.py`
- 独占：`ai_paths/app/graph/nodes/reply_validation.py`
- 独占：`ai_paths/app/graph/nodes/reply_nodes.py`
- 独占：`ai_paths/app/policies/business_rules.json`
- 独占：`docs/architecture/SYSTEM.md`
- 独占：`docs/current/KNOWN_ISSUES.md`
- 临时验证：ignored `artifacts/store_matching/`
- 任务登记：`docs/tasks/active/INDEX.md`、本文件

## 不可破坏合同

- 只有 V3 客户回复链。
- 门店候选只能来自当前 `corp_id + wechat + external_userid/customer_id` 可见范围。
- 模型负责自然语言地点语义；代码负责事实、schema、权限和安全降级。
- 没有可信位置时不得编造最近门店或距离；明确门店名/地址仍须支持。

## 已确认事实与证据

- 生产案例 `af49230e-458d-4931-8d9b-5aad53101208`：地点模型输出 `长沙`、`city`，代码派生 `湖南省长沙市长沙` 后误命中长沙县地址，只交付门店 552。
- 同一客户可见门店共 193 家，其中长沙市 4 家；生产门店快照长沙共 6 家，另 2 家不在该客户范围。
- 提交前发现 `origin/main` 前进到 `2ae193ee4e5c3475f66586809cec8f42815cf02c`；已变基，重叠仅涉及回复提示文件且无冲突，随后重新执行全仓测试。
- 现场复核：reply/control/worker/frontend 均 active；三个后端角色均为 `ai-paths-unified-20260905-175600-ce4eae8d@ce4eae8d579f89925d81fa2549b4b796df635488`、`dirty=false`、角色配置正确。该 release 作为回滚点。
- 近 14 天最新 5000 条线上运行记录中有 784 条涉及门店/位置；分类计数：行政范围 482、地址/位置 342、泛问门店 197、附近/距离 33、多店选择 15、定位卡 10、导航/交通 8、营业时间 4、历史指代 3。只保留聚合计数，不保存客户原文。

## 已完成

- 现场核验生产 release、问题 trace、客户可见门店和门店快照。
- 建立隔离 worktree 和任务分支。
- 审查候选生成的提前返回分支，并复现“长沙市被长沙县单店截断”。
- 结构化行政区按模型声明精度裁剪，模型校验通过后优先用于客户授权门店过滤。
- 模型确认的门店名优先于宽泛地点；同一行政范围内重名门店全部保留为候选。
- 派生地图文本不再冒充客户明确提及的门店或地址证据。
- 完成城市、同名区县、同名门店、泛名、低置信度、权限隔离、精确地址和 POI 回归。
- 取消城市/区县列表固定只发 3 张卡：不超过 6 家时全部发卡，超过 6 家时保留全部候选并改用完整“门店名 + 区县”文字清单。
- 建立 30 类风险目录；执行 310 个分类客户表达场景，覆盖地级市/同根县区、跨城市同名区、县级市、范围不精确、范围过大、无地点、明确/重名门店等。

## 待办

- 把验证后的变更提交并合入干净 `main`。
- 构建生产 release，部署三个角色并完成全链路验证；失败回滚。

## 测试结果

- 语义矩阵内部执行 310 个客户表达：城市 120、同名区县 47、县级市 64、省级/过大范围 36、缺失/歧义位置 40、明确/重名门店 3；全部通过。
- 变基到最新 `origin/main` 后 `python -m pytest -q`：277 passed。
- 变更文件静态检查：通过；全文件默认 Ruff 仍会报告 `reply_validation.py` 中 main 基线已有的未使用局部变量，本任务未触碰该基线代码。
- 唯一警告为第三方 `cozepy`/Authlib 弃用提示，与本改动无关。

## 发布与回滚

- 本任务经用户明确要求提交并部署。
- 只允许从干净 `main` 的完整 SHA 构建；发布前重新记录线上 release 作为回滚点。

## 待沉淀的长期结论

- 明确自然语言地点语义与确定性门店事实之间的唯一职责边界。
