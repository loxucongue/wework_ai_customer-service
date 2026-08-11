from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from app.chat_runtime import ChatRuntime
from app.schemas import ChatRequest
from app.services.sop_execution_service import SopExecutionService


class BackgroundTasks:
    def __init__(self) -> None:
        self.tasks: list[tuple[Any, tuple[Any, ...], dict[str, Any]]] = []

    def add_task(self, func: Any, *args: Any, **kwargs: Any) -> None:
        self.tasks.append((func, args, kwargs))

    async def run_all(self) -> None:
        while self.tasks:
            func, args, kwargs = self.tasks.pop(0)
            result = func(*args, **kwargs)
            if hasattr(result, "__await__"):
                await result


class Repository:
    def __init__(self) -> None:
        self.sent_sop_ids: set[str] = set()
        self.saved_states: list[dict[str, Any]] = []
        self.assistant_messages: list[dict[str, Any]] = []
        self.user_messages: list[dict[str, Any]] = []
        self.sop_tasks: dict[str, dict[str, Any]] = {}
        self.sop_events: dict[str, dict[str, Any]] = {}
        self.sent_sop_categories: set[str] = set()

    def upsert_conversation(self, **kwargs: Any) -> None:
        return None

    def add_user_message(self, **kwargs: Any) -> None:
        self.user_messages.append(kwargs)

    def add_assistant_message(self, **kwargs: Any) -> None:
        self.assistant_messages.append(kwargs)

    def save_run(self, *, conversation_id: str, final_state: dict[str, Any], token_usage: dict[str, Any]) -> None:
        self.saved_states.append(dict(final_state))

    def list_sent_sop_pack_ids_for_customer(
        self, *, customer_id: str, external_userid: str, corp_id: str = "", wechat: str = ""
    ) -> list[str]:
        return sorted(self.sent_sop_ids)

    def list_sent_sop_categories_for_customer(
        self, *, customer_id: str, external_userid: str, corp_id: str = "", wechat: str = ""
    ) -> list[str]:
        return sorted(self.sent_sop_categories)

    def create_sop_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        event_id = str(payload.get("event_id") or "")
        created = event_id not in self.sop_events
        self.sop_events.setdefault(event_id, {"event_id": event_id, "raw_payload": payload, "created": created})
        event = dict(self.sop_events[event_id])
        event["created"] = created
        return event

    def create_sop_send_task(self, **kwargs: Any) -> dict[str, Any]:
        task_id = f"sop_task_{len(self.sop_tasks) + 1}"
        task = {"id": task_id, **kwargs, "created": True}
        self.sop_tasks[task_id] = task
        return dict(task)

    def update_sop_send_task(
        self,
        task_id: str,
        *,
        status: str,
        send_payload: dict[str, Any] | None = None,
        send_response: dict[str, Any] | None = None,
        error: str = "",
        sent_at: str = "",
    ) -> dict[str, Any]:
        task = self.sop_tasks.get(task_id, {"id": task_id})
        task.update(
            {
                "status": status,
                "send_payload": send_payload or {},
                "send_response": send_response or {},
                "error": error,
                "sent_at": sent_at,
            }
        )
        if status == "sent" and task.get("sop_pack_id"):
            self.sent_sop_ids.add(str(task["sop_pack_id"]))
        if status == "sent" and task.get("sop_category"):
            self.sent_sop_categories.add(str(task["sop_category"]))
        self.sop_tasks[task_id] = task
        return dict(task)


class TraceLogger:
    def write_run(self, state: dict[str, Any]) -> str:
        return f"logs/runs/{state.get('request_id')}.json"


class FakeSelectorModel:
    def __init__(self) -> None:
        self.last_usage: dict[str, Any] = {}

    async def chat_json(self, messages: list[dict[str, Any]], *, tier: str = "reply", temperature: float = 0) -> dict[str, Any]:
        text = json.dumps(messages, ensure_ascii=False)
        for pack_id in [
            "s10_new_customer_opening",
            "s10_need_and_case",
            "s10_activity_intro",
            "s10_objection_resolution",
            "s10_deposit_close",
        ]:
            if pack_id in text:
                return {
                    "send_sop": True,
                    "sop_pack_id": pack_id,
                    "need_ai_reply": False,
                    "reason": f"选择未完成 SOP：{pack_id}",
                }
        return {"send_sop": False, "sop_pack_id": "", "need_ai_reply": False, "reason": "SOP 已完成"}


class FixtureSopPackService:
    def load(self) -> dict[str, Any]:
        return {
            "version": 1,
            "updated_at": "",
            "packs": [
                {
                    "id": "s10_new_customer_opening",
                    "enabled": True,
                    "scope": "chat_gate",
                    "sop_category": "opening",
                    "name": "新客破冰",
                    "purpose": "新客首次加微后的基础破冰，建立信任并引导客户说出位置和主要诉求。",
                    "order": 10,
                    "send_once": True,
                    "reply_messages": [
                        {"type": "text", "order": 1, "content": {"text": "亲，您好呀，我们这边主要做斑点和皮肤管理这块。"}},
                        {"type": "text", "order": 2, "content": {"text": "您现在是在什么城市哪个区？我先帮您看下离您近的门店。"}},
                    ],
                },
                {
                    "id": "s10_need_and_case",
                    "enabled": True,
                    "scope": "chat_gate",
                    "sop_category": "effect_case",
                    "name": "需求与效果承接",
                    "purpose": "客户第一次问斑点、效果或是否能做时，承接需求并发送效果案例参考。",
                    "order": 20,
                    "send_once": True,
                    "reply_messages": [
                        {"type": "text", "order": 1, "content": {"text": "可以做的，像雀斑、晒斑、色沉这类，一般都会先看深浅和分布。"}},
                        {"type": "text", "order": 2, "content": {"text": "我先发您一张同类改善参考，您先感受一下。"}},
                        {"type": "image", "order": 3, "content": {"url": "https://example.com/case.jpg"}},
                    ],
                },
                {
                    "id": "s10_activity_intro",
                    "enabled": True,
                    "scope": "chat_gate",
                    "sop_category": "activity_intro",
                    "name": "活动介绍",
                    "purpose": "客户第一次了解活动、价格或预约金时，说明活动价值、预约金用途和费用规则。",
                    "order": 30,
                    "send_once": True,
                    "reply_messages": [
                        {"type": "text", "order": 1, "content": {"text": "现在做的是周年活动，活动价268。"}},
                        {"type": "text", "order": 2, "content": {"text": "线上先交10元预约金，是帮您锁活动名额，到店直接抵扣，未做或不满意可退。"}},
                        {"type": "image", "order": 3, "content": {"url": "https://example.com/activity.jpg"}},
                    ],
                },
                {
                    "id": "s10_objection_resolution",
                    "enabled": True,
                    "scope": "chat_gate",
                    "sop_category": "intro",
                    "name": "顾虑处理",
                    "purpose": "客户担心效果、套路、乱收费、预约金或时间时，先解除最大顾虑再推进下一步。",
                    "order": 40,
                    "send_once": True,
                    "reply_messages": [
                        {"type": "text", "order": 1, "content": {"text": "您担心效果、恢复或者乱收费这些，都可以直接问我，我这边先跟您说清楚。"}},
                        {"type": "video", "order": 2, "content": {"url": "https://example.com/process.mp4"}},
                    ],
                },
                {
                    "id": "s10_deposit_close",
                    "enabled": True,
                    "scope": "chat_gate",
                    "sop_category": "deposit_push",
                    "name": "预约金推进",
                    "purpose": "客户已有明确兴趣或愿意登记时，推进 10 元预约金入口。",
                    "order": 50,
                    "send_once": True,
                    "reply_messages": [
                        {"type": "text", "order": 1, "content": {"text": "您要是想先把活动名额留住，我这边可以先发10元预约入口。"}},
                        {"type": "payment_collection", "order": 2, "content": {"amount": 10, "remark": "锁活动名额，到店抵扣"}},
                    ],
                },
            ],
        }


class FullGraph:
    async def ainvoke(self, state: dict[str, Any]) -> dict[str, Any]:
        content = str(state.get("content") or "")
        output = dict(state)
        output["trace"] = list(state.get("trace") or []) + [
            {"node": "fake_full_ai", "duration_ms": 12, "output_snapshot": {"content": content[:80]}}
        ]
        output["errors"] = []
        output.update(
            {
                "planner_decision": "direct_reply",
                "planner_stage": "S3",
                "planner_sub_rule_id": "fake_ai_reply",
                "reply_messages": _ai_reply_for(content),
            }
        )
        return output


class OutreachClient:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_reply_messages(self, **kwargs: Any) -> dict[str, Any]:
        self.sent.append(kwargs)
        return {"status": "sent", "send_payload": kwargs, "response": {"code": 0, "msg": "ok"}}


def _ai_reply_for(content: str) -> list[dict[str, Any]]:
    if "多少钱" in content or "价格" in content:
        return [
            {"type": "text", "order": 1, "content": {"text": "活动价是268，先付10元锁活动名额，到店抵扣，做付258，未做或不满意可退。"}},
            {"type": "text", "order": 2, "content": {"text": "您现在方便的话，我先帮您把名额留住。"}},
        ]
    if "乱收费" in content or "骗人" in content or "真的" in content:
        return [
            {"type": "text", "order": 1, "content": {"text": "这个您放心，我们是集团连锁，全国300多家，费用会提前讲清楚，认可再做。"}},
            {"type": "text", "order": 2, "content": {"text": "您要是方便，我先按离您近的门店给您看名额。"}},
        ]
    if "预约" in content or "报名" in content or "入口" in content:
        return [
            {"type": "text", "order": 1, "content": {"text": "可以，我先给您发10元预约入口，付完就能锁活动名额。"}},
            {"type": "payment_collection", "order": 2, "content": {"amount": 10, "remark": "锁活动名额，到店抵扣"}},
        ]
    if "斑" in content or "效果" in content:
        return [
            {"type": "text", "order": 1, "content": {"text": "可以看的，斑点一般先看深浅和分布，到店会先检测，不是直接让您做。"}},
            {"type": "text", "order": 2, "content": {"text": "您是在厦门思明附近吗？我先帮您匹配近一点的门店。"}},
        ]
    return [{"type": "text", "order": 1, "content": {"text": "好的，我这边先帮您看。"}}]


async def main() -> None:
    repository = Repository()
    sop_execution_service = SopExecutionService(
        repository=repository,
        sop_reply_pack_service=FixtureSopPackService(),
        model_client=FakeSelectorModel(),
    )
    outreach = OutreachClient()
    runtime = ChatRuntime(
        full_graph=FullGraph(),
        trace_logger=TraceLogger(),
        repository=repository,
        outreach_send_client=outreach,
        sop_execution_service=sop_execution_service,
    )
    turns = [
        "你好",
        "我脸上有斑能做吗",
        "这个多少钱",
        "不会乱收费吧，真的假的",
        "可以，怎么预约",
        "那发我入口吧",
    ]
    history: list[str] = []
    report: list[dict[str, Any]] = []
    for turn_index, content in enumerate(turns, start=1):
        background_tasks = BackgroundTasks()
        request = ChatRequest(
            content=content,
            customer_id="codex_new_customer_sop_e2e",
            corp_id="ww943af61cd5d2afe4",
            conversation_history=history[-10:],
            user_id=7294,
            wechat="CS001",
            external_userid="codex_new_customer_sop_e2e_ext",
            request_context={
                "source_protocol": "workflow-compatible",
                "test_isolated": True,
                "memory_persist_allowed": False,
            },
        )
        started = time.perf_counter()
        response = await runtime.run_platform_reply(request, background_tasks=background_tasks)
        sync_ms = round((time.perf_counter() - started) * 1000, 1)
        state = repository.saved_states[-1]
        sop_gate = state.get("sop_gate") or {}
        before_async = len(outreach.sent)
        async_started = time.perf_counter()
        await background_tasks.run_all()
        async_ms = round((time.perf_counter() - async_started) * 1000, 1)
        sync_messages = [message.model_dump() for message in response.reply_messages]
        async_messages: list[dict[str, Any]] = []
        for item in outreach.sent[before_async:]:
            async_messages.extend(item.get("reply_messages") or [])
        report.append(
            {
                "turn": turn_index,
                "customer": content,
                "sync_ms": sync_ms,
                "async_ms": async_ms,
                "sop_mode": sop_gate.get("mode"),
                "sop_pack_id": sop_gate.get("sop_pack_id"),
                "sop_need_ai_reply": sop_gate.get("need_ai_reply"),
                "sop_error": sop_gate.get("error"),
                "sop_reason": sop_gate.get("reason"),
                "sync_messages": sync_messages,
                "async_messages": async_messages,
                "completed_sops_after_turn": sorted(repository.sent_sop_ids),
            }
        )
        history.append("用户: " + content)
        for message in sync_messages:
            history.append("小贝: " + _visible(message))
        for message in async_messages:
            history.append("小贝: " + _visible(message))
    print(json.dumps(report, ensure_ascii=False, indent=2))


def _visible(message: dict[str, Any]) -> str:
    message_type = str(message.get("type") or "")
    content = message.get("content") if isinstance(message.get("content"), dict) else {}
    if message_type == "text":
        return str(content.get("text") or "")
    if message_type == "image":
        return "[图片]" + str(content.get("url") or "")[:80]
    if message_type == "video":
        return "[视频]" + str(content.get("url") or "")[:80]
    if message_type == "payment_collection":
        return "[预约金收款]" + str(content.get("amount") or 10)
    return f"[{message_type}]"


if __name__ == "__main__":
    asyncio.run(main())
