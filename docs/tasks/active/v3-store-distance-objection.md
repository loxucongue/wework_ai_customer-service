# v3-store-distance-objection

- status: active
- owner: /root
- base_branch: main
- base_sha: 3403e34fcc057e402c55bb195d39cbc9cb03cbb9
- production_verified_at: 2026-09-06T19:05+08:00
- production_releases: control/reply/worker=ai-paths-unified-20260906-154524-007bf2c8@007bf2c88c1405bc74c4e13320b0e27f667673c4

## 目标

- 保留最近一次真实门店推荐依据，与最近一次门店卡交付事实分开使用。
- 当前城市未变化且门店推荐已完成时，不再追问更细地点、不重复发送门店卡。
- 距离卡点存在直接相关且无冲突的话术时，由 V3 Reply 实际采用并记录真实 ID。
- 完成 DeepSeek 隔离验证、合入 main 并发布同一干净 SHA。

## 非目标

- 不改变 V3 公共接口，不新增数据库表或模型调用。
- 不把行政范围匹配伪装成真实公里数排序。
- 不用 Python 关键词替代客户语义和销售判断。

## Change contract

- type: V3 回复质量与跨轮事实连续性修复
- scope: 门店历史摘要、Router/Reply 上下文、卡点话术采用合同、日志只读展示与回归测试
- risk: 同城新位置误触发重复查询；候选话术含冲突事实时被强制采用；历史门店事件兼容
- validation: 确定性合同测试、指定三轮生命周期复现、DeepSeek 专项与真实身份隔离回归、前端构建、发布检查
- rollback: 恢复发布前统一 release；无数据库回滚

## 涉及模块与文件所有权

- `ai_paths/app/chat_runtime.py`
- `ai_paths/app/graph/nodes/sent_message_summary.py`
- `ai_paths/app/graph/nodes/reply_context.py`
- `ai_paths/app/prompts/reply_synthesizer.py`
- `ai_paths/app/services/v3_semantic_router_service.py`
- `ai_paths/app/services/run_observability.py`
- `projects/src/components/logs/run-log-viewer.tsx`
- `projects/src/components/logs/run-log-model.ts`
- 对应门店、策略、日志测试与本任务文档

## 不可破坏合同

- V3 Reply 仍是唯一销售语义决策者；代码只管理事实、工具、稳定 ID 与安全边界。
- 门店、距离、预约、支付事实不得虚构；同一门店卡默认不得重复发送。
- 卡点话术只在直接相关且不存在事实冲突时要求采用。
- 新城市可重新匹配；同一城市更细位置不得承诺能找到更近门店。
- 测试不发送真实客户消息、不写生产业务数据。

## 已确认事实与证据

- 生产当前为 clean `007bf2c8`，三个服务角色健康。
- 问题样本已召回 3 条距离序列、6 条距离话术，最终采用序列但未采用话术。
- 原始门店推荐事件保存了行政范围、完整候选和最终推荐依据；后续门店详情交付覆盖了“最近推荐依据”的展示语义。
- 当前 main 的回复状态压缩只展示最近门店 ID，没有展示查询完成和继续细化是否有价值。

## 已完成

- 完成线上日志、上一轮门店匹配事件与当前 main 实现取证。
- 建立独立分支和 worktree，登记文件所有权。
- 门店卡最近交付与最近有效区域推荐已拆分；详情查询不再覆盖区域匹配依据。
- Router、Reply 和日志均可读取查询范围、是否完成、是否允许继续细化、排序方法和推荐门店。
- 同城距离卡点不再触发无效门店查询或细地址追问；新城市仍可正常重新匹配。
- 活跃卡点存在直接相关且安全的话术时，Reply 必须记录真实序列与话术 ID，并允许只采用长话术中的安全句。
- 同一门店卡逐卡幂等；城市列表混有已送达卡时改用完整文字清单，避免重复卡和错误门店总数。
- 日志页增加门店推荐终态展示，并保持候选、采用和实际发送分离。

## 待办

- 完成最终文档提交、合入 main、生产发布与现场复核。

## 测试结果

- 全仓后端：311 passed。
- 前端：TypeScript、ESLint、Next.js 生产构建通过。
- 指定日志 `5a2d6e1d-0db6-44eb-9bcd-b0ad937c69dd` 使用真实身份、完整生命周期和全 DeepSeek 隔离复现：采用距离序列 11、话术 225，无细地址追问、无门店工具重查、无重复卡、无无依据远近断言；AI 初评通过，10.96 秒，生产发送和关键表写入均为 0。
- 120 条真实身份完整生命周期 DeepSeek 初评：运行错误 0、策略核心覆盖 100%、AI 初评 99.2%、真人表达 100%、安全/无依据事实 0、P50/P95 8.74/13.93 秒、生产发送和关键表写入 0；唯一硬失败为历史门店卡重复。
- 上述唯一硬失败已增加逐卡幂等后原样复现通过：重复卡 0、门店总数保持 4 家、AI 初评通过。该补丁只改变结构化交付幂等，不增加模型调用。

## 发布与回滚

- 发布前保存当前 release `ai-paths-unified-20260906-154524-007bf2c8`。
- 无 schema 迁移；失败时切回该 release 并恢复对应服务。

## 待沉淀的长期结论

- 门店卡“最近交付”与门店“最近有效推荐依据”必须分别建模。
- 门店详情查询不得覆盖此前位置匹配的可解释性证据。
