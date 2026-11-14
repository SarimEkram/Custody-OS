"""Demo helper: press the Stop switch for all transfers (or one).

Usage:  python scripts/stop.py [--transfer TR-0031] [--by user:mokafor]
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.control import press_stop  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("--transfer")
p.add_argument("--by", default="user:mokafor")
a = p.parse_args()
print("stop issued:", asyncio.run(press_stop(a.by, a.transfer)))
