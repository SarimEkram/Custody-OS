"""Shared by the mock lab systems: the correlation header and scan emission."""

from collections.abc import Callable

from fastapi import HTTPException


def need_correlation(cid: str | None) -> str:
    if not cid:
        raise HTTPException(400, "X-Correlation-Id header required")
    return cid


class Emitter:
    """Hands raw scans to the scanner. `drop` simulates a dead reader (sent, never confirmed)."""

    def __init__(self, emit: Callable[[dict], None]) -> None:
        self.emit = emit
        self.drop = False

    def send(
        self,
        reader_id: str,
        kind: str,
        sample_id: str,
        location_id: str,
        correlation_id: str,
        **custody,
    ) -> bool:
        if self.drop:
            return False
        try:
            self.emit(
                {
                    "reader_id": reader_id,
                    "type": kind,
                    "sample_id": sample_id,
                    "location_id": location_id,
                    "correlation_id": correlation_id,
                    **custody,
                }
            )
        except Exception:  # noqa: BLE001 - scanner unreachable: the action happened, just unconfirmed
            return False
        return True
