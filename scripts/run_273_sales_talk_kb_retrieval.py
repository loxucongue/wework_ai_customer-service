from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ai_paths"))

from app.config import get_settings  # noqa: E402
from app.policies.business_scene_table import infer_policy_family  # noqa: E402
from app.services.coze_client import CozeClient  # noqa: E402


DEFAULT_INPUT = Path(
    r"C:\Users\24159\.codex\attachments\62d7c2f7-71af-4d48-a7f6-e28749543112\pasted-text.txt"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Query sales_talk_qa with original 273 business questions.")
    parser.add_argument("--input", default=os.getenv("AI_PATHS_273_INPUT", str(DEFAULT_INPUT)), help="273 TSV input path.")
    parser.add_argument("--limit", type=int, default=int(os.getenv("AI_PATHS_SALES_TALK_KB_LIMIT", "0")))
    parser.add_argument("--workers", type=int, default=int(os.getenv("AI_PATHS_SALES_TALK_KB_WORKERS", "8")))
    parser.add_argument("--timeout", type=int, default=int(os.getenv("AI_PATHS_SALES_TALK_KB_TIMEOUT", "80")))
    parser.add_argument("--kb-name", default=os.getenv("AI_PATHS_SALES_TALK_KB_NAME", "sales_talk_qa"))
    return parser.parse_args()


def read_cases(path: Path, *, limit: int = 0) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8-sig")
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
                "business_logic": logic,
                "expected_policy_family_id": infer_policy_family(
                    stage=current_stage or "未标注",
                    scene_type=current_scene or "未标注",
                    question=question,
                    business_logic=logic,
                ),
            }
        )
        if limit > 0 and len(cases) >= limit:
            break
    return cases


async def run_case(
    case: dict[str, Any],
    *,
    client: CozeClient,
    semaphore: asyncio.Semaphore,
    kb_name: str,
    timeout: int,
) -> dict[str, Any]:
    async with semaphore:
        started = time.perf_counter()
        error = ""
        items: list[dict[str, str]] = []
        try:
            result = await asyncio.wait_for(client.search_kb(kb_name, str(case["question"])), timeout=timeout)
            items = [
                {
                    "document_id": item.document_id,
                    "content": item.content,
                }
                for item in result.items
            ]
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        first = items[0] if items else {}
        return {
            **case,
            "kb_name": kb_name,
            "query": case["question"],
            "hit": bool(items),
            "item_count": len(items),
            "document_ids": [item.get("document_id", "") for item in items if item.get("document_id")],
            "first_output_preview": str(first.get("content") or "")[:240],
            "elapsed_ms": elapsed_ms,
            "error": error,
        }


async def main_async() -> None:
    args = parse_args()
    cases = read_cases(Path(args.input), limit=args.limit)
    client = CozeClient(get_settings())
    semaphore = asyncio.Semaphore(max(1, args.workers))
    started = time.perf_counter()
    try:
        tasks = [
            asyncio.create_task(
                run_case(
                    case,
                    client=client,
                    semaphore=semaphore,
                    kb_name=args.kb_name,
                    timeout=args.timeout,
                )
            )
            for case in cases
        ]
        results: list[dict[str, Any]] = []
        for completed, task in enumerate(asyncio.as_completed(tasks), start=1):
            results.append(await task)
            if completed % 20 == 0 or completed == len(tasks):
                print(f"completed {completed}/{len(tasks)}", flush=True)
        results.sort(key=lambda item: int(item.get("index") or 0))
        json_path, md_path = write_outputs(results, input_path=Path(args.input), kb_name=args.kb_name, workers=args.workers)
        print(f"json={json_path}", flush=True)
        print(f"report={md_path}", flush=True)
        print(f"elapsed={time.perf_counter() - started:.1f}s", flush=True)
    finally:
        await client.aclose()


def md_escape(value: Any) -> str:
    text = str(value or "")
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\r", "").replace("\n", "<br>")


def write_outputs(
    results: list[dict[str, Any]],
    *,
    input_path: Path,
    kb_name: str,
    workers: int,
) -> tuple[Path, Path]:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = ROOT / "logs" / f"{kb_name}_273_retrieval_results_{timestamp}.jsonl"
    md_path = ROOT / "docs" / f"{kb_name}_273_retrieval_report_{timestamp}.md"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)

    with json_path.open("w", encoding="utf-8", newline="\n") as handle:
        for item in results:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")

    hit_count = sum(1 for item in results if item.get("hit"))
    error_count = sum(1 for item in results if item.get("error"))
    lines = [
        f"# {kb_name} 273 原问题检索报告",
        "",
        f"- 输入：`{input_path}`",
        f"- 知识库：`{kb_name}`",
        f"- 并发：`{workers}`",
        f"- 总数：`{len(results)}`",
        f"- 命中：`{hit_count}`",
        f"- 未命中：`{len(results) - hit_count - error_count}`",
        f"- 错误：`{error_count}`",
        f"- 生成时间：`{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`",
        "",
        "| 序号 | 客户阶段 | 场景类型 | 用户问题 | expected_policy_family_id | hit | item_count | document_ids | 首条内容预览 | 耗时ms | error |",
        "| ---: | --- | --- | --- | --- | --- | ---: | --- | --- | ---: | --- |",
    ]
    for item in results:
        lines.append(
            "| "
            + " | ".join(
                [
                    md_escape(item.get("index")),
                    md_escape(item.get("customer_stage")),
                    md_escape(item.get("scene_type")),
                    md_escape(item.get("question")),
                    md_escape(item.get("expected_policy_family_id")),
                    md_escape("是" if item.get("hit") else "否"),
                    md_escape(item.get("item_count")),
                    md_escape(",".join(item.get("document_ids") or [])),
                    md_escape(item.get("first_output_preview")),
                    md_escape(item.get("elapsed_ms")),
                    md_escape(item.get("error")),
                ]
            )
            + " |"
        )
    md_path.write_text("\n".join(lines), encoding="utf-8-sig", newline="\n")
    return json_path, md_path


if __name__ == "__main__":
    asyncio.run(main_async())
