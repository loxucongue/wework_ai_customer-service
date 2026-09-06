# v3-lifecycle-eval-hardening

- status: active
- owner: Codex 主窗口
- base_branch: main
- base_sha: `39706291949b75dfdea764b0b4d3947aa095c436`
- production_verified_at: `2026-09-06T16:04:08+08:00`（沿用共享文档快照；涉及发布时重新核验）
- production_releases: `control/reply/worker=ai-paths-unified-20260906-154524-007bf2c8@007bf2c8`

## 目标

- 修复 V3 评测只覆盖模型图、忽略结构化已发送消息和持久化尾部的问题。
- 在不发送、不执行交易、不写生产库的条件下，模拟完整 `ChatRuntime` 生命周期。
- 为重复门店卡、预约目标缺失、明确退订误推进和生产写入建立确定性硬失败。
- 使用 DeepSeek 重新测试指定门店状态组合和扩大后的真实全场景样本。

## 非目标

- 不修改生产 Reply、门店匹配、销售策略或发送逻辑，除非新评测发现可稳定复现的新运行时缺陷。
- 不调用生产发送、付款、建单、预约、策略回写或生产数据库写接口。
- 不把真实客户身份、聊天原文、原始模型输入输出或详细测试报告写入 Git/docs。

## Change contract

- type: evaluation tooling / test contract
- scope: 评测脚本、临时生命周期存储、结构化消息评审输入、确定性断言、分段与端到端性能指标
- risk: 隔离不严导致生产副作用；模拟环境与生产不等价；硬断言过严误伤合理回复；DeepSeek 配额和延迟
- validation: 确定性单测 → 无模型生命周期 smoke → DeepSeek 指定日志复现 → 扩大真实样本；每批反查生产写入为零
- rollback: 评测工具不进入生产运行链；可直接 revert 本任务提交

## 涉及模块与文件所有权

- 独占：`ai_paths/scripts/evaluate_v3_full_chain_deepseek.py`
- 独占：`ai_paths/scripts/v3_lifecycle_eval/`
- 独占：`ai_paths/app/services/store_destination_resolver.py`
- 独占：`tests/test_v3_deepseek_eval_protocol.py`
- 独占：`tests/test_v3_lifecycle_eval.py`
- 独占：`tests/test_store_matching_tool_contract.py`
- 独占：`docs/standards/V3_REPLY_EVALUATION.md`
- 独占：`docs/current/DEVELOPMENT_STATUS.md`
- 独占：本任务文档、活跃任务索引和历史摘要

## 不可破坏合同

- 所有文本模型节点和 AI 评审必须使用 DeepSeek；发现 GPT 调用即停止该批次。
- 发送、交易、生产 BI、dispatch、outbox 和 Shadow 计划写入必须为零；发现写入尝试立即停止整批。
- Repository 使用一次性临时存储，但必须执行等价持久化和接口响应组装。
- 评审必须读取客户可见的文本、门店卡、付款卡和素材等结构化消息。
- AI judge 只能初评；重复已送达结构消息、明确退订误推进和关键预约目标缺失由确定性断言判失败。

## 已确认事实与证据

- 旧评测构建 graph 时使用 `memory_store=None`。
- 旧 `reply_text` 只拼接 `type=text`，结构化门店卡不会进入评审输入。
- 旧计时只包围 `graph.ainvoke(state)`，不含 ChatRuntime 持久化和接口响应组装。
- 旧总通过率直接采用 AI judge 的 `passed`，没有把低推进分或重复结构消息转为硬失败。

## 已完成

- 从最新 `origin/main` 创建独立分支和 worktree，并登记独占文件范围。
- 新增完整客户可见消息归一化、结构化消息可读渲染和历史交付事件重建。
- 评测改用一次性 SQLite + `ChatRuntime`，执行 run、message、memory、BI 和接口响应组装后销毁。
- 阻断 7 个外部交易写方法；生产源只允许固定只读查询，并支持测试后反查生产落库。
- 新增重复门店卡、预约目标缺失和明确退订误推进硬断言；硬失败可覆盖 AI 误通过。
- 已通过 13 条评测协议测试和 155 条相关回归测试。
- 定向复现确认：目的地解析模型漏掉停车详情后，回退合同把请求错误降成 `match_location + detail_kind=none`，导致已发门店卡被再次物化，且 Reply 未收到预约收口合同。

## 待办

- 在服务器用 `caaca684-1429-4a6e-b0a4-e1c4c612022b` 做 DeepSeek 定向复现。
- 执行门店状态组合和扩大后的真实样本测试，输出 ignored artifacts 报告。
- 根据新证据决定是否需要修改生产运行链；更新共享进度并完成任务收尾。

## 发布与回滚

- 评测工具不部署为生产服务；如发现生产运行时缺陷，另按发布合同决定代码修复和部署。
