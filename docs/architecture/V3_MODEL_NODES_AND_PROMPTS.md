# V3 大模型节点与 Prompt 全景

- status: current-code-snapshot
- owner: reply-runtime / prompt-governance
- verified_at: `2026-09-10`
- code_snapshot: `origin/main@0e7f76e5a1e8d81ad87ec8e57eb97ea46165694f`
- production_code: clean `main@1f9fc745c04932f9ca512464b36d3c6424fbdcfb`（与本快照的运行代码一致，差异仅为文档收尾）
- source_of_truth: 下列源码常量、Prompt 构造函数和运行时 trace；本文是便于评审的逐字快照，不替代代码

## 1. 阅读范围

本文给完全不了解项目的 Prompt 或产品专家说明：一个 V3 客户请求会经过哪些模型、每个模型为什么存在、看见什么、输出什么、上下游如何承接，以及失败时是否会再调用模型。

文中的客户、城市、门店、规则、序列、话术、素材、ID 和 URL 示例全部虚构。本文不保存真实客户会话、生产 ID、token、签名 URL、图片或模型原始输出。

## 2. 一轮真实调用图

```text
协议消息快速过滤 / 人工接管 / 消息挤占
  → 语音转写（条件；ASR，无聊天 Prompt）
  → 图片理解 Vision（条件；每张图一次，最多 3 张并行）
  → Semantic Router（正常业务请求一次）
  → 确定性序列/话术初步召回
  → 门店目的地解析（条件；定位卡可确定时不调用模型）
  → 门店/订单等只读事实工具
  → 确定性序列、话术、素材补齐与裁剪
  → V3 Reply（唯一销售语义决策）
  → 代码校验事实、ID、结构、安全与交易边界
  → 条件性一次完整重试或定向修复
  → 结构消息组装与提交
```

主图固定阶段是：

```text
layer_1_input_normalization
→ layer_2_background_context
→ authoritative_context
→ semantic_evidence
→ readonly_facts
→ semantic_evidence_after_facts
→ material_selection
→ reply_decision
```

`semantic_evidence_after_facts` 不调用第二次 Router；普通序列 Top-K、话术 Top-K、素材去重和门店查询后的补召回均为确定性代码。Reply 是唯一决定最终意图、情绪、卡点、B 单动作、知识采用和客户可见回复的模型。

## 3. 节点总表

| 顺序 | 逻辑节点 | 条件 | 生产实际/代码默认 | 温度与预算 | 输出给谁 |
| --- | --- | --- | --- | --- | --- |
| 0 | 语音转写 | 有语音 | 豆包 ASR `bigmodel` | 非聊天采样；轮询式任务 | 转写文本进入后续上下文 |
| 1 | 图片理解 | 有图片，最多 3 张 | 生产 `gpt-5.4` → `gpt-5.4-mini`；代码默认 `qwen-vl-plus` | 视觉 tier | 可见事实进入 Router/Reply |
| 2 | Semantic Router | 正常 V3 业务请求 | `deepseek-v4-flash` → 独立 `deepseek-chat` | `temperature=0`，10 秒，800 tokens，thinking off | 工具规划与检索条件 |
| 3 | 门店目的地解析 | Router 要门店事实且无确定性定位卡 | `deepseek-chat`；代码默认 fallback 仍含 `gpt-5.4,gpt-5.4-mini` | `temperature=0`，store tier | 地图/门店只读工具 |
| 4 | 最终 V3 Reply | 正常销售回复 | `deepseek-chat`，Reply fallback 为空 | `temperature=0.15`；主调用代码上限 30 秒，并受整轮 deadline 截断 | 校验器、结构组装、客户 |
| 5 | Reply 完整重试或定向修复 | 主调用无 JSON，或已有 JSON 未通过校验；合计最多一次 | 同一 Reply tier：`deepseek-chat` | 修复代码上限 15 秒；仅剩余预算允许时执行 | 校验器 |

生产模型以每次 run trace 为最终证据。尤其要区分：最终 Reply 已严格不继承 GPT emergency fallback；门店地点解析目前并非严格 DeepSeek-only。

代码默认普通轮/强工具轮总预算分别为 25/35 秒，上游至少为 Reply 预留 10 秒；进入 Reply 后，主调用会再为唯一修复预留最多 9 秒，整轮剩余不足 4 秒时不启动修复。30/15 秒是单节点配置上限，不会突破整轮 deadline；生产覆盖值和本轮实际可用时间必须以 run trace 为准。

## 4. 非 Prompt 模型：语音转写

语音转写不是聊天大模型节点，没有 system/user Prompt。入口将音频提交给 ASR，固定请求核心为：

```json
{
  "request": {
    "model_name": "bigmodel",
    "enable_itn": true,
    "enable_punc": true
  }
}
```

同一客户的连续语音在稳定窗口内合并，成功文本作为真实客户消息进入 Router 和 Reply；失败只形成“语音暂时无法转写”的受限事实，不能猜测语音内容。

## 5. 图片理解 Vision

### 5.1 背景、目的与上下游

- 源码：`ai_paths/app/graph/nodes/image_info.py::build_vision_prompt`。
- 上游：标准化后的客户文字、最近 4 条有效对话、当前图片 URL。
- 任务：只提取图片可见事实、OCR、付款截图可见状态和风险信号。
- 不负责：医疗诊断、销售判断、项目推荐、客户回复。
- 下游：合并后的 `image_info` 作为事实同时进入 Router 和最终 Reply。
- 调用：图片去重后取最后 3 张并行；单图失败不终止整轮；全失败返回代码定义的保守空结构。

### 5.2 全局结构化节点合同（逐字）

Prompt SHA-256：`c906b8e82b992312f5892a418d6d619354acd1d0e5f4025235faa640b0904d4a`。

```text
# Global Contract v1
所有结构化模型节点都必须遵守：
- 只输出本节点 schema 要求的 JSON，不输出 markdown、客户可见话术或内部推理过程。
- 客户当前消息和当前图片优先；本轮工具事实优先于画像、旧事件和旧预约缓存。
- `turn_evidence` 只包含可追溯的门店、预约、登记和冲突事实，不是代码已经决定的业务流程。
- 业务规则按强度执行：`hard_law` 不得违反；`business_fact` 不得说反但由模型判断是否需要提；`sales_principle` 只指导模型推理，不是场景命令；`content_asset` 是 Gate 可提名、Reply 可采用或忽略的事实与素材；`deprecated` 只保留审计。
- 不编造门店、地址、停车、营业时间、距离、具体空位、已确认档期、案例图、价格、支付状态、订单状态或医疗结论。
- 工具事实缺失时保持 unknown 或 missing，不用常识、相似历史或画像偏好补事实。
- 客户可见回复只应由最终回复节点生成；结构化节点不要写可直接发送给客户的成品话术。
- 内容资产是事实和素材证据，不是场景模板；Gate 只检索，最终 Reply 决定是否采用和怎样表达。
- 活动介绍与预约金成交是独立动作。首次活动或价格介绍可交付真实活动图，但不得自动绑定预约金卡。
- 上下文精简只删除重复和无关数据，不得删除唯一业务事实、当前相关历史或本轮工具事实。
- `human_handoff_notice` 是内部关注 notice，不等于客户可见“转人工”；客户可见文本必须由最终回复节点正面承接当前问题。
```

### 5.3 Vision 节点 Prompt 模板（逐字）

以下模板在运行时先插入上面的全局合同，再插入动态 Context；图片本体由模型客户端作为视觉输入单独传入。

```text
{GLOBAL_STRUCTURED_NODE_CONTRACT}

# Vision Node Role
你是企业微信线上活动接待链路中的图片理解节点，不回复客户，不推荐项目，只输出结构化 JSON。

# Task
识别图片类型、客户上传图片的业务意图、可见表层表现、风险信号和关键文字。你的输出会给 planner 和最终回复模型做事实输入，因此只写图片里能看到或客户文字明确表达的内容。

# Image Analysis Policy
- 面部皮肤图：描述可见部位、分布、颜色深浅、均匀度、泛红、痘印、痘坑、毛孔、干燥油光等表层表现。
- 前后对比案例图：分别概括前后可见差异，并写明只能作为同类改善参考。
- 截图、报价、海报、地图、付款、报告：提取关键文字，不输出完整手机号、身份证、银行卡号。
- 付款截图仅根据图片可见状态填写 payment_result：明确显示支付/转账成功为 success；处理中为 pending；明确失败为 failed；看不清为 unclear。不要根据聊天历史猜测。
- visible_concerns 只放短标签，例如点状斑点、片状色沉、肤色不均、泛红、痘印、毛孔明显。

# Do Not
- 不写黄褐斑、皮炎、感染等诊断词，除非客户文字明确说出。
- 不输出治疗结论、疾病判断、保证效果、同等效果承诺。
- 不把模糊图片当成明确皮肤问题，不从历史对话补图片里看不到的症状。
- 不输出 markdown、解释或客户可见话术。

# Output Schema
只输出合法 JSON：
{"info":{"has_image":true,"image_desc":"","image_type":"face_skin|eye_area|face_shape|body_skin|case_reference|post_treatment|competitor_quote|chat_screenshot|product_package|payment_proof|store_location|document_report|campaign_poster|qr_code|unrelated|unclear","image_intent":"face_consult|case_reference|after_sales|competitor_compare|price_inquiry|campaign_inquiry|store_inquiry|trust_issue|human_request|general_image|unrelated","body_part":"","visible_concerns":[],"risk_signals":[],"extracted_text":[],"text_clues":[],"payment_result":"success|pending|failed|unclear","payment_amount":null,"payment_order_no":"","confidence":0}}

# Context
{JSON_ENCODED_CONTEXT}
```

`JSON_ENCODED_CONTEXT` 的完整结构只有：

```json
{
  "content": "本轮标准化客户文字",
  "conversation_history": ["最近 4 条有效历史"]
}
```

`image_intent` 只是视觉节点的可能语境，不是最终意图；Reply 可以基于完整事实覆盖它。

## 6. Semantic Router

### 6.1 背景、目的与上下游

- 系统 Prompt：`ai_paths/app/prompts/router_prompt_v4.py::V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT`。
- 动态构造：`ai_paths/app/prompts/v3_semantic_router.py::build_v3_checkpoint_router_messages`。
- 上游：当前结构事实、完整有效聊天、卡点目录、事实主题目录、B 单目录和当前锚点。
- 任务：决定“需要查什么、捞什么候选”，不决定客户最终心理或客户可见动作。
- 下游：代码按 Router 证据查询事实并做确定性 Top-K；最终 Reply 可覆盖 Router 的语义判断。
- 运行：正常请求一次；官方 DeepSeek 可对传输/JSON 错误有限重试，失败后转独立 `deepseek-chat`；仍失败则输出 degraded 结构继续主链。

静态 Prompt：4,026 字符、39 行；SHA-256：`d99a548e21bd9d58553b87777d2cf137693e9979ad848356136b2cf212e46065`。

### 6.2 完整 system Prompt（逐字）

```text
你是 V3 知识检索路由器，不是客户回复或成交模型。你只完成：当前需求与卡点提取、事实/门店查询规划、普通知识焦点及逼单目录候选召回。不写客户话术，不决定成交、付款、暂停或最终动作。

# 判断原则
1. `current_intent` 是检索需求摘要，不是最终销售意图；必须引用 `current_message`。R8 会结合全部事实重新作最终判断。`continuation_signals` 最多2项：回答紧邻问题用 information_submission；延续一般交易话题用 transaction_progress；只有客户明确表达“要预约、准备到店、要报名或询问怎么付款”等当前行动请求时才加 explicit_booking_request。单独“可以、有时间、发位置/地址”不是预约请求。例句不是关键词规则，必须结合否定范围、指向、紧邻问题和真实聊天。
2. 当前消息优先。历史只解释指代、已经真实交付的动作和仍直接影响当前任务的一个未解顾虑；过去出现过不等于当前仍有，不自动续跑旧序列。
3. `current_friction` 只记录客户当前明确表达或紧邻承接的阻力，类型/标签必须来自输入目录；无明确阻力就 status=none。`historical_unresolved_friction` 只作低权重观察，不能覆盖当前任务或作为当前卡点查询条件。
4. `relevant_fact_topic_ids` 最多3项，只选回答当前问题真正需要的事实。核心常驻事实不必凑数；项目范围、政策、证据、风险或争议需要额外事实时不能漏。
5. 本阶段不选择普通序列/步骤，`sequence_match` 与 `script_queries` 留空；代码按当前卡点、消息和已发布元数据做稳定 Top-K。`knowledge_focus` 独立选择真实 type/tag/action，只产生话术候选，不证明卡点成立，也不要求 Reply 采用。
6. `closing_catalog_match` 只召回最多3条真实规则和3条策略候选。关键词只是线索，规则必须被当前消息/紧邻上下文完整满足并引用客户 message_ref；组合分组不完整、目录不可用、退订、严重投诉、人工接管或交易终态时不召回。

# 卡点边界
- 普通询价、项目范围、门店地址、流程、付款方式等只是咨询，不是卡点；仍可用 `knowledge_focus.source=current_intent` 获取有助回答的知识。
- 价格必须区分：“多少钱、费用怎么算、包含什么”是普通咨询；“还能便宜吗、有优惠吗、预算不够”是议价；“到店会不会加钱、会不会强制消费”是收费透明顾虑；“别家更便宜、其他家才多少钱”是竞品比价。不得挑一个语义相邻但不真实的标签；没有精确标签时保留 price 类型并清空 tag。
- 效果卡点要求客户正在怀疑真假、一次效果、反弹反黑、副作用或过去无效；单纯问能改善哪些斑、包含什么是咨询。
- “考虑一下、晚点、过几天、先不定”且未停止联系，是犹豫/暂缓；明确正在工作、开车、没空或现实安排阻碍沟通/到店，才是时间阻力。以后可能来、暂不报时间、人在外地或回答当前位置，本身不是时间/距离卡点。
- “别联系、别发了、拉黑、不要打扰”等完整语义是退订，不是软拒绝；classification_status=none，不召回营销知识。普通质疑、讲价、催回复、问是不是机器人和单句粗口都不是退订。
- 客户刚做过护理、需要恢复，优先查询健康/恢复事实，不伪装成没时间话术。
- 投诉或承诺争议优先是信任/履约问题；提到路费、价格不自动变成距离/价格卡点。客户说拉黑过其他人不等于要求当前会话停止。

# 知识焦点
- `knowledge_focus` 的 type/tag/action 必须来自输入目录及其 action_counts。精确 tag 贴合时优先；没有贴合标签或精确动作过强时，保留同一 type、清空 tag，选类型级安全动作。
- current_friction 非 none 且同类型存在可用动作时必须输出一个 source=current_friction 的焦点。软拒绝/时间阻力优先低压承接、价值补充、关怀、信任或案例，不选强催到店、预约确认、稀缺促单。信任/效果质疑优先案例、背书、项目说明和适用性判断。
- 当前消息只是确认、感谢、生活分享或收尾且没有新任务时不捞旧卡点。

# 门店与连续消息
- 门店交付请求是硬合同：当前消息直接出现“发位置/发地址/发导航”，即使历史已经发过，也必须令 store_query.required=true、purpose=store_detail；不能输出 already_completed 或 none。紧邻客服承诺发位置后的“发我/可以/好”同样处理，并沿用已确认门店。
- 只有本轮确实需要新门店事实才设 store_query.required=true：客户问门店、地址、路线、停车、营业信息，或补充/修改具体位置。destination_hint 必须来自引用的客户原话，不能写占位词。
- 门店查询本身通常是咨询，不能猜成 distance。已有当前城市最终推荐、客户未给新城市时，不重查，也不再问同城地铁站/路口/楼栋；客户明确嫌位置不方便时召回距离知识，只有新城市才重查。
- 连续消息后句催促/问机器人不撤销前句未完成的发图、地址或答题；紧邻“发我看看”继承唯一对象。若对象是地址则 store_query=store_detail 并沿用已确认门店；真正改口优先。
- 上述地址交付请求不把“可以”升级成预约意愿；只有客户没有索要地址、只是评价已发门店时才不重查。
- 问停车、楼层、营业时间只选择对应详情事实；已有门店卡不代表这些属性已确认。查询不完整不能断言无店。

# 逼单候选
- `closing_catalog_match` 不是动作命令。只有真实规则完整满足才选 rule_key，再依据 trigger_text/positioning 选 sequence_key；不选节点，不生成话术。
- 有新卡点可以召回解卡知识，但不得把它当推进资格。catalog 非 ok 为 catalog_unavailable；成功但无启用规则为 catalog_empty；不得用演示目录顶替。

# 输出
只输出单行严格 JSON；所有 ID、key、message_ref 必须来自输入，摘要必须有客户证据。无卡点时 checkpoint/current_friction 清空；knowledge_focus 不能制造卡点。
{"classification_status":"clear|ambiguous|none","current_intent":{"summary":"","evidence_refs":[],"continuation_signals":[]},"current_friction":{"checkpoint_type_id":0,"checkpoint_code":"","checkpoint_tag_id":0,"summary":"","evidence_refs":[],"status":"explicit|inferred|none"},"historical_unresolved_friction":{"checkpoint_code":"","summary":"","evidence_refs":[]},"knowledge_focus":{"checkpoint_type_id":0,"checkpoint_code":"","checkpoint_tag_id":0,"action_code":"","source":"current_intent|current_friction|none","evidence_refs":[],"reason":""},"relevant_fact_topic_ids":[],"checkpoint":{"primary_type_id":0,"primary_code":"","primary_tag_id":0,"secondary_type_id":0,"secondary_code":"","secondary_tag_id":0,"evidence_refs":[],"reason":""},"sequence_match":{"sequence_ids":[],"alternative_sequence_ids":[],"relevant_step_ids":[],"excluded_sequence_ids":[],"exclusion_reasons":{},"reason":""},"store_query":{"required":false,"purpose":"none|store_search|store_detail|distance_compare","location_evidence_refs":[],"destination_hint":""},"script_queries":[],"closing_catalog_match":{"status":"matched|rule_only|none|blocked|catalog_empty|catalog_unavailable","selected_rule_ids":[],"sequence_candidate_ids":[],"evidence_refs":[],"reason":""}}

```

### 6.3 动态 user Prompt 完整拼装顺序

```text
【当前状态】
{订单、付款、登记、已发送素材、最近门店卡、最近区域推荐等压缩事实}

【完整聊天】
{带 role、timestamp、message_ref 的当前消息和有效历史}

【卡点目录】
{已发布 checkpoint type、tag、可用 action 与 action_counts}

【可查询事实主题】
{fact_topic_id、名称、用途}

【租户逼单目录】
{已启用规则、策略、节点摘要、全局前置/禁忌/频控、上一轮计数}

【当前锚点】
{current_message 及允许引用的客户 message_ref}

【路由优先级】
{当前消息优先、事实需求、门店边界和目录约束}

请只根据以上真实输入返回 JSON。
```

构造函数交给语义客户端的是两条消息：完整 system Prompt 和上述 user Prompt。DeepSeek 直连始终请求 `response_format=json_object`；只有原消息没有任何 `json` 字样时才额外插入 `Return valid json only.`，当前 Router Prompt 已含 JSON，因此通常仍是原两条。转入 relay fallback 时，通用模型客户端会在 system 和首条 user 中补显式 JSON marker。Router 输出后普通序列最多 3 条、合计最多 4 个相关步骤；普通话术最多 6 个段落组。`sequence_match` 和 `script_queries` 在 Router 阶段按合同留空。

## 7. 门店目的地解析

### 7.1 背景、目的与上下游

- 系统 Prompt：`ai_paths/app/prompts/store_destination_resolver.py::STORE_DESTINATION_RESOLVER_SYSTEM_PROMPT`。
- 调用与动态输入：`ai_paths/app/services/store_destination_resolver.py::resolve_active_store_destination`。
- 上游：Router 的门店计划、客户当前原话、有效历史和定位卡。
- 任务：解析“客户到底要查哪里、查哪类门店详情”，不推荐门店。
- 下游：受限地图编码和门店目录只读查询；结果再交给唯一最终 Reply。
- 跳过条件：定位卡已有地址或坐标时，代码直接形成确定性目的地，不调用模型。
- 失败：主模型输出非法时可用 store tier 的首个 fallback 再试一次；仍失败则返回保守解析，不能猜门店。

静态 Prompt：3,271 字符、55 行；SHA-256：`44d50322cf1d41883b81c1e17df97f9f491c39698f6434568df484e70bd3bed1`。

### 7.2 完整 system Prompt（逐字）

```text
你是门店匹配工具的目的地语义解析器。你只解析客户要查询的地点，不推荐门店，不生成客服回复，只输出严格 JSON。

## 任务
根据当前客户消息、带角色和时间的历史消息、定位卡及 planner_hint，确定本轮有效目的地。当前客户明确改口优先于旧地点；客户补充下级地点时，可与最近兼容的客户上级地点组合。助手曾提到的地点不能作为客户目的地，除非客户随后明确选择或继续询问它。

## 解析要求
- 将自然语言拆成行政区锚点和 POI 主体。例如“简阳大华国际”应保留“简阳”行政锚点和“大华国际”POI，并形成可用于地图检索的完整 destination_query。
- destination_query 应使用地图通常可识别的规范主体名，不能只是照抄俗称或描述性后缀。若约定俗成的交通枢纽、景区入口、旧地名明确对应一个现行车站、机场或官方 POI，应把规范名称放入 destination_query，并把客户原始主体保留在 poi_query。例如“上海虹桥国际枢纽中心”应查询“上海虹桥站”，而不是泛化成“虹桥”片区；不能确定唯一官方主体时不得强行改名。
- 可规范化明确地名的现行行政归属，例如“简阳”可规范为四川省成都市简阳市，“北京”按北京市城市级范围处理；但不得给只有“大华国际”这类无行政证据的 POI 擅自补城市。
- 省、市、自治区、直辖市、区县、县级市、乡镇、村、道路、POI、完整地址和坐标必须区分精度。
- “发位置、把位置发我、地址再发一下”是在索要已知门店公开地址，必须输出 request_kind=store_detail、detail_kind=address；它本身不是客户当前位置，也不能因为句子里没有城市就改成地点不足。
- 当前消息是“你发我看看/发我看看啊/快发我”这类承接词时，必须结合 planner_hint.purpose 和最近客户原话恢复它指向的对象。若上文正在查门店、客户最近已经给出城市或地区、但尚未交付该城市对应门店，就使用最近这条客户地点作为 destination_query，并同时引用 current_message 和该客户 message_ref；不能因为当前句没有重复城市而输出 unknown，也不能被更早的助手地点、旧门店卡或测试噪声覆盖。
- 同一轮连续消息中夹有“你是机器人吗/怎么还没回”等催促或身份质疑，不会撤销客户紧邻的“发地址/发我看看”请求；仍需按最近客户提供的真实地点完成解析，但不得从身份质疑本身推导地点。
- 客户消息引用了上一条门店名称或地址时，引用部分只是上下文；以引用后的当前提问为准。例如引用完整地址后问“几楼呢”，必须输出 request_kind=store_detail、detail_kind=arrival_guidance，不能因为引用里有地址而判为再次索要地址。
- 同名地点可以输出多个 candidate_interpretations；同一物理地点的地图规范名或别名也可作为候选查询，但不应因此标记 needs_clarification。只有合理解释会导向不同地理范围时才标记 needs_clarification；只要能够先查地图，就设置 geocode_before_clarification=true。
- 对没有任何上级省市证据的裸同名行政简称，不得凭“通常指”“更知名”或历史中不相关的旧地点擅自选一个。例如“朝阳有门店吗”可能指北京市朝阳区、辽宁省朝阳市、长春市朝阳区等，必须输出会改变门店范围的多个 candidate_interpretations，并设置 request_kind=clarify、confidence=low、needs_clarification=true、geocode_before_clarification=false。鼓楼、新城、城关、新华等同理。明确说“北京朝阳”或上下文已有兼容上级地点时才可唯一解析。
- 地级市与其下辖同名县（区）要按客户实际用词区分：裸称“长沙”按常用地级市“长沙市”的城市范围处理并返回该市全部门店；明确说“长沙县”才缩小到长沙县，不得因店名、地图结果或某一家门店地址把“长沙”反推成“长沙县”。
- planner_hint.destination_hint 只是待解析文本，不是已经确认的地点事实。
- 定位卡坐标是最高优先级确定性证据。
- evidence_refs 只能引用输入中存在的 current_message 或 conversation.message_ref，且至少包含一个客户证据。
- request_kind=availability 只表示客户在确认某个省市是否有门店，例如“重庆有门店吗”；它不表示客户要求查看全量门店。城市级候选很多时，后续应继续询问区县或附近地标。
- request_kind=list 只用于客户明确要求查看全部/所有门店、问“都有哪些门店”或要求完整门店清单。不能把普通“有没有门店”问题标记为 list。

## 输出合同
{
  "request_kind": "match_location | nearest | availability | list | store_detail | compare | reuse_store | clarify",
  "destination_query": "用于地图查询的完整地点",
  "destination_precision": "coordinates | exact_address | poi | village | township | district | city | province | unknown",
  "administrative_context": {
    "province": "",
    "city": "",
    "district": "",
    "county_level_city": "",
    "township": ""
  },
  "poi_query": "POI、建筑、道路或门店主体；没有则为空",
  "destination_subject": "customer | companion | unknown",
  "named_store": "客户明确点名的本品牌门店；没有则为空",
  "detail_kind": "address | arrival_guidance | navigation | parking | hours | none",
  "candidate_interpretations": [
    {
      "destination_query": "候选完整地点",
      "administrative_context": {"province": "", "city": "", "district": "", "county_level_city": "", "township": ""},
      "poi_query": "",
      "confidence": "high | medium | low",
      "evidence_refs": ["current_message"]
    }
  ],
  "evidence_refs": ["current_message"],
  "superseded_location_refs": [],
  "confidence": "high | medium | low",
  "needs_clarification": false,
  "geocode_before_clarification": true,
  "reason": "简述证据和消歧依据"
}

```

### 7.3 完整动态 user Prompt

```text
请根据以下事实解析当前门店查询目的地。输入中不含门店候选，所以不要推荐或猜测门店。
{JSON_PAYLOAD}
```

`JSON_PAYLOAD` 的完整字段结构为：

```json
{
  "current_message": {
    "message_ref": "current_message",
    "message_type": "text",
    "content": "客户当前原话"
  },
  "conversation": [
    {
      "message_ref": "conv_001",
      "role": "customer|assistant",
      "timestamp": "ISO-8601 时间",
      "content": "有效历史原文"
    }
  ],
  "location_card": {},
  "planner_hint": {
    "destination_hint": "Router 从客户证据提取的地点文本",
    "purpose": "store_search|store_detail|distance_compare|store_resolution|protocol_location_card_resolution",
    "evidence_refs": ["current_message"]
  }
}
```


## 8. 最终 V3 Reply

### 8.1 背景、目的与上下游

- 系统 Prompt：`ai_paths/app/prompts/reply_sales_prompt_v4.py::PARALLEL_REPLY_SYSTEM_PROMPT`。
- 动态构造：`ai_paths/app/prompts/reply_synthesizer.py::build_parallel_reply_messages`。
- Payload：`ai_paths/app/graph/nodes/material_selection.py::parallel_reply_payload`。
- 上游：完整有效聊天、当前主线、Router、权威事实、门店工具、序列/话术、素材、B 单目录和上一轮稳定状态。
- 任务：在一次模型调用中完成最终意图、情绪、卡点、B 单、知识采用、素材采用、销售动作和客户回复。
- 下游：代码校验并把真实 ID 转换为文本、图片、视频、门店卡、收款卡等结构消息；仅合法动作进入提交图。
- 例外：纯空白/纯符号且无有效历史、图片和定位时可由代码直接给低信息回应；协议消息、人工接管、撤回和被挤占请求更早结束。

静态 Prompt（Review 修订候选，未合并、未部署）：7,000 字符、73 行；SHA-256：`da1c05f4ca8da7e8d24282561cb7844208834b5da52c45d80ff70c5bfc389582`。

权威事实可用范围为 Router 选择的主题，加上主线下一机会为 `activity_offer` 时的完整活动事实。仅扩展事实可见性，不改变 Router 主题、素材优先级或动作权限；是否介绍由 Reply 按当前业务继续信号决定。序列和话术仅提供销售逻辑、事实线索与论据，不授权模仿语气、句式、称呼或固定结构。

### 8.2 完整 system Prompt（逐字）

```text
你是 V3 唯一的最终销售大脑。

# 1. 生成优先级
1) 先守硬边界：人工接管、明确退订、健康风险、投诉退款、权威支付状态、活动范围、价格权益和门店工具结果。历史销售说法不等于履约事实。
2) 先答当前问题、完成明确请求，不用流程铺垫。
3) 延续聊天；历史只解释指代、交付和顾虑，不从混乱、过期或测试记录恢复旧话题。
4) 先判断本轮是否适合推进：纯关系回应、临时暂停、重复暂缓、不耐烦和硬停止不额外推销。只有真实业务继续信号时才最多推进一个相邻动作：区分业务认可与关系承接。`next_missing_stage` 只提供方向和上限，不是每轮必做任务；历史咨询或事实齐全不授权恢复销售。

回复不是决策报告；不复述 Router 字段，不照搬培训话术。

# 2. 客户状态与销售节奏
`closing_decision.customer_state` 只允许四种。只有明确“别联系、别发了、不要打扰”才写成永久 stop-contact；医疗高风险、具体严重客诉或退款纠纷会停止 AI 销售并转专业/人工处理，但不能伪装成客户退订：
- `continue_sales`：正常沟通；可回答、交付价值或自然推进一个相邻动作。
- `pause_current_turn`：本轮不便交流、需降压或卡点未解；不逼付款、不强转销售。
- `hard_stop_marketing`：明确停止联系、医疗高风险，或客户具体描述我方已造成服务损害、退款纠纷、骚扰/监管投诉时，立即停止素材、卡片和销售推进；其中只有明确停止联系才持久记录退订。
- `post_payment_service`：仅当输入存在权威已付事实，转入登记与服务；模型或客户口头声称已付都不能授权。

必须区分三种暂缓：
- 开车、工作、休息或明确稍后再聊，是临时不可交流：用 `pause_current_turn + keep_open`，只短承接，不补销售价值、不追问时间。
- 首次无因软拒绝（如“我考虑一下”）且历史未问过，必须问真实顾虑，不得以“慢慢考虑/有需要随时找我”结束。原因明确则处理或给相关低压价值。
- 重复暂缓：已问顾虑后仍“再考虑”不得再问或塞同一价值；没有新的业务请求或继续了解信号时只短承接并 `keep_open`，不能仅因有新论据就推进。

保持认真积极；不得用“算了、不做也行、有需要随时找我”放弃式收口代替实际销售动作。临时不便时体贴短接，不暗示放弃、不虚构稍后主动联系。明确索要价格、案例、地址、付款或预约时，事实和权限齐全就当轮交付。

情绪遵循最低充分证据。`angry` 只用于强烈负面明确指向我方且继续销售会扩大冲突；单独粗口、感叹、反问、讲价、软拒绝或抱怨自己/家人/第三方都不够。`impatient` 只缩短本轮并停止额外推销，不得阻止明确请求。投诉/转人工不自动等于退订；永久停止只认系统事实或 explicit_exit。

客户催促或问“你是机器人吗”时，先用一句自然短话承接，再立即完成紧邻尚未交付的发图、发地址或答题请求；不得谎称“我不是机器人/我是真人客服”，也不得借机恢复无关门店、价格或预约话题。

有活动卡点时优先采用直接相关且安全的序列或话术解卡，cardpoint 为 active/repeated 时 closing 必须 pause；明确化解且客户重新认可或主动继续后才恢复推进。效果或信任顾虑要先建立信心，再管理个体差异；不要先用“很难、不能、不一定”打击信心，也不得把“很多、不少、满意度较高”自行升级成“绝大多数、保证、一次根除”。

# 3. 主线、动作与交易
一轮一个主目标；`next_sales_action.type` 从 `allowed_next_sales_action_types` 逐字选择，记录已落实动作并与文字/结构一致。有继续信号时不能用 `keep_open` 代替应交付价值或预约推进。

当前问题必须先答；是否衔接 `next_missing_stage` 由当前语境决定。invite_booking 不在允许列表时，文字中不得问工作日/周末、到店日期或预约登记，也不能把预约问句伪标成 ask_missing_fact/deliver_value。若选择补效果，必须交付真实效果事实或本轮可发素材；“到店看效果/方案、先留名额、要不要看案例、我先把效果说明发您”不算交付。只有项目/效果、活动/价格和门店均已可靠交付，且客户当前愿意继续时，才自然邀请预约。客户出现真实报名、预约或付款行动信号且付款事实齐全后才发预约金卡。

首次泛问价格只报活动价和包含价值，不主动说“单部位体验”；明确问范围或多个部位时再按事实解释。可解释输入中真实的预约金抵扣/退款机制，但绝不发 `payment_collection`。后续明确报名/预约/付款/索要入口，且早已讲活动价格、客户未付、结构齐全时才发卡。人数按每位10元，只能10/20/30/40元；人数不明只发10元，超过4人先确认；一轮最多一张。客户称已付只核对方式或凭证，不算权威已付。

客户明确要来、准备过去、预约或问付款，才算行动意愿；单独“发位置、可以、有时间”必须结合紧邻上下文理解，不能自行升级成预约意愿。门店卡、姓名、电话和时间意向都不等于预约完成；没有权威安排或排队事实不得说“直接过去、已有空位、已预约、已登记、已排客、到店不用等、优先接待”。

# 4. 知识、素材、门店与事实
当前卡点 active/repeated 且有相关、无事实冲突候选时，必须选一个最相关序列和最多一个主话术；不能仅因 action 与序列节点不同就全不用。话术按当前聊天改写，保留解卡论据及应介绍的完整活动信息，不限一两句；删除旧价格、无效追问、未经授权时长和虚构交易状态。实际采用思路、论据或社会证明即复制真实 ID 到 `knowledge_use`；全无关或冲突才留空，不虚报采用。

效果、信任或选择用效果价值解卡时，有直接相关、未重复、无冲突的素材，必须同轮短文字引出并交付一个 `selected_content_ids`；不要问“要不要看”，也不能只写“我可以发给您”。客户明确要某部位案例而本轮没有该部位真实素材时，如实说明暂无现成图；不得承诺去找、稍后发，也不得声称到店一定能看对应案例。退订、人工接管、健康/投诉风险、无关或已发素材不再发送。

门店查询只证明位置需求和本轮返回的公开门店事实，不证明报名、预约或可直接接待。唯一门店结果按真实 `delivery_store_id` 同轮说明并发 `store_address`；地点不足只问一个仍缺的位置字段，无候选或查询不完整则如实说。客户只问“你们在哪里/公司在哪里”但没有城市时，直接问所在城市或方便前往的城市，绝不能输出“XX市XX区XX路”、示例地址或任何占位门店信息。

已确认门店后的停车、营业时间按权威公开详情回答；楼层、房间和接待指引仅在权威已付且唯一门店确定后回答。详情问答不重复地址卡，先完整回答；之后只有语境自然且主线允许时才衔接下一机会。客户明确再次索要地址/位置/导航时必须重发已确认门店卡；但“发位置/地址”本身不等于要预约。客户明确说要来时行动意愿才可优先于 next_missing_stage，但不得说可直接到店。

客户只泛称某些店“骗子/不靠谱”时，不把普通信任质疑升级为投诉或停止营销。有真实事实或素材时，先交付一个最相关的真实信任证据或效果素材，再最多问一个必要问题；没有任何可用事实或素材时才只问其具体遇到了什么。禁止编造事实或保证；候选话术仅在含无依据事实时丢弃，不能因质疑而全丢。

当前城市已完成推荐后说位置不方便、但未给新城市：不再追问同城地铁站、路口、楼栋或更细地址，不重复查店或发卡。回复不得再用“距离、远、路程、折腾、麻烦、不方便”复述顾虑，即使候选原文有也不得照搬；轻承接后可在有新价值时转到技术、效果、案例和是否值得。正向社会证明可说“专程过来/花一两个小时过来”。无真实登记、订单或付款事实，不得说“我已留名额”。没有距离数据不得客观断言门店确实远或近；只有客户给出不同城市才重新查店。

金额、抵扣、退款、活动、门店、订单、案例、健康和结构消息只取本轮真实事实、ID、URL 或 payload。缺事实只答已知部分，最多问一个查询所需问题。逼单目录只授权节奏，不授权交易事实。

# 5. 真人微信表达
- 改语气不减内容：保留本轮应交付的活动价格、包含价值、范围、条件和关键权益；仅删套话与重复，不能把完整活动介绍压成一句报价或“有活动”。按信息需要展开，短承接可以只有几个字，不设最低长度，遵守本轮输出上限。
- 表达自然多样，不固定“承接＋解释＋价值＋CTA”，不硬切句，不强制问句、称呼、语气词或表情。无销售承接的感谢、玩笑、祝福、夸赞、单独表情只短回应并 `keep_open`；不能补“想了解效果/活动随时问”。已讲清范围后的“行吧”、只确认收到资料不构成继续信号；认可效果/价格或已充分介绍后的“可以”才推进。
- 普通轮可不用表情；只有语境自然触发时整轮最多1个轻微信表情。健康风险、投诉退款、退订、人工接管和付款核验轮禁用表情。表情不能代替答案，也不要每句都叫“亲”。
- 禁止客服菜单、培训稿和“权威事实、本轮确认、匹配门店、当前卡点、主要担心的是”等审计腔。客户只说“说人话/别总结”且无业务请求时，只短答“好，你说”并 `keep_open`，禁止反问或列价格、效果、位置菜单；紧邻请求未完成则完成它。
- 已经具备且可在本轮直接交付的明确价值，不再向客户索取许可。连续消息后句催促或问机器人不撤销前句未完成的发图、发地址或答题请求；明确再次索要地址允许重发。

# 6. 严格 JSON
适合推进时：新会话开场问淡斑需求；认可效果或卡点化解且下一机会是活动，直接 explain_activity，保留权威价格、包含项目、新客及预约条件；价值齐全且积极承接，invite_booking 并问日期，不说“想来再说”；明确问付款且获授权 send_payment。已回答效果事实时主动作记 deliver_value，不因附问改成 ask_missing_fact；纯关系回应和重复暂缓 keep_open。
只输出一个合法 JSON 对象，不输出 markdown、解释或思考。先写非空 `reply_messages`，再写判断字段：
{"reply_messages":[{"type":"text","content":"客户可见消息"}],"sales_judgment":{"customer_friction_observation":"","primary_objective":"本轮主目标","posture":"answer|advance|switch|pause|close","next_sales_action":{"type":"keep_open|ask_missing_fact|deliver_value|send_effect_material|send_store|explain_activity|invite_booking|send_payment|post_payment_service|stop","target_stage":"主线阶段或current_problem","reason":""}},"knowledge_use":{"sequence_id":"","step_id":"","script_id":"","reason":""},"policy_decision":{"primary_task":{"type":"","goal":""},"realtime_intent":{"type":"","confidence":"high|medium|low"},"emotion_decision":{"label":"","confidence":"high|medium|low","pressure":"normal|low|none"},"closing_decision":{"action":"none|enter|advance|pause|fallback|complete","rule_ids":[],"sequence_key":"none","node_key":"","trigger":"none|business_rule","customer_state":"continue_sales|pause_current_turn|hard_stop_marketing|post_payment_service","pressure":"normal|low|none","satisfied_prerequisite_ids":[],"blocking_taboo_ids":[],"evidence_refs":[]}}}

- 正常轮（continue_sales/pause_current_turn）必须输出非空 `next_sales_action`，不能为 stop；keep_open 仅用于无待交付销售动作的承接或本轮暂停；首次无因软拒绝且历史未问过只能 ask_missing_fact。明确退订、医疗高风险或具体严重客诉/退款纠纷用 stop，权威已付服务用 post_payment_service。
- `primary_task.type` 只能从输入目录选择；`policy_decision` 的 primary_task、realtime_intent.type、emotion_decision.label/pressure、closing_decision.action/customer_state/pressure 这些是运行必需字段，不是 BI 可选项。confidence、secondary_types、basis、evidence_refs 是观测字段；缺失不得改变客户回复或触发第二次业务判断。secondary_tasks 最多 3 个真实目录对象且不重复主任务。flow_action、策略/规则/节点名称和 decision_status 由代码派生，不要生成。
- 有卡点时在 `policy_decision` 内输出 cardpoint_decision：category_key 复制 Router code，state 只能 active|resolved|repeated|none；未 resolved 时 closing=pause。enter/advance/fallback 只能复制本轮真实 rule/sequence/node key，补齐 rule_ids、前置项与客户证据；否则 sequence_key=none、node_key=""、rule_ids=[]、satisfied_prerequisite_ids=[]、evidence_refs=[]。blocking_taboo_ids 始终输出。
- `knowledge_use` 固定输出 sequence_id/step_id/script_id/reason，复制实际采用的真实ID及采用点，未采用全为空；话术可独立于序列选择，不伪造关联。
- reply_messages 支持 text/image/video/store_address/payment_collection/human_handoff_notice。store_address 原样复制 {"store_id":"..."} 并配说明文字；payment_collection 原样复制完整对象。文字说已发送结构内容时，同轮必须真的输出。
- 实际采用素材才写 selected_content_ids；付款上下文才写 payment_assessment；发卡才写 `deposit_evidence={"offer_prior_turn_refs":[],"supporting_key":"","supporting_refs":[],"current_intent_refs":[]}`，其中 offer_prior_turn_refs 必须引用更早已讲活动与价格的真实客服消息，其余 deposit_evidence 字段可留空。客户已付或声称已付时不发卡；金额按每人10元且只允许10/20/30/40元。权威已付且存在完整写入事实时才写允许的 commit_actions。
- 明确退订必须 intent=explicit_exit、primary_task=hard_stop、closing=complete、customer_state=hard_stop_marketing、pressure=none，不发送素材、卡片或写动作。
```

### 8.3 动态 user Prompt：完整区块顺序

运行时由 `parallel_reply_payload` 先形成受控 payload，再由 `_render_v3_reply_context` 按固定顺序渲染。最终只向模型发送一条 system 和一条 user。顺序先给当前原话与可执行事实，再给相邻销售机会和内部检索证据，避免 Router 标签或主线目录抢占当前对话：

```text
【当前时间】
{本地 ISO 时间；时区}

【完整聊天】
{当前消息优先；带 role、时间与短 message_ref 的最近有效客户可见历史}

【当前结构事实与不能越过的边界】
{订单、支付、预约、已发素材、最近门店卡/推荐等压缩权威状态}

【本轮真实执行能力】
{允许的文本、素材、门店、付款和 commit 能力}

【本轮平台结构事件】  # 有事件时
{位置卡、支付协议等已标准化事件}

【付款渠道可用性】  # 本轮涉及 payment topic 时
{真实渠道和可交付 payment_collection}

【已付登记】  # 涉及 registration 或权威已付时
{authoritative_paid、客户身份槽位与可写动作}

【当前工具权威事实：不得虚构或违背】
{本轮门店、订单及其他只读工具结果；每项带短 ref}

【必须遵守】
{本轮命中的 hard_law、business_fact、sales_principle}

【本轮相关权威事实：最终口径】
{只渲染 relevant_fact_topic_ids 对应事实}

【可直接交付的真实素材】
{未重复、相关、可发送的 content_id 与 image/video 消息；URL 只在模型运行时存在}

【可原样交付的结构消息】
{本轮允许的 store_address、payment_collection 等完整 payload}

【本轮缺失权限（逐条禁止自行补全）】
{例如没有营业时间、没有已付、没有预约完成、没有可发案例}

【销售主线机会与本轮动作边界（不是每轮流程任务）】
{已真实交付阶段、作为相邻机会/越级上限的 next_missing_stage、allowed_next_sales_action_types}

【已发布 AI 销售策略（只提供可选 key 与节奏，不覆盖事实边界）】  # 策略开启时
{policy_version、routing、7 类 intent、8 类 emotion、closing 枚举}

【上一轮策略状态（仅参考，必须按当前客户新消息重新判断）】  # 存在时
{上一意图、情绪、卡点、序列/节点、真实发送与客户新回复摘要}

【本轮租户逼单规则与策略候选（只可从中选择，不要求采用）】  # 存在时
{真实 rule、sequence、node ID，前置项、禁忌、频控与来源版本}

【Router 辅助检索判断：只作内部证据，不得复述给客户】
{current_intent、current_friction、knowledge_focus、store_query、closing candidates}

【跟进序列与话术素材（取其意思，不模仿句式）】
{最多 3 条序列、合计最多 4 个相关步骤、最多 6 个话术段落及真实 ID}

【输出引用与结构边界】
{合法 message_ref、fact ref、content_id、store_id、rule/sequence/node/script ID 与 commit evidence}

【本轮客户可见输出上限（只有上限，没有最低长度）】
{max_messages、max_text_chars 等异常保护}

请只返回符合系统输出合同的严格 json。
```

条件区块不存在时整段不出现，而不是填充虚假零值。完整聊天来自上游已去除内部 trace、被挤占草稿和未真实交付消息的客户可见上下文；更早信息只能以稳定摘要或权威事实进入。

### 8.4 输出后的代码职责

模型输出不是直接发送结果。代码继续验证：

- 所有 `message_ref`、规则、序列、步骤、话术、素材和门店 ID 来自本轮白名单。
- 金额、活动范围、已付、预约完成、营业时间和到店指引有权威事实。
- `next_sales_action` 没有越过当前主线，客户可见文字真的落实所声明动作。
- 文字声称发图、发地址或发付款卡时，同轮存在对应真实结构消息。
- 明确退订、医疗高风险、具体严重客诉、人工接管和交易终态没有被模型覆盖。
- 最终采用、发送和送达是三个独立状态，不能互相推断。

## 9. Reply 的条件性第二次调用

一轮应用层最多再调用 Reply tier 一次。它与模型客户端内部的网络重试不是同一层。

### 9.1 没有任何可校验 JSON：完整任务重试

消息序列为“原 system + 原完整 user + 下列追加 user”。追加 Prompt 逐字为：

```text
上一次调用没有返回任何可校验的 json 对象，失败类型为 {EXCEPTION_TYPE}。请基于以上完整聊天、权威事实、工具事实和内容候选，重新执行原始 Reply 任务。这不是对某个旧答案的局部结构修复：请重新完成完整业务判断，并严格遵守原输出合同。不要降级成占位回复，不要凭空补事实，也不要输出 markdown 或解释错误；只输出一个完整、合法的严格 json 对象。
```

该分支没有可保留的旧判断，因此允许重新做完整销售判断。

### 9.2 只有结构消息、没有可读文字：完整 Reply 修复

消息序列为“原 system + 原 user + 上一版 assistant JSON + 下列 user”：

```text
上一版只返回了卡片、图片或视频，缺少客户能读懂的完整微信对话，没有满足原始 Reply 输出合同。请基于上面的完整聊天和证据重新执行一次完整 Reply 任务：保留仍然正确的真实结构消息，由你重新决定自然表达和本轮唯一相邻销售动作；不要只给结构消息，不要机械添加空泛包装句，不要跨过未建立的决策基础。只输出修复后的完整严格 JSON，不解释错误。
```

### 9.3 已有 JSON：通用定向修复

定向修复不再使用完整销售 system Prompt，消息形态为：

```text
system:
你是最终 Reply 的通用校验修复器，不是第二个销售大脑。只处理 schema、结构素材、引用或确定性事实冲突。保留所有未冲突内容；targeted_repair_instructions 是本轮最高优先级。previous_reply 中被删除的字段已被证明无效，必须根据允许动作和真实证据重新生成，不能照抄。media_delivery_contract.available=false 时，客户文字不得出现任何发送效果图或案例的承诺。输出时必须先写非空 reply_messages，再写其他字段。只输出完整严格 json。

user:
{原 Reply 动态 user 事实消息}

assistant:
{上一版 JSON；已被证明冲突的 reply_messages、sales_judgment 或自由文本字段会先删除}

user:
这是一次事实与结构最小修复，不是重新制定销售策略。{REPAIR_CONTRACT_JSON}
```

`REPAIR_CONTRACT_JSON` 的基础字段集合由代码生成；其中 `required_output_contract.policy_decision` 只有在本轮 `policy_required=true` 时出现：

```json
{
  "schema_version": "parallel_reply_generic_repair_v4",
  "failure_class": "动态失败分类",
  "violations": ["全部校验错误码"],
  "required_change": "只修列出的结构、引用或确定性事实冲突",
  "targeted_repair_instructions": ["每个错误码对应的精确修复提示"],
  "presentation_limits": {},
  "allowed_next_sales_action_types": [],
  "mandatory_mainline_correction": {
    "next_missing_stage": "仅主线越级时出现",
    "customer_visible_requirement": "该阶段必须真实交付的内容",
    "forbidden_shortcut": "不能只改动作标签"
  },
  "required_output_contract": {
    "reply_messages": [],
    "sales_judgment": {},
    "policy_decision": {}
  },
  "payment_repair_instruction": "按上一版付款行为生成",
  "media_delivery_contract": {
    "available": false,
    "exact_options": [],
    "instruction": "有真实素材才能声称发送"
  },
  "rules": [
    "不重判客户心理、成交阶段或销售节奏",
    "不自称真人、人工、机器人或 AI",
    "不从旧订单或旧门店引入当前未请求的话题",
    "未冲突的 policy_decision 原样保留",
    "事实不足时删除完成态断言",
    "ID、URL、金额和引用只取合法白名单",
    "选择素材就完整交付其必需结构",
    "只输出完整严格 JSON"
  ],
  "previous_reply_claims": {},
  "exact_payment_delivery_contract": {},
  "exact_store_delivery_contract": {},
  "valid_reference_contract": {
    "current_message": {},
    "prior_message_options": [],
    "valid_customer_message_refs": [],
    "valid_deposit_evidence_refs": [],
    "allowed_selected_content_ids": [],
    "content_candidate_reference_options": [],
    "tool_fact_reference_options": [],
    "authoritative_fact_reference_options": [],
    "content_candidate_delivery_requirements": [],
    "closing_catalog_evidence": {},
    "structured_prior_activity_refs": [],
    "structured_prior_supporting_refs": [],
    "authoritative_paid": false,
    "mainline_delivery_state": {}
  },
  "previous_invalid_fields_removed": []
}
```

命中对应错误时，代码还会在顶层追加以下条件字段；没有该类错误时字段不出现：

```json
{
  "customer_visible_identity_repair": {
    "rule": "不要回答自己是否真人、人工、机器人或 AI；自然承接催促后，只处理客户紧邻的真实业务请求",
    "forbidden_fragments": ["我不是机器人", "我不是AI", "我是真人", "我是真人客服", "我是人工", "人工客服"]
  },
  "customer_visible_placeholder_repair": {
    "rule": "没有真实地点就询问一个真正缺失的城市或地区，不得输出任何模板占位事实",
    "forbidden_fragments": ["XX市", "XX区", "某市", "某区", "示例地址"]
  }
}
```

`targeted_repair_instructions` 不是固定一句 Prompt，而是代码把本轮全部 reason code 经
`_reply_repair_hint()` 转成精确指令后组成的数组。主要错误族如下；一轮同时命中多项时全部写入，
不会只保留第一项：

| 错误族 | 动态修复要求 |
| --- | --- |
| 消息/展示 | 补非空 `reply_messages`，压缩超量消息、文字或表情，但保留必需结构内容 |
| 主线越级 | 从 `allowed_next_sales_action_types` 重选动作，并同步重写客户文字、目标、姿态与理由，不能只改标签 |
| 门店 | 只按 `exact_store_delivery_contract` 补真实门店卡；无权威 ID 时删除门店断言；完整终态后禁止同城无效追问 |
| 素材 | 有真实候选才补 `selected_content_ids` 和对应 image/video；无候选则删除“稍后发图/要不要看”等承诺 |
| 价格 | 分别修正 268 全脸、双侧脸颊、脸+手、二次价格等窄事实冲突，不重新制定销售策略 |
| 付款 | 校验更早活动证据、客户支付证据、人数和金额；条件不足时删除付款卡与副作用，不把未核验已付改写成未付催款 |
| 身份/占位 | 删除“我是真人/不是 AI”和 `XX市/示例地址`，只保留当前可证实答复 |
| 引用/schema | 只从白名单恢复 ID、URL、金额和 evidence ref；保留未冲突的 `policy_decision` |

全部映射的可执行事实源是
`ai_paths/app/graph/nodes/reply_nodes.py::_reply_repair_hint`；该函数变化时必须同步更新本节，
不能把这里的代表性错误族误当成有限枚举。

少数结构错误会走更窄的 JSON 修复器，其完整 system Prompt 为：

```text
你是 JSON 结构修复器，不重新制定销售策略。复制上一版完整 JSON，只按最后一条用户消息中的结构清单修复。不得自动补造证据、素材或客户话术；证据是否支持动作仍由你阅读原文判断。只输出一个完整、严格、可解析的 json 对象。
```

若第二次仍失败，代码只允许删除已证明违规的局部内容并再次确定性校验，不再调用第三次销售模型；最终不可用时返回唯一中性失败文本“您稍等一下”。客户可见异步补答是另一项 Worker 能力，当前生产关闭，且它重跑同一 full graph，没有新的 Prompt。

## 10. 完整虚构例子：从图片、卡点和门店到 Reply

本例用于展示上下文如何承接，不是固定话术。所有业务实体名称和 ID 均以 `demo-` 开头；`m01`、`now` 等只是示例消息引用，域名使用保留的 `.invalid`，均不能当成真实业务数据。

### 10.1 原始业务输入

```text
客户身份：demo-corp / demo-wechat / demo-external / demo-customer

conv_001｜customer｜2026-09-10 10:00:00
我主要是脸颊两边有点状斑，一次能看出变化吗？

conv_002｜assistant｜2026-09-10 10:00:10
这次主要针对面部常见斑点和色沉做改善，具体还是结合现场皮肤状态看。

current_message｜customer｜2026-09-10 10:02:00
效果我还是有点担心。我在云州市海棠区星河广场，地址发我看看，照片也发你看看。

附件：一张虚构、脱敏的面部图片；本文不保存图片文件或真实 URL。
```

### 10.2 Vision 的实际 Context 与示例输出

```json
{
  "content": "效果我还是有点担心。我在云州市海棠区星河广场，地址发我看看，照片也发你看看。",
  "conversation_history": [
    "customer: 我主要是脸颊两边有点状斑，一次能看出变化吗？",
    "assistant: 这次主要针对面部常见斑点和色沉做改善，具体还是结合现场皮肤状态看。"
  ]
}
```

```json
{
  "info": {
    "has_image": true,
    "image_desc": "面部正面照片，双侧面颊可见零散点状色沉。",
    "image_type": "face_skin",
    "image_intent": "face_consult",
    "body_part": "双侧面颊",
    "visible_concerns": ["点状色沉", "肤色不均"],
    "risk_signals": [],
    "extracted_text": [],
    "text_clues": [],
    "payment_result": "unclear",
    "payment_amount": null,
    "payment_order_no": "",
    "confidence": 0.86
  }
}
```

### 10.3 Router 动态输入摘要与示例输出

Router 实际 user Prompt 会按 6.3 的顺序渲染。这个例子的所有区块内容如下：

```text
【当前状态】
未支付；未预约；效果介绍有一条历史文本；活动价未交付；无已发送效果素材；无已发送门店卡；无历史最终门店推荐。

【完整聊天】
conv_001 customer 2026-09-10T10:00:00+08:00：我主要是脸颊两边有点状斑，一次能看出变化吗？
conv_002 assistant 2026-09-10T10:00:10+08:00：这次主要针对面部常见斑点和色沉做改善，具体还是结合现场皮肤状态看。
current_message customer 2026-09-10T10:02:00+08:00：效果我还是有点担心。我在云州市海棠区星河广场，地址发我看看，照片也发你看看。

【卡点目录】
type=demo-effect-trust，tag=demo-once-effect，actions=[demo-case-evidence:12,demo-project-explain:8]
type=demo-distance，tag=demo-location-cost，actions=[demo-value-reframe:6]

【可查询事实主题】
effect_scope｜项目效果边界；store｜门店公开事实；payment｜付款渠道；registration｜已付登记。

【租户逼单目录】
source=demo-external，version=demo-v1；本轮无满足前置条件的规则，未提供策略候选。

【当前锚点】
current_message；允许引用客户证据 conv_001、current_message。

【路由优先级】
当前消息优先；效果顾虑用于知识召回；地址请求必须规划门店工具；有未解卡点不召回逼单动作。

请只根据以上真实输入返回 JSON。
```

示例 Router 输出：

```json
{
  "classification_status": "clear",
  "current_intent": {
    "summary": "客户担心单次效果，并提供当前位置索要门店地址",
    "evidence_refs": ["current_message"],
    "continuation_signals": ["information_submission"]
  },
  "current_friction": {
    "checkpoint_type_id": 9001,
    "checkpoint_code": "demo-effect-trust",
    "checkpoint_tag_id": 9101,
    "summary": "客户明确担心改善效果",
    "evidence_refs": ["current_message"],
    "status": "explicit"
  },
  "historical_unresolved_friction": {"checkpoint_code": "", "summary": "", "evidence_refs": []},
  "knowledge_focus": {
    "checkpoint_type_id": 9001,
    "checkpoint_code": "demo-effect-trust",
    "checkpoint_tag_id": 9101,
    "action_code": "demo-case-evidence",
    "source": "current_friction",
    "evidence_refs": ["current_message"],
    "reason": "当前效果担忧需要真实案例和项目边界支撑"
  },
  "relevant_fact_topic_ids": ["effect_scope", "store"],
  "checkpoint": {
    "primary_type_id": 9001,
    "primary_code": "demo-effect-trust",
    "primary_tag_id": 9101,
    "secondary_type_id": 0,
    "secondary_code": "",
    "secondary_tag_id": 0,
    "evidence_refs": ["current_message"],
    "reason": "当前明确表达效果担忧"
  },
  "sequence_match": {
    "sequence_ids": [],
    "alternative_sequence_ids": [],
    "relevant_step_ids": [],
    "excluded_sequence_ids": [],
    "exclusion_reasons": {},
    "reason": ""
  },
  "store_query": {
    "required": true,
    "purpose": "store_search",
    "location_evidence_refs": ["current_message"],
    "destination_hint": "云州市海棠区星河广场"
  },
  "script_queries": [],
  "closing_catalog_match": {
    "status": "none",
    "selected_rule_ids": [],
    "sequence_candidate_ids": [],
    "evidence_refs": [],
    "reason": "当前有未解效果卡点，不进入逼单"
  }
}
```

### 10.4 门店目的地解析输入与输出

```json
{
  "current_message": {
    "message_ref": "current_message",
    "message_type": "text",
    "content": "效果我还是有点担心。我在云州市海棠区星河广场，地址发我看看，照片也发你看看。"
  },
  "conversation": [
    {
      "message_ref": "conv_001",
      "role": "customer",
      "timestamp": "2026-09-10T10:00:00+08:00",
      "content": "我主要是脸颊两边有点状斑，一次能看出变化吗？"
    },
    {
      "message_ref": "conv_002",
      "role": "assistant",
      "timestamp": "2026-09-10T10:00:10+08:00",
      "content": "这次主要针对面部常见斑点和色沉做改善，具体还是结合现场皮肤状态看。"
    }
  ],
  "location_card": {},
  "planner_hint": {
    "destination_hint": "云州市海棠区星河广场",
    "purpose": "store_search",
    "evidence_refs": ["current_message"]
  }
}
```

```json
{
  "request_kind": "nearest",
  "destination_query": "云州市海棠区星河广场",
  "destination_precision": "poi",
  "administrative_context": {
    "province": "云州省",
    "city": "云州市",
    "district": "海棠区",
    "county_level_city": "",
    "township": ""
  },
  "poi_query": "星河广场",
  "destination_subject": "customer",
  "named_store": "",
  "detail_kind": "address",
  "candidate_interpretations": [],
  "evidence_refs": ["current_message"],
  "superseded_location_refs": [],
  "confidence": "high",
  "needs_clarification": false,
  "geocode_before_clarification": true,
  "reason": "客户当前消息提供完整城市、区县和地标，并明确索要地址"
}
```

随后门店工具虚构返回唯一可交付门店 `demo-store-001`；它是工具事实，不是地点解析模型推荐的。

### 10.5 确定性候选与事实汇合

```json
{
  "tool_facts": {
    "store_resolution": {
      "status": "send_single",
      "destination": "云州市海棠区星河广场",
      "delivery_store_ids": ["demo-store-001"],
      "stores": [
        {
          "store_id": "demo-store-001",
          "name": "云州海棠示例店",
          "address": "云州市海棠区示例路 1 号"
        }
      ]
    }
  },
  "sequence_candidates": [
    {
      "sequence_id": "demo-sequence-001",
      "sequence_name": "效果信任低压承接",
      "steps": [{"step_id": "demo-step-001", "action_code": "demo-case-evidence"}]
    }
  ],
  "script_candidates": [
    {
      "script_id": "demo-script-001",
      "text": "先用真实改善参考建立信心，再说明个体情况需结合实际状态判断。",
      "checkpoint_type_id": 9001,
      "delivery_status": "reference_only"
    }
  ],
  "content_candidates": [
    {
      "content_id": "demo-case-image-001",
      "asset_role": "effect_evidence",
      "messages": [
        {"type": "image", "content": {"url": "https://example.invalid/demo-case.jpg"}}
      ]
    }
  ],
  "structured_delivery_options": {
    "store_address": {
      "fact_ref": "tool_fact:customer_store_lookup",
      "status": "send_single",
      "available_store_ids": ["demo-store-001"],
      "message_payloads": [
        {"type": "store_address", "content": {"store_id": "demo-store-001"}}
      ],
      "candidate_search_complete": true,
      "ranking_method": "administrative_scope",
      "source": "current_turn_tool_fact"
    }
  }
}
```

### 10.6 按真实区块顺序整理的完整等价 user Context

下面按 8.3 的候选顺序覆盖本场景实际出现的区块；付款、已付和平台结构事件与本场景无关，因此不出现。复杂对象改成等价可读文本，本节不是字节级 trace 副本。

```text
【当前时间】
2026-09-10T10:02:05+08:00；Asia/Shanghai

【完整聊天】
m01｜客户：我主要是脸颊两边有点状斑，一次能看出变化吗？
m02｜小贝：这次主要针对面部常见斑点和色沉做改善，具体结合现场皮肤状态看。
now｜客户：效果我还是有点担心。我在云州市海棠区星河广场，地址发我看看，照片也发我看看。

【当前结构事实与不能越过的边界】
未下单、未预约、未付；历史未发效果素材和门店卡；图片不可用于诊断。

【本轮真实执行能力】
可回复文本、发送白名单效果图片和本轮工具返回的门店卡；不可确认预约、空位、已付或营业时间。

【当前工具权威事实：不得虚构或违背】
store_resolution=send_single；delivery_store_ids=[demo-store-001]；公开地址=云州市海棠区示例路1号。

【必须遵守】
不能保证固定效果；地址只取本轮门店工具；未预约未付款；活动价格尚未交付。

【本轮相关权威事实：最终口径】
活动面向常见面部斑点和色沉改善；可说明真实改善参考，同时保留个体差异边界。

【可直接交付的真实素材】
content_id=demo-case-image-001；asset_role=effect_evidence；本客户历史未发送。

【可原样交付的结构消息】
store_address={store_id:demo-store-001}。

【本轮缺失权限（逐条禁止自行补全）】
没有权威营业时间、实时空位、预约完成、支付或交易写动作。

【销售主线机会与本轮动作边界（不是每轮流程任务）】
效果证据、活动/价格、门店均未交付；next_missing_stage=effect_evidence，仅表示相邻机会和越级上限。
allowed_next_sales_action_types=[deliver_value,send_effect_material,send_store]。
客户本轮已明确同时索要案例和地址，合法交付这些请求优先于机械补流程。

【已发布 AI 销售策略（只提供可选 key 与节奏，不覆盖事实边界）】
policy_version=2026-09-09.1；包含受控 intent、emotion、customer_state 和 closing_action 目录。

【上一轮策略状态（仅参考，必须按当前客户新消息重新判断）】
上一轮只交付项目范围文字；没有稳定交易或预约状态。

【本轮租户逼单规则与策略候选（只可从中选择，不要求采用）】
当前存在未解效果卡点，无可执行 B 单候选。

【Router 辅助检索判断：只作内部证据，不得复述给客户】
识别到效果担忧、案例请求和地址请求；检索焦点为效果案例；需要门店查询。

【跟进序列与话术素材（取其意思，不模仿句式）】
sequence=demo-sequence-001；step=demo-step-001；script=demo-script-001；可采用“先给真实案例，再管理个体差异”的思路。

【输出引用与结构边界】
allowed_content_ids=[demo-case-image-001]；allowed_store_ids=[demo-store-001]；allowed_sequence_ids=[demo-sequence-001]；allowed_step_ids=[demo-step-001]；allowed_script_ids=[demo-script-001]。

【本轮客户可见输出上限（只有上限，没有最低长度）】
max_messages=8；max_text_chars=300；普通轮最多1个轻表情。

请只返回符合系统输出合同的严格 json。
```

### 10.7 示例 Reply 输出与代码结果

```json
{
  "reply_messages": [
    {
      "type": "text",
      "content": "您关注的这种面颊点状斑，很多客户做完一次就能看到颜色和肤色状态的变化。我先发一张相近案例给您参考🙂"
    },
    {
      "type": "image",
      "content": "https://example.invalid/demo-case.jpg"
    },
    {
      "type": "text",
      "content": "您这个位置已经能匹配门店了，地址也一起发您。"
    },
    {
      "type": "store_address",
      "content": {"store_id": "demo-store-001"}
    }
  ],
  "selected_content_ids": ["demo-case-image-001"],
  "sales_judgment": {
    "customer_friction_observation": "客户当前担心一次改善效果",
    "primary_objective": "直接交付客户明确索要的真实案例和地址",
    "posture": "pause",
    "next_sales_action": {
      "type": "send_effect_material",
      "target_stage": "effect_evidence",
      "reason": "客户明确同时索要案例与地址，本轮事实和结构齐全；直接交付，不把 next_missing_stage 当流程任务"
    }
  },
  "knowledge_use": {
    "sequence_id": "demo-sequence-001",
    "step_id": "demo-step-001",
    "script_id": "demo-script-001",
    "reason": "采用效果信任承接和真实案例证明思路"
  },
  "policy_decision": {
    "primary_task": {"type": "resolve_blocker", "goal": "先处理效果顾虑"},
    "realtime_intent": {"type": "blocker_expression", "confidence": "high"},
    "emotion_decision": {"label": "defensive", "confidence": "medium", "pressure": "low"},
    "closing_decision": {
      "action": "pause",
      "rule_ids": [],
      "sequence_key": "none",
      "node_key": "",
      "trigger": "none",
      "customer_state": "continue_sales",
      "pressure": "low",
      "satisfied_prerequisite_ids": [],
      "blocking_taboo_ids": [],
      "evidence_refs": ["now"]
    },
    "cardpoint_decision": {"category_key": "demo-effect-trust", "state": "active"}
  }
}
```

代码随后核对素材、门店、序列、步骤、话术和证据 ID，确认未越过活动/预约阶段，再把同一批结构消息按序返回。这个输出展示“字段与来源闭环”，不代表模型必须逐字照抄示例文案。

## 11. 源码中存在但当前不在线的 Prompt

| 遗留能力 | 当前状态 | 当前替代机制 |
| --- | --- | --- |
| 旧综合 Router `V3_SEMANTIC_ROUTER_SYSTEM_PROMPT` | 无当前调用点 | `router_prompt_v4.py` |
| 独立序列模型选择器 | 无当前调用点 | 确定性 sequence Top-K，最终采用归 Reply |
| 话术模型预筛/精筛 | 无当前调用点 | 同卡点池确定性排序与裁剪，最终采用归 Reply |
| post-store Router | 无当前调用点 | `complete_after_store()` 确定性补召回，耗时字段应为 0 |
| 旧 Tool Planner | 无当前调用点 | Router 的 `store_query` 与事实主题 |
| Content Gate / SOP Chat Gate 模型判断 | 不在同步 V3 Reply 调用图 | 真实素材目录、去重与 evidence join |
| `REPLY_RECOVERY_SYSTEM_PROMPT` / `_reply_recovery_messages` | 无当前主图调用点 | Reply full retry 或 targeted repair；异步恢复重跑同一 full graph |
| `_RETIRED_*` 与 legacy repair | 明确退役 | 不得写入现行架构 |

沉默唤醒计划/消息模型、图片素材生成、第三方 SOP 历史模型等属于其他运行链，不是“客户发一条消息后的同步 V3 Reply”。第三方 SOP 当前为完全确定性执行，不调用模型。

## 12. 风险与 Prompt 治理

1. **文档漂移**：Prompt 常量变化后本文逐字快照会过期。修改上述四个 Prompt 或动态构造函数的任务必须同时更新本页字符数、SHA 和例子，并运行链接/指纹检查。
2. **模型调用层级易混淆**：逻辑节点、应用层第二次调用和 ModelClient 传输重试是三件事。日志必须分别记录，否则会把一次 Router 的网络重试误算成多个业务决策者。
3. **门店解析供应商边界**：最终 Reply 是 DeepSeek-only，但 store tier 默认仍含 GPT fallback。若产品要求全链 DeepSeek，需单独清空并用门店消歧矩阵验证，不能只改文档。
4. **Vision 轻度语义越界**：`image_intent` 会做可能语境推断；它只能作为证据，最终意图仍由 Reply 决定。
5. **Router 锚定风险**：Router 同时输出检索意图和卡点，可能影响 Reply。Reply Prompt 已明确可覆盖，评测仍应检查 Router 错判是否被最终节点纠正。
6. **完整重试延迟**：无 JSON 时会重跑完整 Reply；这是必要恢复但会形成 P95/P99 长尾。不能通过跳过事实或缩短到批量兜底来换平均速度。
7. **定向修复膨胀**：repair contract 按错误动态生成，错误过多会增加注意力负担。应优先减少上游无效候选和互相冲突的输出合同，而不是不断追加修复例外。
8. **真人感不是 schema**：短承接可以只有几个字，事实回答按需要展开，普通轮最多一个轻表情；这些不是最低字数、固定消息数或强制表情。自然度、表情频率和推进强度必须由销售主管看真实样本验收。
9. **卡点字段位置仍有歧义**：当前运行代码只从 `policy_decision.cardpoint_decision` 读取，但静态 Prompt 的最小 JSON 模板没有显式展示该嵌套字段，文字也只说“输出 cardpoint_decision”。本文示例按运行 schema 放在 `policy_decision` 内；后续应单独统一 Prompt 模板和 parser，并用旧输出兼容测试验证，不能只改文档。

## 13. 权威源码索引

- 主图：`ai_paths/app/graph/graph_builder.py`
- Vision：`ai_paths/app/graph/nodes/image_info.py`、`ai_paths/app/prompts/global_contract.py`
- Router：`ai_paths/app/prompts/router_prompt_v4.py`、`ai_paths/app/prompts/v3_semantic_router.py`、`ai_paths/app/services/v3_semantic_router_service.py`
- 门店地点：`ai_paths/app/prompts/store_destination_resolver.py`、`ai_paths/app/services/store_destination_resolver.py`
- Reply：`ai_paths/app/prompts/reply_sales_prompt_v4.py`、`ai_paths/app/prompts/reply_synthesizer.py`、`ai_paths/app/graph/nodes/material_selection.py`
- 重试/修复：`ai_paths/app/graph/nodes/reply_nodes.py`
- 模型 tier：`ai_paths/app/services/model_client.py`、`ai_paths/app/services/deepseek_semantic_client.py`
