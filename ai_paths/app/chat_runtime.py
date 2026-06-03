from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import HTTPException, status

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

        route_result = final_state.get("route_result", {})
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
                "intents": final_state.get("intents", []),
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
                "conversation_id": conversation_id,
            },
        )


def build_request_context(request: ChatRequest) -> dict[str, Any]:
    context = dict(request.request_context or {})
    fields = {
        "user_id": request.user_id,
        "corp_id": request.corp_id,
        "wechat": request.wechat,
        "external_userid": request.external_userid,
        "customer_id": request.customer_id,
        "customer_add_wechat_id": request.customer_add_wechat_id,
        "confirmed_store_id": request.confirmed_store_id,
        "confirmed_store_name": request.confirmed_store_name,
        "store_id": request.store_id,
        "store_name": request.store_name,
        "appointment_id": request.appointment_id,
        "appointment_time": request.appointment_time,
    }
    for key, value in fields.items():
        if value not in (None, ""):
            context[key] = value
    return context


def conversation_id_from_request(request: ChatRequest, request_context: dict[str, Any]) -> str:
    explicit = request_context.get("conversation_id") or request_context.get("session_id")
    return str(explicit or request.customer_id or request.external_userid or "unknown")


def conversation_title(content: str) -> str:
    title = (content or "").strip().replace("\n", " ")
    if not title:
        return "图片咨询"
    return title[:40]


def safe_repository_call(func: Any, **kwargs: Any) -> None:
    try:
        func(**kwargs)
    except Exception:
        return


def failed_state_from_exception(initial_state: AgentState, exc: Exception) -> AgentState:
    error = f"{type(exc).__name__}: {exc}"
    state: AgentState = dict(initial_state)
    state["reply_messages"] = []
    state["errors"] = [
        *list(state.get("errors") or []),
        {
            "stage": "run_chat",
            "error": error,
        },
    ]
    state["trace"] = [
        *list(state.get("trace") or []),
        {
            "node": "run_chat",
            "started_at": "",
            "finished_at": "",
            "duration_ms": 0,
            "input_snapshot": {
                "content": state.get("content", ""),
                "customer_id": state.get("customer_id", ""),
                "corp_id": state.get("corp_id", ""),
                "file_image": bool(state.get("file_image")),
            },
            "output_snapshot": {},
            "tool_calls": [],
            "error": error,
        },
    ]
    return state


def collect_model_usage(trace: list[dict[str, Any]]) -> dict[str, Any]:
    calls: list[dict[str, Any]] = []
    summary = {
        "planner_tokens": 0,
        "reply_tokens": 0,
        "vision_tokens": 0,
        "other_tokens": 0,
        "total_tokens": 0,
    }

    def add_call(node: str, call: dict[str, Any]) -> None:
        usage = call.get("usage") if isinstance(call.get("usage"), dict) else {}
        total = int(usage.get("total_tokens") or usage.get("token_count") or 0)
        if total > 0:
            item = {
                "node": node,
                "name": call.get("name", ""),
                "provider": usage.get("provider", ""),
                "model": usage.get("model", ""),
                "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                "completion_tokens": int(usage.get("completion_tokens") or 0),
                "total_tokens": total,
            }
            calls.append(item)
            if node == "planner_brain":
                summary["planner_tokens"] += total
            elif node == "synthesize_reply":
                summary["reply_tokens"] += total
            elif node == "image_understanding":
                summary["vision_tokens"] += total
            else:
                summary["other_tokens"] += total
            summary["total_tokens"] += total
        for nested in call.get("nested_calls", []) or []:
            if isinstance(nested, dict):
                add_call(node, nested)

    for entry in trace or []:
        node = str(entry.get("node") or "")
        for call in entry.get("tool_calls", []) or []:
            if not isinstance(call, dict):
                continue
            add_call(node, call)
    return {"calls": calls, "summary": summary}


def collect_tool_calls(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def add_call(node: str, call: dict[str, Any]) -> None:
        calls.append(
            {
                "node": node,
                "name": call.get("name", ""),
                "input": call.get("input", {}),
                "output": call.get("output", {}),
                "error": call.get("error", ""),
                "usage": call.get("usage", {}),
            }
        )
        for nested in call.get("nested_calls", []) or []:
            if isinstance(nested, dict):
                add_call(node, nested)

    for entry in trace or []:
        node = str(entry.get("node") or "")
        for call in entry.get("tool_calls", []) or []:
            if not isinstance(call, dict):
                continue
            add_call(node, call)
    return calls[:30]
