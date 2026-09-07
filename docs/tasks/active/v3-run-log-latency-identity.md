# V3 运行日志完整耗时与身份信息

## 目标

- AI 运行日志列表显示每次请求的完整服务端耗时。
- 计时从 V3 HTTP 请求进入服务开始，到客户响应完成组装前结束，覆盖模型图之后的持久化与提交尾部。
- 日志详情以可读字段展示客户、客户加微关系、外部联系人、会话、企业、接待人员和企微号等已有身份 ID。

## 非目标

- 不改变 V3 Reply、策略选择、消息发送或主动唤醒逻辑。
- 不补造历史缺失身份，不跨企微号推断客户关系。
- 不新增完整客户聊天或 Prompt 留存。

## 基线

- base SHA：`1dcfbd3574e116f6baf5bfcf1fdde64f5a1a456a`
- 开发分支：`codex/v3-run-log-latency-identity`
- 最后核验生产运行基线：`main@71a18d7d153083dc95e40470e030a48ce4c29f0b`
- 生产接口：仅 V3 `/reply/workflow-compatible-v3`

## 独占范围

- `ai_paths/app/main.py`
- `ai_paths/app/routers/reply.py`
- `ai_paths/app/chat_runtime.py`
- `ai_paths/app/services/v3_request_timing.py`
- `ai_paths/app/routers/operations_admin.py`
- 与运行日志持久化、列表摘要和观测视图直接相关的辅助模块
- `projects/src/components/logs/`
- `projects/src/app/api/logs/runs/`
- 本任务涉及的接口、当前状态和历史摘要文档

## 不可破坏合同

- 销售接触边界保持 `corp_id + wechat + external_userid/customer_id`。
- 管理接口继续使用 Bearer 鉴权，身份字段只从当时运行留存或同边界事实派生。
- 历史字段缺失显示“未记录”，不得用当前平台状态回填成当时事实。
- 观测失败不得使客户回复失败。

## 风险与回滚

- 风险：路由计时与图耗时混用、额外持久化增加尾部延迟、身份字段跨企微串联、前端暴露过多敏感信息。
- 控制：区分完整请求耗时和节点耗时；复用既有字段和脱敏规则；按完整接触边界读取；管理页鉴权不变。
- 回滚：恢复发布前 clean release；本任务优先不新增数据库结构。

## 验证计划

- 后端：V3 入口完整计时、成功/降级/失败路径、运行列表与详情合同、身份隔离与历史缺失。
- 前端：类型检查、Lint、生产构建；列表耗时和详情身份在桌面/移动端真实浏览器检查。
- 发布：同一 clean main SHA 部署 control、reply、worker 和管理前端，核验健康、日志接口和首条新日志。

## 进度

- 已完成：建立干净任务分支并登记文件范围；实现 V3 ASGI 入口到响应发送完成的完整计时；运行详情增加八类身份字段；日志列表和详情 UI 完成；后端 348 条回归、Ruff、前端类型检查、Lint 和生产构建通过。
- 待完成：真实浏览器验收、合入 main、生产部署和文档收口。
