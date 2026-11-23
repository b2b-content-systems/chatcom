"""The reader port and the read cache.

A reader turns one screenshot copy into rows (schemas/read.schema.json). It
never sees more than one image at a time and never joins images: that is
core/screens.py, the same for every reader. Reads are cached per (copy hash,
reader model, prompt version), so a re-run never pays for a read twice."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from chatcom.adapters import store
from chatcom.core.errors import ChatcomError
from chatcom.core.schema import problems

PROMPT_VERSION = "1"


class Reader(Protocol):
    mode: str   # replay | session | gemini | claude
    model: str

    def read(self, image: Path, copy: dict) -> dict: ...


def checked(doc, who: str) -> dict:
    errs = problems("read", doc)
    if errs:
        first = errs[0]
        raise ChatcomError("CHATCOM_E_USAGE", f"{who} returned a read that is not valid: {first['path']} {first['message']}",
                           "Fix the read so it matches schemas/read.schema.json.",
                           context={"problems": errs[:20]})
    return doc


def cached_read(reader: Reader, image: Path, copy: dict, cache_dir: Path | None) -> dict:
    path = None
    if cache_dir is not None:
        safe_model = "".join(c if c.isalnum() or c in "-._" else "_" for c in reader.model)
        path = cache_dir / f"{copy['sha256']}-{safe_model}-v{PROMPT_VERSION}.json"
        if path.is_file():
            try:
                return checked(json.loads(path.read_text("utf-8")), "the read cache")
            except (ChatcomError, json.JSONDecodeError):
                path.unlink()  # a damaged cache entry is read again
    doc = checked(reader.read(image, copy), f"the {reader.mode} reader")
    if path is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        store.write_atomic(path, json.dumps(doc, ensure_ascii=False))
    return doc
