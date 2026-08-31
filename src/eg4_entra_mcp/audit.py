from __future__ import annotations

import asyncio
from pathlib import Path

from .models import AuditEvent


class AuditWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()

    async def write(self, event: AuditEvent) -> None:
        payload = event.model_dump_json(exclude_none=True) + "\n"
        async with self._lock:
            await asyncio.to_thread(self._append, payload)

    def _append(self, payload: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="") as stream:
            stream.write(payload)
            stream.flush()
