from __future__ import annotations

import argparse
import asyncio
import json
import re
import shlex
import subprocess
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
from app.graph.nodes.action_module_outputs import build_planner_fact_output  # noqa: E402
from app.graph.nodes.action_nodes import _resolve_customer_store_workflow  # noqa: E402, PLC2701
from app.schemas import ChatRequest  # noqa: E402
from app.services.coze_client import CozeClient  # noqa: E402
from app.services.customer_store_knowledge import CustomerStoreKnowledgeService  # noqa: E402
from app.services.model_client import ModelClient  # noqa: E402
from app.services.platform_agent_client import PlatformAgentClient  # noqa: E402
from app.services.store_snapshot_service import StoreSnapshotService  # noqa: E402


REQUIRED_IDENTITY_FIELDS = ("customer_id", "corp_id", "wechat", "external_userid", "user_id")
DEFAULT_PRODUCTION_HOST = "root@47.252.81.104"
DEFAULT_SSH_KEY = Path.home() / ".ssh" / "ai-paths-aliyun.pem"
PRODUCTION_RUN_DIR = "/opt/ai-paths/logs/runs"


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the production V3 reply graph with a real customer identity in write-free isolation."
    )
    parser.add_argument("input", nargs="*", help="Customer message. Omit it for interactive mode.")
    parser.add_argument("--identity-source", type=Path, help="Use a specific request JSON or pasted request text.")
    parser.add_argument("--host", default=DEFAULT_PRODUCTION_HOST, help="SSH host used to select a recent identity.")
    parser.add_argument("--ssh-key", type=Path, default=DEFAULT_SSH_KEY, help="SSH private key path.")
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


def _seed_from_parsed(parsed: Any, raw: str = "") -> dict[str, Any] | None:
    seed = dict(parsed) if isinstance(parsed, dict) else {}
    for key in (*REQUIRED_IDENTITY_FIELDS, "customer_add_wechat_id", "conversation_history"):
        if seed.get(key) in (None, ""):
            seed[key] = _find_nonempty_value(parsed, key) or (_extract_json_value(raw, key) if raw else None)
    if any(seed.get(key) in (None, "") for key in REQUIRED_IDENTITY_FIELDS):
        return None
    history = seed.get("conversation_history")
    seed["conversation_history"] = [str(item) for item in history] if isinstance(history, list) else []
    return seed


def _load_request_seed(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise SystemExit(f"无法读取身份请求文件：{path}\n{exc}") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {}
    seed = _seed_from_parsed(parsed, raw)
    if seed is None:
        raise SystemExit("身份请求缺少字段：" + ", ".join(REQUIRED_IDENTITY_FIELDS))
    return seed


def _ssh_command(host: str, ssh_key: Path) -> list[str]:
    if not ssh_key.is_file():
        raise SystemExit(f"未找到 SSH 密钥：{ssh_key}")
    return ["ssh", "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=10", "-i", str(ssh_key), host]


def _load_recent_online_seed(host: str, ssh_key: Path) -> tuple[dict[str, Any], str]:
    list_command = (
        f"find {PRODUCTION_RUN_DIR} -type f -name '*.json' -printf '%T@ %p\\n' "
        "| sort -nr | head -50 | cut -d' ' -f2-"
    )
    ssh = _ssh_command(host, ssh_key)
    try:
        listed = subprocess.run(
            [*ssh, list_command], capture_output=True, text=True, encoding="utf-8", timeout=30, check=True
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"无法读取线上 V3 日志列表：{exc}") from exc
    for remote_path in (line.strip() for line in listed.stdout.splitlines() if line.strip()):
        try:
            result = subprocess.run(
                [*ssh, "cat", remote_path],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
                check=True,
            )
            parsed = json.loads(result.stdout)
        except (json.JSONDecodeError, OSError, subprocess.SubprocessError):
            continue
        seed = _seed_from_parsed(parsed)
        if seed is not None:
            return seed, remote_path
    raise SystemExit("最近 50 条线上 V3 日志中未找到完整客户身份。")


def _run_online(host: str, ssh_key: Path, identity_path: str, query: str, *, full: bool) -> dict[str, Any]:
    command = " ".join(
        [
            "set -a; . /opt/ai-paths/.env; . /opt/ai-paths-v3/v3.env; set +a;",
            "export AI_PATHS_SERVICE_ROLE=model_led_sales_brain_v3",
            "AI_PATHS_BACKGROUND_WORKERS_ENABLED=false SOP_PLATFORM_PULL_ENABLED=false;",
            "cd /opt/ai-paths-v3/tmp;",
            "PYTHONPATH=/opt/ai-paths-v3/current/ai_paths /opt/ai-paths/venv/bin/python -",
            "--full" if full else "",
            "--identity-source",
            shlex.quote(identity_path),
            shlex.quote(query),
        ]
    )
    try:
        result = subprocess.run(
            [*_ssh_command(host, ssh_key), command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            input=Path(__file__).read_text(encoding="utf-8"),
            timeout=180,
            check=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise SystemExit("线上 V3 隔离测试超过 180 秒。") from exc
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise SystemExit(f"线上 V3 隔离测试失败：{detail.strip()}") from exc
    start = result.stdout.find("{")
    end = result.stdout.rfind("}")
    if start < 0 or end < start:
        raise SystemExit(f"线上测试未返回 JSON：{result.stdout.strip()}")
    try:
        return json.loads(result.stdout[start : end + 1])
    except json.JSONDecodeError as exc:
        raise SystemExit("线上测试返回的 JSON 无法解析。") from exc


def _isolated_settings() -> Settings:
    artifact_root = Path("artifacts") / "store_matching_real_tester"
    return Settings(
        AI_PATHS_SERVICE_ROLE="reply",
        AI_PATHS_BACKGROUND_WORKERS_ENABLED=False,
        SOP_PLATFORM_PULL_ENABLED=False,
    ).model_copy(
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


async def _run(seed: dict[str, Any], query: str, *, full: bool) -> dict[str, Any]:
    request = _request(seed, query)
    request_context = build_request_context(request)
    request_context["test_isolated"] = True
    request_context["memory_persist_allowed"] = False
    settings = _isolated_settings()
    platform_client = PlatformAgentClient(settings)
    coze_client = CozeClient(settings)
    model_client = ModelClient(settings)
    snapshot_service = StoreSnapshotService(settings, platform_client)
    knowledge_service = CustomerStoreKnowledgeService(platform_client, snapshot_service)
    try:
        knowledge = await asyncio.to_thread(knowledge_service.load, request_context=request_context)
        state = {
            "content": query,
            "normalized_content": query,
            "conversation_history": list(seed["conversation_history"]),
            "request_context": request_context,
            "customer_store_knowledge": knowledge,
            "request_id": f"isolated-store-tool-{uuid4()}",
        }
        workflow = await _resolve_customer_store_workflow(
            {
                "name": "resolve_customer_store",
                "arguments": {
                    "purpose": "store_search",
                    "destination_hint": query,
                    "use_resolver_admin_fallback": True,
                    "allow_broad_scope_delivery": True,
                },
            },
            state,
            coze_client,
            model_client=model_client,
        )
        lookup = workflow.get("customer_store_lookup", {})
        fact_output = build_planner_fact_output({"customer_store_lookup": lookup}, state)
        structured = fact_output.get("structured_facts", {})
        resolution = structured.get("store_resolution_fact", {})
    finally:
        await coze_client.aclose()
        await model_client.aclose()
        platform_client.close()
    delivery_ids = {str(item) for item in resolution.get("delivery_store_ids") or []}
    delivery_stores = [
        item
        for item in lookup.get("stores") or []
        if str(item.get("store_id") or item.get("id") or "") in delivery_ids
    ]
    output: dict[str, Any] = {
        "input": query,
        "store_scope": {
            "source": knowledge.get("source"),
            "store_count": knowledge.get("store_count", len(knowledge.get("stores") or [])),
            "error": knowledge.get("error") or knowledge.get("store_scope_error") or "",
        },
        "store_resolution": {
            "status": resolution.get("status"),
            "clarification_required": resolution.get("clarification_required"),
            "candidate_store_ids": resolution.get("candidate_store_ids") or [],
            "delivery_store_ids": resolution.get("delivery_store_ids") or [],
        },
        "delivery_stores": delivery_stores,
        "isolation": {
            "reply_model_executed": False,
            "reply_messages_generated": False,
            "memory_persisted": False,
            "external_send_executed": False,
        },
        "errors": ([lookup.get("error")] if lookup.get("error") else []),
    }
    if full:
        output.update(
            destination_resolution=workflow.get("destination_resolution") or {},
            customer_store_lookup=lookup,
            store_resolution_fact=resolution,
        )
    return output


async def _main() -> int:
    args = _arguments()
    input_parts = list(args.input)
    identity_source = args.identity_source
    if identity_source is None and input_parts and Path(input_parts[0]).is_file():
        identity_source = Path(input_parts.pop(0))
    if identity_source is not None:
        seed = _load_request_seed(identity_source.resolve())
        identity_label = str(identity_source)
        online_identity_path = ""
    else:
        print("正在从线上近期 V3 日志选择一个客户身份...", flush=True)
        seed, online_identity_path = _load_recent_online_seed(args.host, args.ssh_key.expanduser().resolve())
        identity_label = "线上近期 V3 日志"
    print(f"身份已就绪（来源：{identity_label}，客户标识已隐藏）。", flush=True)
    ssh_key = args.ssh_key.expanduser().resolve()
    one_shot = " ".join(input_parts).strip()
    if one_shot:
        result = (
            await asyncio.to_thread(_run_online, args.host, ssh_key, online_identity_path, one_shot, full=args.full)
            if online_identity_path
            else await _run(seed, one_shot, full=args.full)
        )
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        return 0
    print("真实 V3 门店匹配工具已启动（不生成回复、不发送、不写客户记忆）。", flush=True)
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
        result = (
            await asyncio.to_thread(_run_online, args.host, ssh_key, online_identity_path, query, full=args.full)
            if online_identity_path
            else await _run(seed, query, full=args.full)
        )
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
