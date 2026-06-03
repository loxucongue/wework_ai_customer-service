from __future__ import annotations

import json
import time
import urllib.request


URL = "http://47.252.81.104/api/ai/chat"


CASES = [
    {
        "name": "price_fallback_spot_public",
        "payload": {
            "content": "我主要是点状斑，预算别太高",
            "customer_id": "public_smoke_price_utf8_20260601_001",
            "corp_id": "public_smoke_price_utf8_20260601_001",
            "conversation_history": [],
        },
    },
    {
        "name": "multi_intent_recap_public",
        "payload": {
            "content": "去之前需要带什么，能不能化妆？另外把刚说的地址和价格帮我顺一下",
            "customer_id": "public_smoke_multi_utf8_20260601_001",
            "corp_id": "public_smoke_multi_utf8_20260601_001",
            "conversation_history": [
                "用户: 我在上海，想周六下午过去看看",
                "小贝: 上海徐汇店这边周六下午可以帮你看看可约时间。",
                "用户: 我主要是点状斑，预算别太高",
                "小贝: 徐汇店地址是上海徐汇区南丹东路300弄9号亚都商务楼；淡斑方向暂未查到明确单次价格。",
            ],
        },
    },
    {
        "name": "soft_fee_trust_public",
        "payload": {
            "content": "这么便宜会不会到店乱收费啊",
            "customer_id": "public_smoke_soft_fee_utf8_20260602_001",
            "corp_id": "public_smoke_soft_fee_utf8_20260602_001",
            "conversation_history": [
                "用户: 祛斑大概多少钱",
                "小贝: 目前暂时没有查到该方向明确的可直接引用价格。",
            ],
        },
    },
]


def main() -> None:
    for case in CASES:
        data = json.dumps(case["payload"], ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            URL,
            data=data,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                text = response.read().decode("utf-8", errors="replace")
                print(f"CASE {case['name']} STATUS {response.status} SECONDS {time.perf_counter() - start:.2f}")
                print(text)
        except Exception as exc:
            print(f"CASE {case['name']} ERROR {type(exc).__name__}: {exc} SECONDS {time.perf_counter() - start:.2f}")
        print("\n---ENDCASE---\n")


if __name__ == "__main__":
    main()
