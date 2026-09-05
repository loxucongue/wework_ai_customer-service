# v3-deepseek-human-reply

- status: active
- owner: Codex
- base_branch: main
- base_sha: 5c3de697afbc90386845aed7dc642b307e9c52c4
- production_verified_at: 2026-09-05 Asia/Shanghai（只读初核，发布前重核）
- production_releases: ai-paths-v3/control/workers=`/opt/ai-paths/releases/ai-paths-unified-20260905-5486e82d`@`5486e82d`（发布前以现场为准）

## 目标

- V3 最终 Reply、完整重试和定向修复只使用 DeepSeek，Router 继续使用 DeepSeek Flash。
- 修复连续消息工单化、旧话题误续接、普通询价错召回、回复重复、内部审计腔、无关门店预加载与策略回写告警。
- 通过确定性测试和 DeepSeek 隔离效果门槛后，合入 main 并全量发布。

## 非目标

- 不更换视觉模型，不启用延时自动触达，不新增公共接口或数据库迁移。
- 不新增 Python 关键词销售判断，不建立第二套销售决策服务。
- 不改动旧 dirty 工作区及暂停中的其他功能分支。

## Change contract

- type: 回复质量、模型配置、运行链性能与 SOP 回写缺陷修复。
- scope: Reply 模型选择；消息挤占输入与 12 条可见历史；Reply/Router Prompt；门店索引按需读取；SOP 客户回复关联查询；相关测试、合同与发布记录。
- risk: DeepSeek 单供应商降低故障接管能力；历史压缩可能漏掉旧上下文；强销售表达可能误推进；SOP 任务误关联可能跨客户回写。
- validation: 静态与确定性回归；两条问题日志复现；40 条 DeepSeek 专项矩阵；120 条真实身份全链只读隔离评测；生产发布后链路核验。
- rollback: 先恢复 Reply 模型环境配置并重启；运行时或语义异常则回滚到发布前统一 release。延时逼单始终保持 Shadow。

## 涉及模块与文件所有权

- `ai_paths/app/config.py`、`services/model_selection.py`
- `services/platform_reply_coordinator.py`、Reply 历史/上下文相关模块
- `prompts/reply_synthesizer.py`、`prompts/v3_semantic_router.py` 及 Router 服务
- `graph/nodes/layer_nodes.py` 及门店按需读取相关模块
- `services/storage/sop_event_repository.py`、`service_rule_data_service.py`
- 上述变更对应的最小测试、销售策略/意图路由/SOP 合同、任务索引与历史摘要

## 不可破坏合同

- V3 Reply 仍是唯一销售语义决策者；代码只负责事实、结构、幂等与安全。
- 明确退订、投诉/高置信愤怒、健康风险、人工接管与交易终态不得误推进。
- 客户状态和 SOP 关联必须严格按 `corp_id + wechat + external_userid/customer_id` 隔离。
- 延时逼单继续 Shadow；测试不发送、不写生产数据库、BI、dispatch、outbox 或 Shadow 计划。
- V1/V2 回复入口不得恢复，control 与 worker 职责不得破坏。

## 已确认事实与证据

- 当前 clean main 与 origin/main 均为 `5c3de697afbc90386845aed7dc642b307e9c52c4`。
- 生产初核仍指向统一 release `5486e82d`；最终 Reply 当前存在 GPT 主模型/竞速模型，Router 为 DeepSeek Flash。
- 两条指定日志分别暴露旧话题菜单式续接，以及连续询价被内部合并说明污染、重复回答和错召回隐形消费。
- 客户回复策略回写调用了 repository 中尚未实现的方法，产生不影响回复但持续告警的 `AttributeError`。

## 已完成

- 从最新 origin/main 创建独立分支和 worktree并登记独占范围。
- 阅读项目宪法、文档索引、运行边界、销售策略、意图情绪、SOP 与发布检查合同。
- Reply、完整重试和定向修复已统一为 `deepseek-chat`，Reply tier 不再继承 GPT emergency fallback；Router 保持 `deepseek-v4-flash`。
- 连续消息正文去除内部合并说明，客户可见历史收敛为最近 12 条并排除未交付/覆盖/内部事件；门店目录改为 Router 判定需要后再加载。
- Reply Prompt 完成人话表达、当前话题优先、询价分类、首次询价预约金、广告案例事实和拜访/预约边界治理；未新增关键词销售分支或额外模型调用。
- SOP repository 已实现客户回复关联查询，严格限定同一销售接触边界、回复前最近真实已发送平台任务并保持幂等。
- V3 同城多门店事实统一最多 3 个 `delivery_store_ids`，完整候选继续用于审计，解决 5 个候选与 3 张卡校验上限互相冲突导致空回复的问题。

## 待办

- 完成最后一轮 40 条 DeepSeek 真实身份隔离复测，确认所有发送和生产写入为零。
- 验收通过后合入 main、发布同一 clean SHA、核验生产并记录回滚点。

## 测试结果

- 全量确定性回归：`186 passed`。
- 前一候选 120 条真实身份只读隔离评测：117 条形成客户回复，策略结构覆盖 94.2%，AI 初评通过 96.7%，真人表达 95.8%，旧话题误续接 0；P50 7731ms、P95 13899ms。原 4 条失败中 3 条为同一多门店合同冲突，1 条为具体广告案例无事实误确认，本任务随后按根因修复。
- 根因修复后的两条真实身份定向复测：西安汉城路返回 3 张真实门店卡；具体抖音抬头纹广告明确无法核实且不套用淡斑活动。两条均为 `main_model`，模型轨迹仅含 `deepseek-v4-flash` 与 `deepseek-chat`，无 warning，耗时分别 12868ms、9264ms。
- 根因修复后的 40 条真实身份回归：运行异常 0，有效客户回复 39/40，AI 初评与真人表达均 97.5%，旧话题误续接、安全失败、无依据事实均为 0；策略结构覆盖 90%，P50/P95 为 9054/15813ms，只观察到 `deepseek-v4-flash` 与 `deepseek-chat`，生产写入尝试 0。唯一空回复样本“有的店是骗子”随后追加短投诉澄清边界，定向复测改为一句承接加一个必要追问，无门店查询或营销推进；最终确定性回归为 `187 passed`。
- 所有模型测试直接执行隔离 V3 主图，内存/临时 SQLite 承载状态，发送与平台写客户端阻断；报告和原文只留 ignored artifacts。

## 发布与回滚

- 待补现场事实、新 release、健康检查和回滚 release。

## 待沉淀的长期结论

- Reply tier 的供应商隔离规则、连续消息模型输入合同、客户可见历史预算、首次询价预约金边界和 SOP 客户回复关联规则。
