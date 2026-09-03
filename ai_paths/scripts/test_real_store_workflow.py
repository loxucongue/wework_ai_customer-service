from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import warnings
from pathlib import Path
from typing import Any
from uuid import uuid4


AI_PATHS_ROOT = Path(__file__).resolve().parents[1]
if str(AI_PATHS_ROOT) not in sys.path:
    sys.path.insert(0, str(AI_PATHS_ROOT))
warnings.simplefilter("ignore")

from app.chat_request_context import build_request_context  # noqa: E402
from app.config import Settings  # noqa: E402
from app.runtime_services import build_reply_services  # noqa: E402
from app.schemas import ChatRequest  # noqa: E402


REQUIRED_IDENTITY_FIELDS = ("customer_id", "corp_id", "wechat", "external_userid", "user_id")


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the production V3 reply graph with a real customer identity in write-free isolation."
    )
    parser.add_argument("identity_source", type=Path, help="Existing request JSON or pasted request text.")
    parser.add_argument("query", nargs="*", help="One customer message. Omit it for interactive mode.")
    parser.add_argument("--full", action="store_true", help="Include routing and tool diagnostics.")
    return parser.parse_args()


def _extract_json_value(raw: str, key: str) -> Any:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*', raw)
    if not match:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(raw[match.end() :].lstrip())
    except json.JSONDecodeError:
        return None
    return value


def _find_nonempty_value(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        direct = value.get(key)
        if direct not in (None, ""):
            return direct
        for child in value.values():
            found = _find_nonempty_value(child, key)
            if found not in (None, ""):
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_nonempty_value(child, key)
            if found not in (None, ""):
                return found
    return None


def _load_request_seed(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise SystemExit(f"无法读取身份请求文件：{path}\n{exc}") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {}
    seed = dict(parsed) if isinstance(parsed, dict) else {}
    for key in (*REQUIRED_IDENTITY_FIELDS, "customer_add_wechat_id", "conversation_history"):
        if seed.get(key) in (None, ""):
            seed[key] = _find_nonempty_value(parsed, key) or _extract_json_value(raw, key)
    missing = [key for key in REQUIRED_IDENTITY_FIELDS if seed.get(key) in (None, "")]
    if missing:
        raise SystemExit("身份请求缺少字段：" + ", ".join(missing))
    history = seed.get("conversation_history")
    seed["conversation_history"] = [str(item) for item in history] if isinstance(history, list) else []
    return seed


def _isolated_settings() -> Settings:
    artifact_root = Path("artifacts") / "store_matching_real_tester"
    return Settings(service_role="reply").model_copy(
        update={
            "aics_storage_backend": "sqlite",
            "db_path": artifact_root / "isolated_state.db",
            "trace_log_dir": artifact_root / "traces",
            "log_dir": artifact_root / "traces",
            "memory_dir": artifact_root / "memory",
        }
    )


def _request(seed: dict[str, Any], query: str) -> ChatRequest:
    context = {
        "interface_version": "v3",
        "api_version": "v3",
        "reply_chain_mode": "model_led_sales_brain_v3",
        "v3_sidecar": True,
        "test_isolated": True,
        "memory_persist_allowed": False,
        "source_protocol": "local_real_store_workflow_test",
    }
    return ChatRequest(
        content=query,
        customer_id=str(seed["customer_id"]),
        corp_id=str(seed["corp_id"]),
        conversation_history=list(seed["conversation_history"]),
        user_id=int(seed["user_id"]),
        wechat=str(seed["wechat"]),
        external_userid=str(seed["external_userid"]),
        customer_add_wechat_id=seed.get("customer_add_wechat_id"),
        request_context=context,
    )


async def _run(services: Any, seed: dict[str, Any], query: str, *, full: bool) -> dict[str, Any]:
    request = _request(seed, query)
    request_context = build_request_context(request)
    request_context["test_isolated"] = True
    request_context["memory_persist_allowed"] = False
    state = services.chat_runtime._initial_state(  # noqa: SLF001
        request,
        f"isolated-store-{uuid4()}",
        request_context,
    )
    final_state = await services.chat_runtime._full_graph.ainvoke(state)  # noqa: SLF001
    facts = final_state.get("fact_envelope") if isinstance(final_state.get("fact_envelope"), dict) else {}
    structured = facts.get("structured_facts") if isinstance(facts.get("structured_facts"), dict) else {}
    resolution = (
        structured.get("store_resolution_fact")
        if isinstance(structured.get("store_resolution_fact"), dict)
        else {}
    )
    knowledge = (
        final_state.get("customer_store_knowledge")
        if isinstance(final_state.get("customer_store_knowledge"), dict)
        else {}
    )
    output: dict[str, Any] = {
        "input": query,
        "reply_messages": final_state.get("reply_messages") or [],
        "route": {
            "scene": final_state.get("scene"),
            "intent": final_state.get("intent"),
            "subflow": final_state.get("subflow"),
        },
        "store_scope": {
            "source": knowledge.get("source"),
            "store_count": knowledge.get("store_count", len(knowledge.get("stores") or [])),
            "error": knowledge.get("error", ""),
        },
        "store_resolution": {
            "status": resolution.get("status"),
            "clarification_required": resolution.get("clarification_required"),
            "candidate_store_ids": resolution.get("candidate_store_ids") or [],
            "delivery_store_ids": resolution.get("delivery_store_ids") or [],
        },
        "isolation": {
            "test_isolated": bool(final_state.get("test_isolated")),
            "memory_persist_allowed": bool(final_state.get("memory_persist_allowed")),
            "commit_graph_executed": False,
            "external_send_executed": False,
        },
        "errors": final_state.get("errors") or [],
    }
    if full:
        output["tool_plan"] = final_state.get("tool_plan") or {}
        output["tool_results"] = final_state.get("tool_results") or {}
        output["store_resolution_fact"] = resolution
        output["trace"] = final_state.get("trace") or []
    return output


async def _main() -> int:
    args = _arguments()
    seed = _load_request_seed(args.identity_source.resolve())
    services = build_reply_services(_isolated_settings())
    try:
        one_shot = " ".join(args.query).strip()
        if one_shot:
            result = await _run(services, seed, one_shot, full=args.full)
            print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
            return 0
        print("真实 V3 门店链路已启动（隔离模式，不发送、不写客户记忆）。", flush=True)
        print("输入客户消息后回车；输入 q、quit 或 exit 退出。", flush=True)
        while True:
            try:
                query = await asyncio.to_thread(input, "\n客户输入> ")
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            query = query.strip()
            if query.lower() in {"q", "quit", "exit"}:
                return 0
            if not query:
                continue
            result = await _run(services, seed, query, full=args.full)
            print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    finally:
        await services.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
