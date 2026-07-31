from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

from app.config import Settings
from app.services.model_client import ModelClient
from app.services.sop_event_decision import normalize_event_decision
from app.services.sop_execution_service import SOP_EVENT_SYSTEM_PROMPT


def _text_message(order: int, text: str) -> dict[str, Any]:
    return {"order": order, "type": "text", "content": {"text": text}}


def _conversation_message(
    ref: str,
    direction: str,
    content: str,
    message_time: str,
) -> dict[str, Any]:
    return {
        "message_ref": ref,
        "direction": direction,
        "content": content,
        "message_time": message_time,
        "message_type": "text",
    }


def _selector(
    *,
    name: str,
    customer_text: str,
    assistant_text: str,
    elapsed_minutes: int,
    action_messages: list[dict[str, Any]],
    resumed_customer_text: str = "",
    resumed_assistant_text: str = "",
    activity_quote_completed: bool = False,
) -> dict[str, Any]:
    recent = [
        _conversation_message("conv_1", "customer", customer_text, "2026-07-31T09:00:00+08:00"),
        _conversation_message("conv_2", "assistant", assistant_text, "2026-07-31T09:01:00+08:00"),
    ]
    assistant_elapsed = {"conv_2": elapsed_minutes}
    latest_customer_ref = "conv_1"
    latest_assistant_ref = "conv_2"
    if resumed_customer_text:
        recent.append(
            _conversation_message(
                "conv_3",
                "customer",
                resumed_customer_text,
                "2026-07-31T15:20:00+08:00",
            )
        )
        latest_customer_ref = "conv_3"
    if resumed_assistant_text:
        recent.append(
            _conversation_message(
                "conv_4",
                "assistant",
                resumed_assistant_text,
                "2026-07-31T15:21:00+08:00",
            )
        )
        latest_assistant_ref = "conv_4"
        assistant_elapsed["conv_4"] = 15

    editable = []
    readonly = []
    for message in action_messages:
        message_type = str(message.get("type") or "")
        content = message.get("content") if isinstance(message.get("content"), dict) else {}
        if message_type == "text":
            editable.append({"order": message.get("order"), "text": content.get("text")})
        else:
            readonly.append(
                {
                    "order": message.get("order"),
                    "type": message_type,
                    "content": content,
                }
            )

    structured_events: list[dict[str, Any]] = []
    if activity_quote_completed:
        structured_events.append(
            {
                "event_type": "activity_quote_completed",
                "event_time": "2026-07-31T08:30:00+08:00",
                "facts": {"activity_price": 268, "deposit_amount": 10},
                "source": "sop_delivery",
            }
        )

    return {
        "test_scenario": name,
        "mode": "platform_actions",
        "event": {
            "event_type": "sop_platform_task",
            "created_at": "2026-07-31T15:36:00+08:00",
        },
        "current_platform_task": {
            "priority": "current_outreach_objective_after_hard_facts",
            "message_content": action_messages,
        },
        "recent_conversation": recent,
        "conversation_activity": {
            "assistant_waiting_customer": not bool(resumed_customer_text),
            "latest_customer_pending_ai_reply": False,
            "silence_after_assistant_minutes": 15 if resumed_assistant_text else elapsed_minutes,
            "event_at": "2026-07-31T15:36:00+08:00",
        },
        "contact_availability_evidence": {
            "latest_customer_message_ref": latest_customer_ref,
            "latest_assistant_message_ref": latest_assistant_ref,
            "assistant_waiting_customer": not bool(resumed_customer_text),
            "minutes_since_latest_assistant": 15 if resumed_assistant_text else elapsed_minutes,
            "assistant_message_elapsed_minutes": assistant_elapsed,
            "customer_messages_after_latest_assistant": 0,
            "evidence_policy": "model_decides_semantics_code_validates_references_and_order",
        },
        "candidate_sops": [],
        "mainline_stage_status": [],
        "platform_actions_summary": [
            {"order": message.get("order"), "type": message.get("type")}
            for message in action_messages
        ],
        "platform_actions": {
            "editable_text_messages": editable,
            "readonly_messages": readonly,
        },
        "platform_payment_collection_gate": {
            "status": "supported" if activity_quote_completed else "not_required"
        },
        "current_payment_state": {"status": "unpaid"},
        "customer_fact_snapshot": {
            "basic_facts": {"deposit_state": {"status": "unpaid"}},
            "recent_structured_events": structured_events,
            "priority": "current_conversation_and_realtime_order_facts_win",
        },
        "completed_sop_pack_ids": [],
        "completed_sop_categories": ["activity_intro"] if activity_quote_completed else [],
        "event_policy_evidence": {
            "daily_soft_limit_reached": False,
            "ai_reply_policy": {"allowed": False},
        },
    }


def _scenarios() -> list[dict[str, Any]]:
    payment_actions = [
        _text_message(1, "活动名额这边还可以登记，您确定参加的话可以先付10元预约金。"),
        {"order": 2, "type": "payment_collection", "content": {"amount": 10}},
    ]
    value_actions = [
        _text_message(1, "我给您补充一个淡斑后的护理要点，平时注意防晒和补水会更稳。"),
        {
            "order": 2,
            "type": "image",
            "content": {"url": "https://example.invalid/configured-asset.jpg"},
        },
    ]
    scenarios = []
    for minutes, phrase in (
        (30, "我在上班，晚点说"),
        (60, "我现在工作中，不方便聊"),
        (300, "我在开车，一会儿联系"),
    ):
        scenarios.append(
            _selector(
                name=f"busy_{minutes}m",
                customer_text=phrase,
                assistant_text="好嘞，您先忙，方便了再回我就行。",
                elapsed_minutes=minutes,
                action_messages=payment_actions,
                activity_quote_completed=True,
            )
        )
    scenarios.extend(
        [
            _selector(
                name="busy_420m",
                customer_text="我先忙工作，晚点再聊",
                assistant_text="好，您先忙，方便了再说。",
                elapsed_minutes=420,
                action_messages=payment_actions,
                activity_quote_completed=True,
            ),
            _selector(
                name="resumed_available",
                customer_text="我先忙工作，晚点再聊",
                assistant_text="好，您先忙，方便了再说。",
                elapsed_minutes=420,
                action_messages=value_actions,
                resumed_customer_text="现在有空了",
                resumed_assistant_text="好嘞，那我们接着聊。",
            ),
            _selector(
                name="resumed_payment_request",
                customer_text="我先忙工作，晚点再聊",
                assistant_text="好，您先忙，方便了再说。",
                elapsed_minutes=420,
                action_messages=payment_actions,
                resumed_customer_text="怎么交，发卡给我",
                resumed_assistant_text="可以，我现在给您接上。",
                activity_quote_completed=True,
            ),
            _selector(
                name="ordinary_silence",
                customer_text="效果怎么样",
                assistant_text="不少顾客做完都能看到比较直观的改善，我再给您补一个护理参考。",
                elapsed_minutes=180,
                action_messages=value_actions,
            ),
        ]
    )
    return scenarios


def _evaluate_expectation(name: str, normalized: dict[str, Any], violations: list[str]) -> tuple[bool, str]:
    if violations:
        return False, ",".join(violations)
    decision = str(normalized.get("decision") or "")
    guard = normalized.get("availability_guard") if isinstance(normalized.get("availability_guard"), dict) else {}
    messages = normalized.get("ai_touch_messages") if isinstance(normalized.get("ai_touch_messages"), list) else []
    if name in {"busy_30m", "busy_60m", "busy_300m"}:
        ok = bool(guard.get("active")) and decision in {"skip", "defer"} and not normalized.get("send_sop")
        return ok, "busy_within_6h_must_not_send"
    if name == "busy_420m":
        ok = bool(guard.get("active")) and decision in {"skip", "defer", "send_ai_touch"}
        if decision == "send_ai_touch":
            ok = ok and len(messages) == 1 and messages[0].get("type") == "text"
        return ok, "busy_after_6h_only_skip_defer_or_one_text"
    ok = not bool(guard.get("active")) and decision == "send"
    return ok, "new_progress_or_ordinary_silence_should_resume_platform_action"


async def _run_case(
    client: ModelClient,
    scenario: dict[str, Any],
    attempt: int,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    messages = [
        {"role": "system", "content": SOP_EVENT_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": "根据系统提示词和以下 JSON 输入，返回严格 JSON。\n"
            + json.dumps(scenario, ensure_ascii=False, separators=(",", ":")),
        },
    ]
    try:
        async with semaphore:
            started = time.perf_counter()
            raw = await client.chat_json(messages, tier="reply", temperature=0)
            duration_ms = int((time.perf_counter() - started) * 1000)
        normalized, violations = normalize_event_decision(raw, scenario)
        passed, expectation = _evaluate_expectation(
            str(scenario.get("test_scenario") or ""),
            normalized,
            violations,
        )
        return {
            "scenario": scenario.get("test_scenario"),
            "attempt": attempt,
            "passed": passed,
            "expectation": expectation,
            "duration_ms": duration_ms,
            "raw": raw,
            "normalized": normalized,
            "violations": violations,
            "model_usage": client.last_usage or {},
        }
    except Exception as exc:
        return {
            "scenario": scenario.get("test_scenario"),
            "attempt": attempt,
            "passed": False,
            "expectation": "model_call_succeeded",
            "duration_ms": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }


async def _main(args: argparse.Namespace) -> int:
    settings = Settings(_env_file=args.env_file)
    client = ModelClient(settings)
    if not client.available:
        raise RuntimeError("No model API key configured")
    semaphore = asyncio.Semaphore(max(1, args.concurrency))
    tasks = [
        _run_case(client, scenario, attempt, semaphore)
        for scenario in _scenarios()
        for attempt in range(1, args.attempts + 1)
    ]
    results = await asyncio.gather(*tasks)
    summary: dict[str, Any] = {
        "total": len(results),
        "passed": sum(1 for item in results if item.get("passed")),
        "failed": sum(1 for item in results if not item.get("passed")),
        "by_scenario": {},
    }
    for scenario in _scenarios():
        name = str(scenario.get("test_scenario") or "")
        scoped = [item for item in results if item.get("scenario") == name]
        summary["by_scenario"][name] = {
            "passed": sum(1 for item in scoped if item.get("passed")),
            "total": len(scoped),
            "decisions": [item.get("normalized", {}).get("decision") for item in scoped],
            "durations_ms": [item.get("duration_ms") for item in scoped],
        }
    report = {"summary": summary, "results": results}
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["failed"] == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--report", default=".tmp_runtime/sop_availability_model_matrix.json")
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=2)
    return asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
