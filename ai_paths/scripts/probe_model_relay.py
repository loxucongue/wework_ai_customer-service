from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from typing import Any

import httpx


DEFAULT_MODELS = [
    "gpt-5.4-mini",
    "gpt-5.4",
]


def _split_models(value: str | None) -> list[str]:
    if not value:
        return list(DEFAULT_MODELS)
    return [item.strip() for item in value.split(",") if item.strip()]


def _settings_defaults() -> dict[str, str]:
    try:
        from app.config import Settings

        settings = Settings()
        return {
            "base_url": settings.model_relay_base_url or settings.anthropic_base_url,
            "api_key": settings.model_relay_api_key or settings.claude_relay_api_key or settings.anthropic_auth_token,
            "claude_api_key": settings.claude_relay_api_key,
            "protocol": settings.model_relay_protocol,
            "anthropic_base_url": settings.anthropic_base_url,
        }
    except Exception:
        return {"base_url": "", "api_key": "", "claude_api_key": "", "protocol": "auto", "anthropic_base_url": ""}


def _anthropic_messages_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        return f"{base}/messages"
    return f"{base}/v1/messages"


def _anthropic_text(raw: dict[str, Any]) -> str:
    return "".join(
        str(part.get("text") or "")
        for part in raw.get("content") or []
        if isinstance(part, dict)
    ).strip()


async def _probe_model(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    api_key: str,
    model: str,
    json_mode: bool,
    protocol: str,
    use_response_format: bool,
    reasoning_enabled: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        if protocol == "anthropic":
            url = _anthropic_messages_url(base_url)
            prompt = (
                '{"task":"connectivity_test","reply_schema":{"ok":true,"model":"string"}}'
                if json_mode
                else "Return one short sentence saying the model is reachable."
            )
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "max_tokens": 64,
            }
            if json_mode:
                payload["system"] = "Return valid JSON only."
            payload["reasoning"] = {"enabled": reasoning_enabled}
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json; charset=utf-8",
                },
                content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            )
        else:
            url = f"{base_url.rstrip('/')}/chat/completions"
            payload: dict[str, Any] = {
                "model": model,
                "messages": [
                    {"role": "system", "content": "You are a connectivity test. Reply briefly."},
                    {"role": "user", "content": "Return one short sentence saying the model is reachable."},
                ],
                "temperature": 0,
                "max_tokens": 64,
            }
            if json_mode:
                payload["messages"] = [
                    {"role": "system", "content": "Return valid json only."},
                    {
                        "role": "user",
                        "content": 'Return valid json only. {"task":"connectivity_test","reply_schema":{"ok":true,"model":"string"}}',
                    },
                ]
                if use_response_format:
                    payload["response_format"] = {"type": "json_object"}
            payload["reasoning"] = {"enabled": reasoning_enabled}
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        text = response.text
        result: dict[str, Any] = {
            "model": model,
            "protocol": protocol,
            "ok": response.is_success,
            "status_code": response.status_code,
            "elapsed_ms": elapsed_ms,
        }
        if response.is_success:
            raw = response.json()
            if protocol == "anthropic":
                content = _anthropic_text(raw)
            else:
                choice = (raw.get("choices") or [{}])[0]
                message = choice.get("message") or {}
                content = str(message.get("content") or "")
            result["content_preview"] = content[:160]
            result["usage"] = raw.get("usage") or {}
        else:
            result["error_preview"] = text[:500]
        return result
    except Exception as exc:
        return {
            "model": model,
            "ok": False,
            "status_code": None,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "error_preview": f"{type(exc).__name__}: {exc}",
        }


async def _main() -> int:
    parser = argparse.ArgumentParser(description="Probe OpenAI-compatible relay models and response speed.")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--api-key-env", default="MODEL_RELAY_API_KEY")
    parser.add_argument("--models", default=os.getenv("MODEL_RELAY_PROBE_MODELS", ""))
    parser.add_argument("--timeout", type=float, default=float(os.getenv("MODEL_RELAY_PROBE_TIMEOUT", "60")))
    parser.add_argument("--protocol", choices=["auto", "openai", "anthropic"], default=os.getenv("MODEL_RELAY_PROTOCOL", "auto"))
    parser.add_argument("--json-mode", action="store_true", help="Ask the model to return a JSON object.")
    parser.add_argument("--no-response-format", action="store_true", help="Do not send OpenAI response_format=json_object.")
    parser.add_argument("--reasoning-enabled", action="store_true", help="Send reasoning.enabled=true instead of the default false.")
    args = parser.parse_args()

    defaults = _settings_defaults()
    base_url = (
        args.base_url.strip()
        or os.getenv("MODEL_RELAY_BASE_URL", "").strip()
        or defaults.get("base_url", "").strip()
        or os.getenv("ANTHROPIC_BASE_URL", "").strip()
    )
    api_key = (
        os.getenv(args.api_key_env, "").strip()
        or (defaults.get("claude_api_key", "").strip() if args.api_key_env == "CLAUDE_RELAY_API_KEY" else "")
        or defaults.get("api_key", "").strip()
        or os.getenv("ANTHROPIC_AUTH_TOKEN", "").strip()
    )
    if not base_url:
        print("Missing MODEL_RELAY_BASE_URL or --base-url", flush=True)
        return 2
    if not api_key:
        print(f"Missing {args.api_key_env}, CLAUDE_RELAY_API_KEY, or ANTHROPIC_AUTH_TOKEN", flush=True)
        return 2
    protocol = (args.protocol if args.protocol != "auto" else defaults.get("protocol") or "auto").strip().lower()
    if protocol == "auto":
        protocol = (
            "anthropic"
            if (
                os.getenv("ANTHROPIC_BASE_URL", "").strip()
                or (defaults.get("anthropic_base_url") and base_url.rstrip("/") == defaults.get("anthropic_base_url", "").rstrip("/"))
            )
            else "openai"
        )

    async with httpx.AsyncClient(timeout=httpx.Timeout(args.timeout, connect=min(10.0, args.timeout))) as client:
        results = []
        for model in _split_models(args.models):
            result = await _probe_model(
                client,
                base_url=base_url,
                api_key=api_key,
                model=model,
                json_mode=args.json_mode,
                protocol=protocol,
                use_response_format=not args.no_response_format,
                reasoning_enabled=bool(args.reasoning_enabled),
            )
            results.append(result)
            status = "OK" if result["ok"] else "FAIL"
            print(f"{status}\t{result['model']}\t{result['elapsed_ms']}ms\tHTTP {result['status_code']}", flush=True)
        print(
            json.dumps(
                {
                    "base_url": base_url,
                    "protocol": protocol,
                    "json_mode": args.json_mode,
                    "response_format": bool(args.json_mode and not args.no_response_format),
                    "reasoning_enabled": bool(args.reasoning_enabled),
                    "results": results,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0 if all(item.get("ok") for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
