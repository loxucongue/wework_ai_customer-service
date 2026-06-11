# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
API_URL = os.getenv("AI_PATHS_API_URL", "http://47.252.81.104/api/ai/chat")
REPORT_JSON = ROOT / "logs" / "sales_talk_kb_regression_report.json"
REPORT_MD = ROOT / "logs" / "sales_talk_kb_regression_report.md"

BASE_PAYLOAD = {
    "corp_id": "ent-753d018266f7453285311ce1d5ed0d94",
    "user_id": "DY1032",
    "wechat": "DY1032",
}

CASES: list[dict[str, Any]] = [
    {
        "id": "once_done",
        "question": "一次做好吗",
        "purpose": "验证是否能先给效果预期，再补个体差异，不直接推检测。",
        "history": ["用户：我主要想改善脸上的斑点。"],
        "expected": ["一次", "效果", "每个人", "情况不一样"],
        "avoid": ["保证", "根治", "必须做三次"],
    },
    {
        "id": "hurt_skin",
        "question": "会伤害皮肤吗",
        "purpose": "验证是否能先给温和、恢复感受和评估口径，不做绝对安全承诺。",
        "history": ["用户：我有点担心做完刺激太大。"],
        "expected": ["温和", "恢复", "皮肤", "先看"],
        "avoid": ["绝对安全", "完全不会", "零风险"],
    },
    {
        "id": "confirm_199",
        "question": "确定199吗",
        "purpose": "验证是否能承接活动价确认，并解释这是活动口径与包含项。",
        "history": ["用户：我在广告上看到199。"],
        "expected": ["199", "活动", "包含", "到店"],
        "avoid": ["所有门店都一样", "肯定最低价"],
    },
    {
        "id": "single_fee",
        "question": "是一次的费用吗",
        "purpose": "验证是否能说明单次体验、预约金和尾款关系。",
        "history": ["用户：你们活动价看着不高。"],
        "expected": ["一次", "活动", "预约金", "尾款"],
        "avoid": ["先别管", "到店再说"],
    },
    {
        "id": "times",
        "question": "要做多少次",
        "purpose": "验证是否能给出次数预期，而不是机械要求检测。",
        "history": ["用户：我是点状斑，肤色也有点不均。"],
        "expected": ["一次", "效果", "每个人", "不一样"],
        "avoid": ["固定三次", "必须做疗程"],
    },
    {
        "id": "identity",
        "question": "你是门店的人吗",
        "purpose": "验证是否能用前期承接和安排负责人的口径回答，不暴露AI。",
        "history": ["用户：我想先了解一下你们活动。"],
        "expected": ["负责人", "安排", "老师", "咨询"],
        "avoid": ["AI", "机器人"],
    },
    {
        "id": "qualification",
        "question": "有资质吗",
        "purpose": "验证是否能承接资质与正规性顾虑。",
        "history": ["用户：我比较担心不正规。"],
        "expected": ["资质", "到店", "看得到", "正规"],
        "avoid": ["绝对", "百分百"],
    },
    {
        "id": "hidden_fee",
        "question": "到店会乱收费吗",
        "purpose": "验证是否能先给收费透明感，再解释活动与包含项。",
        "history": ["用户：我担心广告价和到店不一样。"],
        "expected": ["公开透明", "活动", "包含", "乱收费"],
        "avoid": ["绝对不会", "没有任何可能"],
    },
    {
        "id": "image_case_times",
        "question": "图片上的客户做了多少次",
        "purpose": "验证是否能承接效果案例追问，不乱编固定次数。",
        "history": ["用户：你发的那张效果图我看到了。"],
        "expected": ["效果", "参考", "检测", "针对性"],
        "avoid": ["就是一次", "固定三次"],
    },
    {
        "id": "price_conflict_380",
        "question": "为什么一样的地方还有380的价格",
        "purpose": "验证是否能解释活动档位、包含项和预约口径差异。",
        "history": ["用户：我看到广告上写268。"],
        "expected": ["268", "380", "活动", "设备"],
        "avoid": ["你看错了", "别人乱报"],
    },
    {
        "id": "hand_price",
        "question": "手上的价格是多少 199是一只还是一双",
        "purpose": "验证是否能识别部位计价问题，不误答为面部祛斑价格。",
        "history": ["用户：我看到广告上写手部199。"],
        "expected": ["199", "活动", "检测", "方案"],
        "avoid": ["祛斑268", "面部"],
    },
    {
        "id": "pay_after",
        "question": "是做完付款吗",
        "purpose": "验证是否能说明先检测、满意再补尾款的付款时点。",
        "history": ["用户：你们好像要先付10元。"],
        "expected": ["检测", "方案", "满意", "尾款"],
        "avoid": ["已预约成功", "必须全款先付"],
    },
    {
        "id": "travel",
        "question": "有车费报销吗 可以包接送吗",
        "purpose": "验证是否明确不承诺接送和报销，同时提供路线协助。",
        "history": ["用户：你们门店离我有点远。"],
        "expected": ["没有", "路线", "导航", "近一点"],
        "avoid": ["包接送", "报销车费", "帮你叫车接你来"],
    },
    {
        "id": "mole",
        "question": "可以去痣吗 去痣要多少钱",
        "purpose": "验证是否能承接去痣需求，不套用祛斑活动价。",
        "history": ["用户：你们都做哪些面部问题。"],
        "expected": ["大痣", "小痣", "每个人情况不一样", "到店"],
        "avoid": ["祛斑199", "淡斑268"],
    },
    {
        "id": "effect_times",
        "question": "你发的效果图是做几次的效果",
        "purpose": "验证是否能承接案例真实性与次数预期。",
        "history": ["用户：刚刚那张效果对比图我看到了。"],
        "expected": ["效果", "参考", "检测", "针对性"],
        "avoid": ["你想做哪个项目"],
    },
    {
        "id": "address_dare",
        "question": "为什么不敢发详细地址",
        "purpose": "验证是否能化解门店真实性质疑，并直接给地址逻辑。",
        "history": ["用户：你们西藏有门店吗。"],
        "expected": ["地址", "门店", "最近", "安排"],
        "avoid": ["不能发", "到店再说"],
    },
    {
        "id": "medical_qualification",
        "question": "你们有医疗资质吗？",
        "purpose": "验证是否能承接医疗资质类强信任问题。",
        "history": ["用户：我比较担心是不是正规门店。"],
        "expected": ["资质", "可查", "到店", "正规"],
        "avoid": ["绝对正规", "别担心就行"],
    },
    {
        "id": "two_times_no_effect",
        "question": "已做了2次，不见效果呢？",
        "purpose": "验证是否能先复盘门店、项目、方式和不满点。",
        "history": ["用户：我之前在你们这做过。"],
        "expected": ["哪里做的", "什么项目", "什么方式", "不满意"],
        "avoid": ["正常", "再做几次就好了"],
    },
    {
        "id": "store_name",
        "question": "你们门店名字叫什么",
        "purpose": "验证是否能结合城市信息直接给门店名字。",
        "history": ["用户：我在厦门，想去店里看看。"],
        "expected": ["门店", "厦门", "名字"],
        "avoid": ["你想做什么项目"],
    },
    {
        "id": "price_conflict_308",
        "question": "我看到是199的 你怎么跟我说308的价格",
        "purpose": "验证是否能解释活动价冲突来源，不答非所问。",
        "history": ["用户：我看到广告是199。"],
        "expected": ["199", "308", "活动", "价格"],
        "avoid": ["11111", "你看错了"],
    },
    {
        "id": "no_deposit",
        "question": "我不交定金到店再付全款",
        "purpose": "验证是否能解释活动价与预约名额关系，不强压付款。",
        "history": ["用户：你们要先付10元。"],
        "expected": ["定金", "活动价", "到店", "不强推"],
        "avoid": ["必须先交", "不交不能来"],
    },
    {
        "id": "too_far",
        "question": "太远了 没有时间去",
        "purpose": "验证是否能给近店、方便时段等替代方案。",
        "history": ["用户：你们门店对我来说有点远。"],
        "expected": ["近一点", "方便", "门店", "时间"],
        "avoid": ["那没办法", "有空再说"],
    },
    {
        "id": "bad_store_exp",
        "question": "这个店我去过 一点效果都没有",
        "purpose": "验证是否能核对历史门店与体验问题，承接不满但不乱甩锅。",
        "history": ["用户：我之前去过你们店。"],
        "expected": ["哪家店", "什么时候", "不满意", "重新安排"],
        "avoid": ["不是我们家", "和我们没关系"],
    },
    {
        "id": "no_spot_want_wrinkle",
        "question": "我没有斑 想做皱纹",
        "purpose": "验证是否能从斑点导向切换到皱纹/抗衰方向。",
        "history": ["用户：我主要是想改善面部状态。"],
        "expected": ["皱纹", "抗衰", "皮肤状态", "方向"],
        "avoid": ["还是祛斑", "继续问斑点"],
    },
    {
        "id": "ad_58_real",
        "question": "看广告是58元是真的吗",
        "purpose": "验证是否能承接低价广告真实性，不乱报别的活动价。",
        "history": ["用户：我刚刚刷到你们广告。"],
        "expected": ["58", "活动", "包含", "核对"],
        "avoid": ["268", "199", "不知道"],
    },
    {
        "id": "why_deposit",
        "question": "为什么要交定金",
        "purpose": "验证是否能说明定金是锁活动价和档期，不是强制消费。",
        "history": ["用户：你们是不是非要先付款。"],
        "expected": ["定金", "活动价", "档期", "锁名额"],
        "avoid": ["必须交", "不交不行"],
    },
    {
        "id": "pay_after_effect",
        "question": "有效果在付款吗",
        "purpose": "验证是否能承接满意再补尾款的付款节奏。",
        "history": ["用户：我担心先付钱不放心。"],
        "expected": ["满意", "检测", "尾款", "方案"],
        "avoid": ["先付全款", "不给看就付"],
    },
    {
        "id": "image_effect_so_good",
        "question": "这个图片的客户做几次有这么好的效果",
        "purpose": "验证是否能承接效果图真实性、恢复期和个体差异。",
        "history": ["用户：你发的效果图看起来变化挺大。"],
        "expected": ["参考", "恢复", "效果", "每个人"],
        "avoid": ["保证你也这样", "固定一次"],
    },
    {
        "id": "fee_after_activity",
        "question": "参加活动价到店会不会有其他的收费呀",
        "purpose": "验证是否能强调活动价透明，不回避客户担忧。",
        "history": ["用户：我怕到店加项目。"],
        "expected": ["活动价", "透明", "没有隐形消费", "提前确认"],
        "avoid": ["绝对不可能", "你别担心就行"],
    },
    {
        "id": "arrived_downstairs",
        "question": "我已经到门店楼下了 地址在哪里",
        "purpose": "验证是否能直接发门店地址，不再兜圈子。",
        "history": ["用户：我已经到附近了。"],
        "expected": ["地址", "门店", "楼下", "定位"],
        "avoid": ["你在哪个城市", "再确认需求"],
    },
    {
        "id": "want_9am",
        "question": "我要9点可以吗",
        "purpose": "验证是否能承接早间预约时间诉求，先查档期或说明时间范围。",
        "history": ["用户：我明天想来店里。"],
        "expected": ["9点", "档期", "安排", "确认"],
        "avoid": ["直接默认成功", "无视时间"],
    },
    {
        "id": "come_now",
        "question": "现在过来可以吗",
        "purpose": "验证是否能承接即时到店，快速转成门店+档期确认。",
        "history": ["用户：我离门店不远。"],
        "expected": ["现在", "档期", "门店", "安排"],
        "avoid": ["有空再说", "改问项目"],
    },
    {
        "id": "too_late_2pm",
        "question": "2点太迟了 我下午还要上班 给我安排早点",
        "purpose": "验证是否能承接时间异议并主动协调更早档期。",
        "history": ["用户：你给我的时间有点晚。"],
        "expected": ["早点", "调整", "档期", "安排"],
        "avoid": ["那就没办法", "只能这个时间"],
    },
    {
        "id": "wait_teacher_slow",
        "question": "要等你联系老师我会比较慢 我等下还有其他事",
        "purpose": "验证是否能承接客户嫌慢的情绪，并给出更快确认方案。",
        "history": ["用户：我现在比较赶。"],
        "expected": ["先记下", "尽快", "安排", "不耽误"],
        "avoid": ["继续慢慢问", "那你晚点再来"],
    },
    {
        "id": "other_customer_making_scene",
        "question": "门店怎么有其他客户来闹事呀",
        "purpose": "验证是否能承接现场信任波动，不回避但不扩散。",
        "history": ["用户：我现在在门店附近。"],
        "expected": ["我先核实", "别担心", "给你安排", "不耽误"],
        "avoid": ["不知道", "跟我们没关系"],
    },
    {
        "id": "not_your_price",
        "question": "怎么不是你说的那个价格呀 要我好几千块",
        "purpose": "验证是否能承接价格落差和现场异议，不乱甩门店。",
        "history": ["用户：你之前跟我说活动价不高。"],
        "expected": ["先核对", "活动价", "口径", "给你讲清楚"],
        "avoid": ["门店问题", "你听错了"],
    },
    {
        "id": "pay_tail_before_understand",
        "question": "怎么一到门店就要我先交尾款啊 我还什么都不了解",
        "purpose": "验证是否能承接现场付款异议，先解释正常流程再协助升级。",
        "history": ["用户：我到店了。"],
        "expected": ["先检测", "先了解", "尾款", "我帮你核实"],
        "avoid": ["必须先交", "门店说了算"],
    },
    {
        "id": "wait_mask_too_long",
        "question": "我躺在这都一个多小时了一直敷面膜没人来做呀",
        "purpose": "验证是否能承接现场等待情绪，及时安抚并升级处理。",
        "history": ["用户：我已经在店里了。"],
        "expected": ["不好意思", "马上帮你", "核实", "专业同事"],
        "avoid": ["再等等", "正常"],
    },
    {
        "id": "no_effect_after_done",
        "question": "做完没有效果啊",
        "purpose": "验证是否能承接做后效果落差，先复盘再处理。",
        "history": ["用户：我今天刚做完。"],
        "expected": ["先看", "恢复", "现在最在意", "我帮你核实"],
        "avoid": ["正常", "再等等就行"],
    },
    {
        "id": "supplement_missed_spots",
        "question": "我脸上还有几个没给我做 我可以补做吗",
        "purpose": "验证是否能承接补做诉求，先核对项目和门店执行情况。",
        "history": ["用户：我刚从门店出来。"],
        "expected": ["补做", "核对", "门店", "安排"],
        "avoid": ["不行", "自己等恢复"],
    },
    {
        "id": "refund",
        "question": "我要退钱",
        "purpose": "验证是否能识别退款诉求并进入人工协助链路。",
        "history": ["用户：我对这次体验不满意。"],
        "expected": ["退款", "帮您同步", "专业同事"],
        "avoid": ["继续推项目", "继续报价"],
    },
    {
        "id": "deposit_not_deducted",
        "question": "我交的10定金没有给我抵扣你要退给我",
        "purpose": "验证是否能识别订单/定金争议并进入人工协助链路。",
        "history": ["用户：我到店消费过了。"],
        "expected": ["10元", "核对", "专业同事", "处理"],
        "avoid": ["门店问题", "不归我管"],
    },
]


def request_chat(case: dict[str, Any]) -> dict[str, Any]:
    payload = {
        **BASE_PAYLOAD,
        "customer_id": f"kb-reg-{case['id']}-{uuid.uuid4().hex[:8]}",
        "content": case["question"],
        "messages": case.get("history", []),
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8")
            return {
                "ok": True,
                "status": resp.status,
                "elapsed": round(time.time() - started, 2),
                "payload": payload,
                "response": json.loads(raw),
            }
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "status": exc.code,
            "elapsed": round(time.time() - started, 2),
            "payload": payload,
            "error": detail,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "status": 0,
            "elapsed": round(time.time() - started, 2),
            "payload": payload,
            "error": str(exc),
        }


def extract_reply_texts(data: dict[str, Any]) -> list[str]:
    reply_messages = data.get("reply_messages", [])
    texts: list[str] = []
    for item in reply_messages:
        content = item.get("content")
        if isinstance(content, dict):
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                texts.append(text.strip())
        elif isinstance(content, str) and content.strip():
            texts.append(content.strip())
    return texts


def evaluate_reply(case: dict[str, Any], texts: list[str]) -> dict[str, Any]:
    merged = "\n".join(texts)
    hit_expected = [kw for kw in case.get("expected", []) if kw in merged]
    hit_avoid = [kw for kw in case.get("avoid", []) if kw in merged]
    if not texts:
        grade = "failed"
    elif hit_avoid:
        grade = "risk"
    elif len(hit_expected) >= max(1, len(case.get("expected", [])) // 2):
        grade = "good"
    else:
        grade = "partial"
    return {
        "grade": grade,
        "hit_expected": hit_expected,
        "hit_avoid": hit_avoid,
    }


def build_markdown_report(results: list[dict[str, Any]]) -> str:
    lines = [
        "# 市场客服话术知识库回归测试报告",
        "",
        f"- 测试时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 接口地址：`{API_URL}`",
        f"- 总用例数：{len(results)}",
        "",
        "| 编号 | 问题 | 目的 | 状态 | 评分 | 耗时(s) | 期望命中 | 风险命中 | 回复摘要 |",
        "|---|---|---|---:|---|---:|---|---|---|",
    ]
    for idx, item in enumerate(results, start=1):
        if item["ok"]:
            texts = item["texts"]
            evaluation = item["evaluation"]
            summary = " / ".join(texts[:2]).replace("\n", " ").strip()
            lines.append(
                f"| {idx} | {item['question']} | {item['purpose']} | {item['status']} | "
                f"{evaluation['grade']} | {item['elapsed']} | "
                f"{'、'.join(evaluation['hit_expected']) or '-'} | "
                f"{'、'.join(evaluation['hit_avoid']) or '-'} | {summary[:120]} |"
            )
        else:
            lines.append(
                f"| {idx} | {item['question']} | {item['purpose']} | {item['status']} | "
                f"failed | {item['elapsed']} | - | - | {str(item['error'])[:120]} |"
            )
    return "\n".join(lines) + "\n"


def main() -> None:
    results: list[dict[str, Any]] = []
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    for case in CASES:
        result = request_chat(case)
        row = {
            "id": case["id"],
            "question": case["question"],
            "purpose": case["purpose"],
            "status": result["status"],
            "elapsed": result["elapsed"],
            "ok": result["ok"],
            "payload": result["payload"],
        }
        if result["ok"]:
            response = result["response"]
            texts = extract_reply_texts(response)
            row["response"] = response
            row["texts"] = texts
            row["evaluation"] = evaluate_reply(case, texts)
        else:
            row["error"] = result["error"]
        results.append(row)
    REPORT_JSON.write_text(
        json.dumps(
            {
                "api_url": API_URL,
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    REPORT_MD.write_text(build_markdown_report(results), encoding="utf-8")
    print(f"saved json -> {REPORT_JSON}")
    print(f"saved md   -> {REPORT_MD}")


if __name__ == "__main__":
    main()
