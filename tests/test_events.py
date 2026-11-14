import asyncio

from agent.events import CourierTicker, EventHub, merged_stream


def run(coro):
    return asyncio.run(coro)


async def take(stream, n, timeout=1.0):
    out = []

    async def collect():
        async for ev in stream:
            out.append(ev)
            if len(out) == n:
                return

    await asyncio.wait_for(collect(), timeout)
    return out


def test_every_subscriber_gets_every_published_event_in_order():
    async def go():
        hub = EventHub()
        one, two = hub.subscribe(), hub.subscribe()
        first = asyncio.create_task(take(one, 2))
        second = asyncio.create_task(take(two, 2))
        await asyncio.sleep(0.02)  # both are listening
        hub.publish({"kind": "a"})
        hub.publish({"kind": "b"})
        return await first, await second

    one, two = run(go())
    assert one == two == [{"kind": "a"}, {"kind": "b"}]


def test_the_merged_stream_carries_atlas_events_and_b_events_together():
    async def atlas_feed():
        await asyncio.sleep(0.05)
        yield {"kind": "ledger", "data": {"n": 1}}
        await asyncio.sleep(5)

    async def go():
        hub = EventHub()
        stream = merged_stream(atlas_feed(), hub)
        got = asyncio.create_task(take(stream, 2))
        await asyncio.sleep(0.02)
        hub.publish({"kind": "confirmation_request", "data": {"id": "CONF-1"}})
        return await got

    kinds = {e["kind"] for e in run(go())}
    assert kinds == {"ledger", "confirmation_request"}


def test_ticker_publishes_positions_only_for_couriers_that_are_moving():
    jobs = [
        {"job_id": "CJ-1", "transfer_id": "TR-1", "sample_id": "TS-1", "origin": "FZ-B",
         "destination": "SEQ-3", "status": "in_transit", "progress": 0.5},
        {"job_id": "CJ-2", "transfer_id": "TR-2", "sample_id": "TS-2", "origin": "FZ-A",
         "destination": "SEQ-3", "status": "assigned", "progress": 0.0},
        {"job_id": "CJ-3", "transfer_id": "TR-3", "sample_id": "TS-3", "origin": "FZ-A",
         "destination": "SEQ-3", "status": "arrived", "progress": 1.0},
    ]  # fmt: skip

    async def list_jobs():
        return jobs

    async def go():
        hub = EventHub()
        ticker = CourierTicker(list_jobs, hub)
        listener = asyncio.create_task(take(hub.subscribe(), 1))
        await asyncio.sleep(0.02)
        await ticker.tick()
        return await listener

    [event] = run(go())
    assert event["kind"] == "courier_position"
    assert event["data"] == {"job_id": "CJ-1", "transfer_id": "TR-1", "sample_id": "TS-1",
                             "from": "FZ-B", "to": "SEQ-3", "progress": 0.5}  # fmt: skip


def test_ticker_survives_a_courier_outage_and_keeps_ticking():
    calls = []

    async def flaky():
        calls.append(1)
        raise ConnectionError("courier down")

    async def go():
        ticker = CourierTicker(flaky, EventHub(), interval=0.01)
        task = asyncio.create_task(ticker.run())
        await asyncio.sleep(0.08)
        task.cancel()

    run(go())
    assert len(calls) >= 3
