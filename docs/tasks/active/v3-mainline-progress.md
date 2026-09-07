# v3-mainline-progress

- status: active
- owner: Codex
- base_branch: main
- base_sha: `053a9afd452b24c7021cf17d430852a8276cd6c2`
- production_verified_at: `2026-09-07T20:48:58+08:00`
- production_releases: `control/reply/worker=ai-paths-unified-20260907-201840-44fcd568@44fcd5688c84185cfea83bab5f979e9b3e873006; frontend=frontend-20260907-201840-44fcd568`

## 目标

无活动卡点且交易未终态时，V3 在回答当前问题或完成门店交付后继续一个相关、可执行的主线成交动作；模型声明 `action=ask` 时客户可见回复必须真正包含一个问题。

## 非目标

- 不用 Python 关键词判断客户意图、卡点或业务阶段。
- 不改变退订、人工接管、健康风险、投诉退款、已付与预约终态安全边界。
- 不在一轮同时推进留名额、预约金、登记和到店时间多个动作。
- 不提交指定客户的原始会话或模型输出。

## Change contract

- type: 回复质量修复。
- scope: Semantic Router 输出合同、Reply 上下文、结构一致性校验、脱敏回归测试与合同文档。
- risk: 推进过强、事实主题越权、无卡点时误进入付款或在安全场景继续营销。
- validation: 确定性合同测试、指定场景脱敏重放、DeepSeek 单节点效果测试、全仓测试和部署后隔离 HTTP 验证。
- rollback: 回滚到生产 `44fcd5688c84185cfea83bab5f979e9b3e873006` 对应 release。

## 涉及模块与文件所有权

- `ai_paths/app/prompts/v3_semantic_router.py`
- `ai_paths/app/prompts/reply_synthesizer.py`
- `ai_paths/app/graph/nodes/reply_nodes.py`
- 对应测试与 `docs/contracts/sales-strategy.md`。

## 不可破坏合同

- Reply 仍是唯一销售语义决策点；Router 只提供候选证据。
- 每轮最多一个主要销售动作。
- 卡点未解决时先解卡；明确退订、暂停营销和终态不推进。
- 付款卡、门店卡、预约和订单声明继续服从权威事实与结构校验。

## 已确认事实与证据

- 指定日志无模型、工具或安全门异常；`reply_source=main_model`。
- Router 将客户提交具体门店只概括为地址查询，没有保留继续交易信号。
- Reply 输出结构声明 `action=ask`、`posture=advance`、目标为推进预约，但客户可见消息没有问题。
- 当前一致性校验在无退订、暂停营销或活动卡点时提前返回，未检查该结构自相矛盾。
- 另一个窗口提交 `c3ac96cd` 仅增加生产内存治理待办，主分支已有等价记录，不包含本修复。

## 已完成

- 现场核验生产基线、指定日志和当前代码路径。
- Router 增加只读的 `information_submission` / `transaction_progress` 承接信号，Reply 仍是唯一销售决策点。
- 已确认具体门店且无卡点、安全暂停或终态时，门店事实交付后要求一个到店主线问题。
- 正常销售轮次校验客户可见问题数量；`action=ask` 不得没有问题，多问题不得绕过 action 字段。
- 定向修复同时保留门店交付、问题下限和单动作上限；安全场景仍优先执行原有退订/暂停冲突校验。

## 待办

- 评审、合并 main、部署并完成发布后验证。

## 测试结果

- L1：`python -m pytest -q`，435 passed；`ruff check` 通过。
- Prompt 预算：Router 8174 字符，Reply 8733 字符，均低于既有 8200/9000 上限。
- L2：在生产服务器以生产进程只读环境和指定 request ID 做隔离重放；临时 SQLite、发送/支付/建单写接口阻断，生产写入 0。
- L2 最终结果：`single_targeted_repair_model`，交付真实门店卡并只追加一个到店问题；`hard_failure_codes=[]`，模型图 17.680 秒，完整隔离生命周期 17.888 秒。
- 临时评测代码、原始输入输出和服务器 `/tmp` 产物已清理，未进入 Git。

## 发布与回滚

- 当前未发布；即时回滚目标为 `ai-paths-unified-20260907-201840-44fcd568`。

## 待沉淀的长期结论

- `closing_decision=none` 仅表示不进入跟进序列，不等于停止正常销售主线。
- Router 应同时保留当前事实需求与客户提交信息/继续交易信号，Reply 决定本轮唯一下一步。
