from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.graph.graph_builder import build_graph  # noqa: E402
from app.graph.state import AgentState  # noqa: E402
from app.services.coze_client import CozeClient  # noqa: E402
from app.services.customer_context import CustomerContextService  # noqa: E402
from app.services.memory_store import CustomerMemoryStore  # noqa: E402
from app.services.model_client import ModelClient  # noqa: E402
from app.services.platform_agent_client import PlatformAgentClient  # noqa: E402
from app.services.pricing_repository import LocalPricingRepository  # noqa: E402
from app.services.store_service import StoreService  # noqa: E402
from app.services.trace_logger import TraceLogger  # noqa: E402


IMAGE_URL = (
    "https://coze-coding-project.tos.coze.site/coze_storage_7641059342338457652/"
    "chat_images/user_uploaded_image_94bf3ee7.png?sign=1780476697-341f4fe0ca-0-"
    "7f6262b376e2e480885690a14adb2234e935701d75a4b23014bdbb072800ed2b"
)


def token_summary(trace: list[dict]) -> dict[str, int]:
    summary = {"planner": 0, "reply": 0, "vision": 0, "other": 0, "total": 0}
    for entry in trace or []:
        node = str(entry.get("node") or "")
        for call in entry.get("tool_calls", []) or []:
            usage = call.get("usage") if isinstance(call, dict) else {}
            if not isinstance(usage, dict):
                continue
            total = int(usage.get("total_tokens") or usage.get("token_count") or 0)
            if total <= 0:
                continue
            if node == "planner_brain":
                summary["planner"] += total
            elif node == "synthesize_reply":
                summary["reply"] += total
            elif node == "image_understanding":
                summary["vision"] += total
            else:
                summary["other"] += total
            summary["total"] += total
    return summary


def judge_reply(user_text: str, reply_text: str) -> list[str]:
    issues: list[str] = []
    if "最想先改善哪一点" in reply_text and any(word in user_text for word in ["斑", "淡斑", "祛斑"]):
        issues.append("重复追问已明确的斑点诉求")
    if "发照片" in reply_text and ("[图片]" in user_text or "图片" in user_text):
        issues.append("已有图片仍要求发照片")
    if any(word in reply_text for word in ["系统查询到", "知识库显示", "工具返回", "我是AI", "转人工"]):
        issues.append("暴露系统/工具/AI流程")
    if "多少钱" in user_text and not any(ch.isdigit() for ch in reply_text) and "没有查到" not in reply_text:
        issues.append("价格问题未直接给价格或查不到原因")
    if "上海" in user_text and any(city in reply_text for city in ["厦门", "重庆", "南京"]):
        issues.append("门店城市答非所问")
    return issues


async def run_case(graph, trace_logger, case_name: str, turns: list[dict]) -> dict:
    history: list[str] = []
    rows: list[dict] = []
    customer_id = f"regression_{uuid4().hex[:8]}"

    for index, turn in enumerate(turns, start=1):
        request_id = str(uuid4())
        content = turn["content"]
        state: AgentState = {
            "request_id": request_id,
            "customer_id": customer_id,
            "corp_id": "ww916da62a08044243",
            "content": content,
            "conversation_history": history[-10:],
            "file_image": turn.get("file_image"),
            "user_id": 7294,
            "wechat": "yzm-yibingwen",
            "external_userid": "wmeERVIgAAmeVlaJ_YvK0exNEUMwPxTw",
            "trace": [],
            "errors": [],
        }
        started = time.perf_counter()
        final_state = await graph.ainvoke(state)
        duration = round(time.perf_counter() - started, 1)
        trace_logger.write_run(final_state)
        messages = final_state.get("reply_messages") or []
        reply_text = "\n".join(str(item.get("content") or "") for item in messages)
        history.append(f"用户: {content}")
        history.extend(f"小贝: {item.get('content', '')}" for item in messages if item.get("type") == "text")
        rows.append(
            {
                "turn": index,
                "user": content,
                "reply": reply_text,
                "intents": final_state.get("intents", []),
                "subflow": final_state.get("route_result", {}).get("subflow", ""),
                "tool_keys": list((final_state.get("tool_results") or {}).keys()),
                "duration_seconds": duration,
                "tokens": token_summary(final_state.get("trace", [])),
                "issues": judge_reply(content, reply_text),
                "request_id": request_id,
            }
        )
    return {"case": case_name, "turns": rows}


async def main() -> None:
    settings = get_settings()
    trace_logger = TraceLogger(settings)
    platform_agent_client = PlatformAgentClient(settings)
    graph = build_graph(
        CozeClient(settings),
        trace_logger,
        ModelClient(settings),
        CustomerMemoryStore(settings),
        LocalPricingRepository(settings),
        CustomerContextService(platform_agent_client),
        StoreService(platform_agent_client),
    )
    cases = [
        {
            "name": "淡斑完整生命周期",
            "turns": [
                {"content": "了解一下项目"},
                {"content": "我看看脸上的问题", "file_image": IMAGE_URL},
                {"content": "我这种能解决吗"},
                {"content": "主要斑，这个解决要多少钱"},
                {"content": "就是斑呀"},
                {"content": "那普通一次多少钱"},
                {"content": "我在上海，你们门店在哪里"},
                {"content": "你们是正规的吗"},
                {"content": "周六下午能去看看吗"},
            ],
        },
        {
            "name": "多意图单轮",
            "turns": [
                {"content": "我在上海，想淡斑，光子大概多少钱，附近有门店吗"},
                {"content": "有停车吗，另外你们靠谱吗"},
            ],
        },
        {
            "name": "售后风险边界",
            "turns": [
                {"content": "我做完光子后有点红，第二天了，能化妆吗"},
                {"content": "如果越来越红怎么办"},
            ],
        },
    ]
    report = []
    for case in cases:
        report.append(await run_case(graph, trace_logger, case["name"], case["turns"]))
    output_dir = ROOT / "logs" / "regression"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"chat_quality_{int(time.time())}.json"
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report_path": str(output_path), "cases": len(report)}, ensure_ascii=True))


if __name__ == "__main__":
    asyncio.run(main())
