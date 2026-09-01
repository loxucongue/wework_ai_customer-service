from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from app.config import get_settings
from app.lifecycle import create_lifespan
from app.routers.callbacks import create_callbacks_router
from app.routers.customer_admin import create_customer_admin_router
from app.routers.operations_admin import create_operations_admin_router
from app.routers.outreach_admin import create_outreach_admin_router
from app.routers.reply import create_reply_router
from app.routers.sop_admin import create_sop_admin_router
from app.runtime_roles import RuntimeRole
from app.runtime_services import build_runtime_services
from app.workers.supervisor import WorkerSupervisor


settings = get_settings()
services = build_runtime_services(settings)
runtime_role = settings.runtime_role
worker_supervisor = WorkerSupervisor(settings, services)

app = FastAPI(title=settings.app_name, lifespan=create_lifespan(worker_supervisor))


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service_role": runtime_role.value,
        "configured_service_role": settings.service_role,
        "background_workers_enabled": settings.background_workers_enabled,
        "release": {
            "release_id": settings.release_id,
            "git_commit": settings.build_git_commit,
            "dirty": settings.build_dirty,
            "config_revision": settings.build_config_revision,
        },
        "platform_sop_worker": (
            services.sop_platform_task_service.runtime_status()
            if services.sop_platform_task_service is not None
            else {"enabled": False, "reason": "not_available_in_reply_role"}
        ),
        "strategy_data_callback": services.service_rule_data_service.status(),
    }


if runtime_role is RuntimeRole.REPLY:
    app.include_router(create_reply_router(settings, services))
elif runtime_role is RuntimeRole.CONTROL:
    app.include_router(create_callbacks_router(settings, services))
    app.include_router(create_sop_admin_router(settings, services))
    app.include_router(create_customer_admin_router(settings, services))
    app.include_router(create_operations_admin_router(settings, services))
    app.include_router(create_outreach_admin_router(settings, services, worker_supervisor))
