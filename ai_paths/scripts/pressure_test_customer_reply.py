from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from urllib import request


API_URL = "http://127.0.0.1:8000/chat"
REPORT_DIR = Path("ai_paths/logs/regression")


BASE_PAYLOAD: dict[str, Any] = {
    "customer_id": "pressure_customer_001",
    "corp_id": "ww916da62a08044243",
    "user_id": 7294,
    "wechat": "yzm-yibingwen",
    "external_userid": "wmeERVIgAAmeVlaJ_YvK0exNEUMwPxTw",
}


CASES: list[dict[str, Any]] = [
    {
        "name": "generic_project",
        "content": "了解一下项目",
        "history": [],
        "expect_any": ["想改善", "项目方向", "斑", "痘", "毛孔", "暗沉"],
        "forbid": ["光子嫩肤", "皮秒", "营业执照", "转人工"],
    },
    {
        "name": "image_followup_text",
        "content": "我脸上这种能解决吗",
        "history": ["用户上传了一张面颊照片", "图片描述：面颊和眼周附近有点状斑点、片状色沉、肤色不均。"],
        "expect_any": ["点状", "色沉", "肤色不均", "能改善", "方向"],
        "forbid": ["再发照片", "看不出来", "营业执照"],
    },
    {
        "name": "vague_price",
        "content": "那这个大概多少钱",
        "history": ["用户：我脸上这种能解决吗", "助手：小贝看到主要是点状斑点和色沉，可以先看淡化色素和肤色改善方向。"],
        "expect_any": ["具体", "项目", "配置", "不乱报", "预算"],
        "forbid": ["光子嫩肤新客", "营业执照", "转人工"],
    },
    {
        "name": "ad_price_check",
        "content": "广告上说199能祛斑，是真的吗，有没有其他收费",
        "history": [],
        "expect_any": ["199", "包含", "项目", "次数", "加项", "预约金"],
        "forbid": ["肯定", "保证", "营业执照"],
    },
    {
        "name": "license_request",
        "content": "你们有营业执照吗？直接发我看",
        "history": [],
        "expect_any": ["正规", "资质", "核验", "官方", "门店"],
        "forbid": ["http", "https", "我把营业执照发你", "发送营业执照", "营业执照发你", "匹配到", "你看哪家更方便", "\n1."],
    },
    {
        "name": "trust_rude",
        "content": "我感觉你们就是骗子，一点都不靠谱",
        "history": [],
        "expect_any": ["理解", "谨慎", "资质", "核验", "产品来源", "服务"],
        "forbid": ["转人工", "http", "https"],
    },
    {
        "name": "hard_complaint",
        "content": "你们骗钱，我要投诉退款，马上给我处理",
        "history": [],
        "expect_any": ["专业", "协助", "记录", "核对", "处理"],
        "forbid": ["小贝可以直接退款", "继续看项目"],
    },
    {
        "name": "competitor",
        "content": "别家说一次就能淡很多，还只要299，你们为什么贵",
        "history": [],
        "expect_any": ["对比", "一次", "299", "产品", "部位", "次数", "售后"],
        "forbid": ["他们不正规", "肯定有问题", "保证", "胶原类", "PDRN", "新客体验价", "活动价", "日常单次价"],
    },
    {
        "name": "store_city",
        "content": "我在上海，你们门店在哪里",
        "history": [],
        "expect_any": ["上海", "门店", "地址"],
        "forbid": ["厦门思明", "没有匹配到"],
    },
    {
        "name": "store_unknown_city",
        "content": "重庆有没有门店",
        "history": [],
        "expect_any": ["重庆", "门店"],
        "forbid": ["厦门思明", "上海"],
    },
    {
        "name": "appointment_existing",
        "content": "帮我查一下我明天是不是约了",
        "history": [],
        "expect_any": ["预约", "记录", "核对"],
        "forbid": ["项目方向", "价格或门店信息"],
    },
    {
        "name": "appointment_time_without_store",
        "content": "周六下午能去吗",
        "history": [],
        "expect_any": ["门店", "城市", "周六", "时间"],
        "forbid": ["直接帮你约好了", "已预约成功"],
    },
    {
        "name": "after_sales_mild",
        "content": "我做完后有点泛红，这正常吗",
        "history": ["用户之前做过光电项目"],
        "expect_any": ["泛红", "做完第几天", "照片", "加重"],
        "forbid": ["正常的", "不用担心", "肯定没事"],
    },
    {
        "name": "after_sales_serious",
        "content": "做完以后流脓发烧了，还很疼",
        "history": [],
        "expect_any": ["专业", "协助", "发烧", "流脓", "处理"],
        "forbid": ["正常", "不用管"],
    },
    {
        "name": "unreasonable_price",
        "content": "别废话，最低价给我，不然我就去别家",
        "history": [],
        "expect_any": ["预算", "配置", "不能", "底价", "统一"],
        "forbid": ["最低价是", "保证最低", "他们家"],
    },
    {
        "name": "multi_intent",
        "content": "我在上海，想改善脸上的斑，大概多少钱，你们靠谱吗",
        "history": [],
        "expect_any": ["上海", "斑", "价格", "正规", "资质"],
        "forbid": ["http", "https", "营业执照发你", "胶原类", "PDRN", "喷雾", "洁面", "精华液", "晶钻霜"],
    },
]


def post_chat(payload: dict[str, Any]) -> tuple[dict[str, Any], float]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(API_URL, data=body, headers={"Content-Type": "application/json"}, method="POST")
    started = time.perf_counter()
    with request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data, time.perf_counter() - started


def judge(case: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    texts = [str(msg.get("content") or "") for msg in response.get("reply_messages", [])]
    joined = "\n".join(texts)
    expect_any = case.get("expect_any") or []
    forbid = case.get("forbid") or []
    missing_expect = bool(expect_any) and not any(term in joined for term in expect_any)
    forbidden_hit = [term for term in forbid if term in joined]
    empty = not joined.strip()
    too_generic = any(bad in joined for bad in ["小贝先按你当前问题帮你看", "小贝先按你这句来理解"])
    passed = not empty and not missing_expect and not forbidden_hit and not too_generic
    return {
        "passed": passed,
        "missing_expect": missing_expect,
        "forbidden_hit": forbidden_hit,
        "too_generic": too_generic,
        "reply_text": joined,
    }


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for index, case in enumerate(CASES, start=1):
        payload = {
            **BASE_PAYLOAD,
            "customer_id": f"pressure_{int(time.time())}_{index}",
            "content": case["content"],
            "conversation_history": case.get("history", []),
        }
        try:
            response, elapsed = post_chat(payload)
            verdict = judge(case, response)
            results.append(
                {
                    "case": case["name"],
                    "content": case["content"],
                    "elapsed_sec": round(elapsed, 2),
                    "intent": response.get("intent"),
                    "subflow": response.get("subflow"),
                    "reply_messages": response.get("reply_messages", []),
                    "token_usage": response.get("meta", {}).get("token_usage", {}),
                    "trace_url": response.get("trace_url"),
                    "verdict": verdict,
                }
            )
        except Exception as exc:
            results.append({"case": case["name"], "content": case["content"], "error": repr(exc), "verdict": {"passed": False}})

    summary = {
        "total": len(results),
        "passed": sum(1 for item in results if item.get("verdict", {}).get("passed")),
        "failed": [item["case"] for item in results if not item.get("verdict", {}).get("passed")],
    }
    report = {"summary": summary, "results": results}
    path = REPORT_DIR / f"pressure_customer_reply_{int(time.time())}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(path), **summary}, ensure_ascii=False, indent=2))
    for item in results:
        status = "PASS" if item.get("verdict", {}).get("passed") else "FAIL"
        print(f"\n[{status}] {item['case']} | {item.get('elapsed_sec')}s | {item.get('intent')} / {item.get('subflow')}")
        print(item.get("verdict", {}).get("reply_text") or item.get("error", ""))
        if not item.get("verdict", {}).get("passed"):
            print("verdict:", json.dumps(item.get("verdict", {}), ensure_ascii=False))


if __name__ == "__main__":
    main()
