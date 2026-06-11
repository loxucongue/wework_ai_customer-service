from __future__ import annotations

import csv
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
API_URL = os.getenv("AI_PATHS_API_URL", "http://47.252.81.104/api/ai/chat")
INPUT_PATH = Path(
    r"C:\Users\24159\.codex\attachments\e61f6cd4-6d8f-4628-9021-04fc085d9603\pasted-text.txt"
)
REPORT_JSON = ROOT / "logs" / "stage_scene_regression_report.json"
REPORT_MD = ROOT / "logs" / "stage_scene_regression_report.md"

BASE_PAYLOAD = {
    "corp_id": "ent-753d018266f7453285311ce1d5ed0d94",
    "user_id": "DY1032",
    "wechat": "DY1032",
}

PLACEHOLDER_IMAGE_HINTS = {
    "（发送脸部斑点照片）": "客户发送了脸部斑点照片，想看适合什么方向。",
    "（发送痣/痦子照片）": "客户发送了痣/痦子照片，想确认能不能做。",
    "（发送其他家机构报价截图）": "客户发送了其他家机构报价截图，想做价格对比。",
    "（发送其他家祛斑报价截图）": "客户发送了其他家祛斑报价截图，想对比价格和方案。",
    "（发送竞品截图）": "客户发送了竞品截图，想对比活动或价格。",
    "（发送体检报告或病历）": "客户发送了体检报告或病历，想确认能不能做。",
    "（发送表情包，如大拇指/玫瑰花）": "👍",
    "（陆续回答城市+困扰+年龄+预算+项目偏好完整度达80%）": "我在上海，脸上老年斑比较多，今年58岁，预算别太高，想先了解淡斑方向。",
}


@dataclass
class CaseRow:
    index: int
    stage: str
    scene: str
    question: str


@dataclass
class CaseGroup:
    stage: str
    scene: str
    rows: list[CaseRow] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.stage} / {self.scene}"


def normalize_question(text: str) -> str:
    text = text.strip()
    return PLACEHOLDER_IMAGE_HINTS.get(text, text)


def read_rows() -> list[CaseRow]:
    text = INPUT_PATH.read_text(encoding="utf-8")
    reader = csv.DictReader(text.splitlines(), delimiter="\t")
    rows: list[CaseRow] = []
    current_stage = ""
    current_scene = ""
    for idx, record in enumerate(reader, start=1):
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
            CaseRow(
                index=idx,
                stage=current_stage or "未标注阶段",
                scene=current_scene or "未标注场景",
                question=question,
            )
        )
    return rows


def group_rows(rows: list[CaseRow]) -> list[CaseGroup]:
    groups: list[CaseGroup] = []
    current_key: tuple[str, str] | None = None
    current_group: CaseGroup | None = None
    for row in rows:
        key = (row.stage, row.scene)
        if key != current_key:
            current_group = CaseGroup(stage=row.stage, scene=row.scene)
            groups.append(current_group)
            current_key = key
        current_group.rows.append(row)
    return groups


def build_payload(customer_id: str, text: str, history: list[str]) -> dict[str, Any]:
    return {
        **BASE_PAYLOAD,
        "content": text,
        "customer_id": customer_id,
        "external_userid": customer_id,
        "conversation_history": history[-10:],
    }


def post_chat(payload: dict[str, Any]) -> tuple[dict[str, Any], str | None, int]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=220) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
            error = None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except Exception:
            data = {"raw_error": raw}
        error = f"HTTP {exc.code}"
    except Exception as exc:
        data = {"raw_error": str(exc)}
        error = type(exc).__name__
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return data, error, elapsed_ms


def extract_texts(response: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    for item in response.get("reply_messages", []):
        if item.get("type") != "text":
            continue
        content = item.get("content")
        if isinstance(content, dict):
            text = (content.get("text") or "").strip()
        else:
            text = str(content or "").strip()
        if text:
            texts.append(text)
    return texts


def build_history_entry(role: str, text: str) -> str:
    return f"{role}：{text}"


def run_group(group: CaseGroup) -> list[dict[str, Any]]:
    customer_id = f"stage-scene-{uuid.uuid4().hex[:10]}"
    history: list[str] = []
    results: list[dict[str, Any]] = []
    for row in group.rows:
        question = normalize_question(row.question)
        payload = build_payload(customer_id, question, history)
        response, error, elapsed_ms = post_chat(payload)
        texts = extract_texts(response)
        first_text = texts[0] if texts else ""
        second_text = texts[1] if len(texts) > 1 else ""
        results.append(
            {
                "index": row.index,
                "customer_stage": row.stage,
                "scene_type": row.scene,
                "user_question": row.question,
                "sent_question": question,
                "reply_1": first_text,
                "reply_2": second_text,
                "all_replies": texts,
                "elapsed_ms": elapsed_ms,
                "error": error,
                "request_id": response.get("request_id") or "",
                "scene": response.get("scene") or "",
                "intent": response.get("intent") or "",
                "subflow": response.get("subflow") or "",
            }
        )
        history.append(build_history_entry("用户", question))
        if texts:
            history.append(build_history_entry("小贝", " ".join(texts[:2])))
    return results


def write_markdown(results: list[dict[str, Any]]) -> None:
    lines = [
        "# 阶段场景回归测试报告",
        "",
        f"- 接口：`{API_URL}`",
        f"- 输入文件：`{INPUT_PATH}`",
        f"- 题目数量：{len(results)}",
        "",
        "| 客户阶段 | 场景类型 | 用户问题 | AI实际回复（第1条） | AI引导回复（第2条） |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in results:
        def esc(text: str) -> str:
            return (text or "").replace("\n", "<br>").replace("|", "\\|")

        lines.append(
            f"| {esc(item['customer_stage'])} | {esc(item['scene_type'])} | "
            f"{esc(item['user_question'])} | {esc(item['reply_1'])} | {esc(item['reply_2'])} |"
        )
    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    rows = read_rows()
    groups = group_rows(rows)
    all_results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        future_map = {executor.submit(run_group, group): group.key for group in groups}
        for future in as_completed(future_map):
            all_results.extend(future.result())
    all_results.sort(key=lambda item: item["index"])
    payload = {
        "api_url": API_URL,
        "input_path": str(INPUT_PATH),
        "case_count": len(all_results),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "results": all_results,
    }
    REPORT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(all_results)
    success = sum(1 for item in all_results if not item["error"])
    print(f"completed={len(all_results)} success={success} failed={len(all_results)-success}")
    print(REPORT_JSON)
    print(REPORT_MD)


if __name__ == "__main__":
    main()
