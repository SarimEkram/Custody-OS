"""One-shot wake-ups between the outside world and a paused agent run.

The agent pauses on PENDING_CONFIRMATION / PENDING_APPROVAL and waits on a key; the confirm
endpoint (B) or the approvals change-stream watcher (A) signals it.

shortcut: wait() polls every 10 ms, like ScanBus. It is thread-safe and trivial; move to an
asyncio.Event per key if it ever matters.
"""

import asyncio
import time


class Signals:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def signal(self, key: str, value: str) -> None:
        self.values[key] = value

    def clear(self, key: str) -> None:
        self.values.pop(key, None)

    async def wait(self, key: str, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if key in self.values:
                return self.values[key]
            await asyncio.sleep(0.01)
        return self.values.get(key, "timeout")
