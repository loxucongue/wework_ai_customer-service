from __future__ import annotations

from app.graph.nodes.layer_nodes import (
    _enrich_customer_stores,
    _load_customer_context_with_identity,
    _load_customer_identity,
    _load_customer_stores,
    _load_memory,
)


def _state(snapshot: dict) -> dict:
    return {
        "test_isolated": True,
        "customer_id": "sim_customer",
        "request_context": {
            "test_isolated": True,
            "isolated_replay_snapshot": snapshot,
        },
    }


def test_isolated_replay_snapshot_restores_structured_facts_without_services() -> None:
    snapshot = {
        "saved_memory": {
            "basic_info": {"city": "德阳市"},
            "history_events": [{"event_type": "store_address_sent"}],
        },
        "identity": {"platform_customer_id": "source_customer"},
        "customer_context": {
            "source": "replay_snapshot",
            "appointment": {"has_active": False, "status": "none"},
            "orders": [],
        },
        "customer_store_knowledge": {
            "source": "replay_snapshot",
            "stores": [{"id": "282", "name": "德阳旌阳店"}],
        },
    }
    state = _state(snapshot)
    request_context = state["request_context"]

    memory = _load_memory(None, state)
    identity = _load_customer_identity(None, state, request_context)
    context = _load_customer_context_with_identity(
        None,
        state,
        memory["saved_memory"],
        request_context,
        identity,
    )
    stores = _load_customer_stores(None, request_context, context["customer_context"], identity)

    assert memory["customer_basic_info"] == {"city": "德阳市"}
    assert memory["history_events"] == [{"event_type": "store_address_sent"}]
    assert identity["platform_customer_id"] == "source_customer"
    assert context["appointment_cache"] == {"has_active": False, "status": "none"}
    assert stores["stores"] == [{"id": "282", "name": "德阳旌阳店"}]
    assert _enrich_customer_stores(None, stores, request_context, context["customer_context"]) == stores


def test_replay_snapshot_is_ignored_without_isolated_boundary() -> None:
    snapshot = {"saved_memory": {"basic_info": {"city": "不应读取"}}}
    state = _state(snapshot)
    state["test_isolated"] = False
    state["request_context"]["test_isolated"] = False

    result = _load_memory(None, state)

    assert result["customer_basic_info"] == {}
    assert "replay_snapshot_used" not in result
