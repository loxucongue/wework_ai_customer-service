from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI

from app.runtime_services import ControlServices, ReplyServices, WorkerServices
from app.workers.supervisor import WorkerSupervisor


def create_lifespan(
    services: ReplyServices | ControlServices | WorkerServices,
    supervisor: WorkerSupervisor | None,
):
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        services.storage_store.initialize()
        if supervisor is not None:
            await supervisor.start()
        try:
            yield
        finally:
            if supervisor is not None:
                await supervisor.stop()
            await services.aclose()

    return lifespan
