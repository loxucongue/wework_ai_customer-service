from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from audit_tool_fact_trace import audit_run_file


REAL_CUSTOMER = {
    "customer_id": "20615704",
    "external_userid": "wmanzqsqaaygjwicitvmos657x39lqtg",
    "corp_id": "ww943af61cd5d2afe4",
    "user_id": "7294",
    "wechat": "CS001",
}


SCENARIOS: dict[str, list[str]] = {
    "store_nearby": [
        "\u4f60\u597d",
        "\u6211\u60f3\u770b\u770b\u8138\u4e0a\u7684\u6591",
        "\u6211\u5728\u6df1\u5733\u5357\u5c71\u79d1\u6280\u56ed\u9644\u8fd1",
        "\u54ea\u5bb6\u79bb\u6211\u6700\u8fd1",
        "\u5730\u5740\u53d1\u6211",
        "\u5ba2\u6237\u505a\u5b8c\u4e4b\u540e\u7684\u6548\u679c\u6211\u60f3\u770b\u4e00\u4e0b",
        "\u8fd9\u4e2a\u5927\u6982\u591a\u5c11\u94b1",
        "\u4f1a\u4e0d\u4f1a\u5230\u5e97\u4e71\u6536\u8d39",
        "\u90a3\u6211\u5468\u5929\u4e0a\u5348\u8fc7\u53bb\u53ef\u4ee5\u5417",
        "\u6211\u53eb\u674e\u5a77\uff0c\u7535\u8bdd13800138000\uff0c\u5148\u5e2e\u6211\u767b\u8bb010\u5143\u9884\u7ea6\u91d1",
    ],
    "airport_store": [
        "\u5728\u5417",
        "\u6211\u662f\u60f3\u4e86\u89e3\u6de1\u6591",
        "\u6211\u5728\u53a6\u95e8\u673a\u573a\u9644\u8fd1",
        "\u76f4\u63a5\u7ed9\u6211\u8fd1\u4e00\u70b9\u7684\u95e8\u5e97\u5427",
        "\u53d1\u4e00\u4e0b\u95e8\u5e97\u4f4d\u7f6e",
        "\u4f60\u4eec\u7528\u7684\u662f\u4ec0\u4e48\u65b9\u6cd5",
        "\u770b\u4e00\u4e0b\u6548\u679c\u5bf9\u6bd4",
        "\u6d3b\u52a8\u4ef7\u662f\u591a\u5c11",
        "\u4eca\u5929\u6ca1\u7a7a\uff0c\u5468\u516d\u4e0b\u5348\u53ef\u4ee5\u5417",
        "\u53ef\u4ee5\uff0c\u59d3\u540d\u738b\u654f\uff0c\u7535\u8bdd13900139000",
    ],
    "price_trust": [
        "\u4f60\u4eec\u73b0\u5728\u6709\u4ec0\u4e48\u6d3b\u52a8",
        "\u786e\u5b9a\u662f268\u5417",
        "\u662f\u4e00\u6b21\u7684\u8d39\u7528\u5417",
        "\u5230\u5e97\u4f1a\u4e0d\u4f1a\u53c8\u8ba9\u6211\u52a0\u94b1",
        "\u4f60\u4eec\u6709\u8d44\u8d28\u5417",
        "\u4f1a\u4f24\u76ae\u80a4\u5417",
        "\u6211\u5728\u4e0a\u6d77\u5f90\u6c47\u9644\u8fd1",
        "\u7ed9\u6211\u63a8\u8350\u8fd1\u4e00\u70b9\u7684\u95e8\u5e97",
        "\u90a3\u6211\u660e\u5929\u4e0b\u5348\u53bb\u770b\u770b",
        "\u6211\u5148\u4ea410\u5143\u53ef\u4ee5\uff0c\u7535\u8bdd13700137000\uff0c\u540d\u5b57\u8d75\u4e00",
    ],
    "case_objection": [
        "\u6211\u8fd9\u4e2a\u6591\u505a\u4e86\u4f1a\u6709\u6548\u679c\u5417",
        "\u6709\u5ba2\u6237\u505a\u5b8c\u7684\u6548\u679c\u5417",
        "\u56fe\u7247\u4e0a\u7684\u5ba2\u6237\u505a\u4e86\u51e0\u6b21",
        "\u6211\u4e4b\u524d\u505a\u8fc7\u4e24\u6b21\u6ca1\u770b\u5230\u6548\u679c",
        "\u90a3\u4f60\u4eec\u548c\u522b\u4eba\u5bb6\u6709\u4ec0\u4e48\u4e0d\u4e00\u6837",
        "\u6211\u5728\u897f\u5b89\u9ad8\u65b0\u9644\u8fd1",
        "\u6700\u8fd1\u662f\u54ea\u5bb6",
        "\u53d1\u4f4d\u7f6e\u7ed9\u6211",
        "\u6211\u8003\u8651\u4e00\u4e0b",
        "\u540d\u989d\u80fd\u5148\u4fdd\u7559\u5417",
    ],
    "complaint_refund": [
        "\u628a10\u5143\u9000\u7ed9\u6211\uff0c\u4e0d\u7136\u6211\u6295\u8bc9",
        "\u4e4b\u524d\u8bf4\u597d\u7684\u4ef7\u683c\u5230\u5e97\u4e0d\u4e00\u6837",
        "\u6211\u4e0d\u60f3\u53bb\u4e86",
        "\u4f60\u4eec\u662f\u4e0d\u662f\u9a97\u4eba",
        "\u6211\u8981\u627e\u8d1f\u8d23\u4eba",
        "\u73b0\u5728\u600e\u4e48\u5904\u7406",
        "\u591a\u4e45\u80fd\u9000",
        "\u6211\u4e4b\u524d\u5728\u54ea\u5bb6\u5e97\u4f60\u80fd\u67e5\u5417",
        "\u6211\u5c31\u662f\u4e0d\u6ee1\u610f",
        "\u8ba9\u4e13\u4e1a\u7684\u4eba\u8054\u7cfb\u6211",
    ],
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run real-customer long-dialogue regression via public API.")
    parser.add_argument("--api-url", default="http://47.252.81.104/api/ai/reply")
    parser.add_argument("--logs-url", default="http://47.252.81.104/api/logs/runs")
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--scenario", action="append", choices=sorted(SCENARIOS), help="Scenario name. Repeatable.")
    parser.add_argument("--max-turns", type=int, default=0, help="Limit turns per scenario. 0 means all.")
    parser.add_argument("--timeout", type=int, default=260)
    parser.add_argument("--sleep", type=float, default=0.0)
    parser.add_argument(
        "--real-opening",
        action="store_true",
        help="Allow real appointment opening. Default is dry-run for safety.",
    )
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir or f"docs/real_id_long_dialogues_{stamp}")
    runs_dir = output_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    selected = args.scenario or list(SCENARIOS)
    report: dict[str, Any] = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "api_url": args.api_url,
        "customer": REAL_CUSTOMER,
        "appointment_opening_dry_run": not args.real_opening,
        "scenarios": [],
        "summary": {},
    }

    for scenario_name in selected:
        history: list[str] = []
        scenario_rows: list[dict[str, Any]] = []
        turns = SCENARIOS[scenario_name]
        if args.max_turns > 0:
            turns = turns[: args.max_turns]
        for turn_index, content in enumerate(turns, start=1):
            payload = {
                **REAL_CUSTOMER,
                "content": content,
                "conversation_history": history[-12:],
                "request_context": {
                    "conversation_id": f"real_id_{scenario_name}",
                    "appointment_opening_dry_run": not args.real_opening,
                },
            }
            row = _run_turn(args.api_url, args.logs_url, payload, scenario_name, turn_index, content, runs_dir, args.timeout)
            scenario_rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
            history.append(f"\u7528\u6237\uff1a{content}")
            for message in row.get("reply_messages") or []:
                text = _message_to_history_text(message)
                if text:
                    history.append(f"\u9500\u552e\uff1a{text}")
            if args.sleep:
                time.sleep(args.sleep)
        report["scenarios"].append({"name": scenario_name, "turns": scenario_rows})

    report["summary"] = _summarize(report)
    output_path = output_dir / "report.json"
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report_path": str(output_path), "summary": report["summary"]}, ensure_ascii=False, indent=2))
    return 1 if report["summary"].get("audit_issue_count", 0) else 0


def _run_turn(
    api_url: str,
    logs_url: str,
    payload: dict[str, Any],
    scenario_name: str,
    turn_index: int,
    content: str,
    runs_dir: Path,
    timeout: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    row: dict[str, Any] = {
        "scenario": scenario_name,
        "turn": turn_index,
        "user": content,
    }
    try:
        response = _post_json(api_url, payload, timeout=timeout)
        row["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        payload_data = response.get("data") if isinstance(response.get("data"), dict) else response
        row["http_code"] = response.get("code")
        row["api_msg"] = response.get("msg")
        row["request_id"] = payload_data.get("request_id") or payload_data.get("trace_id")
        row["reply_messages"] = payload_data.get("reply_messages") or []
        row["intent"] = payload_data.get("intent")
        row["subflow"] = payload_data.get("subflow")
        meta = payload_data.get("meta") if isinstance(payload_data.get("meta"), dict) else {}
        row["tool_calls_meta"] = meta.get("tool_calls") or []
        row["quality_flags"] = meta.get("quality_flags") or []
        run_log = _fetch_run_log(logs_url, str(row.get("request_id") or ""), timeout=timeout)
        if run_log:
            run_path = runs_dir / f"{row['request_id']}.json"
            run_path.write_text(json.dumps(run_log, ensure_ascii=False, indent=2), encoding="utf-8")
            row["run_log_path"] = str(run_path)
            audit = audit_run_file(run_path)
            row["audit"] = audit
            row.update(_tool_summary_from_run(run_log))
    except Exception as exc:  # noqa: BLE001
        row["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        row["error"] = f"{type(exc).__name__}: {exc}"
    return row


def _post_json(url: str, payload: dict[str, Any], *, timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def _fetch_run_log(logs_url: str, request_id: str, *, timeout: int) -> dict[str, Any]:
    if not request_id:
        return {}
    url = f"{logs_url}?{urllib.parse.urlencode({'request_id': request_id})}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except Exception:
        return {}


def _tool_summary_from_run(run_log: dict[str, Any]) -> dict[str, Any]:
    trace_source = run_log.get("raw_log") if isinstance(run_log.get("raw_log"), dict) else run_log
    trace = trace_source.get("trace") if isinstance(trace_source.get("trace"), list) else []
    execute = next((item for item in trace if isinstance(item, dict) and item.get("node") == "execute_actions"), {})
    output = execute.get("output_snapshot") if isinstance(execute.get("output_snapshot"), dict) else {}
    calls = output.get("executed_tool_calls") if isinstance(output.get("executed_tool_calls"), list) else []
    store_outputs = [call.get("output") for call in calls if isinstance(call, dict) and call.get("name") == "store_lookup"]
    return {
        "executed_tool_calls": [str(call.get("name") or "") for call in calls if isinstance(call, dict)],
        "store_data_authority": [
            str(item.get("data_authority") or item.get("store_data_authority") or "")
            for item in store_outputs
            if isinstance(item, dict)
        ],
        "platform_error": [
            item.get("platform_error")
            for item in store_outputs
            if isinstance(item, dict) and item.get("platform_error")
        ],
    }


def _message_to_history_text(message: dict[str, Any]) -> str:
    if not isinstance(message, dict):
        return ""
    msg_type = str(message.get("type") or "")
    content = message.get("content")
    if msg_type == "text" and isinstance(content, dict):
        return str(content.get("text") or "").strip()
    if msg_type == "image":
        return "[image]"
    if msg_type == "store_address" and isinstance(content, dict):
        return f"[store_address:{content.get('store_id')}]"
    if msg_type == "book_order" and isinstance(content, dict):
        return f"[book_order:{content.get('order_id')}]"
    if msg_type == "human_handoff" and isinstance(content, dict):
        return f"[human_handoff:{content.get('handoff_reason')}]"
    return ""


def _summarize(report: dict[str, Any]) -> dict[str, Any]:
    turns = [turn for scenario in report.get("scenarios", []) for turn in scenario.get("turns", [])]
    audit_issues: list[str] = []
    store_platform_turns = 0
    store_turns = 0
    tool_call_count = 0
    for turn in turns:
        if turn.get("audit"):
            audit_issues.extend(turn["audit"].get("issues") or [])
        calls = turn.get("executed_tool_calls") or []
        tool_call_count += len(calls)
        if "store_lookup" in calls:
            store_turns += 1
            authorities = turn.get("store_data_authority") or []
            if any(authority == "platform" for authority in authorities):
                store_platform_turns += 1
    messages = [message for turn in turns for message in (turn.get("reply_messages") or []) if isinstance(message, dict)]
    return {
        "turn_count": len(turns),
        "error_count": sum(1 for turn in turns if turn.get("error") or (turn.get("http_code") not in (None, 0, 200))),
        "tool_call_count": tool_call_count,
        "store_lookup_turns": store_turns,
        "store_platform_turns": store_platform_turns,
        "store_platform_ratio": (store_platform_turns / store_turns) if store_turns else None,
        "book_order_messages": sum(1 for message in messages if message.get("type") == "book_order"),
        "store_address_messages": sum(1 for message in messages if message.get("type") == "store_address"),
        "image_messages": sum(1 for message in messages if message.get("type") == "image"),
        "audit_issue_count": len(audit_issues),
        "audit_issues": audit_issues[:50],
    }


if __name__ == "__main__":
    raise SystemExit(main())
