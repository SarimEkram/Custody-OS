"""Tiny .env reader for the smoke scripts (no python-dotenv dependency). Never prints values."""

import os
from pathlib import Path


def load_env(path: str = ".env") -> None:
    for line in Path(path).read_text().splitlines():
        line = line.split(" #")[0].strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())
