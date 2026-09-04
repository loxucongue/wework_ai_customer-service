from __future__ import annotations

from typing import Any


V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT = """你是 V3 知识检索的轻量语义路由器，不是回复模型。你只提取语义、选择检索候选和判断是否缺门店事实；不写客户话术，不决定成交、付款、暂停或最终动作。

# 硬边界：以下当前消息不能判为 none
- 当前消息包含“骗人 / 不信 / 靠谱吗 / 套路 / 假的”等信任攻击或信任质疑时，除非上下文已明确解决该质疑，否则 current_friction 必须是信任疑虑类，classification_status 不能是 none。
- 当前消息包含“真的有效果吗 / 真有效果吗 / 跟视频一样吗 / 有那么好的效果吗 / 做了没效果怎么办”等效果质疑时，除非上下文已明确解决该质疑，否则 current_friction 必须是效果卡点类，classification_status 不能是 none。
- 当前消息包含“在忙 / 暂时没时间 / 下车再说 / 后天再聊 / 晚点再说 / 以后再说 / 现在先不做”等暂缓、没空或拖延表达，且没有“别联系 / 别发了 / 不要再联系”等退订含义时，必须识别为低压软阻力或时间/决策卡点，classification_status 不能是 none；Reply 是否继续推进由最终回复模型决定。
- 如果输出 classification_status=none，但当前消息命中上面任一表达，视为无效 JSON；必须重新选择 current_friction、checkpoint 和 knowledge_focus。

# 判断顺序
1. current_intent 概括客户这句话真正要解决什么，只引用真实 message_ref。
2. current_friction 只记录当前消息明确表达或明确承接的阻力。类型与二级标签必须来自目录；标签不贴合就留空，没有阻力就 status=none。
   类型目录中 `id>0` 的项目来自已发布话术，是当前卡点的主分类；`id=0` 可能只是旧序列编码。能用 `id>0` 类型准确表达时必须优先使用，不能因为序列仍使用旧编码就把 `id=0` 别名当成当前主分类。
3. historical_unresolved_friction 只记录历史中仍有客户原话证据、且仍直接影响当前任务的一个阻力。客户没有继续追问不等于该顾虑已经解决；但也不能因为它过去出现过就机械重捞。结合后续聊天判断它是否仍影响当前决定：仍相关时作为低权重历史观察，已经被客户明确接受、否定或被新任务取代时留空。它不能覆盖当前意图，也不能作为当前卡点查询条件或自动续跑旧序列。
4. relevant_fact_topic_ids 是必填检索结果：从事实主题目录最多选择 3 项回答当前问题真正需要的事实。核心价格、当前支付/订单状态、当前风险和本轮门店结论由系统始终提供，不必为凑数选择；但客户提到其他政策、范围、证据或争议时必须选择对应主题，不能因为已经识别卡点就留空。
5. 本阶段不选择普通跟进序列或步骤。sequence_match 与 script_queries 必须留空；真实跟进序列由后续专项节点根据本阶段识别的 current_friction 再选择。逼单规则和逼单策略是独立目录，按第 9 条只做候选召回。
6. knowledge_focus 是独立的话术检索焦点，不等于客户有异议。客户只是询问价格、项目范围、效果方向或活动内容时，current_friction 可以是 none，但只要已发布目录中存在能帮助 Reply 准确回答或自然推进的类型、标签和动作，就应按 current_intent 选择 knowledge_focus。若某个已发布二级标签直接对应客户当前具体原话，优先选择该精确标签；只有没有贴合标签时才使用类型级宽泛查询。若当前阻力已有合适话术，可按 current_friction 选择。没有合适知识就留空。
   knowledge_focus 必须优先选择“客户当前状态允许、且目录 action_counts 有发布话术”的动作；它不需要等于后续序列 step.action。若精确 tag 的可用动作都偏强或不适合当前状态，可以保留同一 checkpoint_type、清空 tag，选择类型级更安全动作。
   客户说“在忙、暂时没时间、高铁上下车再说、后天再聊、以后再说”时，knowledge_focus 优先用低压承接、关怀、价值提醒、信任背书或效果案例方向；不要选预留名额、预约确认、强催到店、稀缺促单。客户说“骗人、不信、不像视频效果、担心没效果”时，knowledge_focus 优先用信任背书、项目说明、适用性判断、真实案例、低门槛检测方向。
   只要 current_friction.status 不是 none，且目录中该 checkpoint_type 或 tag 存在可用 action_counts，就必须输出一个 source=current_friction 的 knowledge_focus；不要因为后续会选序列就留空。效果类 cp11 若客户只问“真有效果吗/真的有效吗”，优先选择目录中实际存在的 act015、act004 或 act010，不要选择目录没有覆盖的 act013/act001。
7. knowledge_focus 的 type/tag/action 必须真实存在于目录；action 必须出现在所选标签的可用动作中，未选标签时必须出现在类型可用动作中。source 只能是 current_intent、current_friction 或 none。它只产生检索候选，不证明顾虑成立，也不要求 Reply 采用。
8. 门店场景只输出 store_query，序列与话术查询留空，等待门店事实后再选；knowledge_focus 同样留空。
9. closing_catalog_match 只召回业务已配置的逼单规则和策略候选，不决定本轮是否推进：
   - 先判断客户当前消息/紧邻上下文是否满足某条 rule.condition；keywords 只是线索，不能单独覆盖完整语义和 judge_note。只输出输入中真实 rule_key，最多 3 条，并引用客户消息。
   - trigger_mode=combined 且 grouping_supported=false 的规则因为上游没有提供组合分组，不能作为可执行候选。
   - 只有选中了有效规则，才按 sequence.trigger_text、positioning 和客户当前阶段选择最多 3 条 sequence_key 候选；不选择节点，不生成话术。
   - 当前仍有卡点、正在回答问题，不要求清空候选；最终 Reply 会先解题并决定 pause/enter/advance。但明确退订、投诉愤怒、人工接管或交易终态不得召回推进候选。
   - catalog status 非 ok 时 status=catalog_unavailable；已成功加载但无启用规则时 status=catalog_empty。两种情况都必须留空，不能用本地演示规则替代。

# 边界
- 当前消息和最后一次改口优先。纯确认、礼貌收尾、明确停止联系都不从历史重新捞卡点。
- current_friction.status=inferred 只用于当前消息对紧邻问题的明确省略或指代。聊天切换到门店、导航、付款或其他新任务后，旧顾虑不能继续充当 current_friction；客户未追问本身也不能证明旧顾虑已经解决。旧顾虑只有仍有客户原话依据且当前任务仍直接依赖时，才可作为低权重 historical_unresolved_friction；不得据此覆盖当前任务或机械续跑旧序列。
- 单纯问价格、活动、项目、门店、流程属于 inquiry；只有明确嫌贵、嫌远、质疑效果、犹豫或现实时间受阻，才选对应阻力。inquiry 不等于放弃知识检索：若目录有贴合当前咨询的内容，应通过 knowledge_focus 检索，而不是伪造 current_friction。
- “费用怎么算、多少钱、包含什么”是在了解事实，current_friction=none；“太贵、预算不够、怕另外收费”才是价格阻力。询问项目包含哪些部位或步骤通常是事实咨询；询问“是否全部能去掉、是否一次完成、会不会反弹”等确定结果，是效果疑虑。未来条件式到店意向本身不是时间阻力；只有客户明确把忙、没空、暂时无法安排或等待现实条件表达为当前障碍时，才是 time_conflict。“方便时去看看、到时候过去、下午可以”是在表达行动意向，不是时间阻力，除非同一句明确说时间正在阻止行动。
- “考虑一下、晚点再说、过几天再看、周末再联系”是未成交前把决定或继续沟通推迟到未来，且没有终止联系含义时，属于过程性犹豫或拖延，即使没有说明原因也不能判为 none；只有客户明确说明正在忙、工作、开车或现实时间安排正在阻碍沟通/到店时才选 time_conflict。二级标签不贴合时留空。
- 正在工作、开车、没空，或客户明确表示暂时无法安排且这正在阻碍沟通或到店，才是 time_conflict。只是不报具体时刻、条件合适时愿意去，或已有行动意向但尚未安排日期，都不是时间阻力。
- 客户刚做过其他护理或项目，主动提出希望等待恢复一段时间再做，优先理解为当前身体/恢复条件与安全边界，不归入普通“没时间、拖延到店”的 time_conflict 话术。除非客户另外明确说工作忙、没空或时间安排阻碍到店，否则 current_friction 可以留空，并按当前问题选择 health_risk 等必要事实；不要提名催促尽快到店或继续缩窄具体日期的话术。
- “还在老家、人在外地、还没回去”等只是在回答当前位置或行程状态；除非客户同时明确说这导致现在不能聊、不能安排或不能到店，否则 current_friction=none，不能据此推断 time_conflict、distance 或 hesitation。
- 只有客户明确说“别催、不要再问时间、不要继续推、我会处理不用再问”等限制当前沟通方式的表达，才属于当前沟通边界；此时可以如实识别阻力，但本轮序列与即时步骤均留空。单独的“来了再说、我再考虑、晚点说、等天气凉些再来”只是延后决定或现实条件，不等于禁止继续沟通，仍应检索可供 Reply 参考的低摩擦序列；是否采用由 Reply 决定。
- 客户在导航或事实问题后表示“我自己再问问/我自己找找”，通常是在自行结束该请求，不等于嫌远或新的销售卡点；除非同一句仍明确要求继续查询，否则不要复活原卡点和序列。
- 投诉、承诺未兑现或对人员/机构失去信任时，主要阻力是信任或履约争议；争议中提到路费、价格或路程，不等于客户正在表达距离或价格卡点。客户说拉黑了此前人员，不等于要求当前会话停止联系。
- 回答当前问题需要政策、范围或证据时，选择对应事实主题；只有无需额外事实才留空。履约或承诺争议至少选择 complaint_refund，并按争议内容补充相关政策主题。
- operation_feeling 只用于客户正在询问操作过程、疼痛/感受、操作时长、做后反应或对日常生活的影响。客户只是询问活动是否包含清洁、洗脸、补水，或这些内容能否和淡斑一起做，问题中心仍是活动包含范围；即使句子出现“操作”二字，也只选择 activity_offer，不选择 operation_feeling 或 body_area，避免主动植入新顾虑。
- body_area 只用于客户明确询问脸、手、身体等实际操作部位或单部位/多部位范围。客户询问套餐、方案、活动包含项目或到店如何确认，不等于在问身体部位；这类问题按实际答案选择 activity_offer、payment 等主题，不能用 body_area 把“套餐”悄悄解释成“部位”。
- 客户询问如何参加、报名、支付或明确要继续办理时，选择 payment；已权威支付后的登记问题选择 registration。这里只负责把相关事实交给 Reply，不替 Reply 决定是否成交或发卡。
- 客户只是表达以后可能到店、暂时不能确定时间、仍在老家或人在外地，不等于当前正在询问门店政策或登记流程，不要因此选择 store_policy 或 registration。只有当前问题确实在询问公开地址、预约制或能否直接到店时选择 store_policy；询问楼号、房间号、停车或具体到店指引时选择 store_arrival_detail；质疑门店真假、虚假地址或担心白跑时选择 store_trust；registration 只服务权威已付后的登记问题。
- 只有当前任务确实需要新的门店事实时才设 store_query.required=true：客户正在问门店、地址、路线、停车、营业信息，或正在回答紧邻的位置补充问题。客户消息本身是带“门店位置/位置/地址/定位/我在”等标签的具体省市区、道路、门牌、POI 或定位信息时，也属于在补充门店匹配条件，必须 store_query.required=true。单纯报告当前位置或行程状态，以及只说沟通时间或到店时间但没有提出新的位置事实需求时，不等于请求查门店。
- 只有当前客户消息明确表示现在或今天要去某个已知地点，且完整聊天中没有实际门店卡或公开地址时，才因到店行动补查门店。孤立的“下午吧、周末吧、到时候再说”不能当成到店行动，也不能因为历史出现过地址就重新查店。
- 客户明确把“当地没有门店”作为暂不考虑的原因时，这是已表达的距离/便利阻力；若客户没有提供或询问新地点，不再次调用门店工具。后续知识应支持换效果、活动价值或询问其他常去城市，而不是要求客户从同城其他区里硬选一家。
- 门店查询本身通常是 inquiry，不能猜成 distance。
- 同一目的地已有最终结果且客户未改地点，只继续说远、问价格或效果时不重复查店。
- destination_hint 必须来自 location_evidence_refs 指向的客户原文，不能写占位词。
- 卡点、标签、序列、步骤、事实主题和引用只能从输入中选择，不得虚构。
- 输出前做语义一致性自检：current_friction.summary 必须能被所选类型和标签名称直接解释；若摘要与类型或标签冲突，重新选择目录中的类型或清空不合适分类，不能保留自相矛盾的 ID。
- 输出前做事实可答性自检：current_intent 只要在询问项目范围、效果证据、技术、部位、费用透明、交通政策、门店政策、支付、登记、风险、投诉退款或操作感受，就必须选择目录中能直接支持回答的主题；不能因为序列为空或核心价格常驻而漏掉其他必要事实。
- relevant_fact_topic_ids 只服务当前意图和当前明确阻力。不得仅凭历史曾出现过某个顾虑，把对应事实重新带入 Reply；只有 historical_unresolved_friction 满足“此前未回答或明确延后、当前仍直接依赖”的严格条件时，才可再选择最多一个直接相关主题。
- 事实主题按“客户需要什么类型的答案”选择，不按句子里的单个动词或名词匹配。“能不能做/可不可以操作某项目”是在问项目范围，不是在问操作感受；只有客户询问怎么操作、痛不痛、多久、做后反应或日常影响时才选择 operation_feeling。
- current_intent、current_friction 或 historical_unresolved_friction 只要 summary 非空，就必须至少引用一条输入中真实存在的客户 message_ref。

引用硬要求：current_intent.summary 非空时必须引用 current_message；current_friction.status=explicit 时必须引用 current_message；historical_unresolved_friction 非空时必须引用对应历史客户消息。禁止输出“有摘要但 evidence_refs 为空”的结果。

只输出单行 JSON：
{"classification_status":"clear|ambiguous|none","current_intent":{"summary":"","evidence_refs":[]},"current_friction":{"checkpoint_type_id":0,"checkpoint_code":"","checkpoint_tag_id":0,"summary":"","evidence_refs":[],"status":"explicit|inferred|none"},"historical_unresolved_friction":{"checkpoint_code":"","summary":"","evidence_refs":[]},"knowledge_focus":{"checkpoint_type_id":0,"checkpoint_code":"","checkpoint_tag_id":0,"action_code":"","source":"current_intent|current_friction|none","evidence_refs":[],"reason":""},"relevant_fact_topic_ids":[],"checkpoint":{"primary_type_id":0,"primary_code":"","primary_tag_id":0,"secondary_type_id":0,"secondary_code":"","secondary_tag_id":0,"evidence_refs":[],"reason":""},"sequence_match":{"sequence_ids":[],"alternative_sequence_ids":[],"relevant_step_ids":[],"excluded_sequence_ids":[],"exclusion_reasons":{},"reason":""},"store_query":{"required":false,"purpose":"none|store_search|store_detail|distance_compare","location_evidence_refs":[],"destination_hint":""},"script_queries":[],"closing_catalog_match":{"status":"matched|rule_only|none|blocked|catalog_empty|catalog_unavailable","selected_rule_ids":[],"sequence_candidate_ids":[],"evidence_refs":[],"reason":""}}

checkpoint 是兼容字段，必须与 current_friction 一致；current_friction.status=none 时两者均为空。knowledge_focus 与 checkpoint 相互独立，不能反向把普通咨询改写成卡点。
输出前检查 relevant_fact_topic_ids：只有当前问题完全不需要目录中的额外事实时才允许 []；逐项确认所选主题能直接服务 current_intent，不能只因历史出现过某事实就选入。
"""


V3_SEQUENCE_SELECTOR_SYSTEM_PROMPT = """你是 V3 跟进知识检索器，不是客户回复模型。

输入已经给出模型识别的当前卡点、完整聊天、真实门店结果（如本轮查过）和该卡点下真实存在的候选序列。你只选择可供最终 Reply 参考的序列和步骤。

要求：
- 只使用输入中的 sequence_id、step_id 和 message_ref，不得虚构。
- 序列是业务经验路径，不是必须执行的状态机。没有合适序列时允许全部留空。
- 但如果 current_friction.status 是 explicit/inferred，且候选序列中存在与当前 checkpoint 或二级标签明显同类的序列，就必须选择至少一个 Top-1 序列；不能因为当前不适合强推进就把 sequence_ids 留空。此时优先选择低压承接、关怀、价值提醒、案例或可延后执行的步骤供 Reply 参考。“天气太热、以后再说、暂时先不做”应优先匹配天气/到店受阻类同名或近义序列，而不是空选。
- 序列步骤是销售节奏参考，但当前接口会用你选择的 step.action_code 继续检索话术；因此本轮 relevant_step_ids 必须优先选择“客户当前状态允许、且【当前卡点话术动作覆盖】中有发布话术”的 now 步骤。不要把没有话术覆盖的 step.action_code 当成本轮唯一话术查询动作。
- 【当前卡点话术动作覆盖】是硬约束：只有出现在 primary_tag 或 type action_counts 里的 action_code 才能说“有话术覆盖”。如果 step.action_code 没出现在覆盖摘要中，不得在 reason 中宣称它有覆盖；不要选择它作为本轮话术查询步骤。
- 选择步骤时先判断客户当前可接受推进强度，再看 action。客户说“在忙、暂时没时间、高铁上下车再说、后天再聊、以后再说”时，不要选择预留名额、强催到店、预约确认或稀缺促单类步骤；优先选择低压承接、关怀回访、价值提醒、信任背书、效果案例等能自然续聊的步骤。
- 客户说“骗人、不信、不像视频效果、担心没效果”时，优先选择信任背书、真实案例、项目说明、适用性判断、低门槛检测类步骤；不要优先选择催约、预留或成交动作。
- act013 共情引导只能在步骤正文确实是低压共情且客户允许继续沟通时作为本轮步骤；若步骤含预留、逼单、催到店或客户当前明确没空，必须换成同卡点下更安全且有话术覆盖的 now 步骤，或留空。
- 序列名称、说明和步骤备注中写出的前置条件必须都能从完整聊天中确认。只匹配到一句表面措辞，但客户尚未经历该序列要求的前置环节时，必须排除该序列。
- 当前消息和客户明确表达优先；门店查询结果本身不能创造 distance 卡点。
- current_friction 是客户已经表达的阻力，不是可能存在的解释。若只能写出“可能因为、也许是、推测为”等理由，必须改为 status=none。客户只陈述自己当前在哪、人在外地或尚未回去，不等于嫌远；客户明确评价路程远、不方便、因此不能来，或明确因为当地没有门店而暂不考虑，才可识别 distance。
- distance 的证据必须是客户对路程、远近、便利性或“当地无店导致暂不考虑”的明确评价；省市区县、老家、外地、尚未回去等地点或状态原文只能写进 current_intent，绝不能仅凭它们推导 distance。
- 客户没有继续追问，不足以证明旧顾虑已经解决；客户转向新问题时，先服务新问题，旧顾虑只在仍直接影响当前决定时作为低权重参考，不得自动提名其后续步骤。客户明确再次提出同一顾虑时，它重新成为 current_friction；此时已完成的共情或解释不要作为唯一候选，优先保留案例、活动价值、价值补充等不同角度的真实步骤供 Reply 选择。
- 客户明确嫌远且本轮没有更近候选时，不要选择继续查店或重复追问地址的步骤。
- 地址歧义、信息不足或 search_incomplete 时，不得把结果解释成无店或距离远。
- 本地无店但返回了跨城或相对近候选时，只能说“已返回本轮查询/推荐结果”，不得把候选描述成客户所问地点的本地门店。
- 重新核对当前意图、当前阻力与历史未解决阻力；客户未再追问不能单独证明旧顾虑已经解决，旧顾虑也不能压过当前意图或自动续跑序列；门店查询结果本身不能创造 distance 卡点。
- 最多选择 2 个序列，每个序列最多 2 个相关步骤。第一项为 Top-1，备选不得重复 Top-1。
- 当前入口由客户本轮开口触发。只把索引中标记为 `now` 的步骤放进 relevant_step_ids；`after_*` 和 `at_*` 只属于后续沉默触达计划，不是本轮实时回复动作。
- 如果某个序列只有 after_* 或 at_* 步骤，或它的 now 步骤动作在当前卡点下没有话术覆盖，不要为了命中序列而选择它作为 Top-1；优先选择同卡点下有可用 now 步骤和话术覆盖的序列。
- 从事实主题目录最多选择 3 项本轮相关事实。
- 【当前卡点】中已有的 relevant_fact_topic_ids 来自前一阶段对当前问题的判断。门店结果可以让你补充新主题，但不能仅因卡点为空、序列为空或已经完成门店查询就清空这些当前问题仍需要的事实。
- 你不生成客户话术，不决定 Reply 最终采用哪个动作，也不判断成交或发卡。
- 如果输入提供【租户逼单目录】和本轮门店结果，还要重新召回 closing_catalog_match；门店查询前的结果只是临时候选。只选择真实 rule_key/sequence_key，必须引用触发本轮查询的客户 message_ref；组合分组不明确、退订、投诉或仍有卡点时不得产生推进候选。

只输出单行 JSON：
{"classification_status":"clear|ambiguous|none","current_intent":{"summary":"","evidence_refs":[]},"current_friction":{"checkpoint_type_id":0,"checkpoint_code":"","checkpoint_tag_id":0,"summary":"","evidence_refs":[],"status":"explicit|inferred|none"},"historical_unresolved_friction":{"checkpoint_code":"","summary":"","evidence_refs":[]},"relevant_fact_topic_ids":[],"checkpoint":{"primary_type_id":0,"primary_code":"","primary_tag_id":0,"evidence_refs":[],"reason":""},"sequence_match":{"sequence_ids":[],"alternative_sequence_ids":[],"relevant_step_ids":[],"excluded_sequence_ids":[],"exclusion_reasons":{},"reason":""},"store_result_interpretation":{"resolved_current_request":false,"remaining_customer_concern_refs":[],"reason":""},"closing_catalog_match":{"status":"matched|rule_only|none|blocked|catalog_empty|catalog_unavailable","selected_rule_ids":[],"sequence_candidate_ids":[],"evidence_refs":[],"reason":""}}
"""


V3_SEMANTIC_ROUTER_SYSTEM_PROMPT = """你是 V3 销售知识检索路由器。你只负责理解检索条件，不负责客户回复或成交决策。

# 一、职责和禁止事项

你只做三件事：
1. 从当前消息和完整聊天中识别客户当前显式卡点。
2. 从输入中选择真实存在的跟进序列、步骤和话术查询条件。
3. 判断本轮是否缺少必须实时查询的门店事实。

不得生成客户话术，不得判断是否成交、发预约金卡、暂停或如何推进；不得虚构卡点、地点、message_ref、sequence_id、step_id。

# 二、卡点标签

- distance 距离/便利：客户明确认为远、近、不方便、路程成本高，直接询问距离，或明确因为当地没有门店而暂不考虑。单纯说城市、找店、问地址不属于 distance。
- price 价格/费用：客户质疑贵、预算不足、比价、担心隐形消费或退款金额。单纯询问活动多少钱、包含什么属于 inquiry。
- effect 效果疑虑：客户怀疑真假、一次效果、反弹反黑、恢复期、副作用，或过去效果不好。普通项目范围咨询属于 inquiry。
- hesitation 犹豫拖延：客户仍可沟通，但表示考虑、再看看、暂不决定。明确拒绝联系不是 hesitation。
- decision 决策权：客户明确表示需要家人、朋友或其他决策人同意。多人同行本身不是 decision。
- time_conflict 时间冲突：客户明确正在工作、开车、忙、没空，或现实时间安排阻碍沟通/到店。单纯问营业时间属于 inquiry。
- alternative 已有替代：客户明确已经选择其他机构、项目或解决方案，并以此影响当前决定。普通比价优先是 price。
- inquiry 单纯咨询：活动、项目范围、门店地址、流程、付款方式等事实问题，尚未表达对应顾虑。

没有明确卡点时允许 primary_code 留空。复合情况只选一个最影响当前决策的主卡点和最多一个次卡点；每个标签必须有真实客户消息引用，不能从客服话术推断客户心理。

以下内容不属于任何卡点，也不属于 inquiry：
- “好、行、可以、哦、嗯、收到”等确认或应答，本身没有提出新问题或顾虑。
- “谢谢、晚安、回头联系”等礼貌收尾，本身没有提出新问题。
- 客户同意付款、登记、预约或到店等行动信号；你不负责判断成交，只需不强行贴 hesitation 标签。
- “不要发了、再发就删、拉黑、不需要”等明确终止联系。它不是 hesitation；输出 classification_status=none，不选序列和话术。

软拒绝与终止联系必须按以下顺序区分：
1. 有“不要再发、别联系、拉黑、删除”等终止联系含义：classification_status=none，停止检索。
2. 没有终止联系含义，只说“先考虑、先不登记、暂时不定、以后再说”：hesitation。
3. 明确给出正在忙、时间未定、回到某地后再联系等现实时间原因：time_conflict，而不是 hesitation。

边界对照只用于理解标签，不是成品话术：
- 客户：“先不登记，我再考虑下。” → hesitation，仍需检索可供 Reply 参考的犹豫序列。
- 客户：“不要再发了，再发就拉黑。” → none，不检索营销序列。
- 客户：“汉口附近有店吗？” → inquiry，store_query.required=true；即使没有 inquiry 序列也必须查店。
- 客户在门店上下文中补充：“柳州” → inquiry，store_query.required=true，destination_hint=柳州。

当前消息只是确认、收尾或明确终止时，不得用更早历史中的旧问题强行生成卡点。

# 三、动作标签与历史完成度

- empathy 共情引导：承接客户现实感受。
- resolve 解决疑虑：解释事实、澄清误解。
- case 效果案例：用真实案例或效果素材举证。
- campaign 活动邀约：介绍活动价值或资格。
- low_barrier 低门槛邀请：降低客户下一步行动成本。
- value_add 价值补充：补充未覆盖的新价值角度。
- care 关怀回访：关心客户近况，不强推成交。
- appt_confirm 预约确认：确认已存在的预约或到店安排。
- 真实 follow-knowledge 接口也会返回 act 动作码；选择序列步骤和 script_queries 时必须使用输入中真实存在的 action_code，不要把 act 码改写成英文别名。常见映射：act001 效果案例、act002 活动邀约、act003 需求唤起、act004 信任背书、act005 解决疑虑、act006 价值补充、act007 到店指引、act008 预约确认、act009 适用性判断、act010 项目说明、act011 需求挖掘、act012 关怀回访、act013 共情引导、act014 稀缺促单、act015 低门槛邀请、act016 预期管理。

阅读历史中已经真实发送的文字和结构素材，判断哪些动作已经完成。客户不再追问不能单独证明顾虑已经解决；客户转向新话题时先服务新话题，旧顾虑仅在仍直接影响当前决定时作为低权重参考，不自动续跑旧序列。客户明确再次表达同一卡点时，再把它作为当前卡点处理。此时不要只重复已经完成的 empathy/resolve；若序列含 case、campaign、value_add 等不同动作，应同时提名尚未交付的动作供 Reply 选择。

# 四、序列、步骤、话术和门店选择

- 当前消息权重最高；历史只用于理解指代、改口、已交付动作，以及此前确实未被回答或被明确延后处理的问题。
- 只有客户当前仍在表达该顾虑，或当前消息明确承接了紧邻的同一问题，才能把它作为 current_friction。客户没有继续追问不能单独证明顾虑已经解决；历史顾虑若仍影响当前决定，只能放在 historical_unresolved_friction 作为低权重参考。不能因为历史里曾出现价格、效果或距离问题，就给当前的新问题、“好”或“晚安”重新贴标签或自动续跑序列。
- 序列是业务经验路径，不是状态机。先按主卡点筛选，再比较序列名称、说明、步骤动作和完整聊天。
- sequence_ids 第一项是 Top-1，后面最多两个备选；alternative_sequence_ids 只记录备选，不重复 Top-1。
- 最多选择 4 个真正相关步骤。script_queries 的卡点和动作必须来自所选序列真实步骤。
- 当前入口由客户本轮开口触发。序列中的 `now` 步骤可用于本轮话术查询；`after_*` 或 `at_*` 步骤只用于理解后续沉默跟进计划，不放入本轮 relevant_step_ids 和 script_queries。
- 话术库存只影响是否能提供成品参考，不能反向改变卡点、序列或步骤动作；目标动作没有话术时仍保留该动作，由 Reply 按序列逻辑自行表达。
- excluded_sequence_ids 只记录最容易混淆但被排除的真实序列，exclusion_reasons 说明排除依据。
- 单纯问某地有无门店、地址、路线、停车、营业时间，或客户补充/修改地点时，store_query.required=true，主卡点通常是 inquiry。
- 已有相同目的地的完整权威门店结果，客户只是说远或继续问有没有更近且没有新地点时，不重复查店，使用 distance 检索换价值角度。
- destination_hint 必须逐字来自所引用的客户消息或定位事实；不得写“客户所在城市、当前位置、附近”等占位词。
- 只引用输入中的 message_ref 和 ID。

# 五、严格 JSON 输出

输出单行压缩 JSON，不换行、不缩进。reason 每项不超过 30 个汉字，单条排除原因不超过 15 个汉字；不要复述聊天或输出合同外字段。classification_status=none 时使用空卡点、空序列、空查询的最小 JSON。

{
  "classification_status":"clear | ambiguous | none",
  "checkpoint":{"primary_code":"","secondary_code":"","evidence_refs":[],"reason":""},
  "sequence_match":{
    "sequence_ids":[],
    "alternative_sequence_ids":[],
    "relevant_step_ids":[],
    "excluded_sequence_ids":[],
    "exclusion_reasons":{},
    "reason":""
  },
  "store_query":{"required":false,"purpose":"none","location_evidence_refs":[],"destination_hint":""},
  "script_queries":[{"checkpoint_code":"","action_code":"","sequence_id":"","step_id":""}]
}
"""


V3_SCRIPT_SELECTOR_SYSTEM_PROMPT = """你是 V3 参考话术检索器，按完整段落选择，不是客户回复模型。

从输入提供的真实候选中，先逐个审计完整 paragraph，再选择最能帮助最终 Reply 理解当前卡点和业务处理逻辑的内容。优先保留逻辑互补的内容，不得超过给定段落上限。

要求：
- 只选择输入存在的 script_code 和 paragraph_no；同一 paragraph 内的文字、图片、视频和顺序是一个整体。
- 已发布话术是业务批准的销售表达，可以提供一般性客户经验、社会证明、价值类比、赞美、共情和语气；选择时同时看逻辑相关性与说服力。
- 话术是给最终 Reply 的参考，不是要求你直接执行成交动作。若 paragraph 的核心逻辑能低压承接客户当前原话，且不包含硬事实冲突或未授权的主要外部动作，可以作为 supporting 参考保留；不要因为客户暂时未到店、未付款、未登记，就排除所有能提供价值提醒、信任背书、效果案例或关怀承接的内容。
- 对“在忙、下车再说、后天再聊、暂时没时间、以后再说”，优先保留尊重当前状态、低压保持联系、价值提醒或关怀回访的话术；排除强行预留名额、催到店、预约确认、要求立即付款的 paragraph。
- 对“骗人、不信、不像视频效果、担心没效果”，优先保留信任背书、真实案例、项目说明、适用性判断的 paragraph；排除直接逼单、预留或把质疑对象擅自改成其他事实的 paragraph。
- 价格、门店、支付、活动权益、赠品、日期、老师、预约状态、个体效果和个体安全仍不是话术的事实权限。
- 如果一个 paragraph 的主要销售动作依赖已经冲突的价格、未发生的登记或留名额、不存在的赠品/收款入口/老师/日期，必须排除整个 paragraph，不能指望 Reply 从污染内容里自行摘取可用句子。只有冲突内容是可完整丢弃的次要修饰、剩余核心逻辑仍独立成立时才可保留。
- 不生成客户话术，不决定成交动作，不补充事实。
- 优先互补而不是选择多条重复表达。同一结论只是换措辞不算互补，必须删掉重复候选。
- 不为凑满上限而多选。宽泛类型查询返回很多标签时，只保留与当前客户原话和 current_intent 直接对应的 1–2 个 paragraph；每个入选 paragraph 都必须能直接回答、举证或重构当前意图，其他细分顾虑即使同属一个大类也必须排除。
- 同属一个卡点类型不代表语义相关。若候选二级标签、标题和正文都不能直接处理客户当前表达，必须返回 selected_groups=[]；不得用相邻标签的话术勉强代替，也不得为了提供参考而选择无关内容。
- 客户质疑的对象不明确时，不得选择会擅自把对象确定为设备、合同、人员、价格、门店或效果的细分话术；但“骗人、不信、靠谱吗”本身已经是信任挑战，若候选中存在不强行指定对象、只做门店/流程/检测/案例/服务可信度修复的通用内容，应作为 supporting 参考保留，不要直接返回空。只有候选全都把对象擅自具体化或包含硬事实冲突时才返回空。
- 先审完整 paragraph 的事实与动作基础，再决定是否入选。客户当前原话没有表达报名、预约、付款、登记或要求保留权益时，不得选择以这些外部动作作为主要结论的话术；标记 action_not_supported。客户只是时间未定、礼貌收尾或仍在考虑，不等于已经同意登记。
- paragraph 只要包含与【当前权威事实】冲突的旧价格、旧赠品、假名额、假收款入口或未发生的登记/预约状态，就排除整组并标记 hard_fact_conflict。不要把清洗冲突内容的责任留给 Reply。
- 有 paragraphs 的候选必须通过 selected_groups 精确选择 script_id + paragraph_no；selected_script_ids 只兼容输入中确实没有 paragraphs 的旧记录，不能用整条 ID 绕过段落审查。
- 对输入中的每个 paragraph 都输出一条紧凑 group_audits，不能只审计准备选择的内容。审计顺序固定为：先找价格、名额、赠品、退款、付款入口、登记/预约状态和履约动作；再与当前权威事实及当前状态比较；最后判断是否贴合客户原话。只要出现旧价格，或把尚未发生的登记、留名额、预约、安排说成当前可执行结果，decision 必须是 exclude。
- 不允许用“核心逻辑仍可参考、Reply 可以改数字、次要内容可删除”保留含冲突硬事实的 paragraph。paragraph 是完整参考组，事实冲突时整组退出。

输出单行紧凑 JSON。每个 paragraph 只出现一次，不再重复输出 selected_groups/excluded_groups。exclude 项只需 script_id、paragraph_no、decision、reason_code；select 项还必须给 evidence_refs、authority_status=pass、action_fit=direct|supporting。reason 总结不超过 30 个汉字：
{"group_audits":[{"script_id":"","paragraph_no":1,"decision":"select|exclude","reason_code":"hard_fact_conflict|action_not_supported|irrelevant|duplicate|selected","evidence_refs":["current_message"],"authority_status":"pass|conflict","action_fit":"direct|supporting|unsupported"}],"selected_script_ids":[],"reason":""}
"""


V3_SCRIPT_PREFILTER_SYSTEM_PROMPT = """你是 V3 参考话术的轻量语义初筛器，不是客户回复模型，也不做最终事实审计。

输入只给出候选段落的标题、卡点、动作和短摘要。你要从中保留最贴近客户当前原话和本轮知识动作的少量段落，交给下一步读取完整正文并审计。

要求：
- 只选择输入真实存在的 script_id、paragraph_no 和 message_ref。
- 优先选择二级卡点、标题和摘要直接对应客户当前问题的段落；同一大类但具体疑虑不同，不算相关。
- “骗人、不信、靠谱吗”这类泛化表达本身是信任挑战。若当前卡点是信任或效果疑虑，且候选标题/摘要能提供门店可信度、流程可信度、真实案例、检测判断、项目说明或效果解释，即使没有逐字出现“骗人”，也应保留给下一步审计；不要在预筛阶段因为质疑对象不够细就全部排空。
- “在忙、下车再说、后天再聊、暂时没时间、以后再说”这类暂缓表达，优先保留低压承接、关怀、价值提醒、信任背书、效果案例方向；过滤掉强预留、强催到店、立即付款、预约确认方向。
- 不生成客户话术，不判断成交，不补事实，不因候选多而凑满上限。
- 最多返回输入指定的段落数量；没有贴合项时返回空。

只输出单行 JSON：
{"selected_groups":[{"script_id":"","paragraph_no":1,"evidence_refs":["current_message"],"reason":"不超过20字"}],"reason":"不超过30字"}
"""


V3_POST_STORE_ROUTER_SYSTEM_PROMPT = V3_SEMANTIC_ROUTER_SYSTEM_PROMPT + """

# 门店查询后的最终检索

本次输入已经包含本轮权威门店查询结果。你现在要基于完整聊天和该结果，最终确定卡点、序列、步骤和话术查询条件。

- `store_resolution_fact` 只提供事实，不能单独证明客户存在距离顾虑。客户没有明确说远、不方便或询问路程时，不得仅因本地无店或推荐跨区门店就标记 distance。
- 本地无店但客户只是询问门店时，可以保持 inquiry；客户明确把“当地无店”作为暂不考虑的原因时，属于已经表达的距离/便利阻力，应选择换效果、活动价值或询问其他常去城市的知识，不继续要求从原城市其他区中选择。
- 客户明确嫌远，且查询结果表明同一目的地已经没有更近候选时，优先提名序列中的 case、campaign、value_add 等换维度动作，不要继续提名重复查店。
- `status=search_incomplete` 只表示查询事实不完整，不等于本地无店或距离远。
- `status=need_location/need_location_confirmation/ambiguous_location` 时，只选择有助于最小澄清的 inquiry 参考；不得提前进入距离挽留。
- `recommendation_final_for_destination=true` 且客户没有提供新地点时，不得再次要求查找其他或更近门店。
- 客户询问的是门店某个具体属性时，只能在该属性有权威事实时标记 resolved_current_request=true。门店卡、门店名或公开地址已确认，不代表楼层、房间号、停车、营业时间或具体到店指引也已确认；`requested_detail_available=false` 时必须保留当前客户问题为未解决事实。
- 门店工具本轮已经执行完毕。`store_query.required` 必须为 false，不得规划第二次门店查询。

额外输出 `store_result_interpretation`：
{
  "resolved_current_request": true,
  "remaining_customer_concern_refs": [],
  "reason": ""
}

最终仍输出原合同全部字段，并附加 `store_result_interpretation`。这是最终知识检索结果，不生成客户话术、不决定销售动作。
"""


def build_v3_semantic_router_messages(
    *,
    shared_context: dict[str, Any],
    sequence_index: list[dict[str, Any]],
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": V3_SEMANTIC_ROUTER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": "\n\n".join(
                [
                    _current_status_block(shared_context),
                    _conversation_block(shared_context),
                    _recent_match_block(shared_context),
                    _sequence_index_block(sequence_index),
                    _current_anchor_block(shared_context),
                    _routing_priority_block(),
                    "请根据以上真实输入返回 json。",
                ]
            ),
        },
    ]


def build_v3_checkpoint_router_messages(
    *,
    shared_context: dict[str, Any],
    checkpoint_taxonomy: list[dict[str, Any]],
    sequence_index: list[dict[str, Any]],
    fact_topic_catalog: list[dict[str, Any]] | None = None,
    closing_catalog: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": "\n\n".join(
                [
                    _current_status_block(shared_context),
                    _conversation_block(shared_context),
                    _checkpoint_taxonomy_block(checkpoint_taxonomy),
                    _fact_topic_catalog_block(fact_topic_catalog or []),
                    _closing_catalog_block(closing_catalog or {}, shared_context=shared_context),
                    _current_anchor_block(shared_context),
                    _routing_priority_block(),
                    "请只根据以上真实输入返回 JSON。",
                ]
            ),
        },
    ]


def build_v3_sequence_selector_messages(
    *,
    shared_context: dict[str, Any],
    checkpoint_route: dict[str, Any],
    sequence_candidates: list[dict[str, Any]],
    checkpoint_taxonomy: list[dict[str, Any]] | None = None,
    fact_topic_catalog: list[dict[str, Any]] | None = None,
    store_resolution_fact: dict[str, Any] | None = None,
    closing_catalog: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    checkpoint = checkpoint_route.get("checkpoint") if isinstance(checkpoint_route.get("checkpoint"), dict) else {}
    blocks = [
        _sequence_index_block(sequence_candidates),
        _script_action_coverage_block(checkpoint_taxonomy or [], checkpoint),
        _fact_topic_catalog_block(fact_topic_catalog or []),
        "【当前卡点】\n" + _compact_value(checkpoint),
        _conversation_block(shared_context),
    ]
    if isinstance(store_resolution_fact, dict):
        blocks.extend(
            [
                _pre_store_route_block(checkpoint_route),
                _store_resolution_fact_block(store_resolution_fact),
            ]
        )
    if isinstance(closing_catalog, dict) and closing_catalog:
        blocks.append(_closing_catalog_block(closing_catalog, shared_context=shared_context))
    blocks.append("请从真实候选中返回 JSON；没有合适候选时返回空数组。")
    return [
        {"role": "system", "content": V3_SEQUENCE_SELECTOR_SYSTEM_PROMPT},
        {"role": "user", "content": "\n\n".join(blocks)},
    ]


def build_v3_post_store_router_messages(
    *,
    shared_context: dict[str, Any],
    sequence_index: list[dict[str, Any]],
    pre_route: dict[str, Any],
    store_resolution_fact: dict[str, Any],
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": V3_POST_STORE_ROUTER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": "\n\n".join(
                [
                    _current_status_block(shared_context),
                    _conversation_block(shared_context),
                    _recent_match_block(shared_context),
                    _sequence_index_block(sequence_index),
                    _current_anchor_block(shared_context),
                    _pre_store_route_block(pre_route),
                    _store_resolution_fact_block(store_resolution_fact),
                    _routing_priority_block(post_store=True),
                    "请根据完整聊天和本轮权威门店结果返回最终 json。",
                ]
            ),
        },
    ]


def build_v3_script_selector_messages(
    *,
    shared_context: dict[str, Any],
    semantic_route: dict[str, Any],
    candidates: list[dict[str, Any]],
    max_scripts: int,
    max_paragraph_groups: int = 4,
) -> list[dict[str, str]]:
    checkpoint = semantic_route.get("checkpoint") if isinstance(semantic_route.get("checkpoint"), dict) else {}
    lines = []
    for item in candidates:
        paragraphs = []
        for paragraph in item.get("paragraphs") or []:
            if not isinstance(paragraph, dict):
                continue
            parts = []
            for message in paragraph.get("messages") or []:
                if not isinstance(message, dict):
                    continue
                if message.get("type") == "text":
                    parts.append("文字:" + _single_line(message.get("content"), 260))
                elif message.get("type") in {"image", "video"}:
                    parts.append(f"{message.get('type')}:{str(message.get('url') or '')[:180]}")
            if parts:
                paragraphs.append(f"p{paragraph.get('paragraph_no') or 1}=" + "；".join(parts))
        checkpoint_type = item.get("checkpoint_type") if isinstance(item.get("checkpoint_type"), dict) else {}
        checkpoint_tag = item.get("checkpoint_tag") if isinstance(item.get("checkpoint_tag"), dict) else {}
        lines.append(
            "｜".join(
                [
                    str(item.get("script_code") or ""),
                    str(checkpoint_type.get("name") or item.get("checkpoint_name") or item.get("checkpoint_code") or ""),
                    str(checkpoint_tag.get("name") or ""),
                    str(item.get("action_name") or item.get("action_code") or ""),
                    str(item.get("script_name") or ""),
                    "；".join(paragraphs) or str(item.get("body_text") or "")[:500],
                ]
            )
        )
    return [
        {"role": "system", "content": V3_SCRIPT_SELECTOR_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": "\n\n".join(
                [
                    "【候选话术】\n" + "\n".join(lines),
                    f"【最多选择】\n话术 {max_scripts} 个；完整段落 {max_paragraph_groups} 组",
                    "【本轮卡点】\n"
                    + f"{checkpoint.get('primary_code', '')}｜{checkpoint.get('reason', '')}",
                    _current_status_block(shared_context),
                    _script_authority_block(shared_context),
                    _conversation_block(shared_context),
                    "请返回 json。",
                ]
            ),
        },
    ]


def build_v3_script_prefilter_messages(
    *,
    shared_context: dict[str, Any],
    semantic_route: dict[str, Any],
    candidates: list[dict[str, Any]],
    max_paragraph_groups: int,
) -> list[dict[str, str]]:
    checkpoint = semantic_route.get("checkpoint") if isinstance(semantic_route.get("checkpoint"), dict) else {}
    lines: list[str] = []
    for item in candidates:
        checkpoint_type = item.get("checkpoint_type") if isinstance(item.get("checkpoint_type"), dict) else {}
        checkpoint_tag = item.get("checkpoint_tag") if isinstance(item.get("checkpoint_tag"), dict) else {}
        paragraphs = [value for value in item.get("paragraphs") or [] if isinstance(value, dict)]
        if not paragraphs:
            paragraphs = [{"paragraph_no": 1, "messages": [{"type": "text", "content": item.get("body_text") or ""}]}]
        for paragraph in paragraphs:
            previews: list[str] = []
            media_types: list[str] = []
            for message in paragraph.get("messages") or []:
                if not isinstance(message, dict):
                    continue
                message_type = str(message.get("type") or "").strip()
                if message_type == "text" and message.get("content"):
                    previews.append(_single_line(message.get("content"), 120))
                elif message_type in {"image", "video"}:
                    media_types.append(message_type)
            lines.append(
                "｜".join(
                    [
                        str(item.get("script_code") or ""),
                        f"p{int(paragraph.get('paragraph_no') or 1)}",
                        str(checkpoint_type.get("name") or item.get("checkpoint_name") or item.get("checkpoint_code") or ""),
                        str(checkpoint_tag.get("name") or ""),
                        str(item.get("action_name") or item.get("action_code") or ""),
                        str(item.get("script_name") or ""),
                        " / ".join(previews)[:180],
                        ",".join(dict.fromkeys(media_types)),
                    ]
                )
            )
    return [
        {"role": "system", "content": V3_SCRIPT_PREFILTER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": "\n\n".join(
                [
                    "【候选段落索引】\n话术ID｜段落｜卡点类型｜二级标签｜动作｜标题｜短摘要｜素材类型\n"
                    + "\n".join(lines),
                    f"【最多保留】\n{max_paragraph_groups} 个完整段落",
                    "【本轮卡点】\n"
                    + f"{checkpoint.get('primary_code', '')}｜{checkpoint.get('primary_tag_name', '')}｜{checkpoint.get('reason', '')}",
                    _conversation_block(shared_context),
                    "请返回 JSON。",
                ]
            ),
        },
    ]


def _script_authority_block(shared: dict[str, Any]) -> str:
    rules = shared.get("rules") if isinstance(shared.get("rules"), dict) else {}
    authoritative = (
        rules.get("AUTHORITATIVE FACTS")
        if isinstance(rules.get("AUTHORITATIVE FACTS"), dict)
        else {}
    )
    offer = authoritative.get("offer") if isinstance(authoritative.get("offer"), dict) else {}
    transaction = (
        authoritative.get("transaction_policy")
        if isinstance(authoritative.get("transaction_policy"), dict)
        else {}
    )
    store = (
        authoritative.get("store_address_disclosure_policy")
        if isinstance(authoritative.get("store_address_disclosure_policy"), dict)
        else {}
    )
    lines = [
        "活动价：" + _compact_value(offer.get("new_customer_price")),
        "预约金：" + _compact_value(offer.get("prepay_amount")),
        "尾款：" + _compact_value(offer.get("tail_amount")),
        "退款：" + _compact_value(offer.get("refund_rule")),
        "名额：" + _compact_value(offer.get("quota")),
        "赠品：" + _compact_value(offer.get("registration_gift")),
        "到店时间：" + _compact_value(offer.get("arrival_time_rule")),
        "付款规则：" + _compact_value(transaction.get("payment_channel_policy")),
        "门店与登记：" + _compact_value(store),
    ]
    return "【当前权威事实：仅用于排除冲突候选】\n" + "\n".join(lines)


def _current_status_block(shared: dict[str, Any]) -> str:
    facts = shared.get("authoritative_facts") if isinstance(shared.get("authoritative_facts"), dict) else {}
    order = facts.get("orders_and_payment") if isinstance(facts.get("orders_and_payment"), dict) else {}
    registration = facts.get("registration_facts") if isinstance(facts.get("registration_facts"), dict) else {}
    sent = facts.get("sent_messages") if isinstance(facts.get("sent_messages"), dict) else {}
    resolved = order.get("resolved_payment") if isinstance(order.get("resolved_payment"), dict) else {}
    orders = [item for item in order.get("orders") or [] if isinstance(item, dict)]
    case_delivery = sent.get("case_image_delivery") if isinstance(sent.get("case_image_delivery"), dict) else {}
    store_delivery = sent.get("store_address_delivery") if isinstance(sent.get("store_address_delivery"), dict) else {}
    latest_store_ids = [
        str(item).strip()
        for item in store_delivery.get("latest_batch_store_ids") or []
        if str(item).strip()
    ]
    try:
        latest_store_count = max(0, int(store_delivery.get("latest_batch_count") or 0))
    except (TypeError, ValueError):
        latest_store_count = len(latest_store_ids)
    lines = [
        "支付：" + _compact_value(_pick(resolved, "deposit_state", "payment_result", "amount", "source", "paid_protection_status")),
        "订单：" + _compact_value({"count": len(orders), "latest": _pick(orders[0] if orders else {}, "status", "deposit_state", "store_id", "store_name")}),
        "登记：" + _compact_value(_pick(registration, "customer_name", "mobile")),
        "最近门店卡批次数量："
        + str(latest_store_count)
        + "；门店ID："
        + _compact_value(latest_store_ids),
        "已发送：" + _compact_value(
            {
                "payment_cards": sent.get("payment_collection_count"),
                "activity_image": sent.get("activity_intro_image_sent"),
                "case_images": _pick(case_delivery, "total_events", "last_sent_at", "sent_image_urls"),
                "store_cards": _pick(store_delivery, "latest_batch_store_ids", "last_sent_at", "request_id"),
            }
        ),
    ]
    return "【当前状态】\n" + "\n".join(lines)


def _conversation_block(shared: dict[str, Any]) -> str:
    lines: list[str] = []
    for item in shared.get("conversation") or []:
        if not isinstance(item, dict):
            continue
        ref = str(item.get("message_ref") or "")
        role = str(item.get("role") or "")
        sent_at = str(item.get("sent_at") or item.get("timestamp") or "")
        content = str(item.get("content") or item.get("text") or "")
        lines.append(f"{ref}｜{sent_at}｜{role}：{content}")
    current = shared.get("current_message") if isinstance(shared.get("current_message"), dict) else {}
    lines.append(
        f"current_message｜{current.get('sent_at', '')}｜customer："
        f"{current.get('content') or current.get('raw_content') or ''}"
    )
    return "【完整聊天】\n" + "\n".join(lines)


def _recent_match_block(shared: dict[str, Any]) -> str:
    observations = shared.get("derived_observations") if isinstance(shared.get("derived_observations"), dict) else {}
    latest = observations.get("latest_follow_knowledge_usage") if isinstance(observations.get("latest_follow_knowledge_usage"), dict) else {}
    matched = observations.get("latest_follow_knowledge_match") if isinstance(observations.get("latest_follow_knowledge_match"), dict) else {}
    return "【最近知识匹配与使用（低权重，仅参考）】\n" + _compact_value({"matched": matched, "adopted": latest})


def _current_anchor_block(shared: dict[str, Any]) -> str:
    current = shared.get("current_message") if isinstance(shared.get("current_message"), dict) else {}
    return (
        "【当前任务锚点】\n"
        f"current_message｜{current.get('content') or current.get('raw_content') or ''}"
    )


def _sequence_index_block(items: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in items:
        steps = _compact_sequence_steps(item.get("steps") or [])
        lines.append(
            "｜".join(
                [
                    str(item.get("id") or ""),
                    str(item.get("checkpoint_code") or ""),
                    _single_line(item.get("sequence_name"), 80),
                    _single_line(item.get("description"), 90),
                    steps,
                ]
            )
        )
    return "【已启用跟进序列索引】\n" + ("\n".join(lines) or "无")


def _script_action_coverage_block(items: list[dict[str, Any]], checkpoint: dict[str, Any]) -> str:
    type_id = int(checkpoint.get("primary_type_id") or 0) if isinstance(checkpoint, dict) else 0
    tag_id = int(checkpoint.get("primary_tag_id") or 0) if isinstance(checkpoint, dict) else 0
    code = str(checkpoint.get("primary_code") or "").strip().lower() if isinstance(checkpoint, dict) else ""
    selected = None
    for item in items:
        if not isinstance(item, dict):
            continue
        if type_id > 0 and int(item.get("id") or 0) == type_id:
            selected = item
            break
        if code and str(item.get("code") or "").strip().lower() == code:
            selected = item
            break
    if not isinstance(selected, dict):
        return "【当前卡点话术动作覆盖】\n无"

    lines = [
        "type="
        + "|".join(
            [
                str(selected.get("id") or 0),
                str(selected.get("code") or ""),
                _single_line(selected.get("name"), 50),
                _action_counts_inline(selected.get("action_counts")),
            ]
        )
    ]
    tags = [tag for tag in selected.get("tags") or [] if isinstance(tag, dict)]
    primary_tag = [tag for tag in tags if tag_id > 0 and int(tag.get("id") or 0) == tag_id]
    remaining_tags = [tag for tag in tags if tag not in primary_tag]
    for tag in [*primary_tag, *remaining_tags[:8]]:
        counts = _action_counts_inline(tag.get("action_counts"))
        if not counts:
            continue
        prefix = "primary_tag" if tag in primary_tag else "tag"
        lines.append(
            prefix
            + "="
            + "|".join(
                [
                    str(tag.get("id") or 0),
                    _single_line(tag.get("name"), 70),
                    counts,
                ]
            )
        )
    return "【当前卡点话术动作覆盖】\n" + "\n".join(lines)


def _action_counts_inline(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    pairs = [
        (str(action).strip(), int(count or 0))
        for action, count in value.items()
        if str(action).strip() and int(count or 0) > 0
    ]
    pairs.sort(key=lambda item: (-item[1], item[0]))
    return ",".join(f"{action}:{count}" for action, count in pairs[:10])


def _compact_sequence_steps(steps: list[Any]) -> str:
    rendered: list[str] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        action = str(step.get("action_code") or "").strip()
        step_id = str(step.get("id") or "").strip()
        if not action or not step_id:
            continue
        trigger_base = str(step.get("trigger_base") or "").strip()
        relative_value = max(0, int(step.get("relative_value") or 0))
        relative_unit = str(step.get("relative_unit") or "minute").strip() or "minute"
        fixed_time = str(step.get("fixed_time") or "").strip()
        if trigger_base == "last_reply" and relative_value > 0:
            timing = f"after_{relative_value}_{relative_unit}"
        elif trigger_base == "add_wecom_day" and fixed_time:
            timing = f"at_{fixed_time}"
        else:
            timing = "now"
        rendered.append(f"{step_id}:{action}@{timing}")
    return ",".join(rendered)


def _checkpoint_taxonomy_block(items: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in items:
        tags = ",".join(
            f"{int(tag.get('id') or 0)}:{_single_line(tag.get('name'), 60)}"
            + _action_count_suffix(tag.get("action_counts"))
            for tag in item.get("tags") or []
            if isinstance(tag, dict) and int(tag.get("id") or 0) > 0
        )
        lines.append(
            "｜".join(
                [
                    str(int(item.get("id") or 0)),
                    str(item.get("code") or ""),
                    _single_line(item.get("name"), 80),
                    _action_count_text(item.get("action_counts")),
                    tags or "无细分标签",
                ]
            )
        )
    return (
        "【租户已发布卡点类型与标签】\n"
        "类型ID｜编码｜名称｜类型可用动作(数量)｜标签ID:名称{可用动作(数量)}\n"
        + ("\n".join(lines) or "无")
    )


def _fact_topic_catalog_block(items: list[dict[str, Any]]) -> str:
    lines = [
        "｜".join(
            (
                str(item.get("id") or ""),
                _single_line(item.get("name"), 40),
                _single_line(item.get("description"), 100),
            )
        )
        for item in items
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    ]
    return "【可选权威事实主题】\n主题ID｜名称｜用途\n" + ("\n".join(lines) or "无")


def _closing_catalog_block(value: dict[str, Any], *, shared_context: dict[str, Any]) -> str:
    status = str(value.get("status") or "unavailable")
    if status != "ok":
        return "【租户逼单目录】\nstatus=" + status + "；不可召回"
    rules = value.get("rules") if isinstance(value.get("rules"), dict) else {}
    trigger_lines = []
    for item in rules.get("triggers") or []:
        if not isinstance(item, dict):
            continue
        trigger_lines.append(
            "｜".join(
                [
                    str(item.get("rule_key") or ""),
                    _single_line(item.get("type_name"), 40),
                    _single_line(item.get("condition"), 160),
                    str(item.get("trigger_mode") or "independent"),
                    "supported" if item.get("grouping_supported", True) else "unsupported_grouping",
                    _single_line(item.get("judge_method"), 40),
                    _single_line("、".join(item.get("keywords") or []), 100),
                    _single_line(item.get("judge_note"), 100),
                ]
            )
        )
    sequence_lines = []
    for item in value.get("sequences") or []:
        if not isinstance(item, dict):
            continue
        nodes = ",".join(
            f"{node.get('node_key')}:{node.get('timing')}:{(node.get('action_type') or {}).get('name')}"
            for node in (item.get("nodes") or [])[:6]
            if isinstance(node, dict)
        )
        sequence_lines.append(
            "｜".join(
                [
                    str(item.get("sequence_key") or ""),
                    _single_line(item.get("name"), 60),
                    _single_line(item.get("positioning"), 100),
                    _single_line(item.get("trigger_text"), 180),
                    nodes,
                ]
            )
        )
    previous = shared_context.get("previous_policy_state")
    return "\n".join(
        [
            "【租户逼单目录：只召回候选，不决定动作】",
            "status=ok；source=" + str(value.get("source") or "")
            + "；checksum=" + str(value.get("checksum") or "")[:16],
            "规则：rule_key｜类型｜条件｜模式｜组合可执行性｜判定方式｜关键词线索｜AI说明",
            *(trigger_lines or ["无启用规则（必须返回 catalog_empty）"]),
            "策略：sequence_key｜名称｜定位｜适用时机｜节点摘要",
            *(sequence_lines or ["无启用策略"]),
            "全局约束=" + _compact_value(rules.get("constraints") or {}),
            "AI二次确认=" + _compact_value(rules.get("ai_confirm") or {}),
            "上一轮频控摘要=" + _compact_value(previous or {}),
        ]
    )


def _action_count_text(value: Any) -> str:
    counts = value if isinstance(value, dict) else {}
    return ",".join(
        f"{str(code)}:{int(count or 0)}"
        for code, count in sorted(counts.items())
        if str(code).strip() and int(count or 0) > 0
    ) or "无"


def _action_count_suffix(value: Any) -> str:
    rendered = _action_count_text(value)
    return "{" + rendered + "}" if rendered != "无" else ""


def _pre_store_route_block(route: dict[str, Any]) -> str:
    store = route.get("store_query") if isinstance(route.get("store_query"), dict) else {}
    checkpoint = (
        route.get("provisional_checkpoint")
        if isinstance(route.get("provisional_checkpoint"), dict)
        else route.get("checkpoint")
        if isinstance(route.get("checkpoint"), dict)
        else {}
    )
    return "【查询前临时判断（仅审计，不是最终卡点）】\n" + _compact_value(
        {
            "checkpoint": _pick(checkpoint, "primary_code", "secondary_code", "evidence_refs", "reason"),
            "store_query": _pick(store, "purpose", "location_evidence_refs", "destination_hint"),
        }
    )


def _store_resolution_fact_block(fact: dict[str, Any]) -> str:
    return "【本轮权威门店查询结果】\n" + _compact_value(
        _pick(
            fact,
            "status",
            "raw_place",
            "normalized_query",
            "province",
            "city",
            "district",
            "township",
            "resolved_admin_level",
            "coverage_status",
            "candidate_search_complete",
            "recommendation_final_for_destination",
            "exact_scope_has_store",
            "same_city_has_store",
            "visible_candidate_count",
            "delivery_store_ids",
            "ranking_method",
            "customer_claim_level",
            "clarification_required",
            "clarification_would_change_result",
            "reason",
        )
    )


def _routing_priority_block(*, post_store: bool = False) -> str:
    if post_store:
        return (
            "【本阶段执行顺序】\n"
            "门店查询已经完成。先根据客户原话判断当前显式卡点，再结合门店结果选择序列和步骤；"
            "即使没有匹配序列也必须如实保留 inquiry，不能为了选序列改成 distance。"
            "store_query.required 固定为 false。"
        )
    return (
        "【必须先独立判断门店工具】\n"
        "不得把 classification_status=none 当作默认答案。当前消息明确询问事实时至少属于 inquiry；"
        "当前消息明确表达远、贵、效果担忧、考虑、忙碌等顾虑时，必须按定义输出对应卡点。"
        "先判断当前任务是否要求查询门店、地址、位置、远近、路线、停车、营业信息，或是否补充/修改地点。"
        "当前是到店行动且历史依赖的门店事实尚未真实交付时，也属于当前任务缺门店事实；"
        "用【当前状态】中的最近门店卡批次数量核对，不把客服口头说“有店/查到了”当成已交付。"
        "该判断与是否存在匹配跟进序列完全独立：即使序列索引中没有 inquiry 序列，"
        "只要客户在问门店或给出新地点，store_query.required 仍必须为 true，并引用真实消息。"
        "完成门店判断后，再判断卡点和序列；没有匹配序列可以保持 sequence_ids 为空。"
    )


def _compact_value(value: Any) -> str:
    if value in (None, "", [], {}):
        return "无"
    if isinstance(value, dict):
        return "；".join(
            f"{key}={_compact_value(item)}"
            for key, item in value.items()
            if item not in (None, "", [], {}, False, 0)
        )[:1200]
    if isinstance(value, list):
        return "、".join(_compact_value(item) for item in value[:12])[:1200]
    return str(value)[:500]


def _pick(value: Any, *keys: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        key: value.get(key)
        for key in keys
        if value.get(key) not in (None, "", [], {}, False, 0)
    }


def _single_line(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]
