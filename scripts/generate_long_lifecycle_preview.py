# -*- coding: utf-8 -*-
"""生成一条长对话本地预览测试会话，并输出结构化评估报告。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import requests


API_URL = "http://127.0.0.1:8000/chat"
OUT_PATH = Path("projects/public/test-conversations.json")
REPORT_PATH = Path("logs/long_lifecycle_preview_report.json")

IMAGE_URL = (
    "https://coze-coding-project.tos.coze.site/coze_storage_7641059342338457652/"
    "chat_images/user_uploaded_image_94bf3ee7.png"
    "?sign=1780476697-341f4fe0ca-0-7f6262b376e2e480885690a14adb2234e935701d75a4b23014bdbb072800ed2b"
)

SCENARIO = {
    "id": "codex_test_long_lifecycle",
    "title": "Codex测试-长对话12轮完整跟进",
    "turns": [
        {"content": "你好，我想了解一下面部斑点和暗沉，不知道先看什么方向"},
        {"content": "我人在上海，离徐家汇近一点的门店有吗"},
        {"content": "我主要是点状斑，预算别太高"},
        {"content": "我发张照片你帮我看看", "file_image": IMAGE_URL},
        {"content": "主要是想解决斑点，其次是肤色不均"},
        {"content": "那按你前面说的，我这种先看什么方向更合适"},
        {"content": "这种大概多少钱"},
        {"content": "如果第一次过去，有没有先参考的价格"},
        {"content": "你们正规吗，会不会到店乱收费"},
        {"content": "我之前在别家做过一次，效果一般，所以现在比较谨慎"},
        {"content": "那徐汇店周六下午有没有时间"},
        {"content": "去之前需要带什么，能化妆吗？另外把刚说的地址和价格帮我顺一下"},
    ],
}


def build_history(messages: list[dict[str, Any]]) -> list[str]:
    history: list[str] = []
    for message in messages[-10:]:
        role = "用户" if message["role"] == "user" else "小贝"
        text = message.get("content") or ("[图片]" if message.get("imageUrl") else "")
        history.append(f"{role}: {text}")
    return history


def make_message_id(prefix: str, turn_index: int, item_index: int) -> str:
    return f"{prefix}_{turn_index}_{item_index}_{int(time.time() * 1000)}"


def evaluate_turn(
    user_text: str,
    replies: list[str],
    first_reply: str,
    meta: dict[str, Any],
) -> dict[str, Any]:
    reply_text = "\n".join(replies)
    issues: list[str] = []
    strengths: list[str] = []

    if not replies or not reply_text.strip():
        issues.append("空回复")
    else:
        strengths.append("有明确回复")

    if any(term in user_text for term in ["多少钱", "价格", "预算"]):
        if any(ch.isdigit() for ch in reply_text):
            strengths.append("价格问题给了数值参考")
        elif "不乱报" in reply_text or "看具体项目" in reply_text:
            strengths.append("价格问题给了谨慎兜底")
        else:
            issues.append("价格问题没有给到有效承接")

    if any(term in user_text for term in ["正规吗", "乱收费", "怕被坑"]):
        if any(term in reply_text for term in ["资质", "正规", "透明", "隐形", "保障"]):
            strengths.append("信任顾虑有正面承接")
        else:
            issues.append("信任顾虑承接不足")

    if any(term in user_text for term in ["徐家汇", "徐汇店", "地址"]):
        if any(term in reply_text for term in ["徐汇", "地址", "南丹东路"]):
            strengths.append("门店信息有承接")
        else:
            issues.append("门店信息承接不足")

    if any(term in user_text for term in ["照片", "看看"]) or meta.get("image_info", {}).get("has_image"):
        image_info = meta.get("image_info") or {}
        if image_info.get("has_image"):
            strengths.append("图片已进入链路")
        else:
            issues.append("图片链路未识别")

    if any(term in user_text for term in ["刚说的", "前面说的", "那个方向"]):
        if any(term in reply_text for term in ["徐汇", "价格", "方向", "点状斑", "肤色不均"]):
            strengths.append("有前文记忆承接")
        else:
            issues.append("对前文记忆承接弱")

    if len(first_reply) > 180:
        issues.append("首条回复偏长")

    if reply_text.count("？") >= 3:
        issues.append("追问偏多")

    if any(term in reply_text for term in ["系统", "知识库", "工具返回", "我是AI"]):
        issues.append("暴露系统或AI痕迹")

    if not issues:
        strengths.append("当前轮无明显硬伤")

    return {
        "strengths": strengths,
        "issues": issues,
        "intent": meta.get("intent", ""),
        "subflow": meta.get("subflow", ""),
        "token_usage": meta.get("raw", {}).get("token_usage", meta.get("token_usage", {})),
    }


def generate() -> None:
    timestamp_suffix = int(time.time())
    scenario_id = f"{SCENARIO['id']}_{timestamp_suffix}"
    messages: list[dict[str, Any]] = []
    turn_reports: list[dict[str, Any]] = []
    created_at = int(time.time() * 1000)

    for turn_index, turn in enumerate(SCENARIO["turns"], start=1):
        user_message = {
            "id": make_message_id(f"{scenario_id}_u", turn_index, 0),
            "role": "user",
            "content": turn["content"],
            "timestamp": int(time.time() * 1000),
        }
        if turn.get("file_image"):
            user_message["imageUrl"] = turn["file_image"]

        payload = {
            "content": turn["content"],
            "customer_id": scenario_id,
            "corp_id": "ww916da62a08044243",
            "conversation_history": build_history(messages),
            "user_id": 7294,
            "wechat": "yzm-yibingwen",
            "external_userid": "codex_long_preview_01",
        }
        if turn.get("file_image"):
            payload["file_image"] = turn["file_image"]

        started = time.time()
        response = requests.post(API_URL, json=payload, timeout=240)
        elapsed_ms = int((time.time() - started) * 1000)
        response.raise_for_status()
        data = response.json()
        meta = data.get("meta") or {}
        replies = data.get("reply_messages") or []

        messages.append(user_message)

        reply_texts = [item.get("content", "") for item in replies if item.get("content")]
        first_reply = reply_texts[0] if reply_texts else ""

        for assistant_index, item in enumerate(replies, start=1):
            assistant_message: dict[str, Any] = {
                "id": make_message_id(f"{scenario_id}_a", turn_index, assistant_index),
                "role": "assistant",
                "content": item.get("content", ""),
                "contentType": item.get("type", "text"),
                "timestamp": int(time.time() * 1000),
                "duration": elapsed_ms,
            }
            if assistant_index == 1:
                assistant_message["meta"] = {
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
                    "raw": meta,
                }
            messages.append(assistant_message)

        turn_reports.append(
            {
                "turn": turn_index,
                "user": turn["content"],
                "has_image": bool(turn.get("file_image")),
                "elapsed_ms": elapsed_ms,
                "request_id": data.get("request_id", ""),
                "intent": data.get("intent", ""),
                "subflow": data.get("subflow", ""),
                "replies": reply_texts,
                "evaluation": evaluate_turn(turn["content"], reply_texts, first_reply, {
                    **meta,
                    "intent": data.get("intent", ""),
                    "subflow": data.get("subflow", ""),
                }),
            }
        )

    solved_count = sum(1 for item in turn_reports if not item["evaluation"]["issues"])
    issue_count = sum(len(item["evaluation"]["issues"]) for item in turn_reports)

    conversation = {
        "id": scenario_id,
        "title": SCENARIO["title"],
        "messages": messages,
        "createdAt": created_at,
        "updatedAt": int(time.time() * 1000),
    }

    OUT_PATH.write_text(
        json.dumps({"conversations": [conversation]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    REPORT_PATH.write_text(
        json.dumps(
            {
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "conversation_id": scenario_id,
                "summary": {
                    "turn_count": len(turn_reports),
                    "turns_without_issue": solved_count,
                    "issue_count": issue_count,
                },
                "turns": turn_reports,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    generate()
