# store-list-delivery

- status: active
- owner: codex
- base_branch: main
- base_sha: fafe3dea04722185a38c5321dfe07ae177ea8d7d
- production_verified_at: 2026-09-07 15:29 Asia/Shanghai
- production_releases: control/reply/worker/frontend=20260907-152202-b4dfc184@b4dfc184ea635afeb8f62dd881df30b1946dd016

## 目标

- 城市级范围存在大量门店时，普通“是否有店”问题先询问客户所在区县或地标，并提供真实覆盖区县。
- 客户明确要求全部门店时，按编号逐行返回完整门店名、区县和可用的完整地址。
- 主模型失败时，可信门店恢复回复遵守同一展示合同。

## 非目标

- 不修改门店数据源、地图排序算法或门店授权范围。
- 不修改第三方发送协议、SOP 或数据库结构。

## Change contract

- type: V3 reply behavior and deterministic fact contract
- scope: destination request kind, store resolution facts, Reply prompt/admission/recovery, deterministic tests
- risk: explicit full-list requests could be over-clarified; incomplete address facts could be presented as complete
- validation: targeted store semantic/workflow tests plus existing store contract regression suite
- rollback: revert the single implementation commit and redeploy the previous clean main release

## 涉及模块与文件所有权

- `ai_paths/app/prompts/store_destination_resolver.py`
- `ai_paths/app/services/store_destination_resolver.py`
- `ai_paths/app/graph/nodes/action_module_outputs.py`
- `ai_paths/app/prompts/reply_synthesizer.py`
- `ai_paths/app/graph/nodes/reply_validation.py`
- `ai_paths/app/graph/nodes/reply_nodes.py`
- `ai_paths/app/graph/nodes/reply_generation.py`
- 门店相关确定性测试

## 不可破坏合同

- 不从客户文本在 Python 中推断销售意图；目的地解析模型负责区分存在性查询和明确全量列表。
- 具体门店名称、区县和地址只能来自本轮授权门店事实。
- 已有精确位置时继续按权威排序结果交付，不重复索要客户已经提供的位置。

## 已确认事实与证据

- 生产请求 `91022453-013d-44d7-a12c-ed02d97ffdf1` 将“重庆有门店吗”解析为 `request_kind=list`，13 家候选触发 `text_store_list`。
- 主回复及单次修复均遗漏完整门店名和区县，被 `incomplete_text_store_list_contract` 拒绝；可信恢复最终返回完整文字列表。

## 已完成

- 核对生产请求、门店事实、两次模型输出和可信恢复结果。
- 目的地解析新增 `availability`，只把明确全量请求归为 `list`。
- 城市级候选超过 6 家的普通存在性询问改为真实区县覆盖说明与位置追问。
- 明确全量清单改为编号逐行展示完整门店名、区县和地址；可信恢复与准入校验使用同一合同。
- 长期规则已同步到销售策略合同与系统结构文档。

## 待办

- 合并、发布并验证。

## 测试结果

- `python -m pytest tests -q`: 351 passed。
- 门店重点回归：193 passed。
- `python -m compileall -q ai_paths/app`: passed。
- Ruff 修改范围通过；仓库既有 `reply_validation.py:589 F841` 未在本任务修改，使用 `--ignore F841` 核对本次范围通过。
- 真实 DeepSeek 单节点隔离验证：`重庆有门店吗 -> availability`；`重庆都有哪些门店 -> list`；`把重庆全部门店地址发我 -> list/address`。
- 未执行生产写接口或真实客户发送。

## 发布与回滚

- pending

## 待沉淀的长期结论

- 门店列表展示及城市级大量候选的澄清规则写入销售策略运行合同。
