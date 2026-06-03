from __future__ import annotations

from typing import Any

from app.graph import planner_helpers
from app.graph.nodes.project_kb_context import case_request_lacks_specific_context
from app.graph.nodes.result_compaction import ad_price_without_explicit_project
from app.graph.nodes.reply_brief_types import ReplyBriefCallbacks
from app.graph.state import AgentState


def apply_multi_recap_context(state: AgentState, brief: dict[str, Any], callbacks: ReplyBriefCallbacks) -> None:
    content = state.get("normalized_content") or ""
    if callbacks.is_strong_multi_recap_request(content):
        brief["must_answer"].append("本轮客户是在让你复述/整理到店准备、门店地址或价格信息；只回答这些当前问题，不要继续历史预约任务。")
        brief["do_not_say"].extend(
            [
                "可约时段",
                "可预约时间",
                "帮你确认时间",
                "你看哪个时间更方便",
                "哪个时间更方便",
                "要不要约",
                "继续确认接待",
                "帮你查时间",
                "其他门店",
                "更多门店",
                "一并发你",
                "你看哪家更方便",
                "哪家更方便",
            ]
        )
        brief["follow_up"] = "整理完客户要的信息即可；如需下一步，只能轻轻说后续想确认时间再告诉小贝。"


def apply_image_context(state: AgentState, brief: dict[str, Any], callbacks: ReplyBriefCallbacks) -> None:
    content = state.get("normalized_content") or ""
    image_info = state.get("image_info") or {}
    visible = image_info.get("visible_concerns") or callbacks.known_visible_concerns_from_state(state)
    if visible:
        visible_text = "、".join(str(item) for item in visible[:4])
        visible_source = "客户图片" if callbacks.has_actual_image_context(state) else "客户历史/画像"
        brief["known_facts"].append(f"{visible_source}中可见或提到：{visible_text}")
        brief["available_facts"]["has_actual_image"] = callbacks.has_actual_image_context(state)
        brief["must_answer"].append("先承接客户已发图片或已描述的皮肤问题，直接说明可改善方向和限制；只有确实有图片时才说“从图片看”。")
        brief["available_facts"]["visible_concerns"] = list(visible[:6])
        brief["do_not_say"].extend(["再发照片", "发张照片", "正脸自然光照片"])
        if any(term in visible_text for term in ["斑", "色沉", "肤色不均"]):
            brief["known_facts"].append("客户当前已明确关注斑点/色沉/肤色不均，不要再问“最想先改善哪一点”。")
            brief["must_answer"].append("如果客户问斑点能不能淡，要正面说“可以改善/可以往淡化方向看”，再补充不能承诺完全消失，具体看斑点深浅和范围。")
            brief["available_facts"]["project_direction"] = [
                "肤色改善类方向更偏肤色不均、暗沉、浅层色沉改善",
                "针对性色素淡化类方向更偏点状色素问题",
                "具体适合哪类需要结合斑的深浅和范围，不能仅凭照片诊断斑型",
            ]
            brief["do_not_say"].extend([
                "你最想先改善哪一点",
                "最想先改善哪一点",
                "你主要想改善哪一点",
                "改善空间",
                "有一定改善空间",
                "效果节奏",
                "多数顾客反馈",
                "变化比较明显",
            ])
            if not any(term in visible_text for term in ["泛红", "敏感", "屏障", "刺痛", "干痒", "红血丝"]):
                brief["known_facts"].append("当前没有客户明确提到泛红、敏感或屏障受损，不要引入泛红/修护方向。")
                brief["do_not_say"].extend(["泛红", "修护", "屏障重建", "敏感修护", "修护方向"])


def apply_pre_visit_context(state: AgentState, brief: dict[str, Any], callbacks: ReplyBriefCallbacks) -> None:
    content = state.get("normalized_content") or ""
    if callbacks.has_pre_visit_question(content):
        brief["must_answer"].append("客户问到店前准备或能不能化妆时，要直接回答准备事项，不要转去问项目/门店。")
        brief["answer_first"].append("到店前准备事实：建议素颜或淡妆，避免浓妆、假睫毛和刺激性护肤；一般皮肤咨询不需要空腹。")
        brief["do_not_say"].extend(["不要化妆", "不能化妆", "不化妆", "医生评估", "医生"])


def apply_price_context(state: AgentState, brief: dict[str, Any], callbacks: ReplyBriefCallbacks) -> None:
    content = state.get("normalized_content") or ""
    intent_set = {item.get("intent") for item in state.get("intents", [])}
    tool_results = state.get("tool_results", {}) or {}
    project = callbacks.canonical_price_project(callbacks.contextual_price_project(state) or callbacks.extract_project(content))
    if intent_set & {"price_inquiry", "campaign_inquiry"}:
        brief["must_answer"].append("本轮是价格/活动问题，必须优先用已查到的价格事实直接回答；没有查到明确价格时才说明未查到。")
        if ad_price_without_explicit_project(state, project):
            rows = []
            digits = callbacks.extract_price_digits(content)
            if digits:
                brief["known_facts"].append(f"客户看到的广告/活动价格数字：{'、'.join(digits[:3])}")
                brief["available_facts"]["customer_seen_price"] = digits[:3]
            brief["known_facts"].append("客户没有提供明确广告项目或广告截图，不能拿知识库相似命中的商品价格代替报价。")
            brief["answer_first"].append("可以先按客户看到的金额核对收费口径，但当前不能确认是不是同一条广告或同一个活动。")
            brief["must_answer"].append("先解释广告价需要核对对应项目、包含项、尾款和是否另收费；不要引用未确认项目名或不相关价格，也不要确认广告价/活动价真实存在。")
            brief["do_not_say"].extend([
                "确实有在部分活动里出现过",
                "这个价格确实有",
                "这个活动确实有",
                "我们确实有这个价",
                "目前有这个活动",
                "可以按这个价格做",
            ])
        else:
            rows = callbacks.filter_pricing_rows_for_project(callbacks.pricing_rows_from_kb(tool_results), project) or callbacks.filter_pricing_rows_for_project(
                callbacks.pricing_rows(tool_results), project
            )
        if rows:
            row = rows[0]
            name = str(row.get("project_name") or project or "相关项目")
            brief["known_facts"].append(f"价格项目：{name}")
            for bit in callbacks.price_bits(row)[:5]:
                brief["known_facts"].append(bit)
            brief["available_facts"]["prices"] = [callbacks.price_fact_for_brief(row)]
        elif project:
            brief["known_facts"].append(f"本轮想问价格的项目：{project}；未查到明确可引用价格时不能拿其他项目代替。")
            brief["available_facts"]["prices"] = []
        brief["do_not_say"].extend(["价格要看具体配置所以不能说", "到店再说", "门店会有优惠"])
        if callbacks.has_confirmed_spot_goal(state):
            brief["known_facts"].append("客户本轮价格问题承接的是淡斑/斑点改善，不要再追问客户想改善什么。")
            brief["do_not_say"].extend(
                [
                    "你最想先改善哪一点",
                    "最想先改善哪一点",
                    "你主要想改善哪一点",
                    "更在意效果、恢复期还是预算",
                    "更关注效果、恢复期还是预算",
                    "更关注哪方面改善",
                    "斑点本身还是整体肤色",
                ]
            )
            brief["follow_up"] = "价格后只需说明按图片/斑点情况先核对对应方向；如果没有明确价格，就说暂未查到该方向明确价格并建议按针对性色素淡化方向核价，不再问改善重点或效果/恢复期/预算三选一。"


def apply_project_context(state: AgentState, brief: dict[str, Any], callbacks: ReplyBriefCallbacks) -> None:
    intent_set = {item.get("intent") for item in state.get("intents", [])}
    tool_results = state.get("tool_results", {}) or {}
    project_slices = callbacks.project_slices_from_tool_results(tool_results)
    if case_request_lacks_specific_context(state, known_visible_concerns_from_state=callbacks.known_visible_concerns_from_state):
        project_slices = []
    if "project_inquiry" in intent_set or "image_inquiry" in intent_set or project_slices:
        if "project_inquiry" in intent_set or "image_inquiry" in intent_set:
            brief["must_answer"].append("本轮是项目/看图咨询，先回答能否改善、适合的大方向和不能直接判断的边界。")
        elif intent_set & {"price_inquiry", "campaign_inquiry"}:
            brief["must_answer"].append("本轮是需求型价格问题，若没有明确价格，也要先用project_qa说明可考虑方向，再说明暂未查到明确价格。")
        if project_slices:
            brief["available_facts"]["project_qa"] = [
                {
                    "替换词名称": item.get("replacement_name"),
                    "可考虑方向": item.get("direction"),
                    "回复要点": item.get("reply_point"),
                    "可说话术参考": item.get("say"),
                }
                for item in project_slices[:3]
            ]
            replacement_candidates: list[str] = []
            for item in project_slices:
                replacement_candidates.extend(callbacks.project_direction_name_candidates(str(item.get("replacement_name") or "")))
            replacement_names = callbacks.dedupe_strings(replacement_candidates)
            if replacement_names:
                brief["known_facts"].append("项目知识库建议使用这些合规方向表达：" + "、".join(replacement_names[:3]))
                if intent_set & {"price_inquiry", "campaign_inquiry"} and not brief["available_facts"].get("prices"):
                    direction_text = "、".join(replacement_names[:2])
                    brief["answer_first"].append(
                        f"客户在按需求问价格；{direction_text}方向暂未查到可直接引用的明确价格，不能拿不相关项目代替报价。"
                    )
                    brief["must_answer"].append(
                        f"先说明更偏{direction_text}方向，再明确说暂未查到该方向可直接引用的价格；不要只说需要看配置。"
                    )
            for item in project_slices[:2]:
                if item.get("direction"):
                    brief["known_facts"].append("项目知识库可考虑方向：" + str(item["direction"])[:160])
                if item.get("reply_point"):
                    brief["known_facts"].append("项目知识库回复要点：" + str(item["reply_point"])[:160])
            brief["must_answer"].append("项目咨询要优先使用project_qa给出的方向做判断；不要只追问客户，也不要逐字照抄知识库。")


def apply_case_process_ad_dispute_context(state: AgentState, brief: dict[str, Any], callbacks: ReplyBriefCallbacks) -> None:
    content = state.get("normalized_content") or ""
    intent_set = {item.get("intent") for item in state.get("intents", [])}
    if "case_request" in intent_set:
        if case_request_lacks_specific_context(state, known_visible_concerns_from_state=callbacks.known_visible_concerns_from_state):
            brief["must_answer"].append("客户泛泛想看做完效果，但没有说项目、问题方向或上传相关图片；不要自行假设斑点、肤色改善、修护或其他方向。")
            brief["known_facts"].append("本轮只有案例/效果参考诉求，没有明确项目或皮肤问题。")
            brief["do_not_say"].extend([
                "点状斑",
                "肤色改善",
                "淡斑案例",
                "修护方向",
                "术后修护",
                "斑点深浅",
                "肤质和恢复情况",
            ])
            brief["answer_first"].append("可以看同类改善参考；你想看哪个项目，或者哪类皮肤问题的效果参考？")
            brief["follow_up"] = "先承接可以看同类参考；只问一个最必要问题：想看哪个项目或哪类问题的效果参考。"
        else:
            brief["must_answer"].append("客户在要效果案例或前后对比，先承接可以按项目/问题方向看同类改善参考；如果工具没有真实图片链接，不要编造案例图。")
            brief["known_facts"].append("案例诉求不要反复改问客户是要门店、价格还是案例；已有祛斑/淡斑/门店线索时直接围绕同类案例承接。")
            brief["do_not_say"].extend(["您是想了解哪家门店可以看案例", "还是想了解相关项目的案例价格", "案例价格"])
            brief["follow_up"] = "如必须追问，只问一个最必要槽位，例如想看祛斑同类改善还是到店案例。"

    if "project_process" in intent_set:
        brief["must_answer"].append("客户询问项目操作流程或大概要多久，先给通用流程和时长范围；不同项目会有差异时简短说明。")
        brief["known_facts"].append("流程类问题要回答到店评估/清洁/检测/方案确认/操作/术后护理提醒/整体时长，不要只说到店再确认。")
        brief["do_not_say"].extend(["到店再说", "需要到店检测后才能知道流程"])

    if "ad_price_check" in intent_set:
        digits = callbacks.extract_price_digits(content)
        if digits:
            brief["known_facts"].append(f"客户看到的广告/活动价格数字：{'、'.join(digits[:3])}")
            brief["available_facts"]["customer_seen_price"] = digits[:3]
        if ad_price_without_explicit_project(state, callbacks.canonical_price_project(callbacks.contextual_price_project(state) or callbacks.extract_project(content))):
            brief["answer_first"].append("可以先按客户看到的金额核对收费口径，但当前不能确认是不是同一条广告或同一个活动。")
            brief["must_answer"].append("客户没有提供明确广告项目或广告截图时，只能说明需要核对广告对应项目、包含项、预约金/尾款和是否另收费；不能确认该广告价存在。")
            brief["do_not_say"].extend([
                "确实有在部分活动里出现过",
                "这个价格确实有",
                "这个活动确实有",
                "我们确实有这个价",
                "目前有这个活动",
                "可以按这个价格做",
            ])
        else:
            brief["must_answer"].append("客户在核对广告价、预约金、尾款或是否另收费，必须先承接客户看到的价格数字，再解释当前可确认口径；不能直接换成另一个价格不解释。")
        brief["do_not_say"].extend(["绝对没有隐形消费", "肯定没有其他收费", "放心没有其他收费"])

    if intent_set & {"complaint_refund", "human_request"} or planner_helpers._has_fee_or_refund_dispute(content):
        brief["must_answer"].append("本轮是费用、退款、投诉邻近或真实记录核对诉求；先承接客户说法不一致/想退款/需要核对的核心问题，不能答成门店地址或项目介绍。")
        brief["must_answer"].append("没有真实订单、付款、退款接口结果时，不能说“没看到订单/没看到费用记录/没有相关记录”；只能说明需要结合付款记录、项目记录和门店沟通记录核对。")
        brief["known_facts"].append(f"客户当前争议/处理诉求：{content}")
        brief["available_facts"]["needs_professional_assist"] = True
        brief["do_not_say"].extend([
            "没看到订单",
            "没有看到订单",
            "没看到费用记录",
            "没有看到费用记录",
            "没有相关记录",
            "已经退款",
            "可以退款",
            "不能退款",
            "门店地址",
            "营业时间",
        ])
        brief["follow_up"] = "先表达会帮客户同步专业同事/门店核对；如需补信息，只问付款凭证或到店门店/项目记录其中一个最关键项。"


def apply_trust_and_misc_context(state: AgentState, brief: dict[str, Any], callbacks: ReplyBriefCallbacks) -> None:
    content = state.get("normalized_content") or ""
    intent_set = {item.get("intent") for item in state.get("intents", [])}
    if "trust_issue" in intent_set:
        if planner_helpers._is_soft_fee_concern(content):
            brief["must_answer"].append("本轮是收费透明顾虑，只回答收费如何提前确认和逐项核对；不要转去问城市、门店、体验档位或项目方向。")
            brief["known_facts"].append("当前没有可直接引用的资质、设备、老师认证或服务追溯事实时，不能主动说这些背书。")
            brief["do_not_say"].extend(
                [
                    "放心",
                    "最该放心",
                    "不会乱收费",
                    "绝不会额外加收",
                    "没有隐形消费",
                    "明码标价",
                    "所有门店",
                    "所有淡斑类项目",
                    "正规备案",
                    "正规渠道",
                    "备案产品",
                    "备案设备",
                    "资质认证",
                    "统一培训",
                    "收费可复盘",
                    "服务可追溯",
                    "所在城市",
                    "体验档位",
                    "活动档位",
                    "近期可安排",
                ]
            )
            brief["follow_up"] = "回答完收费确认方式即可；如必须补一句，只说有疑问可以逐项核对，不要问城市或推进门店。"
        else:
            brief["must_answer"].append("本轮是信任顾虑，先认可客户谨慎，再基于可用资质/背书事实解释；没有资料时不要编造资质。")
    if "competitor_compare" in intent_set:
        brief["must_answer"].append("本轮是竞品/比价，先认可对比，再拆项目、产品、剂量、部位、次数、售后等维度；不要贬低竞品或跟价。")
    if "after_sales" in intent_set:
        brief["must_answer"].append("本轮是售后/术后反馈，先安抚并确认项目、操作时间、症状；有风险信号时建议让专业人士协助确认。")


def apply_price_recap_and_memory_context(state: AgentState, brief: dict[str, Any], callbacks: ReplyBriefCallbacks) -> None:
    content = state.get("normalized_content") or ""
    if callbacks.asks_price_recap(content):
        brief["must_answer"].append("客户要你把价格再顺一下时，优先复述已知价格事实；没有明确价格时直接说没查到该项目明确价格，不要绕回重复评估。")
        brief["answer_first"].append(callbacks.price_summary_message(state))

    memory_context = callbacks.memory_context_sentence(state)
    if memory_context:
        brief["known_facts"].append(memory_context)


def suggested_followup_for_brief(state: AgentState, callbacks: ReplyBriefCallbacks) -> str:
    content = state.get("normalized_content") or ""
    intent_set = {item.get("intent") for item in state.get("intents", [])}
    if intent_set & {"appointment_intent", "appointment_change", "appointment_cancel"}:
        return "围绕预约诉求确认门店、日期或处理动作。"
    if "store_inquiry" in intent_set:
        return "如有多家门店，让客户选更方便的一家。"
    if "price_inquiry" in intent_set:
        if callbacks.has_price_objection(content):
            return "承接预算压力，不承诺降价，给已知价格档位。"
        if callbacks.has_confirmed_spot_goal(state):
            return "客户已明确关注斑点/淡斑，回答价格后不要再问最想改善哪一点。"
        return "回答价格后，可问客户更在意效果、恢复期还是预算。"
    if "ad_price_check" in intent_set:
        return "先核对客户看到的广告价/预约金/尾款口径，再说明已知收费项和需要确认的项目配置。"
    if "case_request" in intent_set:
        return "围绕客户要看的案例/效果参考承接，缺少真实案例素材时说明可看同类改善参考。"
    if "project_process" in intent_set:
        return "直接说明项目流程和大致时长，必要时补一句不同项目配置会有差异。"
    if "project_inquiry" in intent_set or "image_inquiry" in intent_set:
        if callbacks.has_confirmed_spot_goal(state):
            return "客户已明确关注斑点/淡斑，直接说明淡斑改善方向和限制，不要再问最想改善哪一点。"
        project_slices = callbacks.project_slices_from_tool_results(state.get("tool_results", {}) or {})
        if project_slices or callbacks.known_visible_concerns_from_state(state):
            return "先按已知需求和知识库方向给出判断；只有缺少关键因素时，最多问一个会改变方案的问题。"
        return "先给出常见项目方向选项，再让客户选最接近的困扰；不要只问项目名。"
    if "trust_issue" in intent_set:
        return "认可谨慎，围绕资质、产品来源和服务保障解释。"
    return "轻量承接客户当前问题。"
