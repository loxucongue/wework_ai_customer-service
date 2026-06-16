from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ai_paths"))

from app.main import chat_runtime, memory_store, repository, sqlite_store, trace_logger
from app.config import get_settings
from app.schemas import ChatRequest
from app.services.platform_agent_client import PlatformAgentClient


BASE = {
    "corp_id": "ww943af61cd5d2afe4",
    "user_id": 7294,
    "wechat": "CS001",
    "customer_id": "20615704",
    "customer_add_wechat_id": "20615704",
    "external_userid": "wmanzqsqaaygjwicitvmos657x39lqtg",
}


SCENARIOS: list[dict[str, Any]] = [
    {
        "title": "厦门机场附近门店匹配",
        "conversation_id": "real-store-xiamen-20260616",
        "turns": [
            "你好",
            "我在厦门高崎机场附近",
            "那离我近一点的门店是哪家",
            "地址发我",
            "明天下午可以去吗",
            "那先帮我安排一下，我晚点把电话发你",
        ],
    },
    {
        "title": "深圳科技园附近门店匹配",
        "conversation_id": "real-store-shenzhen-20260616",
        "turns": [
            "你好",
            "我在深圳南山科技园附近",
            "先给我推荐最近门店",
            "把地址发我",
            "周六下午三点可以吗",
            "那你先帮我登记一下",
        ],
    },
]


def _msg_text(data: Any) -> str:
    if isinstance(data, dict):
        return (
            str(data.get("text") or "")
            or str(data.get("url") or "")
            or str(data.get("order_id") or "")
            or str(data.get("store_id") or "")
            or str(data.get("handoff_reason") or "")
        )
    return str(data or "")


async def _run_scenario(scenario: dict[str, Any]) -> dict[str, Any]:
    conversation_id = scenario["conversation_id"]
    memory_store.clear(conversation_id)
    history: list[str] = []
    rows: list[dict[str, Any]] = []
    request_context = await _request_context()

    for turn in scenario["turns"]:
        req = ChatRequest(
            content=turn,
            customer_id=conversation_id,
            corp_id=BASE["corp_id"],
            conversation_history=history[-12:],
            user_id=BASE["user_id"],
            wechat=BASE["wechat"],
            external_userid=BASE["external_userid"],
            customer_add_wechat_id=BASE["customer_add_wechat_id"],
            request_context=request_context,
        )
        response = await chat_runtime.run_chat(req)
        run_detail = repository.get_run(response.request_id)
        raw_log = trace_logger.read_run(response.request_id)
        messages = [msg.model_dump() for msg in response.reply_messages]
        reply_types = [msg.get("type") for msg in messages]
        tool_calls = run_detail.get("tool_calls") or []
        output_snapshot = run_detail.get("output_snapshot") or {}
        rows.append(
            {
                "customer": turn,
                "request_id": response.request_id,
                "intent": response.intent,
                "scene": response.scene,
                "subflow": response.subflow,
                "reply_messages": messages,
                "reply_types": reply_types,
                "reply_texts": [_msg_text(msg.get("content")) for msg in messages],
                "tool_calls": tool_calls,
                "tool_result_keys": output_snapshot.get("tool_result_keys"),
                "store_lookup_status": output_snapshot.get("store_lookup_status"),
                "distance_lookup": output_snapshot.get("distance_lookup"),
                "appointment_opening": output_snapshot.get("appointment_opening"),
                "planner_route": output_snapshot.get("planner_route"),
                "raw_log": raw_log,
            }
        )
        history.append(f"客户: {turn}")
        for reply in messages:
            history.append(f"小贝: {_msg_text(reply.get('content'))}")

    memory_store.clear(conversation_id)
    return {
        "title": scenario["title"],
        "conversation_id": conversation_id,
        "turns": rows,
    }


async def _request_context() -> dict[str, Any]:
    client = PlatformAgentClient(get_settings())
    info = client.get_customer_info(
        user_id=BASE["user_id"],
        corp_id=BASE["corp_id"],
        wechat=BASE["wechat"],
        external_userid=BASE["external_userid"],
    )
    return {
        "customer_id": BASE["customer_id"],
        "customer_add_wechat_id": BASE["customer_add_wechat_id"],
        "external_userid": BASE["external_userid"],
        "user_id": BASE["user_id"],
        "wechat": BASE["wechat"],
        "corp_id": BASE["corp_id"],
        "platform_customer_id": str(info.get("id") or ""),
        "kind": info.get("kind"),
    }


async def main() -> None:
    sqlite_store.initialize()
    results = []
    for scenario in SCENARIOS:
        results.append(await _run_scenario(scenario))
    out_dir = ROOT / "logs" / "real_store_dialogs"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"real_store_dialogs_{stamp}.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out_path)


if __name__ == "__main__":
    asyncio.run(main())
