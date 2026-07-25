# 分支差异与单主分支收口审计

审计时间：2026-07-22（Asia/Shanghai）

## 最终结论

- 最终代码基线：`codex/platform-task-direct-forward-20260722` 的最新提交，验证后快进为唯一长期分支 `main`。
- 不把 `codex/prompt-hierarchy-transaction-20260716` 整支合并。该分支与当前基线在 2026-07-16 后分叉，直接合并会覆盖后续账号隔离、预约金订单约束、精准回复恢复、语音转写、SOP 路由和门店事实保护。
- 分叉分支中仍有效的架构思想已选择性迁移：整轮运行预算、Reply 硬校验与软质量门分层；纯 evidence 视图和 Planner schema/tool/transaction 辅助模块在当前基线已存在。
- 业务规则以当前基线为准。旧分支只作历史审计，不作为运行时规则来源。
- 收口完成后，本地和远端只保留 `main`；以后除隔离高风险试验或用户明确要求外，不新建长期功能分支。

## 分支清单

| 分支 | 最后提交（北京时间） | 最后提交 | 与最终候选关系 | 主要功能差异 | 处理结论 |
|---|---|---|---|---|---|
| `codex/platform-task-direct-forward-20260722` | 2026-07-22 14:29 | `316af3308` | 最终候选 | 汇总最新门店、付款、SOP、精准回复、语音、模型恢复，并增加整轮预算、Reply 质量门分层和精准回复可见入口 | 快进为 `main` |
| `codex/sop-event-routing-20260721` | 2026-07-22 09:46 | `b17c78b14` | 分叉 1 个补丁，但 patch 已等价包含 | `sop_platform_task` 直接转发，不进入模型 | 当前 `c09f145bc` 已等价实现，删除分支 |
| `codex/reply-latency-quality-v2` | 2026-07-22 03:15 | `f50687225` | 最终候选祖先 | 模型事实输入、结构输出、短消息、旧风险、门店指代、候选店与真实距离边界 | 全部已继承，删除分支 |
| `codex/closeout-context-sop-20260703` | 2026-07-21 10:37 | `cc039d89f` | 最终候选祖先 | 企微账号隔离、预约金订单闭环、门店/SOP 共用报价、跨城市卡片保护 | 全部已继承，删除分支 |
| `codex/prompt-hierarchy-transaction-20260716` | 2026-07-21 00:39 | `3ca533e7d` | 真分叉：候选侧 31 个提交、该侧 36 个提交 | 早期提示词分层、精准问答、Normalizer 拆分、Reply 分层、模型预算、项目范围、地址层级、语音、主动触达 A | 禁止整支合并；按下文逐项选择性迁移后删除 |
| `main`（本地旧 tip） | 2026-07-02 09:12 | `bdfc7e74d` | 最终候选祖先 | 早期 SOP 配置/日志、视频、门店指代边界 | 快进到最终候选 |
| `origin/main`（远端旧 tip） | 2026-06-24 19:47 | `bc5978460` | 最终候选祖先 | 更早的门店位置卡事实来源 | 强制更新为最终 `main`（快进） |
| `codex/effect-case-image-fix` | 2026-06-22 11:35 | `0fdb36198` | 最终候选祖先 | 效果图兜底、主动唤醒聊天记录入口 | 全部已继承或被新案例事实机制替代，删除分支 |
| `codex/store-real-api-retry` | 2026-06-21 23:11 | `52ab750ac` | 最终候选祖先 | 早期门店真实接口、案例检索、预约金与回复质量门实验 | 已被当前工具事实和质量门取代，删除分支 |

远端同名分支与本地同名分支内容一致；审计时只有当前候选远端落后本地 3 个提交，最终统一由 `main` 覆盖。

## 真分叉分支逐项审计

`codex/prompt-hierarchy-transaction-20260716` 不能按提交数量判断“更新”。它在旧业务结构上继续开发，而当前候选在另一条线上加入了更晚且优先级更高的生产修复。

### 已由当前机制替代

| 旧分支能力 | 当前保留实现 |
|---|---|
| 提示词层级和交易决策 | 当前 `global_contract.py`、`business_rules.json`、Planner schema 与 Reply contract |
| 精准问答和销售主线 | `precision_qa_playbook.json`、SOP Gate 的 `sop_only/ai_then_sop/ai_only`、Planner/Reply 精准回复输入 |
| 精准回复配置中心 | `/sop/precision` 和 `/api/precision-qa-playbook`，当前 10 个问题 |
| JSON 结构恢复与时延保护 | ModelClient JSON retry、Planner/Reply recovery、持久化 SOP timeout retry、当前整轮预算 |
| 项目范围、手部价格、不可预约项目 | 当前精准回复 playbook 与统一业务规则；不采用旧价格文案 |
| 同区多门店、小城市门店集合、分层地址匹配 | 当前 `store_scope_summary`、共享门店快照、真实 ID 校验和多卡规则 |
| 距离顾虑和最近门店 | 当前拒绝单候选伪排序、区分并列候选与唯一推荐，不输出公里/分钟/车程 |
| 语音转写 | 当前豆包 ASR 提交与旧分支 patch 等价，且保留重试 |
| SOP 主动触达方案 A | 当前 `6ed5a9ca6`，叠加平台任务直转和模型超时持久化重试 |

### 选择性迁移

| 架构能力 | 处理 |
|---|---|
| 整轮模型预算 | 已迁移为 `runtime_budget.py`；默认 shadow，不改变当前回复，只记录 would-skip；可验证后启用 60/75 秒硬预算 |
| Reply 硬校验与软质量门 | 已拆分为 `reply_validation.py` 与 `reply_quality.py`；事实错误可 repair，风格 warning 不清空回复 |
| Current Turn Context 纯 evidence | 当前已有 `turn_evidence_view.py`；Planner/Reply 不接收 legacy `context_hints/open_task/payment_evidence` 业务结论，旧 hints 仅留 trace |
| Planner Normalizer 模块化 | 当前已有 schema、tool fact、transaction、reply structure 四个辅助模块；旧版完整拆分依赖已淘汰字段，整包迁入会回退当前支付和精准回复 schema，因此不迁入旧文件 |

## 最终必须保留的当前业务机制

1. 客户接触档案以 `corp_id + wechat + customer` 隔离；不同企微账号不共享画像、SOP 进度和发送次数。
2. （已废弃，`superseded`）当时曾要求预约金卡必须先有同门店、同金额订单。当前有效规则以 `business_rules.json` 为准：活动报价完成或已铺垫后可按成交节奏发卡；支付后收姓名电话，再创建或关联后台订单。
3. 三个月内已付订单保护为已付；更早订单只作历史事实。成功支付截图可先于平台延迟确认。
4. 已付普通流程只登记姓名、电话、门店、日期和时间意向，不调用 `available_time/create_order_plan`。
5. `sop_platform_task` 无条件直接转发 `message_content`，不进入模型；其他主动事件由 SOP Event 决策。
6. 固定首次加微 SOP 以最新会话拉取成功为前提；客户已真实回复或消息时间不可靠时不发送固定包。
7. 精准回复由 SOP Gate 识别、Planner 决策、Reply 自然表达，并在回答后恢复未完成主线。
8. 效果问题只有近期真实案例图片发送证据才允许不重复查图；SOP 完成标记不能替代图片事实。
9. 门店地址卡必须来自真实 store ID；距离推荐必须有可比较的真实排序，不输出公里、分钟或车程。
10. 普通业务语义和销售节奏交给模型；代码只处理事实、工具、schema、幂等、安全和不可空兜底。
11. 无匹配订单或客户已付时，SOP Event 只能在模型明确删除全部受限 `payment_collection` 并同步调整文本后继续发送非收款内容；`activity_intro_required` 不得通过删卡绕过。
12. 精准回复配置从主界面“回复配置”进入；`/sop` 固定展示“话术包 / 精准回复”导航，精准回复直达路径为 `/sop/precision`。

## 验证证据

- 后端编译：`python -m compileall -q ai_paths/app` 通过。
- 后端全量：`PYTHONPATH=ai_paths python -m pytest workflow_tests -q`，`548 passed, 1 warning`。
- SOP Event 单节点模型矩阵：`29/29` 通过，P50 `5058ms`，P90 `7262ms`。
- 前端：`pnpm run ts-check`、`pnpm run lint:build` 通过。
- Next.js 生产构建通过，路由包含 `/sop`、`/sop/precision`、`/api/precision-qa-playbook`。
- Playwright 已验证桌面与 `390x844` 窄屏均可看到“话术包 / 精准回复”导航并进入精准回复配置页。
- `git diff --check` 通过。

## 后续分支策略

- 日常开发直接提交到 `main`。
- 只有高风险、需要长期并行验证或用户明确要求时才创建 `codex/*` 临时分支。
- 临时分支验证完成后立即合并并删除本地和远端引用，不保留长期“功能分支仓库”。
- 线上 release 必须能够追溯到 `main` 的明确提交；禁止部署仅存在于未合并分支的代码。
