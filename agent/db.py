"""One AsyncMongoClient per process per identity (CLAUDE.md: never a client per request)."""

from __future__ import annotations

from functools import lru_cache

from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from agent.config import settings


@lru_cache(maxsize=None)
def _client(uri: str) -> AsyncMongoClient:
    if not uri:
        raise RuntimeError("MongoDB URI is empty; check .env")
    return AsyncMongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=10000)


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
