from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from test_planner_brain_100_edge_cases import run_case


def _case(content: str, expected: str | list[str], history: list[str] | None = None, note: str = "") -> dict[str, Any]:
    return {
        "content": content,
        "expected": [expected] if isinstance(expected, str) else expected,
        "history": history or [],
        "note": note,
    }


FRESH_CASES: list[dict[str, Any]] = [
    _case("这个弄完是不是很快又长出来", "trust_issue", ["用户：脸上黑色素明显"], "效果反复"),
    _case("做了以后能保持几个月啊", "trust_issue", ["客服：可以先看淡斑改善"], "维持时间"),
    _case("会不会弄了还和原来一样", "trust_issue", ["用户：零散小点"], "效果担心"),
    _case("我怕越做越黑", "trust_issue", ["用户：想改善黑色素"], "安全效果"),
    _case("这种是不是一次就能看到变化", ["project_process", "trust_issue"], ["客服：活动价到店核"], "次数效果"),
    _case("我年龄六十了还能看吗", "project_inquiry", [], "成熟客群"),
    _case("岁数大脸上黄斑多能不能改善", "project_inquiry", [], "年龄+需求"),
    _case("男的脸上暗沉也能做吧", "project_inquiry", [], "男性需求"),
    _case("我这是一块一块发黄", "project_inquiry", ["客服：你更像零散小点还是成片颜色重"], "类型补充"),
    _case("脸颊两边都是小点点", "project_inquiry", ["用户：想淡斑"], "类型补充"),
    _case("别让我选项目，你先说适合啥", "project_inquiry", ["用户：毛孔粗暗沉"], "拒绝泛问"),
    _case("你就先告诉我大概处理方向", "project_inquiry", ["用户：脸上色沉"], "方向"),
    _case("做的时候会痛吗", "project_inquiry", ["客服：可以先看淡斑"], "做前感受"),
    _case("这个会不会刺痛", "project_inquiry", ["用户：想了解祛斑"], "做前感受"),
    _case("整个流程大概多久", "project_process", ["用户：广告上说祛斑"], "流程时长"),
    _case("去了之后是先检测还是先做", "project_process", ["客服：到店先看实际情况"], "到店流程"),
    _case("一趟下来要花多长时间", "project_process", ["用户：明天去可以吗"], "流程耗时"),
    _case("大概要做几次能稳定点", "project_process", ["用户：脸上斑比较多"], "次数"),
    _case("过去之前需要卸妆吗", "emotion_chat", ["客服：帮您约明天下午"], "准备事项"),
    _case("需要空肚子去吗", "emotion_chat", ["客服：今天下午有空位"], "准备事项"),
    _case("能吃完饭再去吗", "emotion_chat", ["用户：我想今天去"], "准备事项"),
    _case("第一次过去要带啥", "emotion_chat", ["客服：预约在厦门百星"], "准备事项"),
    _case("你们长沙总共有几家店", "store_inquiry", [], "城市门店"),
    _case("我在岳麓区，帮我挑近的", "store_inquiry", ["客服：长沙门店有几家"], "区域推荐"),
    _case("高铁站附近是哪家", "store_inquiry", ["用户：人在长沙"], "地标推荐"),
    _case("刚才推荐的店地址再发下", "store_inquiry", ["客服：推荐长沙岳麓店"], "继承门店"),
    _case("停车场入口在哪里", "store_inquiry", ["客服：推荐厦门百星"], "停车细节"),
    _case("到了以后上几楼", "store_inquiry", ["客服：地址在嘉园大厦1楼"], "楼层"),
    _case("营业时间和地址一起发我", "store_inquiry", ["客服：推荐上海浦东二店"], "地址包"),
    _case("从机场打车过去要多久", "store_inquiry", ["用户：厦门机场附近", "客服：推荐厦门百星"], "路程"),
    _case("明天上午还能约吗", "appointment_intent", ["客服：推荐厦门百星"], "预约时间"),
    _case("今天五点左右有位置吗", "appointment_intent", ["客服：推荐上海浦东二店"], "可约查询"),
    _case("那就帮我占一个名额", "appointment_intent", ["客服：活动可以先登记"], "留名额"),
    _case("我现在报名怎么弄", "appointment_intent", ["客服：10元预约入口"], "报名"),
    _case("给我登记一下吧", "appointment_intent", ["客服：你看今天还是明天方便"], "登记"),
    _case("王桂兰", "appointment_intent", ["客服：把姓名发我，我继续帮你确认"], "姓名"),
    _case("13612345678", "appointment_intent", ["客服：把电话发我，我继续帮你确认"], "电话"),
    _case("就这个时间吧", ["appointment_confirm", "appointment_intent"], ["客服：明天13:00有空位"], "确认时间"),
    _case("改到周日上午可以吗", ["appointment_change", "appointment_intent"], ["客服：已帮您登记明天下午"], "改约"),
    _case("明天去不了了先取消", ["appointment_cancel", "appointment_intent"], ["客服：已登记明天13点"], "取消"),
    _case("我之前是不是已经约过", "appointment_confirm", [], "查预约"),
    _case("帮我看看预约成功没", "appointment_confirm", [], "查预约"),
    _case("10元是干啥的", "price_inquiry", ["客服：报名可以先登记"], "预约金"),
    _case("订金到店能抵吗", "price_inquiry", ["客服：活动预约金10元"], "抵扣"),
    _case("不付10块能不能照样去", "price_inquiry", ["客服：先保留名额"], "拒绝预约金"),
    _case("尾款是到了再给吧", "price_inquiry", ["客服：10元预约金"], "尾款"),
    _case("199是不是总共就这些钱", ["ad_price_check", "price_inquiry"], ["用户：看到广告"], "广告价"),
    _case("广告说三百多，到店还加钱不", ["ad_price_check", "price_inquiry", "trust_issue"], [], "广告价透明"),
    _case("你们这个268和380到底差哪", ["ad_price_check", "price_inquiry"], ["客服：活动价268"], "价格差异"),
    _case("一只手一个价还是两只一起", "price_inquiry", ["用户：手部活动"], "包含范围"),
    _case("我预算有限，先按最低的来", "price_inquiry", ["用户：黑色素明显"], "预算"),
    _case("老顾客还有这个活动价吗", "price_inquiry", ["客服：活动价268"], "老客"),
    _case("你们不会到店乱收费吧", "trust_issue", [], "收费信任"),
    _case("资质这些都有吧", "trust_issue", [], "资质"),
    _case("我怕你们骗我过去", "trust_issue", ["客服：发了门店"], "信任"),
    _case("老师是不是专业的", "trust_issue", ["用户：想淡斑"], "人员"),
    _case("你们是活动方还是门店接待", "trust_issue", [], "身份"),
    _case("你是不是自动回复", "trust_issue", [], "身份"),
    _case("我再不满意是不是能退", "trust_issue", ["客服：预约金10元"], "保障"),
    _case("我要退钱，别跟我扯", "complaint_refund", [], "退款"),
    _case("你们不处理我就发网上", "complaint_refund", [], "舆情"),
    _case("上次在你们店做完全没效果", "complaint_refund", [], "效果争议"),
    _case("我做了两回还是那样", "complaint_refund", [], "效果不满"),
    _case("刚做完现在红肿得厉害", "after_sales", [], "售后"),
    _case("做完起泡了怎么办", "after_sales", [], "术后异常"),
    _case("昨天弄完今天可以运动吗", "after_sales", [], "术后护理"),
    _case("结的痂多久掉", "after_sales", ["用户：做了祛斑"], "术后护理"),
    _case("别家承诺一次淡很多", "competitor_compare", [], "竞品"),
    _case("外面99，你们为什么贵", "competitor_compare", [], "竞品价"),
    _case("我朋友在另一家做的便宜", "competitor_compare", [], "朋友竞品"),
    _case("某团上不是这个价", "competitor_compare", [], "平台竞品"),
    _case("能不能发几个真实案例", "case_request", ["用户：想改善黑色素"], "案例"),
    _case("有没有跟我年龄差不多的图", "case_request", ["客服：发了同类案例"], "案例"),
    _case("上面那张图是做几次的", "case_request", ["客服：[图片]"], "案例追问"),
    _case("别重复发同一张，有别的吗", "case_request", ["客服：[图片]"], "案例去重诉求"),
    _case("我拍张脸你看看", "image_inquiry", [], "发图面诊"),
    _case("照片发过去你能判断吗", "image_inquiry", [], "发图"),
    _case("我发图是想看像不像案例", "image_inquiry", [], "图咨询"),
    _case("看着还行，那我要参加", "appointment_intent", ["客服：发了案例"], "高意向"),
    _case("那我现在交钱怎么交", "appointment_intent", ["客服：10元预约金"], "付款推进"),
    _case("还有几个名额，给我留着", "appointment_intent", ["客服：活动名额"], "名额"),
    _case("今天我有空，能不能安排", "appointment_intent", ["客服：推荐门店"], "预约"),
    _case("算了我再想想", "emotion_chat", ["客服：活动价"], "暂缓"),
    _case("好的谢谢你", "emotion_chat", ["客服：发了地址"], "感谢"),
    _case("嗯嗯", "emotion_chat", ["客服：讲了活动"], "低信息"),
    _case("行", ["appointment_intent", "appointment_confirm", "emotion_chat"], ["客服：你看今天还是明天方便"], "短确认"),
    _case("不是问价格，是问有没有效果", "trust_issue", ["客服：活动价268"], "强新意图"),
    _case("不是约时间，我问在哪里", "store_inquiry", ["客服：明天下午有空位"], "强新意图"),
    _case("不是投诉，就是怕乱收费", "trust_issue", [], "普通信任非投诉"),
    _case("不是门店，我问会不会伤脸", "trust_issue", ["客服：推荐门店"], "强新意图"),
    _case("月经期能不能做", "project_inquiry", ["客服：明天有空位"], "经期"),
    _case("我怀孕了能不能参加", "human_request", [], "孕期"),
    _case("我还在哺乳期", "human_request", [], "哺乳"),
    _case("孩子十六岁可以做吗", "human_request", [], "未成年"),
    _case("最近过敏了还能去吗", "human_request", [], "过敏"),
    _case("这个券怎么用", "campaign_inquiry", ["用户：[图片]"], "券"),
    _case("直播间说的活动还在吗", ["campaign_inquiry", "ad_price_check"], [], "直播活动"),
    _case("抖音看到的那个名额怎么抢", ["campaign_inquiry", "appointment_intent"], [], "活动报名"),
    _case("我是广告加来的，想先看看", "project_inquiry", [], "广告首询"),
    _case("黑色素那个活动流程咋样", "project_process", [], "广告流程"),
    _case("手背上这种黑点能看吗", "project_inquiry", [], "部位"),
    _case("脖子暗沉是不是也能改善", "project_inquiry", [], "部位"),
    _case("眼周黑一圈可以看吗", "project_inquiry", [], "部位"),
    _case("脸松了想提一下", "project_inquiry", [], "抗衰"),
    _case("毛孔粗还出油有办法吗", "project_inquiry", [], "毛孔"),
    _case("我主要想提亮别太贵", "price_inquiry", [], "需求+预算"),
    _case("你们这店是正规的吗我到了能看证吗", "trust_issue", [], "资质到店"),
]


def main() -> None:
    rows = [run_case(case) for case in FRESH_CASES[:100]]
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
    out_path = out_dir / "planner_brain_100_fresh_cases_report.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    if failures:
        print("FAILURES")
        for row in failures:
            print(json.dumps({k: row[k] for k in ["content", "expected", "primary", "detected", "note", "reason"]}, ensure_ascii=False))
    print(f"report={out_path}")


if __name__ == "__main__":
    main()
