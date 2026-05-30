from uuid import uuid4
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, status

from app.config import get_settings
from app.graph.builder import build_graph
from app.graph.state import AgentState
from app.schemas import ChatRequest, ChatResponse, ReplyMessage
from app.services.coze_client import CozeClient
from app.services.customer_context import CustomerContextService
from app.services.memory_store import CustomerMemoryStore
from app.services.model_client import ModelClient
from app.services.platform_agent_client import PlatformAgentClient
from app.services.pricing_repository import LocalPricingRepository
from app.services.store_service import StoreService
from app.services.trace_logger import TraceLogger

settings = get_settings()
trace_logger = TraceLogger(settings)
coze_client = CozeClient(settings)
model_client = ModelClient(settings)
memory_store = CustomerMemoryStore(settings)
pricing_repository = LocalPricingRepository(settings)
platform_agent_client = PlatformAgentClient(settings)
customer_context_service = CustomerContextService(platform_agent_client)
store_service = StoreService(platform_agent_client)
compiled_graph = build_graph(
    coze_client,
    trace_logger,
    model_client,
    memory_store,
    pricing_repository,
    customer_context_service,
    store_service,
)

app = FastAPI(title=settings.app_name)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/chat")
async def chat_info() -> dict[str, str]:
    return {
        "status": "ok",
        "message": "Use POST /chat with content, customer_id, corp_id, conversation_history, and optional file_image.",
    }


async def require_api_key(authorization: str | None = Header(default=None)) -> None:
    if not settings.ai_paths_api_key:
        return
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or token != settings.ai_paths_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API token",
        )


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, _: None = Depends(require_api_key)) -> ChatResponse:
    request_id = str(uuid4())
    request_context = build_request_context(request)
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

    final_state: AgentState = await compiled_graph.ainvoke(initial_state)
    log_path = trace_logger.write_run(final_state)
    route_result = final_state.get("route_result", {})
    model_usage = collect_model_usage(final_state.get("trace", []))
    reply_messages = [
        ReplyMessage(**message)
        for message in final_state.get(
            "reply_messages",
            [{"type": "text", "order": 1, "content": "我这边先记录到了，稍后继续帮您确认。"}],
        )
    ]

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


def collect_model_usage(trace: list[dict[str, Any]]) -> dict[str, Any]:
    calls: list[dict[str, Any]] = []
    summary = {
        "planner_tokens": 0,
        "reply_tokens": 0,
        "vision_tokens": 0,
        "other_tokens": 0,
        "total_tokens": 0,
    }
    for entry in trace or []:
        node = str(entry.get("node") or "")
        for call in entry.get("tool_calls", []) or []:
            if not isinstance(call, dict):
                continue
            usage = call.get("usage") if isinstance(call.get("usage"), dict) else {}
            total = int(usage.get("total_tokens") or usage.get("token_count") or 0)
            if total <= 0:
                continue
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
    return {"calls": calls, "summary": summary}
