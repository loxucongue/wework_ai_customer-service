# AI客服预约与订单接口适配与补充需求

日期：2026-05-26  
用途：提交产品/后端评审  

## 一、背景

AI 客服需要接入客户系统，完成预约和订单查询相关能力。

当前重点需要两类能力：

1. 预约接口：查询客户当前是否有预约；客户确认后创建预约；客户要求时取消预约。
2. 订单接口：查询客户全部历史订单，用于判断客户在某个项目大类是否为新客，以及历史消费项目、历史客单价等。

已查看现有《企微第三方平台接口文档》后判断：

- 预约相关底层接口已经有一部分，不是完全不支持。
- 但 AI 客服实际使用时，需要确认这些接口是否能完整覆盖“查当前预约、创建预约、取消预约”的业务链路。
- 订单列表接口字段较全，但目前已知只能查到当前进行中的订单，无法满足历史订单分析。

## 二、现有接口适配结论

| 能力 | 现有文档是否已有接口 | 当前判断 |
|---|---|---|
| 查询客户当前是否有预约 | 部分有 | 可通过 `order/index` 或 `check_customer` 辅助判断，但 `check_customer` 只返回能否创建订单，不返回预约详情；仍需确认是否能稳定查到当前有效预约 |
| 创建预约/开单 | 已有底层接口 | `create_work` 可创建预约金订单，`order_plan` 可添加排客；需要确认组合后是否就是业务所说“预约成功” |
| 取消预约 | 已有底层接口 | `cancel_plan` 可取消排客，但需要先拿到有效 `order_id`；客户维度取消能力仍依赖当前预约查询 |
| 查询客户全部历史订单 | 不满足 | 当前反馈只能查当前进行订单，无法支持新老客、历史消费、客单价判断 |

因此，前三个预约能力不是“系统完全没有”，而是“需要确认现有接口是否可直接复用，或补充客户维度的查询/封装能力”。真正明确缺口最大的是“客户全部历史订单查询”。

## 三、需要确认或补充的接口能力

## 1. 查询客户当前有效预约

### 现有接口情况

相关接口：

- `GET /platform_agent/order/check_customer`
- `GET /platform_agent/order/index`

现有问题：

- `check_customer` 只能判断客户是否可创建订单，不能返回预约门店、预约时间、订单 ID。
- `order/index` 理论上可按 `customer_id` 查订单，但需要确认是否能稳定筛出客户当前有效预约。

### 需求说明

根据客户身份查询当前是否已有有效预约。

用于：

- 判断客户是否已经预约。
- 避免重复预约。
- 客户询问“我约了吗/什么时候到店”时可以准确回复。
- 客户取消预约时先确认要取消哪一单。

### 建议处理方式

优先确认是否可以直接用 `order/index` 实现：

- 按 `customer_id` 查询订单。
- 后端或调用方筛选有效预约状态。
- 返回当前有效预约详情。

如果现有 `order/index` 无法稳定返回当前预约详情，再新增以下接口：

`GET /platform_agent/customer/current_appointment`

### 请求参数

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| corp_id | string | 是 | 企微企业 ID |
| wechat | string | 是 | 员工企微账号 |
| external_userid | string | 否 | 企微外部联系人 ID |
| customer_id | int | 否 | 客户 ID |

说明：

- `external_userid` 和 `customer_id` 至少传一个。
- 如果只传 `external_userid`，系统需要能找到对应客户。

### 返回字段

| 字段 | 类型 | 说明 |
|---|---|---|
| has_appointment | bool | 是否有当前有效预约 |
| order_id | int | 预约订单 ID |
| order_no | string | 订单号 |
| status | int | 订单/预约状态 |
| status_name | string | 状态名称 |
| store_id | int | 门店 ID |
| store_name | string | 门店名称 |
| plan_at | int | 预约时间戳 |
| plan_time_text | string | 可读预约时间 |
| category_id | int | 意向项目大类 ID |
| category_name | string | 意向项目大类名称 |
| items | array | 预约项目列表 |
| can_cancel | bool | 是否可取消 |
| can_change | bool | 是否可改约 |

### 业务要求

- 已取消、已完成、已流退订单不应作为当前有效预约返回。
- 如果客户有多个有效预约，需要返回列表，并标记最近一条。
- 如果没有预约，返回 `has_appointment=false`。

## 2. 创建预约

### 现有接口情况

相关接口：

- `GET /order/schedule/available_time`
- `POST /platform_agent/order/create_work`
- `POST /platform_agent/order/schedule/order_plan`

现有能力：

- `create_work` 可以创建预约金订单。
- `order_plan` 可以为订单添加排客计划。

需要确认：

- `create_work + order_plan` 连续调用成功后，是否等价于业务上的“预约成功”。
- `available_time` 路径是否应统一带 `/platform_agent` 前缀。
- 预约金是否允许为 0。

### 需求说明

客户确认门店、日期、时间、项目后，系统创建预约。

用于：

- AI 客服在客户明确确认后发起预约。
- 创建成功后才能回复客户“已预约”。

### 建议处理方式

优先复用现有接口组合：

1. 查询可预约时间。
2. 调用 `create_work` 创建预约金订单。
3. 调用 `order_plan` 添加排客。

如果 Coze 工作流直接组合多个接口不稳定，或后端希望统一校验流程，再考虑新增：

`POST /platform_agent/appointment/create`

### 请求参数

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| corp_id | string | 是 | 企微企业 ID |
| wechat | string | 是 | 员工企微账号 |
| external_userid | string | 否 | 企微外部联系人 ID |
| customer_id | int | 否 | 客户 ID |
| user_id | int | 是 | 操作员工 ID |
| store_id | int | 是 | 门店 ID |
| category_id | int | 是 | 意向项目/大类 ID |
| plan_date | string | 是 | 预约日期，YYYY-MM-DD |
| plan_time | string | 是 | 预约时间，HH:mm |
| prepay | decimal | 否 | 预约金金额 |
| remark | string | 否 | 备注 |

### 返回字段

| 字段 | 类型 | 说明 |
|---|---|---|
| success | bool | 是否创建成功 |
| order_id | int | 创建后的订单 ID |
| order_no | string | 订单号 |
| store_id | int | 门店 ID |
| store_name | string | 门店名称 |
| plan_at | int | 预约时间戳 |
| plan_time_text | string | 可读预约时间 |
| category_id | int | 项目大类 ID |
| category_name | string | 项目大类名称 |
| prepay_required | string | 应付预约金 |
| prepay_paid | string | 已付预约金 |
| fail_reason | string | 失败原因 |

### 业务要求

创建前系统需要校验：

- 客户是否存在。
- 客户是否已有进行中预约/订单。
- 门店是否开放预约。
- 该日期时间是否可约。
- 项目/大类是否有效。

创建失败时，需要返回明确失败原因，例如：

- 客户已有进行中预约。
- 门店不可预约。
- 当前时间已被占用。
- 项目无效。

## 3. 取消预约

### 现有接口情况

相关接口：

- `POST /platform_agent/order/schedule/cancel_plan`

现有能力：

- 可按 `order_id` 取消排客。

现有问题：

- AI 客服通常拿到的是客户身份，不一定直接知道 `order_id`。
- 所以取消预约依赖“查询客户当前有效预约”先拿到 `order_id`。
- 如果客户有多个有效预约，需要先让客户确认取消哪一单。

### 需求说明

客户明确表示取消预约时，系统取消对应预约。

用于：

- 客户说“明天不去了”“帮我取消预约”时执行取消。
- 取消成功后才能回复客户“已取消”。

### 建议处理方式

优先复用现有接口：

1. 先查询客户当前有效预约，拿到 `order_id`。
2. 调用 `cancel_plan` 取消排客。

如果希望支持“按客户直接取消当前有效预约”，再考虑新增：

`POST /platform_agent/appointment/cancel`

### 请求参数

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| corp_id | string | 是 | 企微企业 ID |
| wechat | string | 是 | 员工企微账号 |
| external_userid | string | 否 | 企微外部联系人 ID |
| customer_id | int | 否 | 客户 ID |
| order_id | int | 否 | 订单 ID，不传时取消当前有效预约 |
| user_id | int | 是 | 操作员工 ID |
| cancel_reason | string | 否 | 取消原因 |

### 返回字段

| 字段 | 类型 | 说明 |
|---|---|---|
| success | bool | 是否取消成功 |
| order_id | int | 订单 ID |
| status | int | 最新状态 |
| status_name | string | 状态名称 |
| fail_reason | string | 失败原因 |

### 业务要求

- 客户没有有效预约时，不应取消，返回失败原因。
- 客户有多个有效预约且未指定 `order_id` 时，不应默认取消，需要返回“存在多条预约，需确认”。
- 取消失败时，需要返回明确原因。

## 4. 查询客户全部历史订单

### 需求说明

根据客户身份查询全部历史订单。

用于：

- 判断客户在某个项目大类是否为新客。
- 判断客户历史做过哪些项目。
- 判断客户历史客单价、平均客单价、最高客单价。
- 判断客户最近一次到店/消费/完成时间。
- 售后场景查询客户曾做项目和操作时间。

### 建议接口

`GET /platform_agent/customer/order_history`

### 请求参数

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| corp_id | string | 是 | 企微企业 ID |
| wechat | string | 是 | 员工企微账号 |
| external_userid | string | 否 | 企微外部联系人 ID |
| customer_id | int | 否 | 客户 ID |
| include_status | string | 否 | 订单状态，默认全部 |
| page | int | 否 | 页码，默认 1 |
| limit | int | 否 | 每页数量，建议默认 100 |

### 返回字段

#### 订单汇总

| 字段 | 类型 | 说明 |
|---|---|---|
| order_count | int | 历史订单总数 |
| completed_count | int | 已完成订单数 |
| total_paid | string | 历史总实收 |
| avg_paid | string | 平均客单价 |
| max_paid | string | 最高客单价 |
| last_order_at | int | 最近下单时间 |
| last_store_at | int | 最近到店时间 |
| last_finish_at | int | 最近完成时间 |

#### 订单明细

| 字段 | 类型 | 说明 |
|---|---|---|
| order_id | int | 订单 ID |
| order_no | string | 订单号 |
| status | int | 订单状态 |
| status_name | string | 状态名称 |
| store_id | int | 门店 ID |
| store_name | string | 门店名称 |
| category_id | int | 系统项目分类 ID，沿用现有字段 |
| category_name | string | 系统项目分类名称，沿用现有字段 |
| created_at | int | 创建时间 |
| plan_at | int | 预约时间 |
| store_at | int | 到店时间 |
| finish_at | int | 完成时间 |
| fee_origin | string | 原价 |
| fee_required | string | 应收 |
| fee_paid | string | 实收 |
| items | array | 项目明细 |

### 业务要求

- 必须返回客户全部历史订单，不只返回当前进行中的订单。
- 必须包含已完成订单，否则无法判断大类新老客。
- 已取消、已流退、退款订单可以返回，但必须有状态区分。
- 到店前订单项目可取 `plans`，到店后/已完成订单项目可取 `buys`，建议统一整理到 `items`。
- 金额字段至少需要返回 `fee_required` 和 `fee_paid`。

## 四、最低优先级

如果只能先开发一部分，建议优先级如下：

1. 查询客户全部历史订单。
2. 查询客户当前有效预约。
3. 确认可复用现有 `create_work + order_plan` 创建预约。
4. 确认可复用现有 `cancel_plan` 取消预约。

原因：

- 历史订单是价格、新老客、售后、复购判断的基础。
- 当前预约查询是避免重复预约和安全取消预约的基础。
- 创建和取消预约已有底层接口，优先确认能否复用，不一定要新增接口。

## 五、待确认问题

1. 项目大类新老客判断以 `category_id` 为准，还是需要单独维护业务大类映射？
2. 退款成功订单是否计入老客消费记录？
3. `order/index` 能否按 `customer_id` 查到当前有效预约详情？
4. `order/index` 是否可以扩展为返回全部历史订单，还是需要单独开发 `order_history`？
5. `create_work + order_plan` 是否等价于完成预约？
6. `cancel_plan` 取消排客后，订单状态是否会同步变为取消/无预约？
7. 预约金是否允许为 0？
8. 客户有多条有效预约时，是否必须二次确认后才能取消？
