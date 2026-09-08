from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI

from app.runtime_services import ControlServices, ReplyServices, WorkerServices
from app.workers.supervisor import WorkerSupervisor


logger = logging.getLogger(__name__)


def create_lifespan(
    services: ReplyServices | ControlServices | WorkerServices,
    supervisor: WorkerSupervisor | None,
):
    async def start_supervisor_after_bind() -> None:
        await asyncio.sleep(2)
        if supervisor is not None:
            await supervisor.start()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        services.storage_store.initialize()
        warmup = getattr(services, "warmup", None)
        if callable(warmup):
            try:
                await asyncio.wait_for(
                    warmup(),
                    timeout=8.0,
                )
            except Exception as exc:
                # Knowledge warmup improves the first request but is never a
                # service-availability requirement. Normal cache loading still
                # works on demand after startup.
                logger.warning("Runtime knowledge warmup skipped: %s", type(exc).__name__)
        supervisor_task: asyncio.Task[None] | None = None
        if supervisor is not None:
            supervisor_task = asyncio.create_task(start_supervisor_after_bind(), name="worker-supervisor-start")
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
