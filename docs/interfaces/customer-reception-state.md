# 客户接待状态同步接口

本页定义稳定接口合同；部署、配置和接管状态以 `docs/current/PRODUCTION_STATE.md` 为准。当前阶段仅接收和持久化，不改变现有消费者的资格来源。

`POST /api/ai/customer/reception-state`，控制面承载。请求头 `Authorization: Bearer <专用凭证>`，内容类型 `application/json`。缺少服务端专用凭证配置时返回 503，错误凭证返回 401。接口不维护企业、成员或来源 IP 白名单；持有凭证的调用方是上报身份事实的权威来源。禁止使用 Reply 接口提交状态。

| 字段 | 类型和约束 | 含义 |
| --- | --- | --- |
| event_id | 非空字符串，最多64字符，区分大小写 | 全局通知唯一ID，重试保持不变 |
| customer_id | 正整数 | 微动客户ID |
| customer_add_wechat_id | 正整数 | 本次加微关系ID |
| wecom_corp_id | 非空字符串，最多64字符 | 企业CorpID |
| employee_wechat_id | 非空字符串，最多64字符 | 企微成员UserID，直接作为接待账号隔离维度 |
| customer_external_user_id | 非空字符串，最多128字符 | 客户ExternalUserID |
| state_version | 正整数 | 企业＋成员＋外部联系人范围内递增，重加不能归零 |
| occurred_at | 正整数 | Unix秒，仅审计，不排序 |
| data.service_mode | 整数1或2 | 1 AI、2人工 |
| data.is_deleted | JSON布尔值 | 本次关系是否删除 |
| data.ai_version | 可选v1/v2/v3或null | 仅记录；省略清空指定值，执行仍为V3 |

所有整数最大9007199254740991，不接受字符串、浮点或布尔替代。标识不得带首尾空白、控制字符；额外字段拒绝。请求体最多16KiB。

```json
{"event_id":"example-001","customer_id":10001,"customer_add_wechat_id":90001,"wecom_corp_id":"example-corp","employee_wechat_id":"example-member","customer_external_user_id":"example-external","state_version":101,"occurred_at":1788919200,"data":{"service_mode":1,"is_deleted":false,"ai_version":"v3"}}
```

成功在事务提交后返回：

```json
{"code":200,"message":"状态已保存","data":{"event_id":"example-001","result":"applied","current_state_version":101,"requested_ai_version":"v3","effective_ai_version":"v3","version_switch_enabled":false}}
```

| HTTP/code | result或message | 调用方处理 |
| --- | --- | --- |
| 200 | applied | 保存成功 |
| 200 | duplicate | 相同通知或同版本同快照，不重复处理；返回当前快照版本 |
| 200 | stale_ignored | 旧版本忽略，不倒退版本 |
| 400 | invalid_payload | 修正字段，不无限原样重试 |
| 401 | invalid_token | 修正专用凭证 |
| 409 | event_id_conflict / state_version_conflict | 同事件异内容、同版本异快照，核对来源 |
| 409 | deleted_relationship_cannot_revive / retired_relationship_conflict | 已删除关系复活或已退役关系替换当前关系 |
| 503 | reception_state_not_configured / state_storage_unavailable | 修正配置或同event_id、同请求退避重试 |

错误格式 `{"code":409,"message":"state_version_conflict","data":null}`。网关限流时按429/Retry-After重试。本阶段应用未新增限流器。

唯一专用配置是 `RECEPTION_STATE_API_KEY`。凭证实际值不进入 Git、文档或日志。服务以 `wecom_corp_id + employee_wechat_id + customer_external_user_id` 建立接触边界；不查询本地成员目录或 `customer_identity_links`，因此调用方必须保证企业、成员、客户及加微关系字段真实一致。不同成员的状态严格隔离，不共享版本或关系历史。

聚合应可靠保存事件、失败补投；切人工或删除时立即拦截旧AI消息，即使通知失败也不得放行。切回AI不补旧消息。本地状态接管之前，接口200仅证明状态已提交；尚不能证明实时回复/SOP/唤醒已按本地状态取消，也不证明客户消息已送达。

上线步骤：先在隔离MySQL演练增量迁移20260914_01，部署经过审核的clean main，配置企业凭证和权威映射，联调接口；保持旧资格查询。之后由聚合补齐存量快照与增量通知，核对覆盖率100%、冲突0、发送拦截及全部消费者版本防线，再单独授权接管。应用回滚保留状态和事件，非空表不允许降级删除。
