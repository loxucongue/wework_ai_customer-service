from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from app.config import Settings
from app.graph.nodes.common import clean_model_text
from app.schemas import ChatRequest


VOICE_FALLBACK_TEXT = "客户发送了一条语音消息，但系统暂时无法转写，请引导客户发文字或重新发送。"
VOICE_TRANSCRIPTION_ATTEMPTS = 3
DOUBAO_ASR_SUCCESS = "20000000"
DOUBAO_ASR_PROCESSING = {"20000001", "20000002"}


class VoiceTranscriptionClient(Protocol):
    provider_name: str
    resource_id: str

    async def transcribe(self, audio_url: str, *, uid: str = "") -> dict[str, Any]:
        ...


class DoubaoAsrClient:
    provider_name = "doubao_asr"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.resource_id = str(settings.doubao_asr_resource_id or "volc.seedasr.auc").strip()
        self._client: httpx.AsyncClient | None = None
        self._client_loop_id: int | None = None

    async def transcribe(self, audio_url: str, *, uid: str = "") -> dict[str, Any]:
        if not self._is_configured():
            raise RuntimeError("doubao_asr_auth_not_configured")
        task_id = str(uuid.uuid4())
        submit_started = time.monotonic()
        resolved_audio_url, redirects = await self._resolve_audio_url(audio_url)
        submit_response = await self._submit(audio_url=resolved_audio_url, task_id=task_id, uid=uid)
        query_result = await self._query(task_id=task_id)
        return {
            "text": _doubao_result_text(query_result.get("body") or {}),
            "task_id": task_id,
            "provider": self.provider_name,
            "resource_id": self.resource_id,
            "audio_url_resolved": resolved_audio_url != audio_url,
            "audio_redirect_count": len(redirects),
            "audio_redirect_hosts": [item.get("host", "") for item in redirects],
            "submit_status_code": submit_response.get("status_code", ""),
            "submit_message": submit_response.get("message", ""),
            "submit_log_id": submit_response.get("log_id", ""),
            "query_status_code": query_result.get("status_code", ""),
            "query_message": query_result.get("message", ""),
            "query_log_id": query_result.get("log_id", ""),
            "query_attempt_count": query_result.get("attempt_count", 0),
            "duration_ms": int((time.monotonic() - submit_started) * 1000),
        }

    async def _resolve_audio_url(self, audio_url: str) -> tuple[str, list[dict[str, Any]]]:
        current_url = audio_url
        redirects: list[dict[str, Any]] = []
        for _ in range(3):
            try:
                response = await self._http_client().get(
                    current_url,
                    headers={"Range": "bytes=0-0"},
                    follow_redirects=False,
                )
            except Exception:
                return current_url, redirects
            if response.status_code not in {301, 302, 303, 307, 308}:
                return current_url, redirects
            location = response.headers.get("Location")
            if not location:
                return current_url, redirects
            next_url = str(response.url.join(location))
            redirects.append(
                {
                    "status_code": response.status_code,
                    "host": urlparse(next_url).netloc,
                }
            )
            current_url = next_url
        return current_url, redirects

    async def _submit(self, *, audio_url: str, task_id: str, uid: str) -> dict[str, Any]:
        payload = {
            "user": {"uid": uid or "ai-paths"},
            "audio": {
                "format": _audio_format_from_url(audio_url),
                "url": audio_url,
            },
            "request": {
                "model_name": "bigmodel",
                "enable_itn": True,
                "enable_punc": True,
            },
        }
        response = await self._http_client().post(
            str(self.settings.doubao_asr_submit_url),
            headers=self._headers(task_id=task_id, submit=True),
            json=payload,
        )
        response.raise_for_status()
        result = self._response_summary(response)
        if result["status_code"] != DOUBAO_ASR_SUCCESS:
            raise RuntimeError(
                f"doubao_asr_submit_failed status={result['status_code']} message={result['message']}"
            )
        return result

    async def _query(self, *, task_id: str) -> dict[str, Any]:
        last_summary: dict[str, Any] = {}
        attempts = max(1, int(self.settings.doubao_asr_poll_attempts or 1))
        interval = max(0.2, float(self.settings.doubao_asr_poll_interval_seconds or 1.0))
        for attempt in range(1, attempts + 1):
            response = await self._http_client().post(
                str(self.settings.doubao_asr_query_url),
                headers=self._headers(task_id=task_id, submit=False),
                json={},
            )
            response.raise_for_status()
            summary = self._response_summary(response)
            summary["attempt_count"] = attempt
            summary["body"] = _safe_response_json(response)
            last_summary = summary
            status_code = summary["status_code"]
            if status_code == DOUBAO_ASR_SUCCESS:
                return summary
            if status_code not in DOUBAO_ASR_PROCESSING:
                raise RuntimeError(
                    f"doubao_asr_query_failed status={status_code} message={summary['message']}"
                )
            if attempt < attempts:
                await asyncio.sleep(interval)
        raise TimeoutError(
            f"doubao_asr_query_timeout status={last_summary.get('status_code', '')} "
            f"message={last_summary.get('message', '')}"
        )

    def _headers(self, *, task_id: str, submit: bool) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Request-Id": task_id,
        }
        api_key = str(self.settings.doubao_asr_api_key or "").strip()
        if api_key:
            headers["X-Api-Key"] = api_key
        else:
            headers["X-Api-App-Key"] = str(self.settings.doubao_asr_app_key or "").strip()
            headers["X-Api-Access-Key"] = str(self.settings.doubao_asr_access_key or "").strip()
        if submit:
            headers["X-Api-Sequence"] = "-1"
        return headers

    def _is_configured(self) -> bool:
        if str(self.settings.doubao_asr_api_key or "").strip():
            return True
        return bool(
            str(self.settings.doubao_asr_app_key or "").strip()
            and str(self.settings.doubao_asr_access_key or "").strip()
        )

    def _http_client(self) -> httpx.AsyncClient:
        loop_id = id(asyncio.get_running_loop())
        timeout = max(1.0, float(self.settings.doubao_asr_timeout_seconds or 15.0))
        if self._client is None or self._client.is_closed or self._client_loop_id != loop_id:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(timeout, connect=min(5.0, timeout)),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
            self._client_loop_id = loop_id
        return self._client

    async def aclose(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    @staticmethod
    def _response_summary(response: httpx.Response) -> dict[str, str]:
        return {
            "status_code": str(response.headers.get("X-Api-Status-Code") or ""),
            "message": str(response.headers.get("X-Api-Message") or ""),
            "log_id": str(response.headers.get("X-Tt-Logid") or ""),
        }


async def transcribe_voice_request(
    request: ChatRequest,
    transcription_client: VoiceTranscriptionClient,
) -> ChatRequest:
    request_context = request.request_context if isinstance(request.request_context, dict) else {}
    if str(request_context.get("msgtype") or "").strip().lower() != "voice":
        return request

    original_content = str(request.content or "").strip()
    audio_url = _audio_url_from_request(request)
    transcription: dict[str, Any] = {
        "status": "skipped",
        "provider": getattr(transcription_client, "provider_name", "voice_transcription"),
        "resource_id": getattr(transcription_client, "resource_id", ""),
        "source_msgtype": "voice",
        "original_content_preview": original_content[:160],
    }
    if audio_url:
        transcription["audio_url_present"] = True
    if not audio_url:
        transcription.update({"status": "failed", "error": "missing_audio_url"})
        return _request_with_voice_fallback(request, transcription)

    attempts: list[dict[str, Any]] = []
    last_error = ""
    uid = _voice_uid_from_request(request)
    for attempt in range(1, VOICE_TRANSCRIPTION_ATTEMPTS + 1):
        try:
            raw = await transcription_client.transcribe(audio_url, uid=uid)
            output = clean_model_text(str(raw.get("text") or ""))
            attempt_info = {
                "attempt": attempt,
                "task_id": str(raw.get("task_id") or ""),
                "submit_status_code": str(raw.get("submit_status_code") or ""),
                "submit_message": str(raw.get("submit_message") or "")[:200],
                "submit_log_id": str(raw.get("submit_log_id") or ""),
                "query_status_code": str(raw.get("query_status_code") or ""),
                "query_message": str(raw.get("query_message") or "")[:200],
                "query_log_id": str(raw.get("query_log_id") or ""),
                "query_attempt_count": raw.get("query_attempt_count", 0),
                "duration_ms": raw.get("duration_ms", 0),
                "audio_url_resolved": bool(raw.get("audio_url_resolved")),
                "audio_redirect_count": raw.get("audio_redirect_count", 0),
                "audio_redirect_hosts": raw.get("audio_redirect_hosts") or [],
                "output_preview": output[:80],
            }
            attempts.append(attempt_info)
            if output:
                transcription.update(
                    {
                        "status": "ok",
                        "attempts": attempts,
                        "attempt_count": attempt,
                        "output_preview": output[:160],
                        "task_id": attempt_info["task_id"],
                        "submit_status_code": attempt_info["submit_status_code"],
                        "submit_message": attempt_info["submit_message"],
                        "submit_log_id": attempt_info["submit_log_id"],
                        "query_status_code": attempt_info["query_status_code"],
                        "query_message": attempt_info["query_message"],
                        "query_log_id": attempt_info["query_log_id"],
                        "query_attempt_count": attempt_info["query_attempt_count"],
                        "audio_url_resolved": attempt_info["audio_url_resolved"],
                        "audio_redirect_count": attempt_info["audio_redirect_count"],
                        "audio_redirect_hosts": attempt_info["audio_redirect_hosts"],
                    }
                )
                return _request_with_transcribed_content(request, output, transcription)
            last_error = "empty_output"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            attempts.append({"attempt": attempt, "error": last_error[:200]})
        if attempt < VOICE_TRANSCRIPTION_ATTEMPTS:
            await asyncio.sleep(0.4 * attempt)
    transcription.update({"status": "failed", "attempts": attempts, "attempt_count": len(attempts), "error": last_error})
    return _request_with_voice_fallback(request, transcription)


def _request_with_transcribed_content(request: ChatRequest, text: str, transcription: dict[str, Any]) -> ChatRequest:
    context = dict(request.request_context or {})
    context["voice_transcription"] = transcription
    context["voice_original_content"] = str(request.content or "").strip()
    return request.model_copy(update={"content": text, "request_context": context})


def _request_with_voice_fallback(request: ChatRequest, transcription: dict[str, Any]) -> ChatRequest:
    context = dict(request.request_context or {})
    context["voice_transcription"] = transcription
    context["voice_original_content"] = str(request.content or "").strip()
    return request.model_copy(update={"content": VOICE_FALLBACK_TEXT, "request_context": context})


def _audio_url_from_request(request: ChatRequest) -> str:
    content = str(request.content or "").strip()
    if content.startswith(("http://", "https://")):
        return content
    context = request.request_context if isinstance(request.request_context, dict) else {}
    raw = context.get("raw_workflow_payload")
    if isinstance(raw, dict):
        parameters = raw.get("parameters") if isinstance(raw.get("parameters"), dict) else {}
        content_obj = parameters.get("content") if isinstance(parameters.get("content"), dict) else {}
        raw_content = str(content_obj.get("content") or "").strip()
        if raw_content.startswith(("http://", "https://")):
            return raw_content
    return ""


def _voice_uid_from_request(request: ChatRequest) -> str:
    context = request.request_context if isinstance(request.request_context, dict) else {}
    return (
        str(request.customer_id or "").strip()
        or str(context.get("external_userid") or "").strip()
        or str(context.get("msgid") or "").strip()
        or "ai-paths"
    )


def _audio_format_from_url(audio_url: str) -> str:
    path = urlparse(audio_url).path.lower()
    suffix = path.rsplit(".", 1)[-1] if "." in path else ""
    if suffix in {"mp3", "wav", "ogg"}:
        return suffix
    return "mp3"


def _doubao_result_text(raw: dict[str, Any]) -> str:
    if not isinstance(raw, dict):
        return ""
    result = raw.get("result")
    if isinstance(result, dict):
        text = clean_model_text(str(result.get("text") or ""))
        if text:
            return text
        utterances = result.get("utterances")
        if isinstance(utterances, list):
            return clean_model_text("".join(str(item.get("text") or "") for item in utterances if isinstance(item, dict)))
    if isinstance(result, list):
        return clean_model_text("".join(str(item.get("text") or "") for item in result if isinstance(item, dict)))
    return clean_model_text(str(raw.get("text") or ""))


def _safe_response_json(response: httpx.Response) -> dict[str, Any]:
    if not response.content:
        return {}
    try:
        parsed = response.json()
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
