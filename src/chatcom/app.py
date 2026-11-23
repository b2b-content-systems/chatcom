"""Application services: each CLI command is one function here. They wire the
pure core to the adapters (WhatsApp parser, show store, writers)."""
from __future__ import annotations

import json
import re
import shlex
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from chatcom.adapters import registry, store
from chatcom.adapters.source import ParseResult
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

SHOT_NOTICE = {
    "session": ("In Claude Code, this session reads your screenshots (or your paste) in full, including phone "
                "numbers and anyone you exclude, and then writes the episode. For a group you do not fully trust, "
                "use CLI mode, where reading and writing are separate model calls and excluded people never reach "
                "the writer."),
    "anthropic": ("Your screenshots go to Google (Gemini), and to Anthropic if the fallback fires. Phone numbers and "
                  "excluded people are removed after reading, before the episode is written. The chat text then "
                  "goes to Anthropic with your key, as for any export."),
    "replay": "Replay reader and writer: nothing leaves this computer.",
}


def notice(mode: str, exclude: list[str], images: bool = False) -> str:
    text = (SHOT_NOTICE if images else NOTICE)[mode]
    if exclude:
        text += f" Excluded before any model call: {', '.join(exclude)}."
    return text


def _exports(export) -> list[str]:
    return [export] if isinstance(export, (str, Path)) else list(export)


def source_of(export, source: str | None = None) -> str:
    """The adapter an export will be read with, without reading it."""
    return registry.sniff(registry.expand(_exports(export)), source).name


def _reader(mode: str, replay_reads: str | None):
    if mode == "replay":
        if not replay_reads:
            raise ChatcomError("CHATCOM_E_USAGE", "screenshots with --writer replay need --replay-reads FILE",
                               "Pass the recorded reads file (tests/fixtures/screenshots shows the shape).")
        from chatcom.adapters.readers.replay import ReplayReader
        return ReplayReader(replay_reads)
    if mode == "session":
        raise ChatcomError("CHATCOM_E_USAGE", "this chatcom cannot read screenshots in skill mode yet",
                           "Ask the person to paste the chat or type it as a transcript (docs/transcript-format.md); "
                           "reading screenshots in the session arrives in a later release.")
    raise ChatcomError("CHATCOM_E_USAGE", "this chatcom cannot read screenshots in CLI mode yet",
                       "Type or paste the chat as a transcript (docs/transcript-format.md); the Gemini reader arrives "
                       "in a later release.")


def shot_context(opts: dict | None, mode: str, operator: str, workdir: Path) -> dict:
    """The registry Context's `shots`: a reader for this mode and where the
    copies go. `opts` carries the CLI flags (speakers, reverse, anchor,
    max_images, replay_reads)."""
    opts = dict(opts or {})
    return {"reader": _reader(mode, opts.pop("replay_reads", None)), "workdir": workdir, "operator": operator,
            "cache_dir": store.home() / "cache" / "reads", **opts}


def _parse_export(export, tz: str, aliases: dict, max_unparsed: float, log: store.RunLog,
                  show: store.Show | None, *, key: bytes | None, source: str | None = None,
                  thread: str | None = None, shots: dict | None = None) -> tuple[ParseResult, str]:
    ctx = registry.Context(tz=tz, aliases=aliases or {}, key=key, thread=thread, shots=shots)
    res, display, adapter = registry.load(_exports(export), ctx, source)
    log.write(f"source {adapter.name}: parsed {len(res.messages)} messages, {res.system_lines} system lines, "
              f"{len(res.unparsed)} unparsed ({res.unparsed_rate:.2%}), format {res.fmt}")
    if res.unparsed_rate > max_unparsed or not res.messages:
        report = (show.dir / "logs" if show and show.dir.exists() else store.home() / "logs")
        report.mkdir(parents=True, exist_ok=True)
        path = report / f"unparsed-{log.run_id}.txt"
        store.write_atomic(path, "".join(f"line {n}: {ln}\n" for n, ln in res.unparsed))
        raise ChatcomError(
            "CHATCOM_E_UNPARSED_LINES",
            f"{len(res.unparsed)} of {res.header_lines + len(res.unparsed)} message lines did not parse "
            f"({res.unparsed_rate:.1%}, limit {max_unparsed:.0%}); read as {res.describe()}",
            "Check the export is unedited (docs/whatsapp-export.md, docs/transcript-format.md), or accept the rate "
            f"with --max-unparsed. The lines are listed in {path} (they were not printed).",
            context={"report": str(path), "format": res.fmt}, trace_path=str(log.path))
    return res, display


def require_flow(st: dict, writer: str, setup: dict, slug: str) -> None:
    """The writer mode must be the one accepted at ingest; otherwise exit 6 with
    the notice for the new flow, so the operator sees what would change."""
    try:
        state_ops.require_flow(st, writer)
    except ChatcomError as e:
        text = notice(writer, setup.get("exclude", [])) if writer in NOTICE else e.what
        nxt = (f"chatcom ingest {slug} --export <file> --accept-data-flow" if writer == "session"
               else f"chatcom episode {slug} --export <file> --writer {writer} --accept-data-flow")
        raise ChatcomError("CHATCOM_E_AWAITING_CONFIRMATION", f"{e.what}. New notice: {text}", e.fix,
                           recovery="ask", context={**e.context, "mode": writer, "notice": text,
                                                    "next_command": nxt}) from None


def resolve_show(slug: str | None) -> store.Show:
    shows = store.list_shows()
    if slug:
        if not store.SLUG.match(slug):
            raise ChatcomError("CHATCOM_E_SHOW_NOT_FOUND", f"'{slug}' is not a show slug (lower-case letters, digits and dashes)",
                               "Run `chatcom shows` to list shows.", context={"shows": shows})
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
                       "Pass the show slug, for example `chatcom episode lake-house --export <export>`.", context={"shows": shows})


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


def new_show(export, setup: dict, *, tz: str, max_unparsed: float = MAX_UNPARSED,
             source: str | None = None, thread: str | None = None, accepted: bool = False,
             writer: str = "anthropic", shots: dict | None = None) -> tuple[store.Show, dict]:
    check("setup", setup, code="CHATCOM_E_SETUP_INVALID", what="setup is not valid",
          fix="Fix the field named, or print a template with `chatcom new --print-setup-template`.")
    exports = _exports(export)
    log = store.RunLog(None, f"new {Path(exports[0]).name}")
    key = store.new_key()
    shot_ctx = None
    if source_of(exports, source) == "screenshots":
        # Reading screenshots is a model call: consent comes first.
        if not accepted:
            text = notice(writer, setup.get("exclude", []), images=True)
            raise ChatcomError("CHATCOM_E_AWAITING_CONFIRMATION", text,
                               "Ask the operator. If they agree, run the same command with --accept-data-flow.",
                               recovery="ask", context={"mode": writer, "notice": text})
        shot_ctx = shot_context(shots, writer, setup["operator_sender"], store.home() / "imports" / log.run_id)
    res, display = _parse_export(exports, setup.get("timezone", tz), setup.get("aliases", {}), max_unparsed, log,
                                 None, key=key, source=source, thread=thread, shots=shot_ctx)
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
    first = window_ops.select(res.messages, None, exclude=exclude, aliases=setup.get("aliases"), key=key,
                              adapter=res.adapter)
    counts = ranking_counts(first.messages)
    cast, chorus = bible_ops.pick_cast(counts, cast=cast_req, no_cast=no_cast, exclude=exclude)
    # Everyone in the export who is not cast or excluded is chorus, not only
    # those who spoke in episode 1's week.
    drop = {n.casefold() for n in exclude} | {c.casefold() for c in cast}
    chorus += [s for s in senders if s.casefold() not in drop and s not in chorus]
    bible = bible_ops.new_bible(slug=slug, title=setup.get("title"), look=setup.get("look", "sitcom"),
                                operator_sender=operator, timezone=setup.get("timezone", tz), created=store.now(),
                                cast=cast, chorus=chorus, photos=photos, no_photo=no_photo,
                                aliases=setup.get("aliases"))
    sheet = bible_ops.new_cast_sheet(bible, photos)
    show.create_dirs()
    if shot_ctx is not None:  # the copies and the transcript move in with the show
        dest = show.dir / "imports" / log.run_id
        dest.parent.mkdir(parents=True, exist_ok=True)
        Path(shot_ctx["workdir"]).rename(dest)
    with show.lock():
        export_paths = [str(Path(e).expanduser().resolve()) for e in exports]
        store.write_json(show.setup_path, {**setup, "slug": slug, "exclude": exclude, "export_paths": export_paths})
        store.write_json(show.cast_path, sheet)
        store.write_json(show.bible_path, bible)
        # Last: a crash before here leaves no secret, and Show.key() names the repair.
        if show.secret_path.is_file():
            show.secret_path.unlink()  # an orphan from a run that died before bible.json
        show.write_key(key)
    log.write(f"created show {slug}: cast {cast}, chorus {len(chorus)}")
    return show, {"slug": slug, "cast": cast, "chorus": chorus, "masked": len(res.masked), "source": res.adapter,
                  "inbox": str(show.inbox),
                  "unparsed_rate": res.unparsed_rate,
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


def _aware(dt: datetime | None, tz: str) -> datetime | None:
    """--since/--until without an offset are in the show's zone."""
    if dt is None or dt.tzinfo is not None:
        return dt
    from zoneinfo import ZoneInfo
    return dt.replace(tzinfo=ZoneInfo(tz))


MASKED_LABEL = re.compile(r"^Unnamed [0-9a-f]{6}$")


def check_exclusions_match(show: store.Show, setup: dict, res: ParseResult) -> None:
    """Fail closed: an excluded phone label that matches no one in this export
    would silently stop excluding that person. Labels are keyed by the show's
    secret, so they change after migrate or a new secret."""
    senders = set(res.senders())
    stale = [e for e in setup.get("exclude", []) if MASKED_LABEL.match(e) and e not in senders]
    if stale:
        current = sorted(s for s in senders if MASKED_LABEL.match(s))
        raise ChatcomError("CHATCOM_E_SETUP_INVALID",
                           f"setup.json excludes {', '.join(stale)}, which matches no sender in this export "
                           "(phone labels change when a show gets a new secret, for example after migrate)",
                           f"Nothing was sent. In {show.setup_path}, replace it under \"exclude\" with the label this "
                           f"export uses ({', '.join(current) or 'none here'}), add an alias for the number, or remove "
                           "it if that person has left the chat.",
                           context={"stale": stale, "current_labels": current})


def check_session_paths(show: store.Show, setup: dict, exports: list[str]) -> None:
    """In session mode the agent, not the operator, types the --export path. It
    may read only the export given at `new` or a file in the show's inbox, so
    an injected command cannot hand some other file to the model."""
    allowed = {str(Path(p)) for p in setup.get("export_paths", [])}
    inbox = show.inbox.resolve()
    for e in exports:
        p = Path(e).expanduser().resolve()
        if str(p) in allowed or p == inbox or inbox in p.parents:
            continue
        raise ChatcomError("CHATCOM_E_EXPORT_OUTSIDE_INBOX",
                           f"in skill mode chatcom reads only the export given at `new` or files in {show.inbox}; "
                           f"{p.name} is elsewhere",
                           f"Ask the operator to move the new export into {show.inbox}, then pass that path.",
                           recovery="ask", context={"inbox": str(show.inbox), "path": str(p)})


def ingest(show: store.Show, export, *, accepted: bool, mode: str, since: datetime | None = None,
           until: datetime | None = None, bottle: bool = False, max_days: int = window_ops.MAX_DAYS,
           max_unparsed: float = MAX_UNPARSED, source: str | None = None,
           thread: str | None = None, replace: bool = False, review: bool = False,
           shots: dict | None = None) -> tuple[Path, window_ops.Window | None, dict]:
    bible, sheet, setup = _load(show)
    exclude = setup.get("exclude", [])
    exports = _exports(export)
    if mode == "session":
        check_session_paths(show, setup, exports)
    images = source_of(exports, source) == "screenshots"
    if images:
        _reader(mode, (shots or {}).get("replay_reads"))  # refuse a mode that cannot read yet, before consent
    if not accepted:
        text = notice(mode, exclude, images=images)
        raise ChatcomError("CHATCOM_E_AWAITING_CONFIRMATION", text,
                           "Ask the operator. If they agree, run the same command with --accept-data-flow.",
                           recovery="ask", context={"mode": mode, "notice": text})
    log = store.RunLog(show, f"ingest {Path(exports[0]).name}")
    with show.lock():
        bible, sheet, setup = _load(show)  # re-read under the lock
        tz = bible["show"]["timezone"]
        key = show.key()
        code = bible_ops.current_code(bible)
        sp = show.state_path(code)
        prior = store.read_json(sp) if sp.exists() else None
        if prior and prior["stage"] in state_ops.IMPORTING and not replace:
            # Only the transcript this import wrote may continue it.
            want = prior.get("transcript")
            if not (want and all(Path(e).expanduser().resolve() == Path(want).resolve() for e in exports)):
                raise ChatcomError("CHATCOM_E_IMPORT_IN_PROGRESS",
                                   f"{code} has a screenshot import at stage '{prior['stage']}'",
                                   f"Continue it with --export {want}, or pass --replace to start the import over."
                                   if want else "Pass --replace to start the import over.",
                                   context={"stage": prior["stage"], "transcript": want})
        shot_ctx = (shot_context(shots, mode, bible["show"]["operator_sender"], show.episode_dir(code) / "import")
                    if images else None)
        res, _ = _parse_export(exports, tz, setup.get("aliases", {}), max_unparsed, log, show, key=key,
                               source=source, thread=thread, shots=shot_ctx)
        if review and res.adapter == "screenshots" and res.artifacts.get("transcript"):
            st = state_ops.new(code, None, mode, store.now(), stage="read", reader=shot_ctx["reader"].mode)
            st["source"] = "screenshots"
            st["transcript"] = res.artifacts["transcript"]
            store.write_json(sp, st)
            log.write(f"{code}: screenshots read, stopped for review at {st['transcript']}")
            return Path(st["transcript"]), None, st
        check_exclusions_match(show, setup, res)
        win = window_ops.select(res.messages, bible_ops.last_cursor(bible), since=_aware(since, tz),
                                until=_aware(until, tz), max_days=max_days, bottle=bottle,
                                exclude=setup.get("exclude", []), aliases=setup.get("aliases"),
                                key=key, adapter=res.adapter)
        if sp.exists():
            st = store.read_json(sp)
            if st["stage"] not in ("ingested", *state_ops.IMPORTING):
                raise ChatcomError("CHATCOM_E_STATE", f"{code} is already {st['stage']}",
                                   f"Restart the episode with `chatcom rewrite {show.slug} --episode {code}` "
                                   "(arrives with the render step), or delete its folder.",
                                   context={"stage": st["stage"], "folder": str(show.episode_dir(code))})
        # A re-ingest before the draft is accepted starts the episode over, with
        # fresh writer attempts: this is the "rerun the skill" recovery.
        st = state_ops.new(code, win.log_entry(), mode, store.now(),
                           reader=shot_ctx["reader"].mode if shot_ctx else (prior or {}).get("accepted_flow", {}).get("reader"))
        st["bottle_requested"] = bottle
        st["unparsed_rate"] = round(res.unparsed_rate, 4)
        st["source"] = res.adapter
        st["resume"] = {"method": win.method, "warning": win.warning}
        ep = show.episode_dir(code)
        store.write_jsonl(ep / "messages.jsonl", win.messages)
        store.write_json(sp, st)
        log.write(f"{code}: window {win.log_entry()} excluded {win.excluded} resume {win.method}")
    return ep / "messages.jsonl", win, st


def _current_state(show: store.Show, bible: dict) -> tuple[str, dict]:
    code = bible_ops.current_code(bible)
    sp = show.state_path(code)
    if not sp.exists():
        raise ChatcomError("CHATCOM_E_STATE", f"{code} has not been ingested",
                           f"Run `chatcom ingest {show.slug} --export <file>` first.")
    return code, store.read_json(sp)


def write_prep(show: store.Show, *, token_budget: int = ranking.DEFAULT_TOKEN_BUDGET) -> Path:
    log = store.RunLog(show, "write-prep")
    with show.lock():
        path = _write_prep_locked(show, token_budget)
    log.write(f"bundle {path}")
    return path


def _write_prep_locked(show: store.Show, token_budget: int) -> Path:
    bible, sheet, setup = _load(show)
    code, st = _current_state(show, bible)
    state_ops.require(st, "ingested")
    require_flow(st, "session", setup, show.slug)
    st = state_ops.start_cycle(st, store.now())
    store.write_json(show.state_path(code), st)
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
        "submit": f"chatcom write-submit {show.slug} --file {shlex.quote(str(wdir / 'beats.json'))} --model <your model name>",
        "attempts_used": st["attempts"],
        "prep_cycle": st["prep_cycles"],
        "last_errors": st["last_errors"],
    })
    return wdir / "bundle.json"


def submit(show: store.Show, beats_doc, *, model: str, mode: str, draft_path: Path | None = None) -> dict:
    """Validate a writer draft. Valid: beats.json and bible.next.json are written
    and the episode moves to `drafted`. Invalid: the attempt is recorded and the
    errors are raised (exit 2), or exit 4 on the third failure."""
    log = store.RunLog(show, f"write-submit mode={mode} model={model}")
    with show.lock():
        bible, sheet, setup = _load(show)  # read under the lock: a concurrent submit cannot be lost
        code, st = _current_state(show, bible)
        state_ops.require(st, "ingested")
        require_flow(st, mode, setup, show.slug)
        state_ops.check_attempts_left(st, str(draft_path) if draft_path else None)
        new_msgs = st["window"]["message_count"]
        extra = bible_ops.new_speakers(bible, store.read_jsonl(show.episode_dir(code) / "messages.jsonl"))
        errs = ([{"path": "$", "message": "not a JSON object"}] if not isinstance(beats_doc, dict)
                else beats_ops.validate(beats_doc, bible=bible, cast_sheet=sheet, new_messages=new_msgs,
                                        extra_chorus=extra))
        if errs:
            st = state_ops.record_attempt(st, ok=False, errors=errs, model=model, now=store.now())
            store.write_json(show.state_path(code), st)
            log.write(f"{code}: attempt {st['attempts']} invalid ({len(errs)} problems)")
            if st["attempts"] >= state_ops.MAX_WRITER_ATTEMPTS:
                state_ops.check_attempts_left(st, str(draft_path) if draft_path else None)
            raise ChatcomError("CHATCOM_E_BEATS_INVALID",
                               f"the draft for {code} failed validation ({len(errs)} problem(s); attempt "
                               f"{st['attempts']} of {state_ops.MAX_WRITER_ATTEMPTS}): {errs[0]['path']} {errs[0]['message']}",
                               "Fix every problem listed and submit the whole beats.json again.",
                               context={"problems": errs, "attempts": st["attempts"]})
        st = state_ops.record_attempt(st, ok=True, errors=None, model=model, now=store.now())
        writer_info = {"mode": mode, "model": model, "attempts": st["attempts"]}
        nxt = bible_ops.apply_beats(bible, beats_doc, window_entry=st["window"], writer=writer_info,
                                    extra_chorus=extra)
        ep = show.episode_dir(code)
        store.write_json(ep / "beats.json", beats_doc)
        store.write_json(ep / "bible.next.json", nxt)
        st = state_ops.advance(st, "drafted", store.now())
        store.write_json(show.state_path(code), st)
        log.write(f"{code}: drafted in {st['attempts']} attempt(s)")
    return {"episode": code, "beats": str(ep / "beats.json"), "bible_next": str(ep / "bible.next.json"),
            "attempts": st["attempts"], "model": model}


def status(show: store.Show) -> dict:
    bible, _, setup = _load(show)
    code = bible_ops.current_code(bible)
    sp = show.state_path(code)
    st = store.read_json(sp) if sp.exists() else None
    return {"show": show.slug, "episode": code, "stage": st["stage"] if st else "not started",
            "attempts": st["attempts"] if st else 0, "prep_cycles": st.get("prep_cycles", 0) if st else 0,
            "writer_mode": st.get("writer_mode") if st else None, "source": st.get("source") if st else None,
            "resume": st.get("resume") if st else None, "lock": show.lock_holder(),
            "next": state_ops.next_command(st, show.slug)}


def run_writer(show: store.Show, writer: Writer, *, token_budget: int = ranking.DEFAULT_TOKEN_BUDGET,
               say=lambda s: print(s, file=sys.stderr)) -> dict:
    """CLI and replay modes: prompt, validate, re-prompt with the errors, at
    most three attempts in all."""
    bible, sheet, setup = _load(show)
    code, st = _current_state(show, bible)
    state_ops.require(st, "ingested")
    require_flow(st, writer.mode, setup, show.slug)
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


def dry_run(show: store.Show, export, *, since=None, until=None, bottle=False,
            max_days: int = window_ops.MAX_DAYS, max_unparsed: float = MAX_UNPARSED,
            token_budget: int = ranking.DEFAULT_TOKEN_BUDGET, source: str | None = None,
            thread: str | None = None, operator_typed: bool = True, max_images: int | None = None) -> dict:
    """What an episode would read and send, with no model call and no writes.
    A dry run asks for no consent, so when an agent may have typed it (no TTY)
    it reads only the inbox and the export given at `new`. Screenshots are
    listed, never read: reading them is a model call."""
    bible, sheet, setup = _load(show)
    exports = _exports(export)
    if not operator_typed:
        check_session_paths(show, setup, exports)
    if source_of(exports, source) == "screenshots":
        from chatcom.adapters import images as image_ops
        entries = image_ops.manifest(registry.expand(exports), max_images or image_ops.MAX_IMAGES)
        return {"episode": bible_ops.current_code(bible), "source": "screenshots",
                "images": [{"name": e.name, "captured": e.captured.isoformat(timespec="seconds"),
                            "captured_from": e.captured_from} for e in entries],
                "note": "a dry run lists screenshots in reading order and never reads them (reading is a model call)"}
    log = store.RunLog(show, f"episode --dry-run {Path(exports[0]).name}")
    key = show.key()
    res, _ = _parse_export(exports, bible["show"]["timezone"], setup.get("aliases", {}), max_unparsed, log, show,
                           key=key, source=source, thread=thread)
    tz = bible["show"]["timezone"]
    win = window_ops.select(res.messages, bible_ops.last_cursor(bible), since=_aware(since, tz),
                            until=_aware(until, tz), max_days=max_days, bottle=bottle,
                            exclude=setup.get("exclude", []), aliases=setup.get("aliases"),
                            key=key, adapter=res.adapter)
    b = bundle_ops.build(bible, sheet, win.messages, token_budget=token_budget)
    prompt_tokens = len(bundle_ops.render_prompt(b)) // 4 + len(bundle_ops.brief()) // 4
    return {"episode": b["episode"], "window": win.log_entry(), "excluded_messages": win.excluded,
            "sampling": b["sampling"], "writer_input_tokens_estimate": prompt_tokens,
            "image_calls_planned": "0 now; new locations are known after the writer (at most 2)",
            "source": res.adapter, "resume": win.method, "warning": win.warning,
            "format": res.fmt, "unparsed_rate": round(res.unparsed_rate, 4)}


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


# ---- migrate ----------------------------------------------------------------

def _upgrade_message(m: dict) -> dict:
    m = dict(m)
    m.setdefault("time_precision", "minute")
    m.setdefault("quote", None)
    src = dict(m.get("source") or {"adapter": "whatsapp"})
    src.setdefault("label", m["sender"])
    m["source"] = src
    if m.get("reactions"):
        m["reactions"] = [{"emoji": r["emoji"], "sender": r.get("sender"), "count": r.get("count", 1)}
                          for r in m["reactions"]]
    return m


def _upgrade_cursor(cursor: dict, msgs: list[dict], key: bytes) -> dict:
    """A v1 cursor gets an empty signature: stored rows carry v1 labels and
    masking, so a signature built from them would not match a v2 parse. An
    empty signature makes resume keep the v1 rule (skip the cursor minute)."""
    if "signature" in cursor:
        return cursor
    return {**cursor, "adapter": "whatsapp", "precision": "minute", "signature": {"raw": [], "canonical": []}}


def migrate(slug: str | None) -> dict:
    """v1 -> v2. Backs up the show, adds the v2 fields to stored message files,
    rebuilds cursor signatures from them, and writes bible.json last: a kill
    part way leaves a v1 bible, and migrate runs again."""
    from chatcom import SCHEMA_VERSION
    if not slug:
        shows = store.list_shows()
        if len(shows) != 1:
            raise ChatcomError("CHATCOM_E_SHOW_AMBIGUOUS" if shows else "CHATCOM_E_SHOW_NOT_FOUND",
                               "name the show to migrate", "Run `chatcom migrate <slug>`.", context={"shows": shows})
        slug = shows[0]
    show = store.Show(slug)
    if not show.exists():
        raise ChatcomError("CHATCOM_E_SHOW_NOT_FOUND", f"no show '{slug}' in {store.home()}", "Run `chatcom shows`.")
    raw = store.read_json(show.bible_path)
    v = raw.get("schema_version")
    if v == SCHEMA_VERSION:
        if not show.secret_path.is_file():
            # A lost secret: a new one keeps the show usable. Old ids no longer
            # match, so the next episode resumes by time or by alignment.
            with show.lock():
                show.write_key(store.new_key())
            return {"show": slug, "from": v, "to": v, "changed": True, "new_secret": True}
        return {"show": slug, "from": v, "to": v, "changed": False}
    if v != 1:
        bible_ops.validate(raw)  # raises the newer-schema or invalid error
    with show.lock():
        stamp = store.now().replace(":", "").replace("-", "")
        backup = show.dir / "snapshots" / f"migrate-v1-{stamp}"
        for name in ("bible.json", "cast.json", "setup.json"):
            src = show.dir / name
            if src.is_file():
                store.write_atomic(backup / name, src.read_bytes())
        if not show.secret_path.is_file():
            show.write_key(store.new_key())
        key = show.key()
        show.create_dirs()  # v1 shows have no inbox/ folder
        stored: dict[str, list[dict]] = {}
        for f in sorted(show.dir.glob("episodes/*/messages.jsonl")):
            rows = [_upgrade_message(m) for m in store.read_jsonl(f)]
            for m in rows:
                check("message", m, code="CHATCOM_E_BIBLE_INVALID", what=f"{f} has a message chatcom cannot upgrade",
                      fix=f"Restore the backup in {backup} and report this.")
            store.write_jsonl(f, rows)
            stored[f.parent.name] = rows
        for sp in sorted(show.dir.glob("episodes/*/state.json")):
            st = store.read_json(sp)
            st.setdefault("prep_cycles", 0)
            st.setdefault("accepted_flow", {"writer": st.get("writer_mode"), "reader": None})
            if st.get("window", {}).get("cursor"):
                st["window"]["cursor"] = _upgrade_cursor(st["window"]["cursor"], stored.get(sp.parent.name, []), key)
            store.write_json(sp, st)
        for bp in sorted(show.dir.glob("episodes/*/bible.next.json")):
            nxt = store.read_json(bp)
            nxt["schema_version"] = SCHEMA_VERSION
            for ep in nxt.get("episodes", []):
                ep["window"]["cursor"] = _upgrade_cursor(ep["window"]["cursor"], stored.get(ep["code"], []), key)
            store.write_json(bp, nxt)
        sheet = store.read_json(show.cast_path)
        sheet["schema_version"] = SCHEMA_VERSION
        check("cast", sheet, code="CHATCOM_E_BIBLE_INVALID", what="cast.json did not upgrade",
              fix=f"Restore the backup in {backup} and report this.")
        for ep in raw.get("episodes", []):
            ep["window"]["cursor"] = _upgrade_cursor(ep["window"]["cursor"], stored.get(ep["code"], []), key)
        raw["schema_version"] = SCHEMA_VERSION
        bible_ops.validate(raw)
        store.write_json(show.cast_path, sheet)
        store.write_json(show.bible_path, raw)  # last: the marker that migrate finished
    return {"show": slug, "from": 1, "to": SCHEMA_VERSION, "changed": True, "backup": str(backup)}
