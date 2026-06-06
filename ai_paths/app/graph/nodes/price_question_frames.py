from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class PriceQuestionFrame:
    name: str
    answer_first: str
    must_answer: str
    reply_point: str
    follow_up: str
    missing_slot: str
    suggested_next_step: str
    do_not_say: tuple[str, ...] = ()


_DEPOSIT_TERMS = ("定金", "订金", "预约金", "10元", "十元", "10块", "十块")


def detect_price_question_frame(content: str) -> str:
    text = str(content or "")
    if not text:
        return ""
    if any(term in text for term in _DEPOSIT_TERMS):
        return "deposit_question"
    if any(term in text for term in ["是不是一次的费用", "是一次的费用吗", "一次的费用", "一次多少钱", "单次费用", "单次多少钱", "一只还是一双", "一边还是两边"]):
        return "single_fee"
    if any(term in text for term in ["要做多少次", "做多少次", "做几次", "几次有效果", "一次做好", "一次能好吗", "一次能不能好"]):
        return "times_question"
    if any(term in text for term in ["做疗付费", "疗程付费", "按疗程付费", "到店付费", "是不是到店付费", "是先付费吗"]):
        return "course_payment"
    if any(term in text for term in ["为什么还有", "价格怎么不一样", "怎么跟我说", "怎么又变成", "同样的地方还有", "一个268一个380", "199你又说"]):
        return "price_conflict"
    if any(term in text for term in ["乱收费", "其他收费", "隐形消费", "额外收费", "另外收费", "到店加钱", "会不会加钱", "会不会乱收费"]):
        return "hidden_fee_concern"
    if any(term in text for term in ["确定268", "确定199", "这个价准吗", "就是这个价吗", "这个价格是真的吗", "268吗", "199吗"]):
        return "confirm_price"
    return ""


def build_price_question_frame(content: str, digits: list[str] | None = None) -> PriceQuestionFrame | None:
    text = str(content or "")
    seen_digits = [str(item) for item in (digits or []) if str(item).strip()]
    primary_digit = seen_digits[0] if seen_digits else "这个价格"
    frame = detect_price_question_frame(text)
    if frame == "confirm_price":
        return PriceQuestionFrame(
            name=frame,
            answer_first=f"客户在确认{primary_digit}这个数字本身。先正面回答它可以先按客户看到的活动或体验口径理解，再补一句还要核对对应项目、范围和适用条件。",
            must_answer="先回答这个数字是客户当前看到的参考口径，再解释为什么还要核对活动来源、项目和包含项；不要直接跳预约。",
            reply_point="把价格先解释成‘客户看到的参考口径’，不是一上来否定，也不是直接确认永久有效。",
            follow_up="只在必要时确认客户看到的是哪条广告、哪个项目，最多追问一个。",
            missing_slot="广告截图或项目名称",
            suggested_next_step="核对广告口径",
            do_not_say=("最低价", "永久有效", "现在就是这个价"),
        )
    if frame == "single_fee":
        return PriceQuestionFrame(
            name=frame,
            answer_first="客户在确认是不是一次费用。先直接说这类价格通常指单次或一个明确范围的口径，不是整个疗程都按这个价；如果广告写的是199、268这类，多半还要分局部、全脸或双侧范围。",
            must_answer="必须先回答‘一次费用/服务范围’这个核心问题，不能只说暂未查到价格。",
            reply_point="把收费范围解释清楚：单次、局部、全脸、单边、双侧，先讲口径再讲金额。",
            follow_up="必要时只确认客户问的是局部、全脸还是双侧，不要再泛问需求。",
            missing_slot="服务范围",
            suggested_next_step="核对收费范围",
            do_not_say=("包全部", "全都包含", "默认就是一整套"),
        )
    if frame == "price_conflict":
        return PriceQuestionFrame(
            name=frame,
            answer_first="客户在比较两个不同数字。先解释价格不一样通常来自活动口径、部位范围、预约方式、是否含尾款或包含项不同，再说当前要帮客户核哪一个口径。",
            must_answer="必须先解释差异来源，不能只说‘发截图我看看’。",
            reply_point="把‘为什么268又说380’拆成价格场景差异，而不是把问题重新丢回给客户。",
            follow_up="只在必要时让客户补充广告截图或项目名中的一个。",
            missing_slot="广告来源或项目名称",
            suggested_next_step="核对价格差异口径",
            do_not_say=("别人错了", "随便改价", "门店乱报价"),
        )
    if frame == "hidden_fee_concern":
        return PriceQuestionFrame(
            name=frame,
            answer_first="客户担心到店后会不会再加钱。先承接这个顾虑，再说明到店前会把项目范围、包含项、尾款和是否有加项逐项核清楚，不会把局部价当成全脸价来讲。",
            must_answer="这是普通收费透明顾虑，先解释收费口径，不要直接升级成人工或投诉场景。",
            reply_point="重点回答收费怎么核、哪里可能产生差异、客户怎么逐项确认。",
            follow_up="如果还要继续，只确认广告截图或项目名其中一个，不转去问门店和预约。",
            missing_slot="广告截图或项目名称",
            suggested_next_step="核对收费口径",
            do_not_say=("绝不加钱", "绝对没有其他收费", "不会有任何额外费用"),
        )
    if frame == "times_question":
        return PriceQuestionFrame(
            name=frame,
            answer_first="客户在问做几次或者能不能一次做好。先直接回答这类改善通常不是做一次就把所有问题定完，很多顾客会先看2-3次左右的变化，再根据范围和皮肤反应调整节奏。",
            must_answer="必须先回答次数和节奏边界，不能改成反问客户想改善什么。",
            reply_point="先给‘不是一次定完、常见先看2-3次左右变化’的方向，再补个体差异。",
            follow_up="最多补问一个关键因素，比如是近几年慢慢出来还是最近明显、范围大不大。",
            missing_slot="斑点范围或出现时间",
            suggested_next_step="确认改善节奏",
            do_not_say=("一次根治", "一次就好", "一次见效"),
        )
    if frame == "course_payment":
        return PriceQuestionFrame(
            name=frame,
            answer_first="客户在问是按单次、到店还是疗程口径付费。先回答一般会按当次确认的项目和范围来核，不是先把完整疗程一次性说死；后面要不要继续做再看当次情况和你自己的安排。",
            must_answer="必须先解释付费口径，不要回成泛泛的‘根据项目付费’。",
            reply_point="把疗程、单次、到店核对这几个口径分开说清楚。",
            follow_up="必要时只确认客户问的是广告体验价还是完整安排。",
            missing_slot="当前问的是体验价还是完整疗程",
            suggested_next_step="核对付费口径",
            do_not_say=("必须先买疗程", "先把整个疗程交完"),
        )
    if frame == "deposit_question":
        return PriceQuestionFrame(
            name=frame,
            answer_first="客户在问10元/定金规则。先说明这通常是预约登记或活动参与口径，不等于已经锁定项目效果或最终费用。",
            must_answer="先把10元、尾款和总价的关系讲清楚，再说明已支付后的具体退款仍要看记录核对。",
            reply_point="解释定金和尾款的关系，不直接许诺已预约成功。",
            follow_up="只解释定金/预约金规则；客户主动说要预约或开单时，再确认门店、日期、时间和手机号。",
            missing_slot="门店和时间",
            suggested_next_step="确认预约信息",
            do_not_say=("一定退", "不交也一样", "已经锁位成功", "您想去哪家门店", "哪家门店", "什么时间方便", "哪天方便"),
        )
    return None


def is_case_times_followup(content: str) -> bool:
    text = str(content or "")
    if not text:
        return False
    case_terms = ["图片上的", "图上的", "你发的效果图", "这个效果图", "这个案例", "这个客户", "客户做完之后"]
    ask_terms = ["做了多少次", "做几次", "几次的效果", "第几次", "几次后"]
    return any(term in text for term in case_terms) and any(term in text for term in ask_terms)


def is_effect_dissatisfaction_followup(content: str) -> bool:
    text = str(content or "")
    if not text:
        return False
    return bool(
        any(term in text for term in ["做了2次", "做了两次", "已经做了", "做完了", "去过门店", "之前做过"])
        and any(term in text for term in ["不见效果", "没效果", "一点效果都没有", "没什么变化", "没有淡", "没淡"])
    )


def extract_customer_seen_price_digits(content: str) -> list[str]:
    return re.findall(r"\d+(?:\.\d+)?", str(content or ""))
