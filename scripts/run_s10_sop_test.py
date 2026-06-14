# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str((ROOT / "ai_paths").resolve()))

from app.main import app  # noqa: E402


LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

BASE_PAYLOAD = {
    "corp_id": "ww943af61cd5d2afe4",
    "user_id": 7294,
    "wechat": "CS001",
    "request_context": {"category_id": "居家产品"},
}

COMMON_FORBIDDEN = ["S10", "焕新体验季", "大型活动", "内部活动", "包接送", "车费报销"]


@dataclass
class SopTurn:
    customer_text: str
    purpose: str
    expected_any: list[str]
    forbidden: list[str]


@dataclass
class SopScenario:
    scenario_id: str
    title: str
    goal: str
    turns: list[SopTurn]


SCENARIOS: list[SopScenario] = [
    SopScenario(
        scenario_id="new_lead_to_price_and_store",
        title="新客破冰到价格再到门店",
        goal="验证开场、需求承接、活动价、隐形消费、门店推荐和时间承接是否形成完整销售节奏。",
        turns=[
            SopTurn("你好，先了解一下", "破冰应轻量收城市或改善方向。", ["您好", "门店", "改善"], COMMON_FORBIDDEN),
            SopTurn("我在厦门，脸上斑点有点多", "先给可看改善方向，不反复追项目名。", ["可以", "淡化", "到店"], COMMON_FORBIDDEN),
            SopTurn("你们现在有什么活动", "直接回周年庆活动和268规则。", ["周年庆活动", "268", "10元"], COMMON_FORBIDDEN),
            SopTurn("到店会不会乱收费", "给费用透明确定感。", ["提前", "说清楚", "认可再做"], COMMON_FORBIDDEN),
            SopTurn("厦门哪家离机场近一点", "真实推荐就近门店。", ["厦门", "店"], COMMON_FORBIDDEN),
            SopTurn("那今天下午能过去吗", "承接时间并查可约，不直接说预约成功。", ["帮您", "时间"], COMMON_FORBIDDEN + ["已预约成功"]),
        ],
    ),
    SopScenario(
        scenario_id="project_method_case_and_price",
        title="项目方法到案例再到付款规则",
        goal="验证方法解释短句化、案例承接、一次费用和做完付款规则是否贴近销售短聊。",
        turns=[
            SopTurn("你们祛斑用什么方法", "短句解释方法，不说明书化。", ["肌源调肤", "到店", "更准"], COMMON_FORBIDDEN),
            SopTurn("有没有客户做完之后的效果图", "承接案例诉求。", ["案例", "参考"], COMMON_FORBIDDEN),
            SopTurn("确定268吗", "直接确认268和10+258。", ["268", "10元", "258"], COMMON_FORBIDDEN),
            SopTurn("这是一次的费用吗", "说明一次活动规则。", ["一次", "268", "258"], COMMON_FORBIDDEN),
            SopTurn("是做完付款吗", "说明预约金、抵扣和认可再做。", ["10元", "抵扣", "认可"], COMMON_FORBIDDEN),
        ],
    ),
    SopScenario(
        scenario_id="trust_and_reservation_push",
        title="信任顾虑到预约推进",
        goal="验证身份、资质、交通服务和预约推进是否自然衔接。",
        turns=[
            SopTurn("你是门店的人吗", "按线上活动咨询和安排负责人承接。", ["负责", "咨询", "安排"], COMMON_FORBIDDEN + ["AI", "机器人"]),
            SopTurn("有资质吗", "说明可核验、到店可看。", ["资质", "到店", "看"], COMMON_FORBIDDEN),
            SopTurn("有车费报销吗 可以包接送吗", "直接否定接送/报销，再给路线协助。", ["没有接送", "交通费用需自理"], [item for item in COMMON_FORBIDDEN if item not in {"包接送", "车费报销"}]),
            SopTurn("我在上海浦东", "承接城市并推荐附近门店。", ["上海", "门店"], COMMON_FORBIDDEN),
            SopTurn("那周六下午能过来吗", "承接时间，不编预约成功。", ["帮您", "周六"], COMMON_FORBIDDEN + ["已预约成功"]),
        ],
    ),
]


def extract_reply_messages(data: dict[str, Any]) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for item in data.get("reply_messages") or []:
        if not isinstance(item, dict):
            continue
        msg_type = str(item.get("type") or "")
        content = item.get("content")
        if msg_type == "text" and isinstance(content, dict):
            text = str(content.get("text") or "").strip()
            if text:
                messages.append({"type": "text", "text": text})
        elif msg_type == "image":
            url = str(content or "").strip()
            if url:
                messages.append({"type": "image", "text": url})
        elif msg_type == "human_handoff" and isinstance(content, dict):
            reason = str(content.get("handoff_reason") or "").strip()
            if reason:
                messages.append({"type": "human_handoff", "text": reason})
    return messages


def run_turn(client: TestClient, scenario_id: str, customer_id: str, history: list[str], turn: SopTurn) -> dict[str, Any]:
    payload = {
        **BASE_PAYLOAD,
        "content": turn.customer_text,
        "customer_id": customer_id,
        "external_userid": customer_id,
        "conversation_history": history[-10:],
    }
    started = time.perf_counter()
    response = client.post("/reply", json=payload)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    data = response.json()
    reply_messages = extract_reply_messages(data)
    texts = [item["text"] for item in reply_messages if item["type"] == "text"]
    all_text = "\n".join(texts)
    forbidden_hits = [item for item in turn.forbidden if item and item in all_text]
    expected_hits = [item for item in turn.expected_any if item and item in all_text]
    meta = data.get("meta") or {}
    tool_calls = [str((item or {}).get("name") or "") for item in (meta.get("tool_calls") or []) if isinstance(item, dict)]
    passed = response.status_code == 200 and bool(texts) and not forbidden_hits and expected_hits
    issues: list[str] = []
    if response.status_code != 200:
        issues.append(f"HTTP {response.status_code}")
    if not texts:
        issues.append("无客户可见回复")
    if forbidden_hits:
        issues.append("命中禁用词：" + "、".join(forbidden_hits))
    if not expected_hits:
        issues.append("关键业务点不足")
    history.append(f"用户：{turn.customer_text}")
    for text in texts[:2]:
        history.append(f"客服：{text}")
    return {
        "scenario_id": scenario_id,
        "customer_text": turn.customer_text,
        "purpose": turn.purpose,
        "reply_1": texts[0] if len(texts) > 0 else "",
        "reply_2": texts[1] if len(texts) > 1 else "",
        "has_image": any(item["type"] == "image" for item in reply_messages),
        "has_handoff": any(item["type"] == "human_handoff" for item in reply_messages),
        "elapsed_ms": elapsed_ms,
        "status_code": response.status_code,
        "request_id": data.get("request_id", ""),
        "policy_family_id": str(meta.get("policy_family_id") or ""),
        "exact_policy_id": str(meta.get("exact_policy_id") or ""),
        "active_scene_id": str(meta.get("active_scene_id") or ""),
        "reply_source": str(meta.get("reply_source") or ""),
        "tool_calls": tool_calls,
        "expected_hits": expected_hits,
        "forbidden_hits": forbidden_hits,
        "passed": passed,
        "issues": issues,
    }


def run_scenario(client: TestClient, scenario: SopScenario) -> dict[str, Any]:
    history: list[str] = []
    customer_id = f"sop_{scenario.scenario_id}_{uuid.uuid4().hex[:8]}"
    turns = [run_turn(client, scenario.scenario_id, customer_id, history, turn) for turn in scenario.turns]
    return {
        "scenario_id": scenario.scenario_id,
        "title": scenario.title,
        "goal": scenario.goal,
        "customer_id": customer_id,
        "passed": all(item["passed"] for item in turns),
        "turns": turns,
    }


def build_markdown(results: list[dict[str, Any]], generated_at: str) -> str:
    total_turns = sum(len(item["turns"]) for item in results)
    passed_turns = sum(1 for item in results for turn in item["turns"] if turn["passed"])
    lines = [
        "# S10 全链路 SOP 测试报告",
        "",
        f"- 生成时间：`{generated_at}`",
        f"- 场景数：`{len(results)}`",
        f"- 场景通过数：`{sum(1 for item in results if item['passed'])}`",
        f"- 轮次数：`{total_turns}`",
        f"- 轮次通过数：`{passed_turns}`",
        "",
        "| 场景 | 用户问题 | AI实际回复（第1条） | AI引导回复（第2条） | exact_policy_id | active_scene_id | 工具调用 | 日志id | 评判 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for scenario in results:
        for turn in scenario["turns"]:
            judgement = "通过" if turn["passed"] else "；".join(turn["issues"]) or "未通过"
            tools = "、".join(turn["tool_calls"]) if turn["tool_calls"] else "-"
            lines.append(
                "| {scene} | {q} | {r1} | {r2} | {policy} | {scene_id} | {tools} | {rid} | {judge} |".format(
                    scene=scenario["title"].replace("|", "/"),
                    q=turn["customer_text"].replace("|", "/"),
                    r1=(turn["reply_1"] or "").replace("|", "/"),
                    r2=(turn["reply_2"] or "").replace("|", "/"),
                    policy=(turn["exact_policy_id"] or "-").replace("|", "/"),
                    scene_id=(turn["active_scene_id"] or "-").replace("|", "/"),
                    tools=tools.replace("|", "/"),
                    rid=(turn["request_id"] or "-").replace("|", "/"),
                    judge=judgement.replace("|", "/"),
                )
            )
    return "\n".join(lines)


def main() -> None:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with TestClient(app) as client:
        results = [run_scenario(client, scenario) for scenario in SCENARIOS]
    stem = datetime.now().strftime("s10_sop_report_%Y%m%d_%H%M%S")
    json_path = LOG_DIR / f"{stem}.json"
    md_path = LOG_DIR / f"{stem}.md"
    json_path.write_text(json.dumps({"generated_at": generated_at, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(build_markdown(results, generated_at), encoding="utf-8")
    print(md_path)


if __name__ == "__main__":
    main()
