# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


API_URL = "http://47.252.81.104/api/ai/chat"
ROOT = Path(__file__).resolve().parents[1]
ALT_ROOT = Path(r"E:\ai_code\vscode_codex\coze_cli_project")
OUT_PATHS = [
    ROOT / "projects/public/test-conversations.json",
    ALT_ROOT / "projects/public/test-conversations.json",
]
REPORT_PATH = ROOT / "logs/leadgen_rhythm_online_test_report.json"


def _now_ts() -> int:
    return int(time.time())


SCENARIOS: list[dict[str, Any]] = [
    {
        "id_prefix": "leadgen_opening",
        "title": "线上节奏测试-泛开场先门店",
        "turns": [
            "你好",
            "了解一下项目",
            "我在厦门机场附近",
            "我先想看看哪家方便一点",
        ],
    },
    {
        "id_prefix": "leadgen_need",
        "title": "线上节奏测试-明确需求先方向",
        "turns": [
            "你好",
            "我主要想祛斑，脸上还有点色沉",
            "那你先直接说我这种一般先看什么方向",
            "我不懂项目名，你按我的情况说就行",
        ],
    },
    {
        "id_prefix": "leadgen_case",
        "title": "线上节奏测试-方向后自然带案例",
        "turns": [
            "咨询一下祛斑",
            "主要是点状斑，顺便有点肤色不均",
            "能不能给我看看类似的效果对比",
            "如果差不多的话我再考虑到店",
        ],
    },
]


def request_chat(customer_id: str, turn_text: str, history: list[str], ext_suffix: str) -> tuple[dict[str, Any], int, str | None]:
    payload = {
        "content": turn_text,
        "customer_id": customer_id,
        "corp_id": "ent-753d018266f7453285311ce1d5ed0d94",
        "user_id": "DY1032",
        "wechat": "DY1032",
        "external_userid": f"codex_{ext_suffix}",
        "conversation_history": history[-10:],
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw), int((time.perf_counter() - start) * 1000), None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return json.loads(raw), int((time.perf_counter() - start) * 1000), f"HTTP {exc.code}"
        except Exception:
            return {"raw_error": raw}, int((time.perf_counter() - start) * 1000), f"HTTP {exc.code}"
    except Exception as exc:
        return {"raw_error": str(exc)}, int((time.perf_counter() - start) * 1000), type(exc).__name__


def extract_text(reply_item: dict[str, Any]) -> str:
    content = reply_item.get("content")
    if isinstance(content, dict):
        if reply_item.get("type") == "text":
            return str(content.get("text") or "")
        return json.dumps(content, ensure_ascii=False)
    return str(content or "")


def build_msg(message_id: str, role: str, text: str, ts: int, duration: int | None = None, data: dict[str, Any] | None = None) -> dict[str, Any]:
    msg: dict[str, Any] = {
        "id": message_id,
        "role": role,
        "content": text,
        "contentType": "text",
        "timestamp": ts,
    }
    if duration is not None:
        msg["duration"] = duration
    if data is not None:
        meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
        msg["meta"] = {
            "intent": data.get("intent", ""),
            "scene": data.get("scene", ""),
            "subflow": data.get("subflow", ""),
            "requestId": data.get("request_id", ""),
            "traceUrl": data.get("trace_url", ""),
            "toolResultKeys": meta.get("tool_result_keys", []),
            "toolCalls": meta.get("tool_calls", []),
            "profileUpdate": meta.get("profile_update"),
            "eventUpdates": meta.get("event_updates", []),
            "imageInfo": meta.get("image_info"),
            "raw": {
                "token_usage": meta.get("token_usage"),
                "model_usage": meta.get("model_usage"),
                "customer_context": meta.get("customer_context"),
                "reply_messages": data.get("reply_messages"),
            },
        }
    return msg


def merge_conversations(out_path: Path, conversations: list[dict[str, Any]]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    existing = {"conversations": []}
    if out_path.exists():
        try:
            existing = json.loads(out_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {"conversations": []}
    existing_list = existing.get("conversations") if isinstance(existing, dict) else []
    if not isinstance(existing_list, list):
        existing_list = []
    new_ids = {c["id"] for c in conversations}
    merged = conversations + [c for c in existing_list if isinstance(c, dict) and c.get("id") not in new_ids]
    out_path.write_text(json.dumps({"conversations": merged}, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    seed = _now_ts()
    conversations: list[dict[str, Any]] = []
    report_rows: list[dict[str, Any]] = []
    base_ts = int(time.time() * 1000)
    all_success = 0
    all_total = 0
    all_elapsed: list[int] = []

    for idx, scenario in enumerate(SCENARIOS, start=1):
        customer_id = f"{scenario['id_prefix']}_{seed}"
        history: list[str] = []
        messages: list[dict[str, Any]] = []
        turn_reports: list[dict[str, Any]] = []
        conv_ts = base_ts + idx * 100000

        for turn_idx, turn in enumerate(scenario["turns"], start=1):
            turn_ts = conv_ts + turn_idx * 60000
            messages.append(build_msg(f"{customer_id}_u_{turn_idx}", "user", turn, turn_ts))
            data, elapsed_ms, error = request_chat(customer_id, turn, history, f"{idx}_{turn_idx}_{seed}")
            all_total += 1
            all_elapsed.append(elapsed_ms)
            if not error:
                all_success += 1

            replies = data.get("reply_messages") or []
            if not isinstance(replies, list):
                replies = []
            if error and not replies:
                replies = [{"type": "text", "content": {"text": f"[请求失败] {error}: {data.get('detail') or data.get('raw_error') or data}"}}]

            reply_texts: list[str] = []
            for reply_idx, item in enumerate(replies, start=1):
                text = extract_text(item)
                reply_texts.append(text)
                messages.append(
                    build_msg(
                        f"{customer_id}_a_{turn_idx}_{reply_idx}",
                        "assistant",
                        text,
                        turn_ts + reply_idx * 1000,
                        elapsed_ms,
                        data if reply_idx == 1 else None,
                    )
                )

            history.append(f"用户: {turn}")
            for text in reply_texts:
                history.append(f"小贝: {text}")

            turn_reports.append(
                {
                    "turn": turn_idx,
                    "user": turn,
                    "elapsed_ms": elapsed_ms,
                    "error": error,
                    "intent": data.get("intent"),
                    "subflow": data.get("subflow"),
                    "tool_result_keys": (data.get("meta") or {}).get("tool_result_keys", []),
                    "reply_messages": reply_texts,
                    "request_id": data.get("request_id", ""),
                }
            )

        conversations.append(
            {
                "id": customer_id,
                "title": scenario["title"],
                "messages": messages,
                "createdAt": conv_ts,
                "updatedAt": conv_ts + len(scenario["turns"]) * 60000,
            }
        )
        report_rows.append({"id": customer_id, "title": scenario["title"], "turns": turn_reports})

    for out_path in OUT_PATHS:
        merge_conversations(out_path, conversations)

    summary = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "api_url": API_URL,
        "success": all_success,
        "total": all_total,
        "success_rate": round((all_success / all_total) * 100, 2) if all_total else 0,
        "avg_elapsed_ms": round(sum(all_elapsed) / len(all_elapsed), 2) if all_elapsed else 0,
        "scenarios": report_rows,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
