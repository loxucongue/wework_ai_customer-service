# core-governance

- status: active
- owner: project
- base_branch: main
- base_sha: 3c300e580058443fd2ff94e86ba2aff326f22219
- production_verified_at: 未核验；本任务不部署
- production_releases: 以现场核验为准

## 目标

把当前“清楚的运行外壳 + 巨型业务内核”收口为单一职责架构：一个销售决策来源、按角色装配最小依赖、Outreach 与第三方 SOP 使用明确阶段、测试只验证公开业务合同。

## 非目标

- 不修改 V3、回调和管理端公开接口。
- 不修改数据库 schema、Prompt 业务含义和功能开关。
- 不部署生产，不调用真实模型和生产写接口。
- 不引入抽象基类、注册器、事件总线、动态代理或兼容双轨。

## Change contract

- type: architecture_refactor
- scope: runtime composition、V3 Graph、Outreach、第三方 SOP、workflow_tests、架构文档
- risk: 回复决策漂移、SOP 重复发送、终态漏回传、Outreach 任务恢复失效
- validation: 公开接口合同、身份隔离、支付/门店、退订、SOP 20→30/70、送达回调、角色启动、前端构建
- rollback: 每个核心域独立提交；失败按域回退，不保留旧实现并行运行

## 涉及模块与文件所有权

- `ai_paths/app/runtime_services.py` 与 `routers/`：角色级依赖装配
- `ai_paths/app/graph/`：语义证据、唯一回复决策和交易提交
- `ai_paths/app/services/outreach/`：计划、首日、执行、消息阶段
- `ai_paths/app/services/sop_platform_task_service.py` 与 `services/sop/`：两段式 SOP
- `workflow_tests/`：公开业务合同；删除私有实现测试
- `docs/architecture/`、`docs/contracts/`：目标结构和长期合同

## 不可破坏合同

- V1/V2 不恢复；Reply/Control/Worker 来自同一 main SHA。
- 身份边界为 `corp_id + wechat + external_userid/customer_id`。
- 人工接管、明确退订、订单、支付、门店和素材 URL 是硬边界。
- 第三方 SOP 保留 `pending` 触发节点与 `store-visit-pending` 内容节点配对。
- 消费、策略数据回传和消息送达分别审计；失败不得伪造完成。
- 测试不得调用真实模型、真实发送或生产写接口。

## 已确认事实与证据

- `main.py` 已收口为 57 行，角色路由物理隔离已完成。
- V3 仍存在前后两次 semantic router，Reply 再输出销售决策；节点命名与真实职责不一致。
- Outreach 的 `_build_plan` 约 959 行、`execute` 约 640 行，并存在 Planning 与 FirstDay 双向职责依赖。
- 第三方 SOP 服务约 4958 行，混合拉取配对、判断发送、终态恢复和日志查询。
- 当前约 152 处测试直接导入私有符号；质量基线只冻结债务。

## 已完成

- 创建干净治理分支与单一活动任务。
- 确认不执行“删除全部测试”，改为公开合同替换后删除私有测试。
- 修复 Control 消息送达回调错误依赖 Reply `ChatRuntime` 的职责缺陷；新增独立异步回复送达终态服务。
- 将 Graph 的 `sales_decision` 更名并收口为 `semantic_evidence`，明确 Semantic Router 不拥有销售动作决策权，Reply 是唯一客户可见销售决策来源。
- 修正文档中已删除的 `runtime_routes.py` 和旧 Graph 节点描述。

## 待办

1. 完成角色级最小依赖装配，删除总服务定位器中的跨角色空对象。
2. 删除 Reply 校验层残留的普通销售语义二次判断，只保留硬合同。
3. 重写 Outreach 和第三方 SOP 阶段内核。
4. 建立最小公开合同测试集并删除私有实现测试。
5. 更新文档、执行门禁、合并 main，生产另行发布。

## 测试结果

尚未开始代码修改。

## 发布与回滚

本任务不部署。完成后只合入 `main`；生产发布使用独立任务现场核验。

## 待沉淀的长期结论

- 公开合同测试是唯一长期测试资产；私有函数测试不作为架构兼容理由。
- 语义证据与销售决策必须分开，销售动作只能有一个模型决策来源。
