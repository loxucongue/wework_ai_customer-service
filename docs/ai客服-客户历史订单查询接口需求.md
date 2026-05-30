# 客户历史订单查询接口需求


## 一、需求背景

AI 客服需要查询客户全部历史订单，用于后续在 AI 侧判断：

- 客户是否做过某类项目。
- 客户在某个业务项目大类下是否属于新客。
- 客户以往消费项目和客单价情况。
- 售后场景中客户曾做过什么项目、什么时候做。

目前文档已有订单列表接口：

`GET /platform_agent/order/index`

该接口字段已经比较完整，包含订单状态、门店、项目、金额、`plans`、`buys` 等。

但当前已知问题是：该接口目前只能查到客户当前进行中的订单，不能查询客户全部历史订单。

## 二、需要新增/调整的能力

### 需求

支持按客户查询全部历史订单。

### 优先方案

直接扩展现有接口：

`GET /platform_agent/order/index`

要求：当传入 `customer_id` 时，可以返回该客户全部历史订单，而不只返回当前进行中的订单。

### 备选方案

如果不方便改原接口，则新增：

`GET /platform_agent/order/history`

返回结构尽量复用现有 `order/index` 的订单列表结构。

## 三、请求参数

尽量沿用现有 `order/index` 参数。

| 参数名 | 类型 | 必填 | 说明 |
|---|---|---|---|
| customer_id | int | 是 | 客户 ID |
| page | int | 否 | 页码，默认 1 |
| limit | int | 否 | 每页数量，默认 20，建议允许传 100 |
| status | int/string | 否 | 订单状态；不传表示全部状态 |

说明：

- 核心是 `status` 不传时，需要返回全部历史订单。
- 如果系统必须区分查询范围，可增加简单参数 `scope=all`，表示查询全部历史订单。

示例：

```http
GET /platform_agent/order/index?customer_id=9631370&page=1&limit=100&scope=all
```

## 四、返回字段

返回字段尽量沿用现有 `order/index`，不要求新增统计字段。

AI 侧至少需要以下现有字段：

### 订单主体字段

| 字段 | 说明 |
|---|---|
| id | 订单 ID |
| customer_id | 客户 ID |
| order_no | 订单号 |
| store_id | 门店 ID |
| store_name | 门店名称 |
| category_id | 意向项目分类 ID |
| category_name | 意向项目名称 |
| status | 订单状态 |
| pay_status | 支付状态 |
| plan_at | 预约时间 |
| store_at | 到店时间 |
| finish_at | 完成时间 |
| created_at | 创建时间 |
| created_kind | 创建类型，预约/开单 |
| fee_origin | 原价金额 |
| fee_required | 销售金额/应收金额 |
| fee_paid | 实收金额 |
| fee_paid_total | 总实收金额，如有 |
| customer_kind | 客户类型 |
| add_wechat_customer_kind | 加微层面客户类型 |
| lost_at | 流退时间 |
| lost_kind | 流退类型 |

### 项目字段

到店前订单项目取 `plans`，到店后订单项目取 `buys`。

AI 侧需要保留现有 `plans[]` 和 `buys[]` 字段，尤其是：

| 字段 | 说明 |
|---|---|
| product_id | 产品 ID |
| specification_id | 规格 ID |
| product_name | 产品名称 |
| number | 数量 |
| origin_price | 原价 |
| price | 销售单价 |
| fee_required | 应收金额 |
| fee_paid | 实收金额，`buys` 中如有 |
| add_kind | 添加类型 |
| is_refund_success | 是否退款成功，`buys` 中如有 |

## 五、状态范围要求

查询全部历史订单时，需要能返回以下状态的订单：

- 进行中订单
- 已到店订单
- 已完成订单
- 已评价订单
- 已取消订单
- 已流退订单
- 退款相关订单

具体状态值沿用现有文档：

```text
0=已流退
1=待定中
2=待排客
3=已排客
4=已超时
5=已到店
6=已完成
7=已评价
8=已取消
```

## 六、不需要系统新增的内容

以下内容不需要客户系统计算，AI 侧会自行处理：

- 项目业务大类归属
- 某项目大类是否新客
- 历史客单价统计
- 平均客单价
- 最高客单价
- 复购判断
- 客户画像总结

客户系统只需要返回完整历史订单原始数据。

## 七、验收标准

1. 传入 `customer_id` 后，可以查到该客户全部历史订单，不只当前进行中订单。
2. 返回结果包含已完成订单。
3. 返回结果包含订单金额字段。
4. 返回结果包含 `plans` 和/或 `buys` 项目明细。
5. 支持分页，`limit` 建议最大至少支持 100。
6. 不传 `status` 或传 `scope=all` 时，返回全部状态订单。
