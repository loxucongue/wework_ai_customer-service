# AI客服客户系统接口需求说明

版本：v1  
日期：2026-05-26  
用途：供产品/系统开发评审 AI 客服接入预约与订单能力  

## 一、背景

AI 客服当前需要接入客户系统的两类能力：

1. 预约能力：查询客户当前是否已有预约；客户确认后创建预约/开单；客户要求时取消预约。
2. 订单能力：查询客户所有历史订单，用于判断客户在某一项目大类是否属于新客、历史消费项目、以往客单价、复购情况。

已查看现有文档：`企微第三方平台接口文档.md`。

结论：现有接口可以覆盖部分预约动作，但不完全满足 AI 客服需求；订单接口目前偏订单列表/当前订单查询，缺少面向 AI 场景的“客户完整历史订单聚合查询”。

## 二、现有接口能力评估

### 1. 客户识别

现有接口：

`GET /platform_agent/customer/get_customer_info`

能力：

- 可通过企微外部联系人 ID 查询客户信息。
- 返回 `customer_id`、`customer_add_wechat_id`、客户类型、手机号、微信号等。

AI 使用方式：

- AI 工作流应先通过 `corp_id + wechat + external_userid` 获取 `customer_id`。
- 后续预约和订单查询均基于 `customer_id`。

是否满足：基本满足。

### 2. 预约相关

现有相关接口：

| 能力 | 现有接口 | 评估 |
|---|---|---|
| 查询门店/项目/预约金选项 | `GET /platform_agent/option` | 可用 |
| 获取分类预约金 | `GET /platform_agent/category/get_prepay` | 可用 |
| 查询可预约时间 | `GET /order/schedule/available_time` | 可用，但路径需确认是否应加 `/platform_agent` 前缀 |
| 创建预约金订单 | `POST /platform_agent/order/create_work` | 可用，可生成订单 ID |
| 添加排客计划 | `POST /platform_agent/order/schedule/order_plan` | 可用，需要已有订单 ID |
| 改约 | `POST /platform_agent/order/schedule/change_plan_time` | 可用，但参数较少，需确认是否能指定具体时间点 |
| 取消排客 | `POST /platform_agent/order/schedule/cancel_plan` | 可用 |
| 查询订单列表 | `GET /platform_agent/order/index` | 可用于查当前订单，但不适合直接作为 AI 的当前预约聚合接口 |
| 验证是否可创建订单 | `GET /platform_agent/order/check_customer` | 只能判断是否存在进行中订单，不能返回预约详情 |

现有不足：

- 缺少“按客户查询当前有效预约”的直接接口。
- `check_customer` 只能返回 `result=1/0`，无法知道客户预约的门店、时间、项目、订单 ID。
- 创建预约需要组合多个接口：客户信息 -> 可预约时间 -> 创建订单 -> 添加排客，AI 工作流使用复杂，建议后端封装聚合接口。
- 改约接口只看到 `date + order_id`，未明确具体时间字段，需系统确认。
- 取消排客需要 `order_id`，AI 必须先能查到客户当前有效预约订单。

### 3. 订单相关

现有接口：

`GET /platform_agent/order/index`

能力：

- 支持按 `customer_id` 查询订单列表。
- 返回订单状态、预约时间、到店时间、完成时间、门店、分类、金额、`plans`、`buys` 等。
- 文档说明：到店前订单项目取 `plans`，到店后订单项目取 `buys`。

现有问题：

- 当前业务已反馈：文档内查询订单接口只能查到当前进行的订单，无法满足“所有历史订单”查询。
- AI 需要判断“客户在某个项目大类是否新客”，必须看到历史已完成/已取消/已流退/已评价/退款等完整订单范围。
- AI 需要统计历史客单价、历史消费项目、最近消费时间、复购项目，当前接口如果只返回进行中订单则不足。

是否满足：不满足，需要新增或扩展“客户历史订单查询接口”。

## 三、AI客服需要的接口清单

建议系统开发提供 3 个 AI 专用聚合接口，避免 AI 工作流直接拼多个底层接口。

## 1. 查询客户当前预约

### 接口名称

查询客户当前有效预约

### 建议接口

`GET /platform_agent/ai/customer/current_appointment`

### 用途

用于 AI 判断客户是否已有预约、预约状态、预约门店、预约时间，并支持后续取消/改约。

### 请求参数

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| corp_id | string | 是 | 企微企业 ID |
| wechat | string | 是 | 员工企微账号 |
| external_userid | string | 否 | 企微外部联系人 ID |
| customer_id | int | 否 | 客户 ID，若已知可直接传 |

说明：

- `external_userid` 和 `customer_id` 至少传一个。
- 如果传 `external_userid`，后端负责映射 `customer_id`。

### 返回字段

```json
{
  "code": 200,
  "msg": "success",
  "data": {
    "has_appointment": true,
    "customer_id": 9631370,
    "appointment": {
      "order_id": 5613087,
      "order_no": "2603181048074461",
      "status": 3,
      "status_name": "已排客",
      "store_id": 11,
      "store_name": "上海徐汇店",
      "plan_at": 1773805200,
      "plan_time_text": "2026-03-18 10:00",
      "category_id": 110,
      "category_name": "皮肤管理",
      "items": [
        {
          "product_id": 810,
          "specification_id": 9578,
          "product_name": "丝雾眉(高级)",
          "number": 1
        }
      ],
      "prepay_required": "10.00",
      "prepay_paid": "0.00",
      "created_at": 1773802087,
      "can_cancel": true,
      "can_change": true
    }
  }
}
```

### 业务规则

- 当前有效预约建议包含状态：待定中、待排客、已排客、已超时但未取消且未完成的订单。
- 已取消、已完成、已流退不应作为当前有效预约返回。
- 如果有多个有效预约，返回最近一条，并额外返回 `appointments` 数组。

## 2. 创建预约/开单

### 接口名称

AI 创建预约

### 建议接口

`POST /platform_agent/ai/appointment/create`

### 用途

客户确认门店、时间、项目后，由 AI 发起预约。后端内部完成必要校验、创建预约金订单、添加排客。

### 请求参数

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| corp_id | string | 是 | 企微企业 ID |
| wechat | string | 是 | 员工企微账号 |
| external_userid | string | 否 | 企微外部联系人 ID |
| customer_id | int | 否 | 客户 ID |
| customer_add_wechat_id | int | 否 | 加微记录 ID，如后端可自动获取则非必填 |
| user_id | int | 是 | 操作员工 ID |
| store_id | int | 是 | 门店 ID |
| category_id | int | 是 | 意向项目/大类 ID |
| plan_date | string | 是 | 预约日期，`YYYY-MM-DD` |
| plan_time | string | 是 | 预约时间，`HH:mm` |
| prepay | decimal | 是 | 预约金，可为 0 时需确认系统是否允许 |
| remark | string | 否 | 备注，建议记录“AI客服创建”及客户需求 |

### 返回字段

```json
{
  "code": 200,
  "msg": "success",
  "data": {
    "appointment_created": true,
    "order_id": 5613087,
    "order_no": "2603181048074461",
    "store_id": 11,
    "store_name": "上海徐汇店",
    "plan_at": 1773805200,
    "plan_time_text": "2026-03-18 10:00",
    "category_id": 110,
    "category_name": "皮肤管理",
    "prepay_required": "10.00",
    "prepay_paid": "0.00"
  }
}
```

### 后端建议封装逻辑

该接口内部建议按顺序处理：

1. 通过客户标识确认 `customer_id` 和 `customer_add_wechat_id`。
2. 校验客户是否已有进行中订单。
3. 校验门店是否开放预约。
4. 校验门店该日期时间是否可约。
5. 创建预约金订单。
6. 添加排客计划。
7. 返回最终预约结果。

### 与现有接口关系

现有底层接口可组合实现：

- `GET /platform_agent/order/check_customer`
- `GET /order/schedule/available_time`
- `POST /platform_agent/order/create_work`
- `POST /platform_agent/order/schedule/order_plan`

但建议后端封装成一个 AI 专用接口，减少工作流多节点失败风险。

## 3. 取消预约

### 接口名称

AI 取消客户当前预约

### 建议接口

`POST /platform_agent/ai/appointment/cancel`

### 用途

客户明确表示取消预约时，由 AI 发起取消。

### 请求参数

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| corp_id | string | 是 | 企微企业 ID |
| wechat | string | 是 | 员工企微账号 |
| external_userid | string | 否 | 企微外部联系人 ID |
| customer_id | int | 否 | 客户 ID |
| order_id | int | 否 | 订单 ID；若为空，后端取消当前最近有效预约 |
| user_id | int | 是 | 操作员工 ID |
| cancel_reason | string | 否 | 取消原因 |

### 返回字段

```json
{
  "code": 200,
  "msg": "success",
  "data": {
    "cancelled": true,
    "order_id": 5613087,
    "status": 8,
    "status_name": "已取消"
  }
}
```

### 业务规则

- 如果客户无有效预约，返回 `cancelled=false` 和明确原因。
- 如果存在多个有效预约且未传 `order_id`，返回需要确认，不要默认取消多条。
- 取消失败时返回可对客解释的原因，例如“不允许取消排客”“预约已完成”“未找到有效预约”。

### 与现有接口关系

现有底层接口：

- `POST /platform_agent/order/schedule/cancel_plan`

现有不足：

- 该接口需要 `order_id`。
- AI 需要先查询客户当前预约才能知道取消哪个订单。
- 建议由后端封装“按客户取消当前有效预约”的能力。

## 4. 查询客户全部历史订单

### 接口名称

查询客户完整历史订单

### 建议接口

`GET /platform_agent/ai/customer/order_history`

### 用途

用于 AI 判断：

- 客户在某个项目大类是否新客。
- 客户历史做过哪些项目。
- 客户最近一次消费/到店时间。
- 客户历史客单价、最高客单价、平均客单价。
- 客户是否属于复购客、老客、沉默回归客。
- 售后服务中客户做过什么项目、什么时候做、在哪家门店做。

### 请求参数

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| corp_id | string | 是 | 企微企业 ID |
| wechat | string | 是 | 员工企微账号 |
| external_userid | string | 否 | 企微外部联系人 ID |
| customer_id | int | 否 | 客户 ID |
| include_status | string | 否 | 订单状态，默认返回全部；可用逗号分隔 |
| start_at | int | 否 | 开始时间 Unix 时间戳 |
| end_at | int | 否 | 结束时间 Unix 时间戳 |
| page | int | 否 | 页码，默认 1 |
| limit | int | 否 | 每页数量，默认 100 |

说明：

- `external_userid` 和 `customer_id` 至少传一个。
- 默认必须返回该客户所有历史订单，不只返回进行中订单。

### 返回字段

```json
{
  "code": 200,
  "msg": "success",
  "data": {
    "customer_id": 9631370,
    "summary": {
      "order_count": 6,
      "completed_count": 4,
      "total_paid": "6800.00",
      "avg_paid": "1700.00",
      "max_paid": "3000.00",
      "last_order_at": 1773802087,
      "last_store_at": 1773805000,
      "last_finish_at": 1773809000
    },
    "orders": [
      {
        "order_id": 5613087,
        "order_no": "2603181048074461",
        "status": 6,
        "status_name": "已完成",
        "created_kind": 2,
        "created_kind_name": "开单",
        "store_id": 11,
        "store_name": "上海徐汇店",
        "category_id": 110,
        "category_name": "皮肤管理",
        "created_at": 1773802087,
        "plan_at": 1773805200,
        "store_at": 1773805000,
        "finish_at": 1773809000,
        "fee_origin": "3000.00",
        "fee_required": "2500.00",
        "fee_paid": "2500.00",
        "items": [
          {
            "source": "buys",
            "product_id": 810,
            "specification_id": 9578,
            "product_name": "丝雾眉(高级)",
            "number": 1,
            "price": "500.00",
            "fee_required": "500.00",
            "fee_paid": "500.00",
            "add_kind": 1,
            "is_refund_success": false
          }
        ]
      }
    ],
    "page": 1,
    "limit": 100,
    "count": 6
  }
}
```

### 关键要求

1. 必须返回所有历史订单，而不是只返回当前进行中订单。
2. 必须包含已完成订单，否则无法判断某项目大类是否老客。
3. 建议包含已取消、已流退、退款订单，但用状态区分，方便 AI 判断。
4. 到店前订单项目取 `plans`，到店后/已完成订单项目取 `buys`，建议后端统一整理到 `items`。
5. 必须返回金额字段，至少包括 `fee_required`、`fee_paid`。
6. 必须返回项目大类 `category_id/category_name`，用于新老客判断。
7. 需要支持分页，但默认 `limit` 建议不低于 100。

## 四、AI业务判断规则

### 1. 当前预约判断

AI 只根据“查询客户当前有效预约”接口判断是否已有预约。

不能仅根据 `check_customer.result=0` 推断预约详情，因为该接口只说明不可创建订单，不返回预约时间和门店。

### 2. 创建预约

AI 只能在客户明确确认以下信息后创建预约：

- 门店
- 日期
- 时间
- 项目/大类
- 如需预约金，客户已确认预约金

创建成功后才能对客户说“已预约”。

### 3. 取消预约

AI 只能在客户明确表达取消后调用取消接口。

取消成功后才能对客户说“已取消”。

### 4. 新老客判断

AI 判断客户在某个项目大类是否新客，应基于历史订单中的 `category_id/category_name` 或项目映射关系。

规则建议：

- 如果客户在该大类有已完成/有效消费记录，则该大类为老客。
- 如果客户在其他大类有消费，但当前咨询大类无消费记录，则当前大类仍可视为该大类新客。
- 已取消、已流退订单不应直接算作已消费，但可作为历史意向参考。

### 5. 客单价判断

AI 需要以下统计：

- 历史总实收
- 平均客单价
- 最高客单价
- 最近一次实收
- 项目大类、新老客、客单价等统计由 AI 侧按业务规则计算，客户系统只需要返回完整历史订单原始数据。

## 五、现有接口是否满足需求

| 需求 | 现有接口是否满足 | 说明 |
|---|---|---|
| 获取客户 ID | 基本满足 | `get_customer_info` 可用 |
| 查询客户当前是否有预约 | 部分满足 | `order/index` 或 `check_customer` 可辅助，但缺少直接预约详情聚合接口 |
| 查询可预约时间 | 基本满足 | `available_time` 可用，但路径前缀需确认 |
| 创建预约/开单 | 部分满足 | `create_work` + `order_plan` 可组合完成，建议封装 |
| 取消预约 | 部分满足 | `cancel_plan` 可用，但需要先查当前有效 `order_id` |
| 改约 | 部分满足 | 有 `change_plan_time`，但参数中未见具体时间点，需确认 |
| 查询客户所有历史订单 | 不满足 | 当前反馈只能查当前进行订单，无法支持新老客和客单价判断 |
| 判断某大类新老客 | 不满足 | 需要完整历史订单原始数据，AI 侧自行按业务大类规则判断 |
| 查询以往客单价 | 不满足 | 需要历史订单金额聚合 |

## 六、本期建议开发范围

建议本期优先开发以下 4 个 AI 专用接口：

1. `GET /platform_agent/ai/customer/current_appointment`
2. `POST /platform_agent/ai/appointment/create`
3. `POST /platform_agent/ai/appointment/cancel`
4. `GET /platform_agent/ai/customer/order_history`

如果开发资源有限，最低可接受方案：

1. 扩展 `GET /platform_agent/order/index`，确保 `customer_id` 查询时可返回所有历史订单，并支持状态筛选。
2. 新增一个 `GET /platform_agent/ai/customer/current_appointment` 聚合接口。
3. AI 创建预约暂时由现有 `create_work + order_plan` 组合实现。
4. AI 取消预约暂时由 `current_appointment + cancel_plan` 组合实现。

## 七、给产品的确认问题

1. `GET /platform_agent/order/index` 当前按 `customer_id` 查询时，是否确实只返回进行中订单？是否可以改为默认返回全部历史订单？
2. `GET /order/schedule/available_time` 是否需要统一改为 `/platform_agent/order/schedule/available_time`？
3. `POST /platform_agent/order/schedule/change_plan_time` 是否支持具体时间点？目前文档只看到 `date + order_id`。
4. AI 创建预约是否允许预约金为 0？
5. 客户有多个有效预约时，是否允许 AI 取消最近一条，还是必须二次确认？
6. 项目大类新老客判断以 `category_id` 为准，还是需要产品/规格映射到业务自定义大类？
7. 历史订单中退款成功的项目是否计入老客消费记录？
