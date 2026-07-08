from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from urllib import request


FAKE_PREFIXES = ("codex_", "fake", "dummy", "mock", "offline_")


def _load_payload(path: str) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise SystemExit("payload must be a JSON object")
    return data


def _identity_value(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if value is None and isinstance(payload.get("request_context"), dict):
        value = payload["request_context"].get(key)
    return str(value or "").strip()


def _validate_real_identity(payload: dict[str, Any]) -> None:
    customer_id = _identity_value(payload, "customer_id")
    external_userid = _identity_value(payload, "external_userid")
    corp_id = _identity_value(payload, "corp_id")
    user_id = _identity_value(payload, "user_id")
    wechat = _identity_value(payload, "wechat")
    missing = [
        name
        for name, value in {
            "customer_id": customer_id,
            "external_userid": external_userid,
            "corp_id": corp_id,
            "user_id": user_id,
            "wechat": wechat,
        }.items()
        if not value
    ]
    if missing:
        raise SystemExit(f"missing real platform identity fields: {', '.join(missing)}")
    for name, value in {"customer_id": customer_id, "external_userid": external_userid}.items():
        lowered = value.lower()
        if lowered.startswith(FAKE_PREFIXES) or "codex" in lowered:
            raise SystemExit(
                f"{name}={value!r} looks like a synthetic id. "
                "Use real platform customer_id/external_userid for store-scope tests."
            )


def _post_json(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return {"status": resp.status, "body": json.loads(body)}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Probe store-related chat/reply flows with real platform identity. "
            "Synthetic ids are rejected because platform store/index cannot return customer scope for them."
        )
    )
    parser.add_argument("payload", help="UTF-8 JSON request payload with real platform ids")
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8000/reply/workflow-compatible",
        help="chat or reply endpoint",
    )
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()

    payload = _load_payload(args.payload)
    _validate_real_identity(payload)
    payload.setdefault("memory_persist_allowed", False)
    if isinstance(payload.get("request_context"), dict):
        payload["request_context"].setdefault("memory_persist_allowed", False)
    result = _post_json(args.url, payload, args.timeout)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
