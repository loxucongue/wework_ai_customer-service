from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import warnings
from pathlib import Path
from types import SimpleNamespace
from typing import Any


AI_PATHS_ROOT = Path(__file__).resolve().parents[1]
if str(AI_PATHS_ROOT) not in sys.path:
    sys.path.insert(0, str(AI_PATHS_ROOT))
warnings.simplefilter("ignore")

from app.graph.nodes.action_module_outputs import build_planner_fact_output  # noqa: E402
from app.graph.nodes.action_nodes import _resolve_customer_store_workflow  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the current V3 store-matching tool workflow against one text input."
    )
    parser.add_argument("query", nargs="*", help="Customer text. Omit it to enter interactive mode.")
    parser.add_argument(
        "--snapshot",
        type=Path,
        help="Path to store_snapshot.json. Defaults to STORE_SNAPSHOT_PATH or a local data directory.",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Print the complete workflow and planner-fact payloads.",
    )
    return parser.parse_args()


def _snapshot_candidates(explicit_path: Path | None) -> list[Path]:
    repository_root = AI_PATHS_ROOT.parent
    candidates = [explicit_path] if explicit_path else []
    env_path = str(os.getenv("STORE_SNAPSHOT_PATH") or "").strip()
    if env_path:
        candidates.append(Path(env_path))
    candidates.extend(
        [
            Path.cwd() / "data" / "store_snapshot.json",
            Path.cwd() / "ai_paths" / "data" / "store_snapshot.json",
            repository_root / "data" / "store_snapshot.json",
            AI_PATHS_ROOT / "data" / "store_snapshot.json",
        ]
    )
    return [path.resolve() for path in candidates if path is not None]


def _load_snapshot(explicit_path: Path | None) -> tuple[Path, list[dict[str, Any]]]:
    attempted: list[str] = []
    for path in _snapshot_candidates(explicit_path):
        if str(path) in attempted:
            continue
        attempted.append(str(path))
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(f"门店快照无法读取：{path}\n{exc}") from exc
        stores_by_id = payload.get("stores_by_id") if isinstance(payload, dict) else None
        if not isinstance(stores_by_id, dict) or not stores_by_id:
            raise SystemExit(f"门店快照没有有效的 stores_by_id：{path}")
        stores = [item for item in stores_by_id.values() if isinstance(item, dict)]
        os.environ["STORE_SNAPSHOT_PATH"] = str(path)
        return path, stores
    searched = "\n".join(f"  - {path}" for path in attempted)
    raise SystemExit(f"未找到门店快照。已检查：\n{searched}\n可使用 --snapshot 指定文件。")


async def _run_query(
    query: str,
    *,
    snapshot_path: Path,
    stores: list[dict[str, Any]],
    full: bool,
) -> dict[str, Any]:
    state: dict[str, Any] = {
        "content": query,
        "normalized_content": query,
        "request_context": {"interface_version": "v3"},
        "shared_context": {
            "current_message": {
                "message_ref": "current_message",
                "message_type": "text",
                "content": query,
            },
            "conversation": [],
        },
        "customer_store_knowledge": {
            "source": "local_store_snapshot",
            "stores": stores,
        },
    }
    coze_client = SimpleNamespace(settings=SimpleNamespace(geocode_workflow_id=""))
    workflow = await _resolve_customer_store_workflow(
        {
            "query": query,
            "purpose": "store_resolution_workflow",
            "use_resolver_admin_fallback": True,
            "allow_broad_scope_delivery": True,
        },
        state,
        coze_client,
        model_client=None,
    )
    tool_results = {
        key: workflow[key]
        for key in ("customer_store_lookup", "distance_calculate")
        if isinstance(workflow.get(key), dict)
    }
    planner_facts = build_planner_fact_output(tool_results, state)
    structured = planner_facts.get("structured_facts") or {}
    resolution = structured.get("store_resolution_fact") or {}
    delivery_ids = [str(item) for item in resolution.get("delivery_store_ids") or []]
    store_facts = [item for item in structured.get("store_facts") or [] if isinstance(item, dict)]
    cards = [
        item
        for store_id in delivery_ids
        for item in store_facts
        if str(item.get("store_id") or "") == store_id
    ]
    output: dict[str, Any] = {
        "input": query,
        "snapshot": str(snapshot_path),
        "loaded_store_count": len(stores),
        "workflow_status": workflow.get("status"),
        "resolution_status": resolution.get("status"),
        "clarification_required": bool(resolution.get("clarification_required")),
        "candidate_store_ids": resolution.get("candidate_store_ids") or [],
        "delivery_store_ids": delivery_ids,
        "store_cards": cards,
    }
    if full:
        output["destination_resolution"] = workflow.get("destination_resolution") or {}
        output["store_lookup_status"] = structured.get("store_lookup_status") or {}
        output["store_resolution_fact"] = resolution
        output["raw_workflow"] = workflow
        output["planner_facts"] = planner_facts
    return output


def _print_result(result: dict[str, Any]) -> None:
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> int:
    args = _parse_args()
    snapshot_path, stores = _load_snapshot(args.snapshot)
    one_shot_query = " ".join(args.query).strip()
    if one_shot_query:
        _print_result(
            asyncio.run(
                _run_query(
                    one_shot_query,
                    snapshot_path=snapshot_path,
                    stores=stores,
                    full=args.full,
                )
            )
        )
        return 0

    print(f"已加载门店快照：{snapshot_path}（{len(stores)} 条）")
    print("输入客户消息后回车；输入 q、quit 或 exit 退出。")
    while True:
        try:
            query = input("\n客户输入> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if query.lower() in {"q", "quit", "exit"}:
            return 0
        if not query:
            continue
        _print_result(
            asyncio.run(
                _run_query(
                    query,
                    snapshot_path=snapshot_path,
                    stores=stores,
                    full=args.full,
                )
            )
        )


if __name__ == "__main__":
    raise SystemExit(main())
