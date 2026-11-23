"""The screenshots adapter: images in, a transcript out, then the transcript
parser (so masking, ids and resume work exactly as for a typed transcript).

    images --manifest/copy (images.py)--> copies in the episode's import folder
           --reader, one copy at a time, cached (readers/)--> rows
           --join (core/screens.py)--> messages
           --transcript.write--> transcript.txt  (`# source: screenshots`)
           --transcript.parse--> the messages every other step uses

Reading needs a reader and the operator's consent; the app passes both in the
registry Context. `--review` stops after transcript.txt so the operator can fix
it and import that file instead."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from chatcom.adapters import images as image_ops, store, transcript
from chatcom.adapters.readers.base import cached_read
from chatcom.adapters.source import ParseResult
from chatcom.core import screens
from chatcom.core.errors import ChatcomError


def load(paths: list[Path], ctx) -> tuple[ParseResult, str]:
    shots = ctx.shots
    if shots is None or shots.get("reader") is None:
        raise ChatcomError("CHATCOM_E_USAGE", "screenshots need a reader and the operator's consent",
                           "Run the command with --accept-data-flow after the operator agrees to the notice.")
    work: Path = shots["workdir"]
    entries = image_ops.manifest(paths, shots.get("max_images", image_ops.MAX_IMAGES))
    copies = image_ops.copy_all(entries, work / "images", shots.get("max_images", image_ops.MAX_IMAGES))
    store.write_json(work / "manifest.json", {"images": [
        {"name": e.name, "sha256": e.sha256, "kind": e.kind, "width": e.width, "height": e.height,
         "captured": e.captured.isoformat(timespec="seconds"), "captured_from": e.captured_from} for e in entries],
        "copies": copies})
    reader = shots["reader"]
    reads = []
    for c in copies:
        doc = cached_read(reader, work / "images" / c["file"], c, shots.get("cache_dir"))
        reads.append(screens.Image(c["file"], datetime.fromisoformat(c["captured"]).date(), doc["rows"],
                                   doc.get("chat_name")))
    joined = screens.join(reads, tz=ZoneInfo(ctx.tz), operator=shots["operator"], speakers=shots.get("speakers"),
                          reverse=shots.get("reverse"), anchor=shots.get("anchor"))
    msgs = [{"sender": m["label"], "time": m["time"], "time_precision": m["time_precision"], "text": m["text"],
             "media": m["media"], "deleted": m["deleted"], "reactions": m["reactions"], "quote": m["quote"],
             "source": {"label": m["label"]}} for m in joined.messages]
    text = transcript.write(msgs, ctx.tz, source="screenshots")
    path = work / "transcript.txt"
    store.write_atomic(path, text)
    store.write_atomic(work / "join.json", json.dumps({"reversed": joined.reversed, "seam_drops": joined.seam_drops,
                                                       "labels": joined.labels, "reader": reader.model}))
    res = transcript.parse(text, ctx.tz, aliases=ctx.aliases, key=ctx.key,
                           anchor=min((r.captured for r in reads), default=None))
    res.group_name = joined.chat_name
    res.fmt = {"images": len(entries), "copies": len(copies), "order": "newest first" if joined.reversed else "as given",
               "reader": reader.model}
    res.artifacts = {"transcript": str(path), "workdir": str(work)}
    return res, joined.chat_name or "Screenshots"
