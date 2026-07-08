# 回复链路职责收口重构设计（2026-07-08）

## 1. 目标和边界

本设计用于旧版快照 `d9aea9886 chore: 保存回复链路重构前快照` 之后的分批重构。

目标不是新增能力，而是把现有回复链路的职责重新收口：

- LangGraph 节点数量不变。
- 代码层只负责输入清洗、事实整理、工具参数保护、schema 归一、硬事实边界和不可空回复保护。
- planner 模型负责业务语义、销售节奏、是否延续历史、是否推进预约金、是否调用工具。
- reply 模型负责基于事实生成自然客服回复。
- SOP 静态包和普通聊天共享核心事实边界，避免同客户口径冲突。

明确不做：

- 不新增 LangGraph 节点。
- 不改外部 API 协议。
- 不改支付、门店、档期工具协议。
- 不把业务语义重新写成 Python 关键词分支。
- 不在代码兜底里生成复杂销售话术。

## 2. 当前节点图

当前线上主回复图保持如下：

```text
layer_1_input_normalization
  -> layer_2_background_context
  -> planner_brain
  -> execute_actions
  -> synthesize_reply
  -> END
```

后台任务：

```text
synthesize_reply 后异步触发 profile_event_extractor
```

重构后仍保持这个拓扑，只重构节点内部文件和职责。

## 3. 节点一：layer_1_input_normalization

### 当前职责

文件：

- `ai_paths/app/graph/nodes/layer_nodes.py`
- `ai_paths/app/graph/nodes/common.py`
- `ai_paths/app/graph/nodes/image_info.py`
- `ai_paths/app/graph/nodes/image_validation.py`

当前入口是 `create_input_normalization_layer()`。

它目前做：

- `content` trim。
- 空文本但有图时转为 `[图片]`。
- mojibake 修复。
- 疑似乱码打 warning。
- 图片理解模型调用。
- 生成 `normalized_content`、`image_info`、`encoding_repair`。

### 重构目标

这个节点只能做输入层事实，不得做业务判断。

允许：

- 文本清洗。
- 乱码修复。
- 假图片值归一。
- 图片事实识别。
- 输入质量标记。

禁止：

- 判断客户是否想报价、门店、预约金。
- 判断客户当前销售阶段。
- 输出客户可见话术。

### 文件级改法

#### `layer_nodes.py`

保留 `create_input_normalization_layer()`，但把具体清洗逻辑下沉。

目标输出：

```python
{
    "normalized_content": str,
    "encoding_repair": dict,
    "input_quality_flags": dict,
    "image_info": dict,
    "errors": list,
    "trace": list,
}
```

`input_quality_flags` 建议字段：

- `empty_text`: bool
- `has_current_image`: bool
- `image_input_normalized`: bool
- `mojibake_suspected`: bool
- `mojibake_repaired`: bool
- `platform_auto_opening_candidate`: bool

注意：`platform_auto_opening_candidate` 只是输入事实标记，不在这里决定是否回复。

#### `common.py`

集中输入工具函数：

- `normalize_text_content(raw: Any) -> tuple[str, dict]`
- `normalize_image_value(raw: Any) -> tuple[str | None, dict]`
- `repair_mojibake_text(text: str) -> tuple[str, dict]`
- `looks_bad_text(text: str) -> bool`

`normalize_image_value` 必须把这些值当成无图：

- `None`
- `""`
- `"False"`
- `"false"`
- `"null"`
- `"None"`
- `"0"`

#### `image_info.py`

只输出图片事实：

- `has_image`
- `image_type`
- `visible_text`
- `skin_or_face_related`
- `case_or_effect_related`
- `activity_or_offer_related`
- `confidence`
- `warnings`

不得输出：

- 是否该发案例图。
- 是否该报价。
- 是否该转人工。

#### `image_validation.py`

只校验 vision 输出 schema，不做业务分流。

### 验收场景

- `file_image="False"` 不触发图片场景。
- `闂ㄥ簵鍦ㄥ摢` 可保守修复为“门店在哪”并记录 trace。
- 企微自动开场只被标记，不在该节点直接返回。
- 图片理解失败时主链路继续。

## 4. 节点二：layer_2_background_context

### 当前职责

文件：

- `ai_paths/app/graph/nodes/layer_nodes.py`
- `ai_paths/app/graph/nodes/conversation_history_fetch.py`
- `ai_paths/app/graph/nodes/sent_message_summary.py`
- `ai_paths/app/services/customer_context.py`
- `ai_paths/app/services/customer_store_knowledge.py`
- `ai_paths/app/services/platform_agent_client.py`
- `ai_paths/app/services/outreach_send_client.py`
- `ai_paths/app/services/customer_appointment_context.py`
- `ai_paths/app/services/customer_order_context.py`

当前入口是 `create_background_context_layer()`。

它目前并行加载：

- 本地 memory。
- 客户身份。
- 平台近 20 条历史。
- 客户订单/预约上下文。
- 客户门店 scope。
- 本地门店目录补全。

### 重构目标

背景层只负责拉取和整理事实，不决定本轮任务。

统一输出 7 个事实分区：

```python
{
    "history_context": dict,
    "identity_context": dict,
    "profile_context": dict,
    "customer_context": dict,
    "store_scope": dict,
    "appointment_context": dict,
    "sent_message_summary": dict,
}
```

现有字段可继续兼容保留，但 planner/reply 新 payload 优先使用分区字段。

### 文件级改法

#### `layer_nodes.py`

`create_background_context_layer()` 内部改成明确 substep：

1. `memory_load`
2. `identity_load`
3. `conversation_fetch`
4. `customer_context_load`
5. `store_scope_load`
6. `store_snapshot_hydrate`
7. `sent_message_summary_build`

每个 substep 只返回：

- `status`
- `duration_ms`
- `source`
- `used`
- `missing`
- `error`
- `summary`

不得把 substep 的内部异常抛到主链路。

#### `conversation_history_fetch.py`

职责固定为：

- 平台 fetch。
- 根据时间字段排序。
- 取最近 20 条。
- 无时间字段时保留接口原序。
- 失败时使用请求自带历史 fallback。

输出：

```python
{
    "conversation_history": list[str],
    "conversation_fetch": {
        "status": "success|failed|skipped|empty",
        "limit": 20,
        "message_count": int,
        "used_message_count": int,
        "missing": list[str],
        "error": str,
        "source": "platform|request_fallback",
    }
}
```

禁止：

- 清理真实客户消息。
- 按业务语义删除“人呢/可以/这家”等短消息。

#### `customer_store_knowledge.py`

职责固定为门店范围事实：

- 平台门店 scope。
- 本地 store snapshot hydrate。
- canonical store id 规范化。
- scope fetch 失败时 stale cache 标记。

不得输出：

- `current_store_to_use`
- `preferred_store_to_reply`
- 客户可见门店话术。

门店事实输出必须带来源：

- `source=request`
- `source=current_message`
- `source=recent_history`
- `source=appointment_cache`
- `source=customer_scope`
- `source=store_snapshot`
- `source=profile_preference`

#### `platform_agent_client.py`

只负责平台 HTTP：

- 基础 retry。
- timeout。
- token 不入日志。
- 错误摘要。

不得在这里做业务解释。

### 验收场景

- 平台历史接口失败：主回复继续，`conversation_fetch.status=failed`。
- 门店 scope 失败：不能回复“没有门店”，只能让后续工具/模型知道 scope 不完整。
- 身份缺字段：默认身份补全继续，trace 只记缺参。

## 5. 当前轮证据层：current_turn_context

### 当前问题

文件：

- `ai_paths/app/graph/nodes/current_turn_context.py`
- `ai_paths/app/graph/nodes/contextual_short_message.py`
- `ai_paths/app/graph/nodes/store_context.py`
- `ai_paths/app/graph/nodes/sent_message_summary.py`
- `ai_paths/app/graph/state.py`

当前 `current_turn_context.py` 容易沉淀业务结论，例如：

- 当前任务是什么。
- 是否 deposit push。
- 是否 health risk followup。
- 是否 post deposit next step。
- 客户该怎么回。

这些应回到 planner 模型。

### 重构目标

把 `current_turn_context` 改为 `turn_evidence`：只整理证据，不做流程决定。

保留对外函数名 `build_current_turn_context()` 以降低改动面，但返回结构改成 evidence 风格。

### 建议拆分文件

#### `turn_evidence_history.py`

职责：

- 识别是否短消息。
- 识别是否指代消息。
- 提取最近客服动作。
- 提取最近客户动作。
- 生成最近 3-5 轮结构摘要。

输出：

```python
{
    "is_short_message": bool,
    "is_deictic_message": bool,
    "recent_assistant_action": str,
    "recent_customer_action": str,
    "recent_turns_summary": list[dict],
}
```

允许少量固定识别，因为它不是业务判断，只是文本形态和角色动作整理。

#### `turn_evidence_store.py`

职责：

- 汇总当前消息、request、历史、门店卡、预约缓存、画像中的门店证据。
- 标记冲突。
- 标记来源优先级，但不决定最终门店。

输出：

```python
{
    "candidates": list[{
        "store_id": str,
        "store_name": str,
        "source": str,
        "confidence": "high|medium|low",
        "recency": str,
    }],
    "unique_recent_store": dict,
    "conflicts": list[dict],
    "profile_preference_only": bool,
}
```

禁止：

- 直接输出“本轮应该用某门店”。

#### `turn_evidence_payment.py`

职责：

- 汇总 payment_collection 是否发过。
- 汇总卡片金额。
- 汇总客户近轮关于已付、没收到、入口打不开、再发等原文片段。
- 汇总同行人数证据。

输出：

```python
{
    "sent_payment_collections": list[dict],
    "latest_payment_amount": int | None,
    "participant_count_evidence": list[dict],
    "payment_text_evidence": list[dict],
    "structured_payment_state": str,
}
```

禁止：

- 直接判定 `deposit_paid`。
- 直接判定 `send_now`。

结构化支付系统事实除外，例如真实订单支付状态为 paid，可以输出 `structured_payment_state=paid`。

#### `turn_evidence_appointment.py`

职责：

- 汇总客户提到的日期、时间、时段。
- 汇总 request/appointment_cache/工具事实里的预约 ID、预约时间。
- 汇总 available_time 事实。

输出：

```python
{
    "time_mentions": list[dict],
    "date_mentions": list[dict],
    "appointment_records": list[dict],
    "available_time_facts": list[dict],
    "missing_for_available_time": list[str],
}
```

禁止：

- 直接判定“可以约”。
- 直接判定“已留位”。

#### `turn_evidence_risk.py`

职责：

- 汇总当前消息和近轮里的健康、投诉、退款、付款异常、强烈不满证据。
- 标记当前轮是否再次明确提及。
- 标记旧画像风险是否只是背景。

输出：

```python
{
    "current_risk_evidence": list[dict],
    "recent_risk_evidence": list[dict],
    "profile_risk_background": list[dict],
    "risk_conflicts": list[dict],
}
```

禁止：

- 直接决定输出 `human_handoff_notice`。

### `state.py`

新增或兼容字段：

- `turn_evidence: dict[str, Any]`

保留 `current_turn_context` 一段时间用于兼容，最终 planner/reply payload 统一改用 `turn_evidence`。

### 验收场景

- `人呢` 有 short evidence，但 planner 决定如何承接。
- `我已经付了` 只形成 payment evidence，planner 输出 `payment_state=customer_claimed_paid`。
- 旧画像心脏病不会让代码强制 risk hold。
- 当前再次说过敏/脸肿，risk evidence 明确给 planner。

## 6. planner_brain

### 当前文件

- `ai_paths/app/graph/nodes/planner_nodes.py`
- `ai_paths/app/graph/planner/brain_v2.py`
- `ai_paths/app/graph/planner/brain_v2_prompts.py`
- `ai_paths/app/graph/planner/brain_v2_normalizer.py`
- `ai_paths/app/graph/planner/runtime_plan.py`
- `ai_paths/app/graph/planner/planner_contract.py`

### `planner_nodes.py`

职责：

- 调用 planner。
- 记录 trace。
- 模型不可用时走最小安全 fallback。

不做：

- 不在 node 层改业务字段。
- 不在 node 层生成客户可见话术。

### `brain_v2.py`

职责：

- 构造 planner payload。
- 调用 planner 模型。
- 调用 repair 模型。
- 记录 model usage。

改法：

- `_planner_payload_for_model()` 中把 `current_turn_context` 改为 `turn_evidence`。
- repair payload 只包含 compact plan、violations、turn evidence、tool facts，不包含长篇半成品话术。
- repair 后如果还有 violation，保留 violation 给后续节点，不直接吞为 `no_reply`。

### `brain_v2_prompts.py`

重写结构：

1. Role
2. Input Map
3. Fact Priority
4. Tool Map
5. Business Intent Policy
6. Payment Policy
7. Store Policy
8. Appointment Policy
9. Risk Notice Policy
10. Output Schema
11. Examples

必须保留的业务硬边界：

- S10 客户可见说“淡斑活动/周年庆活动”，不说内部代号。
- 周年庆活动价 268。
- 预约金每人 10，1/2/3/4 人为 10/20/30/40。
- 预约金口径：到店抵扣，不做退10元。
- 发过卡不等于已付。
- 客户声称已付时不重复发卡，推进门店/时间/姓名电话/检测。
- `available_time` 只说明可看时段，不代表已预约、已留位、已锁定。
- 门店地址、停车、营业时间必须来自工具事实。
- 距离不输出公里、分钟、车程。
- 健康/投诉/退款/付款异常输出 `human_handoff_notice`，客户可见文本正面承接，不说转人工。

### `brain_v2_normalizer.py`

建议拆分为：

- `planner_schema_normalizer.py`
- `planner_tool_policy.py`
- `planner_payment_guard.py`
- `planner_store_guard.py`
- `planner_appointment_guard.py`
- `planner_message_guard.py`

如果先不拆文件，也要在文件内部按上述段落重排。

允许的 normalizer 行为：

- 修正 message order。
- 兼容旧 `human_handoff` 为 `human_handoff_notice`。
- 规范 payment_collection amount。
- planner 明确 `payment_action=send_now` 且漏卡时补结构卡。
- planner 输出结构和状态冲突时删除结构卡并记录 constraint。
- 工具参数不合法时生成 violation。

禁止的 normalizer 行为：

- 生成大段客户可见销售话术。
- 用关键词决定客户属于哪个销售阶段。
- 把短消息强制改成预约金推进。
- 把旧健康风险强制改成本轮风险。

### `runtime_plan.py`

职责：

- 只提供读取 planner 输出的 helper。
- 不做业务 fallback。

### `planner_contract.py`

职责：

- 维护 allowed tools。
- 维护输出 schema 常量。

### planner 验收

- 当前明确问题不能被 repair 修成 `no_reply`。
- planner 可以根据 evidence 判断已付/重发/入口失败，但代码不抢判断。
- `5点有空吧` 有门店和日期时应走 `available_time`。
- `我还有朋友一起` 是否发卡由 planner 的 `payment_action` 决定。

## 7. execute_actions

### 当前文件

- `ai_paths/app/graph/nodes/action_nodes.py`
- `ai_paths/app/graph/nodes/action_module_outputs.py`
- `ai_paths/app/graph/nodes/action_task_results.py`
- `ai_paths/app/graph/nodes/appointment_utils.py`
- `ai_paths/app/graph/nodes/appointment_time_utils.py`
- `ai_paths/app/services/store_service.py`
- `ai_paths/app/services/store_snapshot_service.py`
- `ai_paths/app/services/coze_client.py`

### `action_nodes.py`

当前文件过大，建议拆分工具执行函数。

目标结构：

```python
execute_actions()
  -> normalize_required_tools()
  -> execute_store_lookup()
  -> execute_distance_calculate()
  -> execute_available_time()
  -> execute_case_studies()
  -> execute_professional_assist()
  -> build_planner_fact_output()
```

工具执行统一返回：

```python
{
    "status": "success|empty|failed|skipped",
    "source": str,
    "facts": list|dict,
    "missing": list[str],
    "error": str,
}
```

### `action_module_outputs.py`

职责：

- 工具结果转 `fact_envelope`。
- 不产出客服 reply points。

目标 `fact_envelope`：

```python
{
    "usable_facts": list[str],
    "missing_facts": list[dict],
    "risky_facts": list[dict],
    "unsupported_claims": list[dict],
    "structured_facts": {
        "store_facts": list[dict],
        "recommended_store": dict,
        "price_facts": list[dict],
        "case_facts": list[dict],
        "appointment_facts": list[dict],
        "professional_assist": dict,
        "tool_errors": list[dict],
    }
}
```

关键边界：

- `distance_calculate` 数值只内部排序，不进入客户可复述字段。
- `available_time` 事实不能变成 `appointment_confirmed`。
- 只有真实创建/确认预约才写 `appointment_created/appointment_confirmed`。

### `appointment_time_utils.py`

职责：

- 时间解析。
- available slots 汇总。
- target time 是否可约。
- recommended/backup slot 选择。

不做：

- 不生成“帮您留”文案。

### `store_snapshot_service.py`

职责：

- 本地门店目录事实。
- canonical store id。
- 同名门店消歧。

不做：

- 不替客户选择门店。

### execute_actions 验收

- customer_store_lookup 失败写 `tool_errors`，不让接口 502。
- available_time 失败写 `missing/error`，reply 只能问缺失字段或说明暂未查到。
- case_studies 无图时不编图。

## 8. synthesize_reply

### 当前文件

- `ai_paths/app/graph/nodes/reply_nodes.py`
- `ai_paths/app/graph/nodes/reply_context.py`
- `ai_paths/app/graph/nodes/reply_input.py`
- `ai_paths/app/graph/nodes/reply_validation.py`
- `ai_paths/app/prompts/reply_synthesizer.py`

### `reply_context.py`

目标 payload：

```python
{
    "current_message": str,
    "conversation_history": list[str],
    "turn_evidence": dict,
    "planner": {
        "decision": str,
        "stage": str,
        "conversion_stage": str,
        "payment_state": str,
        "payment_action": str,
        "required_tools": list,
        "constraints": list,
    },
    "tool_facts": dict,
    "business_rules": dict,
    "hard_constraints": list[str],
    "sent_message_summary": dict,
}
```

移除或禁止进入 final payload：

- planner `tool_plan` 长文本。
- planner `known_info` 半成品。
- `reply_points`。
- `sample_reply`。
- 客户可见模板句。

### `reply_synthesizer.py`

目标：

- 变成少量稳定规则。
- 不再堆叠数百条场景 if/else。

核心结构：

1. Persona
2. Reply Priorities
3. Message Types
4. Fact Boundaries
5. Payment Rules
6. Store Rules
7. Appointment Rules
8. Effect/Case Rules
9. Risk Notice Rules
10. Style Rules
11. Output Schema

必须保留：

- 回复短、像微信销售。
- 当前问题优先。
- 效果问题先肯定，再案例，再检测。
- 预约金口径和金额一致。
- 不说退10元。
- 不说公里分钟。
- 不说已预约成功，除非有预约确认事实。

### `reply_validation.py`

分层函数：

- `validate_message_schema`
- `validate_payment_collection_consistency`
- `validate_store_fact_consistency`
- `validate_appointment_fact_consistency`
- `validate_image_fact_consistency`
- `validate_risk_notice_schema`
- `validate_forbidden_absolute_claims`

硬 raise 条件：

- 文本承诺发入口但无 payment_collection。
- payment_collection 金额与文本不一致。
- store_address id 不来自事实。
- 文本说地址/停车/营业时间但无事实。
- 文本说具体可约/已预约/已留位但无对应 appointment fact。
- image URL 不来自 case_facts 或 activity rule。
- 绝对效果/安全承诺。

warning 或 repair hint 条件：

- 风格偏长。
- 话术偏机械。
- 问题太多。
- 销售推进弱。

### `reply_nodes.py`

目标处理顺序：

1. 如果 planner direct_reply 合法，直接用。
2. 如果 direct_reply 不合法，进入 reply model。
3. reply model 校验失败，带原因 retry 一次。
4. retry 仍失败，尝试结构性 deterministic fallback。
5. postprocess 后为空，再进入非业务通用兜底。
6. 永不因后处理清空直接 502。

非业务通用兜底只能是：

```text
我在，继续帮您处理。
```

或同等中性短句，不根据业务场景写复杂模板。

### synthesize_reply 验收

- 明确客户问题不空回复。
- 模型输出被清空后有兜底。
- 不把 `available_time` 说成已留位。
- 不把历史 payment_collection 说成入口状态。

## 9. 后台 profile_event_extractor

### 当前文件

- `ai_paths/app/graph/nodes/profile_nodes.py`
- `ai_paths/app/prompts/profile_analyzer.py`
- `ai_paths/app/services/memory_store.py`

### 重构目标

画像只记录长期事实，不主导当前轮。

### `profile_nodes.py`

职责：

- 异步执行。
- 拉近 50 条历史。
- 降级使用主链路历史。
- 保存画像/事件。

不得：

- 阻塞主回复。
- 把本轮系统消息、自动开场消息写入画像。

### `profile_analyzer.py`

提示词增加：

- 当前消息和近轮事实优先。
- 旧健康风险只做背景。
- preferred_store 只是偏好，不覆盖当前门店。
- 不总结系统提示、工具、内部 notice。

### `memory_store.py`

保存时记录：

- `source`
- `confidence`
- `updated_at`
- `valid_from`

### profile 验收

- 旧健康风险不会长期触发 handoff。
- 画像 preferred_store 不覆盖当前消息真实门店。
- 画像任务失败不影响主回复。

## 10. SOP 模块

### 当前文件

- `ai_paths/app/services/sop_event_service.py`
- `ai_paths/app/services/sop_execution_service.py`
- `ai_paths/app/services/sop_reply_pack_service.py`
- `ai_paths/app/services/sop_message_sanitizer.py`
- `ai_paths/app/services/outreach_service.py`
- `config/sop_reply_packs.json`

### `sop_event_service.py`

职责：

- 接收 `/sop/events`。
- 解析 event。
- 判断候选 SOP 包。
- 去重。
- 返回任务结果。

重构重点：

- 首次加微 immediate/schedule 和 chat gate 共用客户维度 send_once。
- 自动开场消息不触发 SOP，也不触发普通 AI。
- no_candidate 不是错误，日志显示 skipped。

### `sop_execution_service.py`

职责：

- 执行发送。
- 记录 event/task log。
- 调用 outreach send。

重构重点：

- 同客户同 dedupe key 并发只发一次。
- 发送失败记录平台错误摘要。
- 不把模型 no_content 判成接口错误。

### `sop_reply_pack_service.py`

职责：

- 加载配置。
- 保存配置。
- 管理启用/禁用。

增加配置预检：

- enabled pack 必须有消息。
- message type 合法。
- image/video URL 非空。
- payment_collection amount 合法。
- send_once_key 合法。
- 执行范围合法。

### `sop_message_sanitizer.py`

职责：

- 静态包发送前结构校验。
- message type 兼容。
- 媒体 URL 检查。
- 预约金口径统一。

预约金动态金额：

- 若 SOP 包里有 payment_collection，发送前根据 conversation/history evidence 计算 10/20/30/40。
- 超过 4 人不自动发卡，改为文本确认人数。

### SOP 验收

- 首次加微只发送 `s10_new_customer_opening` 一次。
- 客户未回复时后续 SOP 不误判为重复内容。
- 同客户跨两个执行范围共享计数。
- SOP 静态包不再出现“可退/不满意退”等冲突口径。

## 11. 分批实现顺序

### Phase 0：设计文档和快照

- 已提交旧版快照。
- 新增本设计文档。

### Phase 1：turn evidence 收口

提交名：

```text
refactor: 收窄当前轮上下文为事实证据
```

文件：

- `current_turn_context.py`
- `contextual_short_message.py`
- `store_context.py`
- `state.py`
- 新增 `turn_evidence_*.py`
- 对应测试

验收：

- current_turn_context 不输出客户可见话术。
- 不输出强制业务任务。
- planner payload 包含 evidence。

### Phase 2：planner normalizer 收口

提交名：

```text
refactor: 收口 planner normalizer 职责
```

文件：

- `brain_v2.py`
- `brain_v2_normalizer.py`
- `brain_v2_prompts.py`
- `runtime_plan.py`
- `planner_contract.py`
- planner 测试

验收：

- normalizer 不生成复杂业务话术。
- repair 不逃成 no_reply。
- payment_action 仍能控制发卡。

### Phase 3：工具事实统一

提交名：

```text
refactor: 统一工具事实输出
```

文件：

- `action_nodes.py`
- `action_module_outputs.py`
- `appointment_time_utils.py`
- `customer_store_knowledge.py`
- 工具事实测试

验收：

- 工具失败进入 tool_errors。
- distance 不暴露公里分钟。
- available_time 和 appointment_confirmed 分清。

### Phase 4：最终回复输入和校验收口

提交名：

```text
refactor: 简化最终回复输入和硬边界校验
```

文件：

- `reply_context.py`
- `reply_nodes.py`
- `reply_validation.py`
- `reply_synthesizer.py`
- 回复测试

验收：

- final payload 不含半成品话术。
- validation 只硬拦事实错误。
- 不再 502/空回复。

### Phase 5：画像和 SOP 收口

提交名：

```text
refactor: 收口画像和SOP事实边界
```

文件：

- `profile_nodes.py`
- `profile_analyzer.py`
- `sop_event_service.py`
- `sop_execution_service.py`
- `sop_reply_pack_service.py`
- `sop_message_sanitizer.py`
- SOP 测试

验收：

- SOP 和普通聊天口径一致。
- 画像不覆盖当前事实。

### Phase 6：全流程回归矩阵

提交名：

```text
test: 增加新客全流程语义回归矩阵
```

覆盖五类：

- 新客意向/效果/信任。
- 门店/地址/距离/指代。
- 预约/预约金/同行人数。
- 健康风险/投诉退款/付款异常。
- SOP/平台入口/历史承接。

## 12. 每批默认验证

每个提交前必须执行：

```bash
git diff --check
python -m py_compile <本批改动文件>
PYTHONPATH=ai_paths python -m pytest \
  workflow_tests/test_reply_output_strategy.py \
  workflow_tests/test_reply_synth_retry.py \
  workflow_tests/test_store_scope_resilience.py \
  workflow_tests/test_prompt_refactor_contract.py \
  workflow_tests/test_platform_reply_runtime.py \
  workflow_tests/test_sop_event_flow.py -q
```

如果某批不涉及 SOP，可先不跑 SOP 测试，但最终合并前必须跑。

## 13. 风险和控制

主要风险：

- 收窄 Python 业务判断后，短期内模型输出波动变大。
- 提示词重写可能影响已修好的具体场景。
- current_turn_context 返回结构变化可能破坏 planner/reply payload。
- SOP 静态包 sanitizer 收紧后，旧配置可能暴露更多错误。

控制方式：

- 每批只改一个层级。
- 保留旧字段兼容至少一个阶段。
- 所有新结构先双写：旧字段继续输出，新字段进入 payload。
- 通过回归矩阵观察模型是否真的接住历史。
- 硬边界校验不放松，风格/节奏只做 warning 和 repair。

## 14. 最终成功标准

重构完成后应达到：

- 简单客服问题不再因为系统内部冲突答错。
- 历史承接稳定，但旧历史不会压倒当前问题。
- 门店、距离、档期、预约金都有唯一事实边界。
- 预约金是否发送由 planner 语义决定，代码只保证金额和结构正确。
- `available_time` 不再被说成已留位或预约成功。
- 效果疑问回复先给信心，再用案例和到店检测闭环。
- 高风险场景输出 text + `human_handoff_notice`，普通售前顾虑不误触发。
- 模型输出再差也不应造成 502 或空回复。
