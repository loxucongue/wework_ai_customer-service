from __future__ import annotations

from typing import Any, Callable

from app.graph.nodes.common import looks_bad_text, model_usage_snapshot
from app.graph.nodes.image_info import fallback_image_info, validated_image_info, build_vision_prompt
from app.graph.state import AgentState
from app.services.model_client import ModelClient
from app.services.trace_logger import TraceLogger


def create_image_understanding_node(
    *,
    trace_logger: TraceLogger,
    model_client: ModelClient | None,
) -> Callable[[AgentState], Any]:
    async def image_understanding(state: AgentState) -> dict[str, Any]:
        with trace_logger.node(
            state,
            "image_understanding",
            {"file_image": state.get("file_image"), "content": state.get("normalized_content")},
        ) as span:
            has_image = bool(state.get("file_image"))
            model_call: dict[str, Any] | None = None
            if has_image and model_client and model_client.available:
                model_call = {"name": "vision_model", "input": {"tier": "vision"}}
                if model_client.provider_for_tier("vision") == "deepseek":
                    image_info = fallback_image_info(has_image=True)
                    model_call["skipped"] = "deepseek_provider_has_no_image_url_vision_support"
                else:
                    try:
                        payload = await model_client.vision_json(
                            prompt=build_vision_prompt(state),
                            image_url=str(state.get("file_image")),
                            tier="vision",
                        )
                        image_info = validated_image_info(payload, has_image=True)
                        model_call["output"] = {
                            "image_type": image_info.get("image_type"),
                            "confidence": image_info.get("confidence"),
                        }
                        model_call["usage"] = model_usage_snapshot(model_client)
                    except Exception as exc:
                        image_info = fallback_image_info(has_image=True)
                        model_call["error"] = f"{type(exc).__name__}: {exc}"
            else:
                image_info = fallback_image_info(has_image=has_image)
            if model_call:
                span["entry"]["tool_calls"] = [model_call]
            output = {"image_info": image_info, "trace": state.get("trace", [])}
            span["output_snapshot"] = output
            return output

    return image_understanding


def create_normalize_input_node(*, trace_logger: TraceLogger) -> Callable[[AgentState], Any]:
    async def normalize_input(state: AgentState) -> dict[str, Any]:
        with trace_logger.node(state, "normalize_input", {"content": state.get("content")}) as span:
            normalized = (state.get("content") or "").strip()
            if not normalized and state.get("file_image"):
                normalized = "[图片]"
            errors = list(state.get("errors", []))
            if looks_bad_text(normalized):
                errors.append({"node": "normalize_input", "message": "输入疑似乱码，已保留原文但后续会降低置信度"})
            output = {"normalized_content": normalized, "errors": errors, "trace": state.get("trace", [])}
            span["output_snapshot"] = output
            return output

    return normalize_input
