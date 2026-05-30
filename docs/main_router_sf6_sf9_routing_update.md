# 主路由 SF6/SF9 分流修改稿

本文用于更新 Coze 工作流中「主路由判断器」的大模型提示词，目标是把“门店匹配”和“预约查询/邀约”分清楚。

## 修改目标

当前预约链路必须满足：

- 没有明确城市、门店或 `store_id` 时，不进入 `SF9_appointment` 查询可约时间。
- 预约前缺门店时，先进入 `SF6_store_match`。
- 已经明确门店或已有 `store_id` 时，才进入 `SF9_appointment`。
- 查询已有预约、取消预约、改约这类依赖客户订单/预约记录的场景，进入 `SF9_appointment`。
- 地址、门店、路线、停车、附近门店统一进入 `SF6_store_match`。

## 需要新增到输入变量

建议在主路由输入变量中增加以下可选字段：

```text
confirmed_store_id
confirmed_store_name
detected_city
store_match_result
```

字段说明：

- `confirmed_store_id`：上游或历史中已确认的门店 ID，可为空。
- `confirmed_store_name`：上游或历史中已确认的门店名称，可为空。
- `detected_city`：客户当前消息或档案中识别到的城市，可为空。
- `store_match_result`：最近一次 SF6 门店匹配结果，可为空。

如果当前工作流暂时没有这些字段，也可以先不传。主路由仍然可以基于 `content`、`conversation_history`、`portrait`、`customer_basic_info`、`history_events` 判断。

## 替换「一、输入变量」

```text
content
current_message
message_type
conversation_history
portrait
history_events
latest_lifecycle_stage
appointment_info
customer_basic_info

可选输入：
pending_intent
last_bot_question_type
confirmed_store_id
confirmed_store_name
detected_city
store_match_result
```

## 新增「预约前置门店分流规则」

建议放在「上下文承接与槽位补全规则」之后、「短句判断规则」之前。

```text
====================
预约前置门店分流规则
====================

你必须区分“门店匹配”和“预约处理”。

SF6_store_match 负责：
- 门店查询
- 地址查询
- 附近门店
- 路线
- 停车
- 营业时间
- 预约前缺少城市/门店时的门店补全

SF9_appointment 负责：
- 查询客户已有预约
- 查询某个明确门店某天是否有可约时间
- 创建预约前的信息确认
- 改约前的信息确认
- 取消预约前的信息确认
- 到店前轻量问题

必须遵守：

1. 客户当前消息是门店/地址/附近/路线/停车问题时，进入 SF6_store_match。

触发表达包括：
- 门店
- 地址
- 在哪里
- 哪家近
- 附近有吗
- 有停车吗
- 怎么过去
- 地铁怎么走
- 导航发我
- 营业到几点

2. 客户有预约意图，但当前没有明确城市、门店、store_id 时，进入 SF6_store_match。

原因：
查询可预约时间必须先有明确门店 ID。没有门店时不能进入 SF9 直接判断可约时间。

示例：
客户说：“周六能去吗”
如果历史和档案中没有明确门店：
intent = appointment_intent
subflow = SF6_store_match
reason 必须说明：客户想预约，但缺少门店信息，需要先匹配门店。

客户说：“我想过去看看”
如果没有城市/门店：
intent = appointment_intent
subflow = SF6_store_match

客户说：“明天下午可以到店吗”
如果没有城市/门店：
intent = appointment_intent
subflow = SF6_store_match

3. 客户有预约意图，且已经明确门店或已有 confirmed_store_id 时，进入 SF9_appointment。

示例：
客户说：“厦门思明店周六下午能约吗”
intent = appointment_intent
subflow = SF9_appointment

客户说：“就约厦门思明店”
intent = appointment_intent 或 appointment_confirm
subflow = SF9_appointment

客户说：“刚刚你发的厦门思明店，周六下午有时间吗”
如果历史中最近一次门店匹配结果包含厦门思明店：
intent = appointment_intent
subflow = SF9_appointment

4. 客户查询已有预约，进入 SF9_appointment。

触发表达包括：
- 我有没有预约
- 我约的是几点
- 我之前约了吗
- 帮我查一下预约
- 我预约的是哪家店
- 我预约成功了吗

即使当前没有门店，也进入 SF9_appointment，因为这类问题是查询客户已有预约，不是查询某门店可约时间。

5. 客户改约或取消预约，进入 SF9_appointment。

触发表达包括：
- 改时间
- 改到周日
- 换个时间
- 取消预约
- 明天不去了
- 帮我取消

注意：
主路由只负责进入 SF9。是否真的取消/改约，必须由后续预约接口确认，主路由不要输出客服话术。

6. 客户问到店前准备问题，进入 DIRECT_REPLY 或 SF9_appointment。

如果只是通用问题，优先 DIRECT_REPLY：
- 去要带什么
- 能不能化妆
- 要不要空腹
- 到店要做多久

如果问题同时带明确预约信息，例如“我明天去厦门思明店要带什么”，可进入 SF9_appointment。

7. 地址和路线优先 SF6。

即使客户已经预约，只要当前消息主要是“地址发我、怎么去、有停车吗”，也优先进入 SF6_store_match。
```

## 修改「判断优先级」

把原判断优先级替换为：

```text
必须按以下顺序判断：

1. 读取当前消息，确认当前轮客户真实表达。
2. 判断当前消息是否命中强制转人工规则。
3. 判断当前消息是否为门店/地址/路线/停车问题；命中则优先 SF6_store_match。
4. 判断当前消息是否为预约意图。
   - 如果是查询已有预约、改约、取消预约，进入 SF9_appointment。
   - 如果是想预约/问某天能不能去，但缺少明确门店或 store_id，进入 SF6_store_match。
   - 如果是想预约/问某天能不能去，且已有明确门店或 store_id，进入 SF9_appointment。
5. 判断当前消息是否为短句补槽或承接上一轮意图。
6. 提取当前轮相关项目名称 project_namelist。
7. 根据 latest_lifecycle_stage 和 appointment_info 判断当前场景。
8. 根据当前消息或继承意图判断 intent。
9. 根据“场景 + 意图 + 门店是否明确 + 预约信息”选择子流程。
10. 如果意图不清晰，选择 SF11_emotion_companion 或 DIRECT_REPLY 做轻量澄清，不要乱进业务子流程。
```

## 修改「意图分类」

建议保留原意图分类，并补充：

```text
appointment_intent:
想预约、想过去、什么时候能去、周几到店可以吗、明天能去吗、周末可以吗。
注意：如果只是想预约但没有明确门店，应先进入 SF6_store_match。

appointment_query:
查询已有预约、预约时间、预约门店、是否预约成功。
如果当前提示词不方便新增 intent，可继续使用 appointment_confirm，但 reason 里必须说明是查询已有预约。
```

如果不想新增 `appointment_query`，则不要改输出枚举，统一放到 `appointment_confirm`。

## 修改「场景 + 意图到子流程规则」

在所有场景中的 `appointment_intent -> SF9_appointment` 前补充前置条件：

```text
appointment_intent:
- 如果当前消息已有明确门店、confirmed_store_id、confirmed_store_name，或历史最近一轮已确认门店 -> SF9_appointment
- 如果当前消息没有明确门店，且历史/档案/appointment_info 中也没有可用门店 -> SF6_store_match

appointment_confirm / appointment_change / appointment_cancel:
- 始终进入 SF9_appointment

store_inquiry:
- 始终进入 SF6_store_match

pre_visit_question:
- 通用到店前问题 -> DIRECT_REPLY
- 带明确预约/门店上下文的到店前问题 -> SF9_appointment
```

## 新增「特殊纠偏规则」

建议放在原特殊纠偏规则末尾。

```text
8. 当前消息是“周六能去吗 / 明天能去吗 / 周末可以过去吗 / 我想过去看看”
如果没有明确城市、门店、confirmed_store_id 或最近门店匹配结果：
intent = appointment_intent
subflow = SF6_store_match
reason = 客户有预约意图，但查询可约时间前必须先确认门店。

9. 当前消息是“厦门思明店周六能约吗 / 就约厦门思明店 / 刚刚那家周六可以吗”
如果当前消息或历史中有明确门店：
intent = appointment_intent
subflow = SF9_appointment
reason = 客户已有明确门店和预约时间意向，可以进入预约查询/确认流程。

10. 当前消息是“我有没有预约 / 我约的是几点 / 帮我查下预约 / 我预约成功了吗”
intent = appointment_confirm
subflow = SF9_appointment
reason = 客户在查询已有预约信息，需要进入预约查询流程。

11. 当前消息是“帮我取消 / 明天不去了 / 改到周日 / 换个时间”
intent = appointment_cancel 或 appointment_change
subflow = SF9_appointment
reason = 客户在取消或改约，需要先查询预约记录再处理。

12. 当前消息是“地址发我 / 怎么过去 / 有停车吗 / 哪家离我近”
intent = store_inquiry 或 pre_visit_question
subflow = SF6_store_match
reason = 客户当前主要需求是门店地址/路线/停车，应进入门店匹配流程。
```

## 输出示例

### 1. 有预约意图但缺门店

输入：

```text
content = "周六下午能去吗"
conversation_history = []
customer_basic_info = {}
confirmed_store_id = ""
```

输出：

```json
{
  "scene": "S4_appointment_negotiating",
  "intent": "appointment_intent",
  "subflow": "SF6_store_match",
  "reason": "客户当前消息询问周六下午能否到店，属于预约意图，但当前缺少明确城市或门店，查询可约时间前需要先进入门店匹配。",
  "confidence": 0.94,
  "project_namelist": ["暂无"],
  "need_human": false
}
```

### 2. 有明确门店和时间

输入：

```text
content = "厦门思明店周六下午能约吗"
```

输出：

```json
{
  "scene": "S4_appointment_negotiating",
  "intent": "appointment_intent",
  "subflow": "SF9_appointment",
  "reason": "客户当前消息明确提到厦门思明店和周六下午，已有门店和时间意向，可以进入预约查询流程。",
  "confidence": 0.96,
  "project_namelist": ["暂无"],
  "need_human": false
}
```

### 3. 查询已有预约

输入：

```text
content = "我约的是几点来着"
```

输出：

```json
{
  "scene": "S5_appointed_waiting",
  "intent": "appointment_confirm",
  "subflow": "SF9_appointment",
  "reason": "客户当前消息是在查询已有预约时间，需要进入预约查询流程。",
  "confidence": 0.95,
  "project_namelist": ["暂无"],
  "need_human": false
}
```

### 4. 地址路线

输入：

```text
content = "地址发我，我直接过去"
```

输出：

```json
{
  "scene": "S4_appointment_negotiating",
  "intent": "store_inquiry",
  "subflow": "SF6_store_match",
  "reason": "客户当前消息要求发送地址，主要需求是门店地址和路线信息，应进入门店匹配流程。",
  "confidence": 0.95,
  "project_namelist": ["暂无"],
  "need_human": false
}
```

## 回归测试用例

| 输入 | 预期子流程 |
|---|---|
| 周六能去吗 | SF6_store_match |
| 明天下午可以到店吗 | SF6_store_match |
| 我想过去看看 | SF6_store_match |
| 厦门思明店周六能约吗 | SF9_appointment |
| 就约厦门思明店 | SF9_appointment |
| 刚刚那家周六下午有时间吗 | SF9_appointment |
| 我有没有预约 | SF9_appointment |
| 我约的是几点 | SF9_appointment |
| 帮我取消明天预约 | SF9_appointment |
| 我想改到周日 | SF9_appointment |
| 地址发我 | SF6_store_match |
| 有停车吗 | SF6_store_match |
| 到店要带什么 | DIRECT_REPLY |
| 我明天去厦门思明店要带什么 | SF9_appointment |

