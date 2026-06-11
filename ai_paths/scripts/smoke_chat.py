import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.graph.graph_builder import build_graph  # noqa: E402
from app.graph.planner.runtime_plan import planner_public_route, planner_task_views  # noqa: E402
from app.graph.state import AgentState  # noqa: E402
from app.services.appointment_opening_service import AppointmentOpeningService  # noqa: E402
from app.services.coze_client import CozeClient  # noqa: E402
from app.services.customer_context import CustomerContextService  # noqa: E402
from app.services.memory_store import CustomerMemoryStore  # noqa: E402
from app.services.model_client import ModelClient  # noqa: E402
from app.services.platform_agent_client import PlatformAgentClient  # noqa: E402
from app.services.pricing_repository import LocalPricingRepository  # noqa: E402
from app.services.storage import AppRepository, SQLiteStore  # noqa: E402
from app.services.store_service import StoreService  # noqa: E402
from app.services.trace_logger import TraceLogger  # noqa: E402


async def main() -> None:
    settings = get_settings()
    trace_logger = TraceLogger(settings)
    coze_client = CozeClient(settings)
    model_client = ModelClient(settings)
    sqlite_store = SQLiteStore(settings)
    sqlite_store.initialize()
    repository = AppRepository(sqlite_store)
    memory_store = CustomerMemoryStore(settings, repository)
    pricing_repository = LocalPricingRepository(settings)
    platform_agent_client = PlatformAgentClient(settings)
    customer_context_service = CustomerContextService(platform_agent_client)
    store_service = StoreService(platform_agent_client)
    appointment_opening_service = AppointmentOpeningService(platform_agent_client)
    graph = build_graph(
        coze_client,
        trace_logger,
        model_client,
        memory_store,
        pricing_repository,
        customer_context_service,
        store_service,
        appointment_opening_service,
    )

    request_id = str(uuid4())
    state: AgentState = {
        "request_id": request_id,
        "customer_id": "local_test_customer",
        "corp_id": "local_test_corp",
        "content": "\u6211\u8138\u4e0a\u70b9\u72b6\u6591\u6bd4\u8f83\u660e\u663e\uff0c\u76ae\u79d2\u591a\u5c11\u94b1\uff0c\u4f60\u4eec\u6b63\u89c4\u5417",
        "conversation_history": [],
        "file_image": None,
        "trace": [],
        "errors": [],
    }

    final_state = await graph.ainvoke(state)
    log_path = trace_logger.write_run(final_state)
    print(
        json.dumps(
            {
                "request_id": request_id,
                "route_result": planner_public_route(final_state),
                "planner_tasks": planner_task_views(final_state),
                "reply_messages": final_state.get("reply_messages"),
                "tool_result_keys": list((final_state.get("tool_results") or {}).keys()),
                "trace_count": len(final_state.get("trace", [])),
                "log_path": str(log_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
