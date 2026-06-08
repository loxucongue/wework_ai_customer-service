from __future__ import annotations

import json
import time
import urllib.request


API_URL = "http://127.0.0.1:8000/chat/workflow-compatible"


def main() -> None:
    history: list[dict[str, object]] = []
    customer_id = f"smoke_store_realtime_only_{int(time.time())}"
    turns = [
        "长沙有你们门店吧？都在哪些位置？",
        "我在长沙岳麓区西湖公园边上，别都发给我，直接推荐最近的。",
        "把这家具体地址、停车、营业时间一起说清楚。",
    ]
    for index, content in enumerate(turns, start=1):
        payload = {
            "workflow_id": "xiaobei-default",
            "parameters": {
                "customer_id": customer_id,
                "external_userid": customer_id,
                "corp_id": "ent-753d018266f7453285311ce1d5ed0d94",
                "user_id": "DY1032",
                "wechat": "DY1032",
                "content": {
                    "content": content,
                    "msgid": f"{customer_id}_{index}",
                    "msgtime": int(time.time() * 1000),
                    "msgtype": "text",
                },
                "messages": history,
                "request_context": {
                    "conversation_id": customer_id,
                    "customer_id": customer_id,
                    "test_customer": True,
                },
            },
        }
        request = urllib.request.Request(
            API_URL,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=240) as response:
            body = json.loads(response.read().decode("utf-8"))
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        messages = data.get("reply_messages") or []
        print(f"\nTURN {index}: {content}")
        print(json.dumps(messages, ensure_ascii=False, indent=2))
        history.append({"direction": "customer", "content": content, "msgtype": "text", "msgtime": int(time.time() * 1000)})
        for message in messages:
            msg_content = message.get("content")
            text = msg_content.get("text") if isinstance(msg_content, dict) else msg_content
            history.append({"direction": "service", "content": text or "", "msgtype": "text", "msgtime": int(time.time() * 1000)})


if __name__ == "__main__":
    main()
