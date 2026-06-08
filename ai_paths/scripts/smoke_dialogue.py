from __future__ import annotations

import argparse
import json
from typing import Any

import requests


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a simple multi-turn chat smoke test.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--api-key", default="")
    parser.add_argument("--customer-id", required=True)
    parser.add_argument("--messages-json", default="", help="JSON array of messages")
    parser.add_argument("--messages-file", default="", help="UTF-8 JSON file containing a string array")
    parser.add_argument("--message", action="append", default=[], help="One message per argument; can be passed multiple times")
    args = parser.parse_args()

    if args.messages_file:
        with open(args.messages_file, "r", encoding="utf-8") as fp:
            messages = json.load(fp)
        if not isinstance(messages, list) or not all(isinstance(item, str) for item in messages):
            raise SystemExit("messages-file 必须是 UTF-8 字符串数组 JSON 文件")
    elif args.messages_json:
        messages = json.loads(args.messages_json)
        if not isinstance(messages, list) or not all(isinstance(item, str) for item in messages):
            raise SystemExit("messages-json 必须是字符串数组")
    else:
        messages = list(args.message)
    if not messages:
        raise SystemExit("必须提供 messages-json 或至少一个 --message")

    headers = {"Content-Type": "application/json"}
    if args.api_key:
        headers["Authorization"] = f"Bearer {args.api_key}"

    for index, message in enumerate(messages, 1):
        payload: dict[str, Any] = {
            "content": message,
            "customer_id": args.customer_id,
            "corp_id": args.customer_id,
            "conversation_history": messages[: index - 1],
        }
        response = requests.post(args.url, headers=headers, json=payload, timeout=180)
        print(f"TURN {index} STATUS {response.status_code}")
        try:
            body = response.json()
        except Exception:
            print(response.text)
            print("---")
            continue
        print(json.dumps(body.get("reply_messages", body), ensure_ascii=False, indent=2))
        print("---")


if __name__ == "__main__":
    main()
