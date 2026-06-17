from __future__ import annotations

import argparse
import base64
import json
import time
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_URL = "http://47.252.81.104/api/ai/reply/workflow-compatible"


DEFAULT_PARAMETERS = {
    "category_id": "居家产品",
    "customer_id": "20615704",
    "external_userid": "wmanzqsqaaygjwicitvmos657x39lqtg",
    "customer_add_wechat_id": "20615704",
    "user_id": "7294",
    "wechat": "CS001",
    "corp_id": "ww943af61cd5d2afe4",
}


def main() -> None:
    args = parse_args()
    config = load_config(args)
    turns = normalize_turns(config.get("turns") or config.get("messages") or [])
    if not turns:
        raise SystemExit("No turns found. Provide JSON with a turns array.")

    url = str(config.get("url") or args.url or DEFAULT_URL)
    workflow_id = str(config.get("workflow_id") or args.workflow_id or "xiaobei-default")
    parameters = {**DEFAULT_PARAMETERS, **dict(config.get("parameters") or {})}
    history: list[dict[str, Any]] = list(config.get("history") or [])
    results: list[dict[str, Any]] = []

    for index, turn in enumerate(turns, start=1):
        if isinstance(turn, dict):
            user_text = str(turn.get("content") or turn.get("text") or "")
        else:
            user_text = str(turn)
        payload = build_payload(
            workflow_id=workflow_id,
            parameters=parameters,
            user_text=user_text,
            history=history,
            index=index,
        )
        started = time.perf_counter()
        response = post_json(url, payload, timeout=args.timeout)
        elapsed = round(time.perf_counter() - started, 2)
        reply_messages = response.get("data", {}).get("reply_messages") or []

        results.append(
            {
                "turn": index,
                "user": user_text,
                "elapsed": elapsed,
                "trace_id": response.get("execute_id") or response.get("data", {}).get("trace_id"),
                "reply_messages": reply_messages,
            }
        )
        append_history(history, "customer", "text", user_text)
        for message in reply_messages:
            append_reply_to_history(history, message)

        if args.print_each:
            print(json.dumps(results[-1], ensure_ascii=False, indent=2))

    output_path = Path(args.output) if args.output else None
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run workflow-compatible dialog tests without putting Chinese literals in PowerShell commands. "
            "Use --input for a UTF-8 JSON file or --payload-base64 for an ASCII-safe UTF-8 JSON payload."
        )
    )
    parser.add_argument("--input", help="UTF-8 JSON file. Shape: {\"turns\": [\"...\"]}.")
    parser.add_argument("--payload-base64", help="Base64 of UTF-8 JSON payload. Shape: {\"turns\": [\"...\"]}.")
    parser.add_argument("--output", help="Write UTF-8 JSON results to this path.")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--workflow-id", default="xiaobei-default")
    parser.add_argument("--timeout", type=int, default=150)
    parser.add_argument("--print-each", action="store_true")
    return parser.parse_args()


def load_config(args: argparse.Namespace) -> dict[str, Any]:
    if args.input:
        return json.loads(Path(args.input).read_text(encoding="utf-8-sig"))
    if args.payload_base64:
        raw = base64.b64decode(args.payload_base64).decode("utf-8")
        return json.loads(raw)
    raise SystemExit("Provide --input or --payload-base64.")


def normalize_turns(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


def build_payload(
    *,
    workflow_id: str,
    parameters: dict[str, Any],
    user_text: str,
    history: list[dict[str, Any]],
    index: int,
) -> dict[str, Any]:
    now_ms = int(time.time() * 1000)
    msgid = f"utf8_dialog_{now_ms}_{index}"
    return {
        "workflow_id": workflow_id,
        "parameters": {
            **parameters,
            "content": {
                "content": user_text,
                "msgid": msgid,
                "msgtime": now_ms,
                "msgtype": "text",
            },
            "messages": [
                *history,
                {
                    "direction": "customer",
                    "msgtype": "text",
                    "content": user_text,
                    "msgid": msgid,
                    "msgtime": now_ms,
                },
            ],
        },
    }


def post_json(url: str, payload: dict[str, Any], *, timeout: int) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def append_history(history: list[dict[str, Any]], direction: str, msgtype: str, content: Any) -> None:
    history.append(
        {
            "direction": direction,
            "msgtype": msgtype,
            "type": msgtype,
            "content": content,
            "msgtime": int(time.time() * 1000),
        }
    )


def append_reply_to_history(history: list[dict[str, Any]], message: Any) -> None:
    if not isinstance(message, dict):
        return
    msgtype = str(message.get("type") or "text")
    content = message.get("content")
    if msgtype == "text" and isinstance(content, dict):
        content = content.get("text", "")
    append_history(history, "staff", msgtype, content)


if __name__ == "__main__":
    main()
