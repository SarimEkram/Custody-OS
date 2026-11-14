"""Live check: the ElevenLabs key, voice id and model can speak a short prompt.

.venv/bin/python scripts/tts_smoke.py   # writes the MP3 to $TMPDIR and prints its path
"""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, ".")
from agent.voice import ElevenLabsTTS, TTSError  # noqa: E402
from scripts._env import load_env  # noqa: E402

PHRASE = "I'll check out TS-1042 from Freezer B and send it to Sequencing Station 3. Confirm?"


async def main() -> int:
    load_env()
    key, voice = os.environ.get("ELEVENLABS_API_KEY"), os.environ.get("ELEVENLABS_TTS_VOICE_ID")
    if not (key and voice):
        print("FAIL  set ELEVENLABS_API_KEY and ELEVENLABS_TTS_VOICE_ID in .env")
        return 1
    tts = ElevenLabsTTS(key, voice, os.environ.get("ELEVENLABS_TTS_MODEL", "eleven_flash_v2_5"))
    try:
        audio = await tts.synthesize(PHRASE)
    except TTSError as e:
        print(f"FAIL  {e}")
        return 1
    path = os.path.join(tempfile.mkdtemp(), "readback.mp3")
    open(path, "wb").write(audio)
    ok = len(audio) > 1000  # a spoken sentence is tens of KB; an error body is not
    print(f"{'PASS' if ok else 'FAIL'}  {len(audio)} bytes  {path}")
    return 0 if ok else 1


sys.exit(asyncio.run(main()))
