from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from statistics import mean
from typing import Any


OPENING_INPUTS = ("我已经添加了你", "开始聊天", "你好", "您好", "在吗")
OPENING_BUSINESS_TERMS = ("皮肤改善", "活动价格", "附近门店", "门店安排", "改善", "价格", "门店", "城市", "区域")
APPOINTMENT_RISK_TERMS = ("预约成功", "锁位", "锁定", "已预留", "预留名额", "确认好了", "已经确认好了")
SYSTEM_LEAK_TERMS = ("系统查询", "工具", "知识库", "intent", "subflow", "AI判断", "模型")
QUESTION_MARKS = ("？", "?")
DEFLECTIVE_TERMS = ("需要看情况", "需要到店", "需要面诊", "不能保证", "无法保证", "没法确定", "需要确认")
HARD_SELL_TOO_EARLY_TERMS = ("姓名", "电话", "手机号", "预约入口", "小程序")
ORDINARY_TRUST_TERMS = ("乱收费", "隐形消费", "会不会坑", "怕被坑", "靠谱吗", "正规吗", "有保障吗")


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, dict):
        return str(content.get("text") or content.get("handoff_reason") or content.get("url") or "").strip()
    return str(content or "").strip()


def _reply_texts(turn: dict[str, Any]) -> list[str]:
    messages = turn.get("reply_messages")
    if not isinstance(messages, list):
        return []
    return [_message_text(item) for item in messages if isinstance(item, dict) and _message_text(item)]


def _reply_types(turn: dict[str, Any]) -> list[str]:
    messages = turn.get("reply_messages")
    if not isinstance(messages, list):
        return []
    return [str(item.get("type") or "text") for item in messages if isinstance(item, dict)]


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def evaluate_report(report: list[dict[str, Any]]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    total_turns = 0
    failed_turns = 0
    elapsed: list[int] = []

    for conversation in report:
        conv_id = str(conversation.get("id") or "")
        title = str(conversation.get("title") or "")
        turns = conversation.get("turns") if isinstance(conversation.get("turns"), list) else []
        for turn in turns:
            total_turns += 1
            turn_no = int(turn.get("turn") or 0)
            user = str(turn.get("user") or "")
            status = int(turn.get("status") or 0)
            elapsed_ms = int(turn.get("elapsed_ms") or 0)
            if elapsed_ms:
                elapsed.append(elapsed_ms)
            texts = _reply_texts(turn)
            joined = "\n".join(texts)
            types = _reply_types(turn)

            if status != 200 or not texts:
                failed_turns += 1
                issues.append(_issue(conv_id, title, turn_no, user, "接口失败或无客户可见回复", joined))
                continue

            if _is_opening(user) and not _contains_any(joined, OPENING_BUSINESS_TERMS):
                issues.append(_issue(conv_id, title, turn_no, user, "开场没有进入业务承接", joined))

            if _contains_any(joined, APPOINTMENT_RISK_TERMS):
                risk_text = _strip_negated_appointment_risks(joined)
                if _contains_any(risk_text, APPOINTMENT_RISK_TERMS):
                    issues.append(_issue(conv_id, title, turn_no, user, "预约话术存在过度承诺", joined))

            if _contains_any(joined, SYSTEM_LEAK_TERMS):
                issues.append(_issue(conv_id, title, turn_no, user, "客户可见回复疑似泄露系统/工具话术", joined))

            if _asks_too_much(joined):
                issues.append(_issue(conv_id, title, turn_no, user, "单轮追问过多", joined))

            if _too_many_text_messages(types):
                issues.append(_issue(conv_id, title, turn_no, user, "普通回复拆分过多", joined))

            if _price_or_deposit_question(user) and not _answers_price_or_deposit(user, joined):
                issues.append(_issue(conv_id, title, turn_no, user, "价格/预约金问题没有正面回答", joined))

            if _store_question(user) and not _answers_store(user, joined):
                issues.append(_issue(conv_id, title, turn_no, user, "门店/地址问题没有给出可用门店信息", joined))

            if _case_or_effect_question(user) and not _answers_case_or_effect(joined):
                issues.append(_issue(conv_id, title, turn_no, user, "效果/案例问题没有建立信任或承接案例", joined))

            if _ad_or_platform_price_question(user) and not _answers_ad_or_platform_price(user, joined):
                issues.append(_issue(conv_id, title, turn_no, user, "广告价/平台价问题没有解释收费口径", joined))

            if _times_or_once_question(user) and not _answers_times_or_once(user, joined):
                issues.append(_issue(conv_id, title, turn_no, user, "次数/一次效果问题没有正面回答", joined))

            if _ordinary_trust_question(user) and "human_handoff" in types:
                issues.append(_issue(conv_id, title, turn_no, user, "普通信任顾虑被过度升级", joined))

            if _over_deflects(joined):
                issues.append(_issue(conv_id, title, turn_no, user, "回复过度兜底，解决感不足", joined))

            if _premature_appointment_push(user, joined):
                issues.append(_issue(conv_id, title, turn_no, user, "未明确预约时过早推进收集信息或预约金", joined))

            if _appointment_time_question(user) and _claims_time_without_slot_shape(joined):
                issues.append(_issue(conv_id, title, turn_no, user, "预约时间回复缺少明确时段或核对口径", joined))

            if _visit_arrangement_question(user) and not _answers_visit_arrangement(joined):
                issues.append(_issue(conv_id, title, turn_no, user, "到店安排问题没有给出可执行下一步", joined))

    return {
        "total_turns": total_turns,
        "failed_turns": failed_turns,
        "success_rate": round((total_turns - failed_turns) / total_turns, 4) if total_turns else 0,
        "avg_elapsed_ms": int(mean(elapsed)) if elapsed else 0,
        "max_elapsed_ms": max(elapsed) if elapsed else 0,
        "min_elapsed_ms": min(elapsed) if elapsed else 0,
        "issue_count": len(issues),
        "issues": issues,
    }


def _issue(conversation_id: str, title: str, turn: int, user: str, issue: str, reply: str) -> dict[str, Any]:
    return {
        "conversation_id": conversation_id,
        "title": title,
        "turn": turn,
        "user": user,
        "issue": issue,
        "reply": reply[:400],
    }


def _is_opening(user: str) -> bool:
    compact = "".join(user.split())
    return any(term in compact for term in OPENING_INPUTS)


def _asks_too_much(text: str) -> bool:
    without_urls = re.sub(r"https?://\S+", "", text)
    return sum(without_urls.count(mark) for mark in QUESTION_MARKS) > 1


def _too_many_text_messages(types: list[str]) -> bool:
    return sum(1 for item in types if item == "text") > 2


def _price_or_deposit_question(user: str) -> bool:
    return any(term in user for term in ("多少钱", "价格", "费用", "定金", "预约金", "10元", "尾款", "全款", "另收费"))


def _answers_price_or_deposit(user: str, reply: str) -> bool:
    if any(term in user for term in ("定金", "预约金", "10元")):
        return any(term in reply for term in ("10元", "预约登记", "活动参与", "尾款", "可退", "到店"))
    return any(term in reply for term in ("元", "价格", "费用", "单次", "活动", "尾款", "包含", "核对"))


def _store_question(user: str) -> bool:
    return any(term in user for term in ("门店", "地址", "导航", "停车", "附近", "机场", "哪家", "哪里"))


def _answers_store(user: str, reply: str) -> bool:
    if any(term in user for term in ("停车",)):
        return "停车" in reply
    if any(term in user for term in ("地址", "导航", "哪里", "门店", "附近", "机场", "哪家")):
        if any(term in reply for term in ("哪个城市", "城市或区域", "在哪个城市", "哪个位置")):
            return True
        return any(term in reply for term in ("地址", "门店", "店", "导航", "公里", "分钟"))
    return True


def _case_or_effect_question(user: str) -> bool:
    return any(term in user for term in ("效果", "案例", "前后", "做完", "反弹", "保障", "伤皮肤"))


def _answers_case_or_effect(reply: str) -> bool:
    return any(term in reply for term in ("改善", "参考", "案例", "效果", "放心", "保障", "先看", "基础"))


def _appointment_time_question(user: str) -> bool:
    text = str(user or "")
    if "近一点" in text or "远一点" in text:
        text = text.replace("近一点", "").replace("远一点", "")
    return any(term in text for term in ("有时间", "几点", "1点", "一点", "下午", "上午", "能约"))


def _claims_time_without_slot_shape(reply: str) -> bool:
    if any(term in reply for term in ("核一下", "暂时没看到", "没有看到")):
        return False
    if any(term in reply for term in ("有空位", "可约", "能约", "可以")):
        return not bool(re.search(r"\d{1,2}:\d{2}", reply))
    return False


def _strip_negated_appointment_risks(text: str) -> str:
    result = str(text or "")
    for phrase in (
        "不等于已经锁定",
        "不代表已经锁定",
        "不是已经锁定",
        "不等于锁定",
        "不代表锁定",
        "不是锁定",
        "不等于预约成功",
        "不代表预约成功",
    ):
        result = result.replace(phrase, "")
    return result


def _ad_or_platform_price_question(user: str) -> bool:
    return any(term in user for term in ("广告", "直播", "券", "199", "268", "380", "一次费用", "一只还是一双", "另收费", "其他收费", "到店收费", "尾款"))


def _answers_ad_or_platform_price(user: str, reply: str) -> bool:
    if "一次费用" in user or "一次的费用" in user:
        return any(term in reply for term in ("单次", "一次", "不是整个疗程", "疗程"))
    if "一只还是一双" in user:
        return any(term in reply for term in ("一只", "一双", "部位", "双手", "手部"))
    if any(term in user for term in ("另收费", "其他收费", "乱收费", "到店收费")):
        return any(term in reply for term in ("不会乱收费", "收费口径", "包含", "尾款", "项目", "确认清楚"))
    return any(term in reply for term in ("广告", "活动", "券", "口径", "包含", "尾款", "预约金", "单次", "核对"))


def _times_or_once_question(user: str) -> bool:
    return any(term in user for term in ("一次做好", "一次能好", "做多少次", "要做多少次", "做几次", "几次有效", "多少次"))


def _answers_times_or_once(user: str, reply: str) -> bool:
    if any(term in user for term in ("一次做好", "一次能好")):
        return any(term in reply for term in ("一次", "基础变化", "不是一次", "阶段", "后续", "完全"))
    return any(term in reply for term in ("次", "阶段", "先看", "观察", "调整", "疗程"))


def _ordinary_trust_question(user: str) -> bool:
    return any(term in user for term in ORDINARY_TRUST_TERMS) and not any(term in user for term in ("投诉", "退款", "骗我", "骗子"))


def _over_deflects(reply: str) -> bool:
    if not reply:
        return False
    deflect_count = sum(reply.count(term) for term in DEFLECTIVE_TERMS)
    has_customer_value = any(term in reply for term in ("可以", "能", "先", "参考", "口径", "单次", "包含", "门店", "地址", "改善", "保障", "放心"))
    return deflect_count >= 2 and not has_customer_value


def _premature_appointment_push(user: str, reply: str) -> bool:
    if any(term in user for term in ("约", "预约", "过去", "到店", "有时间", "几点", "明天", "周")):
        return False
    if any(term in user for term in ("价格", "多少钱", "费用", "效果", "案例", "乱收费", "资质", "流程", "多久")):
        return any(term in reply for term in HARD_SELL_TOO_EARLY_TERMS)
    return False


def _visit_arrangement_question(user: str) -> bool:
    return any(term in user for term in ("到店", "去店里", "过去看看", "去看看", "可以去")) and any(
        term in user for term in ("怎么安排", "怎么去", "怎么弄", "要怎么", "安排")
    )


def _answers_visit_arrangement(reply: str) -> bool:
    compact = re.sub(r"\s+", "", str(reply or "")).strip("，。！？!?~～")
    if compact in {"你好", "您好", "你好呀", "小贝在的", "你好呀小贝在的"}:
        return False
    return any(term in reply for term in ("城市", "位置", "门店", "时间", "到店", "安排", "推荐"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate long-dialogue report against sales champion rhythm.")
    parser.add_argument("--report", default="logs/online_appointment_long_flows_report.json")
    parser.add_argument("--suffix", default="")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    data = json.loads(Path(args.report).read_text(encoding="utf-8"))
    if args.suffix:
        data = [item for item in data if str(item.get("id") or "").endswith(args.suffix)]
    result = evaluate_report(data)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
