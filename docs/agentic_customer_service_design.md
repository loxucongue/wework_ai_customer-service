# AI 客服 Agentic Workflow 完整设计方案

## 1. 目标

当前系统要完成的是企业微信医美客服回复，不是简单的意图分类机器人。

客户真实消息经常同时包含多个业务诉求，例如:

- 发图片问适合什么项目，同时问价格。
- 问门店地址，同时质疑是否正规。
- 问项目效果，同时提到别家报价。
- 已经有预约，但又继续咨询项目、价格或到店准备。

因此后续架构不再把 `SF1-SF12` 当成互斥子流程，而是改成“可组合调用的业务能力”。主流程大脑根据当前消息和上下文，自主规划要调用哪些工具/skill，再统一合成客服回复。

目标效果:

- 一轮消息可以处理多个意图。
- 不被固定子流程卡死。
- 可以调用多个知识库、客户系统接口和业务 skill。
- 回复既回答当前问题，也推动客户进入下一阶段，例如了解项目、建立信任、预约到店。
- 每个节点、每个工具调用都有日志，方便排查。
- 高风险、投诉、退款、人工诉求仍由硬规则兜底，不交给模型自由判断。

## 2. 核心设计原则

### 2.1 固定子流程降级为业务 Skill

原来的:

```text
主路由 -> SF7_price_consult -> 价格回复
```

改为:

```text
主脑规划 -> price_consult_skill -> 返回价格事实和回复要点
```

`SF7_price_consult` 仍可保留为前端展示标签，但不再代表唯一执行路径。

### 2.2 主脑负责规划，不直接编事实

主脑可以决定:

- 需要查哪个知识库。
- 需要查哪个客户接口。
- 需要调用哪些业务 skill。
- 本轮最多回复几条。
- 回复目标是什么。

主脑不能:

- 编造价格。
- 编造门店地址。
- 编造预约状态。
- 编造资质、医生、案例、售后政策。
- 对医学问题做诊断。

事实必须来自工具、知识库、接口或客户明确表达。

### 2.3 硬规则优先于主脑

以下场景必须先拦截:

- 明确要求人工、真人、顾问。
- 投诉、退款、维权、曝光。
- 未成年、孕妇、哺乳期、严重慢病、严重过敏。
- 病例、检查报告、处方。
- 严重术后异常，例如流脓、出血不止、发烧、视力异常、剧烈疼痛。
- 自杀自残等高危表达。

命中后直接进入人工/高风险处理，不让主脑继续自由规划普通客服话术。

### 2.4 每轮都读全局上下文

每轮最少读取:

- 当前消息。
- 最近对话。
- 客户画像。
- 历史事件。
- 最近项目。
- 最近门店。
- 当前预约状态或预约缓存。
- 图片理解结果。

这样可以做到:

- 当前问题回答完整。
- 如果客户已有预约，可以适时提醒。
- 如果客户已提供过斑型、城市、预算，不重复追问。
- 如果客户前面说“点状为主”，后续价格/项目回复能承接。

### 2.5 所有节点必须可追踪

每个节点记录:

- node name
- input snapshot
- output snapshot
- tool calls
- duration
- error

日志写入:

```text
logs/runs/{request_id}.json
```

后续前端可用 `request_id` 打开调试面板。

## 3. 总体架构

```text
FastAPI /chat
  |
  v
normalize_input
  |
  v
load_context
  |
  v
image_understanding
  |
  v
hard_guardrails
  |
  v
planner_brain
  |
  v
execute_tools_and_skills
  |
  v
reply_synthesizer
  |
  v
profile_event_extractor
  |
  v
persist_trace_and_state
  |
  v
reply_messages
```

## 4. LangGraph State 设计

建议核心状态:

```python
class AgentState(TypedDict, total=False):
    request_id: str
    customer_id: str
    corp_id: str
    content: str
    file_image: str | None
    conversation_history: list[str]

    customer_profile: dict
    customer_basic_info: dict
    history_events: list[dict]
    appointment_cache: dict
    recent_store: dict
    recent_project_names: list[str]

    image_info: dict
    guardrail_result: dict
    action_plan: dict
    tool_results: dict
    skill_outputs: list[dict]
    reply_plan: dict
    reply_messages: list[dict]

    profile_update: dict
    event_updates: list[dict]
    trace: list[dict]
    errors: list[dict]
```

## 5. 节点职责

### 5.1 normalize_input

职责:

- 标准化 `content/current_message`。
- 保留 `file_image`。
- 处理空文本 + 图片消息。
- 清理明显坏编码，例如 `???`。

期望效果:

- 后续节点拿到稳定输入。
- 避免坏 query 污染 Coze 检索日志。

### 5.2 load_context

职责:

- 读取客户画像。
- 读取历史事件。
- 读取最近项目、最近门店、最近门店 id。
- 读取预约缓存。
- 必要时刷新当前预约情况。

期望效果:

- 回复不割裂。
- 客户前面提供过的信息能被复用。
- 任何业务场景都能知道客户是否已有预约。

### 5.3 image_understanding

职责:

- 如果有 `file_image`，调用视觉模型。
- 判断图片类型。
- 提取可见皮肤问题、截图文字、报告/付款/地址等信息。
- 输出结构化 `image_info`。

期望效果:

- 图片面诊不再只识别成“客户发图”。
- 能把“点状斑点、片状色沉、泛红、痘印”等信息提供给项目/价格/画像节点。

### 5.4 hard_guardrails

职责:

- 判断是否命中硬性人工/高风险规则。
- 命中则直接输出人工处理计划。

期望效果:

- 高风险不被普通模型话术稀释。
- 医疗、投诉、退款、未成年等场景合规可控。

### 5.5 planner_brain

职责:

- 根据当前消息和上下文生成 `action_plan`。
- 决定本轮调用哪些 tools/skills。
- 最多选择 4 个工具/skill。
- 规划回复目标。

示例输出:

```json
{
  "primary_goal": "回答客户点状斑适合项目、皮秒价格和正规性顾虑",
  "actions": [
    {
      "type": "skill",
      "name": "project_consult_skill",
      "reason": "客户有点状斑改善需求"
    },
    {
      "type": "skill",
      "name": "price_consult_skill",
      "reason": "客户询问皮秒多少钱"
    },
    {
      "type": "skill",
      "name": "trust_build_skill",
      "reason": "客户询问是否正规"
    }
  ],
  "reply_constraints": {
    "max_messages": 3,
    "tone": "企微客服，自然克制，不强推",
    "must_not": ["编造价格", "承诺效果", "夸大资质"]
  }
}
```

期望效果:

- 不再只能进入单个 SF 子流程。
- 不在既有子流程里的问题，也可以通过知识库和接口组合回答。

### 5.6 execute_tools_and_skills

职责:

- 根据 `action_plan` 调用工具或业务 skill。
- 可并行调用互不依赖的工具。
- 收集结果和错误。

期望效果:

- 所有外部事实统一从工具层进入。
- 工具失败时可降级回复，而不是整轮失败。

### 5.7 reply_synthesizer

职责:

- 根据工具结果和回复目标生成最终 `reply_messages`。
- 合并重复内容。
- 控制最多 1-3 条消息。
- 同时回答当前问题并推动下一阶段。

期望效果:

- 回复像真实客服，不像流程节点拼接。
- 多意图消息能一次性处理完整。
- 可以自然引导客户继续了解项目或到店。

### 5.8 profile_event_extractor

职责:

- 每轮更新画像和历史事件。
- 记录客户明确表达的信息。
- 图片理解结果也可进入画像。

期望效果:

- “点状斑点”“面部色沉”“想淡斑”“价格敏感”等信息能沉淀。
- 后续对话减少重复追问。

## 6. 工具层设计

### 6.1 基础工具

| 工具 | 输入 | 输出 | 用途 |
|---|---|---|---|
| `kb_search` | `kb_name`, `query` | `items[]` | 查 Coze 统一知识库 |
| `price_db_query` | `project_name` 或 SQL 条件 | 项目价格行 | 查价格数据库 |
| `image_analyze` | `file_image`, `content` | `image_info` | 图片理解 |
| `appointment_query` | `customer_id` | 当前预约 | 查预约 |
| `order_query` | `customer_id` | 历史订单/消费 | 查售后上下文 |
| `store_search` | 城市/区域/门店名 | 门店数组 | 门店匹配 |
| `store_detail` | `store_id` | 地址/导航/停车 | 门店详情 |

### 6.2 当前已接通工具

| 工具 | 状态 |
|---|---|
| `kb_search` | 已接通 Coze OAuth + 统一知识库 workflow |
| `price_db_query` | 已接通 Coze OAuth + 价格数据库 workflow |

### 6.3 知识库枚举

| kb_name | 用途 |
|---|---|
| `project_qa` | 项目咨询、项目原理、适合人群、恢复期 |
| `project_price` | 项目价格知识库兜底 |
| `competitor_qa` | 竞品应对 |
| `trust_assets` | 资质、背书、图片资料 |
| `after_sales_qa` | 售后护理 |

## 7. 业务 Skill 设计

### 7.1 project_consult_skill

输入:

- 当前问题
- 图片理解结果
- 最近项目
- `project_qa` 检索结果

输出:

```json
{
  "skill": "project_consult",
  "facts": [],
  "reply_points": [],
  "missing_slots": [],
  "next_step": ""
}
```

期望效果:

- 客户描述需求时，不强迫客户说专业项目名。
- 能从“点状斑、色沉、痘印、毛孔”等需求切入项目方向。

### 7.2 price_consult_skill

输入:

- 当前问题
- 项目候选
- 价格数据库结果
- 客户是否新客/老客

输出:

```json
{
  "skill": "price_consult",
  "facts": ["皮秒新客价999", "活动价1680"],
  "reply_points": ["可以先按999-1680做预算参考"],
  "risks": []
}
```

期望效果:

- 价格数字来自数据库。
- 不再机械堆所有价格。
- 能承接客户需求，例如“点状斑要看深浅和范围”。

### 7.3 trust_build_skill

输入:

- 客户信任顾虑
- `trust_assets` 检索结果
- 可用图片 URL

输出:

- 资质/背书解释要点。
- 可发送的图片资料。
- 禁止夸大提醒。

期望效果:

- 客户问正规、靠谱、怕被骗时，不再回到门店/价格流程。
- 能发送真实知识库图片，而不是编造资料。

### 7.4 competitor_skill

输入:

- 客户提到的竞品、价格、截图文字。
- `competitor_qa` 检索结果。

期望效果:

- 不诋毁竞品。
- 不跟价。
- 引导客户看产品、剂量、部位、次数、售后、操作人员。

### 7.5 after_sales_skill

输入:

- 当前售后问题。
- 当前订单/历史项目。
- 图片理解结果。
- `after_sales_qa` 检索结果。

期望效果:

- 轻微护理问题能给规范建议。
- 严重不适能转人工。
- 不直接说“正常/没事”。

### 7.6 appointment_skill

输入:

- 当前预约意图。
- 当前预约缓存。
- 门店/时间。

期望效果:

- 已有预约时能提醒。
- 预约、改约、取消都先查记录。
- 查询可约时间前必须有门店。

### 7.7 store_skill

输入:

- 城市、区域、门店名、地址/停车/路线问题。

期望效果:

- 地址、导航、停车问题直接处理。
- 不把后续“你们正规吗”继续困在门店流程。

## 8. 回复合成策略

`reply_synthesizer` 应遵守:

- 默认 1-3 条消息。
- 先回应客户最关心的问题。
- 多意图时按客户风险和决策路径排序:
  1. 风险/人工
  2. 信任顾虑
  3. 当前问题事实
  4. 项目/价格解释
  5. 下一步引导
- 不使用“系统查询到”“知识库显示”等词。
- 不暴露工具调用过程。
- 不主动强推预约，但可以轻度推进:
  - “如果你想更准确判断，可以结合照片/到店面诊看斑型。”
  - “你前面说在厦门，我可以继续帮你看就近门店。”

## 9. 分阶段实施任务

### Phase 1: 本地最小闭环

状态: 基本完成

任务:

- FastAPI `/chat`
- LangGraph 基础图
- Coze OAuth JWT
- `kb_search`
- `price_db_query`
- 前端 `/api/chat` 代理到本地
- trace 日志

期望效果:

- 前端可以不直接调用 Coze 主工作流。
- 本地能看到每轮节点执行情况。
- 工具调用失败不导致整轮崩溃。

### Phase 2: 主脑规划与工具执行

任务:

- 新增 `planner_brain` 节点。
- 用小模型输出 `action_plan`。
- 将当前 `route_intents/collect_tools` 改成 `plan_actions/execute_tools`。
- 限制每轮最多 4 个工具/skill。
- 保留硬规则前置。

期望效果:

- 一句话多意图能被规划成多个动作。
- 不在 SF1-SF12 的问题也可以查知识库或接口回答。

### Phase 3: 业务 Skill 模块化

任务:

- 实现 `project_consult_skill`
- 实现 `price_consult_skill`
- 实现 `trust_build_skill`
- 实现 `competitor_skill`
- 实现 `after_sales_skill`

期望效果:

- 每个 skill 输出事实和回复要点，不直接垄断最终回复。
- 回复合成器可以组合多个 skill 输出。

### Phase 4: 大模型回复合成

任务:

- 实现 `reply_synthesizer` 大模型节点。
- 输入 `action_plan + tool_results + skill_outputs + context`。
- 输出最终 `reply_messages`。
- 控制语气、条数、合规边界。

期望效果:

- 回复更像真实企微客服。
- 不再是模板拼接。
- 多工具结果能自然合并。

### Phase 5: 图片理解接入

任务:

- 接入豆包或千问视觉模型。
- 输出统一 `image_info`。
- 图片结果进入项目、价格、售后、画像节点。

期望效果:

- 图片面诊能识别点状斑点、色沉、泛红、痘印等表层信息。
- 截图类图片能提取报价、活动、地址、报告等文字。

### Phase 6: 预约/门店/订单上下文

任务:

- 接入客户当前预约查询。
- 接入门店匹配和门店详情。
- 接入历史订单/售后上下文。
- 每轮读取预约缓存。

期望效果:

- 客户已有预约时，任何场景都能适时提醒。
- 预约查询、改约、取消不依赖固定子流程。
- 售后能结合历史订单判断。

### Phase 7: 画像与事件持久化

任务:

- 每轮运行画像/事件抽取。
- 记录客户需求、项目、顾虑、城市、斑型、意向。
- 将图片理解结果写入画像。
- 增量更新，避免重复刷屏。

期望效果:

- 客户画像越来越完整。
- 后续回复能承接历史细节。
- 运营侧可回看客户意图变化。

### Phase 8: 调试面板

任务:

- 前端展示 `request_id`。
- 增加 trace 查看页。
- 展示每个节点输入输出、工具调用、耗时、错误。

期望效果:

- 出错时不用猜。
- 可以快速判断是主脑规划问题、工具问题、知识库问题还是回复合成问题。

## 10. 成功标准

### 技术标准

- 所有工具调用可追踪。
- 所有模型节点输入输出可追踪。
- 工具失败可降级。
- 不依赖 Coze 主流程。
- Coze 只作为工具层。

### 业务标准

- 多意图消息能完整回复。
- 不强迫客户理解专业项目名。
- 能自然承接客户历史信息。
- 能主动但克制地推进到项目了解和到店。
- 高风险场景能稳定转人工。

### 回复质量标准

- 不机械。
- 不绕圈。
- 不重复追问已知信息。
- 不编造价格、地址、预约、资质。
- 不承诺效果。
- 不诋毁竞品。
- 不泄露系统和工具调用过程。

## 11. 下一步实施建议

立即开始 Phase 2:

1. 新增 `planner_brain` 提示词和 schema。
2. 新增 `action_plan` 状态。
3. 将当前关键词 `route_intents` 改为小模型规划。
4. 将 `collect_tools` 改为按 `action_plan` 执行工具。
5. 保留当前模板回复作为 fallback。
6. 接入大模型回复合成前，先让 skill 输出结构化要点。

这样可以在不一次性推翻现有代码的情况下，把系统从“单路由流程”逐步升级成“受控 Agentic 客服大脑”。
