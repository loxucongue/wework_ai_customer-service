from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:
    from .test_conversation_store import append_test_conversations
except ImportError:
    from test_conversation_store import append_test_conversations


ROOT = Path(__file__).resolve().parents[2]
FRONTEND_FILE = ROOT / "projects" / "public" / "test-conversations.json"
REPORT_FILE = ROOT / "logs" / "online_full_flow_15_turn_report.json"
ENDPOINT = "http://47.252.81.104/api/ai/chat/workflow-compatible"

IMAGE_URL = (
    "https://coze-coding-project.tos.coze.site/coze_storage_7641059342338457652/"
    "chat_images/user_uploaded_image_94bf3ee7.png?"
    "sign=1780476697-341f4fe0ca-0-7f6262b376e2e480885690a14adb2234e935701d75a4b23014bdbb072800ed2b"
)

TURNS: list[dict[str, Any]] = [
    {"content": "你好，我想了解下脸上暗沉和毛孔，没想好做什么"},
    {"content": "我脸上还有点点状斑，预算别太高，先看什么方向？"},
    {"content": "我发张照片你帮我看看", "file_image": IMAGE_URL, "msgtype": "image"},
    {"content": "效果能保证吗？"},
    {"content": "做了会不会反弹？"},
    {"content": "这种大概多少钱？"},
    {"content": "我看别家299，你们能做到同价吗？"},
    {"content": "客户做完之后的效果我想看一下"},
    {"content": "你们正规吗？会不会到店乱收费？"},
    {"content": "上海浦东附近有门店吗？"},
    {"content": "那就推荐一家离浦东机场近点的"},
    {"content": "这家地址和停车发我"},
    {"content": "周六下午能约吗？"},
    {"content": "到店要带什么，要不要素颜？"},
    {"content": "如果我报名后到店不满意，10元能退吗？"},
    {"content": "我做完以后有点红肿，正常吗？"},
    {"content": "我现在不满意，想退款"},
    {"content": "好的，谢谢"},
]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _extract_text(item: dict[str, Any]) -> str:
    content = item.get("content")
    if isinstance(content, dict):
        return str(content.get("text") or content.get("handoff_reason") or "")
    return str(content or "")


def _assistant_replies(data: dict[str, Any]) -> list[str]:
    messages = data.get("reply_messages")
    if not isinstance(messages, list):
        return []
    replies: list[str] = []
    for item in messages:
        if isinstance(item, dict):
            text = _extract_text(item).strip()
            if text:
                replies.append(text)
    return replies


def _post_json(payload: dict[str, Any], timeout: int = 180) -> tuple[int, dict[str, Any], str]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        ENDPOINT,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = {"raw": raw}
            return int(response.status), parsed, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"raw": raw}
        return int(exc.code), parsed, raw
    except Exception as exc:  # noqa: BLE001 - test runner needs to record transport failures.
        return 0, {"error": repr(exc)}, repr(exc)


def _quality_notes(content: str, replies: list[str], status: int, code: Any) -> list[str]:
    notes: list[str] = []
    joined = "\n".join(replies)
    if status != 200 or code != 0:
        notes.append("接口失败或业务 code 非 0")
    if not replies:
        notes.append("无客户可见回复")
    if "知识库" in joined or "工具" in joined or "系统查询" in joined or "我是AI" in joined:
        notes.append("出现系统/工具类表述")
    if content in {"效果能保证吗？", "做了会不会反弹？"} and not any(
        keyword in joined for keyword in ("保障", "维护", "护理", "稳定", "跟进", "效果")
    ):
        notes.append("效果顾虑承接不足")
    if "10元" in content and not any(keyword in joined for keyword in ("10", "定金", "预约", "退", "核对")):
        notes.append("10元/定金问题承接不足")
    if "红肿" in content and not any(keyword in joined for keyword in ("红", "肿", "护理", "老师", "照片", "几天")):
        notes.append("售后症状承接不足")
    if len(replies) > 3:
        notes.append("回复条数偏多")
    return notes


def run() -> dict[str, Any]:
    started = _now_ms()
    customer_id = f"codex_online_full_flow_{int(time.time())}"
    title = f"公网全流程-15轮混合场景-{time.strftime('%H%M')}"
    history: list[str] = []
    frontend_messages: list[dict[str, Any]] = []
    report_turns: list[dict[str, Any]] = []

    for index, turn in enumerate(TURNS, start=1):
        content = str(turn["content"])
        timestamp = started + index * 60_000
        frontend_messages.append(
            {
                "id": f"user-{index}",
                "role": "user",
                "content": content,
                "timestamp": timestamp,
                **({"imageUrl": turn["file_image"]} if turn.get("file_image") else {}),
            }
        )

        parameters: dict[str, Any] = {
            "category_id": "",
            "content": {
                "content": content,
                "msgid": f"{customer_id}_msg_{index}",
                "msgtime": timestamp,
                "msgtype": turn.get("msgtype", "text"),
            },
            "customer_id": customer_id,
            "external_userid": customer_id,
            "user_id": "DY1032",
            "wechat": "DY1032",
            "corp_id": "ent-test",
            "messages": history[-10:],
        }
        if turn.get("file_image"):
            parameters["file_image"] = turn["file_image"]

        payload = {"workflow_id": "xiaobei-default", "parameters": parameters}
        start = time.perf_counter()
        status, response, raw = _post_json(payload)
        elapsed_ms = int((time.perf_counter() - start) * 1000)

        data = response.get("data") if isinstance(response.get("data"), dict) else {}
        replies = _assistant_replies(data)
        code = response.get("code")
        trace_id = str(data.get("trace_id") or response.get("execute_id") or "")
        step = str(data.get("step") or "")

        for reply_index, reply in enumerate(replies, start=1):
            assistant_message: dict[str, Any] = {
                "id": f"assistant-{index}-{reply_index}",
                "role": "assistant",
                "content": reply,
                "timestamp": timestamp + reply_index * 1000,
                "duration": elapsed_ms,
            }
            if reply_index == 1:
                assistant_message["meta"] = {
                    "intent": data.get("intent") or "",
                    "scene": data.get("scene") or "",
                    "subflow": step,
                    "requestId": trace_id,
                    "traceUrl": "",
                    "toolResultKeys": data.get("tool_result_keys") or [],
                    "toolCalls": data.get("tool_calls") or [],
                    "raw": {
                        "status": status,
                        "code": code,
                        "msg": response.get("msg"),
                        "step": step,
                        "token_usage": data.get("token_usage"),
                        "model_usage": data.get("model_usage"),
                    },
                }
            frontend_messages.append(assistant_message)

        history.append(f"客户：{content}")
        for reply in replies:
            history.append(f"小贝：{reply}")

        report_turns.append(
            {
                "turn": index,
                "user": content,
                "status": status,
                "code": code,
                "elapsed_ms": elapsed_ms,
                "trace_id": trace_id,
                "step": step,
                "reply": replies,
                "quality_notes": _quality_notes(content, replies, status, code),
                "error": data.get("error") or response.get("msg") or (raw if status != 200 else ""),
            }
        )
        print(
            json.dumps(
                {
                    "turn": index,
                    "status": status,
                    "code": code,
                    "elapsed_ms": elapsed_ms,
                    "step": step,
                    "reply_count": len(replies),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    conversation = {
        "id": customer_id,
        "title": title,
        "messages": frontend_messages,
        "createdAt": started,
        "updatedAt": _now_ms(),
    }
    append_test_conversations([conversation], path=FRONTEND_FILE)

    failures = [turn for turn in report_turns if turn["status"] != 200 or turn["code"] != 0]
    notes = [turn for turn in report_turns if turn["quality_notes"]]
    report = {
        "endpoint": ENDPOINT,
        "conversation_id": customer_id,
        "conversation_title": title,
        "turn_count": len(TURNS),
        "success_count": len(TURNS) - len(failures),
        "failure_count": len(failures),
        "average_elapsed_ms": round(sum(turn["elapsed_ms"] for turn in report_turns) / max(len(report_turns), 1), 1),
        "max_elapsed_ms": max((turn["elapsed_ms"] for turn in report_turns), default=0),
        "quality_note_count": len(notes),
        "turns": report_turns,
    }
    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(json.dumps(run(), ensure_ascii=False, indent=2))
