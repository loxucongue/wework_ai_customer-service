from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.runtime_roles import RuntimeRole
from app.runtime_services import WorkerServices


logger = logging.getLogger(__name__)


class WorkerSupervisor:
    def __init__(self, settings: Settings, services: WorkerServices) -> None:
        self.settings = settings
        self.services = services
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._first_day_retention_last_date = ""

    async def start(self) -> None:
        if self.services.service_rule_data_service.available:
            self._start("strategy_data_callback", self.services.service_rule_data_service.run())
        if not self.settings.background_workers_enabled:
            logger.info("Background workers are disabled by AI_PATHS_BACKGROUND_WORKERS_ENABLED=false")
            return
        if self.settings.sop_platform_pull_enabled:
            self._start("sop_platform_pull", self.services.sop_platform_task_service.run())
        self._start("storage_retention", self._run_storage_retention())
        if self.settings.store_snapshot_refresh_enabled:
            self._start("store_snapshot_refresh", self._run_store_snapshot_refresh())
        self._start("v3_strategy_outcome_attribution", self._run_v3_strategy_outcome_attribution())
        await self.sync_outreach_workers()

    async def stop(self) -> None:
        if "strategy_data_callback" in self._tasks:
            self.services.service_rule_data_service.stop()
        tasks = list(self._tasks.values())
        self._tasks.clear()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task

    async def sync_outreach_workers(self) -> None:
        if (
            self.settings.runtime_role is not RuntimeRole.WORKER
            or not self.settings.background_workers_enabled
        ):
            return
        if self.settings.outreach_first_day_silence_enabled:
            self._start("outreach_plan_monitor", self._run_outreach_plan_monitor())
            self._start("outreach_task_executor", self._run_outreach_task_executor())

    def _start(self, name: str, coroutine: object) -> None:
        current = self._tasks.get(name)
        if current is not None and not current.done():
            close = getattr(coroutine, "close", None)
            if close is not None:
                close()
            return
        self._tasks[name] = asyncio.create_task(coroutine)  # type: ignore[arg-type]

    async def _run_storage_retention(self) -> None:
        while True:
            try:
                result = await asyncio.to_thread(
                    self.services.repository.prune_runtime_history,
                    trace_days=self.settings.aics_trace_retention_days,
                    run_days=self.settings.aics_run_retention_days,
                )
                if any(result.values()):
                    logger.info("Pruned AICS runtime history: %s", result)
                beijing_date = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
                if self._first_day_retention_last_date != beijing_date:
                    first_day_result = await asyncio.to_thread(
                        self.services.repository.prune_first_day_outreach_runs,
                        raw_days=30,
                        summary_days=90,
                    )
                    self._first_day_retention_last_date = beijing_date
                    logger.info("Pruned first-day outreach run history: %s", first_day_result)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("AICS retention worker iteration failed")
            await asyncio.sleep(6 * 60 * 60)

    async def _run_store_snapshot_refresh(self) -> None:
        while True:
            try:
                snapshot = await asyncio.to_thread(self.services.store_snapshot_service.load_snapshot)
                logger.info(
                    "Store snapshot ready: generated_at=%s stores=%s invalid=%s refresh_error=%s",
                    snapshot.get("generated_at"),
                    snapshot.get("store_count"),
                    snapshot.get("invalid_store_count"),
                    snapshot.get("refresh_error"),
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Store snapshot refresh worker iteration failed")
            await asyncio.sleep(max(300, int(self.settings.store_snapshot_refresh_interval_seconds)))

    async def _run_v3_strategy_outcome_attribution(self) -> None:
        while True:
            try:
                result = await asyncio.to_thread(
                    self.services.repository.refresh_v3_strategy_outcomes,
                    limit=self.settings.v3_strategy_analytics_outcome_batch_size,
                )
                if result.get("updated"):
                    logger.info("Updated V3 strategy outcome attribution: %s", result)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("V3 strategy outcome attribution iteration failed")
            await asyncio.sleep(max(60.0, float(self.settings.v3_strategy_analytics_outcome_poll_seconds)))

    async def _run_outreach_plan_monitor(self) -> None:
        while True:
            try:
                if self.settings.outreach_first_day_silence_enabled:
                    await self.services.outreach_service.evaluate_first_day_opened_silence_customers(
                        limit=self.settings.outreach_plan_monitor_batch_size,
                        silent_minutes=self.settings.outreach_first_day_silence_minutes,
                        auto_activate=self.settings.outreach_plan_monitor_auto_activate,
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Outreach plan monitor iteration failed")
            await asyncio.sleep(max(5.0, float(self.settings.outreach_plan_monitor_poll_seconds)))

    async def _run_outreach_task_executor(self) -> None:
        while True:
            try:
                if self.settings.outreach_first_day_silence_enabled:
                    await self.services.outreach_service.execute_due_first_day_tasks(
                        limit=self.settings.outreach_auto_send_batch_size,
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Outreach task executor iteration failed")
            await asyncio.sleep(max(1.0, float(self.settings.outreach_auto_send_poll_seconds)))
