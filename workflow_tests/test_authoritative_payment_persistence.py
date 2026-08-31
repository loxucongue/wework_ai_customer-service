from __future__ import annotations

from pathlib import Path

from app.chat_runtime import _record_authoritative_payment_fact
from app.config import Settings
from app.services.memory_store import CustomerMemoryStore


def _memory_store(tmp_path: Path) -> CustomerMemoryStore:
    return CustomerMemoryStore(Settings(_env_file=None, memory_dir=tmp_path))


def test_platform_unknown_transfer_fact_persists_for_next_turn(tmp_path: Path) -> None:
    store = _memory_store(tmp_path)
    state = {
        "request_id": "request-transfer-1",
        "image_info": {
            "image_type": "payment_proof",
            "payment_result": "success",
            "payment_amount": 10,
            "source": "platform.unknown_message_transfer",
        },
        "trace": [],
    }

    _record_authoritative_payment_fact(store, state, customer_id="sim_contact_1")

    memory = store.load("sim_contact_1")
    assert memory["basic_info"]["deposit_state"]["status"] == "paid_by_platform_transfer_event"
    assert memory["basic_info"]["deposit_state"]["source"] == "platform.unknown_message_transfer"
    assert [item["event_type"] for item in memory["history_events"]] == ["deposit_payment_confirmed"]
    assert state["authoritative_payment_memory_record"]["status"] == "recorded"


def test_successful_payment_screenshot_persists_as_authoritative_fact(tmp_path: Path) -> None:
    store = _memory_store(tmp_path)
    state = {
        "request_id": "request-screenshot-1",
        "image_info": {
            "image_type": "payment_proof",
            "payment_result": "success",
            "payment_amount": 20,
            "source": "vision.payment_proof",
        },
        "trace": [],
    }

    _record_authoritative_payment_fact(store, state, customer_id="sim_contact_2")

    payment = store.load("sim_contact_2")["basic_info"]["deposit_state"]
    assert payment["status"] == "paid_by_screenshot"
    assert payment["amount"] == 20


def test_non_payment_or_unverified_payment_is_not_persisted(tmp_path: Path) -> None:
    store = _memory_store(tmp_path)
    state = {
        "request_id": "request-text-claim-1",
        "normalized_content": "我已经转好了",
        "image_info": {},
        "trace": [],
    }

    _record_authoritative_payment_fact(store, state, customer_id="sim_contact_3")

    memory = store.load("sim_contact_3")
    assert memory["basic_info"] == {}
    assert memory["history_events"] == []
    assert state["authoritative_payment_memory_record"] == {
        "status": "skipped",
        "deposit_state": "",
        "source": "",
        "reason": "no_current_authoritative_payment_fact",
    }
