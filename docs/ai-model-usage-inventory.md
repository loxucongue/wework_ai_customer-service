# AI 大模型调用清单

本文档记录当前 AI Paths 服务里会调用大模型的主要节点，包括普通客服回复链路和主动唤醒链路。实际模型供应商、模型名由 `ModelClient` 和环境变量配置决定，业务代码按 `tier` 选择模型档位。

## 普通客服回复链路

### 1. 图片理解模型

- 代码位置：`ai_paths/app/graph/nodes/input_nodes.py`
- 调用方式：`model_client.vision_json(...)`
- 模型档位：`vision`
- 任务目的：当本轮有真实图片时，识别图片里的可见皮肤问题或客户上传内容，为 planner 和最终回复提供结构化图片事实。
- 主要输入：
  - 当前客户消息
  - 图片 URL
  - 最近对话上下文
  - 图片理解提示词
- 主要输出：
  - `has_image`
  - `image_type`
  - `visible_concerns`
  - `image_desc`
  - `confidence`
- 边界：只作为表层可见信息，不做诊断、不承诺效果。

### 2. Planner Brain

- 代码位置：`ai_paths/app/graph/nodes/planner_brain_v2.py`
- 调用方式：`model_client.chat_json(...)`
- 模型档位：`planner`
- 任务目的：判断本轮是否回复、是否需要工具、属于哪个业务阶段，并给出工具调用计划或直接回复草案。
- 主要输入：
  - 当前客户消息
  - 最近对话
  - 图片结构化信息
  - 客户画像和历史事件
  - 客户上下文，如预约、订单、已确认门店
  - 可用工具列表
  - 业务规则和 planner 系统提示词
- 主要输出：
  - `decision`: `direct_reply` / `need_tools` / `no_reply`
  - `stage`
  - `sub_rule_id`
  - `reply_messages`
  - `tool_calls`
  - `handoff`
- 边界：只输出 JSON，不直接暴露工具名、内部分析或系统语言给客户。

### 3. Planner 修复模型

- 代码位置：`ai_paths/app/graph/nodes/planner_brain_v2.py`
- 调用方式：`model_client.chat_json(...)`
- 模型档位：`planner`
- 任务目的：当 planner 输出不符合结构、工具契约或安全约束时，按违规原因修复为可执行 JSON。
- 主要输入：
  - 原 planner 输出
  - 违规原因
  - 当前请求上下文
  - planner 修复提示词
- 主要输出：
  - 修复后的 planner JSON
- 边界：只做结构和契约修复，不新增没有事实来源的业务事实。

### 4. 最终回复合成模型

- 代码位置：`ai_paths/app/graph/nodes/reply_synthesizer.py`
- 调用方式：`model_client.chat_json(...)`
- 模型档位：`reply`
- 任务目的：在工具结果和事实已经齐备后，生成最终客户可见回复。
- 主要输入：
  - 当前客户消息
  - 必要历史上下文
  - planner 结论
  - 工具事实结果
  - 图片事实
  - 客户画像、历史事件、预约或订单上下文
  - 最终回复提示词
- 主要输出：
  - `reply_messages`
  - 支持类型：`text`、`image`、`payment_collection`、必要时 `human_handoff`
- 边界：不能编造价格、门店、档期、订单、案例和效果；预约金入口使用 `payment_collection`。

### 5. 客户画像和事件抽取模型

- 代码位置：`ai_paths/app/graph/nodes/profile_analyzer.py`
- 调用方式：`model_client.chat_json(...)`
- 模型档位：`fast`
- 任务目的：每轮从客户消息、回复和工具事实中抽取可长期复用的客户画像、基础信息和历史事件。
- 主要输入：
  - 当前客户消息
  - 最近对话
  - 本轮回复消息
  - 已有客户画像和基础信息
  - 已有历史事件
  - planner 任务信息
  - 工具结果和事实摘要
- 主要输出：
  - `profile_update`
  - `basic_info_update`
  - `event_updates`
- 边界：只持久化客户可见事实、明确表达和结构化事件，不保存系统提示词或内部推理。

## 主动唤醒链路

### 6. 主动唤醒计划模型

- 代码位置：`ai_paths/app/services/outreach_service.py`
- 调用方式：`model_client.chat_json(...)`
- 模型档位：`balanced`
- 任务目的：基于沉默客户上下文判断是否需要创建主动唤醒计划，并拆成最多 3 个待执行任务。
- 主要输入：
  - `customer_id`
  - `corp_id`
  - `user_id`
  - `wechat`
  - `external_userid`
  - 客户记忆和最近消息
  - 当前阶段
  - 业务目标
  - S10 活动上下文
- 主要输出：
  - `should_create_plan`
  - `customer_stage`
  - `stall_reason`
  - `customer_psychology`
  - `plan_goal`
  - `steps`
- 边界：只生成计划和任务，不直接发送消息。

### 7. 主动唤醒消息生成模型

- 代码位置：`ai_paths/app/services/outreach_service.py`
- 调用方式：`model_client.chat_json(...)`
- 模型档位：`balanced`
- 任务目的：为某个主动唤醒任务生成待发送消息；现在既可用于预览，也可用于执行前生成。
- 主要输入：
  - 当前任务信息
  - 所属主动唤醒计划
  - 客户近期上下文
  - S10 活动上下文
  - 主动唤醒消息提示词
- 主要输出：
  - `reply_messages`
  - 支持类型：`text`、`image`、`payment_collection`
- 边界：
  - 不能使用已废弃的 `book_order`
  - 客户已明确要报名、付款入口、交 10 元预约金或锁名额时，预约金入口使用：

```json
{
  "type": "payment_collection",
  "order": 2,
  "content": {
    "amount": 10,
    "remark": ""
  }
}
```

## 当前非模型步骤

- 历史聊天查询：平台 `GET /api/v1/platform-agent/ai-outreach/conversation`
- 主动消息发送：平台 `POST /api/v1/platform-agent/ai-outreach/send`
- 主动发送前复查：调用历史查询接口判断客户是否已回复；失败时记录事件并继续发送
- 人工预览：`POST /admin/outreach/tasks/{task_id}/preview` 只生成并保存待发消息，不触发发送
- 任务执行：`POST /admin/outreach/tasks/{task_id}/execute` 执行发送；平台发送接口读超时按 `accepted_no_response` 记录，避免把 fire-and-forget 请求误判为失败
