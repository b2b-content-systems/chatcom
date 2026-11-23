"""The cast stage: draw the show's images for this episode.

    drafted --chatcom cast--> cast

    plan (core/casting.py) -> notice + --approve-calls N (the exact count)
      -> style sheet -> each member's neutral pose -> the other poses
         (from the neutral one) and the backgrounds -> cutouts
      -> episodes/<code>/cast.next.json; cast.json changes only at `committed`

Images are content-addressed in <show>/cache/images/, so a rerun after a stop
never pays twice. A refused reference photo casts that member from behavior.
A model that is gone or keeps failing moves the whole batch to the fallback,
so a show never mixes models."""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from chatcom.adapters import cutout, images as image_ops, store
from chatcom.adapters.painter import FALLBACK_MODEL, BudgetSpent, SwitchModel
from chatcom.core import casting as rules, state as state_ops
from chatcom.core.errors import ChatcomError
from chatcom.core.schema import check

PARALLEL = 3
EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}

NOTICE = {
    "gemini": ("Casting sends image prompts to Google (Gemini) with your GEMINI_API_KEY. Prompts are built from the "
               "show's look and title, the new sets' descriptions, and each cast member's role, traits and texting "
               "style (these come from the chat and can quote a few words of it). The cast's names and nicknames "
               "are removed from every prompt before it is sent. Members with a reference photo: that photo (a clean "
               "copy, no location or device data) goes too."),
    "replay": "Replay painter: nothing leaves this computer.",
}


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _slug(name: str) -> str:
    return hashlib.sha256(name.encode()).hexdigest()[:12]


class Run:
    def __init__(self, show: store.Show, painter, model: str, names: list[str] = ()):
        self.show, self.painter, self.model = show, painter, model
        self.names = list(names)  # the cast's names and nicknames, never sent
        self.cache = show.dir / "cache" / "images"
        self.made: dict[str, tuple[bytes, str, str]] = {}  # ref name -> (bytes, mime, key)
        self.paid = 0

    def ref(self, name: str, photos: dict[str, Path]) -> tuple[bytes, str]:
        if name.startswith("photo:"):
            return photos[name[6:]].read_bytes(), "image/png"
        data, mime, _ = self.made[name]
        return data, mime

    def draw(self, call: rules.Call, photos: dict[str, Path]):
        call = dataclasses.replace(call, prompt=rules.scrub(call.prompt, self.names))
        refs = [self.ref(r, photos) for r in call.refs]
        key = rules.content_key(self.model, call, [_sha(d) for d, _ in refs])
        for ext in EXT.values():
            hit = self.cache / f"{key}{ext}"
            if hit.exists():
                return hit.read_bytes(), next(m for m, e in EXT.items() if e == ext), key
        refused = self.cache / f"{key}-refused"
        if refused.exists():
            return None  # refused before: never paid for again
        got = self.painter.paint(call.prompt, refs, call.aspect)
        if got.refused:
            self.cache.mkdir(parents=True, exist_ok=True)
            refused.write_text("1")
            return None
        self.paid += 1
        self.cache.mkdir(parents=True, exist_ok=True)
        store.write_atomic(self.cache / f"{key}{EXT.get(got.mime, '.png')}", got.data)
        return got.data, got.mime, key


def _photos(show: store.Show, sheet: dict, needed: set[str]) -> dict[str, Path]:
    """Clean copies (no metadata) of the reference photos this run draws from."""
    out = {}
    for m in sheet["members"]:
        if m["source"] != "photo" or not m.get("photo") or m["name"] not in needed:
            continue
        src = Path(m["photo"]).expanduser()
        if not src.is_file():
            raise ChatcomError("CHATCOM_E_IMAGE_UNREADABLE", f"the reference photo for {m['name']} is missing: {src}",
                               "Put the photo back, or cast them from how they text: set source to behavior in "
                               "cast.json with reason operator_choice.", context={"member": m["name"]})
        dest = show.dir / "cast" / "refs" / _slug(m["name"])
        copies = image_ops.copy_all(image_ops.manifest([src], 1), dest, 1)
        out[m["name"]] = dest / copies[0]["file"]
    return out


def new_locations(beats: dict) -> list[dict]:
    """New sets the episode introduces: the cold open is a scene too."""
    return [s["new_location"] for s in [beats.get("cold_open") or {}, *beats.get("scenes", [])]
            if s.get("new_location")]


def _mime(path: Path) -> str:
    return next((m for m, e in EXT.items() if e == path.suffix), "image/png")


def plan_for(show: store.Show, bible_next: dict, sheet: dict, beats: dict) -> list[rules.Call]:
    locs = new_locations(beats)
    return rules.plan(sheet, bible_next["cast"], bible_next["show"]["look"], bible_next["show"].get("title"), locs)


def cast(show: store.Show, painter, *, accepted: bool, approve_calls: int | None, say=print) -> dict:
    from chatcom import app
    with show.lock():
        bible, sheet, _ = app._load(show)
        code, st = app._current_state(show, bible)
        state_ops.require(st, "drafted", "cast")
        ep = show.episode_dir(code)
        nxt_path = ep / "cast.next.json"
        base = store.read_json(nxt_path) if nxt_path.exists() else copy.deepcopy(sheet)
        bible_next = store.read_json(ep / "bible.next.json")
        beats = store.read_json(ep / "beats.json")
        calls = plan_for(show, bible_next, base, beats)
        if not calls:
            if st["stage"] == "drafted":
                store.write_json(nxt_path, base)
                store.write_json(show.state_path(code), state_ops.advance(st, "cast", store.now()))
            return {"episode": code, "stage": "cast", "calls": 0, "paid": 0, "cast_next": str(nxt_path),
                    "refused": [], "model": base.get("image_model")}
        if not accepted:
            text = NOTICE[painter.mode] + f" This step makes {len(calls)} image calls."
            raise ChatcomError("CHATCOM_E_AWAITING_CONFIRMATION", text,
                               f"Ask the operator. If they agree, run `chatcom cast {show.slug} --accept-data-flow "
                               f"--approve-calls {len(calls)}`.", recovery="ask",
                               context={"notice": text, "calls": len(calls),
                                        "next_command": f"chatcom cast {show.slug} --accept-data-flow "
                                                        f"--approve-calls {len(calls)}"})
        if approve_calls != len(calls):
            raise ChatcomError("CHATCOM_E_AWAITING_CONFIRMATION",
                               f"this step makes {len(calls)} image calls; --approve-calls was {approve_calls}",
                               f"Approve exactly that count: --approve-calls {len(calls)}. Cached images cost nothing "
                               "and are not counted twice.", recovery="ask",
                               context={"calls": len(calls),
                                        "next_command": f"chatcom cast {show.slug} --accept-data-flow "
                                                        f"--approve-calls {len(calls)}"})
        needed = {r[6:] for c in calls for r in c.refs if r.startswith("photo:")}
        photos = _photos(show, base, needed)
        pinned = base.get("image_model")
        if pinned and (painter.mode == "replay") != (pinned == "replay-painter"):
            raise ChatcomError("CHATCOM_E_USAGE", f"this show is cast with {pinned}; the {painter.mode} painter cannot "
                               "add to it", "A show keeps one image model. Use the same painter as its first cast.",
                               context={"image_model": pinned})
        if pinned and painter.mode == "gemini" and getattr(painter, "requested", None) not in (None, pinned):
            raise ChatcomError("CHATCOM_E_USAGE", f"this show is cast with {pinned}, not {painter.requested}",
                               "A show keeps one image model: drop --image-model.", context={"image_model": pinned})
        models = [pinned] if pinned else ([painter.model] if painter.mode != "gemini" else
                                          [painter.model] + ([FALLBACK_MODEL] if painter.model != FALLBACK_MODEL else []))
        last_error = None
        calls_before = painter.calls
        painter.budget = calls_before + approve_calls  # the approved count is a hard cap on billed calls
        try:
            for model in models:
                if painter.mode == "gemini":
                    painter.model = model
                try:
                    result = _batch(show, painter, model, base, bible_next, beats, photos, say)
                    break
                except SwitchModel as e:
                    last_error = f"{model}: {e}"
                    say(f"casting: {model} cannot finish the batch ({e}); moving the whole batch to the next model")
            else:
                fix = ("Run `chatcom cast` again later; images already made are kept." if not pinned else
                       f"This show is cast with {pinned} and keeps that model. Run `chatcom cast` again later. If "
                       "Google has retired the model, this show cannot add images until recasting is supported.")
                raise ChatcomError("CHATCOM_E_PAINTER_UNAVAILABLE", f"no image model could finish casting: {last_error}",
                                   fix, recovery="retry" if not pinned else "ask", side_effects="partial",
                                   context={"models": models})
        except BudgetSpent:
            spent = painter.calls - calls_before
            raise ChatcomError("CHATCOM_E_AWAITING_CONFIRMATION",
                               f"casting needs more image calls than the {approve_calls} approved (retries, a model "
                               f"switch or a refused photo); it stopped after {spent}",
                               f"Images already made are kept and cost nothing again. Ask the operator, then run "
                               f"`chatcom cast {show.slug}` to see the new count.", recovery="ask",
                               side_effects="partial", context={"calls_billed": spent}) from None
        finally:
            # the ledger records every billed call, whatever way the run ends
            spent = painter.calls - calls_before
            if spent:
                st_now = store.read_json(show.state_path(code))
                st_now["image_calls"] = st_now.get("image_calls", 0) + spent
                store.write_json(show.state_path(code), st_now)
            painter.budget = None
        sheet_next, refused, _ = result
        paid = spent
        check("cast", sheet_next, code="CHATCOM_E_BIBLE_INVALID", what="the cast sheet casting made is not valid",
              fix="This is a chatcom bug: report it with the run log.")
        store.write_json(nxt_path, sheet_next)
        st_now = store.read_json(show.state_path(code))  # holds the ledger written above
        st2 = state_ops.advance(st_now, "cast", store.now()) if st_now["stage"] == "drafted" else st_now
        store.write_json(show.state_path(code), st2)
    return {"episode": code, "stage": "cast", "calls": len(calls), "paid": paid, "refused": refused,
            "cast_next": str(nxt_path), "model": sheet_next["image_model"]}


def _batch(show, painter, model, base, bible_next, beats, photos, say):
    sheet = copy.deepcopy(base)
    sheet["image_model"] = model
    names = [n for m in bible_next["cast"] for n in (m["name"], *m.get("aliases", []))]
    run = Run(show, painter, model, names)
    look, title = bible_next["show"]["look"], bible_next["show"].get("title")
    refused: list[str] = []
    by_name = {m["name"]: m for m in sheet["members"]}
    members = bible_next["cast"]

    def finish(call, got, name):
        if got is None:
            raise SwitchModel(f"{model} refused a {call.kind} image")  # only photos may be refused
        data, mime, key = got
        run.made[name] = (data, mime, key)
        return data, mime, key

    # 1. the style sheet
    for c in rules.plan(sheet, [], look, title):
        if c.kind == "style":
            data, mime, key = finish(c, run.draw(c, photos), "style")
            path = show.dir / "cast" / f"style{EXT.get(mime, '.png')}"
            store.write_atomic(path, data)
            sheet["style_sheet"] = str(path.relative_to(show.dir))
    if "style" not in run.made and sheet.get("style_sheet"):
        data = (show.dir / sheet["style_sheet"]).read_bytes()
        run.made["style"] = (data, _mime(show.dir / sheet["style_sheet"]), "")

    # 2. neutral poses (a refused photo recasts that member from behavior)
    def neutral(m):
        entry = by_name.setdefault(m["name"], {"name": m["name"], "source": m["source"], "reason": m["reason"],
                                               "photo": None, "poses": {}})
        if "neutral" in entry["poses"]:
            return m["name"], None
        calls = _neutral_call(sheet, by_name, members, m, look, title)
        got = run.draw(calls[0], photos)
        if got is None and entry["source"] == "photo":
            say(f"casting: the image model declined {m['name']}'s photo; casting them from how they text instead")
            refused.append(m["name"])
            entry.update(source="behavior", reason="provider_refused", photo=None)
            calls = _neutral_call(sheet, by_name, members, m, look, title)
            got = run.draw(calls[0], photos)
        finish(calls[0], got, f"pose:{m['name']}:neutral")
        return m["name"], got

    _each(neutral, members, PARALLEL if painter.mode != "replay" else 1)

    # neutral poses already on file are references too
    for m in members:
        entry = by_name[m["name"]]
        ref = f"pose:{m['name']}:neutral"
        if ref not in run.made and "neutral" in entry["poses"]:
            raw = show.dir / entry["poses"]["neutral"]["raw"]
            run.made[ref] = (raw.read_bytes(), _mime(raw), "")

    # 3. the other poses and the backgrounds
    locs = new_locations(beats)
    rest = [c for c in rules.plan(sheet, [{**m, "source": by_name[m["name"]]["source"]} for m in members], look, title,
                                  locs) if not (c.kind == "pose" and c.pose == "neutral") and c.kind != "style"]

    def other(c):
        return c, run.draw(c, photos)

    done = _each(other, rest, PARALLEL if painter.mode != "replay" else 1)

    # 4. files and cutouts
    for name, (data, mime, key) in list(run.made.items()):
        if not name.startswith("pose:") or not key:
            continue
        _, member, pose = name.split(":", 2)
        _place_pose(show, by_name[member], pose, data, mime, key)
    for c, got in done:
        data, mime, key = finish(c, got, f"{c.kind}:{c.member or c.background}:{c.pose or ''}")
        if c.kind == "pose":
            _place_pose(show, by_name[c.member], c.pose, data, mime, key)
        else:
            path = show.dir / "cast" / "backgrounds" / f"{c.background}{EXT.get(mime, '.png')}"
            store.write_atomic(path, data)
            entry = next((b for b in sheet["backgrounds"] if b["id"] == c.background), None)
            if entry is None:
                desc = next(l["description"] for l in locs if l["id"] == c.background)
                entry = {"id": c.background, "description": desc, "file": None, "key": None}
                sheet["backgrounds"].append(entry)
            entry.update(file=str(path.relative_to(show.dir)), key=key)
    sheet["members"] = [by_name[m["name"]] for m in members]
    return sheet, refused, run.paid


def _each(fn, items, workers):
    """fn over items in parallel. The first error stops the rest: calls not yet
    started are never made (so a batch moving to another model, or out of
    budget, pays for nothing more), then that error is raised."""
    stop = threading.Event()

    def guarded(x):
        if stop.is_set():
            return None
        try:
            return fn(x)
        except BaseException:
            stop.set()
            raise

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(guarded, x) for x in items]
        errors = [f.exception() for f in futures]
    first = next((e for e in errors if e is not None), None)
    if first is not None:
        raise first
    return [f.result() for f in futures]


def _neutral_call(sheet, by_name, members, m, look, title):
    """This member's neutral-pose call, planned with the whole cast (so its look hint is its own)."""
    full = {**sheet, "members": [by_name.get(x["name"], {"name": x["name"], "poses": {}, "source": x["source"],
                                                          "photo": None}) for x in members], "backgrounds": []}
    cast_now = [{**x, "source": by_name.get(x["name"], x)["source"]} for x in members]
    return [c for c in rules.plan(full, cast_now, look, title) if c.member == m["name"] and c.pose == "neutral"]


def _place_pose(show, entry, pose, data, mime, key):
    folder = show.dir / "cast" / "members" / _slug(entry["name"])
    raw = folder / f"{pose}.raw{EXT.get(mime, '.png')}"
    store.write_atomic(raw, data)
    try:
        cut = cutout.key(raw, folder / f"{pose}.png")
    except ChatcomError:
        # the cached drawing cannot be cut out: drop it, so the next run draws it again
        for f in (show.dir / "cache" / "images").glob(f"{key}.*"):
            f.unlink()
        raise
    entry["poses"][pose] = {"file": str(cut.relative_to(show.dir)), "key": key, "raw": str(raw.relative_to(show.dir))}
