from __future__ import annotations

import asyncio
import logging
import time
from app.chat_runtime import record_reply_memory
from app.services.memory_store import CustomerMemoryStore
from app.services.outreach_service import OutreachService
from app.services.service_rule_data_service import ServiceRuleDataService
from app.services.storage import AppRepository
from app.services.trace_logger import TraceLogger


logger = logging.getLogger(__name__)


class V3ReplyFinalizationService:
    """Complete non-customer-visible V3 persistence outside the HTTP path."""

    def __init__(
        self,
        *,
        repository: AppRepository,
        trace_logger: TraceLogger,
        service_rule_data_service: ServiceRuleDataService | None,
        outreach_service: OutreachService | None,
        memory_store: CustomerMemoryStore | None = None,
        poll_seconds: float = 2.0,
        batch_size: int = 10,
    ) -> None:
        self.repository = repository
        self.trace_logger = trace_logger
        self.service_rule_data_service = service_rule_data_service
        self.outreach_service = outreach_service
        self.memory_store = memory_store
        self.poll_seconds = max(0.2, float(poll_seconds or 1.0))
        self.batch_size = max(1, min(int(batch_size or 10), 100))

    async def run(self) -> None:
        while True:
            try:
                result = await asyncio.to_thread(self.process_batch)
                delay = 0.05 if result["claimed"] else self.poll_seconds
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("V3 reply finalization worker iteration failed")
                delay = min(10.0, self.poll_seconds * 2)
            await asyncio.sleep(delay)

    def process_batch(self) -> dict[str, int]:
        jobs = self.repository.claim_v3_reply_finalizations(limit=self.batch_size)
        completed = 0
        failed = 0
        finish_results: list[dict[str, str]] = []
        for job in jobs:
            request_id = str(job.get("request_id") or "")
            started = time.perf_counter()
            try:
                state = job.get("final_state")
                if not isinstance(state, dict):
                    raise ValueError("invalid deferred final_state")
                if bool(state.get("deferred_identity_observation")):
                    state["customer_identity_observation"] = (
                        self.repository.observe_customer_identity(
                            corp_id=str(state.get("corp_id") or ""),
                            wechat=str(state.get("wechat") or ""),
                            external_userid=str(state.get("external_userid") or ""),
                            customer_id=str(
                                state.get("platform_customer_id")
                                or state.get("customer_id")
                                or ""
                            ),
                            user_id=str(state.get("user_id") or ""),
                            customer_add_wechat_id=str(state.get("customer_add_wechat_id") or ""),
                            source="v3_request_finalization",
                        )
                    )
                record_reply_memory(
                    self.memory_store,
                    final_state=state,
                    reply_messages=[
                        item for item in state.get("reply_messages") or [] if isinstance(item, dict)
                    ],
                )
                if self.service_rule_data_service is not None:
                    state["strategy_data_callback"] = (
                        self.service_rule_data_service.enqueue_customer_open(
                            state,
                            allow_empty_reply=bool(
                                state.get("service_rule_data_allow_empty_reply")
                            ),
                        )
                    )
                if self.outreach_service is not None:
                    state["closing_sequence_shadow"] = (
                        self.outreach_service.record_closing_sequence_shadow(state)
                    )
                log_path = self.trace_logger.write_run(state)
                state["trace_url"] = str(log_path)
                self.repository.save_run(
                    conversation_id=str(job.get("conversation_id") or ""),
                    final_state=state,
                    token_usage=(
                        job.get("token_usage") if isinstance(job.get("token_usage"), dict) else {}
                    ),
                )
                self.repository.record_v3_strategy_usage(
                    conversation_id=str(job.get("conversation_id") or ""),
                    final_state=state,
                )
                finish_results.append({"request_id": request_id, "error": ""})
                completed += 1
            except Exception as exc:
                failed += 1
                error = f"{type(exc).__name__}: {exc}"[:1000]
                logger.exception("V3 reply finalization failed for request_id=%s", request_id)
                finish_results.append({"request_id": request_id, "error": error})
            finally:
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                logger.info(
                    "V3 reply finalization request_id=%s elapsed_ms=%s",
                    request_id,
                    elapsed_ms,
                )
        if finish_results:
            try:
                self.repository.finish_v3_reply_finalizations(finish_results)
            except Exception:
                logger.exception("Failed to persist V3 reply finalization batch results")
                for result in finish_results:
                    try:
                        self.repository.finish_v3_reply_finalization(
                            request_id=result["request_id"],
                            error=result["error"],
                        )
                    except Exception:
                        logger.exception(
                            "Failed to persist V3 reply finalization result for request_id=%s",
                            result["request_id"],
                        )
        return {"claimed": len(jobs), "completed": completed, "failed": failed}
