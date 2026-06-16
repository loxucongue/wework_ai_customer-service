from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ai_paths"))

from app.main import chat_runtime, memory_store, sqlite_store  # noqa: E402
from app.schemas import ChatRequest  # noqa: E402


OUT_PATH = ROOT / "projects" / "public" / "test-conversations.json"
REPORT_PATH = ROOT / "logs" / "five_long_dialog_tests_report.json"

BASE = {
    "corp_id": "ww943af61cd5d2afe4",
    "customer_id": os.getenv("AI_PATHS_LONG_TEST_CUSTOMER_ID", "20615704"),
    "external_userid": os.getenv("AI_PATHS_LONG_TEST_EXTERNAL_USERID", "wmanzqsqaaygjwicitvmos657x39lqtg"),
    "customer_add_wechat_id": os.getenv("AI_PATHS_LONG_TEST_CUSTOMER_ADD_WECHAT_ID", "20615704"),
    "user_id": os.getenv("AI_PATHS_LONG_TEST_USER_ID", "7294"),
    "wechat": os.getenv("AI_PATHS_LONG_TEST_WECHAT", "CS001"),
}


SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "codex_long_store_distance",
        "title": "Codex长测-门店距离匹配到预约金",
        "turns": [
            "你好，在吗",
            "我想了解一下淡斑",
            "我在厦门机场附近",
            "离我最近的是哪家店",
            "把位置发我一个",
            "这个大概多久能做完",
            "今天下午可以过去吗",
            "如果可以我想先登记一个",
            "10元是怎么付",
            "我姓王，电话等下发你，可以先帮我留着吗",
            "那到店还要注意什么",
        ],
    },
    {
        "id": "codex_long_price_activity",
        "title": "Codex长测-项目价格活动与预约金",
        "turns": [
            "我脸上有黑色素可以做吗",
            "你们是用什么方法",
            "会不会伤皮肤",
            "多少钱，我看到广告说199",
            "确定是268吗",
            "是一次的费用吗",
            "到店会不会还要加钱",
            "那怎么报名",
            "我不想交定金，到店再付可以吗",
            "好吧，那10元怎么付",
            "我在上海徐汇附近",
        ],
    },
    {
        "id": "codex_long_case_effect",
        "title": "Codex长测-效果案例与信任建立",
        "turns": [
            "客户做完之后的效果我想看一下",
            "这个图片上的客户做了几次",
            "我这种斑也能这样吗",
            "有保障吗",
            "我之前做过两次没什么效果",
            "你们跟别人家有什么区别",
            "为什么一样的地方还有380的价格",
            "那现在参加是什么价",
            "我在深圳南山科技园附近",
            "推荐近一点的门店",
            "可以，帮我登记活动名额",
        ],
    },
    {
        "id": "codex_long_sensitive_trust",
        "title": "Codex长测-敏感肌质资质顾虑",
        "turns": [
            "我是敏感皮可以做吗",
            "我脸颊有点斑和泛红",
            "会不会越做越薄",
            "你们有资质吗",
            "你是门店的人吗",
            "到店会不会强制消费",
            "我老公说别被骗了",
            "太远了没时间去",
            "我在西安高新附近",
            "哪家门店方便一点",
            "周六下午能去看看吗",
        ],
    },
    {
        "id": "codex_long_complaint_refund",
        "title": "Codex长测-不满退款投诉承接",
        "turns": [
            "我之前去过你们店，一点效果都没有",
            "你们是不是骗人的",
            "我交了10元，现在不想去了",
            "把10元退给我，不然我投诉",
            "你们门店说还要额外加钱，怎么和你说的不一样",
            "我就要现在处理",
            "我之前是在厦门做的",
            "我忘了具体哪家店",
            "那你让人联系我",
            "需要我提供什么",
            "多久能有人处理",
        ],
    },
]


def _message_id(prefix: str, turn: int, index: int = 0) -> str:
    return f"{prefix}_{turn}_{index}_{int(time.time() * 1000)}"


def _history(messages: list[dict[str, Any]]) -> list[str]:
    rows: list[str] = []
    for message in messages[-10:]:
        role = "用户" if message.get("role") == "user" else "小贝"
        content = message.get("content")
        if isinstance(content, dict):
            text = content.get("text") or content.get("store_id") or content.get("order_id") or ""
        else:
            text = str(content or "")
        rows.append(f"{role}: {text}")
    return rows


def _content_to_preview(content: Any, msg_type: str) -> str:
    if isinstance(content, dict):
        if msg_type == "store_address":
            return str(content.get("store_id") or "")
        if msg_type == "book_order":
            return str(content.get("order_id") or "")
        return str(content.get("text") or content.get("handoff_reason") or "")
    return str(content or "")


def _response_messages(response: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, message in enumerate(response.reply_messages, start=1):
        data = message.model_dump() if hasattr(message, "model_dump") else dict(message)
        msg_type = data.get("type") or "text"
        content = data.get("content")
        preview = _content_to_preview(content, msg_type)
        rows.append(
            {
                "type": msg_type,
                "content": content,
                "preview": preview,
                "order": data.get("order", index),
            }
        )
    return rows


def _quality_flags(user_text: str, replies: list[dict[str, Any]], meta: dict[str, Any]) -> list[str]:
    texts = [r["preview"] for r in replies if r.get("type") == "text"]
    joined = "\n".join(texts)
    flags: list[str] = []
    if not replies:
        flags.append("空回复")
    if any(term in joined for term in ("知识库", "工具", "路由", "AI客服", "转人工")):
        flags.append("暴露系统痕迹")
    if len(joined) > 220:
        flags.append("回复偏长")
    if joined.count("？") + joined.count("?") > 2:
        flags.append("追问偏多")
    if any(term in user_text for term in ("地址", "位置", "定位", "怎么去", "发我一下")):
        if not any(r.get("type") == "store_address" for r in replies):
            flags.append("地址/定位场景未输出store_address")
    if _case_tool_returned_image(meta) and any(term in user_text for term in ("效果", "图片", "案例", "做完")):
        if not any(r.get("type") == "image" for r in replies) and "我之前做过" not in user_text:
            flags.append("效果/案例场景未输出image")
    if any(term in user_text for term in ("10元怎么付", "登记活动", "先登记", "报名")):
        if not any(r.get("type") == "book_order" for r in replies) and "需要门店" not in joined:
            flags.append("预约金/登记场景未输出book_order或缺少明确前置说明")
    if "投诉" in user_text or "退给我" in user_text:
        if not any(r.get("type") == "human_handoff" for r in replies):
            flags.append("投诉/退款场景未追加human_handoff")
    executed = meta.get("executed_tool_calls") or meta.get("tool_calls")
    if any(term in user_text for term in ("门店", "店", "机场", "附近", "位置", "地址")) and not executed:
        flags.append("疑似缺少工具执行记录")
    return flags


def _case_tool_returned_image(meta: dict[str, Any]) -> bool:
    for call in meta.get("executed_tool_calls") or meta.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        tool_input = call.get("input") if isinstance(call.get("input"), dict) else {}
        if str(tool_input.get("kb_name") or "") != "case_studies":
            continue
        output = call.get("output") if isinstance(call.get("output"), dict) else {}
        items = output.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            if any(str(item.get(key) or "").strip() for key in ("image_url", "imageUrl", "url", "file_url", "fileUrl", "cover_url", "coverUrl")):
                return True
            text = " ".join(str(item.get(key) or "") for key in ("content", "output", "text", "markdown"))
            if "http://" in text or "https://" in text:
                return True
    return False


async def _run_scenario(scenario: dict[str, Any], index: int) -> tuple[dict[str, Any], dict[str, Any]]:
    suffix = int(time.time())
    conversation_id = f"{scenario['id']}_{suffix}"
    customer_id = str(BASE.get("customer_id") or conversation_id)
    memory_store.clear(customer_id)
    messages: list[dict[str, Any]] = []
    turn_reports: list[dict[str, Any]] = []
    created_at = int(time.time() * 1000)

    for turn_index, content in enumerate(scenario["turns"], start=1):
        req = ChatRequest(
            content=content,
            customer_id=customer_id,
            corp_id=BASE["corp_id"],
            conversation_history=_history(messages),
            user_id=int(BASE["user_id"]),
            wechat=BASE["wechat"],
            external_userid=str(BASE.get("external_userid") or f"codex_long_external_{index:02d}"),
            customer_add_wechat_id=str(BASE.get("customer_add_wechat_id") or BASE.get("external_userid") or ""),
        )
        user_message = {
            "id": _message_id(f"{customer_id}_u", turn_index),
            "role": "user",
            "content": content,
            "timestamp": int(time.time() * 1000),
        }
        messages.append(user_message)
        started = time.perf_counter()
        try:
            response = await chat_runtime.run_chat(req)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            reply_rows = _response_messages(response)
            meta = response.meta if isinstance(response.meta, dict) else {}
            request_id = response.request_id
            intent = response.intent
            scene = response.scene
            subflow = response.subflow
            error = ""
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            reply_rows = [{"type": "text", "content": {"text": f"ERROR: {exc}"}, "preview": f"ERROR: {exc}", "order": 1}]
            meta = {}
            request_id = ""
            intent = ""
            scene = ""
            subflow = ""
            error = repr(exc)

        for reply_index, reply in enumerate(reply_rows, start=1):
            message: dict[str, Any] = {
                "id": _message_id(f"{customer_id}_a", turn_index, reply_index),
                "role": "assistant",
                "content": reply["preview"],
                "contentType": reply["type"],
                "timestamp": int(time.time() * 1000),
                "duration": elapsed_ms,
            }
            if reply["type"] == "image":
                message["imageUrl"] = reply["preview"]
            if reply_index == 1:
                message["meta"] = {
                    "intent": intent,
                    "scene": scene,
                    "subflow": subflow,
                    "requestId": request_id,
                    "plannedTools": meta.get("planned_tools") or meta.get("planner_required_tools"),
                    "executedToolCalls": meta.get("executed_tool_calls") or meta.get("tool_calls"),
                    "toolResultKeys": meta.get("tool_result_keys"),
                    "activeSceneId": meta.get("active_scene_id"),
                    "raw": meta,
                }
            messages.append(message)

        flags = _quality_flags(content, reply_rows, meta)
        turn_reports.append(
            {
                "turn": turn_index,
                "user": content,
                "replies": reply_rows,
                "elapsed_ms": elapsed_ms,
                "request_id": request_id,
                "intent": intent,
                "scene": scene,
                "subflow": subflow,
                "active_scene_id": meta.get("active_scene_id"),
                "planned_tools": meta.get("planned_tools") or meta.get("planner_required_tools"),
                "executed_tool_calls": meta.get("executed_tool_calls") or meta.get("tool_calls"),
                "quality_flags": flags,
                "error": error,
            }
        )

    conversation = {
        "id": conversation_id,
        "title": scenario["title"],
        "createdAt": created_at,
        "updatedAt": int(time.time() * 1000),
        "messages": messages,
    }
    report = {
        "id": conversation_id,
        "customer_id": customer_id,
        "title": scenario["title"],
        "turn_count": len(turn_reports),
        "issue_count": sum(len(item["quality_flags"]) for item in turn_reports),
        "turns": turn_reports,
    }
    return conversation, report


def _load_existing_conversations() -> dict[str, Any]:
    if not OUT_PATH.exists():
        return {"conversations": []}
    try:
        data = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"conversations": []}
    if isinstance(data, dict) and isinstance(data.get("conversations"), list):
        return data
    return {"conversations": []}


async def main() -> None:
    sqlite_store.initialize()
    conversations: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []
    for index, scenario in enumerate(SCENARIOS, start=1):
        conversation, report = await _run_scenario(scenario, index)
        conversations.append(conversation)
        reports.append(report)
        print(json.dumps({"completed": scenario["title"], "issues": report["issue_count"]}, ensure_ascii=False))

    data = _load_existing_conversations()
    existing = [item for item in data.get("conversations", []) if not str(item.get("id", "")).startswith("codex_long_")]
    data["conversations"] = conversations + existing
    data["summary"] = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "latest_batch": "five_long_dialog_tests",
        "conversation_count": len(conversations),
    }
    OUT_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(
            {
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "summary": {
                    "scenario_count": len(reports),
                    "turn_count": sum(item["turn_count"] for item in reports),
                    "issue_count": sum(item["issue_count"] for item in reports),
                },
                "reports": reports,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"output": str(OUT_PATH), "report": str(REPORT_PATH)}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
