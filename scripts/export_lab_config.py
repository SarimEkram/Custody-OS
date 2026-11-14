"""Write .lab_config.json from the seeded Atlas data (as scanner_svc, read-only).

.venv/bin/python scripts/export_lab_config.py     # run after seeding / reset_demo.sh
"""

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, ".")
from agent.db import client, database_name  # noqa: E402
from scripts._env import load_env  # noqa: E402
from services.config import export_lab_config  # noqa: E402


async def main() -> int:
    load_env()
    c = client(os.environ["MONGODB_URI_SCANNER"])
    try:
        cfg = await export_lab_config(c[database_name()])
    finally:
        await c.close()
    path = Path(os.environ.get("LAB_CONFIG", ".lab_config.json"))
    path.write_text(json.dumps(cfg.to_json(), indent=2))
    print(f"wrote {path}: {len(cfg.inventory)} stored samples, {len(cfg.readers)} readers, "
          f"{len(cfg.travel)} routes")  # fmt: skip
    for ident in cfg.unresolved_readers:
        print(f"WARNING  reader {ident} has a location_id that matches no location; left out")
    return 0


sys.exit(asyncio.run(main()))
