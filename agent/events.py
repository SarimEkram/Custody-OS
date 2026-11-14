"""Dashboard events that never touch Atlas (positions would blow the 100 ops/sec budget).

A's `/stream` carries ledger/transfer/scan/approval changes from change streams. B adds
`confirmation_request` and `courier_position`; EventHub fans them out in memory and
merged_stream() puts both sources on one SSE connection.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable

log = logging.getLogger("custody.events")


class EventHub:
    def __init__(self) -> None:
        self.subscribers: set[asyncio.Queue] = set()

    def publish(self, event: dict) -> None:
        for queue in list(self.subscribers):
            queue.put_nowait(event)

    async def subscribe(self) -> AsyncIterator[dict]:
        queue: asyncio.Queue = asyncio.Queue()
        self.subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self.subscribers.discard(queue)


async def merged_stream(feed: AsyncIterator[dict], hub: EventHub) -> AsyncIterator[dict]:
    queue: asyncio.Queue = asyncio.Queue()

    async def pump(source: AsyncIterator[dict]) -> None:
        async for event in source:
            await queue.put(event)

    tasks = [asyncio.create_task(pump(feed)), asyncio.create_task(pump(hub.subscribe()))]
    try:
        while True:
            yield await queue.get()
    finally:
        for task in tasks:
            task.cancel()


class CourierTicker:
    """Polls the courier and publishes where each moving sample is (in memory, SSE only)."""

    # 0.4 s matches the dashboard's `.glide` transition (globals.css): one tick per glide, so the
    # courier moves continuously instead of gliding then pausing. In memory/SSE only, no DB writes.
    def __init__(self, list_jobs: Callable[[], Awaitable[list[dict]]], hub: EventHub,
                 interval: float = 0.4) -> None:  # fmt: skip
        self.list_jobs, self.hub, self.interval = list_jobs, hub, interval

    async def tick(self) -> None:
        for job in await self.list_jobs():
            if job["status"] == "in_transit":
                data = {
                    "job_id": job["job_id"], "transfer_id": job["transfer_id"],
                    "sample_id": job["sample_id"], "from": job["origin"], "to": job["destination"],
                    "progress": job["progress"],
                }  # fmt: skip
                for extra in ("original_to", "return_from_progress"):  # only after a reroute home
                    if extra in job:
                        data[extra] = job[extra]
                self.hub.publish({"kind": "courier_position", "data": data})

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception as e:  # noqa: BLE001 - a courier outage must not stop the dashboard feed
                log.warning("courier poll failed: %s", e)
            await asyncio.sleep(self.interval)
