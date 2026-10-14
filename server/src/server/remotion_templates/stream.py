"""Stream committed work events with bounded database reads and cancellable idle waits.

The backend is the only writer, so an idle stream sleeps until a commit wakes it; the timeout is only a safety net."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import suppress
from time import monotonic
from uuid import UUID

from fastapi import HTTPException

from .store import Store

# Longest an idle stream sleeps before reading again if a wake-up was missed; commits wake it at once.
IDLE_POLL_SECONDS = 5.0


async def event_stream(store: Store, work_id: UUID, after: int) -> AsyncIterator[str]:
    """Replay every page before tailing new events; disconnect only closes this generator.

    No database connection spans a yield or wait. ASGI cancels the generator on disconnect;
    cancellation cannot affect the independent runtime worker or durable task state.
    """
    yield "retry: 2000\n: connected\n\n"
    heartbeat = monotonic()
    with store.watch(work_id) as wake:
        while True:
            wake.clear()
            try:
                records = await asyncio.to_thread(store.work_events, work_id, after)
            except HTTPException as exc:
                if exc.status_code not in {404, 410}:
                    raise
                # A terminal transport signal needs no retained event after the work is gone.
                yield (
                    "event: work.deleted\ndata: "
                    + json.dumps({"work_id": str(work_id)}) + "\n\n"
                )
                return
            for event in records:
                yield f"id: {event.id}\nevent: {event.type}\ndata: {event.model_dump_json()}\n\n"
                after = event.id
            if records:
                heartbeat = monotonic()
                continue
            if monotonic() - heartbeat >= 15:
                yield ": heartbeat\n\n"
                heartbeat = monotonic()
            with suppress(TimeoutError):
                await asyncio.wait_for(wake.wait(), IDLE_POLL_SECONDS)
