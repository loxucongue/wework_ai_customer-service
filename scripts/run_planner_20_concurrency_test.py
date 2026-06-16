from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ai_paths"))

from app.config import get_settings  # noqa: E402
from app.graph.planner.brain_v2 import PLANNER_MODEL_NAMES, run_planner_brain_v2  # noqa: E402
from app.services.model_client import ModelClient  # noqa: E402


@dataclass(frozen=True)
class PlannerCase:
    case_id: int
    stage: str
    scenario: str
    message: str
    history: list[dict[str, str]] = field(default_factory=list)
    request_context: dict[str, Any] = field(default_factory=dict)
    expected_types: tuple[str, ...] = ()
    expected_stage: str = ""
    expected_tools: tuple[str, ...] = ()
    notes: str = ""


CASES: list[PlannerCase] = [
    PlannerCase(
        1,
        "S1 打招呼",
        "新客破冰",
        "你好，在吗",
        expected_types=("opening", "greeting", "general", "project_consult"),
        expected_stage="S1",
        expected_tools=("kb_search:sales_talk_qa",),
    ),
    PlannerCase(
        2,
        "S1 介绍",
        "淡斑需求承接",
        "我想了解一下淡斑",
        expected_types=("project_consult", "general"),
        expected_stage="S1",
        expected_tools=("kb_search:sales_talk_qa",),
    ),
    PlannerCase(
        3,
        "S1 疑问解答",
        "技术方法咨询",
        "你们祛斑用什么方法",
        expected_types=("project_consult",),
        expected_stage="S1",
        expected_tools=("kb_search:sales_talk_qa",),
    ),
    PlannerCase(
        4,
        "S1 疑问解答",
        "黑色素改善方向",
        "我脸上黑色素可以做吗",
        expected_types=("project_consult",),
        expected_stage="S1",
        expected_tools=("kb_search:sales_talk_qa",),
    ),
    PlannerCase(
        5,
        "S1 疑问解答",
        "敏感肌顾虑",
        "我是敏感皮可以做吗",
        expected_types=("project_consult", "trust_issue"),
        expected_stage="S1",
        expected_tools=("kb_search:sales_talk_qa",),
    ),
    PlannerCase(
        6,
        "S3 报价铺垫",
        "效果案例诉求",
        "客户做完之后的效果我想看一下",
        expected_types=("case_request", "project_consult"),
        expected_stage="S1|S3",
        expected_tools=("kb_search:case_studies",),
    ),
    PlannerCase(
        7,
        "S3 报价铺垫",
        "案例次数追问",
        "图片上的客户做了多少次",
        history=[
            {"role": "customer", "content": "客户做完之后的效果我想看一下"},
            {"role": "assistant", "content": "可以看同类改善参考，我发您看一下。"},
        ],
        expected_types=("case_request", "project_consult"),
        expected_stage="S3",
        expected_tools=("kb_search:sales_talk_qa",),
    ),
    PlannerCase(
        8,
        "S3 报价",
        "价格纠偏",
        "多少钱，我看广告说199",
        expected_types=("price_inquiry",),
        expected_stage="S3",
        expected_tools=("kb_search:sales_talk_qa",),
    ),
    PlannerCase(
        9,
        "S3 报价",
        "是否一次费用",
        "是一次的费用吗",
        expected_types=("price_inquiry",),
        expected_stage="S3",
        expected_tools=("kb_search:sales_talk_qa",),
    ),
    PlannerCase(
        10,
        "S3 报价",
        "费用透明顾虑",
        "到店会乱收费吗",
        expected_types=("price_inquiry", "trust_issue"),
        expected_stage="S3",
        expected_tools=("kb_search:sales_talk_qa",),
    ),
    PlannerCase(
        11,
        "S3 报价",
        "竞品比价",
        "别家说380，你们怎么268",
        expected_types=("competitor_compare", "price_inquiry"),
        expected_stage="S3",
        expected_tools=("kb_search:sales_talk_qa",),
    ),
    PlannerCase(
        12,
        "S2 问地址",
        "城市画像",
        "我在深圳",
        expected_types=("store_inquiry",),
        expected_stage="S2",
        expected_tools=("store_lookup", "kb_search:sales_talk_qa"),
    ),
    PlannerCase(
        13,
        "S2 地址发送",
        "地标匹配门店",
        "我在厦门机场附近",
        expected_types=("store_inquiry",),
        expected_stage="S2",
        expected_tools=("store_lookup", "kb_search:sales_talk_qa"),
        notes="store_lookup 应带 distance_origin",
    ),
    PlannerCase(
        14,
        "S2 地址发送",
        "门店卡片",
        "把最近门店位置发我",
        history=[{"role": "customer", "content": "我在南山科技园附近"}],
        expected_types=("store_inquiry",),
        expected_stage="S2",
        expected_tools=("store_lookup", "kb_search:sales_talk_qa"),
    ),
    PlannerCase(
        15,
        "S3 收单",
        "到店档期",
        "周六下午3点能去吗",
        history=[
            {"role": "customer", "content": "我在厦门机场附近"},
            {"role": "assistant", "content": "离您近一点可以先看厦门二店。"},
        ],
        expected_types=("appointment", "store_inquiry"),
        expected_stage="S3",
        expected_tools=("store_lookup", "available_time", "kb_search:sales_talk_qa"),
    ),
    PlannerCase(
        16,
        "S3 收单",
        "预约金意向",
        "可以，帮我登记10元预约金",
        history=[
            {"role": "customer", "content": "我在厦门机场附近"},
            {"role": "assistant", "content": "厦门二店离您更近，可以先登记名额。"},
        ],
        expected_types=("appointment", "price_inquiry"),
        expected_stage="S3",
        expected_tools=("kb_search:sales_talk_qa",),
        notes="缺姓名电话/真实建单时不应直接假预约成功",
    ),
    PlannerCase(
        17,
        "S3 逼单",
        "不交定金异议",
        "我不交定金到店再付全款",
        expected_types=("price_inquiry", "trust_issue"),
        expected_stage="S3",
        expected_tools=("kb_search:sales_talk_qa",),
    ),
    PlannerCase(
        18,
        "S3 信任",
        "资质顾虑",
        "你们有资质吗",
        expected_types=("trust_issue",),
        expected_stage="S1|S3",
        expected_tools=("kb_search:sales_talk_qa",),
    ),
    PlannerCase(
        19,
        "S4 投诉退款",
        "退款投诉",
        "把10元退给我，不然我投诉",
        expected_types=("human_request", "complaint_refund"),
        expected_stage="S4",
        expected_tools=("professional_assist",),
    ),
    PlannerCase(
        20,
        "S4 售后反馈",
        "效果不满",
        "做了2次不见效果呢",
        expected_types=("after_sales", "human_request", "complaint_refund"),
        expected_stage="S4",
        expected_tools=("kb_search:sales_talk_qa",),
    ),
]


def _state_for_case(case: PlannerCase) -> dict[str, Any]:
    return {
        "normalized_content": case.message,
        "conversation_history": case.history,
        "image_info": {},
        "request_context": {
            "category_id": "S10",
            "customer_stage": case.stage,
            **case.request_context,
        },
        "customer_context": {},
        "customer_profile": {},
        "customer_basic_info": {},
        "history_events": [],
        "appointment_cache": {},
    }


def _tool_keys(tools: list[dict[str, Any]]) -> list[str]:
    output: list[str] = []
    for tool in tools:
        name = str(tool.get("name") or "").strip()
        kb_name = str(tool.get("kb_name") or "").strip()
        if name == "kb_search" and kb_name:
            output.append(f"{name}:{kb_name}")
        elif name:
            output.append(name)
    return output


def _has_tool(tool_keys: list[str], expected: str) -> bool:
    if ":" in expected:
        return expected in tool_keys
    return any(item == expected or item.startswith(f"{expected}:") for item in tool_keys)


def _judge(case: PlannerCase, plan: dict[str, Any]) -> tuple[bool, str]:
    primary = plan.get("primary_task") or {}
    actual_type = str(primary.get("type") or "").strip()
    actual_stage = str(plan.get("sop_stage") or primary.get("sop_stage") or "").strip()
    tool_keys = _tool_keys(plan.get("required_tools") or [])
    failures: list[str] = []
    if case.expected_types and actual_type not in case.expected_types:
        failures.append(f"type={actual_type or '-'}")
    if case.expected_stage:
        allowed_stages = tuple(item for item in case.expected_stage.split("|") if item)
        if not any(actual_stage.startswith(item) for item in allowed_stages):
            failures.append(f"stage={actual_stage or '-'}")
    for tool in case.expected_tools:
        if not _has_tool(tool_keys, tool):
            failures.append(f"missing_tool={tool}")
    if case.case_id == 13:
        store_tools = [tool for tool in plan.get("required_tools") or [] if tool.get("name") == "store_lookup"]
        if not any(str(tool.get("distance_origin") or "").strip() for tool in store_tools):
            failures.append("missing_distance_origin")
    return not failures, "；".join(failures) if failures else "通过"


async def _run_one(case: PlannerCase, semaphore: asyncio.Semaphore) -> dict[str, Any]:
    async with semaphore:
        start = time.perf_counter()
        client = ModelClient(get_settings())
        try:
            plan, model_call = await run_planner_brain_v2(_state_for_case(case), client)
            ok, judgement = _judge(case, plan)
            primary = plan.get("primary_task") or {}
            return {
                "case_id": case.case_id,
                "stage": case.stage,
                "scenario": case.scenario,
                "message": case.message,
                "expected_types": list(case.expected_types),
                "expected_stage": case.expected_stage,
                "expected_tools": list(case.expected_tools),
                "actual_type": primary.get("type", ""),
                "actual_subtype": primary.get("subtype", ""),
                "actual_stage": plan.get("sop_stage") or primary.get("sop_stage", ""),
                "actual_step": plan.get("sop_step") or primary.get("sop_step", ""),
                "policy_hint": primary.get("policy_hint", ""),
                "required_tools": plan.get("required_tools") or [],
                "tool_keys": _tool_keys(plan.get("required_tools") or []),
                "handoff": plan.get("handoff") or {},
                "reply_strategy": plan.get("reply_strategy") or {},
                "tool_policy_violations": plan.get("tool_policy_violations") or [],
                "model_call": model_call,
                "elapsed_ms": round((time.perf_counter() - start) * 1000),
                "pass": ok,
                "judgement": judgement,
                "notes": case.notes,
            }
        except Exception as exc:
            return {
                "case_id": case.case_id,
                "stage": case.stage,
                "scenario": case.scenario,
                "message": case.message,
                "expected_types": list(case.expected_types),
                "expected_stage": case.expected_stage,
                "expected_tools": list(case.expected_tools),
                "error": f"{type(exc).__name__}: {exc}",
                "elapsed_ms": round((time.perf_counter() - start) * 1000),
                "pass": False,
                "judgement": "Planner 调用失败",
                "notes": case.notes,
            }
        finally:
            await client.aclose()


def _markdown_report(results: list[dict[str, Any]], *, started_at: str, elapsed: float, concurrency: int) -> str:
    passed = sum(1 for item in results if item.get("pass"))
    lines = [
        "# Planner 20 条并发评测报告",
        "",
        f"- 测试时间：{started_at}",
        f"- 模型：{', '.join(PLANNER_MODEL_NAMES)}",
        f"- 并发：{concurrency}",
        f"- 总耗时：{elapsed:.1f}s",
        f"- 通过：{passed}/{len(results)}",
        "",
        "| # | 客户阶段 | 场景 | 用户问题 | 实际主任务 | SOP | 必需工具 | 耗时 | 评判 |",
        "|---:|---|---|---|---|---|---|---:|---|",
    ]
    for item in sorted(results, key=lambda row: row["case_id"]):
        tools = ", ".join(item.get("tool_keys") or [])
        actual = item.get("actual_type") or item.get("error") or "-"
        sop = item.get("actual_stage") or "-"
        lines.append(
            "| {case_id} | {stage} | {scenario} | {message} | {actual} | {sop} | {tools} | {elapsed_ms}ms | {judgement} |".format(
                case_id=item["case_id"],
                stage=_md(item.get("stage", "")),
                scenario=_md(item.get("scenario", "")),
                message=_md(item.get("message", "")),
                actual=_md(actual),
                sop=_md(sop),
                tools=_md(tools),
                elapsed_ms=item.get("elapsed_ms", 0),
                judgement=_md(item.get("judgement", "")),
            )
        )
    return "\n".join(lines) + "\n"


def _md(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


async def main() -> int:
    concurrency = 4
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    start = time.perf_counter()
    semaphore = asyncio.Semaphore(concurrency)
    results = await asyncio.gather(*(_run_one(case, semaphore) for case in CASES))
    elapsed = time.perf_counter() - start
    ordered = sorted(results, key=lambda row: row["case_id"])

    report_dir = ROOT / "logs" / "planner_tests"
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = report_dir / f"planner_20_concurrency_{stamp}.json"
    md_path = report_dir / f"planner_20_concurrency_{stamp}.md"
    payload = {
        "started_at": started_at,
        "elapsed_seconds": round(elapsed, 3),
        "concurrency": concurrency,
        "models": PLANNER_MODEL_NAMES,
        "passed": sum(1 for item in ordered if item.get("pass")),
        "total": len(ordered),
        "results": ordered,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_markdown_report(ordered, started_at=started_at, elapsed=elapsed, concurrency=concurrency), encoding="utf-8")

    print(f"report_json={json_path}")
    print(f"report_md={md_path}")
    print(f"passed={payload['passed']}/{payload['total']} elapsed={elapsed:.1f}s")
    for item in ordered:
        status = "PASS" if item.get("pass") else "FAIL"
        actual = item.get("actual_type") or item.get("error") or "-"
        print(
            f"{item['case_id']:02d} {status} {item['scenario']} | type={actual} | "
            f"stage={item.get('actual_stage','-')} | tools={','.join(item.get('tool_keys') or [])} | {item.get('judgement')}"
        )
    return 0 if payload["passed"] == payload["total"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
