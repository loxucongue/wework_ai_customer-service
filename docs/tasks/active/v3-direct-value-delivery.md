# V3 可直接交付价值与话术媒体联动

- 类型：reply_quality_and_delivery
- Owner：Codex
- 分支：`codex/v3-direct-value-delivery`
- Base SHA：`ae8a8e68f84896c3a55b609b14f2ddc9742ed752`
- 生产基线：执行前现场核验为 `main@8f911bea69268180349fcc992bca7e86f7bee974`

## 目标

- 正常销售场景中，活动价、真实效果证据等本轮已经具备且直接相关的价值应直接交付，不先问“要不要发”。
- 修复话术已携带媒体、Reply 已采用话术文字，但媒体没有进入客户可见输出的问题。
- 保持 V3 Reply 为唯一销售语义决策者；代码只负责候选事实、媒体权限、结构一致性、去重和安全边界。
- 使用 DeepSeek 复现请求 `71dad375-d65b-42e5-a713-1aada65e8811`，并覆盖效果、信任、距离、退订、投诉和重复素材场景。
- 验证通过后合入干净 `main` 并部署同一 SHA 到 control/reply/worker。

## 非目标

- 不新增模型调用、数据库表或公共接口。
- 不改变第三方素材接口和标签内容。
- 不在明确退订、投诉、高风险或无关场景强制发送营销素材。
- 不启用延时自动发送。

## 独占范围

- `ai_paths/app/prompts/reply_synthesizer.py`
- `ai_paths/app/services/v3_semantic_router_service.py`
- `ai_paths/app/graph/nodes/semantic_evidence.py`
- `ai_paths/app/graph/nodes/material_selection.py`
- `ai_paths/app/graph/nodes/action_module_outputs.py`
- `ai_paths/app/graph/nodes/reply_nodes.py`
- `ai_paths/app/graph/nodes/reply_generation.py`
- `ai_paths/app/graph/nodes/reply_quality.py`
- 必要的 Reply 素材交付测试文件
- `docs/contracts/sales-strategy.md`
- `docs/standards/V3_REPLY_EVALUATION.md`
- 本任务文档及历史摘要

## 不可破坏合同

- 正常销售语义由模型判断，不新增 Python 关键词销售分支。
- 图片、视频只能来自本轮真实候选或权威工具事实；不能由模型编造 URL。
- 客户边界、明确退订、投诉、健康风险、人工接管和素材去重继续生效。
- 测试不得发送真实客户消息、写生产 BI/客户记忆/outbox/dispatch 或执行交易动作。

## 风险与回滚

- 风险：过度发送素材、重复发送旧素材、一次发送图片和视频过多、Prompt 冲突造成模型仍只说不发。
- 控制：只提升与当前主任务直接相关的真实媒体；保留历史去重和安全阻断；专项测试验证媒体数量与顺序。
- 代码回滚：恢复上一干净 `main` 提交。
- 生产回滚：恢复上线前记录的 release；无数据库结构需要回滚。

## 验证

- [ ] Prompt 与素材候选确定性合同测试
- [ ] V3 Reply/安全/交易/门店全量确定性回归
- [ ] DeepSeek 指定日志隔离复现
- [ ] DeepSeek 效果、距离、信任及安全边界专项矩阵
- [ ] 零客户发送、零生产写入审计
- [ ] clean main 合并、发布及健康检查

## 已确认根因与实现决策

- 生产日志 `71dad375-d65b-42e5-a713-1aada65e8811` 已召回 6 条距离话术；Reply 采用序列 45、步骤 252 和话术 187。
- 话术 187 的输入同时带真实效果图与视频，但媒体被标成 `sales_reference` 后从“可用真实素材”区域过滤；Prompt 又允许只记 `script_id` 而不选 `selected_content_ids`，最终只发文字并询问是否了解效果。
- 修复不让代码自动选择销售素材：将真实话术媒体明确展示为可直接交付素材，Prompt 要求直接价值不经过许可式追问，Reply 仍输出真实选择 ID，代码只负责原样追加、URL 去重和安全边界。
- 文本话术、已全部发送的图片组不再进入 `allowed_selected_content_ids`；若同组仍有未发送视频，则仅保留剩余媒体为可选结构。
- 2026-09-06 L1 第一轮：相关 Prompt、门店连续性、策略合同与 Reply 重试测试 `68 passed`。
- 2026-09-07 完整确定性回归：`322 passed`；Prompt 预算 8,240 字符，未增加模型调用、数据库表或公共接口。
- 真实矩阵发现旧平台记录只在客户可见历史中保留了门店卡名称/地址、缺少可靠 `store_address_sent` 事件时，停车详情仍可能重发同一门店卡；本任务补充基于助手已交付结构/门店名+地址精确一致性的幂等证据，不判断销售语义。
- DeepSeek 对同一距离异议存在“已采用带图话术、但漏填 `selected_content_ids`”的随机性；收紧为 Reply 选择真实 `script_id` 后，代码只补齐该话术自身唯一、安全、未发送的配套媒体，不另选话术或业务主题。
