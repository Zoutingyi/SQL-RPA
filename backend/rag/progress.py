import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class ProgressEvent:
    doc_id: str
    stage: str
    progress_pct: float
    message: str
    timestamp: str = ""


class ProgressTracker:
    def __init__(self):
        self._queues: dict[str, list[asyncio.Queue]] = {}

    async def publish(self, doc_id: str, stage: str, progress_pct: float, message: str) -> None:
        event = ProgressEvent(
            doc_id=doc_id,
            stage=stage,
            progress_pct=progress_pct,
            message=message,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        queues = self._queues.get(doc_id, [])
        dead = []
        for q in queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            queues.remove(q)

    async def subscribe(self, doc_id: str):
        q: asyncio.Queue = asyncio.Queue(maxsize=64)
        self._queues.setdefault(doc_id, []).append(q)
        try:
            while True:
                event = await q.get()
                yield event
                if event.progress_pct >= 100 or event.stage in ("ready", "failed"):
                    break
        finally:
            self._queues.get(doc_id, []).remove(q) if q in self._queues.get(doc_id, []) else None
            if doc_id in self._queues and not self._queues[doc_id]:
                del self._queues[doc_id]


progress_tracker = ProgressTracker()
