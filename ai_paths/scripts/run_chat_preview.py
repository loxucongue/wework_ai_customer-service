from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_TURNS = [
    "\u4f60\u597d\uff0c\u6211\u60f3\u4e86\u89e3\u4e00\u4e0b\u8138\u4e0a\u7684\u6591\uff0c\u4e5f\u60f3\u77e5\u9053\u4e0a\u6d77\u6709\u6ca1\u6709\u95e8\u5e97",
    "\u4e3b\u8981\u662f\u70b9\u72b6\u6591\uff0c\u8fd9\u4e2a\u5927\u6982\u591a\u5c11\u94b1",
    "\u4f60\u4eec\u6b63\u89c4\u5417\uff1f\u4f1a\u4e0d\u4f1a\u5230\u5e97\u4e71\u6536\u8d39",
]


def post_chat(api_url: str, api_key: str, payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        api_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    start = time.perf_counter()
    with urllib.request.urlopen(request, timeout=240) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw), int((time.perf_counter() - start) * 1000)


def build_preview(api_url: str, api_key: str, customer_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    history: list[str] = []
    messages: list[dict[str, Any]] = []
    report: list[dict[str, Any]] = []
    now = int(time.time() * 1000)
    for idx, content in enumerate(DEFAULT_TURNS, start=1):
        messages.append({"id": f"u{idx}", "role": "user", "content": content, "timestamp": now + idx * 60000})
        result, elapsed_ms = post_chat(
            api_url,
            api_key,
            {
                "content": content,
                "customer_id": customer_id,
                "corp_id": "ww916da62a08044243",
                "user_id": 7294,
                "wechat": "yzm-yibingwen",
                "external_userid": "codex_preview_external_001",
                "conversation_history": history,
                "request_context": {"conversation_id": customer_id},
            },
        )
        meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
        tool_calls = meta.get("tool_calls") if isinstance(meta.get("tool_calls"), list) else []
        assistant_texts: list[str] = []
        for msg_index, item in enumerate(result.get("reply_messages") or [], start=1):
            if not isinstance(item, dict) or not item.get("content"):
                continue
            assistant_texts.append(str(item["content"]))
            message: dict[str, Any] = {
                "id": f"a{idx}_{msg_index}",
                "role": "assistant",
                "content": str(item["content"]),
                "contentType": item.get("type") or "text",
                "timestamp": now + idx * 60000 + msg_index * 1000,
                "duration": elapsed_ms,
            }
            if msg_index == 1:
                message["meta"] = {
                    "intent": result.get("intent") or "",
                    "scene": result.get("scene") or "",
                    "subflow": result.get("subflow") or "",
                    "requestId": result.get("request_id") or "",
                    "traceUrl": result.get("trace_url") or "",
                    "toolResultKeys": meta.get("tool_result_keys") or [],
                    "toolCalls": tool_calls,
                    "profileUpdate": meta.get("profile_update"),
                    "eventUpdates": meta.get("event_updates") or [],
                    "imageInfo": meta.get("image_info"),
                    "raw": {
                        "token_usage": meta.get("token_usage"),
                        "model_usage": meta.get("model_usage"),
                        "conversation_id": meta.get("conversation_id"),
                    },
                }
            messages.append(message)
        history.append(f"\u7528\u6237: {content}")
        for text in assistant_texts:
            history.append(f"\u52a9\u624b: {text}")
        history = history[-10:]
        report.append(
            {
                "turn": idx,
                "content": content,
                "elapsed_ms": elapsed_ms,
                "request_id": result.get("request_id"),
                "intent": result.get("intent"),
                "subflow": result.get("subflow"),
                "reply": assistant_texts,
                "tool_calls": [
                    {"node": call.get("node"), "name": call.get("name"), "error": call.get("error")}
                    for call in tool_calls
                    if isinstance(call, dict)
                ],
                "token_usage": meta.get("token_usage"),
            }
        )
    conversation = {
        "id": customer_id,
        "title": "Codex\u6d4b\u8bd5-\u670d\u52a1\u5668\u4e09\u8f6e\u5bf9\u8bdd",
        "messages": messages,
        "createdAt": now,
        "updatedAt": now + len(DEFAULT_TURNS) * 60000,
    }
    return {"conversations": [conversation]}, report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a three-turn chat preview and write frontend seed JSON.")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000/chat")
    parser.add_argument("--api-key", default=os.environ.get("AI_PATHS_API_KEY", ""))
    parser.add_argument("--customer-id", default="codex_server_preview_20260530")
    parser.add_argument("--conversation-output", default="projects/public/test-conversations.json")
    parser.add_argument("--report-output", default="logs/server_three_turn_report.json")
    args = parser.parse_args()
    conversation_payload, report = build_preview(args.api_url, args.api_key, args.customer_id)
    conversation_path = Path(args.conversation_output)
    report_path = Path(args.report_output)
    conversation_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    conversation_path.write_text(json.dumps(conversation_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
