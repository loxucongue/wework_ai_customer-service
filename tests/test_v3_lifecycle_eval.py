from __future__ import annotations

import json
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ai_paths"))

from app.chat_runtime import ChatRuntime, _chat_response_from_generation  # noqa: E402
from app.config import Settings  # noqa: E402
from app.schemas import ChatRequest  # noqa: E402
from app.services.memory_store import CustomerMemoryStore  # noqa: E402
from app.services.storage import AppRepository, SQLiteStore  # noqa: E402
from app.services.trace_logger import TraceLogger  # noqa: E402
from app.services.v3_reply_finalization_service import V3ReplyFinalizationService  # noqa: E402
from app.services.v3_reply_recovery import v3_generation_key  # noqa: E402
from app.services.workflow_compat import workflow_response_from_chat  # noqa: E402
from scripts.evaluate_v3_full_chain_deepseek import TimedGraph, ephemeral_counts  # noqa: E402


class _ReplyGraph:
    async def ainvoke(self, state: dict[str, object]) -> dict[str, object]:
        state["reply_messages"] = [
            {"type": "text", "order": 1, "content": "可以停车的，我帮您预约一下，您工作日还是周末方便？"}
        ]
        state["reply_source"] = "main_model"
        state["decision_status"] = "ok"
        state["policy_decision"] = {
            "primary_task": {"type": "answer_and_advance"},
            "realtime_intent": {"type": "fact_inquiry"},
            "emotion_decision": {"label": "curious", "flow_action": "continue"},
            "closing_decision": {"action": "enter", "customer_state": "ready"},
        }
        state["tool_results"] = {"follow_knowledge": {"status": "ok"}}
        state["reply_knowledge_use"] = {
            "sequence_id": "45",
            "step_id": "4501",
            "script_id": "310",
            "selected_script_ids": ["310"],
        }
        state["sales_recall"] = {
            "sequence_candidates": [
                {
                    "sequence_id": "45",
                    "sequence_name": "距离顾虑",
                    "checkpoint_code": "distance",
                    "checkpoint_name": "距离",
                    "steps": [
                        {
                            "step_id": "4501",
                            "sort_order": 1,
                            "action_code": "act001",
                            "action_name": "价值说明",
                        }
                    ],
                }
            ],
            "candidates": [
                {
                    "script_id": "310",
                    "source_id": "310",
                    "script_name": "距离价值说明",
                    "reference_text": "不少客户会专程过来，主要看重技术和效果。",
                    "checkpoint_code": "distance",
                    "checkpoint_name": "距离",
                    "action_code": "act001",
                    "action_name": "价值说明",
                }
            ],
        }
        return state


class _AiStatusClient:
    available = True

    async def conversation_status(self, **_kwargs: object) -> dict[str, object]:
        return {"data": {"takeover": {"mode": "ai", "is_human": False}}}


def test_ephemeral_runtime_executes_persistence_and_response_tail(tmp_path: Path) -> None:
    settings = Settings().model_copy(
        update={
            "service_role": "reply",
            "aics_storage_backend": "sqlite",
            "db_path": tmp_path / "state.db",
            "memory_dir": tmp_path / "memory",
            "trace_log_dir": tmp_path / "trace",
            "background_workers_enabled": False,
        }
    )
    store = SQLiteStore(settings)
    store.initialize()
    repository = AppRepository(store)
    memory = CustomerMemoryStore(settings, repository)
    graph = TimedGraph(_ReplyGraph())
    runtime = ChatRuntime(
        full_graph=graph,
        commit_graph=None,
        trace_logger=TraceLogger(settings),
        repository=repository,
        memory_store=memory,
        outreach_system_client=_AiStatusClient(),  # type: ignore[arg-type]
        settings=settings,
    )
    request = ChatRequest(
        content="停车方便吗",
        customer_id="real-shaped-customer",
        corp_id="real-shaped-corp",
        wechat="sl8003",
        external_userid="external-real-shaped",
        request_context={
            "interface_version": "v3",
            "source_protocol": "ephemeral-evaluation",
            "msgid": "lifecycle-replay-1",
        },
    )

    response = asyncio.run(runtime.run_platform_reply(request))
    public_body = workflow_response_from_chat(response)
    generation_key = v3_generation_key(
        corp_id=request.corp_id,
        wechat=str(request.wechat or ""),
        external_userid=str(request.external_userid or ""),
        msgid="lifecycle-replay-1",
    )
    durable = repository.get_v3_generation_result(generation_key=generation_key)
    replay_body = workflow_response_from_chat(_chat_response_from_generation(durable))
    assert replay_body["data"]["has_knowledge"] == public_body["data"]["has_knowledge"] == "true"
    assert replay_body["data"]["followSequence"] == public_body["data"]["followSequence"]
    assert replay_body["data"]["followScript"] == public_body["data"]["followScript"]
    repository.update_run_http_response(request_id=response.request_id, response_body=public_body)
    json.dumps(public_body, ensure_ascii=False)
    pending = repository.get_run(response.request_id)["run"]["output_snapshot"]
    assert pending["post_reply_finalization"]["status"] == "pending"
    finalizer = V3ReplyFinalizationService(
        repository=repository,
        trace_logger=TraceLogger(settings),
        service_rule_data_service=None,
        outreach_service=None,
    )
    assert finalizer.process_batch() == {"claimed": 1, "completed": 1, "failed": 0}
    counts = ephemeral_counts(store)

    assert response.request_id in graph.final_by_request
    assert counts["runs"] == 1
    assert counts["messages"] == 2
    assert counts["v3_strategy_usage_events"] == 1
    run = repository.get_run(response.request_id)["run"]
    assert run["output_snapshot"]["runtime_status"] == "completed"
    assert run["output_snapshot"]["post_reply_finalization"]["status"] == "completed"
    assert public_body["data"]["reply_messages"][0]["type"] == "text"
    assert graph.timing_by_request[response.request_id]["graph_duration_ms"] >= 0
