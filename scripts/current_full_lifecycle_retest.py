# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import requests


API_URL = "http://127.0.0.1:8000/chat"
CUSTOMER_ID = f"codex_full_lifecycle_utf8_20260529_chongqing_{int(time.time())}"
REPORT_PATH = Path("logs/full_lifecycle_retest_20260529_chongqing_utf8.json")

IMAGE_URL = (
    "https://coze-coding-project.tos.coze.site/coze_storage_7641059342338457652/"
    "chat_images/user_uploaded_image_94bf3ee7.png"
    "?sign=1780476697-341f4fe0ca-0-7f6262b376e2e480885690a14adb2234e935701d75a4b23014bdbb072800ed2b"
)

TURNS: list[dict[str, str]] = [
    {"content": "你好，我想了解下脸上的斑和暗沉，不知道适合做什么。"},
    {"content": "你看我脸颊这种，点状为主还有色沉。", "file_image": IMAGE_URL},
    {"content": "那这种适合做什么项目？光子和皮秒大概什么价格？"},
    {"content": "我主要想解决斑点，但是别太贵，你们重庆有没有门店？"},
    {"content": "重庆渝北店地址发我一下，顺便问下周六下午能不能约？"},
    {"content": "你们正规嘛？我有点怕被坑。"},
    {"content": "别家说光子399，你们能不能也这个价？"},
    {"content": "那我先考虑下，周六如果去需要带什么？"},
]


def conversation_history(messages: list[dict[str, Any]]) -> list[str]:
    history: list[str] = []
    for message in messages[-10:]:
        role = "用户" if message["role"] == "user" else "小贝"
        text = message.get("content") or ("[图片]" if message.get("file_image") else "")
        history.append(f"{role}: {text}")
    return history


def quality_flags(user_text: str, replies: list[str], meta: dict[str, Any]) -> list[str]:
    joined = "\n".join(replies)
    flags: list[str] = []
    if not replies or not joined.strip():
        flags.append("空回复")
    if any(bad in joined for bad in ["系统查询到", "知识库", "工具返回", "我是AI客服", "转人工"]):
        flags.append("暴露系统/AI/工具痕迹")
    if any(word in user_text for word in ["价格", "多少钱", "贵"]):
        if not any(ch.isdigit() for ch in joined) and "不乱报" not in joined and "配置" not in joined:
            flags.append("价格问题未给数字或明确兜底")
    if meta.get("image_info", {}).get("has_image") and any(word in joined for word in ["再发", "重新发", "发张照片"]):
        flags.append("已收图仍要求重发图片")
    if "重庆" in user_text and "门店" in user_text and "重庆" not in joined:
        flags.append("重庆门店问题未命中重庆")
    if any(word in user_text for word in ["正规", "怕被坑"]):
        if not any(word in joined for word in ["正规", "资质", "放心", "资料", "专业"]):
            flags.append("信任顾虑承接不足")
    if any(word in user_text for word in ["别家", "399"]):
        if not any(word in joined for word in ["对比", "配置", "产品", "不直接", "价格"]):
            flags.append("竞品比价承接不足")
    if len(joined) > 280:
        flags.append("回复偏长")
    return flags


def run() -> None:
    messages: list[dict[str, Any]] = []
    report: list[dict[str, Any]] = []
    for index, turn in enumerate(TURNS, start=1):
        payload: dict[str, Any] = {
            "content": turn["content"],
            "customer_id": CUSTOMER_ID,
            "corp_id": "ww916da62a08044243",
            "conversation_history": conversation_history(messages),
            "user_id": 7294,
            "wechat": "yzm-yibingwen",
            "external_userid": "codex_external_full_lifecycle_utf8_20260529",
        }
        if turn.get("file_image"):
            payload["file_image"] = turn["file_image"]

        started = time.time()
        response = requests.post(API_URL, json=payload, timeout=180)
        elapsed = round(time.time() - started, 1)
        response.raise_for_status()
        data = response.json()
        replies = [item.get("content", "") for item in data.get("reply_messages", []) if item.get("content")]
        meta = data.get("meta") or {}

        messages.append({"role": "user", "content": turn["content"], "file_image": turn.get("file_image")})
        for reply in replies:
            messages.append({"role": "assistant", "content": reply})

        item = {
            "turn": index,
            "user": turn["content"],
            "has_image": bool(turn.get("file_image")),
            "reply": replies,
            "elapsed_sec": elapsed,
            "request_id": data.get("request_id"),
            "scene": data.get("scene"),
            "intent": data.get("intent"),
            "subflow": data.get("subflow"),
            "intents": meta.get("intents"),
            "tool_keys": meta.get("tool_result_keys"),
            "token_usage": meta.get("token_usage"),
            "image_info": meta.get("image_info"),
            "quality_flags": quality_flags(turn["content"], replies, meta),
        }
        report.append(item)
        print(json.dumps(item, ensure_ascii=False, indent=2))

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps({"customer_id": CUSTOMER_ID, "report": report}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"REPORT_PATH {REPORT_PATH}")


if __name__ == "__main__":
    run()
