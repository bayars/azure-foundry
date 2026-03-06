from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class RunRecord:
    run_id: str
    thread_id: str
    graph_id: str
    status: str = "running"          # running | interrupted | complete | error
    interrupt_payload: Optional[dict[str, Any]] = None
    _queues: list[asyncio.Queue] = field(default_factory=list, repr=False)


class RunStore:
    """Singleton in-process store for run records and SSE fan-out queues."""

    _instance: Optional["RunStore"] = None

    def __new__(cls) -> "RunStore":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._runs: dict[str, RunRecord] = {}
        return cls._instance

    # ── CRUD ──────────────────────────────────────────────────────────────

    def create(self, graph_id: str) -> RunRecord:
        run_id = str(uuid.uuid4())
        thread_id = str(uuid.uuid4())
        record = RunRecord(run_id=run_id, thread_id=thread_id, graph_id=graph_id)
        self._runs[run_id] = record
        return record

    def get(self, run_id: str) -> Optional[RunRecord]:
        return self._runs.get(run_id)

    def update_status(
        self,
        run_id: str,
        status: str,
        interrupt_payload: Optional[dict[str, Any]] = None,
    ) -> None:
        record = self._runs[run_id]
        record.status = status
        if interrupt_payload is not None:
            record.interrupt_payload = interrupt_payload

    # ── SSE fan-out ───────────────────────────────────────────────────────

    def subscribe(self, run_id: str) -> asyncio.Queue:
        """Create a new SSE queue and register it for the given run."""
        q: asyncio.Queue = asyncio.Queue()
        record = self._runs.get(run_id)
        if record:
            record._queues.append(q)
        return q

    def unsubscribe(self, run_id: str, q: asyncio.Queue) -> None:
        record = self._runs.get(run_id)
        if record and q in record._queues:
            record._queues.remove(q)

    async def broadcast(self, run_id: str, event: dict[str, Any]) -> None:
        """Push an event to every SSE subscriber for this run."""
        record = self._runs.get(run_id)
        if not record:
            return
        for q in list(record._queues):
            await q.put(event)


# Module-level singleton
run_store = RunStore()
