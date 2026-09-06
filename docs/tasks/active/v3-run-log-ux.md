# v3-run-log-ux

- status: active
- owner: Codex
- base_branch: origin/main
- base_sha: bfe7ab5e339f600ea2631172a02ad2beedba1416
- production_verified_at: not-required（本任务不操作生产）
- production_releases: not-verified

## 目标

- 把 AI 回复运行日志改为业务判断优先、技术排障下钻的 V3 可视化页面。
- 清晰展示最终意图、情绪、卡点、逼单、候选与采用的跟进序列/话术、节点执行和发送结果。
- 节点原始输入输出按需读取，默认不展示大段 JSON。

## 非目标

- 不修改 V3 Reply 语义、模型、Prompt、知识召回、发送或策略回写逻辑。
- 不新增数据库表或延长原始日志留存时间。
- 不发布生产服务。

## Change contract

- type: 管理端只读接口与前端可视化改造。
- scope: V3 可观测摘要、运行列表、单节点详情、AI 运行日志页面。
- risk: 历史日志字段不完整；原始节点载荷较大；Router 与 Reply 结果容易被误读为同一决策。
- validation: 后端最小合同测试、前端 type/lint/build、桌面与移动端浏览器检查。
- rollback: 回退本任务提交；无数据库数据回滚。

## 涉及模块与文件所有权

- `ai_paths/app/services/run_observability*.py`
- `ai_paths/app/services/storage/run_repository.py`
- `ai_paths/app/routers/operations_admin.py`
- `projects/src/components/logs/run-log-*`
- `projects/src/app/api/logs/runs/**`
- `docs/interfaces/public.md`、本任务 active/history 记录

## 不可破坏合同

- V3 Reply 仍是唯一最终销售语义决策者；日志不得从回复文本重新推断意图、情绪或卡点。
- 管理接口保持 Bearer 鉴权和旧调用兼容。
- 不保存新的完整客户原文、Prompt 或模型输出；原始详情只展示现有留存且保持凭证脱敏。
- 不增加 Reply 主链模型调用、数据库写入或发送动作。

## 已确认事实与证据

- `runs.output_snapshot` 已保存最终意图、情绪、逼单和卡点字段。
- `observability_v3` 已保存 Router 卡点、候选序列、候选话术、最终采用、门店与回复结果。
- 当前 `/admin/runs/{request_id}` 只生成通用 `run_observability_v1`，前端没有读取 V3 业务投影。
- 节点轨迹现有保留期为 14 天，运行摘要为 90 天。

## 已完成

- 完成代码、合同和现有页面结构核验。
- 登记独立任务、分支和文件所有权。

## 待办

- 实现 V3 管理视图、轻量列表和单节点详情接口。
- 重构日志页面与详情抽屉。
- 完成测试、视觉检查、合同更新和合并。

## 测试结果

- 待执行。

## 发布与回滚

- 本任务只合入 main，不执行生产部署。

## 待沉淀的长期结论

- 管理日志应区分 Router 召回判断与 Reply 最终决策。
- 历史字段缺失必须展示为未记录，不能折算为零。
