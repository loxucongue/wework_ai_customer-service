# AI 回复策略接入契约

## 当前实现

- V3 运行时一次读取完整的 `ai_sales_policy_v1` 配置快照，不分别请求逼单、路由、意图和情绪四个接口。
- 当前数据源是 `ai_paths/app/policies/ai_sales_policy_v1.json`。
- `runtime_mode=active` 时，实时会话中的策略会进入当前 V3 Reply，不增加模型调用。
- `closing.silent_tasks_mode=shadow` 时，延时逼单只允许分析和审计，不创建真实发送任务。
- 配置读取失败时优先使用进程内最近一次校验通过的快照；没有可用快照时，本轮关闭 AI 策略，不阻断正常事实问答。

## 宪法边界

- 业务配置负责适用场景、节点目标、时间、素材来源和真实业务口径。
- 当前 V3 Reply 负责理解客户语义、心理、卡点、主任务、情绪和成交节奏，并先回答当前问题，再自然执行至多一个合法推进动作。
- 代码只负责配置传输、schema、稳定 key、事实、工具、安全、幂等、发送和审计。
- 禁止在配置中下发 `raw_prompt`、`system_prompt`、`developer_prompt` 或 `prompt_template`，防止第三方页面绕过 V3 的系统边界。
- 意向评分只用于 BI 和复盘，不能直接触发回复、逼单或发送。

## 第三方平台接口

第三方平台开发完成后，应提供一个“已发布快照”接口。建议：

```http
GET /api/v1/ai-sales-policy/published?scope=default
If-None-Match: <previous-checksum>
```

成功响应直接返回完整 `ai_sales_policy_v1` JSON；建议同时提供：

- `policy_version`：每次发布递增且不可复用。
- `updated_at`：带时区的发布时间。
- `status=published`：V3 不读取草稿。
- `checksum` 或 ETag：用于缓存与审计。
- 稳定的 `sequence_key/node_key/key`：发布后不可因改显示名称而改变。

不要把四个页面做成四次运行时调用。它们共同决定同一轮回复，拆开读取会产生版本不一致。

## 数据源切换

V3 已通过 `AiSalesPolicyProvider.load_raw()` 隔离数据源。接第三方时只需要新增 HTTP Provider，并保持返回：

```python
tuple[dict, dict]  # 完整配置，来源元数据
```

来源元数据只进入管理页和审计，不进入模型上下文。切换流程：

1. 第三方 Provider 以 shadow 方式拉取并校验，V3 仍使用本地 JSON。
2. 对比第三方快照与本地快照的版本、checksum、稳定 key 和校验结果。
3. 将 Provider 切到第三方，保留本地 JSON 作为紧急回退版本。
4. 观察 Reply 的结构化策略决策审计；确认实时会话稳定后再评估延时任务从 shadow 改为 active。

## 延时逼单后续阶段

延时节点不能仅凭配置时间直接发送。进入 active 前必须完成：

- 按 `corp_id + wechat + external_userid/customer_id` 隔离序列状态。
- 客户回复即取消旧节点并重新进入当前 V3 回复链。
- 到期发送前成功拉取最新会话；失败、时间不明或客户已回复时禁止发送。
- 风险、人工接管、明确退订、已付、已预约、订单不匹配和重复发送的确定性拦截。
- 记录策略版本、序列、节点、触发依据、发送前复核、取消和完成原因。

在这些合同测试全部通过前，`silent_tasks_mode` 必须保持 `shadow`。
