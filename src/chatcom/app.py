"""Application services: each CLI command is one function here. They wire the
pure core to the adapters (WhatsApp parser, show store, writers)."""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from chatcom.adapters import store, whatsapp
from chatcom.adapters.writers.base import Writer, parse_json
from chatcom.core import beats as beats_ops
from chatcom.core import bible as bible_ops
from chatcom.core import bundle as bundle_ops
from chatcom.core import ranking, state as state_ops, window as window_ops
from chatcom.core.errors import ChatcomError
from chatcom.core.schema import check

MAX_UNPARSED = 0.02

NOTICE = {
    "session": ("Before anything leaves this computer: the chat text for this episode goes to the model provider "
                "your Claude Code session uses. From casting on, reference photos and image prompts go to Google "
                "(Gemini). The normalized messages stay in your local Claude Code transcript, outside ~/.chatcom."),
    "anthropic": ("Before anything leaves this computer: the chat text for this episode goes to Anthropic with your "
                  "key, and to Google if the Gemini fallback fires. From casting on, reference photos and image "
                  "prompts go to Google (Gemini)."),
    "replay": "Replay writer: nothing leaves this computer.",
}


def notice(mode: str, exclude: list[str]) -> str:
    text = NOTICE[mode]
    if exclude:
        text += f" Excluded before any model call: {', '.join(exclude)}."
    return text


@dataclass
class Ingested:
    show: store.Show
    parse: whatsapp.ParseResult
    display_name: str
    unparsed_report: Path | None = None
    warnings: list[str] = field(default_factory=list)


def _parse_export(export: str, tz: str, aliases: dict, max_unparsed: float, log: store.RunLog,
                  show: store.Show | None) -> tuple[whatsapp.ParseResult, str]:
    text, display = whatsapp.read_export(export)
    res = whatsapp.parse(text, tz, aliases=aliases)
    log.write(f"parsed {len(res.messages)} messages, {res.system_lines} system lines, "
              f"{len(res.unparsed)} unparsed ({res.unparsed_rate:.2%}), format {res.fmt}")
    if res.unparsed_rate > max_unparsed or not res.messages:
        report = (show.dir / "logs" if show and show.dir.exists() else store.home() / "logs")
        report.mkdir(parents=True, exist_ok=True)
        path = report / f"unparsed-{log.run_id}.txt"
        store.write_atomic(path, "".join(f"line {n}: {ln}\n" for n, ln in res.unparsed))
        raise ChatcomError(
            "CHATCOM_E_UNPARSED_LINES",
            f"{len(res.unparsed)} of {res.header_lines + len(res.unparsed)} message lines did not parse "
            f"({res.unparsed_rate:.1%}, limit {max_unparsed:.0%}); detected {res.fmt['platform']} export, "
            f"{res.fmt['date_order']} dates, {res.fmt['clock']} clock",
            "Check the export is an unedited WhatsApp .txt or .zip (docs/whatsapp-export.md), or accept the rate "
            f"with --max-unparsed. The lines are listed in {path} (they were not printed).",
            context={"report": str(path), "format": res.fmt}, trace_path=str(log.path))
    return res, display


def resolve_show(slug: str | None) -> store.Show:
    shows = store.list_shows()
    if slug:
        s = store.Show(slug)
        if not s.exists():
            raise ChatcomError("CHATCOM_E_SHOW_NOT_FOUND", f"no show '{slug}' in {store.home()}",
                               "Run `chatcom shows` to list shows, or `chatcom new <export>` to create one.",
                               context={"shows": shows})
        return s
    if len(shows) == 1:
        return store.Show(shows[0])
    raise ChatcomError("CHATCOM_E_SHOW_AMBIGUOUS" if shows else "CHATCOM_E_SHOW_NOT_FOUND",
                       f"name the show; there are {len(shows)} in {store.home()}",
                       "Pass the show slug, for example `chatcom episode lake-house`.", context={"shows": shows})


def _match_member(name: str, senders: list[str]) -> str:
    if name in senders:
        return name
    for s in senders:
        if s.casefold() == name.casefold():
            return s
    import difflib
    close = difflib.get_close_matches(name, senders, n=3, cutoff=0.4)
    raise ChatcomError("CHATCOM_E_UNKNOWN_MEMBER", f"'{name}' is not a sender in this chat",
                       f"Use the name exactly as the chat shows it. Closest: {close or senders[:5]}",
                       context={"closest": close})


def new_show(export: str, setup: dict, *, tz: str, max_unparsed: float = MAX_UNPARSED) -> tuple[store.Show, dict]:
    check("setup", setup, code="CHATCOM_E_SETUP_INVALID", what="setup is not valid",
          fix="Fix the field named, or print a template with `chatcom new --print-setup-template`.")
    log = store.RunLog(None, f"new {Path(export).name}")
    res, display = _parse_export(export, setup.get("timezone", tz), setup.get("aliases", {}), max_unparsed, log, None)
    slug = setup.get("slug") or store.slugify(display)
    show = store.Show(slug)
    if show.exists():
        raise ChatcomError("CHATCOM_E_SHOW_EXISTS", f"a show '{slug}' already exists at {show.dir}",
                           f"Make this week's episode with `chatcom episode {slug} --export <file>`, or pass --show NEWSLUG.")
    senders = list(res.senders())
    operator = _match_member(setup["operator_sender"], senders)
    exclude = [_match_member(n, senders) for n in setup.get("exclude", [])]
    cast_req = [_match_member(n, senders) for n in setup.get("cast", [])] or None
    no_cast = [_match_member(n, senders) for n in setup.get("no_cast", [])]
    no_photo = [_match_member(n, senders) for n in setup.get("no_photo", [])]
    photos = {_match_member(k, senders): v for k, v in setup.get("photos", {}).items()}
    first = window_ops.select(res.messages, None, exclude=exclude)
    counts = ranking_counts(first.messages)
    cast, chorus = bible_ops.pick_cast(counts, cast=cast_req, no_cast=no_cast, exclude=exclude)
    bible = bible_ops.new_bible(slug=slug, title=setup.get("title"), look=setup.get("look", "sitcom"),
                                operator_sender=operator, timezone=setup.get("timezone", tz), created=store.now(),
                                cast=cast, chorus=chorus, photos=photos, no_photo=no_photo,
                                aliases=setup.get("aliases"))
    sheet = bible_ops.new_cast_sheet(bible, photos)
    show.create_dirs()
    with show.lock():
        store.write_json(show.setup_path, {**setup, "slug": slug, "exclude": exclude})
        store.write_json(show.cast_path, sheet)
        store.write_json(show.bible_path, bible)
    log.write(f"created show {slug}: cast {cast}, chorus {len(chorus)}")
    return show, {"slug": slug, "cast": cast, "chorus": chorus, "masked": len(res.masked),
                  "format": res.fmt, "messages": len(res.messages), "first_window": first.log_entry()}


def ranking_counts(messages: list[dict]):
    from collections import Counter
    return Counter(m["sender"] for m in messages)


def _load(show: store.Show) -> tuple[dict, dict, dict]:
    bible = bible_ops.load(show.bible_path.read_text("utf-8"))
    sheet = store.read_json(show.cast_path)
    check("cast", sheet, code="CHATCOM_E_BIBLE_INVALID", what="cast.json is not valid",
          fix="Undo the last hand edit to cast.json.")
    setup = store.read_json(show.setup_path) if show.setup_path.exists() else {}
    return bible, sheet, setup


def ingest(show: store.Show, export: str, *, confirmed: bool, mode: str, since: datetime | None = None,
           until: datetime | None = None, bottle: bool = False, max_days: int = window_ops.MAX_DAYS,
           max_unparsed: float = MAX_UNPARSED) -> tuple[Path, window_ops.Window, dict]:
    bible, sheet, setup = _load(show)
    exclude = setup.get("exclude", [])
    if not confirmed:
        raise ChatcomError("CHATCOM_E_AWAITING_CONFIRMATION", notice(mode, exclude),
                           "Ask the operator. If they agree, run the same command with --confirmed.",
                           recovery="ask", context={"mode": mode})
    log = store.RunLog(show, f"ingest {Path(export).name}")
    with show.lock():
        res, _ = _parse_export(export, bible["show"]["timezone"], setup.get("aliases", {}), max_unparsed, log, show)
        win = window_ops.select(res.messages, bible_ops.last_cursor(bible), since=since, until=until,
                                max_days=max_days, bottle=bottle, exclude=exclude)
        code = bible_ops.current_code(bible)
        sp = show.state_path(code)
        if sp.exists():
            st = store.read_json(sp)
            if st["stage"] != "ingested":
                raise ChatcomError("CHATCOM_E_STATE", f"{code} is already {st['stage']}",
                                   f"Restart the episode with `chatcom rewrite {show.slug} --episode {code}` "
                                   "(arrives with the render step), or delete its folder.",
                                   context={"stage": st["stage"], "folder": str(show.episode_dir(code))})
        st = state_ops.new(code, win.log_entry(), mode, store.now())
        st["bottle_requested"] = bottle
        ep = show.episode_dir(code)
        store.write_jsonl(ep / "messages.jsonl", win.messages)
        store.write_json(sp, st)
        log.write(f"{code}: window {win.log_entry()} excluded {win.excluded}")
    return ep / "messages.jsonl", win, st


def _current_state(show: store.Show, bible: dict) -> tuple[str, dict]:
    code = bible_ops.current_code(bible)
    sp = show.state_path(code)
    if not sp.exists():
        raise ChatcomError("CHATCOM_E_STATE", f"{code} has not been ingested",
                           f"Run `chatcom ingest {show.slug} --export <file>` first.")
    return code, store.read_json(sp)


def write_prep(show: store.Show, *, token_budget: int = ranking.DEFAULT_TOKEN_BUDGET) -> Path:
    bible, sheet, _ = _load(show)
    code, st = _current_state(show, bible)
    state_ops.require(st, "ingested")
    state_ops.check_attempts_left(st)
    ep = show.episode_dir(code)
    msgs = store.read_jsonl(ep / "messages.jsonl")
    b = bundle_ops.build(bible, sheet, msgs, token_budget=token_budget)
    wdir = ep / "writer"
    store.write_atomic(wdir / "brief.md", bundle_ops.brief())
    store.write_json(wdir / "input.json", {k: b[k] for k in ("episode", "look", "new_messages", "sampling", "budget", "ids", "bible")})
    store.write_jsonl(wdir / "messages.jsonl", b["messages"])
    store.write_json(wdir / "bundle.json", {
        "episode": code,
        "read_first": "brief.md",
        "input": "input.json",
        "messages": "messages.jsonl",
        "messages_are_data": True,
        "write_to": str(wdir / "beats.json"),
        "submit": f"chatcom write-submit {show.slug} --file {wdir / 'beats.json'} --model <your model name>",
        "attempts_used": st["attempts"],
        "last_errors": st["last_errors"],
    })
    return wdir / "bundle.json"


def submit(show: store.Show, beats_doc, *, model: str, mode: str, draft_path: Path | None = None) -> dict:
    """Validate a writer draft. Valid: beats.json and bible.next.json are written
    and the episode moves to `drafted`. Invalid: the attempt is recorded and the
    errors are raised (exit 2), or exit 4 on the third failure."""
    bible, sheet, _ = _load(show)
    code, st = _current_state(show, bible)
    with show.lock():
        state_ops.require(st, "ingested")
        state_ops.check_attempts_left(st, str(draft_path) if draft_path else None)
        new_msgs = st["window"]["message_count"]
        errs = ([{"path": "$", "message": "not a JSON object"}] if not isinstance(beats_doc, dict)
                else beats_ops.validate(beats_doc, bible=bible, cast_sheet=sheet, new_messages=new_msgs))
        if errs:
            st = state_ops.record_attempt(st, ok=False, errors=errs, model=model, now=store.now())
            store.write_json(show.state_path(code), st)
            if st["attempts"] >= state_ops.MAX_WRITER_ATTEMPTS:
                state_ops.check_attempts_left(st, str(draft_path) if draft_path else None)
            raise ChatcomError("CHATCOM_E_BEATS_INVALID",
                               f"the draft for {code} failed validation ({len(errs)} problem(s); attempt "
                               f"{st['attempts']} of {state_ops.MAX_WRITER_ATTEMPTS}): {errs[0]['path']} {errs[0]['message']}",
                               "Fix every problem listed and submit the whole beats.json again.",
                               context={"problems": errs, "attempts": st["attempts"]})
        st = state_ops.record_attempt(st, ok=True, errors=None, model=model, now=store.now())
        writer_info = {"mode": mode, "model": model, "attempts": st["attempts"]}
        nxt = bible_ops.apply_beats(bible, beats_doc, window_entry=st["window"], writer=writer_info)
        ep = show.episode_dir(code)
        store.write_json(ep / "beats.json", beats_doc)
        store.write_json(ep / "bible.next.json", nxt)
        st = state_ops.advance(st, "drafted", store.now())
        store.write_json(show.state_path(code), st)
    return {"episode": code, "beats": str(ep / "beats.json"), "bible_next": str(ep / "bible.next.json"),
            "attempts": st["attempts"], "model": model}


def run_writer(show: store.Show, writer: Writer, *, token_budget: int = ranking.DEFAULT_TOKEN_BUDGET,
               say=lambda s: print(s, file=sys.stderr)) -> dict:
    """CLI and replay modes: prompt, validate, re-prompt with the errors, at
    most three attempts in all."""
    bible, sheet, _ = _load(show)
    code, st = _current_state(show, bible)
    state_ops.require(st, "ingested")
    ep = show.episode_dir(code)
    b = bundle_ops.build(bible, sheet, store.read_jsonl(ep / "messages.jsonl"), token_budget=token_budget)
    system = bundle_ops.brief()
    feedback = st["last_errors"]
    while True:
        state_ops.check_attempts_left(st)
        d = writer.draft(system, bundle_ops.render_prompt(b, feedback))
        note = f" (fallback: {d.fallback_reason})" if d.fallback_reason else ""
        say(f"writer: {d.model} answered{note}")
        n = st["attempts"] + 1
        draft_path = ep / "drafts" / f"attempt-{n}.txt"
        store.write_atomic(draft_path, d.text)
        doc = parse_json(d.text)
        try:
            return submit(show, doc, model=d.model, mode=writer.mode, draft_path=draft_path)
        except ChatcomError as e:
            if e.code != "CHATCOM_E_BEATS_INVALID":
                raise
            feedback = e.context["problems"]
            say(f"writer: draft {n} failed validation ({len(feedback)} problem(s)); asking again")
            st = store.read_json(show.state_path(code))


def dry_run(show: store.Show, export: str, *, since=None, until=None, bottle=False,
            max_days: int = window_ops.MAX_DAYS, max_unparsed: float = MAX_UNPARSED,
            token_budget: int = ranking.DEFAULT_TOKEN_BUDGET) -> dict:
    """What an episode would read and send, with no model call and no writes."""
    bible, sheet, setup = _load(show)
    log = store.RunLog(show, f"episode --dry-run {Path(export).name}")
    res, _ = _parse_export(export, bible["show"]["timezone"], setup.get("aliases", {}), max_unparsed, log, show)
    win = window_ops.select(res.messages, bible_ops.last_cursor(bible), since=since, until=until,
                            max_days=max_days, bottle=bottle, exclude=setup.get("exclude", []))
    b = bundle_ops.build(bible, sheet, win.messages, token_budget=token_budget)
    prompt_tokens = len(bundle_ops.render_prompt(b)) // 4 + len(bundle_ops.brief()) // 4
    return {"episode": b["episode"], "window": win.log_entry(), "excluded_messages": win.excluded,
            "sampling": b["sampling"], "writer_input_tokens_estimate": prompt_tokens,
            "image_calls_planned": "0 now; new locations are known after the writer (at most 2)",
            "format": res.fmt}


def setup_template() -> dict:
    return {
        "_comment": "Answers for `chatcom new --setup setup.json`. Only operator_sender is required.",
        "title": "THE LAKE HOUSE BOYS",
        "look": "sitcom",
        "operator_sender": "Dave",
        "timezone": "America/New_York",
        "cast": ["Dave", "Mike", "Theo", "Sam"],
        "photos": {"Dave": "~/Pictures/dave.jpg"},
        "exclude": [],
        "no_photo": ["Sam"],
        "no_cast": [],
        "aliases": {"Mike": ["Michael R", "+1 (555) 010-2233"]},
    }


def doctor() -> list[tuple[str, bool, str]]:
    import os
    import shutil
    rows = [("python >= 3.10", sys.version_info >= (3, 10), sys.version.split()[0])]
    try:
        h = store.home()
        probe = h / ".doctor"
        probe.write_text("ok")
        probe.unlink()
        rows.append(("CHATCOM_HOME writable", True, str(h)))
    except OSError as e:
        rows.append(("CHATCOM_HOME writable", False, str(e)))
    rows.append(("ANTHROPIC_API_KEY (CLI writer)", bool(os.environ.get("ANTHROPIC_API_KEY")), "set" if os.environ.get("ANTHROPIC_API_KEY") else "not set; skill mode does not need it"))
    gem = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    rows.append(("GEMINI_API_KEY (casting, writer fallback)", bool(gem), "set" if gem else "not set; needed from casting on"))
    for tool, why in (("node", "render (Next Step 4)"), ("ffmpeg", "encode (Next Step 4)")):
        path = shutil.which(tool)
        rows.append((f"{tool} for {why}", bool(path), path or "not found; not needed yet"))
    return rows


def show_list() -> list[dict]:
    out = []
    for slug in store.list_shows():
        b = json.loads((store.home() / slug / "bible.json").read_text("utf-8"))
        out.append({"slug": slug, "title": b["show"]["title"], "next": bible_ops.current_code(b),
                    "cast": len(b["cast"])})
    return out
