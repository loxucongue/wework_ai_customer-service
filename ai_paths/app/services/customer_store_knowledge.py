from __future__ import annotations

from typing import Any

from app.services.customer_context_extractors import compact_request_context
from app.services.platform_agent_client import PlatformAgentClient
from app.services.store_snapshot_service import StoreSnapshotService


class CustomerStoreKnowledgeService:
    def __init__(
        self,
        platform_client: PlatformAgentClient | None = None,
        store_snapshot_service: StoreSnapshotService | None = None,
    ) -> None:
        self._platform_client = platform_client
        self._store_snapshot_service = store_snapshot_service

    def load(self, *, request_context: dict[str, Any], customer_context: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._platform_client or not self._platform_client.available:
            return {"source": "platform_agent_unavailable", "stores": [], "appointment_extra_stores": []}

        ctx = dict(request_context or {})
        customer = (customer_context or {}).get("customer") if isinstance(customer_context, dict) else {}
        customer = customer if isinstance(customer, dict) else {}
        identity = (customer_context or {}).get("identity") if isinstance(customer_context, dict) else {}
        identity = identity if isinstance(identity, dict) else {}

        platform_customer_id = str(
            customer.get("id")
            or identity.get("platform_customer_id")
            or (customer_context or {}).get("customer_id")
            or ctx.get("platform_customer_id")
            or ""
        ).strip()
        customer_add_wechat_id = str(customer.get("customer_add_wechat_id") or ctx.get("customer_add_wechat_id") or "").strip()

        customer_info_error = ""
        if not (platform_customer_id and customer_add_wechat_id) and ctx.get("external_userid"):
            try:
                info = self._platform_client.get_customer_info(
                    user_id=ctx.get("user_id"),
                    corp_id=ctx.get("corp_id"),
                    wechat=ctx.get("wechat"),
                    external_userid=ctx.get("external_userid"),
                )
                platform_customer_id = str(info.get("id") or platform_customer_id).strip()
                customer_add_wechat_id = str(info.get("customer_add_wechat_id") or customer_add_wechat_id).strip()
            except Exception as exc:
                customer_info_error = f"{type(exc).__name__}: {exc}"

        if not platform_customer_id or not customer_add_wechat_id:
            return {
                "source": "missing_customer_store_scope",
                "customer_id": platform_customer_id,
                "customer_add_wechat_id": customer_add_wechat_id,
                "stores": [],
                "appointment_extra_stores": [],
                "error": customer_info_error,
            }

        scoped_context = dict(ctx)
        scoped_context["input_customer_id"] = ctx.get("customer_id")
        scoped_context["platform_customer_id"] = platform_customer_id
        scoped_context["customer_id"] = platform_customer_id
        scoped_context["customer_add_wechat_id"] = customer_add_wechat_id
        try:
            rows = self._platform_client.list_stores(
                customer_id=platform_customer_id,
                customer_add_wechat_id=customer_add_wechat_id,
                request_context=scoped_context,
            )
        except Exception as exc:
            return {
                "source": "platform_agent.store_index_error",
                "customer_id": platform_customer_id,
                "customer_add_wechat_id": customer_add_wechat_id,
                "stores": [],
                "appointment_extra_stores": [],
                "error": f"{type(exc).__name__}: {exc}",
                "request_context": compact_request_context(scoped_context),
            }
        if self._store_snapshot_service:
            scoped = self._store_snapshot_service.stores_for_scope(rows, request_context=scoped_context)
            stores = scoped["stores"]
            grouped_by_region = scoped["grouped_by_region"]
            missing_snapshot_store_ids = scoped["missing_snapshot_store_ids"]
            snapshot_meta = {
                "snapshot_generated_at": scoped.get("snapshot_generated_at"),
                "snapshot_store_count": scoped.get("snapshot_store_count"),
                "snapshot_source": scoped.get("snapshot_source"),
                "snapshot_refresh_error": scoped.get("snapshot_refresh_error"),
            }
        else:
            stores = [self._store_knowledge_from_row(row, source="scope_row_fallback") for row in rows]
            stores = [item for item in stores if item.get("store_id") and item.get("store_name")]
            grouped_by_region = {}
            missing_snapshot_store_ids = [str(item.get("store_id") or "") for item in stores if item.get("store_id")]
            snapshot_meta = {"snapshot_source": "service_unavailable"}
        scoped_ids = {str(item.get("store_id") or "") for item in stores}
        extra_ids = self._appointment_store_ids(customer_context or {}, request_context)
        extra_ids = [store_id for store_id in extra_ids if store_id and store_id not in scoped_ids]
        appointment_extra_stores = [self._appointment_extra_store(store_id, scoped_context) for store_id in extra_ids]

        return {
            "source": "platform_agent.store_index+store_snapshot",
            "identity": {
                "input_customer_id": ctx.get("customer_id"),
                "platform_customer_id": platform_customer_id,
                "customer_add_wechat_id": customer_add_wechat_id,
                "external_userid": ctx.get("external_userid"),
            },
            "customer_id": platform_customer_id,
            "customer_add_wechat_id": customer_add_wechat_id,
            "store_count": len(stores),
            "stores": stores,
            "grouped_by_region": grouped_by_region,
            "missing_snapshot_store_ids": missing_snapshot_store_ids,
            **snapshot_meta,
            "appointment_extra_stores": [item for item in appointment_extra_stores if item.get("store_name")],
            "request_context": compact_request_context(scoped_context),
        }

    def _appointment_extra_store(self, store_id: str, request_context: dict[str, Any]) -> dict[str, Any]:
        if self._store_snapshot_service:
            cached = self._store_snapshot_service.store_by_id(store_id, request_context=request_context)
            if cached.get("store_name"):
                cached["source"] = "appointment_extra_snapshot"
                return cached
        return self._store_knowledge_from_row({"id": store_id}, source="appointment_extra_missing_snapshot")

    @staticmethod
    def _store_knowledge_from_row(row: dict[str, Any], *, source: str) -> dict[str, Any]:
        store_id = str(row.get("id") or row.get("store_id") or "").strip()
        begin = str(row.get("business_hours_begin") or "").strip()
        end = str(row.get("business_hours_end") or "").strip()
        return {
            "store_id": store_id,
            "store_name": str(row.get("name") or "").strip(),
            "province": "",
            "city": "",
            "district": "",
            "store_address": str(row.get("tencent_address") or row.get("address") or "").strip(),
            "business_hours": f"{begin}-{end}".strip("-"),
            "is_open": bool(begin and end),
            "map_url": str(row.get("tencent_map_store") or row.get("map_store") or "").strip(),
            "parking_name": "",
            "parking_address": "",
            "parking_url": "",
            "guidance_video": row.get("guidance_video") or [],
            "source": source,
            "detail_source": "scope_row_fallback",
        }

    @staticmethod
    def _appointment_store_ids(customer_context: dict[str, Any], request_context: dict[str, Any]) -> list[str]:
        ids: list[str] = []
        for key in ("confirmed_store_id", "store_id"):
            value = request_context.get(key)
            if value not in (None, ""):
                ids.append(str(value))
        appointment = customer_context.get("appointment") if isinstance(customer_context, dict) else {}
        if isinstance(appointment, dict) and appointment.get("store_id") not in (None, ""):
            ids.append(str(appointment.get("store_id")))
        orders = customer_context.get("orders") if isinstance(customer_context, dict) else []
        for order in orders if isinstance(orders, list) else []:
            if isinstance(order, dict) and order.get("store_id") not in (None, ""):
                ids.append(str(order.get("store_id")))
        return list(dict.fromkeys(ids))[:8]
