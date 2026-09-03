# v3-bi-dashboard

## 目标

- 基于既有 V3 策略埋点管理接口，交付首版产品化 BI 面板。
- 优化管理端信息架构、筛选、指标概览、维度排行、转化与问题监控。
- 核实“预约卡点话术”和“SOP异议素材”页面是否仍被运行链路使用；仅在无运行依赖后删除页面和导航。

## 非目标

- 不修改 V3 Reply 销售语义。
- 不新增第二套统计事实或前端自行计算业务口径。
- 不启用延时逼单发送，不写生产数据。

## Base 与依赖

- base SHA: `403deb4568d85c5a1981b5e0cd46dd98f4881c19`
- branch: `codex/v3-bi-dashboard`
- 后端依赖：`codex/v3-sales-decision-observability@a858624d` 提供的只读 analytics API；该后端尚未通过模型金标发布门槛。
- production baseline: 发布前现场核验。

## 独占范围

- `projects/src/app/` 下 BI 页面、删除确认后的旧页面和 API 代理。
- `projects/src/components/admin/` 下管理面板组件与导航。
- `projects/src/app/globals.css`（仅必要的管理端样式）。
- 本任务相关接口与任务文档。

## Change contract

- 类型：管理前端、只读 API 代理、无用页面治理。
- 风险：统计口径误读、后端不可用时页面崩溃、误删仍被导航或接口依赖的旧页面。
- 验证：引用检索、TypeScript/ESLint/build、空态和错误态、Playwright 桌面/窄屏视觉检查。
- 回滚：回滚本任务前端提交；不影响 Reply、埋点写入或发送链路。

## 待办

- [x] 从最新 `origin/main` 建立干净分支并登记独占范围。
- [ ] 审计两个旧页面、导航、API 和服务端运行引用。
- [ ] 实现 BI 面板及只读代理。
- [ ] 删除确认无用页面和入口。
- [ ] 完成构建与浏览器验收。
- [ ] 更新接口文档并提交。
