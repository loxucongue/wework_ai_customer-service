from __future__ import annotations

import base64
import json
import time
import urllib.request
from pathlib import Path
from typing import Any

try:
    from .test_conversation_store import append_test_conversations
except ImportError:
    from test_conversation_store import append_test_conversations


ROOT = Path(__file__).resolve().parents[2]
PUBLIC_FILE = ROOT / "projects/public/test-conversations.json"
REPORT_FILE = ROOT / "logs/lifecycle_unclear_need_test_report.json"
API_URL = "http://127.0.0.1:8000/chat"
IMAGE_URL = (
    "https://coze-coding-project.tos.coze.site/coze_storage_7641059342338457652/"
    "chat_images/user_uploaded_image_94bf3ee7.png?"
    "sign=1780476697-341f4fe0ca-0-7f6262b376e2e480885690a14adb2234e935701d75a4b23014bdbb072800ed2b"
)


def image_url_to_data_url(url: str) -> str:
    raw = urllib.request.urlopen(url, timeout=30).read()
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


def post_chat(payload: dict[str, Any]) -> tuple[dict[str, Any], float]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    start = time.perf_counter()
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data, time.perf_counter() - start


def quality_judgement(user_text: str, resp: dict[str, Any], expected: str, history: list[str]) -> dict[str, Any]:
    reply_items = resp.get("reply_messages") or []
    replies = [str(m.get("content") or "") for m in reply_items if m.get("type") != "image"]
    joined = "\n".join(replies)
    meta = resp.get("meta") or {}
    subflow = resp.get("subflow", "")
    intent = resp.get("intent", "")
    problems: list[str] = []
    good: list[str] = []

    if any(word in joined for word in ["系统", "工具", "知识库", "AI客服"]):
        problems.append("暴露系统/工具/AI过程")
    if "小贝" in joined:
        good.append("使用小贝人设")
    if "厦门" in user_text and ("厦门" in joined or subflow == "SF6_store_match"):
        good.append("承接城市门店问题")
    if "思明" in user_text and ("思明" in joined or "厦门" in joined):
        good.append("承接思明/厦门上下文")
    if user_text == "你帮我看看这个":
        image_info = meta.get("image_info") or {}
        if image_info.get("has_image") and image_info.get("visible_concerns"):
            good.append("图片理解识别到可见问题")
        if not any(term in joined for term in ["斑", "色沉", "肤色不均", "淡斑"]):
            problems.append("看图回复未承接斑点/色沉")
    if "多少钱" in user_text and not any(ch.isdigit() for ch in joined):
        problems.append("价格轮没有给出任何价格或预算参考")
    if "怕被坑" in user_text and not any(term in joined for term in ["资质", "产品", "售后", "隐形", "正规", "保障", "透明"]):
        problems.append("信任顾虑没有给判断维度")
    if "周六" in user_text and subflow not in {"SF9_appointment", "SF6_store_match"}:
        problems.append(f"预约轮路由异常：{subflow}")
    if any(term in joined for term in ["你在哪个城市", "所在城市", "哪个城市"]) and ("厦门" in "\n".join(history) or "思明" in "\n".join(history)):
        problems.append("已有城市上下文仍重复追问城市")
    if "http" in joined and "ocean-cloud-tos" in joined:
        problems.append("图片资料 URL 被当成文本输出")
    if "放心" in joined:
        problems.append("信任回复出现过强安抚词")
    if len(joined) > 280:
        problems.append("回复偏长")
    if not joined.strip():
        problems.append("空回复")

    return {
        "expected": expected,
        "intent": intent,
        "subflow": subflow,
        "good": good,
        "problems": problems,
        "reply_text": joined,
    }


def main() -> None:
    now_ms = int(time.time() * 1000)
    conv_id = f"codex_test_unclear_need_{now_ms}"
    title = time.strftime("Codex测试-无明确需求生命周期-%Y%m%d-%H%M")
    customer_id = f"lifecycle_unclear_need_{now_ms}"
    history: list[str] = []
    messages: list[dict[str, Any]] = []
    round_reports: list[dict[str, Any]] = []
    image_payload = image_url_to_data_url(IMAGE_URL)

    turns = [
        {
            "content": "你们厦门有店吗？我也不知道适合做什么，就是脸有点暗",
            "image": None,
            "expect": "门店问题和初步需求都要承接，不应只问客户在哪个城市。",
        },
        {
            "content": "我在思明附近，脸上还有点小斑点",
            "image": None,
            "expect": "匹配思明附近门店，同时承接斑点和暗沉需求。",
        },
        {
            "content": "你帮我看看这个",
            "image": IMAGE_URL,
            "expect": "识别图片斑点/色沉，给项目方向，不应泛泛追问。",
        },
        {
            "content": "那这个大概多少钱",
            "image": None,
            "expect": "继承淡斑/图片上下文问价格，不能让客户重新说项目。",
        },
        {
            "content": "如果做这个会不会没效果？大概预算也想先知道一下",
            "image": None,
            "expect": "多意图：同时处理效果顾虑和价格预算，不应只回答一半。",
        },
        {
            "content": "会不会没效果啊，我有点怕被坑",
            "image": None,
            "expect": "信任顾虑要给判断维度和保障说明，不转人工不过度免责。",
        },
        {
            "content": "厦门思明店离我近吗？如果近的话周六下午能去看看吗",
            "image": None,
            "expect": "多意图：同时处理门店距离/地址和周六预约意向。",
        },
    ]

    for index, turn in enumerate(turns, start=1):
        user_ts = now_ms + index * 60000
        user_message: dict[str, Any] = {
            "id": f"u_{index}_{now_ms}",
            "role": "user",
            "content": turn["content"],
            "timestamp": user_ts,
        }
        if turn.get("image"):
            user_message["imageUrl"] = turn["image"]
        messages.append(user_message)
        history.append(f"用户: {turn['content']}")

        payload: dict[str, Any] = {
            "content": turn["content"],
            "customer_id": customer_id,
            "corp_id": customer_id,
            "conversation_history": history[-10:],
            "user_id": 7294,
            "wechat": "yzm-yibingwen",
            "external_userid": "wmeERVIgAAmeVlaJ_YvK0exNEUMwPxTw",
        }
        if turn.get("image"):
            payload["file_image"] = image_payload

        try:
            resp, elapsed = post_chat(payload)
        except Exception as exc:
            resp = {
                "request_id": "",
                "reply_messages": [
                    {
                        "type": "text",
                        "order": 1,
                        "content": f"[测试调用失败] {type(exc).__name__}: {exc}",
                    }
                ],
                "meta": {},
            }
            elapsed = 0.0

        meta_raw = resp.get("meta") or {}
        first_meta = {
            "intent": resp.get("intent", ""),
            "scene": resp.get("scene", ""),
            "subflow": resp.get("subflow", ""),
            "requestId": resp.get("request_id", ""),
            "traceUrl": resp.get("trace_url", ""),
            "toolResultKeys": meta_raw.get("tool_result_keys", []),
            "profileUpdate": meta_raw.get("profile_update", {}),
            "eventUpdates": meta_raw.get("event_updates", []),
            "imageInfo": meta_raw.get("image_info", {}),
            "memoryLoaded": True,
            "raw": meta_raw,
        }

        for msg_index, item in enumerate(resp.get("reply_messages") or [], start=1):
            content = str(item.get("content") or "")
            messages.append(
                {
                    "id": f"a{msg_index}_{index}_{now_ms}",
                    "role": "assistant",
                    "content": content,
                    "contentType": item.get("type", "text"),
                    "timestamp": user_ts + 15000 + msg_index * 200,
                    "duration": int(elapsed * 1000),
                    "meta": first_meta if msg_index == 1 else None,
                }
            )
            if item.get("type") != "image":
                history.append(f"助手: {content}")

        judgement = quality_judgement(turn["content"], resp, turn["expect"], history)
        token_usage = meta_raw.get("token_usage") or {}
        round_reports.append(
            {
                "round": index,
                "user": turn["content"],
                "request_id": resp.get("request_id", ""),
                "elapsed_seconds": round(elapsed, 2),
                "token_usage": token_usage,
                **judgement,
            }
        )

    conversation = {
        "id": conv_id,
        "title": title,
        "messages": messages,
        "createdAt": now_ms,
        "updatedAt": int(time.time() * 1000),
    }

    append_test_conversations([conversation], path=PUBLIC_FILE)

    report = {
        "conversation_id": conv_id,
        "title": title,
        "public_file": str(PUBLIC_FILE),
        "rounds": round_reports,
        "summary": {
            "total_rounds": len(round_reports),
            "problem_rounds": [item["round"] for item in round_reports if item["problems"]],
            "total_tokens": sum(int((item.get("token_usage") or {}).get("total_tokens") or 0) for item in round_reports),
            "avg_elapsed_seconds": round(sum(float(item["elapsed_seconds"]) for item in round_reports) / max(len(round_reports), 1), 2),
        },
    }
    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "conversation_id": report["conversation_id"],
                "title": report["title"],
                "summary": report["summary"],
                "report_file": str(REPORT_FILE),
            },
            ensure_ascii=True,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
