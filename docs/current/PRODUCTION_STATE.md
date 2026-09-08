# 生产状态

- status: verified-snapshot
- owner: operations
- verified_at: `2026-09-08T12:39:17+08:00`
- source_of_truth: 服务器现场核验；本页只在上述时刻有效

## 当前后端 release

- release: `ai-paths-unified-20260908-123216-3fc85db4`
- git commit: `3fc85db46a17662aa47e0903297eb515de13ec43`
- branch contract: `main`
- dirty: `false`
- config revision: `9e2a9dc4f2559bd0a78d38226717365dd5e548988c7a4595972ecaa37321137f`
- database backend: MySQL

| 角色 | Unit | 现场状态 | 现场健康信息 |
| --- | --- | --- | --- |
| control | `ai-paths.service` | active/running | `/health` 返回 `service_role=control`，后台 worker 关闭 |
| reply | `ai-paths-v3.service` | active/running | `/health` 返回 `service_role=reply`，与当前 release/commit 一致 |
| worker | `ai-paths-workers.service` | active/running | `/health` 返回 `service_role=worker`，后台 worker 已启用 |
| 管理前端 | `ai-paths-frontend.service` | active/running | `frontend-20260907-201840-44fcd568`；身份名称和兼容字段已同步 |

三个后端角色均由同一 clean main SHA 构建。V3 Reply 进程的有效覆盖配置为 `MODEL_REPLY=deepseek-chat`、Reply fallback 为空；沉默唤醒由独立 `OUTREACH_DECISION_MODEL=deepseek-chat` 配置控制且 fallback 为空，不再继承 worker 的通用 GPT tier。共享基础环境仍保留其他角色的全局模型默认值，实际模型应以每次 run trace 为准。

## 已核验开关

- `AI_SALES_POLICY_ENABLED=true`
- `FOLLOW_KNOWLEDGE_ENABLED=true`
- `AI_CLOSING_CATALOG_SOURCE=external_then_local`
- `OUTREACH_FIRST_DAY_SILENCE_ENABLED=true`
- `OUTREACH_FIRST_DAY_SILENCE_MINUTES=1`
- 企微 allowlist 为空，表示全部企微号进入候选。
- 启用水位：`2026-09-05T09:41:20+00:00`；水位前历史沉默不补发。
- 当前代码仍要求沉默计划前和每次发送前由平台明确确认 AI 模式；人工、未知或状态查询失败均阻断。
- 沉默唤醒不再设置每位客户每日计划数和任务数上限；有卡点时按所选外部跟进序列的完整节点数生成任务，无卡点时按 DeepSeek 选择的未完成主线素材生成动态任务。
- 安静时段为 `22:00–08:00`，常规计划顺延到 `08:30`；客户夜间仍活跃时，完整计划压缩到最后一条客户消息后的 40 分钟内，但每个节点发送前仍重新执行 AI/人工、客户回复、退订、订单终态等校验。

## Worker 与 outbox

- 第三方 SOP worker 正常运行，现场 `queue_depth=0`、`pending_total=0`、`in_flight_count=0`，最近轮询错误为空。
- Reply 健康信息显示策略数据 outbox：`sent=1994`、`pending=35`、`dead=16`。
- 策略数据外发当前 `delivery_enabled=false`；恢复前必须对 dead/pending 做专项审计，不能直接批量重放。
- 平台订单异步归因开关未在本次安全配置核验中发现显式启用值；在下一次归因或发布任务中重新确认，不把未知写成已启用。

## 回滚状态

- 后端 `/opt/ai-paths/previous` 与 Reply `/opt/ai-paths-v3/previous` 均指向上一 clean release `ai-paths-unified-20260908-122728-bafdf430`。
- 前端 `/opt/ai-paths-frontend/previous` 指向 `frontend-20260907-152202-b4dfc184`。
- 数据库已迁移到 `20260907_02`，新增兼容的 `aics_customer_identity_links`；发布前 AICS 20 张表、841,130 行的一致性压缩备份保存在 `/opt/ai-paths/backups/pre-44fcd568-20260907-201840/aics-before-20260907_02.sql.gz`，SHA-256 为 `481b853b31b244fa8e307a31f3be632ef46904ca3ea7692fdfc3da1e4a23439c`。旧代码会忽略新表，回滚 release 不要求破坏性降级。
- 回滚仍应同时恢复三个后端角色、前端和 release 环境标识，并重新核验健康。

## 本次发布观察

- 2026-09-08 12:39 发布并核验 clean `main@3fc85db46a17662aa47e0903297eb515de13ec43`，release 为 `ai-paths-unified-20260908-123216-3fc85db4`。企业微信固定开场和撤回协议消息在 AI/人工状态、语音、连续消息协调及模型链之前直接返回空回复；审计在同一 FastAPI 请求生命周期的响应后台阶段以单事务写入。生产 10 次协议请求 HTTP P50/P95 为 `1.95/2.17ms`、最大 `78.27ms`，10 条 run 和 10 条 `platform_protocol_filter` 节点全部落库，客户消息、策略采用、主动唤醒、dispatch/outbox 写入均为 0。沉默扫描目标间隔为 15 秒，现场单轮约 32～34 秒，长轮完成后固定等待 5 秒再开始下一轮，连续失败按 15/30/60 秒退避；1 分钟仍是候选资格而不是准点发送承诺。全仓 465 条测试通过；无数据库迁移、无前端变更，control/reply/worker 使用同一 SHA，V1/V2 回复路由为 404，Nginx 配置检查通过，三个 unit 均 active 且 `NRestarts=0`。统一回滚点为 `ai-paths-unified-20260908-122728-bafdf430`。
- 2026-09-07 22:16 发布并核验 clean `main@e738330c`：距离卡点回复不再复述或放大“远、折腾、麻烦”，而是轻承接后转向技术、效果和案例价值；Reply 使用第三方距离话术时先对候选示例做客户可见表达适配，但保留真实序列、话术和素材 ID。销售可说“先保留活动名额”，真实“已预约/已登记/已排客”仍需权威事实。指定日志 DeepSeek 只读复现采用序列 11、话术 225 并直接交付效果视频，无门店重查、重复门店卡或兜底；全仓 438 条测试通过。本次无数据库迁移、无前端变更；V3 鉴权边界 401、V2 路由 404，四个 unit active 且 `NRestarts=0`，SOP 队列和 pending 为 0，回滚点为 `ai-paths-unified-20260907-211433-17813a1`。
- 2026-09-07 21:24 发布并核验 clean `main@17813a1`：V3 Router 保留客户提交信息和继续交易的只读证据，Reply 在无活动卡点且交易未终态时回答当前事实后回到一个主线动作；已确认具体门店时只追问一个到店时间方向。结构校验要求 `action=ask` 必须有可见问题、正常销售轮次最多一个问题，并保持退订、人工接管、风险和终态优先。全仓 435 条测试及 Ruff 通过；指定日志生产配置隔离重放交付真实门店卡并只追加一个到店问题，生产写入为 0。发布后合成 V3 HTTP 验证成功且测试数据精确清理；V3 路由鉴权返回 401、V2 路由返回 404，三个后端与前端均 active、`NRestarts=0`，新版本启动后二十分钟无 error 级日志。MySQL 仍为 `20260907_02`，SOP 队列和 pending 均为 0；本次无数据库迁移、无前端变更，回滚点为 `ai-paths-unified-20260907-201840-44fcd568`。
- 2026-09-07 20:35 发布 clean `main@44fcd568`：逐提交收敛所有 worktree 和远端分支。预约时间边界、直接预约收口已是 main 祖先；身份合同修复完整合入；生产内存治理作为已知问题保留；旧 DeepSeek 评测分支只含过期活跃任务占位且工作区有未提交错字/产物，未覆盖最新版代码。
- 新版把平台客户 ID、企微外部联系人 ID、平台接待人员 ID、接待企微和加微关系 ID 分开处理，客户状态只按 `corp_id + wechat + external_userid` 隔离；新增身份质量与冲突只读接口。生产最近 200 条入参中 200 条平台客户和外部联系人互不混用，199 条具备完整托管身份；唯一缺接待人员 ID 的旧请求发生于 9 月 5 日，新版会在模型前返回 400。
- 合并后 422 条后端回归、Python 编译、迁移单 head、前端 TypeScript/Lint/生产构建通过。上线后合成 V3 请求返回成功，随后精确清理其 1 条 run、8 条 trace、1 条消息和 1 条会话；未产生策略事件、主动唤醒、SOP 或发送记录。错误身份请求在模型前返回 HTTP 400。
- control、reply、worker 均返回 release `20260907-201840-44fcd568`、完整 SHA 和 `dirty=false`；四个 unit active 且 `NRestarts=0`，前端 `/logs` 返回 200，Nginx 配置检查通过。SOP 队列和 pending 为 0；主动唤醒完成 1038 候选扫描，36.594 秒、错误 0，全部账号、1 分钟、DeepSeek、无 fallback 和动态序列节点配置保持生效。
- 发布过程没有在生产机安装依赖或构建前端；本地构建产物直接部署并复用已验证依赖硬链接。生产根分区当前 78%、剩余约 8.4 GB；数据库备份必须保留到本版本稳定确认后。

- 2026-09-07 19:49 发布 clean `main@0873b27d`：沉默唤醒改为从第三方 92 条跟进序列和 522 条话术中选择，有卡点时按所选序列全部 3～11 个节点生成任务；兼容平台当前 `act001～act038` 及后续合法 `actNNN`，不再静默丢弃新动作节点。取消固定两步、固定 15～20 分钟、固定场景组合和每日 2 计划/4 任务限制；夜间非活跃顺延，夜间活跃 40 分钟内压缩。
- 发布现场目录为 92 条可用序列、544 个完整节点、522 条话术、28 种实际动作码，非法序列 0；隔离验证做到 544/544 节点均有渐进话术候选，28/28 代表动作由 DeepSeek 选择真实话术，92/92 序列完整保留夜间节点。完整确定性回归 411 条通过。
- 同轮修复扫描器逐客户访问远程 MySQL 查询已处理指纹的问题，改为一次有界快照并在真实会话刷新后保留权威复核；生产 1038 个候选的一轮扫描约 33.5～35.3 秒完成、错误 0，不再持续数分钟卡在 running。平台明确返回“会话不存在”或身份缺失时永久阻断本指纹，临时 AI 状态查询异常最多重试 3 次，避免历史无效客户无限占用扫描名额。
- control、reply、worker 的 `/health` 均返回 release `20260907-190313-0873b27d`、同一完整 SHA、`dirty=false`；四个 unit active 且 `NRestarts=0`，Nginx 配置检查通过，V3 路由存在、产品 V2 路由仍未注册。即时回滚 release 为 `20260907-185509-25cea55f`，本次无数据库迁移。
- 2026-09-07 16:58 发布 clean `main@9b8ba028`：合入大城市门店列表最新版、预约时间事实边界和已配置 B 单规则候选恢复。control/reply/worker 使用同一 release，三个 `/health` 的 commit、config revision 和 `dirty=false` 一致，均 active/running 且 `NRestarts=0`；唯一客户回复路由仍为 `/reply/workflow-compatible-v3`。
- “怎么预约”真实身份只读重放命中本地真实规则、策略、节点和话术，Reply 输出文字及 10 元预约金卡，耗时 14.43 秒；“明早 9 点可以吗”在无本轮营业时间事实时被校验并修复为到店意向、门店确认后再去，耗时 11.74 秒。两条均只使用 DeepSeek，生产发送和生产落库为 0。
- 当前预约合同区分三层事实：可预约/时间可协调是业务能力，客户提出的具体时间可记录为到店意向；具体空位、营业时间、已预约、已安排或允许直接到店必须有本轮权威事实。双次模型越界时使用同话题的安全恢复，不再被门店补位置兜底抢走。
- 大城市普通“有没有门店”且候选超过 6 家时，按最新版先给真实覆盖区县并追问区县/地标；只有客户明确索要全部门店，才编号列出完整门店名、区县和地址。门店类失败恢复与预约事实失败恢复已按错误类型隔离。
- 合并后全仓 394 条测试通过，重叠的 Reply 恢复、门店事实校验、Prompt 与业务规则均完成组合回归；本次无数据库迁移、无前端变更。生产根分区 76%，剩余约 9.0 GB。

- V3 唯一回复路由为 `/reply/workflow-compatible-v3`，未注册产品 V1/V2 回复路由。
- Reply 进程 `/proc/<pid>/environ` 已现场确认 `MODEL_REPLY=deepseek-chat`、`AI_SALES_POLICY_ENABLED=true`；共享基础环境中的其他角色模型值不代表 Reply 实际模型。
- Nginx 配置检查通过，四个 service 均 `NRestarts=0`。
- Worker 健康信息已显示沉默唤醒为全账号、1 分钟、`deepseek-chat`、无 fallback，计划扫描和发送执行任务均存活。发布后首批 9 个判断产生 2 个计划并各成功发送第一步，5 个明确人工模式被阻断，1 个重复指纹被阻断，1 个场景合同失败进入有限重试；留存模型均为 DeepSeek，GPT 和“不支持模型”错误为 0。
- 指定问题客户已经生成两步计划并成功发送第一步；第二步保持 pending，仅在客户继续沉默且发送前平台仍明确为 AI 时执行。1 分钟是进入候选阈值，不是固定发送时刻；首批多节点计划生成和平台/RDS 调用仍有约 1～3 分钟延迟，详见 `KNOWN_ISSUES.md`。
- 已采用的话术若带有本轮相关、安全且未发送的效果图/视频，会把媒体作为客户可见结构消息直接交付，不再先问“要不要发效果图”；活动价格等已有权威价值也应直接回答。代码只补齐模型已经采用的话术自身媒体，不跨话术或跨主题替模型做销售选择。
- 指定问题场景 DeepSeek 隔离复现 2/2 通过；20 条真实身份只读矩阵 AI 初评通过率和真人表达通过率均为 95%，许可式素材追问为 0，策略适用样本中的序列/话术采用为 6/6，生产发送和关键写入均为 0。确定性回归为 326 条全部通过。
- 销售策略 BI 已改为“有效决策 → 卡点 → 序列/话术候选 → Reply 正式采用”的管理链路，移除 7d 排客率。候选和正式采用使用独立字段，历史回填 110 条均有运行快照证据、0 条无法确认；有效客户轮次中旧序列字段的 9 条有 5 条只是候选，正式采用为 4 条，话术正式采用为 2 条。
- SOP 运行监控已上线：独立只读接口 `/admin/sop-platform-dashboard` 从 MySQL 聚合平台任务、本地任务、客户、真实发送消息、无需发送、异常、未完成、小时趋势、企微号分布和持久化耗时；页面同时从 worker 进程读取实时队列，默认不刷新第三方平台。
- SOP 专用聚合接口热态实测约 3.3～4.2 秒；服务重启后的首个冷查询约 15～19 秒。发送完成只按主动发送接口返回的消息 ID 计数，不从会话归档推断。
- SOP 处理结果总览已独立为“监控”导航下的 `/analytics/sop`；`/logs/sop-platform` 只保留任务证据，`/logs/sop` 明确标为底层事件日志。三个页面只读，不触发平台拉取、发送、消费或回传。
- 2026-09-07 14:09 现场口径更新为平台任务 121、本地任务 121、确认发送 67 批/147 条消息、无需发送 52、异常 1、未完成 1；队列、平台待处理和执行中均为 0。独立看板接口本次发布后实测 7.45 秒。
- 30 天线上口径为 213 个客户、279 个真实 V3 轮次、23 个有效策略决策；卡点识别率 `9/23=39.1%`、序列正式采用率 `4/9=44.4%`、话术正式采用率 `2/10=20.0%`、正常决策 `16/23=69.6%`。整页接口实测 `4.21s`，9 个并发只读视图无错误。
- 桌面和 390px 手机真实浏览器验收通过：无控制台错误、无横向溢出、7d 排客率未出现；334 条后端回归、前端类型/Lint/生产构建通过。
- MySQL/RDS 在发布前后均有间歇性连接超时；当前三个角色健康、SOP 队列和 pending 为 0，但该外部连接风险需继续处理，详见 `KNOWN_ISSUES.md`。
- 沉默计划模型现在接收去 URL 的素材来源、用途与本轮可发送状态；写作和审核共享逐步媒体交付合同。当前生产目录共 68 张图片、0 个视频；无媒体步骤禁止生成悬空看图表达，`effect_proof` 必须绑定真实可发媒体。
- 千人千面日志列表和详情接口已现场返回 `business_summary`、`observability_view`、10 个模型/修复节点及素材摘要；前端桌面、手机和节点抽屉已验收。
- 主动唤醒 BI 已上线 `/analytics/outreach` 和只读接口 `/admin/outreach/dashboard`。当前 100 条上限查询返回 91 条队列记录：客户 ID 与外部联系人 ID 覆盖 `91/91`，会话 ID 覆盖 `54/91`；4 条真实生成计划的记录全部带任务明细，共 8 个任务引用。队列和详情可查看并复制客户、外部联系人、会话、客户加微、平台客户、企业、接待人员、企微、唤醒运行、计划、任务及平台消息 ID；没有真实计划或历史未留存会话的记录明确显示未记录，不补造 ID。
- 主动唤醒看板热态现场查询约 6.2 秒。已修复后台统计较慢时 Worker 健康响应体在等待期间过期、导致前端错误显示不可用并返回 500 的问题；修复后接口返回 `runtime_source=worker_service`。该看板只读，不触发扫描、计划、发送、重试或平台写入。
- 本次发布 control、reply、worker 和前端均为 clean `main@71a18d7d`，四个 unit 为 active 且 `NRestarts=0`，三个后端 `/health` 的 release/commit 一致。主动唤醒接口热态实测约 6.46 秒；后端 345 条回归、前端类型/Lint/生产构建以及桌面/390px 手机真实浏览器验收通过。本次只扩展只读观测，不修改候选、计划、发送或 SOP 运行逻辑。
- 生产根分区使用率为 74%、剩余约 10 GB；本次前端 release 通过只读硬链接复用未变更的依赖文件，并已清理自身 `/tmp`。后续仍按发布保留规范维护当前版和已验证回滚版。
- AI 运行日志现在从 V3 接口进入 Reply 服务时开始计时，到最后一个 HTTP 响应体成功发送后结束；列表和详情的“接口总耗时”优先使用该口径，同时保留模型图耗时供排障。发布后的真实请求已显示 8.79～10.2 秒完整耗时；发布前历史请求不补造新口径。
- 日志详情新增客户 ID、客户加微 ID、外部联系人 ID、企微 ID/账号、企业 ID、接待人员 ID、会话 ID、请求 ID，并支持逐项复制。字段只取本轮请求留存，不查询当前平台状态补历史；上游未传入时显示“未记录”。桌面 1440px 和手机 390px 真实浏览器验收通过。
- 本次发布 control、reply、worker 和前端均为 clean `main@b4dfc184`，四个 unit 为 active 且 `NRestarts=0`，三个后端 `/health` 的 release/commit 一致；后端 348 条回归、前端类型/Lint/生产构建通过。无数据库迁移，不修改 V3 回复、策略、发送或主动唤醒逻辑。生产根分区使用率为 76%，剩余约 9.3 GB。

每次发布任务都必须重新记录 main SHA、三个角色 release/健康、数据库、worker/outbox、Nginx 和回滚点。超过核验时间后，本页只能作为线索，不能替代现场事实。

## 2026-09-08 09:29 发布快照

- 后端 control/reply/worker 已发布 clean `main@42b27eaede76b329d86880668e6a20ad9aa0dd85`，release 为 `ai-paths-unified-20260908-092900-42b27eae`，三套 `/health` 均返回 `dirty=false` 且 SHA 一致。
- 回滚指针：`/opt/ai-paths/previous` 和 `/opt/ai-paths-v3/previous` 均指向 `ai-paths-unified-20260908-092011-ac512d2e`；其上一版为 `ai-paths-unified-20260908-000754-d13b73ae`。
- 本次无数据库迁移、无前端变更。`MODEL_REPLY=deepseek-chat`，`OUTREACH_DECISION_MODEL=deepseek-chat`，fallback 为空；沉默唤醒仍为全企微、1 分钟、按外部跟进序列节点数生成任务，夜间非活跃顺延、夜间活跃 40 分钟压缩。
- 修复点：计划主事务已提交后，审计事件、客户状态更新或回读失败不再把计划误标成失败；自动批准的 draft 计划会在重复指纹拦截前恢复；过期节点重排时保留平台相对间隔，不再把 9 个节点压成 8 秒内连发。
- 指定客户计划 `c11264db-4b83-42ab-9724-21a3b7dc6785` 已从 draft 恢复为 active，并按平台 0/0/2/2/5/5/15/15/30 分钟节点重排；发布后确认前 6 个节点已发送，第 7/8/9 个仍 pending，后续任务仍受发送前 AI/人工、客户新回复、退订、订单终态等校验保护。该密集节奏来自平台序列配置，不是旧版 08:30:00-08:30:08 连发压缩。
- 生产库当前仍有 7 个普通 draft 计划、16 个非首日 pending/checking 任务；首日沉默唤醒 draft pending 为 0。最近 10 分钟 systemd 日志未见 error/traceback/failed。

## 2026-09-08 11:55 第三方 SOP 运行时热修

- 发布 clean `main@a8f0f63c97ed9e48f8877681bce6cf8eab474b32`，release 为 `ai-paths-unified-20260908-114958-a8f0f63c`；control、reply、worker 三套 `/health` 的 release、完整 SHA、config revision 和 `dirty=false` 一致，三个 unit 均为 active 且 `NRestarts=0`。
- 修复规范客户身份对象直接展开到旧 Outreach 客户端造成 `platform_customer_id` 非法参数的问题；完整身份继续用于审计和客户边界，客户端调用只接收其合同规定的 5 个兼容字段。
- 第三方固定消息预检兼容平台实际返回的 `msg_type`、`content_text`、`media_url` 和 `media_urls_json`，同时继续拦截非法类型、空文本和非法媒体 URL。
- 全仓 457 条确定性测试、相关 Ruff 和服务器离线预检通过。发布后新任务 `82347` 完成会话状态与历史拉取、真实发送、consume `30` 和 service-rule-data 回传；worker `sent=1`、`pending_total=0`、`queue_depth=0`、`in_flight_count=0`、最近轮询错误为空。
- 未补发或重开历史终态任务；`82344` 仍保持 `completed_without_send/invalid_message_content`。本次无数据库迁移、无前端变更；统一回滚点为 `ai-paths-unified-20260908-092900-42b27eae`。

## 2026-09-08 13:19 第三方 SOP 严格顺序发布

- 发布 clean `main@2b7d70910c2da4d93a64f459a992dc29a4300316`，release 为 `ai-paths-unified-20260908-131434-2b7d7091`；control、reply、worker 三套 `/health` 的 release、完整 SHA、config revision 和 `dirty=false` 一致，三个 unit 均为 active 且 `NRestarts=0`。
- 同一销售接触档案只允许处理最早内容。前序未确认发送时，模型不发、发送失败或未知、人工接管、客户删除、夜间拦截等全部记录失败并保留内容；运行代码不再主动写平台 `70`，也不能消费未发送前序后跳发后序。
- 发送超时或仅返回受理但没有消息 ID 时等待送达回调或会话中的相同发送证据；配对触发节点只有在内容真实发送后才与内容节点一起回传 `30`。恢复查询使用持久化退避，重启后继续保留顺序阻塞。
- 发布后连续两次只读核验为 `pending_total=0`、`queued_count=0`、`in_flight_count=0`、`last_poll_error` 为空，发送和消费计数均为 0；历史未发任务没有补发或消费，86 个历史任务 ID 仅恢复为顺序阻塞。Worker 最近 300 行错误日志无 traceback。
- 全仓 479 条确定性测试通过，变更范围 Ruff、服务器编译和模块导入通过。V3 路由无鉴权返回 401，产品 V2 路由返回 404，管理接口无鉴权返回 401；Nginx 配置检查通过。本次无数据库迁移，生产根分区使用率 81%、剩余约 7.3 GB。
- 统一回滚点为 `ai-paths-unified-20260908-131037-bb86bc3b`；回滚后必须同步恢复 control/reply/worker 三个角色和两个环境文件的 release 元数据。
