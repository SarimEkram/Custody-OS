"""One place that builds Mongo clients (one AsyncMongoClient per process, CLAUDE.md).

python.org Python builds on macOS ship no CA store, so TLS to Atlas fails with
CERTIFICATE_VERIFY_FAILED unless the driver is pointed at certifi's bundle.
"""

import os

import certifi
from pymongo import AsyncMongoClient


def client(uri: str) -> AsyncMongoClient:
    return AsyncMongoClient(uri, tlsCAFile=certifi.where(), serverSelectionTimeoutMS=8000)


def database_name() -> str:
    return os.environ.get("MONGODB_DB", "custody")
