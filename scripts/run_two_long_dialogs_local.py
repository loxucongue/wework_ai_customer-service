from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ai_paths"))

from app.main import chat_runtime, memory_store, sqlite_store
from app.schemas import ChatRequest


BASE = {
    "corp_id": "ww943af61cd5d2afe4",
    "user_id": 7294,
    "wechat": "CS001",
    # Do not use a real external_userid here; this script is for isolated context tests.
    "external_userid": None,
}


SCENARIOS = [
    (
        "深圳位置与预约承接",
        "codex-long-dialog-a-20260615",
        [
            "你好",
            "我想看下脸上的斑",
            "我在深圳",
            "南山科技园附近",
            "那离我近一点的是哪家",
            "好的",
            "周六下午3点可以吗",
            "嗯，那地址再发我一下",
        ],
    ),
    (
        "厦门效果与价格承接",
        "codex-long-dialog-b-20260615",
        [
            "了解一下",
            "我脸上黑色素比较多可以做吗",
            "你们用的是什么方法",
            "我想看看做完的效果",
            "多少钱，我看到广告说199",
            "我在厦门机场附近",
            "明天下午可以去吗",
            "可以，帮我安排近一点的",
        ],
    ),
]


def _text_from_response(response) -> list[str]:
    rows: list[str] = []
    for msg in response.reply_messages:
        data = msg.model_dump()
        mtype = data.get("type")
        content = data.get("content")
        if isinstance(content, dict):
            text = content.get("text") or content.get("url") or content.get("order_id") or content.get("handoff_reason")
        else:
            text = content
        rows.append(f"{mtype}: {text}")
    return rows


async def run() -> None:
    sqlite_store.initialize()
    report: list[dict[str, object]] = []
    for title, customer_id, turns in SCENARIOS:
        memory_store.clear(customer_id)
        history: list[str] = []
        dialog: list[dict[str, object]] = []
        for content in turns:
            req = ChatRequest(
                content=content,
                customer_id=customer_id,
                corp_id=BASE["corp_id"],
                conversation_history=history[-10:],
                user_id=BASE["user_id"],
                wechat=BASE["wechat"],
                external_userid=BASE["external_userid"],
            )
            resp = await chat_runtime.run_chat(req)
            replies = _text_from_response(resp)
            dialog.append(
                {
                    "customer": content,
                    "assistant": replies,
                    "request_id": resp.request_id,
                    "intent": resp.intent,
                    "scene": resp.scene,
                    "subflow": resp.subflow,
                    "tool_result_keys": resp.meta.get("tool_result_keys") if isinstance(resp.meta, dict) else None,
                }
            )
            history.append(f"用户: {content}")
            for reply in replies:
                history.append(f"小贝: {reply}")
        report.append({"title": title, "customer_id": customer_id, "dialog": dialog})
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(run())
