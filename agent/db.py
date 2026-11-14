"""One AsyncMongoClient per process per identity (CLAUDE.md: never a client per request).

python.org Python builds on macOS ship no CA store, so TLS to Atlas fails with
CERTIFICATE_VERIFY_FAILED unless the driver is pointed at certifi's bundle. Timestamps come
back timezone-aware (UTC) so they never clash with `datetime.now(UTC)`.
"""

from __future__ import annotations

import os
from functools import cache

import certifi
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from agent.config import settings


def client(uri: str) -> AsyncMongoClient:
    """A fresh client (scripts that close it, services that own it)."""
    return AsyncMongoClient(
        uri, tlsCAFile=certifi.where(), serverSelectionTimeoutMS=8000, tz_aware=True
    )


def database_name() -> str:
    return os.environ.get("MONGODB_DB", "custody")


@cache
def _client(uri: str) -> AsyncMongoClient:
    if not uri:
        raise RuntimeError("MongoDB URI is empty; check .env")
    return client(uri)


def agent_client() -> AsyncMongoClient:
    """agent_svc: insert-only ledger, read-only custody_events/grants."""
    return _client(settings.mongodb_uri_agent)


def agent_db() -> AsyncDatabase:
    return agent_client()[settings.mongodb_db]


def admin_db() -> AsyncDatabase:
    """admin_svc: setup scripts, seed, revoke. Never used by the agent loop."""
    return _client(settings.mongodb_uri_admin)[settings.mongodb_db]


def scanner_db() -> AsyncDatabase:
    """scanner_svc: the only writer of custody_events."""
    return _client(settings.mongodb_uri_scanner)[settings.mongodb_db]
