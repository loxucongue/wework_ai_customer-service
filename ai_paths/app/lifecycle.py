from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI

from app.workers.supervisor import WorkerSupervisor


def create_lifespan(supervisor: WorkerSupervisor):
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await supervisor.start()
        try:
            yield
        finally:
            await supervisor.stop()

    return lifespan
