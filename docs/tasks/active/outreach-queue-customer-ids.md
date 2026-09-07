# outreach-queue-customer-ids

- status: active
- owner: Codex
- base_branch: main
- base_sha: eacdeaed140592dd058b5d3bb0b839760b095466
- production_baseline: `ai-paths-unified-20260907-133305-5043dbf3`

## 目标

- 在主动唤醒看板的当前队列及明细中展示可排障的客户身份与任务 ID。
- 补齐客户、外部联系人、会话、企业、企微、运行、计划和任务等已有稳定标识。
- 保持页面易读，并支持复制单个 ID，避免默认展示大段 JSON。

## 非目标

- 不改变主动唤醒候选、计划、发送和安全门禁。
- 不新增客户身份字段或数据库表，不扩大原始聊天与模型输出留存。
- 不把 token、手机号等未纳入现有管理合同的敏感信息加入队列接口。

## Change contract

- type: read-only observability + frontend
- scope: 主动唤醒队列返回结构、客户/任务身份卡片、测试和合同文档
- risk: 跨企微关联错误；历史 ID 缺失；表格过宽；管理接口泄露不必要字段
- validation: SQLite/MySQL 兼容合同、客户边界测试、前端类型/Lint/构建、桌面与移动端交互、生产只读核验
- rollback: 恢复发布前 backend/reply/worker/frontend release；无数据库迁移

## 不可破坏合同

- 身份关联固定使用 `corp_id + wechat + external_userid/customer_id`。
- 只展示当时已留存或可从同一身份边界确定性派生的 ID；缺失显示“未记录”。
- 看板查询只读，不触发计划、发送或平台写接口。

## 待办

- 合入 main、部署及生产核验。

## 已完成

- 核对生产快照：原队列已有客户、外部联系人、企业、企微、运行和计划 ID，但缺少会话、接待人员及任务 ID 的统一输出与展示。
- 后端按完整销售接触边界批量补齐最新会话 ID、客户名称/备注和接待人员 ID；队列增加任务 ID、状态、计划/发送时间及平台消息 ID 引用。
- 队列表格直接展示客户、会话、计划和首个任务关键 ID；详情页新增“客户与 ID”区，完整展示并支持逐项复制。
- 未新增数据库表、模型调用、客户发送或手机号等额外敏感信息。

## 测试结果

- 后端全量回归：`345 passed`。
- 新增跨企微同客户标识隔离测试，确保会话与名称不会串号。
- 前端 TypeScript、ESLint、Next.js 生产构建和服务端打包通过。
