# 生产状态

- status: verified-snapshot
- owner: operations
- verified_at: `2026-09-11T16:59:51+08:00`
- source_of_truth: 服务器 release 指针、进程环境、systemd 与健康检查；本页只对上述时刻负责

## 当前部署

| 角色 | Unit | Release / commit | 状态 |
| --- | --- | --- | --- |
| control | `ai-paths.service` | `ai-paths-unified-20260911-sop-timeout-e2bd8ccf` / `e2bd8ccf1851cee869324a461512e0379fb0f1de` | active，`NRestarts=0` |
| reply | `ai-paths-v3.service` | 同上 | active，`NRestarts=0` |
| worker | `ai-paths-workers.service` | 同上 | active，`NRestarts=0` |
| 管理前端 | `ai-paths-frontend.service` | `frontend-20260909-v3-supervisor-3192f19a` | active，`NRestarts=0` |

三个后端角色均来自 clean `main`，`dirty=false`，产品接口版本为 V3；已核验进程实际工作目录、两份发布身份环境与发布清单一致。管理前端保持原版。本次发布将第三方 SOP 任务并发硬上限降为 4，为 `/pending`、`/sop-messages` 增加一次独立新连接恢复，任务前两次可恢复失败不告警，连续第三次失败才终态消费并告警；全局拉取连续三轮超时才发一次系统告警，成功后清零。无 schema、业务门槛、消费协议或模型配置变更。

2026-09-11 素材 P1 恢复时没有部署代码或重启服务：当时生产 release 为 `25e1ed66`，已从受测 clean `main@5fd237cc` 使用冻结工具向现有表回填 231 个已验证别名，对应 217 个 canonical 素材身份，并按精确证据恢复 22 条历史 `response_committed` 占用。回填前后四个 unit 均 active、`NRestarts=0`，三个后端健康检查为 200，近 40 分钟未发现 5xx、Traceback 或 CRITICAL 标记。

本次为用户知悉风险后的例外上线，并非完整质量验收通过。确定性回归 865 项通过；部署后 14 项接口/访问保护检查通过；服务器隔离完整模型图抽样 6/6（价格、案例、地址、活动、退订、已付服务），零消息派发和策略外发。正式公网回复与回调维持来源 IP 白名单，非白名单请求返回 403；未模拟受信平台真实送达。

部署后 90 个虚构 Reply 场景：解析 90/90、综合 79/90、硬安全 6/6、明确动作 11/12、主动推进 27/30、活动完整性 7/10、无关插入 3/22，零模型请求错误和 GPT 回退。明确动作未达标项为效果回答的动作标签不符合预设，并非完全没有回答效果。活动遗漏和不必要推进仍需修正，完整 L3 与业务盲审仍未完成；抽样通过不可替代全部验收。

## 当前模型事实

| 用途 | 生产实际模型 | 说明 |
| --- | --- | --- |
| Semantic Router | `deepseek-v4-flash` | 失败时使用独立 `deepseek-chat` 语义客户端，不进入 GPT |
| 最终 Reply、完整重试、定向修复 | `deepseek-chat` | Reply fallback 为空；不继承全局 GPT emergency fallback |
| 图片理解 | `gpt-5.4`，fallback `gpt-5.4-mini` | 仅有图片时调用，不做销售决策 |
| 门店目的地解析 | `deepseek-chat` | 生产未覆盖该 tier 的代码默认 fallback，默认仍含 `gpt-5.4,gpt-5.4-mini`；是否发生 fallback 以 run trace 为准 |
| 沉默唤醒计划 | `deepseek-chat` | 独立配置，fallback 为空，不属于同步 V3 Reply 节点 |

Reply 有效配置：`V3_REPLY_MAX_MESSAGES=8`、`V3_REPLY_MAX_TEXT_CHARS=300`、
`V3_REPLY_TEMPERATURE=0.15`。客户可见自动失败补答保持
`V3_REPLY_RECOVERY_ENABLED=false`；代码存在不等于线上启用。

完整节点、Prompt 和动态上下文见
[V3 大模型节点与 Prompt 全景](../architecture/V3_MODEL_NODES_AND_PROMPTS.md)。

## 当前业务开关

- `AI_SALES_POLICY_ENABLED=true`。
- `FOLLOW_KNOWLEDGE_ENABLED=true`。
- `AI_CLOSING_CATALOG_SOURCE=external_then_local`：外部目录正常且非空时使用外部；否则使用版本化临时本地目录，不在同一轮混用。
- 沉默唤醒已启用，候选阈值 1 分钟，账号白名单为空表示覆盖全部企微；不限制加微时间。
- 沉默唤醒仍要求计划前和每次发送前确认平台明确为 AI 接待，并复核新客户消息、退订、关系删除和交易终态。1 分钟是候选资格，不是发送 SLA。
- 安静时段为 `22:00–08:00`；普通计划顺延，客户夜间仍活跃时将完整节点压缩至最后客户消息后的 40 分钟内。
- 延时 B 单和四大区策略保持 Shadow；目录存在不会自动授权发送。

## 数据、队列与已知现场量

- 数据库后端为 MySQL，现场 schema head 为 `20260910_01`；素材身份目录现有 231 个 verified 别名、217 个 canonical 身份（193 个图片别名、38 个视频别名），22 条占用均为 `response_committed`。Follow Knowledge 当前 299 个媒体引用中 293 个已验证，6 个因超过 6 MiB 安全上限保持 pending/disabled；13 个业务角色均至少有一个 verified 素材。不要使用其他系统的 `alembic_version` 表推断本产品 schema。
- 第三方 SOP 有效并发与配置并发均为 4；核验时 `pending_total=0`、`queue_depth=0`、`in_flight_count=0`，最近轮询错误为空，连续轮询超时为 0。发布后连续 20 次拉取成功，平均约 687 ms，瞬时超时、恢复成功和恢复耗尽计数均为 0；当日告警总数在观察窗口内保持 17 条，没有新增。
- V3 `v3_reply_finalization` 可靠收尾 Worker 已启用；非关键 memory、trace、BI、Shadow 与 outbox 由持久状态幂等补齐。
- 策略数据 outbox 最近已知为 `sent=1994`、`pending=59`、`dead=16`，且 `delivery_enabled=false`。恢复外发前必须专项审计，禁止直接批量重放。
- 平台订单异步归因开关在本次核验中没有取得明确启用事实，保持 unknown，不能写成已启用或未成交。

## 回滚点

- 后端与 Reply previous：`ai-paths-unified-20260911-refund-fallback-31882257`，完整 SHA `31882257f955dcf2c2c366755ebd18534ee9d0a5`。
- 本次发布前两次切换因发布脚本只切代码、未同步两份发布身份，健康检查按设计回滚；同时发现旧错误处理会在回滚后错误打印成功。修正为代码、并发和两份发布身份一体切换/回滚，三个角色统一验活后重新部署成功。发布包 SHA256 为 `a3beb499df68a67e915054a1f6d9f4398a87f52bc911d391afd5816e0f991ec9`；服务器受限目录 `/opt/ai-paths/artifacts/` 保存环境备份和发布脚本，密钥文件不下载、不入 Git。
- 前端 previous：`frontend-20260908-v3-proactive-05723ce3`。
- 数据库迁移前备份已存在；新增结构兼容旧代码，代码回滚不要求破坏性降级。
- 素材回填冻结计划 canonical checksum 为 `ff705fc9d69d34773a815bb49e2a2bddcb31cb772d90135b081581fabafff909`；回填前空表备份 checksum 为 `e8e1bf9b5d01f8ae9ba28d1063a1e3fb9283c0a33f614b9a91f08646286c7099`，受限恢复产物保存在服务器 `/opt/ai-paths/artifacts/material-governance-production-audit-4e299c1a/`。已有占用时自动 rollback 会拒绝删除，恢复必须按该备份和冻结进度做独立人工审计，不能释放真实新占用。

任何发布前都必须重新核验 current/previous 指针、完整 SHA、`dirty=false`、四个 unit、数据库
head、队列和 Nginx；本页不是永久配置清单。历史发布经过由 Git 与
[任务历史索引](../tasks/history/INDEX.md)追溯，不再堆叠在当前状态页。
