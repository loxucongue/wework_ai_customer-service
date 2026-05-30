# SF1 新客首响测试方案

## 测试目标

验证工作流在新客首次来话、低意图咨询、刚加好友、泛泛了解、轻度需求表达时，是否能正确进入 `SF1_new_customer_reply`，并生成自然、短、低压力的首响回复。

## 本轮用例

- 用例文件：`workflow_tests/sf1_new_customer_reply_cases.json`
- 用例数量：24
- 工作流：`7639623828015988742`
- 暂定结果文件：`workflow_tests/results/sf1_new_customer_reply_results.json`

## 覆盖方向

1. 纯打招呼：
   - 你好
   - 在吗
   - 哈喽
   - 有人吗

2. 泛泛了解：
   - 我想了解一下
   - 请问这里是咨询的吗
   - 你们这里能先简单介绍一下吗

3. 新客身份：
   - 第一次咨询
   - 刚加上
   - 朋友推荐
   - 看到广告来的

4. 低意图需求：
   - 想看看有没有适合我的
   - 不知道自己适合什么
   - 想改善皮肤
   - 想变年轻一点

5. 带轻度具体困扰的新客：
   - 斑明显但第一次问
   - 毛孔和出油
   - 脸上有点问题

6. 带历史上下文：
   - 前一轮已经打招呼，当前继续说想了解
   - 前一轮助手问方向，当前仍低意图
   - 客户强调“不一定做”

## 预期路由

大多数用例期望：

```json
{
  "scene": "S1_icebreaking",
  "intent": "greeting",
  "subflow": "SF1_new_customer_reply"
}
```

允许少量边界：

- 带明确具体项目问题时，可进入 `SF3_project_consult`，但本轮 SF1 用例刻意避免直接项目问答。
- 普通新客不应进入 `HUMAN_HANDOFF`。
- 不应进入 `SF6_store_match` 或 `SF9_appointment`。
- 不应进入 `SF7_price_consult`，除非客户明确问价。

## 回复质量判定

通过标准：

- 能自然承接首次咨询。
- 每轮最多问一个关键问题。
- 回复不超过 1-3 条。
- 不直接报价。
- 不主动预约。
- 不主动问门店/城市/位置。
- 不强推具体项目或产品。
- 不承诺效果。
- 不说“作为AI”“系统判断”“数据库显示”。
- 不输出“抱歉，未能获取到回复内容”。

重点错误：

- 把“刚加的，想先了解皮肤项目”过早路由到项目咨询并直接推荐项目。
- 把“看到广告来的”直接路由到活动推送。
- 把“想看看有没有优惠，但还不知道做什么”直接路由到价格或活动。
- 把“我有点怕医美，先问问”转人工。
- 一次性列出大量项目，造成营销味重。

## 执行命令

```powershell
.\workflow_tests\run_workflow_tests.ps1 `
  -CasesPath .\workflow_tests\sf1_new_customer_reply_cases.json `
  -OutPath .\workflow_tests\results\sf1_new_customer_reply_results.json
```

## 当前阻塞

当前进程没有检测到 `COZE_WORKLOAD_API_TOKEN`。需要先在 PowerShell 设置环境变量后才能执行真实 API 调用。

