"""Stream committed work events with bounded database reads and cancellable idle waits."""

import asyncio
from collections.abc import AsyncIterator
from time import monotonic
from uuid import UUID

from .store import Store


async def event_stream(store: Store, work_id: UUID, after: int) -> AsyncIterator[str]:
    """Replay every page before tailing new events; disconnect only closes this generator.

    No SQLite connection spans a yield or wait. ASGI cancels the generator on disconnect;
    cancellation cannot affect the independent runtime worker or durable task state.
    """
    yield "retry: 2000\n: connected\n\n"
    heartbeat = monotonic()
    while True:
        records = await asyncio.to_thread(store.work_events, work_id, after)
        for event in records:
            yield f"id: {event.id}\nevent: {event.type}\ndata: {event.model_dump_json()}\n\n"
            after = event.id
        if records:
            heartbeat = monotonic()
            continue
        if monotonic() - heartbeat >= 15:
            yield ": heartbeat\n\n"
            heartbeat = monotonic()
        await asyncio.sleep(0.25)
