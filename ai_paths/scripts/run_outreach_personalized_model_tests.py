from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from app.config import Settings
from app.services.model_client import ModelClient
from app.services.outreach_assets import (
    appointment_blocker_materials,
    build_appointment_blocker_asset_catalog,
    build_appointment_blocker_scene_index,
    enrich_recent_outreach_media,
)
from app.services.outreach_first_day_prompts import (
    FIRST_DAY_CONTRACT_VERIFIER_PROMPT,
    FIRST_DAY_PLAN_WRITER_PROMPT,
    FIRST_DAY_SCENE_ANALYST_PROMPT,
    FIRST_DAY_SCENE_SCHEMA_REPAIR_PROMPT,
)
from app.services.outreach_prompts import (
    OUTREACH_PLAN_REVIEW_SYSTEM_PROMPT,
    OUTREACH_PLAN_SCHEMA_REPAIR_SYSTEM_PROMPT,
    OUTREACH_PLAN_SYSTEM_PROMPT,
    S10_OUTREACH_CONTEXT,
)
from app.services.precision_qa_playbook_service import PrecisionQaPlaybookService
from app.services.sop_reply_pack_service import SopReplyPackService
from app.services.sop_platform_task_policy import personalized_payment_collection_eligibility
from app.services.outreach_service import (
    FIRST_DAY_SILENCE_TRIGGER_TYPE,
    OutreachService,
    _first_day_available_sources_by_scene,
    _first_day_final_plan_error,
    _first_day_scene_analysis_error,
    _first_day_scene_lock_error,
    _first_day_verifier_error,
    _first_day_outreach_plan_error,
    _first_day_message_policy_error,
    _merge_first_day_scene_schema_repair,
    _normalize_first_day_scene_analysis,
    _normalize_first_day_repaired_plan,
    _first_day_writer_payload,
    _normalize_outreach_plan_response,
    _outreach_plan_context_error,
    _outreach_plan_structure_error,
    build_outreach_activity_quote_fact,
)


REVIEW_PROMPT = """
你是独立的销售对话质量评审。根据测试场景、当前权威活动事实、真实素材目录和计划 json，分别给以下六项 1–5 分：
psychology_accuracy、arc_diversity、asset_fit、human_tone、conversion_action、timing_fit。
4 分表示可上线，5 分表示优秀。paid_suppression 场景只要正确拒绝普通营销计划即可全部给 5 分。

评审边界：
- offer_context 是当前权威事实，可以被计划使用；历史客服话术只证明已经说过什么。
- asset_catalog 只证明可选择的素材。素材不是每轮必需，没有合适素材时 asset_strategy=none 应按正确选择评分。
- 对没有具体斑点类型的客户，`case_search` 使用“淡斑效果案例/斑点改善案例”等通用查询，并提供真实 fallback_asset_id，属于素材匹配正确；文字直接承接将附素材时，asset_fit 不应低于4分。
- 这是客户沉默后的递进触达计划，不是对当前消息的即时客服回复；首轮可立即到 12 小时，后续至少间隔 6 小时。
- `trigger_context.trigger_type=first_day_opened_silence` 时改用首日合同：固定2步，第一步立即，第二步在15–20分钟后；两步必须推进不同场景，第一步不得与近期客服/AI内容高度重合，且两步都只能使用中性称谓，不得推断性别。
- 首日门店场景没有门店查询工具和权威门店事实时，只能询问省市、区县或常去区域；出现“我给您查、帮您匹配、推荐附近门店、往就近门店看”等无法执行的承诺应判 hard_error。
- 只有事实、安全、支付、结构或明显违背场景目标的问题才算 hard_error；轻微措辞偏好只能影响分数。
- 客户已说忙、有时间再约或等天气时，计划不得继续追问具体日期、工作日、周末或时段。
- 价格透明顾虑不应为了配素材而硬发案例图或活动图；痘印痘坑案例查询不得添加客户未提供的程度、肤质或疗程。
- 相邻角度、新价值和 CTA 索取的信息不同即可认为有递进；不能仅因为都需要客户回复就把 arc_diversity 判为 3 分。
- 发卡步骤的文字和 CTA 必须直接承接随消息附上的 10 元预约金卡；如果让客户回复“活动/入口”后再发卡，属于支付动作不一致。
- `should_send_payment_collection=true` 时，运行时代码会在该步 text 后追加真实预约金卡，计划模型不允许自行输出卡片。只检查 text/CTA 是否直接引导点击随消息附上的卡片；不得因为计划的 `reply_messages` 里只有 text 而判失败。
- `activity_quote_fact.completed=true`、`reply_wait_minutes<180` 且客户刚连续质疑隐形消费、骗局或收费真实性时，最后一步直接附预约金卡是已确认的成交策略，不属于动作越界；结构和文字一致时 conversion_action 应不低于 4 分。
- 客户可见文字出现“本轮、当前步骤、计划任务”等内部结构词时，human_tone 和 conversion_action 均不得高于 3 分。
- 评分必须以客户实际看到的 `reply_messages` 为准，后台 `new_value/message_goal` 写得正确但客户文字没有交付不算完成。出现“您空下来我再帮您看、后面方便再说、先不打扰”等送客表达，且同一句没有新增专业、知识或活动价值时，psychology_accuracy 和 conversion_action 均不得高于 3 分。
- 只有 recent_messages 中存在真实完整报价且 plan 标记发卡时，才检查文字与卡片是否一致。没有完整报价时不发卡是正确硬边界，不得因此判 hard_error 或降低 conversion_action。
- 没有完整报价时，最后一轮直接讲一个权威活动事实并用封闭式动作收口，可评为有效成交推进；不能要求客户先回复关键词才提供本轮本可直接说明的信息。
- 没有完整报价时，最后一轮清楚给出一个量化活动事实（268元、限30名或180元赠送）并封闭式询问登记，可将 conversion_action 评为4分以上；不要因为未发卡扣分。
- reply_messages 已直接交付本轮 new_value，CTA 只是自然的后续选择时可以高分；任何“回复看/活动/继续/判断后我再提供”都应降低 conversion_action。
- 每套计划必须至少一轮 content_mode=value_only；该轮不提价格、名额、预约金、付款或强成交 CTA。
- 选择 case_search/configured_image/operation_video 后仍问“要不要我发”属于动作不一致，应降低 conversion_action；直接说明“我给您放一个参考”才是正确承接。
- `case_search` 的 `fallback_asset_id` 是可选兜底，不是必填。只要 `case_query` 合法且未编造 URL，不能因 fallback 为空判 hard_error 或降低 asset_fit；运行时代码会查询真实案例库，失败时发送模型会按无素材事实改写文字。
- 次数顾虑需要先给非绝对的正面预期，再说明按实际状态评估；只复读类型、深浅和检测属于没有正面回答。
- 次数顾虑以“没法一口答死/没法只看一眼定/不能确定”开头时，psychology_accuracy 不得高于3分；应先说明很多客户一次能看到直观改善，再补真实判断边界。
- 最后一轮已经给出活动量化事实并直接问“登记一个吗”或同义封闭式动作时，conversion_action 应为4分以上。以“有空找我、需要时喊我、觉得合适告诉我、我再发完整活动”收尾只能给3分以下。
- “算了、太远、不方便、暂时不用、以后再说”属于软拒绝，不等于明确停止联系。除 paid_suppression 或明确“不要再联系/别发消息/拉黑/投诉退款”外，`should_create_plan=false` 属于 hard_error。
- human_tone 评估客户可见 `reply_messages`，不是后台计划分析。4 分要求像真人微信：通常1–2句、具体口语、没有报告腔或问卷腔；出现“困扰、改善思路、是否接近、接着判断、往下判断”等抽象计划语言，或要求客户“回我A/B/C”选择内部分类时，human_tone 不得高于3分。
- 客户只有“你好/在吗”等问候、没有描述皮肤问题时，首轮直接假设“您这种斑点/先看皮肤状态”，或用“您先说说”式问卷开场，human_tone 不得高于3分。
- 相邻两轮客户文字都在说检测、皮肤状态、价格、同一个缺失信息或同一个 CTA 时，arc_diversity 不得高于3分；后台角度枚举不同不能掩盖客户实际收到的内容重复。
- 每套计划应生成 2–3 步完整周期。后续步骤必须假设前一步未回复，并换成不依赖同一条缺失信息的新价值；三步重复催地址、时间、照片或同一顾虑时，arc_diversity 和 timing_fit 均不得高于2分。
- 低意向或长期沉默计划仍应形成 2–3 步完整周期，但可以拉开间隔并用 `cta=none` 直接交付关怀、科普、专业价值或真实证据。同一个必要事实整套最多询问一次，后续不能继续催同一信息。
- 必须结合 `conversation_activity.reply_wait_minutes` 评分：刚开始沉默时首轮应承接最近顾虑并保持成交动量；沉默超过一天时首轮应明显降低催促感，用产品价值、斑点/护理知识、轻关怀或真实证据重新建立联系。长期沉默仍重复历史问题或原 CTA 时，psychology_accuracy 和 timing_fit 均不得高于3分。
- `reply_wait_minutes>=1440` 时，第一轮应为 `cta=none`，优先采用 education/proof/professionalism/self_image 的陈述式价值触达；仍用 empathy/convenience 包装“活动还是效果”等问卷或问号催回复时，psychology_accuracy、human_tone 和 timing_fit 均不得高于3分。
- `reply_wait_minutes>=1440` 时，把第一轮与全部历史客服消息逐条比较。若仍在说历史已经发过的门店、地址、路线、停车、检测、价格、预约金或案例，即使换了措辞，psychology_accuracy、arc_diversity 和 timing_fit 均不得高于2分。
- `customer_silence_minutes>=4320` 时，第一轮必须提供一个历史未讲过的批准科普、未发送真实证据或全新产品价值。说“门店已经发您了”“到店先检测”“之前给您介绍过”属于明显无价值复读。
- `offer_context.outreach_knowledge_facts` 是允许模型选用的候选知识目录，不代表这些内容历史已经发给客户。只有 `recent_messages/recent_sop_delivery/recent_media_delivery` 才能证明客户实际收到过某个话题或素材。
- `customer_silence_minutes>=4320` 且最近没有客户主动报名、询问付款或明确参加时，本周期应以重新开口为目标，不应因历史报价完成就复读268/10/258或直接附预约金卡；这样做应降低 psychology_accuracy、conversion_action 和 timing_fit。
- `customer_silence_minutes>=4320` 时，评估动作发生时已经等待足够久，第一步必须在0–180分钟内轻触，后续每一步与前一步至少间隔24小时；不满足时 timing_fit 不得高于2分。
- 长期沉默计划最后一步用一个简短、自然、与前两轮新价值相关的问题促使客户重新开口，是正确的 conversion_action，可评4分以上；不能因为它是问句就降分。“您平时是不是经常在户外呀？”以及历史未问过的“您现在最想先改善脸上哪一块呀？”都属于可接受的单一低门槛问题。只有使用“还是”二选一、要求客户选择效果/价格/活动/门店、口令式选择或重复历史问题时才应扣分。
- 时间只按数值判断：`customer_silence_minutes>=4320` 时，第一步在0–180分钟且后续相邻步骤差值均不少于1440分钟，就符合长期沉默节奏，不得凭主观感觉误判 timing_fit。
- 第一轮已讲防晒、清洁、补水等日常护理，第二轮仍讲另一条日常护理，即使把标签改成 professionalism 也属于语义重复，arc_diversity 不得高于3分；第二轮应切换真实证据、技术或记录价值。
- 一步输出两条 text 是允许的：只有两条共同围绕一个新价值、自然分段且没有重复时才可高分；机械拆句、两条重复结论或借第二条夹带额外营销，应降低 human_tone。
- “您先忙、等方便再说、先不打扰、后面有空再找我、先放着”等主动送客表达，在任何步骤出现时 conversion_action 不得高于3分；如果整条消息没有继续提供具体新价值，psychology_accuracy 也不得高于3分。
- 最后一轮未发卡时，客户可见文字必须用明确封闭式问题完成收口；只陈述活动事实，或以“想了解我继续说/我给您留着”结束时，conversion_action 不得高于3分。
- 客户尚未明确接受时使用“我先给您留着、先把资格留上、已经登记”等已执行表述属于事实越界，应判 hard_error；正确方式是询问是否登记。
- 单条活动消息同时堆叠活动价、限量名额和赠品三个卖点时，human_tone 不得高于3分。自然微信应结合当前客户只选一个主要理由。
- 客户文字承诺会发送案例、图片、视频或参考，但该步 `asset_strategy=none` 时，asset_fit 和 conversion_action 均不得高于3分。“我给您发个同类改善参考/给您放个做前做后参考”都属于明确素材承诺，不能因为语气自然而放过。
- “我给您找了个做前做后的真实对比，您先看看”属于自然案例承接；“给您补个同类真实参考，看看改善思路是否接近”属于机器表达。

以下抑制属于正确行为，不得判 hard_error，并应给六项 5 分：
- 输入只有平台固定自动加好友开场，没有客户本人真实开口。
- 已付、已预约、退款投诉、健康风险、明确停止联系或历史归属不可信。
- 客户已长期沉默，历史活动、案例、预约金和催促已经饱和，并且 offer_context、真实素材和客户事实中都没有相关、真实、未讲过的新价值。

客户本人发送“你好”“在吗”等自然问候属于真实开口。客户表达距离、天气、忙碌、“算了”等软拒绝时，只要历史没有营销饱和且 offer_context 仍有相关未讲价值，就不应直接抑制。
“历史营销饱和且无新价值”抑制只适用于 `customer_silence_minutes>=4320` 的长期沉默；近期真实顾虑且没有硬边界时 `should_create_plan=false` 必须判为 hard_error。

输出必须是扁平字段，不要把六项分数放进 `scores` 子对象：
{
  "psychology_accuracy": 5,
  "arc_diversity": 5,
  "asset_fit": 5,
  "human_tone": 5,
  "conversion_action": 5,
  "timing_fit": 5,
  "hard_error": false,
  "hard_error_reason": "",
  "concise_reason": "简洁结论"
}

最终校准：
- 长期沉默第一步在 0–180 分钟都符合要求；最后一步用“您平时是不是经常在户外呀？”这类单一日常问题重新开口，`conversion_action/timing_fit` 可给 4 分以上。
- 客户刚连续质疑隐形消费或骗局，`reply_wait_minutes<180` 且完整报价已完成时，最后一步直接附 10 元预约金卡是正确成交动作；结构一致时不得称为越界或删除卡片。
- `reply_wait_minutes<180`、完整报价已完成且客户刚提出反弹、效果或价格等普通顾虑时，在先解决顾虑并交付不同价值后，最后一步直接附预约金卡同样是正确成交收口，结构一致时 conversion_action 应不低于 4。
- 历史只讲过门店、停车、活动价格、预约金和检测，而温和护理、防晒、原相机记录没有发送时，不能把计划判成“无新价值”。
- 客户文字含“回我一个字/回我一下、我好接着说、如果您还想了解、我继续跟您说可以吗、我整理好了要发您吗、我先给您留着”等流程尾巴时，human_tone 和 conversion_action 不得高于 3；自然问题应直接停在问号。
- 长期沉默只有两项真实新价值时，使用两步并在第二步结束是正确设计；第三步只是泛问日晒或要求回复口令时应删除，不能因为少一轮扣分。
只输出有效 json。
""".strip()

FIRST_DAY_SEMANTIC_REVIEW_PROMPT = """
你是首日微信沉默跟进的独立语义评审，只评价客户实际看到的两步消息，不检查程序结构。
判定位置重复前必须核对消息角色：客户问“有门店吗”不等于客服已经询问客户省市或区县；此前客服只回答“有门店”时，后续首次询问客户常去城市或区县是有效推进，不得降低 `conversation_continuity`、`scene_execution` 或 `semantic_non_repeat`。
活动介绍和预约推进是两个不同交付：前者说明活动规则，后者把 10 元规则转成微信转账或红包、锁名额、时间灵活的可执行动作。只要客户可见文本增加该行动价值，就不得仅因都出现 10 元、抵扣和退款而判为重复。

输入包含测试案例、权威场景分析、候选计划、素材目录和 `runtime_delivery_view`。分别给以下五项 1 至 5 分，4 分表示可上线：
- `workflow.scene_analysis` 是已经通过代码合同的权威场景锁。测试案例中的预期字段只用于离线统计，不能覆盖权威场景锁；不得仅因候选执行的锁定场景与测试案例预期不同而降低任何语义分数。
- 评审前必须逐步读取 `workflow.scene_analysis.step1_scene/step2_scene`。如果候选计划的 `scene` 与这两个锁定场景一致，禁止写“和锁定场景不一致、偏离权威场景锁”作为扣分理由；只能评价客户可见文本是否真正交付该锁定场景。
- `conversation_continuity`：是否自然承接客户最近聊天，而不是重新开场或答非所问。
- `scene_execution`：两步客户可见文本是否分别真正完成锁定场景，而不只是后台 scene 标签正确。
- `semantic_non_repeat`：第一步是否避开历史客服/SOP内容，两步是否交付不同的新价值。
- `human_tone`：是否像真人微信短聊，具体、自然、没有报告腔、问卷腔或流程尾巴。
- `barrier_accuracy`：是否准确处理客户真实卡点，并保持合理销售推进。

评审边界：
- `您、亲、顾客、很多人` 都是中性表达，“亲”绝不是性别称谓。
- `asset_strategy=configured_image` 且 `asset_id` 来自素材目录，表示代码会在文字后立即发送真实图片；`reply_messages` 只能包含文本，禁止要求其中出现 image 或 URL。
- `runtime_delivery_view.structured_delivery_appended_by_code.asset_will_be_sent=true` 是图片会随本步骤真实发送的确定性事实。此时绝对禁止声称“只有文本、没有图片、没有实际发送素材”。
- 当锁定场景是 `effect_proof` 且上述图片发送事实为 true 时，真实图片本身就是本步骤交付的新效果证据。自然、简短且用于引出图片的一句话是合格的微信短聊，不得因为文字没有复述图片内容而降低 `scene_execution` 或 `semantic_non_repeat`。
- 当锁定场景是 `deposit_close` 且 `payment_card_will_be_sent=true` 时，预约金卡本身就是交易动作；文本仍需说明锁名额和低风险退款，但不得因为 `reply_messages` 没有卡片结构而判失败。
- `activity_intro` 必须说明 10 元预约登记、到店抵扣和退款规则；`deposit_close` 必须再次说明 10 元锁名额、后面来仍可用和低风险退款。两步都出现“10元、到店抵扣、未做或不满意可退”不自动算重复，只有两步没有区分“活动规则介绍”和“预约金锁名额行动”时才降低 `semantic_non_repeat`。
- `trust_repair` 中“到店先看效果和方案，满意或确认适合再做”是直接交付的低风险价值，不是送客或等待式承接，不得因此降低 `scene_execution`、`human_tone` 或 `barrier_accuracy`。只有“您慢慢看、以后需要再联系、方便时再说、下次再聊”等结束当前推进的表达才是送客。
- `persuasion_angle=self_image` 必须真正写到改善后的自信、重视自身状态或给自己一次改善机会；“确认适合再决定、心里更稳或更有底”仍是低风险决策语义。若两步分别交付真实的低风险价值和真实的自我形象价值，应视为两个不同场景；若两步都围绕适合后再决定，则降低 `scene_execution` 和 `semantic_non_repeat`。
- 只能根据 `recent_messages` 和结构事实判断客户卡点。禁止凭空声称存在费用、支付、距离、健康或其他聊天中没有出现的异议，并禁止因此降低 `barrier_accuracy`。
- 第二步延迟 15、16、17、18、19 或 20 分钟都合法，时间由代码评审，不得主观扣分。
- 支付门禁为 false 只代表禁止发卡，不代表应停止文本计划。
- `scene_delivery_check` 是内部自检，只能辅助理解；必须以 `reply_messages` 的真实文本和素材动作判断是否完成场景。
- 当 `case.expected` 明确要求停止触达，且场景分析正确输出 `eligible=false`、计划没有步骤时，五项都给 5 分，不得因没有客户话术扣分。
- 轻微措辞偏好只能扣对应分数，不能设置 `hard_error=true`。
- 只有客户可见文本明显虚构已查到门店、已锁名额、已创建订单等事实，或场景文字与锁定目标完全相反时，才可设置 `hard_error=true`。
- 必须逐条对照 `case.expected`，但当它与上述运行时合同冲突时以上述合同为准。

只返回一个 JSON：
{
  "conversation_continuity": 5,
  "scene_execution": 5,
  "semantic_non_repeat": 5,
  "human_tone": 5,
  "barrier_accuracy": 5,
  "hard_error": false,
  "hard_error_reason": "",
  "concise_reason": "简洁结论"
}
""".strip()

ALLOWED_ANGLES = {
    "education",
    "proof",
    "professionalism",
    "empathy",
    "self_image",
    "convenience",
    "scarcity",
    "low_risk_action",
}
ALLOWED_ASSET_STRATEGIES = {"none", "configured_image", "operation_video", "case_search"}

FIRST_DAY_EXPECTED_SCENES: dict[str, dict[str, Any]] = {
    "first_day_completed_effect_activity_store_then_deposit": {
        "step1": {"store_area_request"},
        "step2": {"deposit_close"},
    },
    "first_day_effect_next_scene": {"step1": {"activity_intro", "trust_repair", "objection_resolution"}, "step2": {"activity_intro", "store_area_request", "trust_repair", "objection_resolution"}},
    "first_day_price_already_explained": {"step1": {"effect_proof", "trust_repair", "objection_resolution", "store_area_request"}, "step2": {"effect_proof", "store_area_request", "trust_repair", "objection_resolution"}},
    "first_day_store_without_lookup": {"step1": {"store_area_request"}, "step2": {"effect_proof", "activity_intro"}},
    "first_day_consider_neutral_self_image": {"step1": {"trust_repair"}, "step2": {"effect_proof", "activity_intro", "store_area_request", "objection_resolution"}},
    "first_day_history_near_repeat": {"step1": {"trust_repair", "objection_resolution"}, "step2": {"store_area_request", "trust_repair", "objection_resolution"}},
    "first_day_unknown_gender": {"step1": {"effect_proof"}, "step2": {"store_area_request", "trust_repair"}},
    "real_effect_many_images_then_activity": {"step1": {"activity_intro"}, "step2": {"trust_repair", "objection_resolution"}},
    "real_effect_text_only_should_deliver_images": {"step1": {"effect_proof"}, "step2": {"activity_intro"}},
    "real_price_complete_scam_objection": {"step1": {"effect_proof", "trust_repair"}, "step2": {"trust_repair", "objection_resolution", "store_area_request"}},
    "real_price_asked_but_not_answered": {"step1": {"activity_intro"}, "step2": {"store_area_request", "trust_repair", "objection_resolution"}},
    "real_effect_after_customer_photo_no_overdiagnosis": {"step1": {"activity_intro"}, "step2": {"store_area_request"}},
    "real_long_full_funnel_do_not_restart": {"step1": {"trust_repair"}, "step2": {"objection_resolution"}},
    "real_long_location_correction_and_noise": {"step1": {"effect_proof"}, "step2": {"activity_intro", "trust_repair"}},
    "real_consider_after_full_pitch_neutral": {"step1": {"trust_repair"}, "step2": {"store_area_request"}},
    "real_too_far_soft_refusal_new_value": {"step1": {"effect_proof"}, "step2": {"activity_intro"}},
    "real_busy_weather_no_date_pressure": {"step1": {"effect_proof", "activity_intro"}, "step2": {"effect_proof", "activity_intro"}},
    "real_active_itch_health_risk_suppress": {"eligible": False},
    "real_auto_opening_only_defensive_suppress": {"eligible": False},
    "real_noisy_long_unknown_gender_no_repeat": {"step1": {"trust_repair"}, "step2": {"objection_resolution"}},
    "real_store_city_missing_no_lookup_tool": {"step1": {"store_area_request"}, "step2": {"effect_proof", "activity_intro"}},
    "real_store_city_known_district_missing": {"step1": {"store_area_request"}, "step2": {"effect_proof"}},
    "real_store_card_already_sent_progress_activity": {"step1": {"effect_proof"}, "step2": {"activity_intro"}},
    "real_payment_page_slow_valid_order": {"step1": {"deposit_close"}, "step2": {"effect_proof", "trust_repair"}},
    "real_payment_requested_but_order_missing": {"step1": {"store_area_request"}, "step2": {"effect_proof", "trust_repair"}},
}


def _first_day_scene_contract_passed(case_id: str, scene_analysis: dict[str, Any]) -> bool:
    expected = FIRST_DAY_EXPECTED_SCENES.get(case_id)
    if not expected:
        return False
    eligible = bool(scene_analysis.get("eligible"))
    if expected.get("eligible") is False:
        return bool(scene_analysis) and scene_analysis.get("eligible") is False
    return bool(
        eligible
        and str(scene_analysis.get("step1_scene") or "") in expected["step1"]
        and str(scene_analysis.get("step2_scene") or "") in expected["step2"]
        and str(scene_analysis.get("step1_scene") or "")
        != str(scene_analysis.get("step2_scene") or "")
    )


def _first_day_review_delivery_view(
    plan: dict[str, Any],
    asset_catalog: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    assets = {
        str(item.get("asset_id") or ""): item
        for item in asset_catalog
        if isinstance(item, dict) and str(item.get("asset_id") or "")
    }
    output: list[dict[str, Any]] = []
    for step in plan.get("steps") or []:
        if not isinstance(step, dict):
            continue
        asset_id = str(step.get("asset_id") or "")
        asset_strategy = str(step.get("asset_strategy") or "none")
        asset = assets.get(asset_id) or {}
        output.append(
            {
                "step": step.get("step"),
                "scene": step.get("scene"),
                "customer_visible_texts": [
                    str((message.get("content") or {}).get("text") or "")
                    for message in step.get("reply_messages") or []
                    if isinstance(message, dict) and isinstance(message.get("content"), dict)
                ],
                "structured_delivery_appended_by_code": {
                    "asset_will_be_sent": bool(
                        (asset_strategy != "none" and asset_id in assets)
                        or asset_strategy == "case_search"
                    ),
                    "asset_strategy": asset_strategy,
                    "asset_id": asset_id,
                    "asset_type": asset.get("type") or ("image" if asset_strategy == "case_search" else ""),
                    "payment_card_will_be_sent": bool(step.get("should_send_payment_collection")),
                },
            }
        )
    return output


def _hard_errors(plan: dict[str, Any], asset_ids: set[str], case: dict[str, Any]) -> list[str]:
    if not bool(plan.get("should_create_plan", True)):
        expected = str(case.get("expected") or "").lower()
        if (
            bool(case.get("allow_suppression"))
            or str(case.get("id") or "") == "paid_suppression"
            or "should_create_plan=false" in expected
            or int((case.get("conversation_activity") or {}).get("real_customer_message_count") or 0) == 0
        ):
            return []
        return ["unexpected_suppression"]
    steps = [item for item in plan.get("steps") or [] if isinstance(item, dict)]
    errors: list[str] = []
    first_day = str((case.get("trigger_context") or {}).get("trigger_type") or "") == FIRST_DAY_SILENCE_TRIGGER_TYPE
    structure_error = _first_day_outreach_plan_error(plan) if first_day else _outreach_plan_structure_error(plan)
    if structure_error:
        errors.append(f"structure:{structure_error}")
    context_error = _outreach_plan_context_error(
        plan,
        activity_quote_fact=build_outreach_activity_quote_fact(case.get("recent_messages") or [], {}),
        reply_wait_minutes=int((case.get("conversation_activity") or {}).get("reply_wait_minutes") or 0),
        customer_silence_minutes=int(
            (case.get("conversation_activity") or {}).get("customer_silence_minutes") or 0
        ),
        allow_first_day_internal_activity_quote=first_day,
    )
    if context_error:
        errors.append(f"context:{context_error}")
    if (first_day and len(steps) != 2) or (not first_day and len(steps) not in {2, 3}):
        errors.append("step_count")
    angles = [str(item.get("persuasion_angle") or "") for item in steps]
    if not first_day and any(angle not in ALLOWED_ANGLES for angle in angles):
        errors.append("invalid_angle")
    if not first_day and any(left == right for left, right in zip(angles, angles[1:])):
        errors.append("repeated_adjacent_angle")
    content_modes = [str(item.get("content_mode") or "") for item in steps]
    if "value_only" not in content_modes:
        errors.append("missing_value_only")
    if sum(bool(item.get("should_send_payment_collection")) for item in steps) > 1:
        errors.append("multiple_payment_cards")
    if bool(case.get("expect_payment_collection")) and not any(
        bool(item.get("should_send_payment_collection")) for item in steps
    ):
        errors.append("missing_expected_payment_card")
    if bool(case.get("forbid_payment_collection")) and any(
        bool(item.get("should_send_payment_collection")) for item in steps
    ):
        errors.append("unexpected_payment_card")
    delays = [int(item.get("delay_minutes") or 0) for item in steps]
    if first_day:
        if not delays or delays[0] != 0:
            errors.append("invalid_first_delay")
        if len(delays) == 2 and not 15 <= delays[1] - delays[0] <= 20:
            errors.append("invalid_step_gap")
    else:
        if delays and not 0 <= delays[0] <= 720:
            errors.append("invalid_first_delay")
        if any(not 360 <= right - left <= 4320 for left, right in zip(delays, delays[1:])):
            errors.append("invalid_step_gap")
    if delays and delays[-1] > 10080:
        errors.append("plan_too_long")
    for index, step in enumerate(steps):
        expected_no_reply_action = "end_plan" if index == len(steps) - 1 else "advance_to_next_step"
        if str(step.get("no_reply_action") or "") != expected_no_reply_action:
            errors.append(f"invalid_no_reply_action:{index + 1}")
        if not str(step.get("no_reply_strategy") or "").strip():
            errors.append(f"missing_no_reply_strategy:{index + 1}")
        reply_messages = step.get("reply_messages")
        source_ids = {
            str(item or "")
            for item in step.get("content_sources") or []
            if isinstance(item, str) and str(item or "")
        }
        mainline_sop_step = first_day and any(
            source_id.startswith("sop-pack:") for source_id in source_ids
        )
        invalid_message_count = (
            not isinstance(reply_messages, list)
            or not reply_messages
            or (not mainline_sop_step and len(reply_messages) > 2)
        )
        if invalid_message_count or any(
            not isinstance(item, dict) or str(item.get("type") or "") != "text"
            for item in reply_messages or []
        ):
            errors.append(f"invalid_reply_messages:{index + 1}")
        strategy = str(step.get("asset_strategy") or "none")
        if strategy not in ALLOWED_ASSET_STRATEGIES:
            errors.append(f"invalid_asset_strategy:{index + 1}")
        if strategy in {"configured_image", "operation_video"} and str(step.get("asset_id") or "") not in asset_ids:
            errors.append(f"unknown_asset:{index + 1}")
        if (
            bool(step.get("should_send_payment_collection"))
            and not first_day
            and index != len(steps) - 1
        ):
            errors.append("payment_not_final")
        if str(step.get("content_mode") or "") == "value_only" and bool(
            step.get("should_send_payment_collection")
        ):
            errors.append("value_only_payment")
        if first_day:
            policy_error, _evidence = _first_day_message_policy_error(
                [
                    str((message.get("content") or {}).get("text") or "")
                    for message in step.get("reply_messages") or []
                    if isinstance(message, dict) and isinstance(message.get("content"), dict)
                ],
                step_index=index + 1,
                plan={
                    "source_snapshot": {
                        "recent_messages": case.get("recent_messages") or [],
                        "recent_sop_delivery": case.get("recent_sop_delivery") or [],
                    }
                },
                context={},
            )
            if policy_error:
                errors.append(f"message_policy:{index + 1}:{policy_error}")
    return errors


def _long_silence_timing_is_valid(plan: dict[str, Any], case: dict[str, Any]) -> bool:
    activity = case.get("conversation_activity") or {}
    if int(activity.get("customer_silence_minutes") or 0) < 4320:
        return False
    steps = [item for item in plan.get("steps") or [] if isinstance(item, dict)]
    if len(steps) not in {2, 3}:
        return False
    delays = [int(item.get("delay_minutes") or -1) for item in steps]
    return (
        0 <= delays[0] <= 180
        and all(1440 <= right - left <= 4320 for left, right in zip(delays, delays[1:]))
        and delays[-1] <= 10080
    )


async def _first_day_chat_json(
    client: ModelClient,
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    for attempt in range(1, 4):
        try:
            return await client.chat_json(
                messages,
                tier="strong",
                temperature=0.0,
            )
        except Exception as exc:
            if attempt >= 3 or "timeout" not in f"{type(exc).__name__}: {exc}".lower():
                raise
    raise RuntimeError("首日模型超时重试异常结束")


async def _run_first_day_workflow(
    client: ModelClient,
    payload: dict[str, Any],
    artifacts: dict[str, Any],
    appointment_material_catalog: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload["available_sources_by_scene"] = _first_day_available_sources_by_scene(payload)
    if int((payload.get("conversation_activity") or {}).get("real_customer_message_count") or 0) == 0:
        scene_analysis = _normalize_first_day_scene_analysis(
            {},
            message_count=len(payload.get("recent_messages") or []),
            source_snapshot=payload,
        )
        artifacts["scene_analysis"] = scene_analysis
        artifacts["routing_decision"] = "first_day_customer_not_opened"
        return (
            {
                "should_create_plan": False,
                "stall_reason": "first_day_customer_not_opened",
                "plan_arc": "",
                "steps": [],
            },
            artifacts,
        )
    scene_analysis = await _first_day_chat_json(
        client,
        [
            {"role": "system", "content": FIRST_DAY_SCENE_ANALYST_PROMPT},
            {"role": "user", "content": json.dumps({"source_snapshot": payload}, ensure_ascii=False)},
        ],
    )
    artifacts["scene_analysis_raw"] = scene_analysis
    scene_analysis = _normalize_first_day_scene_analysis(
        scene_analysis,
        message_count=len(payload.get("recent_messages") or []),
        source_snapshot=payload,
    )
    scene_error = _first_day_scene_analysis_error(scene_analysis, source_snapshot=payload)
    if scene_error:
        invalid_scene_analysis = scene_analysis
        scene_analysis = await _first_day_chat_json(
            client,
            [
                {"role": "system", "content": FIRST_DAY_SCENE_SCHEMA_REPAIR_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "source_snapshot": payload,
                            "invalid_scene_analysis": scene_analysis,
                            "schema_error": scene_error,
                            "locked_scenes": {
                                "step1": str(scene_analysis.get("step1_scene") or ""),
                                "step2": str(scene_analysis.get("step2_scene") or ""),
                            },
                            "available_sources_by_scene": payload.get(
                                "available_sources_by_scene"
                            )
                            or {},
                            "instruction": "只修复 JSON 结构合同，不改变已有业务判断。",
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        )
        scene_analysis = _merge_first_day_scene_schema_repair(
            invalid_scene_analysis,
            scene_analysis,
        )
        scene_analysis = _normalize_first_day_scene_analysis(
            scene_analysis,
            message_count=len(payload.get("recent_messages") or []),
            source_snapshot=payload,
        )
        artifacts["scene_analysis_repaired"] = scene_analysis
        scene_error = _first_day_scene_analysis_error(scene_analysis, source_snapshot=payload)
    if scene_error:
        invalid_scene_analysis = scene_analysis
        scene_analysis = await _first_day_chat_json(
            client,
            [
                {"role": "system", "content": FIRST_DAY_SCENE_SCHEMA_REPAIR_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "source_snapshot": payload,
                            "invalid_scene_analysis": scene_analysis,
                            "schema_error": scene_error,
                            "locked_scenes": {
                                "step1": str(scene_analysis.get("step1_scene") or ""),
                                "step2": str(scene_analysis.get("step2_scene") or ""),
                            },
                            "available_sources_by_scene": payload.get(
                                "available_sources_by_scene"
                            )
                            or {},
                            "instruction": "再次只修复剩余 JSON 结构错误，保留已有业务判断并返回完整对象。",
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        )
        scene_analysis = _merge_first_day_scene_schema_repair(
            invalid_scene_analysis,
            scene_analysis,
        )
        scene_analysis = _normalize_first_day_scene_analysis(
            scene_analysis,
            message_count=len(payload.get("recent_messages") or []),
            source_snapshot=payload,
        )
        artifacts["scene_analysis_repaired_2"] = scene_analysis
        scene_error = _first_day_scene_analysis_error(scene_analysis, source_snapshot=payload)
    if scene_error:
        raise RuntimeError(f"scene_analysis_invalid: {scene_error}")
    artifacts["scene_analysis"] = scene_analysis
    if not bool(scene_analysis.get("eligible")):
        return (
            {
                "should_create_plan": False,
                "stall_reason": str(scene_analysis.get("suppress_reason") or "scene_analyst_suppressed"),
                "plan_arc": "",
                "steps": [],
            },
            artifacts,
        )

    writer_result = await _first_day_chat_json(
        client,
        [
            {"role": "system", "content": FIRST_DAY_PLAN_WRITER_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    _first_day_writer_payload(
                        payload,
                        scene_analysis,
                        appointment_material_catalog=appointment_material_catalog,
                    ),
                    ensure_ascii=False,
                ),
            },
        ],
    )
    artifacts["writer_result"] = writer_result
    normalized_writer_result = _normalize_outreach_plan_response(dict(writer_result))
    writer_structure_error = _first_day_final_plan_error(
        normalized_writer_result,
        scene_analysis=scene_analysis,
        source_snapshot=payload,
    )
    artifacts["writer_structure_error"] = writer_structure_error
    verifier_result = await _first_day_chat_json(
        client,
        [
            {"role": "system", "content": FIRST_DAY_CONTRACT_VERIFIER_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "source_snapshot": payload,
                        "scene_contract": scene_analysis,
                        "candidate_plan": writer_result,
                        "candidate_structure_error": writer_structure_error,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    )
    artifacts["verifier_result_raw"] = verifier_result
    verifier_error = _first_day_verifier_error(verifier_result)
    if verifier_error:
        verifier_result = await _first_day_chat_json(
            client,
            [
                {"role": "system", "content": FIRST_DAY_CONTRACT_VERIFIER_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "source_snapshot": payload,
                            "scene_contract": scene_analysis,
                            "candidate_plan": writer_result,
                            "invalid_verifier_result": verifier_result,
                            "schema_error": verifier_error,
                            "instruction": "只修复审核结果 JSON 合同，不得输出或改写客户计划。",
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        )
        artifacts["verifier_result_repaired"] = verifier_result
        verifier_error = _first_day_verifier_error(verifier_result)
    if verifier_error:
        raise RuntimeError(f"verifier_invalid: {verifier_error}")
    artifacts["verifier_result"] = verifier_result
    if str(verifier_result.get("decision") or "") == "block":
        return (
            {
                "should_create_plan": False,
                "stall_reason": "contract_verifier_blocked",
                "plan_arc": "",
                "steps": [],
            },
            artifacts,
        )
    needs_repair = bool(writer_structure_error) or str(verifier_result.get("decision") or "") == "repair"
    if needs_repair:
        violations = list(verifier_result.get("violations") or [])
        repair_instructions = list(verifier_result.get("repair_instructions") or [])
        if writer_structure_error and not repair_instructions:
            violations.append(
                {
                    "code": "deterministic_contract_error",
                    "field": "candidate_plan",
                    "evidence": writer_structure_error,
                }
            )
            repair_instructions.append(
                {
                    "field": "candidate_plan",
                    "instruction": "修复确定性合同错误，严格保留两个锁定场景和业务目标。",
                }
            )
        repaired_writer_result = await _first_day_chat_json(
            client,
            [
                {"role": "system", "content": FIRST_DAY_PLAN_WRITER_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        _first_day_writer_payload(
                            payload,
                            scene_analysis,
                            appointment_material_catalog=appointment_material_catalog,
                            candidate_plan=writer_result,
                            violations=violations,
                            repair_instructions=repair_instructions,
                            deterministic_error=writer_structure_error,
                        ),
                        ensure_ascii=False,
                    ),
                },
            ],
        )
        artifacts["writer_repair_result"] = repaired_writer_result
        plan = _normalize_first_day_repaired_plan(
            _normalize_outreach_plan_response(dict(repaired_writer_result)),
            scene_analysis=scene_analysis,
        )
    else:
        plan = normalized_writer_result
    plan_error = _first_day_final_plan_error(
        plan,
        scene_analysis=scene_analysis,
        source_snapshot=payload,
    )
    artifacts["final_contract_error"] = plan_error
    if plan_error:
        raise RuntimeError(f"final_plan_invalid: {plan_error}")
    return plan, artifacts


async def _run_case(
    client: ModelClient,
    case: dict[str, Any],
    *,
    attempt: int,
    asset_catalog: list[dict[str, Any]],
    appointment_material_catalog: list[dict[str, Any]],
    first_day_sop_sequence: list[dict[str, Any]],
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    first_day = (
        str((case.get("trigger_context") or {}).get("trigger_type") or "")
        == FIRST_DAY_SILENCE_TRIGGER_TYPE
    )
    conversation_activity = dict(case.get("conversation_activity") or {})
    if "real_customer_message_count" not in conversation_activity:
        auto_opening = "我已经添加了你，现在我们可以开始聊天了。"
        conversation_activity["real_customer_message_count"] = sum(
            1
            for message in case.get("recent_messages") or []
            if str(message.get("direction") or message.get("sender_type") or "").lower()
            in {"customer", "user", "external"}
            and str(message.get("content") or "").strip() != auto_opening
        )
    payload = {
        "customer_id": f"model-test-{case['id']}",
        "memory": {},
        "recent_messages": case.get("recent_messages") or [],
        "conversation_activity": conversation_activity,
        "customer_context": case.get("customer_context") or {},
        "current_stage": (
            "first_day_opened_silence" if first_day else "day2_personalized_spoken_unbooked"
        ),
        "business_goal": (
            "在客户首日意向仍高时自然承接最近聊天，立即推进当前最合适场景，"
            "并在15至20分钟后推进下一场景"
            if first_day
            else "推动客户重新开口，并逐步推进到店或支付10元预约金"
        ),
        "offer_context": S10_OUTREACH_CONTEXT,
        "activity_quote_fact": build_outreach_activity_quote_fact(
            case.get("recent_messages") or [],
            {},
        ),
        "payment_collection_gate": personalized_payment_collection_eligibility(
            case.get("customer_context") or {},
            amount=10,
        ),
        "asset_catalog": [
            {key: item.get(key) for key in ("asset_id", "type", "source_pack_name", "sop_category", "purpose")}
            for item in asset_catalog
        ],
        "recent_media_delivery": enrich_recent_outreach_media(
            case.get("recent_media_delivery") or {"urls": [], "document_ids": []},
            asset_catalog,
        ),
        "recent_sop_delivery": case.get("recent_sop_delivery") or [],
        "appointment_blocker_scene_index": build_appointment_blocker_scene_index(
            {"items": appointment_material_catalog}
        ),
        "first_day_sop_sequence": first_day_sop_sequence,
        "trigger_context": case.get("trigger_context") or {},
    }
    async with semaphore:
        workflow_artifacts: dict[str, Any] = {}
        try:
            if first_day:
                plan, workflow_artifacts = await _run_first_day_workflow(
                    client,
                    payload,
                    workflow_artifacts,
                    appointment_material_catalog,
                )
            else:
                plan = await client.chat_json(
                    [
                        {"role": "system", "content": OUTREACH_PLAN_SYSTEM_PROMPT},
                        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                    ],
                    tier="strong",
                    temperature=0.0,
                )
                plan = _normalize_outreach_plan_response(plan)
                plan = await client.chat_json(
                    [
                        {"role": "system", "content": OUTREACH_PLAN_REVIEW_SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": json.dumps(
                                {"source_snapshot": payload, "candidate_plan": plan},
                                ensure_ascii=False,
                            ),
                        },
                    ],
                    tier="strong",
                    temperature=0.0,
                )
                plan = _normalize_outreach_plan_response(plan)
                structure_error = _outreach_plan_structure_error(plan) or _outreach_plan_context_error(
                    plan,
                    activity_quote_fact=payload["activity_quote_fact"],
                    reply_wait_minutes=int(payload["conversation_activity"].get("reply_wait_minutes") or 0),
                    customer_silence_minutes=int(
                        payload["conversation_activity"].get("customer_silence_minutes") or 0
                    ),
                )
                for _repair_attempt in range(3):
                    if not structure_error:
                        break
                    plan = await client.chat_json(
                        [
                            {"role": "system", "content": OUTREACH_PLAN_SCHEMA_REPAIR_SYSTEM_PROMPT},
                            {
                                "role": "user",
                                "content": json.dumps(
                                    {
                                        "source_snapshot": payload,
                                        "candidate_plan": plan,
                                        "structure_error": structure_error,
                                        "repair_instruction": (
                                            "严格按 structure_error 修复完整 json；保留现有业务语义和客户可见文字，"
                                            "不要重新判断是否创建计划，不要解释。"
                                        ),
                                    },
                                    ensure_ascii=False,
                                ),
                            },
                        ],
                        tier="strong",
                        temperature=0.0,
                    )
                    plan = _normalize_outreach_plan_response(plan)
                    structure_error = _outreach_plan_structure_error(plan) or _outreach_plan_context_error(
                        plan,
                        activity_quote_fact=payload["activity_quote_fact"],
                        reply_wait_minutes=int(payload["conversation_activity"].get("reply_wait_minutes") or 0),
                        customer_silence_minutes=int(
                            payload["conversation_activity"].get("customer_silence_minutes") or 0
                        ),
                    )
        except Exception as exc:
            return {
                "case_id": case["id"],
                "attempt": attempt,
                "passed": False,
                "evaluation_status": "model_unavailable",
                "hard_errors": ["plan_model_unavailable"],
                "plan": {},
                "review": {},
                "workflow": workflow_artifacts,
                "scene_contract_passed": _first_day_scene_contract_passed(
                    str(case.get("id") or ""),
                    workflow_artifacts.get("scene_analysis") or {},
                ) if first_day else True,
                "model_error": f"{type(exc).__name__}: {exc}",
            }
        try:
            review_messages = [
                    {
                        "role": "system",
                        "content": FIRST_DAY_SEMANTIC_REVIEW_PROMPT if first_day else REVIEW_PROMPT,
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "case": case,
                                "offer_context": S10_OUTREACH_CONTEXT,
                                "asset_catalog": payload["asset_catalog"],
                                "plan": plan,
                                "workflow": workflow_artifacts,
                                "runtime_delivery_view": _first_day_review_delivery_view(
                                    plan,
                                    asset_catalog,
                                ) if first_day else [],
                            },
                            ensure_ascii=False,
                        ),
                    },
                ]
            review = (
                await _first_day_chat_json(client, review_messages)
                if first_day
                else await client.chat_json(
                    review_messages,
                    tier="strong",
                    temperature=0.0,
                )
            )
        except Exception as exc:
            review = {
                "review_unavailable": True,
                "review_error": f"{type(exc).__name__}: {exc}",
            }
    nested_scores = review.get("scores")
    if isinstance(nested_scores, dict):
        for key in (
            "psychology_accuracy",
            "arc_diversity",
            "asset_fit",
            "human_tone",
            "conversion_action",
            "timing_fit",
        ):
            if review.get(key) is None and nested_scores.get(key) is not None:
                review[key] = nested_scores[key]
    hard_errors = _hard_errors(
        plan,
        {str(item.get("asset_id") or "") for item in asset_catalog},
        case,
    )
    if _long_silence_timing_is_valid(plan, case):
        model_timing_fit = int(review.get("timing_fit") or 0)
        review["timing_fit_model"] = model_timing_fit
        review["timing_fit"] = max(4, model_timing_fit)
        review["timing_fit_source"] = "deterministic_long_silence_boundary"
    if first_day:
        scores = [
            int(review.get(key) or 0)
            for key in (
                "conversation_continuity",
                "scene_execution",
                "semantic_non_repeat",
                "human_tone",
                "barrier_accuracy",
            )
        ]
        review["deterministic_contract_passed"] = not hard_errors
    else:
        scores = [
            int(review.get(key) or 0)
            for key in (
                "psychology_accuracy",
                "arc_diversity",
                "asset_fit",
                "human_tone",
                "conversion_action",
                "timing_fit",
            )
        ]
    passed = (
        not hard_errors
        and not bool(review.get("review_unavailable"))
        and not bool(review.get("hard_error"))
        and all(score >= 4 for score in scores)
    )
    evaluation_status = (
        "review_unavailable"
        if bool(review.get("review_unavailable"))
        else ("passed" if passed else "failed")
    )
    return {
        "case_id": case["id"],
        "attempt": attempt,
        "passed": passed,
        "evaluation_status": evaluation_status,
        "hard_errors": hard_errors,
        "plan": plan,
        "workflow": workflow_artifacts,
        "scene_contract_passed": _first_day_scene_contract_passed(
            str(case.get("id") or ""),
            workflow_artifacts.get("scene_analysis") or {},
        ) if first_day else True,
        "review": review,
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", action="append", default=[])
    parser.add_argument(
        "--report",
        default=".tmp_runtime/outreach_personalized_model_report_20260728.json",
    )
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--case-id", action="append", default=[])
    args = parser.parse_args()

    settings = Settings()
    client = ModelClient(settings)
    fixture_paths = args.fixture or [
        "workflow_tests/fixtures/outreach_personalized_model_cases_20260728.json"
    ]
    fixture_metadata: list[dict[str, Any]] = []
    cases: list[dict[str, Any]] = []
    for fixture_path in fixture_paths:
        fixture_payload = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
        if isinstance(fixture_payload, dict):
            fixture_metadata.append(
                {
                    "fixture": fixture_path,
                    **{
                        key: value
                        for key, value in fixture_payload.items()
                        if key != "cases"
                    },
                }
            )
            fixture_cases = fixture_payload.get("cases") or []
        else:
            fixture_metadata.append({"fixture": fixture_path})
            fixture_cases = fixture_payload
        if not isinstance(fixture_cases, list):
            raise ValueError(f"fixture cases must be a list: {fixture_path}")
        cases.extend(fixture_cases)
    case_ids = [str(case.get("id") or "") for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("fixture case ids must be unique across all input files")
    selected_case_ids = {str(item).strip() for item in args.case_id if str(item).strip()}
    if selected_case_ids:
        cases = [case for case in cases if str(case.get("id") or "") in selected_case_ids]
    appointment_playbook = PrecisionQaPlaybookService(settings).load()
    appointment_material_catalog = appointment_blocker_materials(appointment_playbook)
    sop_sequence_builder = OutreachService.__new__(OutreachService)
    sop_sequence_builder.sop_reply_pack_service = SopReplyPackService(settings)
    first_day_sop_sequence = sop_sequence_builder._first_day_sop_sequence()
    asset_catalog = [
        *build_appointment_blocker_asset_catalog(appointment_playbook),
        *sop_sequence_builder._first_day_sop_asset_catalog(first_day_sop_sequence),
    ]
    semaphore = asyncio.Semaphore(max(1, args.concurrency))
    try:
        results = await asyncio.gather(
            *[
                _run_case(
                    client,
                    case,
                    attempt=attempt,
                    asset_catalog=asset_catalog,
                    appointment_material_catalog=appointment_material_catalog,
                    first_day_sop_sequence=first_day_sop_sequence,
                    semaphore=semaphore,
                )
                for case in cases
                for attempt in range(1, max(1, args.attempts) + 1)
            ]
        )
    finally:
        await client.aclose()

    passed = sum(1 for item in results if item["passed"])
    unavailable = sum(
        1
        for item in results
        if item.get("evaluation_status") in {"model_unavailable", "review_unavailable"}
    )
    evaluable = len(results) - unavailable
    semantic_pass_rate = round(passed / evaluable, 4) if evaluable else 0
    availability_rate = round(evaluable / len(results), 4) if results else 0
    first_day_results = [
        item
        for item in results
        if str((next((case for case in cases if case.get("id") == item.get("case_id")), {})
                .get("trigger_context") or {}).get("trigger_type") or "")
        == FIRST_DAY_SILENCE_TRIGGER_TYPE
    ]
    scene_accuracy = round(
        sum(bool(item.get("scene_contract_passed")) for item in first_day_results)
        / len(first_day_results),
        4,
    ) if first_day_results else 1.0
    scene_pairs_by_case: dict[str, list[tuple[Any, Any, Any]]] = {}
    for item in first_day_results:
        analysis = (item.get("workflow") or {}).get("scene_analysis") or {}
        pair = (
            bool(analysis.get("eligible")),
            analysis.get("step1_scene"),
            analysis.get("step2_scene"),
        )
        scene_pairs_by_case.setdefault(str(item.get("case_id") or ""), []).append(pair)
    scene_consistency_rate = round(
        sum(len(set(pairs)) == 1 for pairs in scene_pairs_by_case.values())
        / len(scene_pairs_by_case),
        4,
    ) if scene_pairs_by_case else 1.0
    evaluable_results = [
        item for item in results if item.get("evaluation_status") not in {"model_unavailable", "review_unavailable"}
    ]
    deterministic_contract_pass_rate = round(
        sum(not item.get("hard_errors") for item in evaluable_results) / len(evaluable_results),
        4,
    ) if evaluable_results else 0.0
    first_day_evaluable = [item for item in evaluable_results if item in first_day_results]
    scene_execution_rate = round(
        sum(int((item.get("review") or {}).get("scene_execution") or 0) >= 4 for item in first_day_evaluable)
        / len(first_day_evaluable),
        4,
    ) if first_day_evaluable else 1.0
    report = {
        "fixture": fixture_paths,
        "fixture_metadata": fixture_metadata,
        "total": len(results),
        "passed": passed,
        "pass_rate": round(passed / len(results), 4) if results else 0,
        "evaluable": evaluable,
        "unavailable": unavailable,
        "semantic_pass_rate": semantic_pass_rate,
        "availability_rate": availability_rate,
        "scene_accuracy": scene_accuracy,
        "scene_consistency_rate": scene_consistency_rate,
        "deterministic_contract_pass_rate": deterministic_contract_pass_rate,
        "scene_execution_rate": scene_execution_rate,
        "results": results,
    }
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "total",
                    "passed",
                    "pass_rate",
                    "evaluable",
                    "unavailable",
                    "semantic_pass_rate",
                    "availability_rate",
                    "scene_accuracy",
                    "scene_consistency_rate",
                    "deterministic_contract_pass_rate",
                    "scene_execution_rate",
                )
            },
            ensure_ascii=False,
        )
    )
    passed_gate = bool(
        report["pass_rate"] >= 0.9
        and deterministic_contract_pass_rate == 1.0
    )
    return 0 if passed_gate else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
