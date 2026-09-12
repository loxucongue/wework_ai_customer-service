# V3 付款、活动完整性与速度修复

- 类型：V3 Reply 结构正确性、销售事实完整性与热路径性能修复
- 基线：`origin/main@9c2c72f954d1382e08ba2e0eb25aaa838f8c722e`
- 分支：`codex/v3-payment-activity-latency-repair`
- 生产基线：`ai-paths-unified-20260911-sop-timeout-e2bd8ccf` / `e2bd8ccf1851cee869324a461512e0379fb0f1de`，部署前必须重新现场核验
- 目标：分两次发布。第一版消除合法付款请求因结构/证据合同错位落入“您稍等一下”，并把活动介绍完整性提高到 `10/10`；第二版在不丢事实、不增加兜底的前提下把生产自然流量降至 P50 `<=15s`、P95 `<=25s`。
- 非目标：不处理 `wework:sl0132`、`P1_SLOT_MAP`、`P1_DEFAULT_DEVICE_ID`；不修改 SOP、主动唤醒、B 单、第三方发送/送达、客户身份边界、数据库 schema、模型供应商或统一兜底文案。
- 产品口径：正常价值、效果、活动和预约推进不是缺陷；软拒绝允许换价值角度推进，明确退订才停止营销。活动事实有内容时不得因追求短回复而压缩。旧自然度“无关插入”只观察，不作为压制积极销售的门槛。

## Change contract

- 付款：只修正权威结构引用、schema 归一与 admission/repair 一致性。模型负责判断销售意图和客户人数；代码只验证模型引用的当前消息、活动交付事实、人数、金额与付款结构一致，不新增 Python 关键词意图分支。
- 活动：从本轮权威活动事实生成动态完整性清单；只在 Reply Prompt 中增加紧凑、上下文驱动的完整交付约束，不恢复旧自然度候选，不做大范围话术重写。
- 性能：保留客户身份、订单/付款、接管状态的每轮新鲜读取；优先合并同一客户的只读记忆/SOP 交付快照并增加脱敏分段耗时。允许在全部一致性门槛通过后，仅在 Reply 专用环境覆盖 `AICS_MYSQL_HOST` 切换同实例内网地址。
- 失败观测：客户可见仍只有“您稍等一下”；内部明确区分模型格式、准入拒绝、修复耗尽、传输错误与超时，不记录客户原文、凭证或数据库地址。
- 公共边界：V3 HTTP、外部消息协议和数据库 schema 不变；不增加模型节点，不启用异步客户补答。

## Ownership 与冲突检查

- 独占模块：`reply_validation.py`、`reply_admission.py`、`reply_nodes.py` 的付款一致性与定向修复；Reply 活动事实渲染和销售 Prompt；Reply 背景只读快照、MySQL 连接耗时和直接测试/评测工具。
- 不触碰：`v3-refund-fallback-repair` 独占的 `reply_generation.py`；第三方 SOP task/service/client/alert 文件；MytRpc 平台派发与设备映射；生产发送配置。
- 只读取证后的最小文件集：
  - 第一版：`ai_paths/app/graph/nodes/reply_validation.py`、`reply_admission.py`、`reply_nodes.py`、`ai_paths/app/prompts/reply_synthesizer.py`、`reply_sales_prompt_v4.py`，以及 admission、Prompt 和全图评测的直接测试/合同文档。
  - 第二版：`ai_paths/app/graph/nodes/layer_nodes.py`、现有存储 repository 的只读快照/连接领取耗时实现、对应性能合同测试与脱敏观测文档；是否需要其他文件以最终取证为准。
  - 发布记录：只更新本任务、动态生产状态和历史索引，不携带原始输出或真实素材。
- 冲突检查：当前唯一相邻 active ownership 是 `v3-refund-fallback-repair` 的 `reply_generation.py`，本任务不修改该文件；未发现第一版最小文件集与其他 active task 重叠。若后续发现必须修改其他 active ownership、公共接口或 schema，停止该部分而不越权扩展。

## 验收与发布

- 第一版：两个修正付款场景各重复三次，付款卡 `6/6`、fallback `0/6`；单人及 2/3/4 人金额、未知人数、指定付款方式、已付/退款/健康阻断通过；90 条 L2 的活动完整性 `10/10`、主动推进 `>=28/30`、明确动作 `12/12`、硬安全 `6/6`、内部语言泄漏 `0`；完整 L3、HTTP 重放和 finalization 均为零真实发送、零策略外发。
- 第二版：至少 50 个隔离混合场景无内容/结构/身份回退；上线后至少 30 条自然流量达到 P50 `<=15s`、P95 `<=25s`，fallback 不高于基线，5xx 与进程重启为 0。
- 发布授权：产品负责人已明确要求实现、提交并按上述两批部署；只允许 clean `main` 完整 SHA。第一版发布前确认生产已包含并验收 `sop-timeout-recovery`；第二版内网 RDS 切换须先证明同 `server_uuid`、TLS、schema 与只读数据一致，并保存 Reply 专用环境备份。
- 回滚：第一版回滚到部署前 clean release；第二版回滚到第一版 SHA，并恢复原 Reply 专用 `v3.env`。持续 5xx、身份串账、事实缺失、fallback 上升、连接错误、P95 超门槛或任何真实测试发送立即停止/回滚。

## 当前进度

- 状态：`stage2_candidate_validated_release_prerequisites_pending`。
- 已完成：第一版付款证据、付款卡动作一致性、动态活动完整交付、定向修复、超时完整重试约束、相邻容器付款审计字段无损提升，以及 L2/L3 评测设施修正；未修改 `reply_generation.py`、MytRpc、SOP、数据库 schema、外部协议或发送配置。
- 已确认根因：结构化活动交付引用由 `material_selection` 产生但付款校验未接纳；旧顶层 `action=none` 会压过实际付款卡；主线指向活动但 Router 未选中活动主题时 Reply 缺少完整活动事实；模型偶发把已生成的 `deposit_evidence` 放入 `policy_decision`，旧归一只处理 `sales_judgment`，导致证据被忽略并触发兜底。
- 第一版候选：提交 `086b83e222eb962affcaa3745ea2b651ae4a86fd` 已推送，保持为可独立发布的第一版边界。
- 第一版确定性证据：修改文件 `ruff`、`git diff --check`、Prompt 逐字快照校验通过；全仓 `1027 passed, 12 skipped`，跳过项为既有环境条件测试。
- 第一版 L2：固定基线 `183da8883ca207719463f03884786a1169bc6860`，DeepSeek、temperature `0.15`、候选 Prompt SHA `44e89831daa4e51b4b8dabe52e0b4ee11a2eeb84f86bf4013514ecff1f0dc827`；解析 `90/90`、活动完整 `10/10`、主动推进 `29/30`、明确动作 `12/12`、硬安全 `6/6`、内部语言泄漏 `0`，P50 `1932 ms`、P95 `2628 ms`。无关插入 `6/22` 按产品决策只观察。
- 第一版付款 L3：两个修正场景从零连续三轮，付款卡 `6/6`、fallback `0/6`；每轮 HTTP、幂等重放和 finalization 通过。
- 第一版完整 L3：`28/28` HTTP/结构/重放/finalization 合同通过，fallback `0`，真实发送 `0`，策略 outbox `0`；语义预设观察为 `24/28`，差异包括软拒绝继续解卡、重发地址后邀约及健康风险内部动作标签，不作为替代上述 L2 业务门槛的分母。
- 第二版生产只读基线：当前生产 `e2bd8ccf1851cee869324a461512e0379fb0f1de` 最近 `60` 条自然 V3 请求总体 P50 `6214 ms`、P95 `29076 ms`；其中进入 AI 图的样本仅 `12` 条，P50 `26957 ms`、P95 `50935 ms`、fallback/error `1`。AI 子样本的入口持久化 P50 `2697 ms`、接管检查 P50 `844 ms`、背景层 P50 `5705 ms`、Router P50 `2616 ms`、Reply P50 `3025 ms`；旧 SOP 进度读取 P50 `4055 ms`。报告仅含聚合耗时与计数，位于 ignored `artifacts/v3-payment-activity-latency-repair/stage2-production-baseline.json`。
- 第二版实现：同一销售接触边界的记忆与 SOP 已交付记录合并为一次只读数据库快照，旧接口仍保留；客户身份、订单/付款、接管状态继续每轮新鲜读取。新增入口连接领取、预检总耗时、快照连接/查询和 Reply 核心持久化的脱敏指标；新增只读聚合审计工具，不输出客户原文、身份、数据库地址或凭证。
- 第二版一致性与性能证据：快照输出与原三次独立读取逐字段一致，连接领取从 `3` 次降为 `1` 次；延迟注入测试证明移除两次远程连接等待；不同接待 WeChat/外部联系人不共享 SOP 状态。本次完整全仓为 `1033 passed, 12 skipped`；修改文件 `ruff`、`compileall`、`git diff --check` 通过。全仓 `ruff` 仍有 `45` 个基线未用导入/变量问题，均不在本任务文件中，未越权清理。未改前端，因此无需前端构建。
- 第二版隔离全链路：评测工具增加显式全矩阵 HTTP L3 模式；在临时 SQLite、虚构身份、虚构已验证素材且硬阻断发送下，生产对照并发 `2` 的 `60` 个不同场景为 `60/60` HTTP/结构/重放/finalization 合同通过，fallback `0`、真实发送 `0`、策略 outbox `0`。此前默认 L3 子集连续两轮另有 `56/56` 通过。一次未登记虚构素材的 `action-03` 缺图被证明为素材身份门禁正确阻断，登记隔离素材后通过。压力观察中并发 `3` 为 `58/60`，两条分别因模型格式错误和总预算超时进入恢复/兜底；改为单并发后连续 `6/6` 通过，因此不判定为本次只读快照回退，但保留为上线后负载时段兜底监控风险。
- 发布前置：`v3-refund-fallback-repair` 代码已是当前生产 `e2bd8ccf` 的祖先，但其 active task 仍登记为未关闭；按本任务发布合同，在该独立任务正式关闭并重新现场核验生产基线前，不执行第一版发布。
- 第二版内网 RDS 前置：仓库和生产 Reply 环境目前没有提供可核验的同 VPC 内网 endpoint；因此不得猜测或切换 `AICS_MYSQL_HOST`。取得运维提供的候选地址后，仍必须验证 `server_uuid`、数据库名、TLS、schema/索引指纹和只读样本一致，并先备份 Reply 专用 `v3.env`。
- 第二版候选：提交 `87b48076` 已推送；分支头同时包含第一版独立提交 `086b83e2`，工作区干净。
- 待完成：由 `v3-refund-fallback-repair` 所有者正式关闭其 active task 后，集成 clean main、重新现场核验并发布第一版；第一版稳定后再发布第二版代码。第二版上线后累计至少 `30` 条进入 AI 图的自然请求，达到 P50 `<=15s`、P95 `<=25s` 且 fallback 不升、5xx/重启为 `0` 才算通过；内网 RDS 地址未提供前，不执行主机切换。
- 原始模型输出、日志和性能明细只写 ignored `artifacts/v3-payment-activity-latency-repair/`。
