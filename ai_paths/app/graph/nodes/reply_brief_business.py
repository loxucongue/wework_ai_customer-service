from __future__ import annotations

import html
import re
from typing import Any

from app.graph import planner_helpers
from app.graph.nodes.memory_usage_policy import should_suppress_profile_memory_for_reply
from app.graph.nodes.intent_signals import is_broad_ad_intro
from app.graph.nodes.price_question_frames import (
    build_price_question_frame,
    extract_customer_seen_price_digits,
    is_case_times_followup,
    is_effect_dissatisfaction_followup,
)
from app.graph.nodes.project_kb_context import case_request_lacks_specific_context
from app.graph.nodes.result_compaction import ad_price_without_explicit_project
from app.graph.nodes.reply_brief_types import ReplyBriefCallbacks
from app.graph.planner_dispute_signals import is_deposit_rule_question
from app.graph.planner_general_signals import is_low_information_closing
from app.graph.state import AgentState


def _generic_case_request(state: AgentState, callbacks: ReplyBriefCallbacks) -> bool:
    return case_request_lacks_specific_context(state)


def apply_direct_reply_context(state: AgentState, brief: dict[str, Any], callbacks: ReplyBriefCallbacks) -> None:
    content = state.get("normalized_content") or ""
    if is_deposit_rule_question(content):
        brief["answer_first"].append("按活动口径，到店先了解，满意再做；如果到店了解后不满意不做，10元预约登记/活动参与金一般可退。")
        brief["must_answer"].append("客户在问定金、预约金或10元规则，先给公司规则口径；不要只说需要核对，也不要否定定金规则或切到广告价、门店查询、可约时间。")
        brief["known_facts"].append("当前业务规则中，10元通常属于预约登记或活动参与资格确认口径，不等于已预约成功或已锁位；已付款后的退款争议仍需结合付款和活动记录核对。")
        brief["do_not_say"].extend(["没有需要支付定金", "目前没有定金", "不需要定金", "锁位", "锁定", "预留名额", "已预约成功"])
        brief["follow_up"] = "如果客户想继续预约，再确认门店、日期、时间和手机号；否则不用主动推进。"
    if is_low_information_closing(content):
        brief["must_answer"].append("客户当前只是感谢、收到、暂缓或轻量收尾，只需自然收住，不继承历史预约、门店或项目任务。")
        brief["answer_first"].append("自然回应客户的感谢或收尾，有需要时再来找小贝即可。")
        brief["do_not_say"].extend(["最近有在关注什么", "想了解什么项目", "哪天方便", "哪个门店", "查一下可约时间", "继续预约"])
        brief["follow_up"] = "不要追加新的问题。"


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
    if _generic_case_request(state, callbacks) or should_suppress_profile_memory_for_reply(state):
        return
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
    digits = extract_customer_seen_price_digits(content) or callbacks.extract_price_digits(content)
    frame = build_price_question_frame(content, digits)

    frame_only = bool(frame and frame.name == "times_question" and not (intent_set & {"price_inquiry", "campaign_inquiry"}))
    if not (intent_set & {"price_inquiry", "campaign_inquiry"}) and not frame_only:
        return

    if not frame_only:
        brief["must_answer"].append("本轮是价格/活动问题：有明确价格事实就直接答；没有明确价格事实时，不编价格、不拿相似项目替代。")
    if frame:
        brief["answer_first"].append(frame.answer_first)
        brief["must_answer"].append(frame.must_answer)
        brief["known_facts"].append(frame.reply_point)
        brief["do_not_say"].extend(list(frame.do_not_say))
        if frame.name in {"deposit_question", "single_fee", "confirm_price", "price_conflict", "course_payment"}:
            brief["must_answer"].append("这是窄价格口径追问，本轮只回答收费口径本身；不要主动推进预约、门店、时间、姓名电话。")
            brief["do_not_say"].extend(
                [
                    "您想去哪家门店",
                    "你想去哪家门店",
                    "哪家门店",
                    "哪个门店",
                    "什么时间方便",
                    "哪天方便",
                    "先登记预约",
                    "咱们先登记预约",
                    "到店后老师会按",
                ]
            )
        if not brief.get("follow_up"):
            brief["follow_up"] = frame.follow_up
    if frame_only:
        brief["do_not_say"].extend(["你想改善哪方面", "想改善什么", "可以先了解一下你想"])
        return
    if ad_price_without_explicit_project(state, project):
        _apply_ad_price_boundary(content, brief, callbacks, frame)
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
    if "campaign_inquiry" in intent_set and any(term in content for term in ["券", "优惠券", "活动券", "代金券"]):
        brief["must_answer"].append("客户当前在问券或活动是否可用，先回答有没有查到对应活动，再说明需要核对的券详情；不要切回皮肤分析。")
        brief["do_not_say"].extend(["斑点出现多久", "晒后明显", "想改善哪一点", "更适合怎么推进"])
        brief["follow_up"] = "如需继续确认，只问券对应的项目、广告截图或使用条件其中一项。"

    if callbacks.has_confirmed_spot_goal(state):
        brief["known_facts"].append("客户已明确关注斑点/淡斑，价格后不要再问客户想改善哪一点。")
        brief["do_not_say"].extend(["最想先改善哪一点", "效果、恢复期还是预算", "斑点本身还是整体肤色"])


def apply_project_context(state: AgentState, brief: dict[str, Any], callbacks: ReplyBriefCallbacks) -> None:
    content = state.get("normalized_content") or ""
    intent_set = _intent_set(state)
    tool_results = state.get("tool_results", {}) or {}
    project_slices = callbacks.project_slices_from_tool_results(tool_results)
    broad_ad_intro = is_broad_ad_intro(content)
    if _generic_case_request(state, callbacks) or should_suppress_profile_memory_for_reply(state):
        project_slices = []

    if not ({"project_inquiry", "image_inquiry", "price_inquiry", "campaign_inquiry"} & intent_set or project_slices):
        return

    if {"project_inquiry", "image_inquiry"} & intent_set:
        brief["must_answer"].append("本轮是项目/看图咨询：先基于已知需求给出改善方向，再说明边界；不要只追问客户。")
        if broad_ad_intro:
            brief["answer_first"].append("可以的，你这轮主要就是想先把祛斑方向、价格口径、效果参考和到店安排顺清楚。")
            brief["must_answer"].append("这是广告引流的初次咨询，先短承接并主动给信息：先说祛斑先看方向，再说明价格按活动口径核，效果按同类改善参考看，到店安排后面可以接着确认。")
            brief["do_not_say"].extend(
                [
                    "如果主要是点状斑",
                    "点状斑点",
                    "点状斑为主",
                    "片状色沉",
                    "肤色不均",
                    "肤色不均深浅范围",
                    "你这个更偏",
                    "先稳肤、再淡化、后巩固",
                    "温和型还是进阶型",
                    "斑点深浅、范围",
                    "完整方案",
                ]
            )
            brief["follow_up"] = "只在必要时问客户这一轮更想先听价格、效果参考还是到店安排，其余先不用追问。"
            return
        if _wants_direct_direction_answer(content):
            brief["must_answer"].append("客户明确不想继续被追问，这一轮必须直接给优先改善方向和一句理由，不要再追问。")
            brief["do_not_say"].extend(
                [
                    "方便我帮你更准一点",
                    "想改善哪一点",
                    "更关注哪方面",
                    "你再说一下",
                    "再补充",
                    "你是更在意",
                    "发我看看",
                ]
            )
            brief["follow_up"] = "这一轮不要问问题，直接给结论和一句边界。"
        if _is_initial_broad_project_intro(content):
            brief["answer_first"].append("可以，祛斑这类我先帮你按方向、价格口径和到店安排这三块顺一下：先看适合的改善方向，再核活动价和到店安排。")
            brief["must_answer"].append("客户只是初步了解该方向，按话术合集标准短承接即可：一句说明大方向，再问一个关键问题；不要展开完整方案、节奏、价格或预约，也不要擅自细分斑型。")
            brief["do_not_say"].extend(["如果主要是点状斑", "点状斑为主", "片状色沉", "你这个更偏", "先稳肤、再淡化、后巩固", "避免反复或加深", "温和型还是进阶型", "斑点深浅、范围", "完整方案", "到店安排"])
            brief["follow_up"] = "只在必要时问客户这轮更想先看价格、效果参考还是到店安排，不继续分析细分斑型。"
            return
        if callbacks.has_confirmed_spot_goal(state):
            brief["must_answer"].append("客户已明确点状斑/色沉方向，直接给优先方向和一句边界；不要再问温和型还是进阶型，也不要问客户想改善哪一点。")
            brief["do_not_say"].extend(["温和型还是进阶型", "更适合当前状态", "最想改善哪一点", "更关注哪方面", "肤质、补水、抗衰", "轮廓提升"])
        if _asks_effect_timeline(content):
            brief["answer_first"].append("这类淡化改善通常不是一次就把结果定完，会按阶段观察；具体节奏要看实际情况来调。")
            brief["must_answer"].append("客户问几次或多久看到变化，先正面回答改善周期边界，不要改问肤质、补水、抗衰或轮廓。")
            brief["available_facts"]["effect_timeline_exact"] = False
            brief["do_not_say"].extend(["你更关注哪方面", "肤质、补水、抗衰", "轮廓提升", "明显变化", "1-3次", "2-4次", "3-5次", "一次就"])
            if not callbacks.has_confirmed_spot_goal(state):
                brief["do_not_say"].extend(["点状斑", "点状斑点", "片状色沉", "肤色不均", "斑点深浅、范围"])
    elif intent_set & {"price_inquiry", "campaign_inquiry"}:
        brief["must_answer"].append("需求型价格问题即使没有明确价格，也要先说明可考虑方向，再说明暂未查到明确价格。")

    if broad_ad_intro or not project_slices:
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
            if any(term in content for term in ["预算", "别太高", "不太高", "太贵", "贵", "便宜"]):
                brief["known_facts"].append("客户有预算顾虑；无明确价格事实时，可以说明先核对基础单次或当前活动可用配置，不先推组合配置。")
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
        case_items = _case_items_from_tool_results(state)
        if is_case_times_followup(content):
            brief["answer_first"].append("这类效果图一般更适合作为阶段性改善参考，单张图片通常看不出准确做了几次，不能把次数硬说死。")
            brief["must_answer"].append("客户追问图片上的客户做了多少次时，先回答‘通常是阶段性改善参考，具体次数要看原始案例记录’，不要重新泛问客户想看什么项目。")
            brief["known_facts"].append("案例次数追问优先解释案例图只能代表同类改善趋势，不一定能直接看出单次还是多次。")
            brief["do_not_say"].extend(["想看哪个项目", "哪类问题", "淡斑方向", "肤色改善方向"])
        if _generic_case_request(state, callbacks):
            brief["must_answer"].append("客户泛泛想看效果案例，但没有明确项目、问题方向或相关图片；不要自行假设淡斑、修护或其他方向。")
            brief["known_facts"].append("本轮只有案例/效果参考诉求，没有明确项目或皮肤问题。")
            if not is_case_times_followup(content):
                brief["answer_first"].append("可以先看同类改善参考；如果客户想看得更准，再按项目或问题方向细分。")
            brief["do_not_say"].extend(["点状斑", "斑点", "肤色不均", "色沉", "肤色改善", "淡斑案例", "修护方向", "案例价格"])
            if not is_case_times_followup(content):
                brief["follow_up"] = "只在必要时问客户想看哪个项目或哪类问题的效果参考，不继续分析皮肤方向。"
        else:
            brief["must_answer"].append("客户要效果案例或前后对比时，承接可以看同类改善参考；没有真实图片链接时不要编造案例图。")
            if case_items:
                brief["known_facts"].append(f"案例素材库已命中{len(case_items)}条同类改善参考资料；只能作为参考，不能承诺同样变化。")
                image_url = _case_image_url(case_items)
                if image_url:
                    brief["answer_first"].append("已有同类改善参考图，可以先发客户看一张；文字说明要简短，不能再反问要不要发图。")
                    brief["available_facts"]["case_asset_image_url"] = image_url
                    brief["do_not_say"].extend(["需要我发你", "要不要发", "我可以发", "帮你找图", "发你几张"])
                brief["available_facts"]["case_studies"] = [
                    {
                        "documentId": str(item.get("documentId") or "")[:40],
                        "text": str(item.get("output") or item.get("content") or item.get("description") or "")[:240],
                    }
                    for item in case_items[:3]
                    if isinstance(item, dict)
                ]
            else:
                brief["known_facts"].append("当前没有可直接发送的真实案例图片或案例资料，不能编造前后对比。")
            brief["do_not_say"].extend(["案例价格", "哪家门店可以看案例"])

    if "project_process" in intent_set:
        brief["must_answer"].append("客户问操作流程或大概要多久，先给通用流程和时长范围；不同项目配置会有差异。")
        brief["known_facts"].append("流程类问题应覆盖：到店评估/清洁检测/方案确认/操作/护理提醒/整体时长。")
        brief["answer_first"].append("这类项目一般会先做皮肤状态确认，再做清洁和项目操作，结束后会有护理提醒；到店整体通常在40-60分钟左右，项目操作本身多在20-30分钟。")
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
        if _effect_guarantee_question(content):
            if _asks_effect_maintenance(content):
                brief["answer_first"].append(
                    "客户问做后会不会反复或能维持多久：先说明不是做完就永久不再变化，基础改善和服务跟进有保障；后续防晒、护理和生活习惯会影响维持稳定度。"
                )
                brief["must_answer"].append("必须直接回答维持/反复顾虑，不要只泛泛说效果有保障。")
                brief["do_not_say"].extend(["永久", "永远", "不反弹", "不会反弹", "一次解决"])
            brief["answer_first"].append(
                "客户问效果保障时，第一句必须正面承接：基础改善和服务跟进是有保障的，会先看客户基础、匹配方案并把预期说清楚；不要以不能保证、不能承诺、因人而异开头。"
            )
            brief["must_answer"].append(
                "客户泛问效果保障时，不要只说不能保证；要按优秀客服口径表达“到店先了解/检测，满意再做，做后会跟进护理维持”，但不编具体案例、次数或绝对效果。"
            )
            brief["do_not_say"].extend(["效果不能承诺完全消失", "不能承诺完全消失", "不能保证效果", "不能承诺效果"])
            brief["do_not_say"].extend(
                [
                    "美眸",
                    "佛山顺德店",
                    "持证",
                    "备案",
                    "资质",
                    "所有项目",
                    "顾客反馈",
                    "案例节点",
                    "包效果",
                    "一定有效",
                    "100%见效",
                    "根治",
                    "不反弹",
                ]
            )
        if planner_helpers._is_soft_fee_concern(content):
            brief["answer_first"].append("你担心到店后收费说法不一致，这个我先跟你说清楚：项目范围、包含项、尾款和是否另加项目，都会在你确认前先核清楚。")
            brief["must_answer"].append("本轮是收费透明顾虑，只回答收费如何提前确认和逐项核对；不要转去问城市、门店或预约。")
            brief["known_facts"].append("没有可引用资质、设备或服务追溯事实时，不要主动编造这些背书。")
            brief["do_not_say"].extend(["放心", "不会乱收费", "绝不会额外加收", "没有隐形消费", "所有门店", "资质认证", "近期可安排", "哪天方便", "哪个门店", "帮你查时间", "附近门店"])
        else:
            brief["must_answer"].append("本轮是信任顾虑，先认可客户谨慎，再基于可用资质/背书事实解释；没有资料时不要编造。")
    if "competitor_compare" in intent_set:
        brief["must_answer"].append("本轮是竞品/比价：认可对比，拆项目、产品、剂量、部位、次数、售后等维度；不贬低竞品、不跟价。")
    if "after_sales" in intent_set:
        brief["must_answer"].append("本轮是售后/操作后反馈：先安抚并确认项目、操作时间、症状；有风险信号时让专业人士协助确认。")
        if is_effect_dissatisfaction_followup(content):
            brief["answer_first"].append("已经做了两次还没看到明显变化，这种情况先别急着继续加项目，更要先看做的是哪类、间隔多久、现在主要是完全没变化还是变化不明显。")
            brief["must_answer"].append("客户在问做了几次还不见效果时，先给调整判断框架，不要直接按投诉处理，也不要立刻转去门店、价格或预约。")
            brief["do_not_say"].extend(["一点效果都没有就是失败", "继续加项目", "先去约时间", "先看门店"])


def apply_price_recap_and_memory_context(state: AgentState, brief: dict[str, Any], callbacks: ReplyBriefCallbacks) -> None:
    content = state.get("normalized_content") or ""
    if callbacks.asks_price_recap(content):
        brief["must_answer"].append("客户要你把价格再顺一下时，优先复述已知价格事实；没有明确价格就直接说暂未查到。")
        brief["answer_first"].append(callbacks.price_summary_message(state))

    if _generic_case_request(state, callbacks):
        return

    memory_context = "" if should_suppress_profile_memory_for_reply(state) else callbacks.memory_context_sentence(state)
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


def _apply_ad_price_boundary(
    content: str,
    brief: dict[str, Any],
    callbacks: ReplyBriefCallbacks,
    frame: Any | None = None,
) -> None:
    digits = extract_customer_seen_price_digits(content) or callbacks.extract_price_digits(content)
    if digits:
        brief["known_facts"].append(f"客户看到的广告/活动价格数字：{'、'.join(digits[:3])}")
        brief["available_facts"]["customer_seen_price"] = digits[:3]
    brief["known_facts"].append("客户没有提供明确广告项目或广告截图时，不能拿相似知识库命中的价格代替报价。")
    if not frame:
        brief["answer_first"].append("可以先按客户看到的金额核对收费口径，但当前不能确认是不是同一条广告或同一个活动。")
        brief["must_answer"].append("解释广告价需要核对对应项目、包含项、预约金/尾款和是否另收费；不要确认广告价真实存在。")
    if frame and frame.name in {"single_fee", "confirm_price", "price_conflict", "hidden_fee_concern", "course_payment", "deposit_question"}:
        brief["known_facts"].append("广告价格追问需要优先解释价格口径，再补是否要核对广告项目、包含项、预约金或尾款。")
    brief["do_not_say"].extend(["这个价格确实有", "目前有这个活动", "可以按这个价格做", "肯定没有其他收费", "绝对没有隐形消费", "斑点出现多久", "晒后明显", "想改善哪一点", "更适合怎么推进"])
    if not brief.get("follow_up"):
        brief["follow_up"] = "如需继续确认，只问广告截图、项目名称或收费包含项其中一项，不转去问皮肤细节。"


def _intent_set(state: AgentState) -> set[str]:
    return {str(item.get("intent") or "") for item in state.get("intents", []) if isinstance(item, dict)}


def _case_items_from_tool_results(state: AgentState) -> list[dict[str, Any]]:
    tool_results = state.get("tool_results", {}) or {}
    value = tool_results.get("case_studies") if isinstance(tool_results, dict) else None
    if isinstance(value, dict):
        items = value.get("items") or value.get("outputList") or []
        if not items and (value.get("content") or value.get("output")):
            items = [value]
        return [item for item in items if isinstance(item, dict)]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _case_image_url(items: list[dict[str, Any]]) -> str:
    for item in items:
        content = str(item.get("content") or item.get("output") or "")
        match = re.search(r'<img\s+[^>]*src=["\']([^"\']+)["\']', content, flags=re.IGNORECASE)
        if match:
            return html.unescape(match.group(1))
        stripped = content.strip()
        if stripped.startswith("http://") or stripped.startswith("https://"):
            return html.unescape(stripped.split()[0])
    return ""


def _effect_guarantee_question(content: str) -> bool:
    return any(
        term in content
        for term in [
            "效果有保障",
            "效果保障",
            "效果能保证",
            "能保证吗",
            "有保障吗",
            "保障效果",
            "保证效果",
            "有效果吗",
            "会不会反弹",
            "怕反弹",
            "担心反弹",
            "能维持多久",
            "维持多久",
            "保持多久",
            "能保持多久",
        ]
    )


def _is_initial_broad_project_intro(content: str) -> bool:
    text = content or ""
    if not any(term in text for term in ["了解淡斑", "了解祛斑", "了解一下淡斑", "了解一下祛斑", "想了解淡斑", "想了解祛斑"]):
        return False
    return not any(term in text for term in ["点状", "片状", "色沉", "肤色不均", "预算", "多少钱", "价格", "案例", "效果对比"])


def _asks_effect_timeline(content: str) -> bool:
    return any(term in (content or "") for term in ["几次", "多久", "多长时间", "看到变化", "见效", "周期"])


def _asks_effect_maintenance(content: str) -> bool:
    return any(
        term in (content or "")
        for term in ["会不会反弹", "怕反弹", "担心反弹", "反弹", "能维持多久", "维持多久", "保持多久", "能保持多久"]
    )


def _wants_direct_direction_answer(content: str) -> bool:
    text = content or ""
    return any(
        term in text
        for term in [
            "别一直问我",
            "不要一直问",
            "别老问我",
            "别问了",
            "你直接说",
            "先说方向",
            "先说我这种",
            "先说我这个",
            "你判断",
            "你就说",
            "我不懂项目",
            "先看什么方向",
            "先看哪个方向",
        ]
    )
