"""Replay reader for CI and the demo: recorded reads, no keys, no network.

The file is {"model": "...", "reads": {KEY: read}} where KEY is the original
image name, `name#k` for tile k of a tall image, or the copy's sha256."""
from __future__ import annotations

import json
from pathlib import Path

from chatcom.core.errors import ChatcomError


class ReplayReader:
    mode = "replay"

    def __init__(self, path: str | Path):
        p = Path(path).expanduser()
        try:
            doc = json.loads(p.read_text("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ChatcomError("CHATCOM_E_USAGE", f"--replay-reads {p.name} is not a readable JSON file ({type(e).__name__})",
                               "Pass the recorded reads file.") from None
        if not isinstance(doc, dict) or not isinstance(doc.get("reads"), dict):
            raise ChatcomError("CHATCOM_E_USAGE", f"--replay-reads {p.name} has no \"reads\" object",
                               'The file is {"reads": {"IMAGE NAME": {...read...}}}.')
        self.model = str(doc.get("model") or "replay")
        self.reads = doc["reads"]

    def read(self, image: Path, copy: dict) -> dict:
        keys = [copy["sha256"], f"{copy['image']}#{copy['tile']}"]
        if copy["tiles"] == 1:
            keys.append(copy["image"])
        for k in keys:
            if k in self.reads:
                return self.reads[k]
        raise ChatcomError("CHATCOM_E_USAGE", f"the replay file has no read for {keys[-1]}",
                           "Record a read for every screenshot (and every tile of a tall one).",
                           context={"keys": keys[1:]})
