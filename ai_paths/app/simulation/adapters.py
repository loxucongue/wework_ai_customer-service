from __future__ import annotations

import asyncio
import json
import httpx
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from app.schemas import CozeKbItem, CozeKbResult
from app.services.model_client import ModelClient


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SimulationWorld:
    scenario_id: str
    identity: dict[str, Any]
    customer: dict[str, Any] = field(default_factory=dict)
    orders: list[dict[str, Any]] = field(default_factory=list)
    stores: list[dict[str, Any]] = field(default_factory=list)
    case_facts: list[dict[str, Any]] = field(default_factory=list)
    geocodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    distances: dict[str, dict[str, Any]] = field(default_factory=dict)
    available_times: dict[str, Any] = field(default_factory=dict)
    voice_transcripts: dict[str, str] = field(default_factory=dict)
    conversation: list[dict[str, Any]] = field(default_factory=list)
    outbox: list[dict[str, Any]] = field(default_factory=list)
    external_writes: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    faults: dict[str, Any] = field(default_factory=dict)

    def apply_facts(self, patch: dict[str, Any]) -> None:
        for key in (
            "customer",
            "orders",
            "stores",
            "case_facts",
            "geocodes",
            "distances",
            "available_times",
            "voice_transcripts",
            "faults",
        ):
            if key in patch:
                setattr(self, key, deepcopy(patch[key]))

    def take_fault(self, key: str) -> Any:
        fault = self.faults.get(key)
        if isinstance(fault, list):
            if not fault:
                return None
            return fault.pop(0)
        if fault is not None:
            self.faults.pop(key, None)
        return fault

    def append_customer_message(self, content: str, *, msgtype: str = "text", created_at: str = "") -> None:
        self.conversation.append(
            {
                "direction": "customer",
                "role": "user",
                "content": content,
                "msgtype": msgtype,
                "created_at": created_at or _now_iso(),
            }
        )

    def append_assistant_messages(self, messages: list[dict[str, Any]], *, source: str) -> None:
        for message in messages:
            content = _visible_content(message)
            self.conversation.append(
                {
                    "direction": "staff",
                    "role": "assistant",
                    "content": content,
                    "msgtype": str(message.get("type") or "text"),
                    "created_at": _now_iso(),
                    "reply_message": deepcopy(message),
                    "source": source,
                }
            )


class SimulationOutreachClient:
    simulation_adapter = True

    def __init__(self, world: SimulationWorld) -> None:
        self.world = world

    @property
    def available(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None

    async def fetch_conversation(self, **kwargs: Any) -> dict[str, Any]:
        self.world.tool_calls.append({"name": "conversation_fetch", "arguments": deepcopy(kwargs)})
        fault = self.world.faults.get("conversation_fetch")
        if fault:
            return _fault_result(fault)
        limit = max(1, min(int(kwargs.get("limit") or 30), 50))
        messages = deepcopy(self.world.conversation[-limit:])
        return {"status": "ok", "messages": messages, "message_count": len(messages), "simulation": True}

    async def send_reply_messages(self, **kwargs: Any) -> dict[str, Any]:
        identity = {
            "customer_id": kwargs.get("fallback_customer_id"),
            "external_userid": kwargs.get("fallback_external_userid"),
            "corp_id": kwargs.get("fallback_corp_id"),
            "wechat": kwargs.get("fallback_wechat"),
        }
        _assert_sim_prefixes(identity)
        messages = deepcopy(kwargs.get("reply_messages") or [])
        item = {
            "request_id": str(kwargs.get("request_id") or ""),
            "identity": identity,
            "reply_messages": messages,
            "created_at": _now_iso(),
            "transport": "simulation_outbox",
        }
        self.world.outbox.append(item)
        self.world.append_assistant_messages(messages, source="simulation_outbox")
        return {
            "status": "sent",
            "simulation": True,
            "send_payload": item,
            "response": {"code": 0, "msg": "captured_by_simulation_outbox"},
        }


class SimulationModelClient(ModelClient):
    """Inject reproducible provider failures, then delegate to the real model client."""

    simulation_adapter = True

    def __init__(self, settings: Any, world: SimulationWorld) -> None:
        super().__init__(settings)
        self.world = world

    async def _post_chat(
        self,
        payload: dict[str, Any],
        *,
        tier: str,
        fallback_index: int,
        errors: list[str],
    ) -> dict[str, Any]:
        fault = self.world.take_fault(f"model:{tier}")
        if fault is None:
            fault = self.world.take_fault("model:any")
        mode = str((fault or {}).get("mode") if isinstance(fault, dict) else fault or "").strip().lower()
        if mode in {"timeout", "read_timeout"}:
            raise httpx.ReadTimeout("simulation injected model timeout")
        if mode in {"502", "http_502"}:
            raise RuntimeError("Model HTTP 502: simulation injected provider failure")
        if mode in {"429", "http_429"}:
            raise RuntimeError("Model HTTP 429: simulation injected provider throttle")
        if mode in {"malformed_json", "json_malformed"}:
            return {
                "choices": [{"message": {"content": "{malformed json"}}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }
        if mode in {"json_protocol", "json_protocol_502"}:
            raise RuntimeError(
                "Model HTTP 502: Response input messages must contain the word 'json' "
                "to use text.format of type json_object"
            )
        return await super()._post_chat(
            payload,
            tier=tier,  # type: ignore[arg-type]
            fallback_index=fallback_index,
            errors=errors,
        )


class SimulationVoiceTranscriptionClient:
    simulation_adapter = True
    provider_name = "simulation_voice"
    resource_id = "simulation_voice_transcript"

    def __init__(self, world: SimulationWorld) -> None:
        self.world = world

    async def transcribe(self, audio_url: str, *, uid: str = "") -> dict[str, Any]:
        self.world.tool_calls.append(
            {
                "name": "voice_transcription",
                "arguments": {"audio_url": audio_url, "uid": uid},
            }
        )
        fault = self.world.take_fault("voice_transcription")
        if fault:
            raise RuntimeError(str((fault or {}).get("error") if isinstance(fault, dict) else fault))
        text = str(self.world.voice_transcripts.get(audio_url) or "").strip()
        if not text:
            return {"status": "failed", "text": "", "error": "simulation_voice_transcript_missing"}
        return {"status": "success", "text": text, "provider": self.provider_name}

    async def aclose(self) -> None:
        return None


class SimulationCustomerContextService:
    simulation_adapter = True

    def __init__(self, world: SimulationWorld) -> None:
        self.world = world

    def load_identity(self, *, customer_id: str, request_context: dict[str, Any]) -> dict[str, Any]:
        return {
            "input_customer_id": customer_id,
            "platform_customer_id": customer_id,
            "customer_add_wechat_id": "sim_relation_1",
            "external_userid": request_context.get("external_userid"),
            "customer_info": {"id": customer_id, "customer_add_wechat_id": "sim_relation_1", **self.world.customer},
            "request_context": {
                **request_context,
                "platform_customer_id": customer_id,
                "customer_add_wechat_id": "sim_relation_1",
            },
            "cache_hit": False,
            "error": "",
        }

    def load_with_identity(
        self,
        *,
        customer_id: str,
        memory: dict[str, Any],
        request_context: dict[str, Any],
        identity: dict[str, Any],
    ) -> dict[str, Any]:
        return self.load(customer_id=customer_id, memory=memory, request_context=request_context)

    def load(self, *, customer_id: str, memory: dict[str, Any], request_context: dict[str, Any]) -> dict[str, Any]:
        return {
            "customer_id": customer_id,
            "platform_customer_id": customer_id,
            "customer_add_wechat_id": "sim_relation_1",
            "source": "platform_agent",
            "identity": {
                "input_customer_id": customer_id,
                "platform_customer_id": customer_id,
                "customer_add_wechat_id": "sim_relation_1",
                "external_userid": request_context.get("external_userid"),
            },
            "customer": {"id": customer_id, "customer_add_wechat_id": "sim_relation_1", **deepcopy(self.world.customer)},
            "orders": deepcopy(self.world.orders),
            "appointment": deepcopy(request_context.get("appointment") or {}),
            "request_context": deepcopy(request_context),
        }


class SimulationStoreKnowledgeService:
    simulation_adapter = True

    def __init__(self, world: SimulationWorld) -> None:
        self.world = world

    def load(self, **kwargs: Any) -> dict[str, Any]:
        stores = deepcopy(self.world.stores)
        return {
            "source": "simulation_store_scope",
            "customer_id": self.world.identity["customer_id"],
            "customer_add_wechat_id": "sim_relation_1",
            "store_count": len(stores),
            "stores": stores,
            "grouped_by_region": _group_stores(stores),
            "appointment_extra_stores": [],
            "simulation": True,
        }

    def with_appointment_extra_stores(self, *, customer_store_knowledge: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        return deepcopy(customer_store_knowledge)


class SimulationStoreService:
    simulation_adapter = True

    def __init__(self, world: SimulationWorld) -> None:
        self.world = world

    def available_time(self, *, store_id: str, date: str, customer_context: dict[str, Any] | None = None) -> dict[str, Any]:
        self.world.tool_calls.append({"name": "available_time", "arguments": {"store_id": store_id, "date": date}})
        fault = self.world.faults.get("available_time")
        if fault:
            return {"source": "simulation.available_time", "slots": {}, **_fault_result(fault)}
        key = f"{store_id}|{date}"
        slots = deepcopy(self.world.available_times.get(key) or self.world.available_times.get(date) or {})
        return {"source": "simulation.available_time", "date": date, "store_id": store_id, "slots": slots}


class SimulationPlatformAgentClient:
    simulation_adapter = True

    def __init__(self, world: SimulationWorld) -> None:
        self.world = world

    @property
    def available(self) -> bool:
        return True

    def close(self) -> None:
        return None

    def get_customer_info(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "id": self.world.identity["customer_id"],
            "customer_add_wechat_id": "sim_relation_1",
            **deepcopy(self.world.customer),
        }

    def list_orders(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.world.tool_calls.append({"name": "order_index", "arguments": deepcopy(kwargs)})
        return deepcopy(self.world.orders)

    def list_stores(self, **kwargs: Any) -> list[dict[str, Any]]:
        return deepcopy(self.world.stores)

    def list_store_options(self, **kwargs: Any) -> list[dict[str, Any]]:
        return deepcopy(self.world.stores)

    def list_categories(self, **kwargs: Any) -> list[dict[str, Any]]:
        return [{"id": "S10N", "name": "淡斑活动"}]

    def store_info(self, store_id: int | str, **kwargs: Any) -> dict[str, Any]:
        return deepcopy(next((item for item in self.world.stores if str(item.get("store_id") or item.get("id")) == str(store_id)), {}))

    def available_time(self, *, store_id: int | str, date: str, **kwargs: Any) -> dict[str, Any]:
        key = f"{store_id}|{date}"
        return deepcopy(self.world.available_times.get(key) or self.world.available_times.get(date) or {})

    def check_customer(self, **kwargs: Any) -> dict[str, Any]:
        return {"status": "ok", "customer_id": self.world.identity["customer_id"]}

    def category_prepay(self, **kwargs: Any) -> dict[str, Any]:
        return {"prepay": 10}

    def my_collection(self, **kwargs: Any) -> list[dict[str, Any]]:
        return [{"id": "sim_collection", "name": "仿真收款"}]

    def prepay_order(self, **kwargs: Any) -> dict[str, Any]:
        return self._write("prepay_order", kwargs, {"status": "simulated"})

    def create_work_order(self, **kwargs: Any) -> dict[str, Any]:
        order_id = f"sim_order_{len(self.world.orders) + 1}"
        result = {"id": order_id, "order_id": order_id, "status": "created"}
        self.world.orders.append(
            {
                "id": order_id,
                "order_id": order_id,
                "store_id": str(kwargs.get("store_id") or ""),
                "prepay_required": kwargs.get("prepay") or 10,
                "prepay_paid": 0,
                "status": "pending",
            }
        )
        return self._write("create_work_order", kwargs, result)

    def modify_work_order(self, **kwargs: Any) -> dict[str, Any]:
        return self._write("modify_work_order", kwargs, {"status": "modified"})

    def create_order_plan(self, **kwargs: Any) -> dict[str, Any]:
        return self._write("create_order_plan", kwargs, {"status": "created", "id": "sim_plan_1"})

    def change_plan_time(self, **kwargs: Any) -> dict[str, Any]:
        return self._write("change_plan_time", kwargs, {"status": "changed"})

    def cancel_plan(self, **kwargs: Any) -> dict[str, Any]:
        return self._write("cancel_plan", kwargs, {"status": "cancelled"})

    def add_customer_mobile(self, **kwargs: Any) -> dict[str, Any]:
        return self._write("add_customer_mobile", kwargs, {"status": "saved"})

    def _write(self, name: str, arguments: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        self.world.external_writes.append(
            {
                "name": name,
                "arguments": deepcopy(arguments),
                "result": deepcopy(result),
                "transport": "simulation_only",
            }
        )
        self.world.tool_calls.append({"name": name, "arguments": deepcopy(arguments)})
        fault = self.world.faults.get(name)
        if fault:
            return _fault_result(fault)
        return deepcopy(result)


class SimulationCozeClient:
    simulation_adapter = True

    def __init__(self, world: SimulationWorld, *, geocode_workflow_id: str, distance_workflow_id: str) -> None:
        self.world = world
        self.settings = SimpleNamespace(
            geocode_workflow_id=geocode_workflow_id,
            distance_workflow_id=distance_workflow_id,
            kb_workflow_id="sim_kb",
        )

    async def aclose(self) -> None:
        return None

    async def search_kb(self, kb_name: str, query: str) -> CozeKbResult:
        self.world.tool_calls.append({"name": "kb_search", "arguments": {"kb_name": kb_name, "query": query}})
        fault = self.world.faults.get("kb_search")
        if fault:
            raise RuntimeError(str(fault))
        items = [
            CozeKbItem(
                content=json.dumps(item, ensure_ascii=False),
                document_id=str(item.get("case_id") or item.get("document_id") or f"sim_case_{index}"),
            )
            for index, item in enumerate(self.world.case_facts, start=1)
        ]
        return CozeKbResult(kb_name=kb_name, items=items, raw={"simulation": True})

    async def run_workflow(self, workflow_id: str, parameters: dict[str, Any]) -> dict[str, Any]:
        self.world.tool_calls.append({"name": "coze_workflow", "workflow_id": workflow_id, "arguments": deepcopy(parameters)})
        if workflow_id == self.settings.geocode_workflow_id:
            address = str(parameters.get("address") or "")
            value = deepcopy(_fixture_geocode(self.world.geocodes, address) or _infer_geocode(address))
            return {"data": value}
        if workflow_id == self.settings.distance_workflow_id:
            key = f"{parameters.get('origin')}|{parameters.get('destination')}"
            value = deepcopy(self.world.distances.get(key) or {"distance": 1000, "duration": 600})
            return {"data": {"output": value}}
        return {"data": {"output": []}, "simulation": True}


def _fixture_geocode(geocodes: dict[str, dict[str, Any]], address: str) -> dict[str, Any]:
    exact = geocodes.get(address)
    if exact:
        return exact

    text = str(address or "").strip()
    matches = [
        (key, value)
        for key, value in geocodes.items()
        if key and (key in text or text in key)
    ]
    if not matches:
        return {}

    longest = max(len(key) for key, _ in matches)
    best = [(key, value) for key, value in matches if len(key) == longest]
    if len(best) != 1:
        return {}
    return best[0][1]


def _group_stores(stores: list[dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for store in stores:
        province = str(store.get("province") or "未知省")
        city = str(store.get("city") or "未知市")
        district = str(store.get("district") or "未知区")
        output.setdefault(province, {}).setdefault(city, {}).setdefault(district, []).append(deepcopy(store))
    return output


def _infer_geocode(address: str) -> dict[str, Any]:
    text = str(address or "")
    mapping = {
        "广州": ("广东省", "广州市", ""),
        "荆州": ("湖北省", "荆州市", ""),
        "洪湖": ("湖北省", "荆州市", "洪湖市"),
        "武平": ("福建省", "龙岩市", "武平县"),
        "甲良": ("贵州省", "黔南布依族苗族自治州", "荔波县"),
        "乌林": ("湖北省", "荆州市", "洪湖市"),
        "东坑": ("", "", ""),
        "双流": ("四川省", "成都市", "双流区"),
        "厦门": ("福建省", "厦门市", ""),
    }
    for token, region in mapping.items():
        if token in text:
            province, city, district = region
            return {
                "formatted_address": text,
                "province": province,
                "city": city,
                "district": district,
                "location": "114.000000,30.000000" if city else "",
            }
    return {"formatted_address": text, "location": ""}


def _fault_result(fault: Any) -> dict[str, Any]:
    if isinstance(fault, dict):
        return deepcopy(fault)
    return {"status": "failed", "error": str(fault)}


def _visible_content(message: dict[str, Any]) -> str:
    message_type = str(message.get("type") or "text")
    content = message.get("content")
    if isinstance(content, dict):
        if message_type == "text":
            return str(content.get("text") or content.get("content") or "")
        if message_type == "store_address":
            return "门店位置：" + str(content.get("store_id") or "")
        if message_type == "payment_collection":
            return "付款给：仿真收款 " + str(content.get("amount") or "")
        return f"[{message_type}]" + str(content.get("url") or "")
    return str(content or "")


def _assert_sim_prefixes(identity: dict[str, Any]) -> None:
    for key, value in identity.items():
        if key in {"customer_id", "external_userid", "corp_id", "wechat"} and not str(value or "").startswith("sim_"):
            raise RuntimeError(f"simulation outbox rejected non-simulation {key}: {value!r}")
