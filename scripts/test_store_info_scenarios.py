from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import error, request


API_URL = "http://127.0.0.1:8000/chat"
BASE_PAYLOAD: dict[str, Any] = {
    "corp_id": "ww916da62a08044243",
    "user_id": 7294,
    "wechat": "yzm-yibingwen",
    "external_userid": "wmeERVIgAAmeVlaJ_YvK0exNEUMwPxTw",
}


SCENARIOS: list[dict[str, Any]] = [
    {"name": "泛门店缺城市", "content": "你们门店在哪里"},
    {"name": "城市门店列表-厦门", "content": "你们厦门有几家门店"},
    {"name": "城市门店列表-西安", "content": "西安有哪些门店"},
    {"name": "城市门店列表-重庆", "content": "重庆有没有门店"},
    {"name": "具体门店地址", "content": "厦门思明店地址发我"},
    {"name": "具体门店营业时间", "content": "厦门思明店几点关门"},
    {"name": "具体门店停车", "content": "厦门思明店有停车吗"},
    {"name": "具体门店导航", "content": "厦门思明店怎么过去"},
    {"name": "门店状态异常", "content": "中贸这边关门了吗"},
    {"name": "地区简称门店状态", "content": "小寨店还开吗"},
    {"name": "附近但缺精确定位", "content": "我在机场附近，哪家店近"},
    {
        "name": "门店追问补城市",
        "content": "我在上海",
        "conversation_history": [
            "用户: 你们门店在哪里",
            "助手: 小贝先帮你看附近门店，你方便说下所在城市或区域吗？",
        ],
    },
    {
        "name": "指代门店地址",
        "content": "刚刚那家地址发我",
        "conversation_history": [
            "用户: 我在厦门湖里",
            "助手: 厦门湖里这边可以看厦门二店或厦门百星。",
            "用户: 厦门百星吧",
        ],
        "confirmed_store_name": "厦门百星",
    },
    {
        "name": "预约前门店补全",
        "content": "我想下午过去但不知道哪家店",
    },
]


def post_json(url: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any] | str, float]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    started = time.perf_counter()
    try:
        with request.urlopen(req, timeout=90) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            elapsed = time.perf_counter() - started
            try:
                return resp.status, json.loads(text), elapsed
            except json.JSONDecodeError:
                return resp.status, text, elapsed
    except error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        elapsed = time.perf_counter() - started
        try:
            return exc.code, json.loads(text), elapsed
        except json.JSONDecodeError:
            return exc.code, text, elapsed
    except Exception as exc:
        elapsed = time.perf_counter() - started
        return 0, f"{type(exc).__name__}: {exc}", elapsed


def reply_text(data: dict[str, Any] | str) -> str:
    if not isinstance(data, dict):
        return str(data)
    messages = data.get("reply_messages") or []
    if not isinstance(messages, list):
        return ""
    return " | ".join(str(item.get("content") or "") for item in messages if isinstance(item, dict))


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    output_dir = Path("logs") / "store_scenarios"
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []

    for index, scenario in enumerate(SCENARIOS, start=1):
        payload = {
            **BASE_PAYLOAD,
            "content": scenario["content"],
            "customer_id": f"store-scenario-{stamp}-{index}",
            "conversation_history": scenario.get("conversation_history", []),
        }
        for key in ("confirmed_store_id", "confirmed_store_name", "store_id", "store_name"):
            if scenario.get(key) not in (None, ""):
                payload[key] = scenario[key]
        status, data, elapsed = post_json(API_URL, payload)
        item = {
            "scenario": scenario,
            "status": status,
            "elapsed_seconds": round(elapsed, 3),
            "reply": reply_text(data),
            "response": data,
        }
        results.append(item)
        meta = data.get("meta", {}) if isinstance(data, dict) else {}
        intents = meta.get("intents", []) if isinstance(meta, dict) else []
        skills = "/".join(str(intent.get("skill") or "") for intent in intents if isinstance(intent, dict))
        print(f"{index:02d}. {scenario['name']} [{status}] {elapsed:.1f}s {skills}")
        print(f"    用户: {scenario['content']}")
        print(f"    回复: {item['reply']}")

    report_path = output_dir / f"store_scenarios_{stamp}.json"
    report_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告: {report_path.resolve()}")


if __name__ == "__main__":
    main()
