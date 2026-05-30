# -*- coding: utf-8 -*-
"""Generate visible customer-service regression conversations for the local chat UI."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


API_URL = "http://127.0.0.1:8000/chat"
OUT_PATH = Path("projects/public/test-conversations.json")
REPORT_PATH = Path("logs/customer_lifecycle_test_report.json")


IMAGE_URL = (
    "https://coze-coding-project.tos.coze.site/coze_storage_7641059342338457652/"
    "chat_images/user_uploaded_image_94bf3ee7.png"
    "?sign=1780476697-341f4fe0ca-0-7f6262b376e2e480885690a14adb2234e935701d75a4b23014bdbb072800ed2b"
)


@dataclass
class Turn:
    content: str
    image_url: str = ""


@dataclass
class Scenario:
    id: str
    title: str
    turns: list[Turn]


SCENARIOS: list[Scenario] = [
    Scenario(
        "codex_test_lifecycle_01",
        "Codex测试-01 无明确需求到项目",
        [
            Turn("了解一下项目"),
            Turn("就是脸上有点暗沉还有小斑点，不知道做什么"),
            Turn("那光子嫩肤和皮秒哪个更适合我"),
        ],
    ),
    Scenario(
        "codex_test_lifecycle_02",
        "Codex测试-02 图片面诊到价格",
        [
            Turn("我想看看脸上的问题", IMAGE_URL),
            Turn("这种适合做什么项目"),
            Turn("光子嫩肤普通一次多少钱"),
            Turn("有点贵，不能便宜点吗"),
        ],
    ),
    Scenario(
        "codex_test_lifecycle_03",
        "Codex测试-03 多意图价格门店",
        [
            Turn("我在上海，想改善斑点，顺便问下光子多少钱，附近有门店吗"),
            Turn("虹口那家地址发我"),
        ],
    ),
    Scenario(
        "codex_test_lifecycle_04",
        "Codex测试-04 信任顾虑",
        [
            Turn("你们这个是正规的吗？我有点怕被坑"),
            Turn("有什么证件或者资质能看吗"),
        ],
    ),
    Scenario(
        "codex_test_lifecycle_05",
        "Codex测试-05 竞品比价",
        [
            Turn("别家说光子嫩肤299，你们怎么贵这么多"),
            Turn("那你们优势在哪里"),
        ],
    ),
    Scenario(
        "codex_test_lifecycle_06",
        "Codex测试-06 门店和停车",
        [
            Turn("你们门店在哪里"),
            Turn("我在厦门"),
            Turn("思明店有停车吗"),
        ],
    ),
    Scenario(
        "codex_test_lifecycle_07",
        "Codex测试-07 预约查询",
        [
            Turn("我之前有没有预约"),
            Turn("如果没有的话，厦门思明店周六下午能约吗"),
        ],
    ),
    Scenario(
        "codex_test_lifecycle_08",
        "Codex测试-08 售后轻症",
        [
            Turn("我做完光子两天了，有点泛红正常吗"),
            Turn("没有流脓，就是有点干"),
        ],
    ),
    Scenario(
        "codex_test_lifecycle_09",
        "Codex测试-09 投诉升级",
        [
            Turn("都是骗人的，我做的丢了200块钱，一点用都没有"),
            Turn("我要投诉"),
        ],
    ),
    Scenario(
        "codex_test_lifecycle_10",
        "Codex测试-10 通用闲聊转需求",
        [
            Turn("在吗"),
            Turn("我不知道自己适合什么，就是想变白一点"),
            Turn("预算不要太高，有新客活动吗"),
        ],
    ),
]


def message_id(prefix: str, idx: int) -> str:
    return f"{prefix}_{idx}_{int(time.time() * 1000)}"


def conversation_history(messages: list[dict[str, Any]]) -> list[str]:
    history: list[str] = []
    for message in messages[-10:]:
        role = "用户" if message["role"] == "user" else "小贝"
        text = message.get("content", "")
        if message.get("imageUrl") and not text:
            text = "[图片]"
        history.append(f"{role}: {text}")
    return history


def quality_flags(user_text: str, replies: list[str], meta: dict[str, Any]) -> list[str]:
    joined = "\n".join(replies)
    flags: list[str] = []
    if not replies or not joined.strip():
        flags.append("空回复")
    if any(bad in joined for bad in ["系统查询到", "知识库", "工具返回", "我是AI客服", "转人工"]):
        flags.append("暴露系统或AI流程")
    if "多少钱" in user_text or "价格" in user_text or "贵" in user_text:
        if not any(ch.isdigit() for ch in joined) and "没查到" not in joined and "不乱报" not in joined:
            flags.append("价格问题未给价格或明确兜底")
    if "图片" in user_text or meta.get("image_info", {}).get("has_image"):
        if any(text in joined for text in ["再发", "重新发", "发张照片"]) and meta.get("image_info", {}).get("has_image"):
            flags.append("已收图仍要求重复发图")
    if any(term in user_text for term in ["怕被坑", "正规", "资质"]):
        if not any(term in joined for term in ["资质", "正规", "谨慎", "证", "资料"]):
            flags.append("信任顾虑承接不足")
    if any(term in user_text for term in ["投诉", "骗人", "骗"]):
        if not any(term in joined for term in ["专业", "同事", "门店", "协助", "核实"]):
            flags.append("投诉风险未升级协助")
    if len(joined) > 260:
        flags.append("回复偏长")
    return flags


def run() -> None:
    conversations: list[dict[str, Any]] = []
    report: list[dict[str, Any]] = []
    now = int(time.time() * 1000)

    for scenario_index, scenario in enumerate(SCENARIOS, start=1):
        messages: list[dict[str, Any]] = []
        turn_reports: list[dict[str, Any]] = []
        for turn_index, turn in enumerate(scenario.turns, start=1):
            user_message = {
                "id": message_id(f"{scenario.id}_u", turn_index),
                "role": "user",
                "content": turn.content,
                "timestamp": int(time.time() * 1000),
                "imageUrl": turn.image_url or None,
            }
            payload = {
                "content": turn.content or "[图片]",
                "customer_id": scenario.id,
                "corp_id": "codex-local",
                "conversation_history": conversation_history(messages),
                "user_id": 7294,
                "wechat": "yzm-yibingwen",
                "external_userid": f"codex_external_{scenario_index:02d}",
            }
            if turn.image_url:
                payload["file_image"] = turn.image_url

            started = time.time()
            response = requests.post(API_URL, json=payload, timeout=160)
            elapsed = round(time.time() - started, 1)
            response.raise_for_status()
            data = response.json()
            meta = data.get("meta") or {}
            output = data.get("reply_messages") or data.get("output") or []
            assistant_texts = [str(item.get("content") or "") for item in output if item.get("content")]

            messages.append(user_message)
            for assistant_index, item in enumerate(output, start=1):
                messages.append(
                    {
                        "id": message_id(f"{scenario.id}_a", turn_index * 10 + assistant_index),
                        "role": "assistant",
                        "content": item.get("content", ""),
                        "contentType": item.get("type", "text"),
                        "timestamp": int(time.time() * 1000),
                        "duration": int(elapsed * 1000),
                        "meta": {
                            "intent": data.get("intent", ""),
                            "scene": data.get("scene", ""),
                            "subflow": data.get("subflow", ""),
                            "requestId": data.get("request_id", ""),
                            "traceUrl": data.get("trace_url", ""),
                            "toolResultKeys": meta.get("tool_result_keys", []),
                            "raw": meta,
                        }
                        if assistant_index == 1
                        else None,
                    }
                )

            turn_reports.append(
                {
                    "turn": turn_index,
                    "user": turn.content,
                    "has_image": bool(turn.image_url),
                    "reply": assistant_texts,
                    "elapsed_sec": elapsed,
                    "request_id": data.get("request_id", ""),
                    "intent": data.get("intent", ""),
                    "subflow": data.get("subflow", ""),
                    "token_usage": meta.get("token_usage", {}),
                    "tool_keys": meta.get("tool_result_keys", []),
                    "quality_flags": quality_flags(turn.content, assistant_texts, meta),
                }
            )

        updated = int(time.time() * 1000)
        conversations.append(
            {
                "id": scenario.id,
                "title": scenario.title,
                "messages": messages,
                "createdAt": now - scenario_index * 1000,
                "updatedAt": updated,
            }
        )
        report.append(
            {
                "id": scenario.id,
                "title": scenario.title,
                "turns": turn_reports,
                "issue_count": sum(len(item["quality_flags"]) for item in turn_reports),
            }
        )

    OUT_PATH.write_text(json.dumps({"conversations": conversations}, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps({"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "report": report}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"conversations": len(conversations), "report": str(REPORT_PATH), "output": str(OUT_PATH)}, ensure_ascii=False))


if __name__ == "__main__":
    run()
