from __future__ import annotations

from typing import Any

from app.graph import planner_helpers
from app.graph.nodes.project_kb_context import case_request_lacks_specific_context
from app.graph.nodes.result_compaction import ad_price_without_explicit_project
from app.graph.nodes.reply_brief_types import ReplyBriefCallbacks
from app.graph.state import AgentState


def apply_multi_recap_context(state: AgentState, brief: dict[str, Any], callbacks: ReplyBriefCallbacks) -> None:
    content = state.get("normalized_content") or ""
    if not callbacks.is_strong_multi_recap_request(content):
        return
    brief["must_answer"].append("客户是在让你复述或整理当前已知信息；只回答本轮整理诉求，不继续推进历史预约任务。")
    brief["do_not_say"].extend(
        [
            "可约时段",
            "帮你查时间",
            "哪个时间更方便",
            "要不要约",
            "继续确认接待",
            "你看哪家更方便",
            "更多门店",
        ]
    )
    brief["follow_up"] = "整理完客户要的信息即可；如需下一步，只能轻轻说明后续想确认时间再告诉小贝。"


def apply_image_context(state: AgentState, brief: dict[str, Any], callbacks: ReplyBriefCallbacks) -> None:
    image_info = state.get("image_info") or {}
    visible = image_info.get("visible_concerns") or callbacks.known_visible_concerns_from_state(state)
    if not visible:
        return

    visible_text = "、".join(str(item) for item in visible[:4])
    visible_source = "客户图片" if callbacks.has_actual_image_context(state) else "客户历史/画像"
    brief["known_facts"].append(f"{visible_source}中可见或提到：{visible_text}")
    brief["available_facts"]["has_actual_image"] = callbacks.has_actual_image_context(state)
    brief["available_facts"]["visible_concerns"] = list(visible[:6])
    brief["must_answer"].append("先承接客户已发图片或已描述的皮肤问题；能基于可见事实推理改善方向，但不能诊断或承诺效果。")
    brief["do_not_say"].extend(["再发照片", "发张照片", "医生诊断", "确定是某疾病"])

    if any(term in visible_text for term in ["斑", "色沉", "肤色不均"]):
        brief["known_facts"].append("客户已明确关注斑点/色沉/肤色不均，不要反复追问最想改善哪一点。")
        brief["available_facts"]["project_direction"] = [
            "肤色改善类方向更偏肤色不均、暗沉、浅层色沉改善",
            "针对性色素淡化类方向更偏点状色素问题",
            "具体适合哪类要看斑点深浅、范围、肤质和预算",
        ]
        brief["must_answer"].append("如果客户问斑点能不能淡，正面回答可以往淡化方向看，再说明不能承诺完全消失。")
        brief["do_not_say"].extend(["改善空间", "明显变化", "多数顾客反馈", "第1次后", "第3次后"])


def apply_pre_visit_context(state: AgentState, brief: dict[str, Any], callbacks: ReplyBriefCallbacks) -> None:
    content = state.get("normalized_content") or ""
    if not callbacks.has_pre_visit_question(content):
        return
    brief["must_answer"].append("客户问到店前准备时，直接回答准备事项，不要转去问项目或门店。")
    brief["answer_first"].append("到店前建议素颜或淡妆，避免浓妆、假睫毛和刺激性护肤；一般皮肤咨询不需要空腹。")
    brief["do_not_say"].extend(["不要化妆", "不能化妆", "医生"])


def apply_price_context(state: AgentState, brief: dict[str, Any], callbacks: ReplyBriefCallbacks) -> None:
    content = state.get("normalized_content") or ""
    intent_set = _intent_set(state)
    tool_results = state.get("tool_results", {}) or {}
    project = callbacks.canonical_price_project(callbacks.contextual_price_project(state) or callbacks.extract_project(content))

    if not (intent_set & {"price_inquiry", "campaign_inquiry"}):
        return

    brief["must_answer"].append("本轮是价格/活动问题：有明确价格事实就直接答；没有明确价格事实时，不编价格、不拿相似项目替代。")
    if ad_price_without_explicit_project(state, project):
        _apply_ad_price_boundary(content, brief, callbacks)
        return

    rows = callbacks.filter_pricing_rows_for_project(callbacks.pricing_rows_from_kb(tool_results), project)
    if not rows:
        rows = callbacks.filter_pricing_rows_for_project(callbacks.pricing_rows(tool_results), project)

    if rows:
        row = rows[0]
        name = str(row.get("project_name") or project or "相关项目")
        brief["known_facts"].append(f"价格项目：{name}")
        for bit in callbacks.price_bits(row)[:5]:
            brief["known_facts"].append(bit)
        brief["available_facts"]["prices"] = [callbacks.price_fact_for_brief(row)]
    elif project:
        brief["known_facts"].append(f"本轮想问价格的项目：{project}；暂未查到可直接引用的明确价格。")
        brief["available_facts"]["prices"] = []
    brief["do_not_say"].extend(["到店再说", "门店会有优惠", "高配方案", "体验档位"])

    if callbacks.has_confirmed_spot_goal(state):
        brief["known_facts"].append("客户已明确关注斑点/淡斑，价格后不要再问客户想改善哪一点。")
        brief["do_not_say"].extend(["最想先改善哪一点", "效果、恢复期还是预算", "斑点本身还是整体肤色"])


def apply_project_context(state: AgentState, brief: dict[str, Any], callbacks: ReplyBriefCallbacks) -> None:
    intent_set = _intent_set(state)
    tool_results = state.get("tool_results", {}) or {}
    project_slices = callbacks.project_slices_from_tool_results(tool_results)
    if case_request_lacks_specific_context(state, known_visible_concerns_from_state=callbacks.known_visible_concerns_from_state):
        project_slices = []

    if not ({"project_inquiry", "image_inquiry", "price_inquiry", "campaign_inquiry"} & intent_set or project_slices):
        return

    if {"project_inquiry", "image_inquiry"} & intent_set:
        brief["must_answer"].append("本轮是项目/看图咨询：先基于已知需求给出改善方向，再说明边界；不要只追问客户。")
    elif intent_set & {"price_inquiry", "campaign_inquiry"}:
        brief["must_answer"].append("需求型价格问题即使没有明确价格，也要先说明可考虑方向，再说明暂未查到明确价格。")

    if not project_slices:
        return

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
            brief["answer_first"].append(
                f"客户在按需求问价格，{'、'.join(replacement_names[:2])}方向暂未查到可直接引用的明确价格。"
            )
    for item in project_slices[:2]:
        if item.get("direction"):
            brief["known_facts"].append("项目知识库可考虑方向：" + str(item["direction"])[:160])
        if item.get("reply_point"):
            brief["known_facts"].append("项目知识库回复要点：" + str(item["reply_point"])[:160])
    brief["must_answer"].append("项目咨询优先使用 project_qa 给出的方向做判断；不要逐字照抄知识库。")


def apply_case_process_ad_dispute_context(state: AgentState, brief: dict[str, Any], callbacks: ReplyBriefCallbacks) -> None:
    content = state.get("normalized_content") or ""
    intent_set = _intent_set(state)

    if "case_request" in intent_set:
        if case_request_lacks_specific_context(state, known_visible_concerns_from_state=callbacks.known_visible_concerns_from_state):
            brief["must_answer"].append("客户泛泛想看效果案例，但没有明确项目、问题方向或相关图片；不要自行假设淡斑、修护或其他方向。")
            brief["known_facts"].append("本轮只有案例/效果参考诉求，没有明确项目或皮肤问题。")
            brief["answer_first"].append("可以看同类改善参考；需要先知道客户想看哪个项目或哪类问题。")
            brief["do_not_say"].extend(["点状斑", "肤色改善", "淡斑案例", "修护方向", "案例价格"])
        else:
            brief["must_answer"].append("客户要效果案例或前后对比时，承接可以看同类改善参考；没有真实图片链接时不要编造案例图。")
            brief["do_not_say"].extend(["案例价格", "哪家门店可以看案例"])

    if "project_process" in intent_set:
        brief["must_answer"].append("客户问操作流程或大概要多久，先给通用流程和时长范围；不同项目配置会有差异。")
        brief["known_facts"].append("流程类问题应覆盖：到店评估/清洁检测/方案确认/操作/护理提醒/整体时长。")
        brief["do_not_say"].extend(["到店再说", "到店检测后才能知道流程"])

    if "ad_price_check" in intent_set:
        _apply_ad_price_boundary(content, brief, callbacks)

    if intent_set & {"complaint_refund", "human_request"} or planner_helpers._has_fee_or_refund_dispute(content):
        brief["must_answer"].append("本轮是费用、退款、投诉邻近或真实记录核对诉求；先承接客户争议点，不要答成门店地址或项目介绍。")
        brief["must_answer"].append("没有真实订单/付款/退款接口结果时，不能说没有记录、已退款、可退款或不能退款。")
        brief["known_facts"].append(f"客户当前争议/处理诉求：{content}")
        brief["available_facts"]["needs_professional_assist"] = True
        brief["do_not_say"].extend(["没看到订单", "没有费用记录", "已经退款", "可以退款", "不能退款", "门店地址"])
        brief["follow_up"] = "说明会同步专业同事/门店核对；如需补信息，只问付款凭证、到店门店、项目记录里最关键的一项。"


def apply_trust_and_misc_context(state: AgentState, brief: dict[str, Any], callbacks: ReplyBriefCallbacks) -> None:
    content = state.get("normalized_content") or ""
    intent_set = _intent_set(state)
    if "trust_issue" in intent_set:
        if planner_helpers._is_soft_fee_concern(content):
            brief["must_answer"].append("本轮是收费透明顾虑，只回答收费如何提前确认和逐项核对；不要转去问城市、门店或预约。")
            brief["known_facts"].append("没有可引用资质、设备或服务追溯事实时，不要主动编造这些背书。")
            brief["do_not_say"].extend(["放心", "绝不会额外加收", "没有隐形消费", "所有门店", "资质认证", "近期可安排"])
        else:
            brief["must_answer"].append("本轮是信任顾虑，先认可客户谨慎，再基于可用资质/背书事实解释；没有资料时不要编造。")
    if "competitor_compare" in intent_set:
        brief["must_answer"].append("本轮是竞品/比价：认可对比，拆项目、产品、剂量、部位、次数、售后等维度；不贬低竞品、不跟价。")
    if "after_sales" in intent_set:
        brief["must_answer"].append("本轮是售后/操作后反馈：先安抚并确认项目、操作时间、症状；有风险信号时让专业人士协助确认。")


def apply_price_recap_and_memory_context(state: AgentState, brief: dict[str, Any], callbacks: ReplyBriefCallbacks) -> None:
    content = state.get("normalized_content") or ""
    if callbacks.asks_price_recap(content):
        brief["must_answer"].append("客户要你把价格再顺一下时，优先复述已知价格事实；没有明确价格就直接说暂未查到。")
        brief["answer_first"].append(callbacks.price_summary_message(state))

    memory_context = callbacks.memory_context_sentence(state)
    if memory_context:
        brief["known_facts"].append(memory_context)


def suggested_followup_for_brief(state: AgentState, callbacks: ReplyBriefCallbacks) -> str:
    content = state.get("normalized_content") or ""
    intent_set = _intent_set(state)
    if intent_set & {"appointment_intent", "appointment_change", "appointment_cancel"}:
        return "围绕预约诉求确认门店、日期或处理动作。"
    if "store_inquiry" in intent_set:
        return "如果有多家门店，基于客户位置直接给推荐；不要只让客户自己选。"
    if "price_inquiry" in intent_set:
        if callbacks.has_price_objection(content):
            return "承接预算压力，不承诺降价，给已查价格事实或明确说明未查到。"
        if callbacks.has_confirmed_spot_goal(state):
            return "客户已明确关注斑点/淡斑，回答价格后不要再问最想改善哪一点。"
        return "回答价格后，只问一个能推进判断的问题。"
    if "ad_price_check" in intent_set:
        return "核对客户看到的广告价、预约金、尾款和是否另收费，不确认未核实广告存在。"
    if "case_request" in intent_set:
        return "围绕客户要看的案例/效果参考承接，缺少真实素材时说明可看同类改善参考。"
    if "project_process" in intent_set:
        return "直接说明流程和大致时长，必要时补一句不同配置会有差异。"
    if "project_inquiry" in intent_set or "image_inquiry" in intent_set:
        if callbacks.has_confirmed_spot_goal(state):
            return "客户已明确关注斑点/淡斑，直接说明淡化方向和限制，不要再问最想改善哪一点。"
        if callbacks.project_slices_from_tool_results(state.get("tool_results", {}) or {}) or callbacks.known_visible_concerns_from_state(state):
            return "先按已知需求和知识库方向给判断；只有缺关键因素时最多问一个问题。"
        return "先给常见方向选项，再让客户选最接近的困扰。"
    if "trust_issue" in intent_set:
        return "认可谨慎，围绕资质、产品来源和服务保障解释。"
    return "轻量承接客户当前问题。"


def _apply_ad_price_boundary(content: str, brief: dict[str, Any], callbacks: ReplyBriefCallbacks) -> None:
    digits = callbacks.extract_price_digits(content)
    if digits:
        brief["known_facts"].append(f"客户看到的广告/活动价格数字：{'、'.join(digits[:3])}")
        brief["available_facts"]["customer_seen_price"] = digits[:3]
    brief["known_facts"].append("客户没有提供明确广告项目或广告截图时，不能拿相似知识库命中的价格代替报价。")
    brief["answer_first"].append("可以先按客户看到的金额核对收费口径，但当前不能确认是不是同一条广告或同一个活动。")
    brief["must_answer"].append("解释广告价需要核对对应项目、包含项、预约金/尾款和是否另收费；不要确认广告价真实存在。")
    brief["do_not_say"].extend(["这个价格确实有", "目前有这个活动", "可以按这个价格做", "肯定没有其他收费", "绝对没有隐形消费"])


def _intent_set(state: AgentState) -> set[str]:
    return {str(item.get("intent") or "") for item in state.get("intents", []) if isinstance(item, dict)}

