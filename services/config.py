"""Lab configuration, built from the seeded Atlas data and written to a JSON file.

Lab systems are independent of the database on purpose: they read this file, never Atlas, so
they hold no DB credentials. `scripts/export_lab_config.py` regenerates it (run it after the
seed or a reset). The scanner reads the same file for its known readers and samples.

A reader resolves to a real location by exact id. As a safety net for hand-edited data, an id
that matches a real location once hyphens are removed (`SEQ3` -> `SEQ-3`) is accepted when
that is unique; anything else is listed in `unresolved_readers` and never guessed. (The seed
itself used to produce such ids; that is fixed in `db/seed.py`.)
"""

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx

log = logging.getLogger("lab.scanner_emit")


@dataclass
class LabConfig:
    inventory: dict[str, str] = field(default_factory=dict)  # stored sample -> freezer
    sample_ids: set[str] = field(default_factory=set)
    readers: dict[str, str] = field(default_factory=dict)  # reader id -> location
    unresolved_readers: list[str] = field(default_factory=list)
    freezer_readers: dict[str, str] = field(default_factory=dict)  # location -> reader id
    station_readers: dict[str, str] = field(default_factory=dict)
    courier_readers: dict[str, str] = field(default_factory=dict)
    travel: dict[tuple[str, str], float] = field(default_factory=dict)

    def to_json(self) -> dict:
        return {
            "inventory": self.inventory,
            "sample_ids": sorted(self.sample_ids),
            "readers": self.readers,
            "unresolved_readers": self.unresolved_readers,
            "freezer_readers": self.freezer_readers,
            "station_readers": self.station_readers,
            "courier_readers": self.courier_readers,
            "travel": [[a, b, m] for (a, b), m in self.travel.items()],
        }

    @classmethod
    def load(cls, path: str | Path) -> "LabConfig":
        raw = json.loads(Path(path).read_text())
        raw["sample_ids"] = set(raw["sample_ids"])
        raw["travel"] = {(a, b): m for a, b, m in raw["travel"]}
        return cls(**raw)


def _resolve(location_id: str, known: set[str]) -> str | None:
    if location_id in known:
        return location_id
    squashed = location_id.replace("-", "").upper()
    matches = [k for k in known if k.replace("-", "").upper() == squashed]
    return matches[0] if len(matches) == 1 else None


def build_lab_config(
    locations: list[dict], samples: list[dict], identities: list[dict]
) -> LabConfig:
    kinds = {loc["_id"]: loc.get("kind") for loc in locations}
    cfg = LabConfig(sample_ids={s["_id"] for s in samples})
    cfg.inventory = {s["_id"]: s["location_id"] for s in samples if s.get("status") == "stored"}

    for ident in sorted(identities, key=lambda i: i["_id"]):
        if ident.get("kind") != "scanner":
            continue
        where = _resolve(ident.get("location_id", ""), set(kinds))
        if where is None:
            cfg.unresolved_readers.append(ident["_id"])
            continue
        cfg.readers[ident["_id"]] = where
        cfg.courier_readers.setdefault(where, ident["_id"])
        if kinds[where] == "freezer":
            cfg.freezer_readers.setdefault(where, ident["_id"])
        elif kinds[where] in ("station", "lab"):  # anywhere a sample can be delivered
            cfg.station_readers.setdefault(where, ident["_id"])

    for loc in locations:
        for other, minutes in loc.get("travel_minutes", {}).items():
            if (other, loc["_id"]) not in cfg.travel:
                cfg.travel[(loc["_id"], other)] = minutes
    return cfg


async def export_lab_config(db) -> LabConfig:
    """Read the seeded collections (scanner_svc may read exactly these three)."""
    return build_lab_config(
        await db.locations.find({}).to_list(),
        await db.samples.find({}).to_list(),
        await db.identities.find({}).to_list(),
    )


def _in_background(job: Callable[[], None]) -> None:
    threading.Thread(target=job, daemon=True).start()


def http_emit(
    scanner_url: str,
    client: httpx.Client | None = None,
    patience: float = 120.0,
    pause: float = 3.0,
    run: Callable[[Callable[[], None]], None] = _in_background,
) -> Callable[[dict], None]:
    """What a lab system calls when something physical happens: post a raw scan to the scanner.

    Delivered in the background, like a reader that holds its scan until the scanner answers: the
    lab call never waits on it, and a scanner that is asleep (a free-tier host takes ~30 s to wake)
    still gets the scan. Only failures where the scan cannot have arrived are retried (no
    connection, or the host's proxy answering 502-504), so a retry never records a scan twice.
    """
    http = client or httpx.Client(timeout=60.0)  # one attempt outlasts a cold start
    url = f"{scanner_url.rstrip('/')}/scan"

    def deliver(scan: dict) -> None:
        what = f"{scan.get('type')} scan of {scan.get('sample_id')}"
        end = time.monotonic() + patience
        while True:
            try:
                r = http.post(url, json=scan)
                if r.status_code not in (502, 503, 504):
                    if r.is_error:
                        log.warning("scanner refused the %s: HTTP %s", what, r.status_code)
                    return
            except (httpx.ConnectError, httpx.ConnectTimeout):
                pass  # never reached the scanner: safe to send again
            except httpx.HTTPError as e:  # it may have arrived (e.g. read timeout): don't resend
                log.warning("%s may not have reached the scanner: %s", what, e)
                return
            if time.monotonic() >= end:
                log.warning("%s lost: scanner unreachable for %ss", what, patience)
                return
            time.sleep(pause)

    def emit(scan: dict) -> None:
        run(lambda: deliver(scan))

    return emit
