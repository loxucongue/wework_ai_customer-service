# 治理并行分支集成

- status: active
- owner: primary integrator
- base_sha: `f36add37412310613936be5bbd8f58fb591fead3`
- target_branch: `main`
- production_baseline: 未在本任务中现场核验；禁止部署

## 目标

集成并验证以下四个从同一基线产生的提交：

- runtime: `853be215d857354e99fd9e4dfd8b4ee410552dfe`
- sop: `1b34c3b9b77551a3abffbb81069f272f932dbb3e`
- outreach: `1178217e77e7b17fb9a090225a3d13725f8ff804`
- quality: `e5422babfd21dbc9927333bea1cb4511e8170c69`

完成角色隔离、人工接管失败关闭、SOP 公共执行核心、Outreach 职责迁移和自动质量门禁，最终形成唯一干净的 `main` 提交。

## 非目标

- 不部署生产。
- 不修改数据库 schema 或运行数据。
- 不解除 AI 销售策略、延时逼单或跟进任务的 shadow。
- 不调用真实模型、真实发送或生产写接口。
- 不改变销售语义、支付、门店、SOP 消费和策略回传合同。

## 不可破坏合同

- 回复产品接口只保留 V3。
- 人工接管状态不可得时保守阻断。
- `corp_id + wechat + external_userid/customer_id` 完全隔离。
- 会话拉取失败、已付保护、真实订单发卡和明确退订边界保持不变。
- 消息送达、SOP 消费和策略数据回传分别审计。

## 集成顺序

1. runtime
2. sop
3. outreach
4. quality
5. 处理接线和启动阻断
6. 全量验证
7. 快进 main、推送并删除所有临时分支/worktree

## 验证

- Python 编译与全量确定性测试
- V3-only 路由合同
- reply/control/worker 隔离启动和关闭
- 前端 TypeScript、ESLint、生产构建
- 质量审计和增长门禁
- `git diff --check`

## 回滚

集成验证完成前保留四个源 worktree 和提交。`main` 不移动、生产不切换；失败时删除本集成分支即可回到 base SHA。
