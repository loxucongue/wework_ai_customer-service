# V3-only 选择性迁移与合并方案

- status: proposed
- owner: project
- last_verified: 2026-08-31 Asia/Shanghai
- source_of_truth: Git graph、生产 release、systemd/Nginx 有效状态、确定性测试

## 1. 目标与基线

目标不是把 V3 分支整体 merge 到 main，而是把两个线上代码线正在承担的有效能力，按职责迁移到同一个 main commit：

- control API：管理、日志、配置、平台回调。
- reply V3：唯一客户回复入口、语义路由、知识检索、回复生成。
- workers：第三方 SOP、主动触达、恢复任务、策略数据 outbox。
- frontend：管理页面；不再依赖后端 release 目录。

设计基线：

| 项目 | 基线 |
|---|---|
| 目标分支 | `origin/main@e54ff98d` |
| 生产 shared/worker 代码 | `0ef5f545` |
| 生产 V3 reply 代码 | `7c0cfc04` |
| 分叉点 | `be65e329` |
| main / V3 独有提交 | 136 / 323 |
| 分叉后修改文件 | main 185；V3 204 |
| 两边都修改的文件 | 81 |
| 直接 merge 文本冲突 | 50 |

截至验证时，生产 shared、worker、V3、frontend 均 active；V1/V2 公网与 shared 内部旧入口均为 410。2026-09-01 02:00 仍有 MySQL cutover timer，因此数据库切换与代码线合并不能放在同一变更窗口。

## 2. 冲突裁决规则

“最新”定义为最新有效行为，不等于提交时间最大。

裁决顺序：

1. **线上真实行为优先**：先确定请求实际进入哪个 release、路由和进程。
2. **功能责任代码线优先**：回复链以生产 V3 为基线；SOP/worker/storage/control 以生产 main 为基线。
3. **同一责任线的后续有效修复优先**：必须能说明修复对象、测试和是否被后续规则 supersede。
4. **同文件不同职责按函数合并**：禁止整文件 `ours/theirs`。
5. **同功能、同语义仍冲突时才按时间比较**：较新提交必须建立在相同业务合同上；否则进入审核清单。
6. **未提交、未上线改动不自动胜出**：先保全、单独测试和审核，再决定是否迁移。
7. **历史命名不代表产品版本**：被 V3 import 的 `v2_*` 先等价重命名，不能直接删除。

禁止：

- 整分支 merge 后批量解决冲突。
- 全局 `-X ours`、`-X theirs`。
- 按文件 mtime 或字符串 `v1/v2` 批量删除。
- 在 dirty worktree、detached HEAD 或未完成 MySQL cutover 验证时部署。
- 在同一个发布窗口同时迁移 reply、worker 和数据库。

## 3. 功能所有权矩阵

| 功能 | 默认基线 | 合并方式 | 理由 |
|---|---|---|---|
| V3 HTTP、graph builder、parallel reply | V3 `7c0cfc04` | 选择性迁入 | 当前线上唯一回复链 |
| semantic router、follow knowledge、V3 evaluation | V3 `7c0cfc04` | 选择性迁入 | main 不具备这些能力 |
| reply prompt、evidence、reply validation | V3 `7c0cfc04` | 以 V3 为主，补 main 的非回复硬边界 | V3 已在线验证 |
| V1/V2 退役路由 | main `0ef5f545` | 保持 410，最终删除实现 | 当前已上线合同 |
| 第三方 SOP 拉取、顺序、未开口直发 | main `7af3065e+` | main 原样保留 | 8/28–8/31 最新线上修复 |
| `/consume` 20/30/70、终态恢复和完整审计 | main | main 原样保留 | 当前日志/恢复合同 |
| SOP `service-rule-data` 终态策略 | main | main 原样保留 | 用户已确认的枚举和全终态回传 |
| V3 customer-opening strategy outbox | V3 | 保留生产行为；消费者迁到 workers | 当前由 V3 sidecar 隐式消费 |
| 消息送达回调与恢复 | main | main 为主 | main 有更晚的 delivery recovery 修复 |
| outreach 调度、幂等、存储、发送 | main | main 为主 | V3 分支缺 main 后续大量约束 |
| outreach 文案/语义 | 已提交 V3 + main 事实 | 模型层单独融合 | 不能用调度代码决定销售语义 |
| payment collection | 生产 V3 + main 幂等事实 | 函数组合，不选整文件 | V3 有 reply party assessment；main 有 unanswered-card 防重复 |
| store resolution/destination | 生产 V3 | V3 为主，保留 main 的权威快照/发送事实 | V3 有更新的 destination workflow |
| MySQL/schema/repository | main | main 为主，增加 V3 strategy repository | MySQL cutover 与 SOP 最新修复都在 main |
| 管理页/SOP 日志 | main | main 为主，补 V3 trace 字段 | main 页面更新更晚 |
| Nginx/systemd/release manifest | 当前生产配置 + 两边 unit | 人工重建 canonical 配置 | 仓库配置目前有漂移 |
| 文档与 AGENTS | main | 只保留 canonical 文档 | 旧分支文档只能取证 |
| 测试 | 两边并集 | 按合同迁移，删除纯 V1/V2 route 用例 | 测试不是按分支选边 |

## 4. 50 个直接冲突的处理分组

### A. main 直接胜出

- `.gitignore`、`AGENTS.md`
- `services/sop_platform_client.py`
- `services/sop_platform_task_service.py`
- `services/sop_event_decision.py`
- `services/sop_execution_service.py`
- `services/message_delivery.py`
- `services/outreach_system_client.py`
- `storage/message_delivery_repository.py`
- `storage/mysql_schema.py`
- `storage/run_repository.py`
- `storage/serialization.py`
- `storage/sop_event_repository.py`
- `scripts/migrate_sqlite_to_mysql.py`
- `config/sop_reply_packs.json`

这里的“直接胜出”表示以 main 文件为起点，再补 V3 明确需要的新接口；不是拒绝 V3 调用适配。

### B. V3 直接胜出

- `graph/nodes/reply_nodes.py` 的 V3 reply 路径
- `graph/nodes/reply_validation.py` 的 V3 reply 合同
- `prompts/reply_synthesizer.py`
- V3 相关 `platform_reply_runtime`、reply strategy 测试基线

main 中仅对 SOP、结构发送、权威事实和幂等仍有效的断言，以独立 guard/contract 形式补回。

### C. 必须逐函数人工整合

- `app/main.py`
- `app/config.py`
- `app/chat_runtime.py`
- `graph/nodes/action_nodes.py`
- `graph/nodes/activity_intro_image.py`
- `graph/nodes/common.py`
- `graph/nodes/layer_nodes.py`
- `graph/nodes/sent_message_summary.py`
- `graph/planner/brain_v2_prompts.py`
- `policies/business_rules.json`
- `prompts/sop_chat_gate.py`
- `services/conversation_mode_relay.py`
- `services/model_client.py`
- `services/outreach_service.py`
- `services/payment_collection.py`
- `services/run_observability.py`
- `services/store_resolution_v2.py`
- `projects/src/components/logs/run-log-viewer.tsx`

整合时每个函数必须标记 `source=main|v3|combined`，并在对应 commit 说明保留了哪条线上行为。

### D. 测试冲突

以下不按分支取舍，而是将两边断言拆到新的 unit/contract/integration 层：conversation mode、live tracking、message delivery、model timeout、outreach client、platform runtime、precision QA、reply output、SOP event、SOP platform task。

旧 Planner 专属脚本和测试只有在 V3 合同已有等价覆盖后才能删除。

## 5. 未提交 worktree 的隔离规则

以下内容不能直接进入迁移基线：

- V3 worktree：29 个已修改文件、10 个未跟踪文件，约 `+1202/-176`。
- E 盘 detached 工作区：回复、支付、SOP、模型选择等 38 个已修改文件及多个新文件。
- 旧 main、SOP candidate、store preference、effect case 等 dirty worktree。

处理方式：

1. 为每个 dirty worktree 生成 `HEAD/status/diff-stat/hash/mtime` 私有 manifest。
2. 生成可恢复 patch/bundle，保存在本机私有 artifacts，不直接提交 main。
3. 按功能拆成候选：reply、store、payment、SOP、outreach、observability。
4. 与生产基线逐项比较；已有线上等价实现则丢弃候选。
5. 只有通过确定性测试并完成业务审核的候选，才以新 commit 重做，不直接应用整包 patch。

## 6. 分批迁移顺序

### M0：冻结与保全

- 等 MySQL cutover 单独完成并稳定验证。
- 记录 main/V3/前端 release、配置摘要、route 表、worker 状态和数据库 backend。
- 保全所有 dirty worktree。
- 为当前生产 main `0ef5f545`、V3 `7c0cfc04` 建立不可变发布标签或 manifest。

门禁：不得修改生产行为。

### M1：拆 entrypoint，不改行为

目标结构：

```text
ai_paths/app/entrypoints/
  control_api.py
  reply_v3.py
  workers.py
ai_paths/app/composition/
  shared.py
  control.py
  reply.py
  workers.py
```

- control 只注册管理、日志、回调、配置接口。
- reply 只注册 V3 reply 和 health。
- workers 不暴露业务 HTTP，仅保留 health/metrics。
- 依赖创建从模块 import side effect 改为显式 composition。

门禁：三个角色使用同一 main SHA；与当前三个进程的 route/worker 快照等价。

### M2：迁入 V3 独有核心

先迁入不与 main 冲突的 V3 文件：graph builder、parallel reply、reply input、semantic router、follow knowledge、V3 evaluation、strategy repository、相关 fixtures/tests。

门禁：模块 import、V3 golden、router、knowledge、store matrix 全通过；尚不切生产流量。

### M3：解决 reply 共享冲突

按顺序处理：state/common → action/tool outputs → store facts → sent summary → reply context → reply synthesizer/validation → chat runtime。

- 以 V3 线上输出语义为基线。
- main 的权威发送事实、账户边界、delivery manifest、订单/支付硬边界以独立证据加入。
- 不把 main 旧 Planner 主线重新接回 V3 graph。

门禁：固定输入对当前生产 V3 与新实现做结构化 diff；允许 request_id/时间差异，不允许 reply type、工具事实、支付/门店硬边界倒退。

### M4：整合 control、SOP 和 storage

- main 的 SOP、delivery、recovery、MySQL 和管理 API 保持主导。
- 接入 V3 新 repository/schema 时只做向后兼容新增，禁止 destructive migration。
- main 的 SOP `service-rule-data` 终态直连行为不改变。
- V3 strategy outbox 消费器迁入 workers，保留现有 retry/dead 审计，保证单一消费者。

门禁：20→送达→30、70、失败恢复、人工接管、客户删除、未开口直发、scene 枚举、`contentExhausted` 全部通过。

### M5：整合 outreach

- 先迁调度/幂等/客户回复阻断/账户边界，不迁未提交的新销售政策。
- 再把 V3 已提交的候选检索和模型表达接到 main 的任务/发送框架。
- 静默唤醒作为独立功能开关，默认关闭并仅限测试 allowlist。

门禁：真实客户回复阻断、人工接管、删除关系、重复卡片、跨 WeChat 隔离、发送失败恢复。

### M6：删除产品 V1/V2 和重命名历史模块

- 删除旧 route 与 V2 service 装配。
- 保留第三方 `/v1` 协议、历史 schema 读取兼容。
- 被 V3 使用的 `v2_derived_observations`、`v2_reply_admission`、`v2_sales_recall_service`、`store_resolution_v2` 先做无行为重命名，每次一个模块并保留兼容 import 一个发布周期；随后删除兼容 import。

门禁：运行时 import graph 中没有产品 V1/V2 entrypoint，历史日志仍可读取。

### M7：前端和部署统一

- 从同一 main SHA 构建 frontend，但发布目录保持独立。
- 仓库写入完整 canonical Nginx/systemd 配置。
- release manifest 必须包含 `branch=main`、full SHA、dirty=false、role、interface=v3、schema revision。

门禁：仓库配置与 `nginx -T`、`systemctl cat` 的规范化 diff 为零。

## 7. 测试与上线门禁

每一批均单独 commit、单独验收，不把失败留给下一批修复。

1. 静态门禁：compile/import、route snapshot、配置 schema、无循环 import。
2. 确定性门禁：unit + contracts + integration；不调用真实模型、不真实发送、不写生产平台。
3. V3 行为门禁：trusted golden、store、payment、knowledge、takeover、reply evidence。
4. SOP 行为门禁：消费、策略回传、送达回调、恢复、日志 v3。
5. 存储门禁：SQLite 与 MySQL 双后端，旧数据读取与新写入一致。
6. shadow：新 reply 端口只接脱敏重放，不接客户流量；对比当前 8013。
7. canary：仅测试账号/allowlist；先 reply，观察后再迁 control，workers 最后单独切换。
8. 生产验收：V3 health、control、worker、callback、SOP 队列、策略 outbox、管理页、延迟和错误率。

任何一项未通过都不进入下一批。

## 8. 发布与回滚

- 现有 shared `0ef5f545`、V3 `7c0cfc04`、frontend `dce86d4b` 保留为迁移前基线。
- 新合并版本部署到新 release 和新端口，不覆盖当前目录。
- reply 切流只改一个 Nginx upstream/location；失败立即恢复 8013。
- control/worker 分两个窗口切换；worker 切换前确认旧消费者已停、新消费者取得唯一租约。
- 数据库只允许向后兼容 schema；旧 release 必须能继续读取。
- 回滚后执行重复消费、重复发送和 outbox lease 检查，不能只看 health。

## 9. 需要用户审核的业务项

### R1：明确要求停止联系后是否仍继续营销

V3 dirty 文件 `v3_sales_policy.py` 提议把“别联系、别发了、暂时不定”等多数拒绝继续视为可换角度触达，只在投诉退款、健康风险、人工接管时停止。

审核结论：**拆分软拒绝和明确退订**。

- “考虑一下、暂时不定、晚点、忙、没空”等软拒绝：允许模型结合完整上下文换价值角度继续推进，不设“一次拒绝即永不营销”的机械规则。
- “别联系、别发了、取消接收”等明确退订：立即停止商业营销，代码记录可审计 stop-contact 事实，模型不得覆盖。

不能把明确退订继续营销。依据包括《消费者权益保护法实施条例》关于消费者选择取消后立即停止商业性信息，以及《个人信息保护法》第二十四条、第四十四条关于便捷拒绝和限制/拒绝个人信息处理的规定。

### R2：60 秒静默后自动激活 V3 唤醒

V3 dirty `V3SilenceWakeupService` 默认参数包含 `silent_seconds=60`、`auto_activate=True`，并新增约 3.3 万字节服务代码。

审核结论：**不随主迁移合入**。后续单独设计最小静默时间、allowlist、频控、客户回复阻断、人工接管/删除校验和 kill switch，再做灰度。

### R3：未提交的支付/门店/效果案例 hotfix

这些 worktree 不是线上事实。建议先以生产 V3 为基线，把每个 hotfix 转成失败 fixture；只有能稳定复现并证明修复、且不破坏其他矩阵时才重做为新 commit。

当前决策：R1 仅允许软拒绝继续推进，明确退订硬停止；R2 排除；R3 进入测试候选，不自动合入。

## 10. 最快安全工期

按当前 50 个直接冲突、两套线上 release 和 23 个 worktree 估算：

| 阶段 | 工作量 | 最快耗时 |
|---|---|---|
| M0 保全、manifest、MySQL 切换后复核 | 不改业务 | 2–3 小时 |
| M1–M3 entrypoint + V3 reply 合并与 shadow | 主要冲突集中区 | 6–8 小时 |
| M4–M5 SOP/storage/outreach 整合 | worker 与回调高风险区 | 6–8 小时 |
| M6–M7 删除旧实现、前端/部署统一、canary | 发布收口 | 4–6 小时 |
| 生产观察与最终清理 | 观察错误率、重复发送、outbox | 12–24 小时观察窗 |

工程实施约 18–25 小时。若 MySQL cutover 正常、确定性测试没有暴露新语义冲突：

- **最快 2 个工作日**可以完成同一 main SHA 的生产切换。
- **第 3 个工作日**完成观察、旧分支/worktree/release 清理和文档收口。
- 若要求把所有 dirty hotfix 也一并审核迁入，预计增加 1–2 个工作日；快速路径默认把它们隔离在主迁移之外。

不能为了压缩到一天而把 reply、worker、数据库一起切换。最快策略是先完成统一 reply shadow/canary，再单独切 control 和 workers。
