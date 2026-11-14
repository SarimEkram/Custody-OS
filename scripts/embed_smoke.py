"""Smoke test for MongoDB-hosted Voyage embeddings (ai.mongodb.com).

Usage:  python3 scripts/embed_smoke.py [model]
Reads VOYAGE_API_KEY / VOYAGE_BASE_URL / VOYAGE_MODEL from .env. Never prints the key.
"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        env[key.strip()] = val.split(" #", 1)[0].strip().strip('"').strip("'")
    return env


env = load_env(Path(__file__).resolve().parent.parent / ".env")
key = env.get("VOYAGE_API_KEY", "")
base = env.get("VOYAGE_BASE_URL", "https://ai.mongodb.com/v1").rstrip("/")
model = sys.argv[1] if len(sys.argv) > 1 else env.get("VOYAGE_MODEL", "voyage-3.5")

if not key:
    sys.exit("VOYAGE_API_KEY is empty in .env")

body = json.dumps(
    {
        "input": ["Agent checked out TS-1042 from FZ-B for jlee under GRANT-0007."],
        "model": model,
        "input_type": "document",
    }
).encode()
req = urllib.request.Request(
    f"{base}/embeddings",
    data=body,
    headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
)
try:
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.load(r)
except urllib.error.HTTPError as e:
    sys.exit(f"FAIL {e.code}: {e.read().decode()[:300]}")

vec = data["data"][0]["embedding"]
print(f"PASS  model={data.get('model', model)}  dims={len(vec)}  usage={data.get('usage')}")
print(f"-> set EMBED_DIM={len(vec)} in .env")
