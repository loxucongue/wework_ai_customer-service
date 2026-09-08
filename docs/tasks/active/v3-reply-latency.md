# V3 Reply 关键路径性能优化

- status: active
- owner: Codex
- branch: `codex/v3-reply-latency`
- base_sha: `f6ef53cbc9acdb5d45a00d261937669eecf57dd7`
- production_baseline_at_start: `05723ce3ab88a629f659e6f0caf51bfac8c7f6eb`
- production_baseline_before_release: `cb33fc65fe18af4204cb55a442be31a7a42367ff`

## 目标

- 补齐从 HTTP 进入到返回的分阶段耗时。
- 减少入口和上下文阶段的数据库往返与事件循环阻塞。
- 将共享知识目录从请求冷刷新改为可审计的单飞快照读取。
- 在不增加模型调用、不降低销售效果和安全边界的前提下降低 V3 P50/P95。

## 非目标

- 不修改 V3 销售语义决策权。
- 不新增模型节点、消息发送能力或数据库表。
- 不改变客户身份隔离、人工接管、退订和交易事实边界。

## 独占范围

- `ai_paths/app/chat_runtime.py`
- `ai_paths/app/graph/nodes/layer_nodes.py`
- `ai_paths/app/graph/nodes/authoritative_context.py`
- 与 V3 入口事务、知识快照和性能观测直接相关的 service/storage 文件
- 对应测试、`docs/current/`、`docs/architecture/` 与发布记录

## 不可破坏合同

- V3 是唯一产品回复入口；Router 与 Reply 模型节点数量不增加。
- 人工接管状态未知时不得继续自动回复。
- 权威订单、门店、支付和预约事实不得因超时被伪造成否定结果。
- 客户可见回复、请求幂等和可恢复收尾任务必须在返回前可靠持久化。

## 验证

- Windows 确定性与集成回归：`616 passed`；Python 编译和 `git diff --check` 通过。
- Ruff 全树仍有 `49` 个历史问题；`origin/main` 与候选数量相同，本任务未自动改写无关模块。
- Linux 候选目录 Python 编译通过；服务器运行环境未安装 pytest，因此没有重复宣称 Linux 全量测试。
- 首轮 120 条 DeepSeek 真实身份 L3 隔离评测：运行异常 0、安全失败 0、生产写入尝试 0，生命周期 P50/P95 `9509/13159ms`；但 3 秒状态门禁产生 4 条兜底、25 秒模型预算产生 1 条超时，不满足可靠性要求，已否决该预算。
- 第二轮 120 条（状态门禁 8 秒、普通/复杂图 35/45 秒）：AI 初评 `95.0%`、真人表达 `96.2%`、策略核心覆盖 `98.8%`、生命周期 P50/P95 `8488/12434ms`，模型超时 0、安全失败 0、生产写入尝试 0；37 条具备候选的样本采用序列 26、话术 33。仍有 1 条状态门禁超时，因此最终恢复该外部依赖原有 12 秒失败关闭上限，避免用短超时制造兜底。
- 最终预算为状态门禁 12 秒、普通/复杂图 35/45 秒。120 条运行层复核：运行异常 0、状态门禁超时 0、模型超时 0、策略核心覆盖 `100%`、生产写入尝试 0；仅保留 1 条既有 `store_availability_fact_required` 事实门失败。生命周期 P50/P95 `9422/13964ms`，模型图 P50/P95 `8082/11613ms`，图后临时库持久化与返回 P50/P95 `62/170ms`。
- L4 在发布后只读观察真实 V3 请求，分别核验完整 HTTP、Reply 图和响应后审计补写，确认三个服务角色 SHA 一致。

## 回滚

- 代码回滚到发布前现场记录的 production release `cb33fc65fe18af4204cb55a442be31a7a42367ff`。
- 若仅知识预热异常，关闭新快照刷新并恢复上一版同步读取；不得绕过事实和人工接管门禁。
