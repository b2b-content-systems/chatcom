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
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from chatcom.adapters import images as image_ops, store, transcript
from chatcom.adapters.readers.base import cached_read
from chatcom.adapters.readers.gemini import ReadTooLong
from chatcom.adapters.source import ParseResult
from chatcom.core import screens
from chatcom.core.errors import ChatcomError

PARALLEL = 3
MAX_SPLITS = 2


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
    folder = work / "images"

    def read_one(c: dict, depth: int = 0) -> list[screens.Image]:
        try:
            doc = cached_read(reader, folder / c["file"], c, shots.get("cache_dir"))
        except ReadTooLong:
            if depth >= MAX_SPLITS:
                raise ChatcomError("CHATCOM_E_READER_GAVE_UP", f"{c['image']} holds more text than a read can "
                                   "return, even split", "Take that part of the chat in shorter screenshots.",
                                   recovery="ask", side_effects="partial", context={"image": c["image"]}) from None
            return [img for half in image_ops.split_copy(c, folder) for img in read_one(half, depth + 1)]
        return [screens.Image(c["file"], datetime.fromisoformat(c["captured"]).date(), doc["rows"],
                              doc.get("chat_name"))]

    # up to 3 reads at once; results keep manifest order. On the first error the reads not yet started are
    # cancelled, so a failing import stops paying; reads already done are in the cache for the retry.
    pool = ThreadPoolExecutor(max_workers=PARALLEL if reader.mode != "replay" else 1)
    futures = [pool.submit(read_one, c) for c in copies]
    try:
        reads = [img for f in futures for img in f.result()]
    except BaseException:
        pool.shutdown(wait=True, cancel_futures=True)
        raise
    pool.shutdown(wait=True)
    joined = screens.join(reads, tz=ZoneInfo(ctx.tz), operator=shots["operator"], speakers=shots.get("speakers"),
                          reverse=shots.get("reverse"), anchor=shots.get("anchor"))
    msgs = [{"sender": m["label"], "time": m["time"], "time_precision": m["time_precision"], "text": m["text"],
             "media": m["media"], "deleted": m["deleted"], "reactions": m["reactions"], "quote": m["quote"],
             "source": {"label": m["label"]}} for m in joined.messages]
    try:
        text = transcript.write(msgs, ctx.tz, source="screenshots")
    except ValueError as e:  # join() makes every name writable; this is a guard, not a path
        raise ChatcomError("CHATCOM_E_USAGE", f"the screenshots could not be written as a transcript: {e}",
                           "Name the speaker with --speaker LABEL=NAME, or run with --review.") from None
    path = work / "transcript.txt"
    store.write_atomic(path, text)
    store.write_atomic(work / "join.json", json.dumps({"reversed": joined.reversed, "seam_drops": joined.seam_drops,
                                                       "labels": joined.labels, "reader": reader.model}))
    res = transcript.parse(text, ctx.tz, aliases=ctx.aliases, key=ctx.key,
                           anchor=min((r.captured for r in reads), default=None))
    res.group_name = joined.chat_name
    res.fmt = {"images": len(entries), "copies": len(copies), "order": "newest first" if joined.reversed else "as given",
               "reader": reader.model}
    res.artifacts["reader_calls"] = getattr(reader, "calls", 0)
    answered = getattr(reader, "answered_by", None)
    if answered:  # name the models that really answered (a fallback may have read everything)
        res.artifacts["reader_answered"] = dict(answered)
        res.fmt["reader"] = ", ".join(f"{m} x{n}" for m, n in answered.most_common())
    if getattr(reader, "fallbacks", None):
        res.artifacts["reader_fallbacks"] = list(reader.fallbacks)
    res.artifacts.update({"transcript": str(path), "workdir": str(work)})
    return res, joined.chat_name or "Screenshots"
