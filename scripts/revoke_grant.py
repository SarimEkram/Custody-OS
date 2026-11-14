"""Demo helper: revoke a grant mid-transfer (beat 3).

Usage:  python scripts/revoke_grant.py GRANT-0007 [--by user:jlee] [--reason "..."]
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.control import revoke_grant  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("grant_id")
p.add_argument("--by", default="user:jlee")
p.add_argument("--reason", default="revoked from the command line")
a = p.parse_args()
g = asyncio.run(revoke_grant(a.grant_id, a.by, a.reason))
print(f"{g['_id']} is now {g['status']} (by {g['revoked_by']} at {g['revoked_at']:%H:%M:%S} UTC)")
