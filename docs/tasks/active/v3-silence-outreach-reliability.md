# V3 沉默客户唤醒可靠性修复

- 类型：production_bugfix
- Owner：Codex
- 分支：`codex/v3-silence-outreach-reliability`
- Base SHA：`de2f6cf4eb5a9c1b4ad994c92e5fc69fe74e0ebe`
- 生产基线：`ai-paths-unified-20260907-003744-5ad40a66`

## 目标

- 沉默客户唤醒全部使用 DeepSeek，不继承不可用的 GPT worker tier。
- 运行口径统一为不限制加微日期、销售/AI 最近回复后沉默 1 分钟进入候选。
- 修复模型节点失败后不重试、候选时间落后和扫描缺少运行状态的问题。
- 使用指定真实客户身份做只读/无发送验证，验收后合入 main 并上线。

## 非目标

- 不放宽人工接管、客户删除、明确退订、健康风险、订单终态和客户已回复门禁。
- 不补发启用水位前历史沉默，不手工给指定客户发送测试消息。
- 不新增数据库表或公共回复接口。

## 独占范围

- `ai_paths/app/config.py`
- `ai_paths/app/runtime_services.py`
- `ai_paths/app/main.py`
- `ai_paths/app/workers/supervisor.py`
- `ai_paths/app/services/outreach/first_day.py`
- `ai_paths/app/services/outreach/planning.py`
- `ai_paths/app/services/outreach_first_day_prompts.py`
- `ai_paths/app/services/storage/outreach_repository.py`
- 相关确定性/模型隔离测试
- 沉默唤醒合同、生产状态、已知问题和任务索引

## 不可破坏合同

- 客户边界仍为 `corp_id + wechat + external_userid/customer_id`。
- 计划前和发送前必须由平台明确确认 AI 模式；未知失败关闭。
- 客户新回复、人工接管、关系失效或订单终态必须取消/阻断发送。
- 测试不得触发真实客户发送或生产业务写入。

## 验证

- [x] 模型配置、Prompt、失败重试、候选时间和健康状态单元测试：专项 15 条通过
- [x] Outreach/SOP/运行角色回归：相关 36 条、全仓 332 条通过
- [x] DeepSeek 指定真实身份隔离规划：真实只读上下文生成计划成功，实际模型 `deepseek-chat`，发送调用 0；数据仅写入一次性 SQLite 并销毁
- [ ] clean main 合并、生产发布、服务与候选状态核验

## 风险与回滚

- 风险：修复后恢复此前无法生成的沉默计划，增加真实主动触达量。
- 控制：保留启用水位、每日频次、会话指纹和发送前二次事实门禁；发布后观察首批运行。
- 回滚：恢复 `ai-paths-unified-20260907-003744-5ad40a66`；本次无数据库迁移。
