"""HMAC signatures on custody_events (shared by services/scanner and the verifier).

The scanner signs every scan with SCANNER_HMAC_SECRET; the verifier checks it. The agent can verify
but, lacking the secret's write path (scanner_svc DB user), can't forge a stored scan.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone

from agent.config import settings


def _canonical(scan: dict) -> bytes:
    at = scan["scanned_at"]
    if isinstance(at, datetime):
        at = (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
        at = at.isoformat(timespec="milliseconds")
    parts = [scan["scanner_id"], scan["type"], scan["sample_id"], scan["location_id"],
             scan.get("correlation_id") or "", at]
    return "|".join(parts).encode()


def sign_scan(scan: dict, secret: str | None = None) -> str:
    key = (secret or settings.scanner_hmac_secret).encode()
    return "hmac-sha256:" + hmac.new(key, _canonical(scan), hashlib.sha256).hexdigest()


def verify_scan(scan: dict, secret: str | None = None) -> bool:
    return hmac.compare_digest(scan.get("signature", ""), sign_scan(scan, secret))
