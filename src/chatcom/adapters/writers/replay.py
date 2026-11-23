"""Replay writer for the demo show and CI: returns recorded responses in
order, so a full run needs no keys and no network."""
from __future__ import annotations

import json
from pathlib import Path

from chatcom.adapters.writers.base import Draft
from chatcom.core.errors import ChatcomError


class ReplayWriter:
    mode = "replay"

    def __init__(self, path: str | Path):
        doc = json.loads(Path(path).read_text("utf-8"))
        if isinstance(doc, dict) and "responses" in doc:
            self.model = doc.get("model", "replay")
            self.responses = doc["responses"]
        else:
            self.model, self.responses = "replay", [doc]
        self.calls = 0

    def draft(self, system: str, prompt: str) -> Draft:
        if self.calls >= len(self.responses):
            raise ChatcomError("CHATCOM_E_WRITER_UNAVAILABLE", f"the replay file has only {len(self.responses)} response(s)",
                               "Record another response, or run with --writer anthropic.", recovery="ask")
        r = self.responses[self.calls]
        self.calls += 1
        return Draft(r if isinstance(r, str) else json.dumps(r), self.model)
