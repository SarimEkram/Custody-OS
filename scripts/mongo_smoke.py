"""Live, read-only check of the three Atlas users and what exists in the `custody` database.

.venv/bin/python scripts/mongo_smoke.py
"""

import asyncio
import os
import sys

from pymongo import AsyncMongoClient
from pymongo.errors import PyMongoError

sys.path.insert(0, ".")
from scripts._env import load_env  # noqa: E402

USERS = {"agent_svc": "MONGODB_URI_AGENT", "scanner_svc": "MONGODB_URI_SCANNER",
         "admin_svc": "MONGODB_URI_ADMIN"}  # fmt: skip


async def ping(name: str, env: str) -> bool:
    client = AsyncMongoClient(os.environ[env], serverSelectionTimeoutMS=8000)
    try:
        await client.admin.command("ping")
        print(f"PASS  {name:<12} authenticated and reachable")
        return True
    except PyMongoError as e:
        print(f"FAIL  {name:<12} {type(e).__name__}: {str(e)[:120]}")
        return False
    finally:
        await client.close()


async def inventory() -> None:
    client = AsyncMongoClient(os.environ["MONGODB_URI_ADMIN"], serverSelectionTimeoutMS=8000)
    db = client[os.environ.get("MONGODB_DB", "custody")]
    try:
        print("\ncollections in", db.name)
        for info in sorted(await db.list_collections(), key=lambda c: c["name"]):
            n = await db[info["name"]].estimated_document_count()
            validated = "validator" if info.get("options", {}).get("validator") else "-"
            print(f"  {info['name']:<22} docs={n:<5} {validated}")
    except PyMongoError as e:
        print(f"  could not list: {type(e).__name__}: {str(e)[:120]}")
    finally:
        await client.close()


async def main() -> int:
    load_env()
    results = [await ping(n, e) for n, e in USERS.items()]
    await inventory()
    return 0 if all(results) else 1


sys.exit(asyncio.run(main()))
