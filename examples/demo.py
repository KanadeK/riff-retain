"""Create tiny synthetic WAVE files and a receipt; never overwrites a directory."""

import json
import struct
import sys
import wave
from pathlib import Path

from riff_retain.core import snapshot

root = Path(sys.argv[1] if len(sys.argv) > 1 else ".demo")
root.mkdir(parents=True, exist_ok=False)
source = root / "before.wav"
with wave.open(str(source), "wb") as stream:
    stream.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
    stream.writeframes(struct.pack("<8h", 0, 100, -100, 200, -200, 300, -300, 0))
original = source.read_bytes()
for filename, title in (("before.wav", b"Old title"), ("tagged.wav", b"New title")):
    raw = original + b"bext" + struct.pack("<I", len(title)) + title + b"\0"
    raw = raw[:4] + struct.pack("<I", len(raw) - 8) + raw[8:]
    (root / filename).write_bytes(raw)
bad = bytearray((root / "tagged.wav").read_bytes())
bad[44] ^= 1  # audio payload mutation, independent of metadata edit
(root / "damaged.wav").write_bytes(bad)
(root / "before.json").write_text(json.dumps(snapshot(source), indent=2) + "\n", encoding="utf-8")
print(f"Created {root}; tagged.wav changes only bext, damaged.wav also changes audio.")
