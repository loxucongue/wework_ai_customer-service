from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.graph import planner_helpers, task_state


def _case(content: str, expected: str | list[str], history: list[str] | None = None, note: str = "") -> dict[str, Any]:
    return {
        "content": content,
        "expected": [expected] if isinstance(expected, str) else expected,
        "history": history or [],
        "note": note,
    }


CASES: list[dict[str, Any]] = [
    _case("做了得不得反弹", "trust_issue", ["用户：厦门机场附近", "客服：推荐厦门百星"], "方言式效果稳定性"),
    _case("弄完会不会又回到原来那样", "trust_issue", ["客服：这类可以先看淡斑改善方向"], "反复/回退顾虑"),
    _case("这个能管好久嘛", "trust_issue", ["客服：先看黑色素改善方向"], "维持时间口语"),
    _case("做一次是不是过几天就白搭", "trust_issue", ["用户：我脸上黑色素明显"], "效果持续怀疑"),
    _case("会不会越弄越花", "trust_issue", ["用户：零散小点"], "效果变差顾虑"),
    _case("我妈六十多脸上斑重还能整不", "project_inquiry", [], "年龄适配需求"),
    _case("年纪大了是不是不适合", "project_inquiry", ["客服：主要是淡斑提亮方向"], "年龄疑问"),
    _case("我这种老斑是不是没救了", "project_inquiry", [], "负面表达但未投诉"),
    _case("就是脸灰黄，一片一片的", "project_inquiry", ["客服：你更像零散小点还是成片颜色重？"], "类型补充"),
    _case("零零散散的小黑点", "project_inquiry", ["用户：想淡斑", "客服：你更像零散小点还是成片颜色重？"], "类型补充"),
    _case("你别问那么多先说我这个方向", "project_inquiry", ["用户：脸上点状斑", "客服：可以先看淡斑方向"], "拒绝追问"),
    _case("我就想知道大概怎么弄", "project_inquiry", ["用户：黑色素明显"], "承接项目方向"),
    _case("这个操作是不是很疼", "project_inquiry", ["客服：这类大多数可以先看淡斑方向"], "操作感受"),
    _case("弄这个是不是要很久", "project_process", ["用户：看到祛斑活动"], "流程时长"),
    _case("到店以后先干嘛后干嘛", "project_process", ["客服：活动价到店核"], "流程"),
    _case("整个下来耽误多长时间", "project_process", ["用户：明天想去"], "到店流程时间"),
    _case("做前要不要洗脸素颜", "emotion_chat", ["客服：帮你约厦门百星"], "到店准备"),
    _case("我去了需要带身份证吗", "emotion_chat", ["客服：明天下午有空位"], "到店准备"),
    _case("吃了早饭能不能去", "emotion_chat", ["用户：想今天过去"], "准备事项"),
    _case("长沙有点远有近一点的不", "store_inquiry", ["客服：长沙目前有多家门店"], "门店精细位置"),
    _case("我在五一广场边上", "store_inquiry", ["客服：你在长沙哪个区附近"], "位置补充"),
    _case("机场边哪个店近", "store_inquiry", ["用户：我在厦门"], "地标找店"),
    _case("你刚说的那家停车咋停", "store_inquiry", ["客服：推荐厦门百星"], "继承门店"),
    _case("导航给我，不要发一堆", "store_inquiry", ["客服：推荐上海浦东二店"], "地址导航"),
    _case("店叫啥名字我怕走错", "store_inquiry", ["客服：推荐长沙五一广场店"], "门店名"),
    _case("我已经到楼下了", "store_inquiry", ["客服：地址在嘉园大厦1楼"], "到店指引"),
    _case("电梯坐到几楼", "store_inquiry", ["客服：地址在中天广场B座5楼"], "楼层指引"),
    _case("周边好停车不", "store_inquiry", ["客服：推荐长沙五一广场店"], "停车"),
    _case("你们拉萨也有店吗", "store_inquiry", [], "未知城市门店"),
    _case("明天下午能过去不", "appointment_intent", ["客服：推荐厦门百星"], "预约时间"),
    _case("那就下午一点", "appointment_intent", ["客服：明天下午有13:00、14:30"], "时间选择"),
    _case("我叫罗学聪", "appointment_intent", ["客服：你把姓名发我，我继续帮你确认"], "姓名补槽"),
    _case("19976988097", "appointment_intent", ["客服：你把电话发我，我继续帮你确认"], "电话补槽"),
    _case("报名", "appointment_intent", ["客服：这个活动到店满意再做"], "高意向"),
    _case("先给我留一个", "appointment_intent", ["客服：10元预约登记"], "留名额"),
    _case("我今天晚点到可以吗", "appointment_intent", ["客服：厦门百星今天下午有空位"], "改时间倾向"),
    _case("换到后天上午行不", "appointment_intent", ["客服：已帮你看明天下午"], "改约"),
    _case("我临时有事不去了", "appointment_intent", ["客服：明天13:00这个时间有空位"], "取消/改约前"),
    _case("帮我查下我到底约没约上", "appointment_confirm", [], "查询预约"),
    _case("我付了10块是不是就算预约了", "price_inquiry", ["客服：会发预约入口"], "预约金规则"),
    _case("十块钱干嘛用的", "price_inquiry", ["客服：报名可以先登记"], "预约金规则"),
    _case("不交那10块我直接去行不", "price_inquiry", ["客服：想去的话可以看时间"], "拒绝预约金"),
    _case("定金后面抵不抵", "price_inquiry", ["客服：活动口径是预约登记"], "定金抵扣"),
    _case("到店再交全款可以不", "price_inquiry", ["客服：活动价到店核"], "付款方式"),
    _case("广告说199，结果是不是还有别的坑", "ad_price_check", [], "广告价透明"),
    _case("你刚说268，图上咋是380", "ad_price_check", ["客服：活动价是268"], "价格冲突"),
    _case("199是一只手还是两只手", "price_inquiry", ["客服：手部活动价268"], "部位/包含项"),
    _case("是不是一次的钱", "price_inquiry", ["客服：活动价268"], "一次费用"),
    _case("做几回才算完", "project_process", ["客服：淡斑改善方向"], "次数不应泛问"),
    _case("大概要花多少，别给我整贵的", "price_inquiry", ["用户：黑色素明显"], "预算低"),
    _case("老客户还能用这个价不", "price_inquiry", ["客服：活动价268"], "老客价"),
    _case("你们是不是套路，到了又让我加项目", "trust_issue", [], "普通收费信任"),
    _case("我怕你们不正规", "trust_issue", ["客服：门店地址已发"], "资质信任"),
    _case("有没有证，别糊弄我", "trust_issue", [], "资质"),
    _case("你们是不是骗子哦", "trust_issue", [], "普通疑虑非强投诉"),
    _case("再这样我投诉你们", "complaint_refund", ["客服：会帮你核对"], "强情绪"),
    _case("把钱退我，不然我曝光", "complaint_refund", [], "退款舆情"),
    _case("我去过你家一点用没有", "complaint_refund", [], "效果不满"),
    _case("做了两次没啥变化咋回事", "complaint_refund", [], "效果不满/售后边界"),
    _case("刚弄完这里红得厉害", "after_sales", [], "售后"),
    _case("做完流黄水了", "after_sales", [], "术后异常"),
    _case("结痂能抠吗", "after_sales", ["用户：昨天做了淡斑"], "术后护理"),
    _case("现在能喝酒不", "after_sales", ["用户：刚做完"], "术后护理"),
    _case("别人家说一次就干净", "competitor_compare", [], "竞品承诺"),
    _case("隔壁只要99你们咋这么贵", "competitor_compare", [], "竞品价格"),
    _case("小红书有人说你们贵", "competitor_compare", [], "外部评价"),
    _case("我朋友在别家做的效果很好", "competitor_compare", [], "竞品对比"),
    _case("你是门店里面的人吗", "trust_issue", [], "身份"),
    _case("你到底是真人还是机器", "trust_issue", [], "身份/AI"),
    _case("你们负责活动还是负责接待", "trust_issue", [], "身份"),
    _case("我发你图看看像不像我", "image_inquiry", [], "无图片但图咨询"),
    _case("照片里这个做了几次", "case_request", ["客服：[图片]"], "案例追问"),
    _case("还有没有同龄人的对比", "case_request", ["客服：我先给你看同类参考"], "案例"),
    _case("这个图是不是你们客户", "case_request", ["客服：发了同类参考图"], "案例真实性"),
    _case("效果图能不能再发一个", "case_request", ["客服：发了同类参考图"], "重复案例请求"),
    _case("看着有点心动", "appointment_intent", ["客服：活动价到店满意再做"], "心动高意向"),
    _case("那我怎么参加", "appointment_intent", ["客服：10元预约登记"], "参加方式"),
    _case("名额还有没有", "appointment_intent", ["客服：活动名额有限"], "名额"),
    _case("你直接帮我弄吧", "appointment_intent", ["客服：你看今天还是明天方便"], "推进"),
    _case("先这样吧", "emotion_chat", ["客服：活动价是268"], "结束"),
    _case("我考虑下", "emotion_chat", ["客服：今天可以先登记"], "暂缓"),
    _case("嗯", "emotion_chat", ["客服：这个活动到店满意再做"], "低信息"),
    _case("可以", "appointment_intent", ["客服：你看今天还是明天方便"], "确认承接"),
    _case("不用了", "emotion_chat", ["客服：给你发门店"], "结束而非取消"),
    _case("不用了明天不去了", "appointment_intent", ["客服：明天13:00有空位"], "取消倾向"),
    _case("哪个老师给我做", "trust_issue", ["客服：到店先检测"], "人员专业信任"),
    _case("会不会伤皮肤", "trust_issue", ["用户：想淡斑"], "安全顾虑"),
    _case("疼不疼，会不会留印", "trust_issue", ["用户：想淡斑"], "安全+效果"),
    _case("月经来了还能弄不", "project_inquiry", ["客服：明天可到店"], "经期适配"),
    _case("我怀孕了还能做这个吗", "human_request", [], "医疗风险"),
    _case("未成年能报名不", "human_request", [], "未成年风险"),
    _case("哺乳期可以吗", "human_request", [], "哺乳期风险"),
    _case("我皮肤过敏期能不能做", "human_request", [], "过敏风险"),
    _case("你们上班到几点", "store_inquiry", [], "营业时间"),
    _case("我在长沙，门店都有哪些位置", "store_inquiry", [], "城市门店列表"),
    _case("别发全部，离岳麓西湖公园近的", "store_inquiry", ["客服：长沙门店很多"], "地标推荐"),
    _case("从我这过去大概多久", "store_inquiry", ["用户：我在岳麓西湖公园", "客服：推荐长沙岳麓店"], "距离耗时"),
    _case("给我停车和营业时间", "store_inquiry", ["客服：推荐长沙岳麓店"], "地址包"),
    _case("这券还能用吗", "campaign_inquiry", ["用户：[图片]", "客服：这是活动券"], "券承接"),
    _case("我没有图，就看到直播说的", "ad_price_check", ["客服：发我广告图核对"], "无图广告价"),
    _case("直播间那个黑色素活动咋报", "appointment_intent", [], "直播+报名"),
    _case("手背斑也能弄吗", "project_inquiry", [], "部位需求"),
    _case("脖子黑可以看吗", "project_inquiry", [], "非脸部需求"),
    _case("我不是问价格，我想看效果", "case_request", ["客服：活动价268"], "否定价格转案例"),
    _case("不是要投诉，就是怕到店乱收费", "trust_issue", ["客服：预约金10元"], "否定投诉"),
    _case("不是预约，我问地址", "store_inquiry", ["客服：明天13:00有空位"], "强新意图覆盖预约"),
    _case("不是问门店，我问会不会反复", "trust_issue", ["客服：推荐厦门百星"], "强新意图覆盖门店"),
    _case("那这个和别家的有啥区别", "competitor_compare", ["用户：别家99"], "指代竞品"),
    _case("这个是不是你们宣传那套", "campaign_inquiry", ["客服：发了活动券"], "指代活动"),
]


def run_case(case: dict[str, Any]) -> dict[str, Any]:
    state: dict[str, Any] = {
        "normalized_content": case["content"],
        "content": case["content"],
        "image_info": {},
        "conversation_history": case["history"],
        "history_events": [],
        "customer_basic_info": {},
        "customer_profile": {},
        "appointment_cache": {},
        "active_task": {},
        "guardrail_result": {},
    }
    intents = planner_helpers.detect_intents(case["content"], {})
    intents = planner_helpers.filter_spurious_intents(state, intents)
    active_task = task_state.build_active_task(state, intents)
    intents = task_state.apply_active_task_intent(state, intents, active_task)
    intents = planner_helpers.filter_spurious_intents(state, intents)
    intents = planner_helpers.enrich_intents_with_tool_plan(state, planner_helpers.filter_spurious_intents(state, intents))
    intents = planner_helpers.filter_spurious_intents(state, intents)
    primary = intents[0].get("intent") if intents else ""
    detected = [str(item.get("intent") or "") for item in intents[:3]]
    return {
        **case,
        "primary": primary,
        "detected": detected,
        "skills": [str(item.get("skill") or "") for item in intents[:3]],
        "pass_primary": primary in case["expected"],
        "pass_top3": any(intent in case["expected"] for intent in detected),
        "reason": intents[0].get("reason", "") if intents else "",
    }


def main() -> None:
    rows = [run_case(case) for case in CASES[:100]]
    total = len(rows)
    primary_ok = sum(1 for row in rows if row["pass_primary"])
    top3_ok = sum(1 for row in rows if row["pass_top3"])
    failures = [row for row in rows if not row["pass_primary"]]
    report = {
        "summary": {
            "total": total,
            "primary_accuracy": round(primary_ok / total, 4),
            "top3_accuracy": round(top3_ok / total, 4),
            "primary_pass": primary_ok,
            "top3_pass": top3_ok,
            "primary_fail": len(failures),
        },
        "failures": failures,
        "rows": rows,
    }
    out_dir = Path("logs")
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "planner_brain_100_edge_cases_report.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    if failures:
        print("FAILURES")
        for row in failures:
            print(json.dumps({k: row[k] for k in ["content", "expected", "primary", "detected", "note", "reason"]}, ensure_ascii=False))
    print(f"report={out_path}")


if __name__ == "__main__":
    main()
