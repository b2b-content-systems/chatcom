"""The writer port. Three adapters implement it: AnthropicWriter (CLI mode,
Gemini fallback), ReplayWriter (demo and CI) and the session handoff
(skill mode, driven by `chatcom write-prep` / `write-submit`)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Protocol


@dataclass
class Draft:
    text: str
    model: str
    fallback_reason: str | None = None


class Writer(Protocol):
    mode: str

    def draft(self, system: str, prompt: str) -> Draft: ...


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```\s*$", re.S)


def parse_json(text: str):
    """The first JSON object in a model reply (tolerates a code fence)."""
    t = _FENCE.sub("", text.strip())
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(t[start:end + 1])
        except json.JSONDecodeError:
            return None
    return None
