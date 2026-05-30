import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.graph.builder import build_graph  # noqa: E402
from app.graph.state import AgentState  # noqa: E402
from app.services.coze_client import CozeClient  # noqa: E402
from app.services.trace_logger import TraceLogger  # noqa: E402


async def main() -> None:
    settings = get_settings()
    trace_logger = TraceLogger(settings)
    coze_client = CozeClient(settings)
    graph = build_graph(coze_client, trace_logger)

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
                "route_result": final_state.get("route_result"),
                "intents": final_state.get("intents"),
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
