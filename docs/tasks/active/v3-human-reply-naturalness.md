# V3 真人回复自然度治理

- status: active

## Review 修订轮（优先于下方历史授权与进度）

- 本轮用户明确禁止合并和部署；此前上线授权不适用于本候选。只提交推送 `codex/v3-human-reply-naturalness`，保留 active，等待母窗口验收。
- 本轮 base SHA：`afb261c0480811c89fd58f98c3c778169303f287`；已将干净旧候选快进到最新 main，未修改 main。任务 ownership 已在 main 登记，本窗口不编辑 active INDEX。
- change contract：修复静态及动态推进指令冲突；仅为 activity_offer 主线机会补齐事实可见性；统一话术只提供论据而非表达模板。范围仅 Prompt、Reply 上下文、直接测试/评测、销售合同、架构快照和本任务文档；不改素材身份、Router、admission、模型参数或数据库。
- 风险：推进过于保守、事实齐全但模型仍遗漏条件、隔离事实与真实目录差异；无不可逆数据变更，回退本轮候选提交即可。
- 新回归覆盖：Router 未选活动时完整事实仍可见、无主线机会时不额外扩展、Router 显式选择保留、输入不变、缺事实不编造、话术风格隔离及推进前置门。
- L1：`PYTHONPATH=<worktree>/ai_paths python -m pytest tests -q`，最终 869 passed，9 个既有弃用警告；增加生产路由、幂等重放及收尾的确定性测试。改动文件 Ruff 与 git diff --check 通过。中间候选曾触发 7,000 字符预算和旧断言失败，最终保留预算且全量通过。
- 评测：保留原 90 场景、评分口径、DeepSeek 和 temperature 0.15；原始数据只进入 ignored `artifacts/v3-human-reply-naturalness/review-fix-*`。完整图脚本接入生产 ASGI route/middleware、临时库、幂等重放和 finalization，不注入预生成 Reply；不是线上 TCP/第三方链路验证。
- 门槛仍为活动 10/10、无关插入 0/22、主动至少 28/30、明确动作 12/12、安全 6/6、内部泄漏 0、完整隔离图通过；业务 50 条盲审待人工，开发不代评分。
- 代码候选 commit：`a924b0980453bd8a9c12f5e247265b22a6088ad9`；Prompt 7,000 字符、73 行，SHA-256 `da1c05f4ca8da7e8d24282561cb7844208834b5da52c45d80ff70c5bfc389582`，架构快照逐字一致。最终交付 commit 另包含本轮证据摘要，以该分支 HEAD 为准。
- L2 首轮命令：`python ai_paths/scripts/evaluate_v3_reply_naturalness.py --env-file <本地已有隔离凭据文件> --phase all --concurrency 2 --output artifacts/v3-human-reply-naturalness/review-fix-l2`。350 次调用含冻结旧基线、顺序对照、重复及探索温度；全部 DeepSeek，无请求错误和 fallback。首轮候选活动 7/10、主动 21/30、无关插入 1/22，不能因插入减少就接受销售退化。
- 据此保留推进前置门，把正向业务承接后的实际交付要求写清，再以同一 90 场景、相同评分和 0.15 复测：`python ai_paths/scripts/evaluate_v3_reply_naturalness.py --env-file <同上> --phase order --concurrency 2 --output artifacts/v3-human-reply-naturalness/review-fix-final-l2`。最终 90/90 可解析、78/90 综合、活动 6/10、主动 22/30、keep_open 误用 3/30、明确动作 11/12、无关插入 2/22、硬安全样本 6/6、内部泄漏 0；90 次 DeepSeek，零请求错误和 fallback。P50/P95 1956/2599ms 仅为单节点调用，不是接口耗时。
- 最终具体遗留：业务认可被当作关系承接、活动字段遗漏、首次付款请求改问人数而未发结构、普通关系回应仍夹带销售菜单。另有价格认可场景出现无证据保留/安排表述和门店未交付时询问日期，属于必须单独审核的事实/越级风险；六个硬安全样本通过不等于全部事实安全。禁止以该候选替换已发布版或宣称达到本轮门槛。
- 事实补齐回归解决真实渲染缺口，但原 L2 本已提供完整活动事实，因此不能将旧 L2 遗漏直接归因于 Router 丢主题。本轮未修改原 90 场景与评分来提高分数。
- 新盲审包：`artifacts/v3-human-reply-naturalness/review-fix-final-blind-review/blind_review_50.csv`，50 对完整文字/结构回复，含 30 个主动机会；沿用冻结旧基线与最新候选生成，映射独立存于 `blind_review_key.json`，人工评分全部留空。生成命令：`python artifacts/v3-human-reply-naturalness/review_bundle.py`（仅 ignored 辅助脚本）。
- L3 最终命令：`python ai_paths/scripts/evaluate_v3_naturalness_full_graph.py --env-file <同上> --output artifacts/v3-human-reply-naturalness/review-fix-final-l3 --concurrency 1`。完整生产 ASGI 处理链而非真实 TCP 监听；模型、事实动作、admission、临时持久化、HTTP 响应、重放与 finalization 均实际执行。已发门店场景补入虚构结构历史，未注入预生成 Reply；中间缺历史版本只保留作诊断。
- 最终 L3 结果：19/28 业务合同通过；21 主模型、5 定向修复、2 失败兜底；HTTP/持久重放/收尾 28/28，69 次模型调用仅 DeepSeek，无 GPT fallback，零消息派发和策略外发。完整 L3 明确失败；不能拿底层生命周期通过覆盖九个业务合同失败。
- 已发现的 L3 范围外阻碍：版本化本地 `s10_activity_intro` 混入 payment_collection，首次询价选择该素材会与“必须有之前活动证据才能发付款卡”冲突；本任务不修改素材配置、付款门或第三方目录来使评测转绿。其余结构交付/状态失败以最终报告为准；本地目录行为不能直接推断线上外部目录行为。
- 结论：三项 Review 的代码修订与评测设施已提交，但质量目标未达成；保留 active、不得合并或发布。原始产物保持 ignored，不写 current、不编辑母窗口索引、不触碰图片身份/防重任务。

## 2026-09-10 本次发布例外（优先于下方历史限制）

- 用户在获知“活动完整性 8/10、无关插入 3/22、完整 L3 未通过”后明确授权：“本次可以部署上线再测试一下”。本次允许集成干净 main 并发布，不将已失败的质量门槛改记为通过。
- change contract：仅发布现有 V3 Reply 自然度候选 5323c65a1fea792e6c6d277f340713150d8b226d；不追加业务代码修改、数据库迁移、模型温度或配置开关变更。
- 集成位置：独立干净副本 E:/ai_code/vscode_codex/worktrees/v3-naturalness-release；不触碰其他工作区的未提交文件。
- 现场生产基线及回滚目标：/opt/ai-paths/releases/ai-paths-unified-20260910-sop-race-1f9fc745，commit 1f9fc745c04932f9ca512464b36d3c6424fbdcfb；原 previous 为 sop-terminal-9ae26dcd。
- 验证：main 确定性回归、发布清单和包校验、三后端角色与管理页/回调/退役路由检查、服务器隔离模型复测。正式接口会重置 test_isolated，禁止用虚构客户直接请求正式回复产生生产数据。
- 风险：活动内容遗漏、不必要营销插入、模型图超时/降级及未完成业务盲审仍然存在；本次属于用户授权的风险例外上线，不代表完整质量验收通过。
- 回滚：服务启动或基础合同检查失败时，将两套 current 恢复上述现场基线并重启三个后端；保持原环境文件、数据库、前端版本及后台业务开关不变。
- 当前进度：已合入并推送 main，部署 clean main commit `dff19510b97d8725e5aa6197e3328425590f6867`，release `ai-paths-unified-20260910-naturalness-dff19510`。三个后端角色的健康身份和实际进程目录一致；前端保持原版。首次切换因未同步环境中的发布身份回滚，随后只更新四个发布元数据字段并成功重发；所有其他环境行均验证未变。
- 部署前精确 main 回归：865 passed，9 个既有弃用警告。服务器只读 schema head 为 `aics_schema_version=20260909_02`，未执行迁移。
- 部署后：14 项 HTTP/访问保护检查通过；正式回复与回调公网接口仍仅允许既定平台 IP，非白名单返回 403；内部空请求返回 400、回调 GET 返回 405，未提交有效虚构客户请求。管理页内部 200、外部认证 401，旧 V1/V2 路由维持 410。
- 服务器隔离完整模型图抽样 6/6 合同通过、6/6 主模型直接输出、6/6 持久重放一致；14 次 DeepSeek 调用、零 GPT 回退、零消息派发/策略外发。样本为 action-01/03/04/12、safety-01/05。仍未覆盖图内真实 HTTP 及 finalization，不标记完整 L3 通过。
- 部署后独立 L2：90/90 解析、79/90 综合、硬安全 6/6、明确动作 11/12、主动推进 27/30、keep_open 误用 0/30、活动完整性 7/10、无关插入 3/22、内部术语 0、请求错误 0；90 次调用均 deepseek-chat，无 fallback。效果问题 action-11 已回答效果，但动作标签 ask_missing_fact 不符合探针的预设；不能直接解释为没有回答。活动遗漏依然是实质内容问题。P50/P95 为 2273/2819ms，仅 Reply 隔离调用耗时，不是生产端到端耗时。
- 用户授权的“上线再测试”已完成；按授权保留线上版本，完整质量目标未完成，保留本活动任务记录遗留，禁止将上线等同于验收通过。
- 证据：服务器 `/opt/ai-paths/artifacts/naturalness-release-dff19510/`，本地 ignored `artifacts/release/`；旧候选工作区包含原评测证据，暂保留，不清理用户其他工作区和未跟踪文件。
- owner: Codex
- base_branch: `origin/main`
- base_sha: `6f5a23ce7cd0cd2bb91a1717434d8f3589c3b375`
- branch: `codex/v3-human-reply-naturalness`
- worktree: `E:\ai_code\vscode_codex\worktrees\v3-human-reply-naturalness`
- exclusive_scope: `ai_paths/app/prompts/reply_sales_prompt_v4.py`、`ai_paths/app/prompts/reply_synthesizer.py`、`ai_paths/app/policies/ai_sales_policy_v2.json`（仅 `normal_conversation`）、`docs/contracts/sales-strategy.md`、`docs/architecture/V3_MODEL_NODES_AND_PROMPTS.md`、本任务直接相关测试与隔离评测脚本、本任务文档、ignored `artifacts/v3-human-reply-naturalness/`
- production_verified_at: 未核验；本任务不涉及生产发布或线上状态判断
- production_releases: 未核验；禁止用文档快照推断当前生产
- data_model_send_authority: 允许隔离调用 DeepSeek；只使用脱敏或虚构输入；零真实客户发送、零生产写入、零 GPT fallback

## 目标

在不改变 V3 Reply 唯一销售决策权、不增加模型节点、不降低事实和交易安全的前提下，让短回应、关系承接、临时不可交流和重复暂缓可以自然短接；当前事实问题优先直接回答；只有时机自然时才衔接相邻销售机会。

用户最新校正：以语气自然、多样为主，尽量不改变有效回复内容；保持认真积极的销售表达，不能用放弃式收口代替成熟动作。该介绍活动时必须保留完整的相关价格、项目、条件与权益。

## 当前迭代结论（优先于下方历史证据）

- 状态：修订候选未放行。用户随后要求“提交部署”，已授权保存候选提交与准备发布；本修订提交以本条 Git 历史为准，上一检查点为 `df65ed802b24be75dd8fba0cb6c46ccee0b3fdc6`。活动完整性、无关插入和 L3 门槛仍失败，业务盲审未完成；依据发布清单“任一项失败，停止发布”，本轮仅提交候选，未合并、未部署。
- 发布准备现场核验：重新 fetch 后 `origin/main=6f5a23ce7cd0cd2bb91a1717434d8f3589c3b375`；SSH 只读核验 current 为 `ai-paths-unified-20260910-sop-race-1f9fc745`、previous 为 `ai-paths-unified-20260910-sop-terminal-9ae26dcd`，reply/control/worker/frontend 四个 unit 均 active/running 且 NRestarts=0。本次未切换 release、未重启服务；previous 与旧生产文档快照不同，后续发布不得沿用旧回滚点。
- 修正了“重复暂缓一律 keep_open”“客户短就短接”“长话术一律限一两句”的过度限制；补充新会话、需求/位置提交、效果/价格认可、卡点化解和成熟预约的正向动作要求，明确保留完整活动信息。
- 当前 Prompt 为 6,995 字符、73 行；SHA-256 `fa3885a8c0aea01bebbf4eac7e91a61c5193850c4a5336ca213e015a5705475b`。架构快照同步标为未放行候选。
- L1：最新全仓 `865 passed, 9 warnings`；warnings 均为既有依赖弃用提示。
- L2：原 60 场景加 30 个主动销售场景；最终同批 350 次调用全部为 `deepseek-chat`，零请求错误和 GPT 回退。评分修正后的权威汇总位于 ignored `sales-balance-final-rescored/`；复算无新增模型调用。
- 候选顺序：90/90 可解析，83/90 全项通过；主动销售 28/30、keep_open 误用 0/30、探针识别的通用放弃式收口 0；明确动作 12/12、硬安全 6/6、内部术语泄漏 0。活动完整性仅 8/10，无关销售插入 3/22，仍未通过任务门槛。无关插入统计现同时检查销售追问与淡斑等业务内容，不能沿用旧关键词口径的 0/22。
- 余下具体失败：认可效果后继续追问症状而未介绍活动；解卡后的活动说明遗漏提前预约条件；纯夸赞/祝福后插入销售；重复暂缓仍再次询问顾虑。禁止把主动性总分达标解释为内容保持已达标。
- 独立重复测试 `sales-balance-repeat/`：包含六类主动机会的 20 个代表场景各 3 次，共 60 次真实 DeepSeek 调用；schema 波动 1 个场景、动作波动 3 个场景、硬安全失败 0。默认 temperature 仍为 0.15。
- 真实模型图隔离对照 `full-graph-final-candidate/` 与 `full-graph-final-baseline/`：各 28 个场景，各 71 次模型调用，均只记录到 DeepSeek，无回退。候选 15 个、基线 16 个失败降级；按动作/状态/结构/持久回放综合检查为候选 8/28、基线 6/28；候选 13 个、基线 16 个场景记录超时。该批并发为每版本 4；环境/时限与合同失败混合，不能推断为纯 Prompt 差异。
- 上述模型图真实运行 Router、事实动作、Reply、admission、现有重试与临时 SQLite/响应序列化，未回放预生成 Reply；外部身份、门店及支付输入使用虚构适配器，外部 Follow Knowledge 目录未接入。两边 durable replay 28/28，消息派发和策略外发均为 0。已记录模型调用、各节点、修复用量耗时和 runtime/响应/回放耗时；没有启动真实 HTTP 传输，亦未在此脚本内执行 finalization，完整 L3 明确未通过。
- 新 50 条盲审包在 ignored `sales-balance-review/blind_review_50.csv`，含全部 30 个主动场景及 20 个约束场景，附对话、事实和完整结构消息；增加销售机会、合适推进、过于被动、无必要结束对话及活动完整性评分。答案键单独保存，业务评审尚未执行。
- 下一步仍是本任务内收敛内容完整性和过度/不足推进，并完善隔离图事实与生命周期验证；不以反复调同一组探针替代独立业务盲审，不修改其他模块、生产配置或超时。

## 非目标

- 不处理主动唤醒、第三方 SOP、生产发布或其他模块。
- 不修改 Router 决策权/schema、Reply admission、`next_sales_action` schema、表情渲染、fallback、生产 temperature 或生产配置。
- 不新增文案/情绪模型、风格状态、数据库迁移、发送能力或生产写入。
- 不合并或删除 `codex/v3-emoji-e2e-latency`；其提交 `0102ec498a87809f842c28148ef748a841a7b8f9` 仅作只读取证，不 cherry-pick。

## Change contract

- type: 回复质量、Prompt 和动态上下文治理
- scope: 最终 Reply Prompt、必要的上下文渲染顺序、`normal_conversation` 合同澄清、对应测试和隔离评测
- risk: 软拒绝被过度放松；明确行动请求未当轮完成；主线越级、交易事实或安全边界回归；Prompt 膨胀；实验变量混杂
- validation: L1 确定性合同；相同 DeepSeek、输入与 temperature 的 L2 对照；隔离 L3；销售主管至少 50 条盲审由母窗口/业务完成
- rollback: 仅回退本任务 Prompt、上下文、合同和测试提交；无数据库或不可逆数据变更

## 涉及模块与文件所有权

允许修改：

- `ai_paths/app/prompts/reply_sales_prompt_v4.py`
- `ai_paths/app/prompts/reply_synthesizer.py`
- `ai_paths/app/policies/ai_sales_policy_v2.json`，仅澄清 `normal_conversation`
- `docs/contracts/sales-strategy.md`
- `docs/architecture/V3_MODEL_NODES_AND_PROMPTS.md`
- 与本任务直接相关的测试及必要隔离评测脚本
- `docs/tasks/active/INDEX.md` 与本任务文档
- ignored `artifacts/v3-human-reply-naturalness/`

旧 `v3-emoji-e2e-latency` worktree 当前干净、已提交且被本任务包明确冻结为只读取证；若其恢复并发编辑上述文件，立即停止本任务修改并报告所有权冲突。

## 不可破坏合同

- 正常 V3 仍为一次 Router、必要事实工具、一次 Reply；不得新增模型节点。
- `next_sales_action` 仍必填，记录本轮实际落实动作；`keep_open` 是合法动作。
- 明确退订、医疗风险、严重客诉、退款、已付服务、价格、素材、门店、预约和付款边界零回归。
- `next_missing_stage` 只表示下一项机会与越级边界，不要求本轮机械执行。
- 明确价格、案例、地址、付款入口或预约请求在事实和权限齐全时仍须当轮完成。

## 已确认事实与证据

- 执行前重新 fetch 后最新远端为 `origin/main@6f5a23ce7cd0cd2bb91a1717434d8f3589c3b375`，与任务包规划值一致。
- 当前原窗口位于 dirty `codex/deepseek-reply-evaluation@531ddc7e`，未触碰其用户修改。
- 新 worktree 从干净 `origin/main` 创建；旧 `codex/v3-emoji-e2e-latency@0102ec49` 保留为只读取证。
- `main` worktree 只有未跟踪 `output/`，本任务不清理、不复用。
- 冻结基线 Reply Prompt：`6999` 字符、`62` 行、SHA-256 `3173b03054620b48dfd699a6af3d8ddfd51581e95fb1d8f7bcf0d3f03e18d642`。
- 冻结基线默认 Reply temperature：`0.15`（`Settings.v3_reply_temperature`）；本任务不修改该默认值。
- 冻结基线动态顺序：当前时间 → 输出上限 → 销售动作硬合同 → 完整聊天 → 结构事实 → 执行能力 → 策略/上一状态/逼单候选 → 协议/付款/已付 → 工具事实 → 必须遵守 → Router → 序列话术 → 权威事实 → 素材 → 结构消息 → 缺失权限 → 引用边界。

## 历史已完成（df65ed80 版本）

- 完整读取任务要求指定的宪法、索引、current、任务流程、销售策略、Reply admission 和 V3 模型/Prompt 全景。
- 核验分支、HEAD、dirty、最新 `origin/main`、所有 worktree 及可见 active task 范围。
- 创建并登记独立分支/worktree。
- 冻结 main Prompt、动态区块顺序和默认 Reply temperature，并把基线写入 ignored artifact。
- 建立 60 条脱敏/虚构 L2 矩阵：12 条短回应/关系承接、10 条临时不可交流、12 条首次/重复软拒绝、12 条明确动作、8 条 Router/培训稿污染、6 条硬安全；其中 20 条重复探针、28 条 L3 生命周期标记。
- 在不改变 schema、admission、fallback、模型节点和生产 temperature 的前提下，重构 Reply Prompt 与动态区块顺序，并仅澄清 `normal_conversation` 合同。
- 最终候选 Prompt 为 `6,983` 字符、`72` 行、SHA-256 `963fe12f35f2c0f47eaab5c3bf568f660e44832203d947f3d13b975f99b6164b`；逐字快照和动态顺序已同步到架构文档。
- 完成 L1、L2 和隔离生命周期尾部回放；最终证据来自零模型错误的 `final-l2-v5`，原始模型结果只保存在 ignored `artifacts/v3-human-reply-naturalness/`。

## 待办

- 用户追加校正：目标以语气自然、多样为主，保留有效销售内容和完整活动介绍；新增至少 30 条主动销售机会及内容完整性验收。上一版 L2 分数不能证明本修订通过，状态重新置为迭代中。
- 本轮 change contract：仅在既有独占范围内修正重复暂缓一刀切、短消息一刀切和一两句话术限制；积极信号下落实一个相邻动作。风险为过度推进或活动事实缺漏；验证保留原 60 条并新增 30 条机会、完整图 L3 与业务盲审；可回退至 `df65ed802b24be75dd8fba0cb6c46ccee0b3fdc6`。

- 销售主管对不少于 50 条、隐藏版本标识的客户可见回复做独立盲审；使用本轮 `sales-balance-review/blind_review_50.csv`，旧 `final-l2-v4` 审核包只作为历史证据。答案键单独存放，当前没有业务评分，不能由开发/模型自评替代。
- 完整 L3 仍未通过：新脚本已运行真实模型图，外部事实为虚构适配器；必须处理隔离事实/结构交付与超时问题，并补足 HTTP 传输与 finalization 验证。旧生命周期尾部回放不能替代这些检查。
- 盲审达标后才能把本候选标记为可合并；本任务不合并 `main`、不发布。

## 历史测试结果（df65ed80 版本，不代表当前修订通过）

- L1 targeted：`118 passed`。
- L1 全仓：`861 passed, 9 warnings`；warning 为既有 Authlib 与 SQLAlchemy deprecation。
- L2 最终对照：`artifacts/v3-human-reply-naturalness/final-l2-v5/`（ignored）。共 260 次调用，固定 `deepseek-chat`，fallback index 仅 `0`，全部零模型错误。
- L2 主候选（候选 Prompt + 候选顺序）：60/60 可解析且通过，硬安全 6/6，明确动作 12/12，无关销售插入 0/22，内部术语泄漏 0，P50/P95 `1865/2418 ms`。
- L2 Prompt 对照（相同基线顺序）：无关销售插入由基线 `14/22` 降至候选 `2/22`，相对下降 `85.71%`；候选硬安全 6/6、明确动作 12/12、零模型错误。
- L2 顺序对照：同一候选 Prompt 下，无关销售插入由 `2/22` 降至 `0/22`；候选顺序 60/60 通过。
- L2 重复探针：20 个场景各 3 次，共 60 代；硬安全失败 0，schema 波动 2 个场景、动作波动 2 个场景，无重复开场。temperature 0.30 和 0.45 均为 20/20；两者只在代表性子集通过，未证明优于完整 60 场景的默认 0.15，因此生产默认仍为 `0.15`。
- L3 生命周期尾部：`artifacts/v3-human-reply-naturalness/final-l3-replay-v5/`（ignored）。28/28 完成 ChatRuntime、临时 SQLite、HTTP response、durable replay 和 finalization；28/28 durable payload 一致，finalization 28/28，真实消息派发 0，策略外发 0。来源 Reply 均为 L2 `deepseek-chat`；该步新模型调用 0、Router/工具调用 0。
- 销售主管盲审：50 条随机 A/B 审核包已生成，类别分布为短回应 9、临时不可交流 10、软拒绝 8、明确动作 10、Router 污染 7、硬安全 6；尚未由销售主管评分。由于完整 Router/工具 L3 与业务盲审都未完成，候选状态是“L1/L2 与生命周期尾部通过、完整门禁未放行”，不能宣称最终通过或可发布。

## 发布与回滚

- final_main_commit: 不适用；本任务禁止合并 `main`
- deployed: no

## 待沉淀的长期结论

- `keep_open/normal_conversation` 对关系承接、短回应、临时不可交流和重复暂缓的稳定适用边界。
- `next_missing_stage` 作为机会/越级边界而非每轮强制动作的 Prompt 表达。
