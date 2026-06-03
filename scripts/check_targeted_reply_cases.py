# -*- coding: utf-8 -*-
"""定点验证价格兜底与多意图合并回复。"""

from __future__ import annotations

import json
import time
from pathlib import Path

import requests


API_URL = "http://127.0.0.1:8000/chat"
OUT_PATH = Path("logs/targeted_reply_cases_report.json")


CASES = [
    {
        "name": "price_fallback_spot",
        "content": "这种大概多少钱",
        "conversation_history": [
            "用户: 我发张照片你帮我看看",
            "小贝: 从你发的图片看，主要可见面部斑点和色沉，可以先按淡斑和肤色改善方向看。",
            "用户: 主要是想解决斑点，其次是肤色不均",
            "小贝: 根据你提到的点状斑和肤色不均，可以先考虑淡斑和肤色改善方向。",
        ],
    },
    {
        "name": "multi_intent_recap",
        "content": "去之前需要带什么，能不能化妆？另外把刚说的地址和价格帮我顺一下",
        "conversation_history": [
            "用户: 我人在上海，离徐家汇近一点的门店有吗",
            "小贝: 上海这边小贝先匹配到3家门店，你看哪家更方便：1. 上海徐汇店：上海徐汇区南丹东路300弄9号亚都商务楼 2. 上海静安店：上海市静安区陕西北路278号金鹰国际购物中心写字楼 3. 上海浦东店：上海市浦东新区张杨路655号福兴大厦",
            "用户: 那徐汇店周六下午有没有时间",
            "小贝: 徐汇店周六下午（6月6日）有时间，可选时段包括09:00、09:45、10:30。",
            "用户: 如果第一次过去，有没有先参考的价格",
            "小贝: 小贝这边暂时没查到淡斑的明确单次价格，不能拿别的项目价格代替报价。按你现在这类斑点方向，价格一般要先分是更偏整体肤色改善，还是更偏点状色素处理，再对应核价。",
        ],
    },
    {
        "name": "price_impatient_no_exact_price",
        "content": "我就是想知道大概多少钱，别一直问了",
        "conversation_history": [
            "用户: 我主要是点状斑，预算别太高",
            "小贝: 看你描述是点状斑为主，这类情况方向上会更偏针对性色素淡化类项目。",
            "小贝: 价格方面，暂时没查到该方向可直接引用的明确数字，我不拿别的项目乱报。",
        ],
    },
    {
        "name": "direct_project_direction",
        "content": "我不懂项目，你直接说我这个先看哪个方向",
        "conversation_history": [
            "用户: 我发张照片你帮我看看",
            "小贝: 从图片看，脸颊和眼周附近有点状斑点、片状色沉和肤色不均。",
            "用户: 主要想解决斑点，其次肤色不均",
        ],
    },
    {
        "name": "store_extra_fee_dispute",
        "content": "你们门店说要额外加钱，怎么说不一样",
        "conversation_history": [
            "用户: 厦门百星店吧",
            "小贝: 厦门百星店地址：厦门市湖里区枋湖西路189号，营业时间10:00-19:00。",
            "用户: 那我下午五点能约吗",
            "小贝: 今天下午5点在厦门百星店可以继续帮你确认。",
        ],
    },
    {
        "name": "trust_after_price",
        "content": "这么便宜会不会到店乱收费啊",
        "conversation_history": [
            "用户: 祛斑大概多少钱",
            "小贝: 目前暂时没有查到该方向明确的可直接引用价格。",
        ],
    },
]


def main() -> None:
    results = []
    for case in CASES:
        payload = {
            "content": case["content"],
            "customer_id": f"targeted_case_{case['name']}_{int(time.time())}",
            "corp_id": "ww916da62a08044243",
            "conversation_history": case["conversation_history"],
            "user_id": 7294,
            "wechat": "yzm-yibingwen",
            "external_userid": f"targeted_{case['name']}",
        }
        started = time.time()
        response = requests.post(API_URL, json=payload, timeout=240)
        elapsed_ms = int((time.time() - started) * 1000)
        response.raise_for_status()
        data = response.json()
        results.append(
            {
                "name": case["name"],
                "elapsed_ms": elapsed_ms,
                "intent": data.get("intent"),
                "subflow": data.get("subflow"),
                "replies": [item.get("content", "") for item in (data.get("reply_messages") or [])],
                "request_id": data.get("request_id"),
            }
        )

    OUT_PATH.write_text(json.dumps({"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "cases": results}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
