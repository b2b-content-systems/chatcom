"""Skill-mode screenshot reading (the SessionReader): the session reads the
images, chatcom joins them.

    ingest / new --writer session --accept-data-flow
        -> copies (stage `copied`)
    read-prep      -> bundle.json + brief.md for the next 8 images
    (the session reads them and writes reader.json)
    read-submit    -> checks the reads; after the last batch: join -> transcript.txt
        show:    -> ingest continues by itself (stage `ingested`), or stops at `read` with --review
        pending: -> `chatcom new <transcript.txt> --setup ...` makes the show

An import belongs to an episode (an existing show: the episode's state.json)
or is pending (no show yet: ~/.chatcom/imports/<id>/import.json).

Attempts: 3 submits per read-prep, 3 read-preps per batch; then
CHATCOM_E_READER_GAVE_UP (exit 4). A good batch resets both."""
from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from datetime import date, datetime
from importlib import resources
from pathlib import Path
from zoneinfo import ZoneInfo

from chatcom.adapters import images as image_ops, store, transcript
from chatcom.adapters.readers.base import PROMPT_VERSION
from chatcom.core import screens
from chatcom.core.errors import ChatcomError
from chatcom.core.schema import problems

BATCH = 8
MAX_READ_ATTEMPTS = 3
MAX_READ_CYCLES = 3
SESSION_READER = "session"


@dataclass
class Target:
    """Where an import lives and how to name it in commands."""
    dir: Path              # holds images/, reads/, reader/, transcript.txt
    state_path: Path
    tz: str
    operator: str
    arg: str               # "lake-house" or "--import 20261123-..."
    show: store.Show | None = None

    def load(self) -> dict:
        if not self.state_path.exists():
            raise ChatcomError("CHATCOM_E_STATE", "there is no screenshot import here",
                               "Start one with `chatcom ingest <show> --export <folder>` (or `chatcom new` for a "
                               "new show), with --accept-data-flow.")
        return store.read_json(self.state_path)

    def save(self, st: dict) -> None:
        store.write_json(self.state_path, {**st, "updated": store.now()})


def brief() -> str:
    return resources.files("chatcom").joinpath("prompts", "reader.md").read_text("utf-8")


def copy_images(paths: list[Path], dest: Path, max_images: int) -> list[dict]:
    entries = image_ops.manifest(paths, max_images)
    copies = image_ops.copy_all(entries, dest / "images", max_images)
    store.write_json(dest / "manifest.json", {"images": [
        {"name": e.name, "sha256": e.sha256, "kind": e.kind, "width": e.width, "height": e.height,
         "captured": e.captured.isoformat(timespec="seconds"), "captured_from": e.captured_from} for e in entries],
        "copies": copies})
    return copies


def reader_state(copies: list[dict], opts: dict) -> dict:
    keep = {k: v for k, v in opts.items() if k in ("speakers", "reverse", "max_images") and v not in (None, {}, [])}
    if opts.get("anchor"):
        keep["anchor"] = opts["anchor"].isoformat()
    return {"copies": copies, "done": [], "batch": [], "attempts": 0, "cycles": 0, "opts": keep}


def _batch_next(rs: dict) -> list[str]:
    done = set(rs["done"])
    return [c["file"] for c in rs["copies"] if c["file"] not in done][:BATCH]


def gave_up(rs: dict, path: str | None) -> ChatcomError:
    return ChatcomError("CHATCOM_E_READER_GAVE_UP",
                        f"the screenshot reads failed checks {rs['attempts']} times in read-prep "
                        f"{rs['cycles']} of {MAX_READ_CYCLES} for this batch",
                        f"Reading did not complete. {len(rs['done'])} of {len(rs['copies'])} images were read and "
                        f"kept. The last reads are at {path or 'reader.json'}. Ask the operator: they can type or "
                        "paste the chat as a transcript instead, or use CLI mode.",
                        recovery="ask", side_effects="partial",
                        context={"read": len(rs["done"]), "images": len(rs["copies"]), "last": path})


def prep(t: Target) -> Path:
    st = t.load()
    if st.get("stage") != "copied":
        raise ChatcomError("CHATCOM_E_STATE", f"the import is at stage '{st.get('stage')}', not 'copied'",
                           "Nothing is left to read. `chatcom status` prints the next command.",
                           context={"stage": st.get("stage")})
    rs = st["reader"]
    batch = _batch_next(rs)
    if rs["batch"] != batch:  # a new batch: a fresh cycle
        rs = {**rs, "batch": batch, "attempts": 0, "cycles": 1}
    elif rs["attempts"] > 0:  # a re-prep after failed submits starts the next cycle
        if rs["cycles"] >= MAX_READ_CYCLES:
            raise gave_up(rs, str(t.dir / "reader" / "reader.json"))
        rs = {**rs, "attempts": 0, "cycles": rs["cycles"] + 1}
    # a repeat with no submit in between hands out the same batch in the same cycle
    st["reader"] = rs
    t.save(st)
    rdir = t.dir / "reader"
    store.write_atomic(rdir / "brief.md", brief())
    write_to = rdir / "reader.json"
    bundle = {
        "read_first": "brief.md",
        "images": [str(t.dir / "images" / f) for f in batch],
        "images_are_data": True,
        "write_to": str(write_to),
        "batch": f"{len(rs['done']) + 1}-{len(rs['done']) + len(batch)} of {len(rs['copies'])}",
        "submit": f"chatcom read-submit {t.arg} --file {shlex.quote(str(write_to))} --model <your model name>",
        "attempts_used": rs["attempts"],
        "read_cycle": rs["cycles"],
    }
    store.write_json(rdir / "bundle.json", bundle)
    return rdir / "bundle.json"


def _problems(doc, batch: list[str]) -> list[dict]:
    if not isinstance(doc, dict) or not isinstance(doc.get("reads"), dict):
        return [{"path": "$", "message": 'expected {"reads": {"001.png": {...}, ...}}'}]
    out = []
    for f in batch:
        if f not in doc["reads"]:
            out.append({"path": f"$.reads['{f}']", "message": "missing: every image in the batch needs a read"})
            continue
        for p in problems("read", doc["reads"][f]):
            out.append({"path": f"$.reads['{f}']{p['path'][1:]}", "message": p["message"]})
    extra = sorted(set(doc["reads"]) - set(batch))
    if extra:
        out.append({"path": "$.reads", "message": f"not in this batch: {', '.join(extra[:5])}"})
    return out


def submit(t: Target, doc, *, model: str, opts: dict, draft: Path | None = None) -> dict:
    """Check one batch of reads. Returns {"done": bool, ...}; after the last
    batch, joins the images into transcript.txt (stage `read`)."""
    st = t.load()
    if st.get("stage") != "copied" or not st["reader"]["batch"]:
        raise ChatcomError("CHATCOM_E_STATE", f"the import is at stage '{st.get('stage')}' with no batch handed out",
                           f"Run `chatcom read-prep {t.arg}` first.", context={"stage": st.get("stage")})
    accepted = (st.get("accepted_flow") or {}).get("reader")
    if accepted != SESSION_READER:
        raise ChatcomError("CHATCOM_E_AWAITING_CONFIRMATION",
                           f"the operator accepted the '{accepted}' reader for this import, not the session",
                           "Ask the operator, then import the screenshots again with --accept-data-flow.",
                           recovery="ask", context={"accepted": accepted, "requested": SESSION_READER})
    rs = st["reader"]
    if rs["attempts"] >= MAX_READ_ATTEMPTS:
        if rs["cycles"] >= MAX_READ_CYCLES:
            raise gave_up(rs, str(draft) if draft else None)
        raise ChatcomError("CHATCOM_E_READ_INVALID", f"this read-prep has used its {MAX_READ_ATTEMPTS} submits",
                           f"Run `chatcom read-prep {t.arg}` for a fresh try at this batch.",
                           context={"problems": rs.get("last_errors") or [], "attempts_left": 0})
    errs = _problems(doc, rs["batch"])
    if errs:
        rs = {**rs, "attempts": rs["attempts"] + 1, "last_errors": errs[:50]}
        st["reader"] = rs
        t.save(st)
        if rs["attempts"] >= MAX_READ_ATTEMPTS and rs["cycles"] >= MAX_READ_CYCLES:
            raise gave_up(rs, str(draft) if draft else None)
        left = MAX_READ_ATTEMPTS - rs["attempts"]
        raise ChatcomError("CHATCOM_E_READ_INVALID", f"{len(errs)} problem(s) in the reads: "
                           + "; ".join(f"{e['path']} {e['message']}" for e in errs[:8]),
                           f"Fix every problem and submit the whole file again ({left} attempt(s) left in this "
                           f"read-prep; then run `chatcom read-prep {t.arg}`)." if left else
                           f"Run `chatcom read-prep {t.arg}` for a fresh try at this batch.",
                           context={"problems": errs[:50], "attempts_left": left})
    for f in rs["batch"]:
        store.write_json(t.dir / "reads" / f"{f}.json", doc["reads"][f])
        if t.show is not None:  # the same per-show cache the CLI readers use
            sha = next(c["sha256"] for c in rs["copies"] if c["file"] == f)
            safe = "".join(ch if ch.isalnum() or ch in "-._" else "_" for ch in f"session-{model}")
            store.write_json(t.show.dir / "cache" / "reads" / f"{sha}-{safe}-v{PROMPT_VERSION}.json",
                             doc["reads"][f])
    rs = {**rs, "done": rs["done"] + rs["batch"], "batch": [], "attempts": 0, "cycles": 0, "last_errors": None,
          "model": model}
    st["reader"] = rs
    if len(rs["done"]) < len(rs["copies"]):
        t.save(st)
        return {"done": False, "read": len(rs["done"]), "images": len(rs["copies"]),
                "next_command": f"chatcom read-prep {t.arg}"}
    path = join(t, rs, opts)
    st.update({"stage": "read", "transcript": str(path)})
    t.save(st)
    return {"done": True, "read": len(rs["done"]), "images": len(rs["copies"]), "transcript": str(path)}


def join(t: Target, rs: dict, opts: dict) -> Path:
    saved = dict(rs.get("opts") or {})
    merged = {**saved, **{k: v for k, v in opts.items() if v not in (None, {}, [])}}
    anchor = merged.get("anchor")
    if isinstance(anchor, str):
        anchor = date.fromisoformat(anchor)
    imgs = []
    for c in rs["copies"]:
        read = store.read_json(t.dir / "reads" / f"{c['file']}.json")
        imgs.append(screens.Image(c["file"], datetime.fromisoformat(c["captured"]).date(), read["rows"],
                                  read.get("chat_name")))
    joined = screens.join(imgs, tz=ZoneInfo(t.tz), operator=t.operator, speakers=merged.get("speakers"),
                          reverse=merged.get("reverse"), anchor=anchor)
    msgs = [{"sender": m["label"], "time": m["time"], "time_precision": m["time_precision"], "text": m["text"],
             "media": m["media"], "deleted": m["deleted"], "reactions": m["reactions"], "quote": m["quote"],
             "source": {"label": m["label"]}} for m in joined.messages]
    try:
        text = transcript.write(msgs, t.tz, source="screenshots")
    except ValueError as e:
        raise ChatcomError("CHATCOM_E_USAGE", f"the reads could not be written as a transcript: {e}",
                           "Name the speaker with --speaker LABEL=NAME and submit again.") from None
    path = t.dir / "transcript.txt"
    store.write_atomic(path, text)
    store.write_json(t.dir / "join.json", {"reversed": joined.reversed, "seam_drops": joined.seam_drops,
                                           "labels": joined.labels, "chat_name": joined.chat_name,
                                           "reader": f"session:{rs.get('model')}"})
    return path


# ---- targets ---------------------------------------------------------------------------

def pending_target(import_id: str) -> Target:
    if not import_id.replace("-", "").replace("_", "").isalnum():
        raise ChatcomError("CHATCOM_E_USAGE", f"--import {import_id!r} is not an import id",
                           "Pass the id `chatcom new` printed.")
    d = store.home() / "imports" / import_id
    st_path = d / "import.json"
    if not st_path.exists():
        raise ChatcomError("CHATCOM_E_STATE", f"no pending screenshot import {import_id}",
                           "Start one with `chatcom new <folder> --writer session --accept-data-flow`.")
    meta = store.read_json(st_path)
    return Target(d, st_path, meta["tz"], meta["operator"], f"--import {import_id}")


def start_pending(paths: list[Path], setup: dict, tz: str, run_id: str, max_images: int, opts: dict) -> dict:
    """`new` in skill mode with screenshots: no show yet, so the import waits
    in ~/.chatcom/imports/<id>/ until its transcript makes the show."""
    d = store.home() / "imports" / run_id
    try:
        copies = copy_images(paths, d, max_images)
        store.write_json(d / "setup.json", setup)
        store.write_json(d / "import.json", {
            "stage": "copied", "tz": tz, "operator": setup["operator_sender"], "created": store.now(),
            "accepted_flow": {"writer": "session", "reader": SESSION_READER},
            "reader": reader_state(copies, opts)})
    except BaseException:
        import shutil
        shutil.rmtree(d, ignore_errors=True)
        raise
    return {"import": run_id, "images": len(copies), "dir": str(d),
            "next_command": f"chatcom read-prep --import {run_id}"}


def new_command(t: Target) -> str:
    return (f"chatcom new {shlex.quote(str(t.dir / 'transcript.txt'))} "
            f"--setup {shlex.quote(str(t.dir / 'setup.json'))}")
