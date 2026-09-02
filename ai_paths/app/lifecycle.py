from __future__ import annotations

import asyncio
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
        supervisor_task: asyncio.Task[None] | None = None
        if supervisor is not None:
            supervisor_task = asyncio.create_task(supervisor.start(), name="worker-supervisor-start")
        try:
            yield
        finally:
            if supervisor_task is not None and not supervisor_task.done():
                supervisor_task.cancel()
            if supervisor is not None:
                await supervisor.stop()
            if supervisor_task is not None:
                await asyncio.gather(supervisor_task, return_exceptions=True)
            await services.aclose()

    return lifespan
