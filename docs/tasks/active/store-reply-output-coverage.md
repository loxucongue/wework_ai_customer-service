# store-reply-output-coverage

## 目标

- 继续扩大 V3 门店场景覆盖，从“门店事实工作流正确”推进到“最终客户可见回复正确”。
- 覆盖门店相关与非门店输入、详细/粗略地址、同名/泛名地点、匹配/未匹配、远近/重发导航等场景。
- 准确性以结构化门店事实、地址/geocode 结果和全量可见门店库为准；不得编造地址、距离、路线、停车、营业时间或门店存在性。

## 非目标

- 不发送真实客户消息。
- 不调用生产写接口。
- 不把原始模型输出、客户原文、测试报告写入 Git/docs；完整报告只放 ignored `artifacts/`。
- 不新增第二套销售语义规则引擎；V3 Reply 仍是唯一客户可见销售语义决策。

## Base

- base SHA: `d44c05bf0`
- branch: `codex/store-reply-output-coverage`

## 独占范围

- `ai_paths/app/graph/`
- `ai_paths/app/prompts/`
- `ai_paths/app/services/store_destination_resolver.py`
- `ai_paths/app/services/v3_semantic_router_service.py`
- ignored artifact: `artifacts/store_reply_output_coverage_20260902/`

## 不可破坏合同

- 客户回复产品接口只允许 V3。
- 门店事实必须来自门店工具、定位卡或 geocode；模型不得自行编造。
- 明确停止联系不得推进销售。
- 隔离测试必须 `test_isolated=true`，不落 BI、不发送、不写生产。

## 已完成

- 修复结构化定位卡、历史门店重发、短地点回答等场景的目的地解析。
- 修复无本地门店时 `distance_calculate` 覆盖 `customer_store_lookup=no_match`，导致 Reply 误发其他城市门店卡的问题。
- 修复 V3 校验/结构补全读取事实路径不一致的问题：当 `fact_envelope` 缺失或为空时，回读 `evidence_join.normalized_tool_facts.structured_facts`。
- 修复 Reply 模型超时或单次修复失败导致空回复的问题：仅在已有当前轮核验门店卡结构时，用中性文本包装已核验地址卡，不选择新门店、不改变销售策略。
- 修复客户发送 `位置：/地址：/定位：...` 结构化地址但语义模型漏规划门店工具的问题；归一层会把它作为门店匹配事实输入触发工具。

## 验证证据

- 本地静态编译通过：
  - `python -m py_compile ai_paths/app/services/v3_semantic_router_service.py ai_paths/app/graph/nodes/action_module_outputs.py ai_paths/app/graph/nodes/reply_generation.py ai_paths/app/graph/nodes/reply_validation.py ai_paths/app/graph/nodes/reply_nodes.py`
- 服务器隔离真实 V3 Reply 矩阵：
  - 报告目录：`artifacts/store_reply_output_coverage_20260902/reply_matrix_out_full3/`
  - 样本数：62
  - 通过：62
  - 失败：0
  - 复核：0
  - 平均耗时：9635ms
  - P95：12939ms
  - 最大：16585ms
  - 可见门店库：233 家
  - Reply 来源：`main_model` 62/62

## 待办

- 提交并合入 `main`。
- 合入后把摘要写入 `docs/tasks/history/INDEX.md`，并移除本活动任务文件。
