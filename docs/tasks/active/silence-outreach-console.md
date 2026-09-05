# silence-outreach-console

- status: active
- owner: Codex
- base_branch: main
- base_sha: `6ecbc87f509a3b578a0e5136a16fda0541ff9f50`

## 目标

- 将“首日千人千面日志/配置”统一改为“沉默客户唤醒”产品语义。
- 重构页面信息层级，让运营能直接看懂开关、适用范围、AI 门禁、阈值、启用水位、计划与阻断结果。
- 核验生产配置和真实运行日志，确认全账号、1 分钟、不限加微时间及人工阻断实际生效。

## 非目标

- 不重命名历史数据库表、持久化字段和兼容 API 路径。
- 不修改沉默唤醒算法、发送频率、客户安全边界或 V3 Reply。
- 不伪造符合条件的生产客户或主动触发客户发送。

## Change contract

- type: 管理前端改版与线上只读审计
- scope: 沉默唤醒日志页、配置展示、运营总览文案、导航名称及合同文档
- risk: 展示层误读旧字段；配置保存后 worker 未重启导致显示与执行不一致；页面改版破坏历史日志详情
- validation: 前端类型检查/构建、后端回归、浏览器视觉与交互核验、线上进程环境/配置接口/真实阻断日志核验
- rollback: 回滚前端 release；后端沉默唤醒开关和 worker 保持不变

## 独占范围

- `projects/src/components/logs/first-day-outreach-log-viewer.tsx`
- `projects/src/components/admin/operations-dashboard.tsx`
- `projects/src/components/admin/app-shell.tsx`
- 相关 API/前端测试和页面合同文档

## 已确认事实

- 历史内部 API 与数据库仍使用 `first-day` / `first_day_opened_silence`，需要兼容保留。
- 当前业务逻辑已经不限制加微时间，只有部署启用水位后的出站消息可以进入。
- 计划前与发送前都要求平台明确 AI 模式，人工或未知状态失败关闭。

## 完成

- 页面及导航的产品语义统一为“沉默客户唤醒”，历史 `first-day` API 路径仅保留兼容。
- 增加规则摘要、当前页运行概览、中文阻断原因和移动端详情返回，并明确配置保存后需重启后台执行进程。
- 生产配置接口与 worker 进程环境一致：全账号、1 分钟、不限制加微时间、启用水位生效。
- 启用水位后抽查 31 条真实判断，14 条因人工模式阻断、12 条因客户未真实开口阻断、5 条因客户关系失效阻断，未创建计划、未发送消息。

## 测试证据

- TypeScript、ESLint、Next.js 生产构建通过。
- 后端回归 `199 passed`。
- 桌面端与 390px 手机端页面交互通过，浏览器控制台 0 error / 0 warning。
- 生产 control、reply、worker 健康；配置接口和 worker 环境值一致。

## 待办

- 合入 main、部署前端并完成生产页面复验。
- 归档任务。
