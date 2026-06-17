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
    "user_id": 7294,
    "wechat": "CS001",
}


SCENARIOS: dict[str, list[str]] = {
    "store_nearby": [
        "你好",
        "我想看看脸上的斑",
        "我在深圳南山科技园附近",
        "哪家离我最近",
        "地址发我",
        "客户做完之后的效果我想看一下",
        "这个大概多少钱",
        "会不会到店乱收费",
        "那我周天上午过去可以吗",
        "我叫李婷，电话13800138000，先帮我登记10元预约金",
    ],
    "airport_store": [
        "在吗",
        "我是想了解淡斑",
        "我在厦门机场附近",
        "直接给我近一点的门店吧",
        "发一下门店位置",
        "你们用的是什么方法",
        "看一下效果对比",
        "活动价是多少",
        "今天没空，周六下午可以吗",
        "可以，姓名王敏，电话13900139000",
    ],
    "price_trust": [
        "你们现在有什么活动",
        "确定是268吗",
        "是一次的费用吗",
        "到店会不会又让我加钱",
        "你们有资质吗",
        "会伤皮肤吗",
        "我在上海徐汇附近",
        "给我推荐近一点的门店",
        "那我明天下午去看看",
        "我先交10元可以，电话13700137000，名字赵一",
    ],
    "case_objection": [
        "我这个斑做了会有效果吗",
        "有客户做完的效果吗",
        "图片上的客户做了几次",
        "我之前做过两次没看到效果",
        "那你们和别人家有什么不一样",
        "我在西安高新附近",
        "最近是哪家",
        "发位置给我",
        "我考虑一下",
        "名额能先保留吗",
    ],
    "complaint_refund": [
        "把10元退给我，不然我投诉",
        "之前说好的价格到店不一样",
        "我不想去了",
        "你们是不是骗人",
        "我要找负责人",
        "现在怎么处理",
        "多久能退",
        "我之前在哪家店你能查吗",
        "我就是不满意",
        "让专业的人联系我",
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
            history.append(f"用户：{content}")
            for message in row.get("reply_messages") or []:
                text = _message_to_history_text(message)
                if text:
                    history.append(f"销售：{text}")
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
        row["request_id"] = response.get("request_id")
        row["reply_messages"] = response.get("reply_messages") or []
        row["intent"] = response.get("intent")
        row["subflow"] = response.get("subflow")
        meta = response.get("meta") if isinstance(response.get("meta"), dict) else {}
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
    tool_calls = 0
    book_orders = 0
    store_cards = 0
    images = 0
    for turn in turns:
        calls = turn.get("executed_tool_calls") or []
        tool_calls += len(calls)
        if "store_lookup" in calls:
            store_turns += 1
            if "platform" in (turn.get("store_data_authority") or []):
                store_platform_turns += 1
        audit = turn.get("audit") if isinstance(turn.get("audit"), dict) else {}
        audit_issues.extend(str(issue) for issue in audit.get("issues") or [])
        for message in turn.get("reply_messages") or []:
            if not isinstance(message, dict):
                continue
            if message.get("type") == "book_order":
                book_orders += 1
            elif message.get("type") == "store_address":
                store_cards += 1
            elif message.get("type") == "image":
                images += 1
    return {
        "turn_count": len(turns),
        "error_count": sum(1 for turn in turns if turn.get("error")),
        "tool_call_count": tool_calls,
        "store_lookup_turns": store_turns,
        "store_platform_turns": store_platform_turns,
        "store_platform_ratio": round(store_platform_turns / store_turns, 4) if store_turns else None,
        "book_order_messages": book_orders,
        "store_address_messages": store_cards,
        "image_messages": images,
        "audit_issue_count": len(audit_issues),
        "audit_issues": sorted(set(audit_issues)),
    }


if __name__ == "__main__":
    raise SystemExit(main())
