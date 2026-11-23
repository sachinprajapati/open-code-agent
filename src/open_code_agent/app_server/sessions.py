"""Connection-owned, in-memory application-server session registry."""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime

from open_code_agent.agent import AgentSession
from open_code_agent.protocol import AgentMessage


def now() -> str:
    """Return a portable UTC timestamp without leaking host locale."""
    return datetime.now(UTC).isoformat()


@dataclass
class ActiveRun:
    """The single task currently owned by one session."""

    run_id: str
    task: asyncio.Task[None]
    sequence: int = 0


@dataclass
class SessionRecord:
    """State that never outlives its parent server connection."""

    session_id: str
    session: AgentSession
    history: list[AgentMessage] = field(default_factory=list)
    turn_count: int = 0
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)
    active: ActiveRun | None = None
    approval_context: dict[str, str] = field(default_factory=dict)
    task_id: str | None = None


class SessionRegistry:
    """Protect session mutation and ensure owned provider resources are closed."""

    def __init__(self, maximum: int) -> None:
        self._maximum = maximum
        self._records: dict[str, SessionRecord] = {}
        self._lock = asyncio.Lock()

    async def add(self, session: AgentSession) -> SessionRecord:
        async with self._lock:
            if len(self._records) >= self._maximum:
                raise RuntimeError("session_limit")
            session_id = secrets.token_urlsafe(24)
            while session_id in self._records:
                session_id = secrets.token_urlsafe(24)
            record = SessionRecord(session_id=session_id, session=session)
            self._records[session_id] = record
            return record

    async def get(self, session_id: str) -> SessionRecord | None:
        async with self._lock:
            return self._records.get(session_id)

    async def list(self) -> list[SessionRecord]:
        async with self._lock:
            return [self._records[key] for key in sorted(self._records)]

    async def remove(self, session_id: str) -> SessionRecord | None:
        async with self._lock:
            return self._records.pop(session_id, None)

    async def close_all(self) -> None:
        records = await self.list()
        for record in records:
            if record.active is not None:
                record.active.task.cancel()
        if records:
            await asyncio.gather(
                *(
                    record.active.task
                    for record in records
                    if record.active is not None
                ),
                return_exceptions=True,
            )
        for record in records:
            await record.session.aclose()
        async with self._lock:
            self._records.clear()
