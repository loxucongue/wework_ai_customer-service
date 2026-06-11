from __future__ import annotations

import asyncio
import csv
import json
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ai_paths"))

from app.graph.state import AgentState
from app.main import compiled_graph


DEFAULT_INPUT = Path(
    r"C:\Users\24159\.codex\attachments\e61f6cd4-6d8f-4628-9021-04fc085d9603\pasted-text.txt"
)
INPUT_PATH = Path(os.getenv("AI_PATHS_273_INPUT", str(DEFAULT_INPUT)))
MAX_CONCURRENCY = int(os.getenv("AI_PATHS_273_WORKERS", "20"))
PER_CASE_TIMEOUT_SECONDS = int(os.getenv("AI_PATHS_273_TIMEOUT", "120"))

BASE_REQUEST = {
    "corp_id": "ent-753d018266f7453285311ce1d5ed0d94",
    "user_id": 7294,
    "wechat": "DY1032",
}

IMAGE_PLACEHOLDERS: dict[str, str] = {
    "（发送脸部斑点照片）": "我发一张脸部斑点照片，你帮我看看适合什么方向",
    "（发送痣/痦子照片）": "我发一张痣的位置照片，你帮我看看能不能处理",
    "（发送其他家机构报价截图）": "我发你一张别家报价截图，你帮我看看有什么区别",
    "（发送其他家祛斑报价截图）": "我发你一张别家祛斑报价截图，你帮我对比一下",
    "（发送竞品截图）": "我发你一张别家活动截图，你帮我看看",
    "（发送体检报告或病历）": "我发了体检报告，想问问这种情况能不能做",
    "（发送表情包，如大拇指/玫瑰花）": "👍",
    "（连续回答城市+困扰+年龄+预算+项目偏好完整度达80%）": "我在上海，脸上老年斑比较多，今年58岁，预算别太高，想先了解淡斑方向。",
}


def read_cases() -> list[dict[str, Any]]:
    text = INPUT_PATH.read_text(encoding="utf-8-sig")
    reader = csv.DictReader(text.splitlines(), delimiter="\t")
    rows: list[dict[str, Any]] = []
    current_stage = ""
    current_scene = ""
    for index, record in enumerate(reader, start=1):
        stage = (record.get("客户阶段") or "").strip()
        scene = (record.get("场景类型") or "").strip()
        question = (record.get("用户问题") or "").strip()
        if stage:
            current_stage = stage
        if scene:
            current_scene = scene
        if not question:
            continue
        rows.append(
            {
                "index": index,
                "customer_stage": current_stage or "未标注",
                "scene_type": current_scene or "未标注",
                "question": question,
                "sent_question": IMAGE_PLACEHOLDERS.get(question, question),
            }
        )
    return rows


def build_state(case: dict[str, Any]) -> AgentState:
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


def extract_replies(final_state: dict[str, Any]) -> tuple[list[str], str]:
    text_replies: list[str] = []
    handoff_reason = ""
    for item in final_state.get("reply_messages") or []:
        msg_type = item.get("type")
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
    return text_replies, handoff_reason


def judge_result(error: str, text_replies: list[str]) -> str:
    if error:
        return f"不通过：{error}"
    if not text_replies:
        return "不通过：无客户可见回复"
    joined = " ".join(text_replies)
    internal_terms = ("intent", "subflow", "reply_brief", "module_outputs", "知识库", "工具返回", "路由")
    if any(term in joined for term in internal_terms):
        return "不通过：疑似泄露内部信息"
    if len(text_replies) > 2:
        return "可优化：回复条数偏多"
    if any(len(text) > 180 for text in text_replies):
        return "可优化：单条回复偏长"
    vague_terms = ("需要进一步确认", "具体要看", "无法判断", "不确定")
    if any(term in joined for term in vague_terms) and len(joined) < 45:
        return "可优化：回复偏泛"
    return "通过"


def md_escape(value: Any) -> str:
    text = str(value or "")
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\r", "").replace("\n", "<br>")


def write_outputs(results: list[dict[str, Any]]) -> tuple[Path, Path]:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = ROOT / "logs" / f"ai_customer_reply_273_inprocess_results_{timestamp}.jsonl"
    md_path = ROOT / "docs" / f"ai_customer_reply_273_inprocess_report_{timestamp}.md"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)

    with json_path.open("w", encoding="utf-8", newline="\n") as handle:
        for item in results:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")

    lines = [
        "# AI 客服 273 条进程内全量回复报告",
        "",
        "- 运行方式：`compiled_graph.ainvoke(...)`",
        f"- 输入：`{INPUT_PATH}`",
        f"- 并发：{MAX_CONCURRENCY}",
        f"- 单条超时：{PER_CASE_TIMEOUT_SECONDS}s",
        f"- 总数：{len(results)}",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "| 客户阶段 | 场景类型 | 用户问题 | AI实际回复（第1条） | AI引导回复（第2条） | 日志id | 评判 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in results:
        lines.append(
            "| "
            + " | ".join(
                [
                    md_escape(item.get("customer_stage", "")),
                    md_escape(item.get("scene_type", "")),
                    md_escape(item.get("question", "")),
                    md_escape(item.get("reply_1", "")),
                    md_escape(item.get("reply_2", "")),
                    md_escape(item.get("log_id", "")),
                    md_escape(item.get("judgement", "")),
                ]
            )
            + " |"
        )
    md_path.write_text("\n".join(lines), encoding="utf-8-sig", newline="\n")
    return json_path, md_path


async def run_case(case: dict[str, Any], semaphore: asyncio.Semaphore) -> dict[str, Any]:
    async with semaphore:
        started = time.perf_counter()
        state = build_state(case)
        error = ""
        final_state: dict[str, Any] = {}
        try:
            final_state = await asyncio.wait_for(compiled_graph.ainvoke(state), timeout=PER_CASE_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            error = "TimeoutError"
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        text_replies, handoff_reason = extract_replies(final_state)
        return {
            **case,
            "customer_id": state["customer_id"],
            "status_code": 200 if not error else 0,
            "elapsed_ms": elapsed_ms,
            "error": error,
            "reply_1": text_replies[0] if text_replies else "",
            "reply_2": text_replies[1] if len(text_replies) > 1 else "",
            "all_text_replies": text_replies,
            "handoff_reason": handoff_reason,
            "log_id": final_state.get("request_id") or state["request_id"],
            "judgement": judge_result(error, text_replies),
            "response": final_state,
        }


async def main_async() -> None:
    started = time.perf_counter()
    cases = read_cases()
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    print(
        f"start cases={len(cases)} mode=inprocess workers={MAX_CONCURRENCY} timeout={PER_CASE_TIMEOUT_SECONDS}s",
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
                    "judgement": f"不通过：{type(exc).__name__}",
                    "error": str(exc),
                }
            )
        if completed % 20 == 0 or completed == len(cases):
            print(f"completed {completed}/{len(cases)}", flush=True)
    results.sort(key=lambda item: int(item.get("index", 0)))
    json_path, md_path = write_outputs(results)
    elapsed = time.perf_counter() - started
    ok = sum(1 for item in results if item.get("judgement") == "通过")
    warn = sum(1 for item in results if str(item.get("judgement", "")).startswith("可优化"))
    bad = len(results) - ok - warn
    print(f"done elapsed={elapsed:.1f}s pass={ok} optimize={warn} fail={bad}")
    print(f"json={json_path}")
    print(f"report={md_path}")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
