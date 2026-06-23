import json
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from app.config import Settings
from app.graph.state import AgentState, TraceEntry

RUN_OBSERVABILITY_KEYS = (
    "policy_id",
    "policy_family_id",
    "policy_match_level",
    "policy_version",
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compact(value: Any, max_chars: int = 1600) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        text = str(value)
        if text.startswith("data:image/") and ";base64," in text:
            mime = text.split(";", 1)[0].replace("data:", "")
            return f"[base64 image omitted: {mime}, {len(text)} chars]"
        return text[:max_chars] + "..." if len(text) > max_chars else value
    if isinstance(value, list):
        return [compact(item, max_chars=max_chars // 2) for item in value[:8]]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, val in list(value.items())[:24]:
            if key == "trace" and isinstance(val, list):
                result[key] = f"{len(val)} trace entries"
                continue
            result[key] = compact(val, max_chars=max_chars // 2)
        return result
    text = str(value)
    return text[:max_chars] + "..." if len(text) > max_chars else text


class TraceLogger:
    def __init__(self, settings: Settings):
        self.log_dir: Path = settings.trace_log_dir or settings.log_dir

    @contextmanager
    def node(self, state: AgentState, node_name: str, input_snapshot: dict[str, Any]) -> Iterator[dict[str, Any]]:
        started = time.perf_counter()
        entry: TraceEntry = {
            "node": node_name,
            "started_at": utc_now_iso(),
            "input_snapshot": compact(input_snapshot),
            "tool_calls": [],
            "error": None,
        }
        result: dict[str, Any] = {"entry": entry}
        try:
            yield result
        except Exception as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            entry["finished_at"] = utc_now_iso()
            entry["duration_ms"] = int((time.perf_counter() - started) * 1000)
            if "output_snapshot" in result:
                entry["output_snapshot"] = compact(result["output_snapshot"])
            state.setdefault("trace", []).append(entry)

    def write_run(self, state: AgentState) -> Path:
        request_id = state.get("request_id") or "unknown"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        path = self.log_dir / f"{request_id}.json"
        serializable = compact(state, max_chars=50000)
        if isinstance(serializable, dict) and isinstance(state.get("trace"), list):
            serializable["trace"] = [compact(entry, max_chars=20000) for entry in state.get("trace", [])]
            for key in RUN_OBSERVABILITY_KEYS:
                if key in state:
                    serializable[key] = compact(state.get(key), max_chars=20000)
        path.write_text(json.dumps(serializable, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def read_run(self, request_id: str) -> dict[str, Any]:
        if not request_id:
            return {}
        path = self.log_dir / f"{request_id}.json"
        if not path.exists():
            return {}
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
            return parsed if isinstance(parsed, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}
