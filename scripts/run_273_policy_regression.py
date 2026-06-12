from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import sys
import time
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ai_paths"))

from app.main import compiled_graph  # noqa: E402


DEFAULT_INPUT = Path(
    r"C:\Users\24159\.codex\attachments\5130a6ad-3693-4f49-9ab5-300d0bc841b4\pasted-text.txt"
)
INPUT_PATH = Path(os.getenv("AI_PATHS_273_INPUT", str(DEFAULT_INPUT)))
MAX_CONCURRENCY = int(os.getenv("AI_PATHS_273_WORKERS", "12"))
PER_CASE_TIMEOUT_SECONDS = int(os.getenv("AI_PATHS_273_TIMEOUT", "180"))
LIMIT = int(os.getenv("AI_PATHS_273_LIMIT", "0"))

BASE_REQUEST = {
    "corp_id": "ent-753d018266f7453285311ce1d5ed0d94",
    "user_id": 7294,
    "wechat": "DY1032",
}

IMAGE_PLACEHOLDERS: dict[str, str] = {
    "（发送脸部斑点照片）": "我发一张脸部斑点照片，你帮我看看适合什么方向",
    "（发送其他家机构报价截图）": "我发你一张别家机构报价截图，你帮我看看有什么区别",
    "（发送其他家祛斑报价截图）": "我发你一张别家祛斑报价截图，你帮我对比一下",
    "（发送竞品截图）": "我发你一张别家活动截图，你帮我看看",
    "（发送体检报告或病历）": "我发了体检报告，想问问这种情况能不能做",
    "（发送表情包，如大拇指/玫瑰花）": "挺好的",
    "（连续回答城市+困扰+年龄+预算+项目偏好完整度达80%）": (
        "我在上海，脸上老年斑比较多，今年58岁，预算别太高，想先了解淡斑方向"
    ),
}

FORBIDDEN_VISIBLE_TERMS = (
    "intent",
    "subflow",
    "reply_brief",
    "module_outputs",
    "工具",
    "路由",
    "知识库",
)

FORBIDDEN_BUSINESS_TERMS = (
    "根治",
    "100%见效",
    "百分百见效",
    "绝对安全",
    "保证效果",
    "包效果",
    "一次一定好",
    "包接送",
    "车费报销",
)


def read_cases() -> list[dict[str, Any]]:
    text = INPUT_PATH.read_text(encoding="utf-8-sig")
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    if not reader.fieldnames or len(reader.fieldnames) < 3:
        raise ValueError(f"Input fieldnames invalid: {reader.fieldnames!r}")

    stage_key, scene_key, question_key = reader.fieldnames[:3]
    logic_key = reader.fieldnames[3] if len(reader.fieldnames) >= 4 else None

    cases: list[dict[str, Any]] = []
    current_stage = ""
    current_scene = ""
    for index, record in enumerate(reader, start=1):
        stage = str(record.get(stage_key) or "").strip()
        scene = str(record.get(scene_key) or "").strip()
        question = str(record.get(question_key) or "").strip()
        logic = str(record.get(logic_key) or "").strip() if logic_key else ""
        if stage:
            current_stage = stage
        if scene:
            current_scene = scene
        if not question:
            continue
        cases.append(
            {
                "index": index,
                "customer_stage": current_stage or "未标注",
                "scene_type": current_scene or "未标注",
                "question": question,
                "sent_question": IMAGE_PLACEHOLDERS.get(question, question),
                "business_logic": logic,
            }
        )
    if LIMIT > 0:
        cases = cases[:LIMIT]
    return cases


def build_state(case: dict[str, Any]) -> dict[str, Any]:
    customer_id = f"reply273_{case['index']}_{uuid.uuid4().hex[:8]}"
    request_id = str(uuid.uuid4())
    return {
        "request_id": request_id,
        "customer_id": customer_id,
        "corp_id": BASE_REQUEST["corp_id"],
        "content": case["sent_question"],
        "conversation_history": [],
        "file_image": None,
        "user_id": BASE_REQUEST["user_id"],
        "wechat": BASE_REQUEST["wechat"],
        "external_userid": customer_id,
        "customer_add_wechat_id": None,
        "confirmed_store_id": None,
        "confirmed_store_name": None,
        "store_id": None,
        "store_name": None,
        "appointment_id": None,
        "appointment_time": None,
        "request_context": {
            "user_id": BASE_REQUEST["user_id"],
            "corp_id": BASE_REQUEST["corp_id"],
            "wechat": BASE_REQUEST["wechat"],
            "external_userid": customer_id,
            "customer_id": customer_id,
        },
        "trace": [],
        "errors": [],
    }


def extract_text_replies(final_state: dict[str, Any]) -> tuple[list[str], list[str], str]:
    text_replies: list[str] = []
    reply_types: list[str] = []
    handoff_reason = ""
    for item in final_state.get("reply_messages") or []:
        if not isinstance(item, dict):
            continue
        msg_type = str(item.get("type") or "")
        reply_types.append(msg_type)
        content = item.get("content")
        if msg_type == "text":
            if isinstance(content, dict):
                text = str(content.get("text") or "").strip()
            else:
                text = str(content or "").strip()
            if text:
                text_replies.append(text)
        elif msg_type == "human_handoff":
            if isinstance(content, dict):
                handoff_reason = str(content.get("handoff_reason") or "").strip()
            else:
                handoff_reason = str(content or "").strip()
    return text_replies, reply_types, handoff_reason


def judge_result(error: str, text_replies: list[str], reply_types: list[str]) -> str:
    if error:
        return f"不通过：{error}"
    if not text_replies:
        return "不通过：无客户可见回复"
    joined = " ".join(text_replies)
    if any(term in joined for term in FORBIDDEN_VISIBLE_TERMS):
        return "不通过：疑似泄露内部信息"
    if any(term in joined for term in FORBIDDEN_BUSINESS_TERMS):
        return "不通过：包含禁止承诺/表达"
    if len(text_replies) > 2:
        return "可优化：回复条数偏多"
    if any(len(text) > 180 for text in text_replies):
        return "可优化：单条回复偏长"
    if "human_handoff" in reply_types and len(text_replies) > 2:
        return "可优化：专业协助前回复偏多"
    return "通过"


def state_meta(final_state: dict[str, Any]) -> dict[str, Any]:
    return {
        "policy_family_id": str(final_state.get("policy_family_id") or ""),
        "exact_policy_id": str(final_state.get("exact_policy_id") or final_state.get("policy_id") or ""),
        "policy_id": str(final_state.get("policy_id") or ""),
        "active_scene_id": str(final_state.get("active_scene_id") or ""),
        "active_scene_match_level": str(final_state.get("active_scene_match_level") or ""),
        "active_scene_score": final_state.get("active_scene_score", 0),
        "scene_guidance_injected": bool(final_state.get("scene_guidance_context")),
        "planner_source": str(final_state.get("planner_source") or ""),
        "tool_result_keys": sorted((final_state.get("tool_results") or {}).keys()),
        "primary_task": final_state.get("primary_task") or {},
    }


async def run_case(case: dict[str, Any], semaphore: asyncio.Semaphore) -> dict[str, Any]:
    async with semaphore:
        started = time.perf_counter()
        state = build_state(case)
        final_state: dict[str, Any] = {}
        error = ""
        try:
            final_state = await asyncio.wait_for(
                compiled_graph.ainvoke(state),
                timeout=PER_CASE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            error = "TimeoutError"
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        text_replies, reply_types, handoff_reason = extract_text_replies(final_state)
        meta = state_meta(final_state)
        return {
            **case,
            "customer_id": state["customer_id"],
            "elapsed_ms": elapsed_ms,
            "error": error,
            "state_errors": final_state.get("errors", []),
            "reply_source": str(final_state.get("reply_source") or ""),
            "reply_types": reply_types,
            "reply_1": text_replies[0] if text_replies else "",
            "reply_2": text_replies[1] if len(text_replies) > 1 else "",
            "all_text_replies": text_replies,
            "handoff_reason": handoff_reason,
            "log_id": final_state.get("request_id") or state["request_id"],
            "judgement": judge_result(error, text_replies, reply_types),
            **meta,
        }


def md_escape(value: Any) -> str:
    text = str(value or "")
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\r", "").replace("\n", "<br>")


def counter_table(title: str, counter: Counter[str], limit: int = 30) -> list[str]:
    lines = [f"## {title}", "", "| 值 | 数量 |", "| --- | ---: |"]
    for key, value in counter.most_common(limit):
        lines.append(f"| {md_escape(key or '<empty>')} | {value} |")
    if not counter:
        lines.append("| <none> | 0 |")
    lines.append("")
    return lines


def has_visible_text(item: dict[str, Any]) -> bool:
    return bool(str(item.get("reply_1") or "").strip())


def write_outputs(results: list[dict[str, Any]]) -> tuple[Path, Path]:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = ROOT / "logs" / f"ai_customer_reply_273_policy_results_{timestamp}.jsonl"
    md_path = ROOT / "docs" / f"ai_customer_reply_273_policy_report_{timestamp}.md"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)

    with json_path.open("w", encoding="utf-8", newline="\n") as handle:
        for item in results:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")

    family_counter = Counter(str(item.get("policy_family_id") or "") for item in results)
    policy_counter = Counter(str(item.get("exact_policy_id") or "") for item in results)
    scene_counter = Counter(str(item.get("active_scene_id") or "") for item in results)
    judgement_counter = Counter(str(item.get("judgement") or "") for item in results)
    reply_source_counter = Counter(str(item.get("reply_source") or "") for item in results)
    handoff_count = sum(1 for item in results if "human_handoff" in (item.get("reply_types") or []))
    no_visible_text = [item for item in results if not has_visible_text(item)]
    metadata_only_handoff = [
        item
        for item in results
        if item.get("reply_source") == "metadata_only_handoff" or ("human_handoff" in (item.get("reply_types") or []) and not has_visible_text(item))
    ]
    handoff_without_text = [
        item for item in results if "human_handoff" in (item.get("reply_types") or []) and not has_visible_text(item)
    ]
    missing_scene = [
        item
        for item in results
        if item.get("policy_family_id")
        and not str(item.get("policy_family_id", "")).startswith("HUMAN_HANDOFF")
        and not item.get("active_scene_id")
    ]

    lines = [
        "# AI 客服 273 条策略回归报告",
        "",
        f"- 运行方式：`compiled_graph.ainvoke(...)`",
        f"- 输入：`{INPUT_PATH}`",
        f"- 并发：`{MAX_CONCURRENCY}`",
        f"- 单条超时：`{PER_CASE_TIMEOUT_SECONDS}s`",
        f"- 总数：`{len(results)}`",
        f"- human_handoff：`{handoff_count}`",
        f"- no_visible_text：`{len(no_visible_text)}`",
        f"- metadata_only_handoff：`{len(metadata_only_handoff)}`",
        f"- handoff_without_text：`{len(handoff_without_text)}`",
        f"- 缺少 active_scene_id（非 HUMAN）：`{len(missing_scene)}`",
        f"- 生成时间：`{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`",
        "",
    ]
    lines.extend(counter_table("评判聚合", judgement_counter))
    lines.extend(counter_table("reply_source 聚合", reply_source_counter))
    lines.extend(counter_table("policy_family_id 聚合", family_counter))
    lines.extend(counter_table("exact_policy_id 聚合", policy_counter))
    lines.extend(counter_table("active_scene_id 聚合", scene_counter))

    lines.extend(
        [
            "## 缺少 active_scene_id 样本（前 40 条）",
            "",
            "| 序号 | 客户阶段 | 场景类型 | 用户问题 | policy_family_id | exact_policy_id | 日志id |",
            "| ---: | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for item in missing_scene[:40]:
        lines.append(
            "| "
            + " | ".join(
                [
                    md_escape(item.get("index")),
                    md_escape(item.get("customer_stage")),
                    md_escape(item.get("scene_type")),
                    md_escape(item.get("question")),
                    md_escape(item.get("policy_family_id")),
                    md_escape(item.get("exact_policy_id")),
                    md_escape(item.get("log_id")),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## 无客户可见 text 样本",
            "",
            "| 序号 | 客户阶段 | 场景类型 | 用户问题 | reply_source | reply_types | policy_family_id | exact_policy_id | 日志id |",
            "| ---: | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for item in no_visible_text:
        lines.append(
            "| "
            + " | ".join(
                [
                    md_escape(item.get("index")),
                    md_escape(item.get("customer_stage")),
                    md_escape(item.get("scene_type")),
                    md_escape(item.get("question")),
                    md_escape(item.get("reply_source")),
                    md_escape(",".join(item.get("reply_types") or [])),
                    md_escape(item.get("policy_family_id")),
                    md_escape(item.get("exact_policy_id")),
                    md_escape(item.get("log_id")),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## 全量明细",
            "",
            "| 客户阶段 | 场景类型 | 用户问题 | AI实际回复（第1条） | AI引导回复（第2条） | 日志id | reply_source | policy_family_id | exact_policy_id | active_scene_id | 评判 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for item in results:
        lines.append(
            "| "
            + " | ".join(
                [
                    md_escape(item.get("customer_stage")),
                    md_escape(item.get("scene_type")),
                    md_escape(item.get("question")),
                    md_escape(item.get("reply_1")),
                    md_escape(item.get("reply_2")),
                    md_escape(item.get("log_id")),
                    md_escape(item.get("reply_source")),
                    md_escape(item.get("policy_family_id")),
                    md_escape(item.get("exact_policy_id")),
                    md_escape(item.get("active_scene_id")),
                    md_escape(item.get("judgement")),
                ]
            )
            + " |"
        )
    md_path.write_text("\n".join(lines), encoding="utf-8-sig", newline="\n")
    return json_path, md_path


async def main_async() -> None:
    started = time.perf_counter()
    cases = read_cases()
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    print(
        f"start cases={len(cases)} workers={MAX_CONCURRENCY} timeout={PER_CASE_TIMEOUT_SECONDS}s",
        flush=True,
    )
    tasks = [asyncio.create_task(run_case(case, semaphore)) for case in cases]
    results: list[dict[str, Any]] = []
    completed = 0
    for task in asyncio.as_completed(tasks):
        completed += 1
        try:
            results.append(await task)
        except Exception as exc:
            results.append(
                {
                    "index": -1,
                    "customer_stage": "未知",
                    "scene_type": "未知",
                    "question": "",
                    "reply_1": "",
                    "reply_2": "",
                    "log_id": "",
                    "policy_family_id": "",
                    "exact_policy_id": "",
                    "active_scene_id": "",
                    "judgement": f"不通过：{type(exc).__name__}",
                    "error": str(exc),
                }
            )
        if completed % 20 == 0 or completed == len(cases):
            print(f"completed {completed}/{len(cases)}", flush=True)
    results.sort(key=lambda item: int(item.get("index", 0)))
    json_path, md_path = write_outputs(results)
    elapsed = time.perf_counter() - started
    print(f"json={json_path}", flush=True)
    print(f"report={md_path}", flush=True)
    print(f"elapsed={elapsed:.1f}s", flush=True)


if __name__ == "__main__":
    asyncio.run(main_async())
