from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import HTTPException, status

from app.chat_request_context import build_request_context, conversation_id_from_request, conversation_title
from app.chat_runtime_helpers import failed_state_from_exception, safe_repository_call
from app.chat_runtime_metrics import collect_model_usage, collect_tool_calls
from app.graph.planner.runtime_plan import planner_public_route
from app.graph.state import AgentState
from app.schemas import ChatRequest, ChatResponse, ReplyMessage
from app.services.storage import AppRepository
from app.services.trace_logger import TraceLogger


class ChatRuntime:
    def __init__(self, compiled_graph: Any, trace_logger: TraceLogger, repository: AppRepository) -> None:
        self._compiled_graph = compiled_graph
        self._trace_logger = trace_logger
        self._repository = repository

    async def run_chat(self, request: ChatRequest) -> ChatResponse:
        request_id = str(uuid4())
        request_context = build_request_context(request)
        conversation_id = conversation_id_from_request(request, request_context)
        safe_repository_call(
            self._repository.upsert_conversation,
            conversation_id=conversation_id,
            request=request,
            title=conversation_title(request.content),
        )
        safe_repository_call(
            self._repository.add_user_message,
            conversation_id=conversation_id,
            request_id=request_id,
            content=request.content,
            file_image=request.file_image,
        )
        initial_state: AgentState = {
            "request_id": request_id,
            "customer_id": request.customer_id,
            "corp_id": request.corp_id,
            "content": request.content,
            "conversation_history": request.conversation_history,
            "file_image": request.file_image,
            "user_id": request.user_id,
            "wechat": request.wechat,
            "external_userid": request.external_userid,
            "customer_add_wechat_id": request.customer_add_wechat_id,
            "confirmed_store_id": request.confirmed_store_id,
            "confirmed_store_name": request.confirmed_store_name,
            "store_id": request.store_id,
            "store_name": request.store_name,
            "appointment_id": request.appointment_id,
            "appointment_time": request.appointment_time,
            "request_context": request_context,
            "trace": [],
            "errors": [],
        }

        try:
            final_state: AgentState = await self._compiled_graph.ainvoke(initial_state)
        except Exception as exc:
            failed_state = failed_state_from_exception(initial_state, exc)
            self._trace_logger.write_run(failed_state)
            safe_repository_call(
                self._repository.save_run,
                conversation_id=conversation_id,
                final_state=failed_state,
                token_usage=collect_model_usage(failed_state.get("trace", []))["summary"],
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="AI customer service run failed before producing a reply.",
            ) from exc

        route_result = planner_public_route(final_state)
        model_usage = collect_model_usage(final_state.get("trace", []))
        raw_reply_messages = final_state.get("reply_messages") or []
        if not raw_reply_messages:
            final_state.setdefault("errors", []).append(
                {
                    "stage": "final_reply",
                    "error": "Final reply model failed or produced no safe customer-facing reply.",
                }
            )
            self._trace_logger.write_run(final_state)
            safe_repository_call(
                self._repository.save_run,
                conversation_id=conversation_id,
                final_state=final_state,
                token_usage=model_usage["summary"],
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Final reply model failed or produced no safe customer-facing reply.",
            )
        reply_messages = [ReplyMessage(**message) for message in raw_reply_messages]
        log_path = self._trace_logger.write_run(final_state)
        safe_repository_call(
            self._repository.add_assistant_message,
            conversation_id=conversation_id,
            request_id=request_id,
            reply_messages=[message.model_dump() for message in reply_messages],
        )
        safe_repository_call(
            self._repository.save_run,
            conversation_id=conversation_id,
            final_state=final_state,
            token_usage=model_usage["summary"],
        )

        return ChatResponse(
            request_id=request_id,
            reply_messages=reply_messages,
            scene=str(route_result.get("scene", "")),
            intent=str(route_result.get("intent", "")),
            subflow=str(route_result.get("subflow", "")),
            trace_url=str(log_path),
            meta={
                "tool_result_keys": list((final_state.get("tool_results") or {}).keys()),
                "profile_update": final_state.get("profile_update", {}),
                "event_updates": final_state.get("event_updates", []),
                "image_info": final_state.get("image_info", {}),
                "memory_error": final_state.get("memory_error"),
                "customer_context": final_state.get("customer_context", {}),
                "customer_context_error": final_state.get("customer_context_error"),
                "model_usage": model_usage["calls"],
                "token_usage": model_usage["summary"],
                "tool_calls": collect_tool_calls(final_state.get("trace", [])),
                "planner_source": final_state.get("planner_source", ""),
                "policy_id": final_state.get("policy_id", ""),
                "policy_family_id": final_state.get("policy_family_id", ""),
                "exact_policy_id": final_state.get("exact_policy_id", ""),
                "policy_match_level": final_state.get("policy_match_level", ""),
                "policy_version": final_state.get("policy_version", ""),
                "scene_guidance_candidates": final_state.get("scene_guidance_candidates", []),
                "scene_guidance_injected": bool(final_state.get("scene_guidance_injected")),
                "reply_source": final_state.get("reply_source", ""),
                "postprocess_changed": bool(final_state.get("postprocess_changed")),
                "postprocess_reasons": final_state.get("postprocess_reasons", []),
                "conversation_id": conversation_id,
            },
        )
