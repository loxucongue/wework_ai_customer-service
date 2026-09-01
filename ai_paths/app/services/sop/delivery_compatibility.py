from __future__ import annotations

from typing import Any

from app.services.sop.execution_core import SopExecutionCore


class SopDeliveryCompatibilityService(SopExecutionCore):
    """Finalize historical ``source_kind=sop_event`` delivery callbacks.

    The retired event receiver and model executor are intentionally not part of
    this service.  It only preserves terminal delivery handling for dispatches
    that may have been created before the route was removed.
    """

    def __init__(self, *, repository: Any, memory_store: Any | None = None) -> None:
        self.repository = repository
        self.memory_store = memory_store
