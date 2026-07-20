from __future__ import annotations

import asyncio
import json
from typing import Any

from app.graph.nodes.common import clean_model_text
from app.schemas import ChatRequest
from app.services.coze_client import CozeClient


VOICE_FALLBACK_TEXT = "客户发送了一条语音消息，但系统暂时无法转写，请引导客户发文字或重新发送。"
VOICE_TRANSCRIPTION_ATTEMPTS = 3


async def transcribe_voice_request(request: ChatRequest, coze_client: CozeClient) -> ChatRequest:
    request_context = request.request_context if isinstance(request.request_context, dict) else {}
    if str(request_context.get("msgtype") or "").strip().lower() != "voice":
        return request

    original_content = str(request.content or "").strip()
    audio_url = _audio_url_from_request(request)
    workflow_id = str(getattr(coze_client.settings, "audio_to_text_workflow_id", "") or "").strip()
    transcription: dict[str, Any] = {
        "status": "skipped",
        "workflow_id": workflow_id,
        "source_msgtype": "voice",
        "original_content_preview": original_content[:160],
    }
    if audio_url:
        transcription["audio_url_present"] = True
    if not workflow_id:
        transcription.update({"status": "failed", "error": "audio_to_text_workflow_id_not_configured"})
        return _request_with_voice_fallback(request, transcription)
    if not audio_url:
        transcription.update({"status": "failed", "error": "missing_audio_url"})
        return _request_with_voice_fallback(request, transcription)

    attempts: list[dict[str, Any]] = []
    last_error = ""
    for attempt in range(1, VOICE_TRANSCRIPTION_ATTEMPTS + 1):
        try:
            raw = await coze_client.run_workflow(workflow_id, {"input": audio_url})
            output = _workflow_output_text(raw)
            attempt_info = {
                "attempt": attempt,
                "coze_code": raw.get("code"),
                "coze_msg": str(raw.get("msg") or "")[:200],
                "execute_id": str(raw.get("execute_id") or ""),
                "debug_url": str(raw.get("debug_url") or ""),
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
                        "execute_id": attempt_info["execute_id"],
                        "debug_url": attempt_info["debug_url"],
                        "coze_code": attempt_info["coze_code"],
                        "coze_msg": attempt_info["coze_msg"],
                    }
                )
                return _request_with_transcribed_content(request, output, transcription)
            last_error = str(raw.get("msg") or "empty_output")[:200]
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


def _workflow_output_text(raw: dict[str, Any]) -> str:
    if not isinstance(raw, dict):
        return ""
    data = raw.get("data")
    parsed: Any = data
    if isinstance(data, str) and data:
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            parsed = {"output": data}
    if isinstance(parsed, dict):
        for key in ("output", "text", "result"):
            output = clean_model_text(str(parsed.get(key) or ""))
            if output:
                return output
    return clean_model_text(str(parsed or raw.get("output") or ""))
