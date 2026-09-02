# store-workflow-full-coverage

## 目标

- 继续验证“任何输入”在 V3 门店链路中的处理：上游是否应调用门店工作流、门店工作流是否返回准确事实、最终是否有安全下一步。
- 覆盖门店相关与非门店输入、详细/粗略/同名/泛名地址、匹配/未匹配、远近距离、重发地址/导航、门店详情等场景。

## 非目标

- 不发送真实客户消息。
- 不调用生产写接口。
- 不把原始测试输出、模型输出或客户原文写入 Git/docs。
- 不把销售语义改成代码关键词分支。

## Base

- base SHA: `784fb660772a8e30a9c59da720169c612a10090d`
- branch: `codex/store-workflow-full-coverage`

## 独占范围

- `ai_paths/app/graph/nodes/action_nodes.py`
- `ai_paths/app/graph/nodes/reply_contract.py`
- `ai_paths/app/services/store_destination_resolver.py`
- ignored artifact: `artifacts/store_workflow_full_coverage_20260902/`

## 不可破坏合同

- V3 Reply 仍是唯一销售语义决策。
- 门店事实必须来自结构化门店工具；不编造地址、距离、停车、营业时间、路线。
- 明确退订/停止联系不得推进销售。

## 当前状态

- 第一阶段门店事实矩阵 31/31 通过。
- 第二阶段 router + protocol recovery + store workflow 组合矩阵 61/61 通过。
- 验证路径：`artifacts/store_workflow_full_coverage_20260902/combined_matrix_out5/`。
- 覆盖结果：
  - 上游 V3 semantic router 只在门店相关输入、定位卡协议恢复、地址/导航重发等场景调用门店工作流。
  - 价格、效果、信任、忙、已支付、预约时间、停止联系等非门店输入未误调用门店工具。
  - 泛地标、无父级锚点短地名、省份级泛问进入确认，不直接返回地图服务商任意落点。
  - 城市明确但本地无门店返回无本地候选，不跨城市/跨省兜门店。
  - 店名、城市+区、详细地址、定位卡、导航/地址重发可匹配正确门店范围。
- 速度观察：61 条平均 11034ms，P95 21777ms，最大 31304ms；外部模型/地图链路仍存在偶发慢调用。
