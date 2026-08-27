from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
AI_PATHS = ROOT / "ai_paths"
if str(AI_PATHS) not in sys.path:
    sys.path.insert(0, str(AI_PATHS))

from app.config import Settings  # noqa: E402
from app.services.model_client import ModelClient  # noqa: E402
from app.services.sop_platform_task_service import SopPlatformTaskService  # noqa: E402


class UnusedDependency:
    pass


GROUP_TEXTS = {
    1: "我把您刚才查询的上海浦东店公开地址和门店卡发给您。",
    2: "周年庆活动价是268元，包含淡斑、皮肤检测、基础清洁和补水。",
    3: "我给您发两组真实客户做完后的效果参考，您可以先对比看看。",
    4: "活动名额可以先交10元预约金锁定，到店时抵扣。",
}


def _tasks() -> list[dict[str, Any]]:
    return [
        {
            "task_id": str(index),
            "scheduledAt": f"2026-08-27 09:0{index}:00",
            "sortOrder": index,
            "triggerEvent": "follow_up",
            "useAiCopy": False,
            "scene": {"name": f"sequence-{index}"},
            "message_content": [{"type": "text", "content": GROUP_TEXTS[index]}],
        }
        for index in range(1, 5)
    ]


def _context(history: list[tuple[str, str]]) -> dict[str, Any]:
    timeline = [
        {
            "message_ref": f"msg_{index:03d}",
            "role": role,
            "message_type": "text",
            "content": content,
        }
        for index, (role, content) in enumerate(history, start=1)
    ]
    return {
        "conversation_timeline": timeline,
        "timeline_structure": {
            "latest_message_role": history[-1][0],
            "customer_message_count": sum(role == "customer" for role, _ in history),
            "assistant_message_count": sum(role == "assistant" for role, _ in history),
        },
        "customer_relation": {"status": "active", "is_deleted": False},
        "business_state": {},
    }


CASES = [
    ("send_first", "1", [("customer", "浦东店的地址和门店卡发我看下。")]),
    (
        "skip_1_send_2",
        "2",
        [("assistant", GROUP_TEXTS[1]), ("customer", "活动具体多少钱？")],
    ),
    (
        "skip_1_2_send_3",
        "3",
        [
            ("assistant", GROUP_TEXTS[1]),
            ("assistant", GROUP_TEXTS[2]),
            ("customer", "有真实效果图可以看看吗？"),
        ],
    ),
    (
        "skip_1_2_3_send_4",
        "4",
        [
            ("assistant", GROUP_TEXTS[1]),
            ("assistant", GROUP_TEXTS[2]),
            ("assistant", GROUP_TEXTS[3]),
            ("customer", "我参加，预约金怎么付？"),
        ],
    ),
    (
        "all_skip_after_explicit_stop",
        "",
        [
            ("assistant", GROUP_TEXTS[1]),
            ("assistant", GROUP_TEXTS[2]),
            ("assistant", GROUP_TEXTS[3]),
            ("assistant", GROUP_TEXTS[4]),
            ("customer", "不要再发了。"),
        ],
    ),
]


async def run(output_path: Path) -> dict[str, Any]:
    settings = Settings()
    model = ModelClient(settings)
    service = SopPlatformTaskService(
        settings=settings,
        repository=UnusedDependency(),
        platform_client=UnusedDependency(),
        system_client=UnusedDependency(),
        model_client=model,
        customer_context_service=UnusedDependency(),
    )
    results: list[dict[str, Any]] = []
    try:
        for scenario_id, expected_selected, history in CASES:
            started = asyncio.get_running_loop().time()
            try:
                decision = await service._decide_customer_batch(_tasks(), context=_context(history))
                evaluated_ids = [str(item.get("task_id") or "") for item in decision["evaluations"]]
                expected_last = int(expected_selected) if expected_selected else 4
                expected_prefix = [str(index) for index in range(1, expected_last + 1)]
                selected = str(decision.get("selected_task_id") or "")
                results.append(
                    {
                        "scenario_id": scenario_id,
                        "expected_selected_task_id": expected_selected,
                        "selected_task_id": selected,
                        "expected_evaluated_prefix": expected_prefix,
                        "evaluated_task_ids": evaluated_ids,
                        "decision": decision,
                        "passed": selected == expected_selected and evaluated_ids == expected_prefix,
                        "duration_ms": int((asyncio.get_running_loop().time() - started) * 1000),
                    }
                )
            except Exception as exc:
                results.append(
                    {
                        "scenario_id": scenario_id,
                        "expected_selected_task_id": expected_selected,
                        "passed": False,
                        "error": f"{type(exc).__name__}: {exc}",
                        "duration_ms": int((asyncio.get_running_loop().time() - started) * 1000),
                    }
                )
    finally:
        await model.aclose()
    report = {
        "schema_version": "sop_deepseek_sequence_matrix_v1",
        "model": settings.sop_platform_decision_model,
        "case_count": len(results),
        "passed_count": sum(bool(item.get("passed")) for item in results),
        "all_passed": all(bool(item.get("passed")) for item in results),
        "results": results,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe DeepSeek SOP sequential group decisions without platform writes.")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = asyncio.run(run(Path(args.output)))
    print(
        json.dumps(
            {
                "output": args.output,
                "passed_count": report["passed_count"],
                "case_count": report["case_count"],
                "all_passed": report["all_passed"],
            },
            ensure_ascii=False,
        )
    )
    if not report["all_passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
