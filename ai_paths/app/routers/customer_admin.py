from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.config import Settings
from app.runtime_services import RuntimeServices

from .security import api_key_dependency


def create_customer_admin_router(settings: Settings, services: RuntimeServices) -> APIRouter:
    router = APIRouter()
    require_api_key = api_key_dependency(settings)
    repository = services.repository

    @router.get("/admin/conversations", dependencies=[Depends(require_api_key)])
    async def conversations(limit: int = 50) -> dict[str, Any]:
        return {"items": repository.list_conversations(limit=limit)}

    @router.get("/admin/conversations/{conversation_id}", dependencies=[Depends(require_api_key)])
    async def conversation(conversation_id: str) -> dict[str, Any]:
        return repository.get_conversation(conversation_id)

    @router.get("/admin/customers/{customer_id}/memory", dependencies=[Depends(require_api_key)])
    async def customer_memory(
        customer_id: str,
        wechat: str,
        corp_id: str = "",
        external_userid: str = "",
    ) -> dict[str, Any]:
        scope = repository.resolve_customer_account_scope(
            customer_id,
            wechat=wechat,
            corp_id=corp_id,
            external_userid=external_userid,
        )
        if scope.get("status") == "ambiguous_scope":
            raise HTTPException(status_code=409, detail=scope)
        sales_contact_key = str(scope.get("sales_contact_key") or "")
        return repository.load_memory(sales_contact_key) or {} if sales_contact_key else {}

    @router.delete("/admin/customers/{customer_id}/memory", dependencies=[Depends(require_api_key)])
    async def clear_customer_memory(
        customer_id: str,
        wechat: str,
        corp_id: str = "",
        external_userid: str = "",
    ) -> dict[str, Any]:
        scope = repository.resolve_customer_account_scope(
            customer_id,
            wechat=wechat,
            corp_id=corp_id,
            external_userid=external_userid,
        )
        if scope.get("status") == "ambiguous_scope":
            raise HTTPException(status_code=409, detail=scope)
        sales_contact_key = str(scope.get("sales_contact_key") or "")
        if not sales_contact_key:
            raise HTTPException(
                status_code=409,
                detail={"error": "customer account scope could not be resolved", "scope": scope},
            )
        services.memory_store.clear(sales_contact_key)
        return {"status": "ok", "customer_id": customer_id, "wechat": wechat, "scope": scope}

    @router.get("/admin/customer-records", dependencies=[Depends(require_api_key)])
    async def customer_records(
        customer_id: str,
        wechat: str,
        corp_id: str = "",
        external_userid: str = "",
    ) -> dict[str, Any]:
        customer = str(customer_id or "").strip()
        if not customer:
            raise HTTPException(status_code=400, detail="customer_id is required")
        account = str(wechat or "").strip()
        if not account:
            raise HTTPException(status_code=400, detail="wechat is required")
        result = repository.inspect_customer_records(
            customer,
            wechat=account,
            corp_id=corp_id,
            external_userid=external_userid,
        )
        if (result.get("scope") or {}).get("status") == "ambiguous_scope":
            raise HTTPException(status_code=409, detail=result.get("scope"))
        return result

    @router.post("/admin/customer-records/clear", dependencies=[Depends(require_api_key)])
    async def clear_customer_records(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        customer = str(payload.get("customer_id") or "").strip()
        if not customer:
            raise HTTPException(status_code=400, detail="customer_id is required")
        account = str(payload.get("wechat") or "").strip()
        if not account:
            raise HTTPException(status_code=400, detail="wechat is required")
        result = repository.clear_customer_records(
            customer,
            wechat=account,
            corp_id=str(payload.get("corp_id") or "").strip(),
            external_userid=str(payload.get("external_userid") or "").strip(),
            clear_memory=bool(payload.get("clear_memory", True)),
            clear_sop=bool(payload.get("clear_sop", True)),
            clear_conversations=bool(payload.get("clear_conversations", False)),
            clear_outreach=bool(payload.get("clear_outreach", False)),
        )
        if result.get("status") == "ambiguous_scope":
            raise HTTPException(status_code=409, detail=result.get("scope"))
        return result

    return router
