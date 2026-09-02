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
from app.runtime_services import (
    ControlServices,
    ReplyServices,
    WorkerServices,
    build_control_services,
    build_reply_services,
    build_worker_services,
)
from app.workers.supervisor import WorkerSupervisor


settings = get_settings()
runtime_role = settings.runtime_role
if runtime_role is RuntimeRole.REPLY:
    services: ReplyServices | ControlServices | WorkerServices = build_reply_services(settings)
    worker_supervisor = None
elif runtime_role is RuntimeRole.CONTROL:
    services = build_control_services(settings)
    worker_supervisor = None
else:
    services = build_worker_services(settings)
    worker_supervisor = WorkerSupervisor(settings, services)

app = FastAPI(title=settings.app_name, lifespan=create_lifespan(services, worker_supervisor))


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
        **_role_health(),
    }


if runtime_role is RuntimeRole.REPLY:
    assert isinstance(services, ReplyServices)
    app.include_router(create_reply_router(settings, services))
elif runtime_role is RuntimeRole.CONTROL:
    assert isinstance(services, ControlServices)
    app.include_router(create_callbacks_router(settings, services))
    app.include_router(create_sop_admin_router(settings, services))
    app.include_router(create_customer_admin_router(settings, services))
    app.include_router(create_operations_admin_router(settings, services))
    app.include_router(create_outreach_admin_router(settings, services))


def _role_health() -> dict[str, Any]:
    if isinstance(services, ReplyServices):
        return {
            "strategy_data_outbox": services.service_rule_data_service.status(),
        }
    if isinstance(services, ControlServices):
        return {
            "message_delivery_callback": {
                "required": settings.message_delivery_callback_required,
                "enabled": services.message_delivery_service.enabled,
            },
        }
    return {
        "platform_sop_worker": services.sop_platform_task_service.runtime_status(),
        "strategy_data_callback": services.service_rule_data_service.status(),
    }
