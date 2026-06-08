from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:
    from .test_conversation_store import append_test_conversations
except ImportError:
    from test_conversation_store import append_test_conversations


DEFAULT_API_URL = "http://47.252.81.104/api/ai/chat/workflow-compatible"
DEFAULT_WORKFLOW_ID = "xiaobei-default"
DEFAULT_CORP_ID = "ent-753d018266f7453285311ce1d5ed0d94"
DEFAULT_USER_ID = "DY1032"
DEFAULT_WECHAT = "DY1032"


SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "appointment-standard",
        "title": "Codex预约长对话-标准预约确认",
        "turns": [
            "我已经添加了你，现在我们可以开始聊天了。",
            "我在厦门机场附近，想去店里看看",
            "先给我推荐最近的一家",
            "停车方便吗？",
            "明天下午有时间吗？",
            "下午一点可以吗？",
            "罗学聪",
            "19976988097",
            "10元是做什么的？",
            "到店要带什么，要不要素颜？",
            "好的，那我明天下午去",
        ],
    },
    {
        "id": "appointment-deposit-change",
        "title": "Codex预约长对话-预约金顾虑与改时间",
        "turns": [
            "你好",
            "我在厦门，想约个明天到店咨询",
            "湖里区这边方便点",
            "厦门百星离我近吗？",
            "明天下午有时间吗？",
            "下午三点多一点可以吗？",
            "我不想先交定金，到店付全款行不行？",
            "那10元如果我临时有事能改吗？",
            "我叫陈阿姨",
            "13800138000",
            "可以先帮我登记吗？",
        ],
    },
    {
        "id": "appointment-followup-natural",
        "title": "Codex预约长对话-补槽自然表达与到店准备",
        "turns": [
            "你好呀",
            "我在厦门机场附近，昨天你们说百星离得近",
            "地址再发我一个",
            "明天上午没空，明天下午可以吗？",
            "1点可以吗？",
            "就是罗学聪啊",
            "19976988097",
            "我朋友能陪我一起过去吗？",
            "到店大概要多久？",
            "我怕找不到地方",
            "那就先这样",
        ],
    },
]


def _make_request(
    api_url: str,
    api_key: str,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], int, int]:
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
    try:
        with urllib.request.urlopen(request, timeout=240) as response:
            raw = response.read().decode("utf-8")
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        status = int(exc.code)
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {"code": status, "msg": "invalid json response", "raw": raw}
    return parsed, elapsed_ms, status


def _assistant_display_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    message_type = str(message.get("type") or "text")
    if isinstance(content, dict):
        if message_type == "image":
            return str(content.get("url") or content.get("text") or "[图片]").strip()
        if message_type == "human_handoff":
            return str(content.get("handoff_reason") or "").strip()
        if message_type == "appointment_push":
            text = str(content.get("text") or "").strip()
            return text or "[预约推送]"
        if message_type == "book_order":
            order_id = str(content.get("order_id") or "").strip()
            return f"[预约订单:{order_id}]" if order_id else "[预约订单]"
        return str(content.get("text") or "").strip()
    return str(content or "").strip()


def _frontend_message_content(message: dict[str, Any]) -> str | dict[str, Any]:
    content = message.get("content")
    if isinstance(content, dict):
        return content
    return str(content or "")


def _append_history_message(history: list[dict[str, Any]], direction: str, content: str) -> None:
    history.append(
        {
            "direction": direction,
            "content": content,
            "msgtype": "text",
            "msgtime": int(time.time() * 1000),
        }
    )


def _build_payload(
    workflow_id: str,
    customer_id: str,
    turn_text: str,
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    now_ms = int(time.time() * 1000)
    return {
        "workflow_id": workflow_id,
        "parameters": {
            "customer_id": customer_id,
            "external_userid": customer_id,
            "corp_id": DEFAULT_CORP_ID,
            "user_id": DEFAULT_USER_ID,
            "wechat": DEFAULT_WECHAT,
            "content": {
                "content": turn_text,
                "msgid": f"{customer_id}_{now_ms}_external",
                "msgtime": now_ms,
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


def _extract_reply_messages(response: dict[str, Any]) -> list[dict[str, Any]]:
    data = response.get("data")
    if isinstance(data, dict) and isinstance(data.get("reply_messages"), list):
        return [item for item in data["reply_messages"] if isinstance(item, dict)]
    if isinstance(response.get("reply_messages"), list):
        return [item for item in response["reply_messages"] if isinstance(item, dict)]
    return []


def _run_scenario(
    api_url: str,
    api_key: str,
    workflow_id: str,
    scenario: dict[str, Any],
    run_suffix: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    base_id = str(scenario["id"])
    conversation_id = f"codex-{base_id}-{run_suffix}"
    customer_id = f"codex_{base_id}_{run_suffix}"
    title = f"{scenario['title']}（{run_suffix}）"
    turns = list(scenario["turns"])
    created_at = int(time.time() * 1000)
    history: list[dict[str, Any]] = []
    frontend_messages: list[dict[str, Any]] = []
    report_turns: list[dict[str, Any]] = []
    message_index = 1

    for turn_no, turn_text in enumerate(turns, start=1):
        user_ts = int(time.time() * 1000)
        frontend_messages.append(
            {
                "id": f"{conversation_id}_u_{message_index}",
                "role": "user",
                "content": turn_text,
                "timestamp": user_ts,
            }
        )
        message_index += 1
        _append_history_message(history, "customer", turn_text)

        payload = _build_payload(workflow_id, customer_id, turn_text, history)
        response, elapsed_ms, status = _make_request(api_url, api_key, payload)
        reply_messages = _extract_reply_messages(response)
        execute_id = str(response.get("execute_id") or response.get("request_id") or "")
        assistant_texts: list[str] = []

        if not reply_messages:
            error_text = str(response.get("msg") or response.get("error") or response.get("raw") or "接口未返回可见回复")
            frontend_messages.append(
                {
                    "id": f"{conversation_id}_a_{message_index}",
                    "role": "assistant",
                    "content": f"[接口异常 {status}] {error_text[:180]}",
                    "contentType": "error",
                    "timestamp": int(time.time() * 1000),
                    "duration": elapsed_ms,
                    "meta": {"requestId": execute_id, "raw": {"workflow_response": response}},
                }
            )
            message_index += 1
        else:
            for idx, item in enumerate(reply_messages, start=1):
                content_type = str(item.get("type") or "text")
                content = _frontend_message_content(item)
                visible_text = _assistant_display_text(item)
                if visible_text and content_type == "text":
                    assistant_texts.append(visible_text)
                    _append_history_message(history, "service", visible_text)
                frontend_message: dict[str, Any] = {
                    "id": f"{conversation_id}_a_{message_index}",
                    "role": "assistant",
                    "content": content,
                    "contentType": content_type,
                    "timestamp": int(time.time() * 1000) + idx,
                    "duration": elapsed_ms,
                }
                if idx == 1:
                    frontend_message["meta"] = {
                        "requestId": execute_id,
                        "raw": {"workflow_response": response},
                    }
                frontend_messages.append(frontend_message)
                message_index += 1

        report_turns.append(
            {
                "turn": turn_no,
                "user": turn_text,
                "status": status,
                "elapsed_ms": elapsed_ms,
                "execute_id": execute_id,
                "reply_messages": reply_messages,
                "assistant_texts": assistant_texts,
            }
        )

    conversation = {
        "id": conversation_id,
        "title": title,
        "createdAt": created_at,
        "updatedAt": int(time.time() * 1000),
        "messages": frontend_messages,
    }
    report = {
        "id": conversation_id,
        "customer_id": customer_id,
        "title": title,
        "turn_count": len(turns),
        "turns": report_turns,
    }
    return conversation, report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run online appointment long dialogues and append to frontend test data.")
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--api-key", default=os.environ.get("AI_EXTERNAL_API_KEY", ""))
    parser.add_argument("--workflow-id", default=DEFAULT_WORKFLOW_ID)
    parser.add_argument("--conversation-output", default="projects/public/test-conversations.json")
    parser.add_argument("--report-output", default="logs/online_appointment_long_flows_report.json")
    parser.add_argument("--scenario-id", action="append", default=[])
    parser.add_argument("--run-suffix", default=time.strftime("%Y%m%d%H%M%S"))
    args = parser.parse_args()

    conversations: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []
    conversation_path = Path(args.conversation_output)
    report_path = Path(args.report_output)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    selected_ids = {item for item in args.scenario_id if item}
    scenarios = [item for item in SCENARIOS if not selected_ids or item["id"] in selected_ids]

    for scenario in scenarios:
        conversation, report = _run_scenario(
            args.api_url,
            args.api_key,
            args.workflow_id,
            scenario,
            args.run_suffix,
        )
        conversations.append(conversation)
        reports.append(report)
        append_test_conversations([conversation], path=conversation_path)
        print(json.dumps({"scenario": scenario["id"], "turns": report["turn_count"]}, ensure_ascii=False))

    existing_reports: list[dict[str, Any]] = []
    if report_path.exists():
        try:
            loaded = json.loads(report_path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                incoming_ids = {item["id"] for item in reports}
                existing_reports = [
                    item
                    for item in loaded
                    if isinstance(item, dict) and item.get("id") not in incoming_ids
                ]
        except json.JSONDecodeError:
            existing_reports = []
    report_path.write_text(json.dumps(reports + existing_reports, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"count": len(conversations), "report": str(report_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
