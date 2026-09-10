# V3 素材交付治理

- task_id: `v3-material-delivery-governance`
- status: candidate_ready（待母窗口验收，未合并/部署）
- owner: 独立执行窗口
- branch: `codex/v3-material-delivery-governance`
- base_sha: `dafbeee8494c7f5f7de53a6e969f79329968b8d6`
- production_baseline: 本任务不部署；若取证涉及线上事实，执行窗口必须现场只读核验并记录，不得引用旧聊天或快照代替
- task_type: 代码 + 配置/业务素材目录治理；是否需要可逆数据迁移，由执行窗口取证后决定

## 任务使命

解决同一张图片可能同时来自效果素材库、跟进序列或卡点话术，并因来源 ID、URL、签名或转码不同而被重复候选、重复发送的问题；同时关闭“选中活动内容就连带获得收款等交易动作权限”的边界漏洞。

最终效果不是简单按 URL 去重，而是建立可跨来源、跨轮次、跨进程识别同一物理素材的统一身份，并把“内容可用”与“业务动作获权”彻底分开。正常相关素材仍应被直接交付，不能为了避免重复而整体减少销售价值。

## 已确认产品规则

1. 同一物理图片即使来自不同目录、不同话术、不同 URL、签名变化或常见重编码，在同一客户接触档案内也最多交付一次。
2. 客户接触档案边界严格为 `corp_id + wechat + external_userid`；不得跨企微接待身份共享已发素材状态。
3. 同一素材可以同时保留多个业务角色和来源，用于检索和审计；一次实际发送只能按当轮选定角色更新对应业务交付状态，不能因为目录别名误判其他阶段已经完成。
4. 跟进序列、卡点话术和活动内容只提供阶段目标、语义候选和素材引用，不能授权收款、预约确认、门店确认或其他真实完成态。
5. Reply 仍是唯一销售决策模型；Router 只召回，序列只提供参考，事实/工具决定动作资格，交付层只负责规范化、幂等和去重，不在代码中新增销售意图判断。
6. “这个活动给你留着”“给您安排上”等销售性软承接允许自然表达；只有“已预约成功、名额已锁定、预约金已到账”等真实完成态需要权威事实。本任务不得恢复已撤销的措辞拦截。

## 推荐技术方向

执行窗口完成只读取证后，可以在任务边界内自主调整细节，但方案应优先收敛到以下结构：

- 在素材进入目录或同步阶段生成统一的 canonical media identity：可靠的上游文件 ID 优先；可取得文件时以字节 SHA-256 作为精确身份，并以保守感知指纹识别常见缩放/压缩副本。
- 把来源 ID、URL、目录位置、序列/话术引用和业务角色作为 alias/provenance 绑定到 canonical identity，而不是各自成为独立可发送素材。
- 候选进入 Reply 前先按 canonical identity 合并；Reply 看到的是一个可交付实体及其相关角色，而不是多份重复素材。
- 按客户接触档案持久记录已实际交付的 canonical identity 和本次使用角色；服务重启、URL 变化和任务重放后仍能阻断重复。
- 最终提交前保留一次确定性安全校验，防止上游遗漏或并发重放造成同批重复；已存在的稳定消息 ID 幂等合同继续有效。
- 已知目录素材的热路径不得依赖临时联网下载来计算指纹；运行时下载和指纹只能作为未知素材的有界 fallback 或审计能力。
- 感知指纹必须保守，并保留版本、阈值和可审计的人工 override，避免把不同效果图误合并。
- 内容候选只携带文本/图片/视频等内容能力；收款卡、预约写入、门店确认等动作必须由 Reply 明确选择且通过当轮权威事实与动作白名单，不能从 `activity_intro` 或任何素材角色隐式继承。

如果现有结构能够用更小且同样可靠的方案满足全部验收，执行窗口可以采用；若需要新增不可逆迁移、改变公共接口、增加模型节点或改变以上产品语义，必须先回母窗口审核。

## 独占范围

- V3 素材目录及同步后的规范化身份。
- 素材候选装配、跨来源合并和 Reply 输入中的素材证据结构。
- 素材交付记忆、最终交付去重、并发/重放幂等。
- 与上述行为直接相关的 schema、可逆迁移/回填工具、合同、架构说明和确定性测试。

执行窗口先取证再在本文件登记精确文件集。当前 active INDEX 无其他任务，仍不得顺手修改无关模块。

## 非目标和禁止项

- 不改 Reply Prompt、销售节奏、主线阶段、意图/情绪/卡点判断或自然度策略。
- 不新增模型调用，不用 Python 关键词分支判断正常销售语义。
- 不治理每一张素材的业务内容是否正确、过期或适配具体项目；本任务只保证身份、来源、角色、动作和重复边界。
- 不把素材去重等同于聚合平台已按 `client_message_id` 完成客户侧发送幂等。
- 不读取或提交真实客户原文、原始模型输出、签名 URL、生产素材文件或完整日志。
- 不发送客户消息、不写第三方生产数据、不合并 main、不部署。

## 必读依据

- `AGENTS.md`
- `docs/INDEX.md`
- `docs/current/MASTER_CONTEXT.md`
- `docs/current/DEVELOPMENT_STATUS.md`
- `docs/current/KNOWN_ISSUES.md`
- `docs/architecture/SYSTEM.md`
- `docs/architecture/V3_MODEL_NODES_AND_PROMPTS.md`
- `docs/contracts/sales-strategy.md`
- `docs/contracts/v3-reply-admission.md`
- `docs/contracts/RUNTIME_BOUNDARIES.md`
- `docs/contracts/message-delivery-callback.md`
- `docs/interfaces/public.md`
- `docs/tasks/PARENT_CHILD_WORKFLOW.md`

## 验收标准

### 产品与能力边界

- 单独选中活动内容或跟进话术素材，绝不会自动生成 `payment_collection`、预约完成态或写动作。
- 合法收款流程仍可在 Reply 明确选择、权威事实齐全和动作白名单允许时正常工作。
- 同一图片同时存在于效果素材库和跟进序列/卡点话术时，本轮候选只保留一个可交付实体，最终最多发送一次。
- 同一客户后续轮次即使 URL、签名、来源 ID 或常见压缩/缩放发生变化，也不会再次建议或发送；服务重启后结论不变。
- 不同客户接触档案互不污染；不同物理素材、图片与视频不会被误判为同一资产。
- 同一 canonical asset 的多个业务角色可保留，但只有本轮实际选择的角色更新业务交付状态。
- 失败重试、并发请求和相同消息重放不会造成额外素材发送记录或重复结构消息。

### 工程与性能

- 已知素材正常路径不新增网络下载和模型调用；候选规模、存储和延迟均有明确上界。
- 精确哈希、感知指纹、阈值版本和 override 行为可解释、可测试；误合并有恢复路径。
- 如需迁移或回填，必须可重复、可中断、可回滚；先用合成/脱敏数据 dry-run，不执行生产写入。
- 确定性测试覆盖：跨来源同图、换 URL、签名变化、重编码、近似但不同图片、跨企微隔离、跨进程记忆、并发重放、角色归因和交易动作隔离。
- 相关回归及全量确定性测试通过，格式检查通过；测试默认不调用真实模型、真实发送或生产写接口。

### 文档与交付

- 长期产品合同说明 canonical identity、provenance/role、持久交付记忆以及内容/动作分权；架构文档与实现一致。
- 任务完成回报只给母窗口 `结论 / 改动 / 证据 / 风险 / 下一步`，原始产物留在 ignored `artifacts/`。
- 子窗口提交候选分支供母窗口 review；未经母窗口验收不得合并或部署。

## 停止并回报母窗口的条件

- 需要改变上述业务语义、客户身份边界或 Reply 唯一销售决策权。
- 必须修改公共接口且会影响第三方调用方。
- 必须执行不可逆数据迁移、生产写入、真实客户发送或部署。
- 发现与其他 active task 的 ownership 冲突，或无法同时满足“防重复”和“正常直接交付素材”。

## 当前状态

### 执行取证与 change contract（2026-09-10）

- 已 fetch 核验最新 origin/main=上述 base，包含登记提交 dafbeee8；启动 dirty=false。
- 独占 worktree：`C:/Users/24159/.codex/worktrees/444a/coze_cli_project`；已从该 base 建立任务分支。当前 main 活动索引仅登记本任务，无模块 ownership 冲突。
- 生产基线：未访问生产，本任务不对文档中的旧 release 作现场确认或上线声明。
- 取证：material_fingerprint 的指纹为进程缓存，450ms 预算失败退回 URL；Follow Knowledge file_id 可缺失为 0，效果配置允许仅 URL；case_image_sent 历史仅保留 100 个事件，document ID 仅保留 200 项；异步收尾不是并发素材预占锁。
- 取证：_v3_available_assets_for_turn 仅对 deposit_close 去付款卡，其他内容角色可携带交易结构；selected-content 完整交付校验将所有非文本结构视作必需项，可能通过修复间接要求补交易卡。
- 类型/范围：先独立收紧内容候选的结构能力（仅 text/image/video），保留 Reply 的独立付款/门店/写入事实选项及现有语义判断。
- 本阶段精确文件集：`ai_paths/app/services/content_capabilities.py`（新）；`ai_paths/app/graph/nodes/reply_contract.py`；`ai_paths/app/graph/nodes/material_selection.py`；`ai_paths/app/graph/nodes/reply_validation.py`；`tests/test_v3_content_capabilities.py`（新）；`docs/contracts/sales-strategy.md`；`docs/architecture/SYSTEM.md`；本任务文件。未登记文件不写入；后续身份与账本方案确认后另补精确集。
- 风险：未知 URL 且无可靠 ID/字节时不能区分新图与已发图；此失败语义已报母窗口，相关交付行为暂不修改。不得把近似哈希视为物理同一性的绝对证明。
- 验证：本地合成字节与候选、零网络 fetcher、L1 内容能力及合法独立交易回归；不调用真实模型/生产接口。
- 回滚：候选提交不集成即可；本阶段无迁移、无生产写入。
- 母窗口已确认：未知身份附件失败关闭、文字链路继续；不得声称发图，暂时失败不计已发送，目录同步确认后可恢复。已知身份热路径零下载，失败原因区分身份未知/超时/格式不支持/指纹失败。
- 母窗口另确认：`response_committed` 仅为稳定响应与素材占用同事务提交，不等于 sent/send_succeeded/delivered。事务回滚不留占用；没有权威失败事件不自动解除。正常 V3 实际发送/送达与 dispatch 映射仍为第三方独立接口任务，本任务不扩大公共协议，接受平台失败后可能不自动补发。验收只称 AI 生成侧去重。
- 为落实上述日志口径，补充独占 `projects/src/components/logs/run-log-viewer.tsx`（仅同步响应显示用语）和 `docs/contracts/message-delivery-callback.md`（仅说明现有协议边界）。不改变外部字段/schema。
- 扩展精确文件集（统一身份和持久去重）：`ai_paths/app/services/material_identity.py`（新）、`ai_paths/app/services/storage/material_repository.py`（新）、`ai_paths/app/services/storage/schema.sql`、`ai_paths/app/services/storage/mysql_schema.py`、`ai_paths/app/services/storage/store_base.py`、`ai_paths/app/services/storage/repositories.py`、`ai_paths/app/services/storage/run_repository.py`、`ai_paths/app/graph/graph_builder.py`、`ai_paths/app/graph/state.py`、`ai_paths/app/graph/nodes/semantic_evidence.py`、`ai_paths/app/services/v3_semantic_router_service.py`、`ai_paths/app/graph/nodes/reply_nodes.py`、`ai_paths/app/chat_runtime.py`、`ai_paths/scripts/sync_material_identities.py`（新）、`ai_paths/migrations/versions/20260910_01_add_material_identity.py`（新）、`tests/test_v3_material_identity.py`（新）。回填只运行本地合成数据；生产应用迁移和目录回填留给独立发布任务。原登记模块边界内，无其他 active ownership。

- 已完成：只读取证、合同确认、统一身份/响应占用/内容隔离实现、可逆本地同步工具、长期文档和确定性回归。
- 待完成：母窗口对候选 review/验收；集成与发布另立任务。本文件保留 active 供母窗口验收后归档。
- 发布：未授权。
- 回滚点：仅任务分支候选，不改变 main 运行行为；如实现失败直接不集成。

### 候选验收证据（2026-09-10）

- 最终重新 fetch：`origin/main=dafbeee8494c7f5f7de53a6e969f79329968b8d6`，登记提交仍为基线；没有其他 active 模块 ownership 冲突。独占 worktree/分支不变。精确改动为上述登记集合中的 25 个文件；`reply_nodes.py` 经取证已有仅图片/视频物化约束，无需修改。
- L1 全量：`PYTHONPATH=ai_paths python -m pytest tests -q --disable-warnings --tb=short` → **920 passed, 9 warnings，56.78 秒**。原始报告仅在 ignored `artifacts/material-governance/pytest-all-final.txt`。本任务新增身份 26 项、能力隔离 26 项，共 52 项。
- 证据组一（不重复）：`test_cross_source_reencode_restart_and_contact_isolation`、`test_stable_file_id_signature_change_needs_no_network`、`test_real_process_restart_keeps_claim`、`test_failed_transaction_replay_and_concurrency`、`test_core_response_and_claim_commit_or_rollback_together`。覆盖效果/跟进来源、换域名/签名、常见 JPEG/缩放、SQLite 重开及真正的新 Python 进程、并发唯一占用、同响应重放和事务尾部失败回滚。验收结论只为 AI 生成侧跨轮次/跨进程去重。
- 证据组二（不误杀正常新素材）：`test_near_images_and_video_do_not_merge`、`test_distinct_new_material_remains_available_after_nearby_material_claim`；近似但修改局部的真实合成图不合并，上一素材占用后新素材仍可交付。三个接触档案字段分别隔离；未知身份同步后恢复，暂时失败不产生占用；可信 file ID 零下载直接可用。9 组 JPEG 质量/尺寸参数验证常见转码样本。
- 证据组三（内容不能获权、权威动作正常）：`test_v3_content_capabilities.py` 对 6 角色 × 4 动作类型验证所有内容列表过滤收款、门店、预约完成和写动作，同时保留文字/图片与软承接；action-only 不能算已采用/完成。全量同时通过 `test_v3_quality_gate_whitelist.py` 的合法收款金额、预约完成权威事实/无事实拒绝测试，及 `test_v3_mainline_action_admission.py` 的真实付款卡与明确预约入口测试；未修改这些既有事实/动作规则。
- 恢复/审计：dry-run 不创建数据库；重复 apply 幂等；中断前或已回滚的 undo 可重入；目录变化拒绝过期候选；未占用别名可显式拆分、其他别名占用保留。身份未知、读取超时、字节不可取、格式不支持、指纹失败和目录/账本异常分别审计。
- 检查：全部变更 Python 文件 Ruff check 通过；7 个新增 Python 文件格式检查通过；`git diff --check` 通过。管理页单行显示变更通过 TypeScript TSX 语法转译；当前 worktree 无前端依赖，未运行完整前端构建或浏览器验收。
- 权限审计：全部媒体、身份和数据库均为本地合成值；零真实模型、零客户发送、零第三方生产写、零部署。未运行 L2/L3/L4，未连接 MySQL 实例验证迁移或并发锁；SQLite 确定性证据不能冒称生产端到端证据。
- 明确限制：感知匹配是保守近似，不能绝对证明物理同一性，不能保证裁剪/重度压缩自动匹配；视频换编码依赖可靠 ID 或人工登记；错误别名一旦已占用不自动释放，只能独立审计迁移纠正；`response_committed` 不代表发送或送达，平台实际失败后可能不自动补发。历史已被截断/缺少身份的旧发送记录不能凭空恢复，生产启用前需单独审计历史与目录回填范围。
- 发布前提：另立任务执行可恢复目录同步/迁移、MySQL 验证和必要 L2–L4；目录未确认附件保持失败关闭。实际 `response_id/client_message_id ↔ dispatch_id` 发送映射及权威失败回调仍为独立第三方集成任务。
