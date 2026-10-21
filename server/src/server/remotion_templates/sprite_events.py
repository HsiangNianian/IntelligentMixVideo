"""Push AI asset catalog changes to open template editors over SSE, so no client polls the shared library.

`sprite_store` calls `changed()` after every committed insert, update or delete of a publication. Each open
`GET /api/sprites/stream` then wakes, tells its client to read the catalog again and sleeps until the next change.
Every connection, including the browser's automatic reconnect, starts with the same event, so changes made while a
client was disconnected are picked up as well. Wake-ups are in-process because the backend runs as one instance.
"""

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager, suppress
from threading import Lock

# An idle stream sends a named ping this often: proxies keep the connection open, and the client, which cannot see
# SSE comments, treats a longer silence as a dead connection and reconnects.
PING_SECONDS = 15.0

_watching = Lock()
_watchers: set[tuple[asyncio.AbstractEventLoop, asyncio.Event]] = set()


@contextmanager
def watch() -> Iterator[asyncio.Event]:
    """An event set by every later catalog change; call from the event loop that waits on it."""
    entry = (asyncio.get_running_loop(), asyncio.Event())
    with _watching:
        _watchers.add(entry)
    try:
        yield entry[1]
    finally:
        with _watching:
            _watchers.discard(entry)


def changed() -> None:
    """Wake every open stream; safe to call from worker threads, and a stream whose loop is gone is skipped."""
    with _watching:
        entries = list(_watchers)
    for loop, event in entries:
        with suppress(RuntimeError):
            loop.call_soon_threadsafe(event.set)


async def stream() -> AsyncIterator[str]:
    """Announce the catalog on connect and after each change, coalescing bursts; ping while idle.

    The event is cleared before it is announced, so a change that commits while the client reads the catalog sets
    it again and leads to one more announcement instead of being lost.
    """
    with watch() as wake:
        yield "retry: 3000\n\n"
        while True:
            wake.clear()
            yield "event: catalog.changed\ndata: {}\n\n"
            while True:
                try:
                    await asyncio.wait_for(wake.wait(), PING_SECONDS)
                    break
                except TimeoutError:
                    yield "event: ping\ndata: {}\n\n"
