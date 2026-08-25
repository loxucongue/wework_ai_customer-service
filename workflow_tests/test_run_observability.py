from __future__ import annotations

from app.services.run_observability import build_run_observability, trace_wall_duration_ms


def test_wall_duration_uses_critical_path_instead_of_summing_parallel_nodes() -> None:
    traces = [
        {
            "node_name": "content_gate",
            "created_at": "2026-08-25T10:00:00+00:00",
            "duration_ms": 5000,
        },
        {
            "node_name": "tool_planner",
            "created_at": "2026-08-25T10:00:00+00:00",
            "duration_ms": 7000,
        },
        {
            "node_name": "reply_synthesizer",
            "created_at": "2026-08-25T10:00:07+00:00",
            "duration_ms": 3000,
        },
    ]

    assert trace_wall_duration_ms(traces) == 10000


def test_observability_view_exposes_nodes_models_final_messages_and_delivery() -> None:
    detail = {
        "run": {
            "request_id": "request-1",
            "duration_ms": 15000,
            "created_at": "2026-08-25T10:00:12+00:00",
            "error": "",
            "input_snapshot": {
                "content": "多少钱",
                "request_context": {
                    "interface_version": "v3",
                    "reply_chain_mode": "model_led_sales_brain_v3",
                    "msgtype": "text",
                },
            },
            "output_snapshot": {
                "http_response_reply_messages": [
                    {"type": "text", "content": "活动价是268元。"}
                ],
                "warnings": [],
            },
        },
        "node_traces": [
            {
                "id": "trace-1",
                "node_name": "reply_synthesizer",
                "created_at": "2026-08-25T10:00:00+00:00",
                "duration_ms": 4000,
                "input_snapshot": {"content": "多少钱"},
                "output_snapshot": {
                    "reply_messages": [{"type": "text", "content": "活动价是268元。"}]
                },
                "tool_calls": [
                    {
                        "name": "reply_model",
                        "input": {"messages": [{"role": "user", "content": "多少钱"}]},
                        "raw_json_output": {"reply_messages": []},
                        "usage": {
                            "model": "gpt-5.4",
                            "duration_ms": 3500,
                            "total_tokens": 240,
                            "attempts": 1,
                        },
                    }
                ],
                "error": "",
            }
        ],
    }
    dispatches = [
        {
            "id": "dispatch-1",
            "source_channel": "async_reply",
            "source_kind": "ai_async_reply",
            "status": "send_succeeded",
            "expected_count": 1,
            "succeeded_count": 1,
            "failed_count": 0,
            "items": [
                {
                    "message_index": 0,
                    "message_type": "text",
                    "status": "send_succeeded",
                }
            ],
        }
    ]

    result = build_run_observability(detail, dispatches=dispatches)

    assert result["summary"]["status"] == "delivered"
    assert result["summary"]["interface_version"] == "v3"
    assert result["summary"]["model_call_count"] == 1
    assert result["summary"]["final_messages"][0]["content"] == "活动价是268元。"
    assert result["nodes"][0]["node_kind"] == "reply"
    assert result["nodes"][0]["model_calls"][0]["model"] == "gpt-5.4"
    assert result["delivery"]["status"] == "send_succeeded"


def test_observability_marks_neutral_fallback_as_warning_state() -> None:
    detail = {
        "run": {
            "request_id": "request-fallback",
            "duration_ms": 1000,
            "error": "",
            "input_snapshot": {"content": "你好", "request_context": {}},
            "output_snapshot": {
                "reply_messages": [{"type": "text", "content": "您稍等一下"}]
            },
        },
        "node_traces": [],
    }

    result = build_run_observability(detail)

    assert result["summary"]["status"] == "fallback"
    assert result["summary"]["fallback_detected"] is True


def test_fallback_status_is_not_hidden_by_pending_delivery_callback() -> None:
    detail = {
        "run": {
            "request_id": "request-fallback-pending",
            "duration_ms": 1000,
            "error": "",
            "input_snapshot": {"content": "你好", "request_context": {}},
            "output_snapshot": {
                "reply_messages": [{"type": "text", "content": "您稍等一下"}]
            },
        },
        "node_traces": [],
    }
    dispatches = [
        {
            "id": "dispatch-pending",
            "status": "platform_accepted",
            "expected_count": 1,
            "succeeded_count": 0,
            "failed_count": 0,
            "items": [],
        }
    ]

    result = build_run_observability(detail, dispatches=dispatches)

    assert result["summary"]["status"] == "fallback"
    assert result["delivery"]["status"] == "pending"
