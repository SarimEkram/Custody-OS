"""Live check: the ElevenLabs key, scribe_v3 and keyterms work on a short clip.

.venv/bin/python scripts/stt_smoke.py [clip.wav|webm]   # no argument: synthesise one with macOS `say`
"""

import asyncio
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, ".")
from agent.voice import ElevenLabsSTT, STTError  # noqa: E402
from scripts._env import load_env  # noqa: E402

PHRASE = "Move TS-1042 from Freezer B to Sequencing Station 3 for 2 PM."
KEYTERMS = ["TS-1042", "EV-2201", "TS-1077", "Freezer A", "Freezer B", "Sequencing Station 3"]


def synthesise() -> str:
    d = tempfile.mkdtemp()
    subprocess.run(["say", "-o", f"{d}/t.aiff", PHRASE], check=True)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", f"{d}/t.aiff", f"{d}/t.wav"],
                   check=True)  # fmt: skip
    return f"{d}/t.wav"


async def main() -> int:
    load_env()
    path = sys.argv[1] if len(sys.argv) > 1 else synthesise()
    stt = ElevenLabsSTT(
        os.environ["ELEVENLABS_API_KEY"], os.environ.get("ELEVENLABS_STT_MODEL", "scribe_v3")
    )
    try:
        heard = await stt.transcribe(open(path, "rb").read(), os.path.basename(path), KEYTERMS)
    except STTError as e:
        print(f"FAIL  {e}")
        return 1
    words = [w for w in heard["words"] if w.get("type") == "word"]
    worst = min((w["logprob"] for w in words), default=None)
    ok = "TS-1042" in heard["text"]
    print(
        f"{'PASS' if ok else 'FAIL'}  {heard['text']!r}  words={len(words)} worst_logprob={worst}"
    )
    return 0 if ok else 1


sys.exit(asyncio.run(main()))
