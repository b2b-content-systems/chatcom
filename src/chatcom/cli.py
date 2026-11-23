"""chatcom command line. Exit codes: 0 success, 2 usage or setup, 3 preflight,
4 provider, 5 render or encode, 6 awaiting confirmation. The last stdout line
of a command that makes a file is that file's absolute path."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from chatcom import __version__, app
from chatcom.core.errors import ChatcomError, redact


def _local_tz() -> str:
    if os.environ.get("TZ") and "/" in os.environ["TZ"]:
        return os.environ["TZ"].lstrip(":")
    try:
        target = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in target:
            return target.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    return "UTC"


def _date(s: str) -> datetime:
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an ISO 8601 date or time: {s!r}") from None
    return dt  # without an offset, the show's timezone applies (app._aware)


def _day(s: str):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD: {s!r}") from None


def _speaker(s: str) -> tuple[str, str]:
    if "=" not in s:
        raise argparse.ArgumentTypeError("expected LABEL=NAME, for example A=Mike or right=Dave")
    label, name = (x.strip() for x in s.split("=", 1))
    if not label or not name:
        raise argparse.ArgumentTypeError("expected LABEL=NAME, for example A=Mike or right=Dave")
    return label, name


def _shot_flags(p: argparse.ArgumentParser, *, reading: bool) -> None:
    """Screenshot flags. `reading`: the command reads images in CLI mode."""
    p.add_argument("--speaker", action="append", type=_speaker, default=[], metavar="LABEL=NAME",
                   help="screenshots: name a label for this import (A=Mike), or your own bubbles (right=Dave)")
    p.add_argument("--max-images", type=int, default=40, help="screenshots: cap on images or tiles (default 40)")
    if reading:
        order = p.add_mutually_exclusive_group()
        order.add_argument("--reverse", dest="reverse", action="store_const", const=True, default=None,
                           help="screenshots: the images are newest first")
        order.add_argument("--no-reverse", dest="reverse", action="store_const", const=False,
                           help="screenshots: the images are oldest first")
        p.add_argument("--anchor-date", type=_day, help="screenshots: the date the first screenshot shows (YYYY-MM-DD)")
        p.add_argument("--replay-reads", help="screenshots: recorded reads (with --writer replay)")


def _shots(args) -> dict:
    return {"speakers": dict(args.speaker), "max_images": args.max_images,
            "reverse": getattr(args, "reverse", None), "anchor": getattr(args, "anchor_date", None),
            "replay_reads": getattr(args, "replay_reads", None)}


def _pair(s: str) -> tuple[str, str]:
    if "=" not in s:
        raise argparse.ArgumentTypeError("expected NAME=PATH")
    name, path = s.rsplit("=", 1)
    return name, path


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="chatcom", description="Turn your group chat into your favorite TV series.")
    p.add_argument("--version", action="version", version=f"chatcom {__version__}")
    p.add_argument("--json", action="store_true",
                   help="machine output: results as JSON on stdout, errors as a JSON envelope on stderr")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("new", help="create a show from a chat export and run setup")
    s.add_argument("export", nargs="*")
    s.add_argument("--source", help="force the source adapter (whatsapp, telegram, transcript, screenshots)")
    s.add_argument("--thread", help="pick one chat in a multi-chat export")
    s.add_argument("--setup", help="setup.json with the first-run answers")
    s.add_argument("--show", dest="slug")
    s.add_argument("--title")
    s.add_argument("--look", choices=["sitcom", "anime", "cartoon80s", "truecrime", "cutscene", "facetime", "flipphone", "reality"])
    s.add_argument("--operator")
    s.add_argument("--cast", action="append", default=None, help="a cast member (repeat)")
    s.add_argument("--photo", action="append", type=_pair, default=[], metavar="NAME=PATH")
    s.add_argument("--exclude", action="append", default=[])
    s.add_argument("--no-photo", action="append", default=[])
    s.add_argument("--no-cast", action="append", default=[])
    s.add_argument("--tz")
    s.add_argument("--max-unparsed", type=float, default=app.MAX_UNPARSED)
    s.add_argument("--print-setup-template", action="store_true")
    s.add_argument("--accept-data-flow", action="store_true",
                   help="screenshots: the operator has read the data-flow notice and agrees")
    s.add_argument("--writer", choices=["anthropic", "replay", "session"], default="anthropic",
                   help="screenshots: the mode, which picks the reader and the notice")
    _shot_flags(s, reading=True)

    for name, hlp in (("ingest", "(skill mode) step 1 prints the data-flow notice and exits 6; step 2 hands over messages"),
                      ("episode", "make this week's episode (this release stops after the writer: beats.json)")):
        e = sub.add_parser(name, help=hlp)
        e.add_argument("show", nargs="?")
        e.add_argument("--export", required=True, nargs="+", help="the export file, a folder, or several image files")
        e.add_argument("--source", help="force the source adapter (whatsapp, telegram, transcript, screenshots)")
        e.add_argument("--thread", help="pick one chat in a multi-chat export")
        e.add_argument("--since", type=_date)
        e.add_argument("--until", type=_date)
        e.add_argument("--bottle", action="store_true")
        e.add_argument("--max-days", type=int, default=14)
        e.add_argument("--max-unparsed", type=float, default=app.MAX_UNPARSED)
        e.add_argument("--accept-data-flow", action="store_true",
                       help="the operator has read the data-flow notice and agrees")
        e.add_argument("--replace", action="store_true",
                       help="screenshots: start over an import that stopped at copied or read")
        _shot_flags(e, reading=name == "episode")
        if name != "ingest":
            e.add_argument("--writer", choices=["anthropic", "replay", "session"], default="anthropic")
            e.add_argument("--writer-model", default=os.environ.get("CHATCOM_WRITER_MODEL"))
            e.add_argument("--replay", help="recorded writer responses (with --writer replay)")
            e.add_argument("--dry-run", action="store_true")
            e.add_argument("--token-budget", type=int, default=12_000)
            e.add_argument("--review", action="store_true",
                           help="screenshots: stop after transcript.txt so you can check it, then import that file")

    r = sub.add_parser("read-prep", help="(skill mode) hand out the next screenshots to read; prints bundle.json")
    r.add_argument("show", nargs="?")
    r.add_argument("--import", dest="import_id", help="a pending import from `chatcom new` (no show yet)")
    r = sub.add_parser("read-submit", help="(skill mode) check the session's reader.json; after the last batch, join")
    r.add_argument("show", nargs="?")
    r.add_argument("--import", dest="import_id", help="a pending import from `chatcom new` (no show yet)")
    r.add_argument("--file", required=True)
    r.add_argument("--model", required=True, help="the model that read the screenshots, as the session reports it")
    r.add_argument("--review", action="store_true", help="stop after transcript.txt so the operator can check it")
    _shot_flags(r, reading=True)

    w = sub.add_parser("write-prep", help="(skill mode) write the writer bundle and print its path")
    w.add_argument("show", nargs="?")
    w.add_argument("--token-budget", type=int, default=12_000)

    w = sub.add_parser("write-submit", help="(skill mode) validate the session's beats.json")
    w.add_argument("show", nargs="?")
    w.add_argument("--file", required=True)
    w.add_argument("--model", required=True, help="the model that wrote the draft, as the session reports it")

    st = sub.add_parser("status", help="where this week's episode stands, and the next command")
    st.add_argument("show", nargs="?")
    mg = sub.add_parser("migrate", help="upgrade a show made by an older chatcom (backs it up first)")
    mg.add_argument("show", nargs="?")
    sub.add_parser("shows", help="list shows")
    sub.add_parser("doctor", help="check this computer for what chatcom needs")
    return p


def _tty() -> bool:
    return sys.stdin.isatty()


def emit(args, data: dict, lines: list[str]) -> None:
    """Human lines (the last one is the artifact path when there is one), or
    one JSON object with --json."""
    if getattr(args, "json", False):
        print(json.dumps(data, ensure_ascii=False, default=str))
    else:
        for ln in lines:
            print(ln)


def _setup_from(args) -> dict:
    setup: dict = {}
    if args.setup:
        try:
            setup = json.loads(Path(args.setup).expanduser().read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise ChatcomError("CHATCOM_E_SETUP_INVALID", f"cannot read {args.setup}: {e}",
                               "Pass a JSON file; print a template with `chatcom new --print-setup-template`.") from None
        setup = {k: v for k, v in setup.items() if not k.startswith("_")}
    flags = {"slug": args.slug, "title": args.title, "look": args.look, "operator_sender": args.operator,
             "timezone": args.tz, "cast": args.cast}
    setup.update({k: v for k, v in flags.items() if v})
    if args.photo:
        setup["photos"] = {**setup.get("photos", {}), **dict(args.photo)}
    for key, val in (("exclude", args.exclude), ("no_photo", args.no_photo), ("no_cast", args.no_cast)):
        if val:
            setup[key] = setup.get(key, []) + val
    return setup


def cmd_new(args) -> int:
    if args.print_setup_template:
        print(json.dumps(app.setup_template(), indent=2))
        return 0
    if not args.export:
        raise ChatcomError("CHATCOM_E_USAGE", "no export given", "Run `chatcom new <export.zip|export.txt|folder>`.")
    setup = _setup_from(args)
    if "operator_sender" not in setup:
        if not _tty():
            raise ChatcomError("CHATCOM_E_NO_TERMINAL", "setup needs your sender name and there is no terminal to ask",
                               "Pass --operator NAME (and other setup flags), or --setup setup.json.")
        setup["operator_sender"] = input("Your name as it appears in the chat: ").strip()
        if "title" not in setup:
            t = input("Show title (Enter to let the writer name it): ").strip()
            if t:
                setup["title"] = t
    show, info = app.new_show(args.export, setup, tz=setup.get("timezone") or _local_tz(), max_unparsed=args.max_unparsed,
                              source=args.source, thread=args.thread, accepted=args.accept_data_flow,
                              writer=args.writer, shots=_shots(args))
    if info.get("pending"):  # skill mode with screenshots: read them first, then the transcript makes the show
        emit(args, info, [f"{info['images']} screenshots copied for reading (import {info['import']})",
                          f"next: {info['next_command']}"])
        return 0
    fmt = ", ".join(str(v) for v in info["format"].values() if v)
    lines = [f"show '{info['slug']}' created from {info['messages']} messages "
             f"({info['source']}: {fmt}; {info['unparsed_rate']:.1%} of lines unparsed)",
             f"cast: {', '.join(info['cast'])}"]
    if info["chorus"]:
        lines.append(f"chorus: {', '.join(info['chorus'])}")
    if info["masked"]:
        lines.append(f"{info['masked']} sender(s) shown only as a phone number are masked as 'Unnamed xxxxxx'; "
                     "add them to aliases in setup.json to name them")
    lines.append(f"next week's export goes in {info['inbox']} (or the same path as this one)")
    lines.append(str(show.bible_path))
    emit(args, {**info, "bible": str(show.bible_path)}, lines)
    return 0


def cmd_ingest(args) -> int:
    show = app.resolve_show(args.show)
    path, win, st = app.ingest(show, args.export, accepted=args.accept_data_flow, mode="session", since=args.since,
                               until=args.until, bottle=args.bottle, max_days=args.max_days,
                               max_unparsed=args.max_unparsed, source=args.source, thread=args.thread,
                               replace=args.replace, shots=_shots(args))
    if win is None:  # screenshots: copied for the session to read
        nxt = app.state_ops.next_command(st, show.slug)
        emit(args, {"episode": st["episode"], "stage": st["stage"], "images": len(st["reader"]["copies"]),
                    "next_command": nxt},
             [f"{st['episode']}: {len(st['reader']['copies'])} screenshot copies ready to read", f"next: {nxt}"])
        return 0
    lines = [f"{st['episode']}: {len(win.messages)} messages from {win.start:%Y-%m-%d %H:%M} to {win.end:%Y-%m-%d %H:%M} "
             f"(source {st['source']}, resumed by {win.method}; {st['unparsed_rate']:.1%} of lines unparsed)"]
    if win.gap:
        lines.append(f"skipped {win.gap['skipped']} older unread messages (over the {args.max_days}-day cap)")
    if win.warning:
        print(f"warning {win.warning['code']}: {win.warning['what']} ({win.warning['from']} to {win.warning['to']}). "
              f"fix: {win.warning['fix']}", file=sys.stderr)
    lines += [f"next: chatcom write-prep {show.slug}", str(path)]
    emit(args, {"episode": st["episode"], "stage": st["stage"], "messages": len(win.messages), "source": st["source"],
                "resume": win.method, "warning": win.warning, "gap": win.gap, "messages_path": str(path),
                "next_command": f"chatcom write-prep {show.slug}"}, lines)
    return 0


def cmd_episode(args) -> int:
    show = app.resolve_show(args.show)
    if args.dry_run:
        info = app.dry_run(show, args.export, since=args.since, until=args.until, bottle=args.bottle,
                           max_days=args.max_days, max_unparsed=args.max_unparsed, token_budget=args.token_budget,
                           source=args.source, thread=args.thread, operator_typed=_tty(),
                           max_images=args.max_images)
        print(json.dumps(info, indent=2, ensure_ascii=False))
        return 0
    if args.writer == "session":
        raise ChatcomError("CHATCOM_E_USAGE", "the session writer runs through the skill, not `episode`",
                           f"Use `chatcom ingest {show.slug} --export F`, then write-prep and write-submit.")
    if args.writer == "replay":
        if not args.replay:
            raise ChatcomError("CHATCOM_E_USAGE", "--writer replay needs --replay FILE", "Pass the recorded responses file.")
        from chatcom.adapters.writers.replay import ReplayWriter
        writer = ReplayWriter(args.replay)
        confirmed = True
    else:
        images = app.source_of(args.export, args.source) == "screenshots"
        if images:
            app.shot_context(_shots(args), "anthropic", "", Path("."))  # refuses until the CLI reader exists
        from chatcom.adapters.writers.anthropic import AnthropicWriter
        writer = AnthropicWriter(model=args.writer_model)
        confirmed = args.accept_data_flow
        if not confirmed:
            setup = app.store.read_json(show.setup_path) if show.setup_path.exists() else {}
            text = app.notice("anthropic", setup.get("exclude", []))
            if not _tty():
                raise ChatcomError("CHATCOM_E_AWAITING_CONFIRMATION", text,
                                   "Ask the operator. If they agree, run the same command with --accept-data-flow.",
                                   recovery="ask", context={"mode": "anthropic", "notice": text})
            print(text, file=sys.stderr)
            confirmed = input("Continue? [y/N] ").strip().lower() in ("y", "yes")
            if not confirmed:
                print("Stopped. Nothing left this computer.", file=sys.stderr)
                return 0
    path, win, st = app.ingest(show, args.export, accepted=True, mode=writer.mode, since=args.since,
                               until=args.until, bottle=args.bottle, max_days=args.max_days,
                               max_unparsed=args.max_unparsed, source=args.source, thread=args.thread,
                               replace=args.replace, review=args.review, shots=_shots(args))
    if win is None:  # --review: stopped after transcript.txt
        nxt = app.state_ops.next_command(st, show.slug)
        emit(args, {"episode": st["episode"], "stage": st["stage"], "transcript": str(path), "next_command": nxt},
             [f"{st['episode']}: screenshots read; check the transcript, fix any line, then run:", f"  {nxt}",
              str(path)])
        return 0
    if win.warning:
        print(f"warning {win.warning['code']}: {win.warning['what']}. fix: {win.warning['fix']}", file=sys.stderr)
    res = app.run_writer(show, writer, token_budget=args.token_budget)
    # Writer calls are billed only in CLI mode with your key; replay costs nothing.
    billed = res["attempts"] if writer.mode == "anthropic" else 0
    emit(args, {"episode_path": res["beats"], "stage": "drafted", "model": res["model"], "calls_billed": billed,
                "attempts": res["attempts"], "bible_next": res["bible_next"], "source": win_source(show, res)},
         [f"{res['episode']} drafted by {res['model']} in {res['attempts']} attempt(s) "
          f"(source {win_source(show, res)}, resumed by {win.method})",
          f"bible after this episode (applied when the episode is committed): {res['bible_next']}",
          "render and commit arrive with Next Step 4", res["beats"]])
    return 0


def win_source(show, res) -> str:
    st = app.store.read_json(show.state_path(res["episode"]))
    return st.get("source") or "unknown"


def cmd_write_prep(args) -> int:
    show = app.resolve_show(args.show)
    path = app.write_prep(show, token_budget=args.token_budget)
    emit(args, {"bundle": str(path)}, [str(path)])
    return 0


def cmd_write_submit(args) -> int:
    show = app.resolve_show(args.show)
    p = Path(args.file).expanduser()
    try:
        doc = json.loads(p.read_text("utf-8"))
    except FileNotFoundError:
        raise ChatcomError("CHATCOM_E_USAGE", f"no file at {p}", "Write beats.json first, then submit it.") from None
    except json.JSONDecodeError as e:
        doc = None
        print(f"warning: {p.name} is not JSON (line {e.lineno}: {e.msg})", file=sys.stderr)
    res = app.submit(show, doc, model=args.model, mode="session", draft_path=p)
    emit(args, {"episode_path": res["beats"], "stage": "drafted", "model": res["model"], "calls_billed": 0,
                "attempts": res["attempts"]},
         [f"{res['episode']} drafted ({res['attempts']} attempt(s), model reported: {res['model']})", res["beats"]])
    return 0


def _show_or_import(args):
    if args.import_id and args.show:
        raise ChatcomError("CHATCOM_E_USAGE", "pass a show or --import, not both",
                           "A pending import from `chatcom new` has no show yet.")
    return None if args.import_id else app.resolve_show(args.show)


def cmd_read_prep(args) -> int:
    path = app.read_prep(_show_or_import(args), args.import_id)
    emit(args, {"bundle": str(path)}, [str(path)])
    return 0


def cmd_read_submit(args) -> int:
    show = _show_or_import(args)
    p = Path(args.file).expanduser()
    try:
        doc = json.loads(p.read_text("utf-8"))
    except FileNotFoundError:
        raise ChatcomError("CHATCOM_E_USAGE", f"no file at {p}", "Write reader.json first, then submit it.") from None
    except json.JSONDecodeError as e:
        doc = None
        print(f"warning: {p.name} is not JSON (line {e.lineno}: {e.msg})", file=sys.stderr)
    res = app.read_submit(show, doc, model=args.model, opts=_shots(args), import_id=args.import_id,
                          review=args.review, draft=p)
    if not res["done"]:
        lines = [f"read {res['read']} of {res['images']} screenshots", f"next: {res['next_command']}"]
    elif res["stage"] == "read":
        lines = [f"all {res['images']} screenshots read and joined: {res['transcript']}",
                 "check the transcript (fix any line), then run:", f"  {res['next_command']}", res["transcript"]]
    else:
        lines = [f"all {res['images']} screenshots read; {res['messages']} messages ingested "
                 f"(resumed by {res['resume']})", f"next: {res['next_command']}", res["messages_path"]]
    emit(args, res, lines)
    return 0


def cmd_status(args) -> int:
    info = app.status(app.resolve_show(args.show))
    lock = f"held by process {info['lock']['pid']} since {info['lock']['since']}" if info["lock"] else "free"
    emit(args, info, [f"{info['show']} {info['episode']}: {info['stage']}"
                      f" (attempts {info['attempts']}, prep cycle {info['prep_cycles']}, source {info['source'] or '-'})",
                      f"lock: {lock}", f"next: {info['next']}"])
    return 0


def cmd_migrate(args) -> int:
    info = app.migrate(args.show)
    if info.get("new_secret"):
        emit(args, info, [f"{info['show']} had no secret file; made a new one. Message ids change, so the next "
                          "episode resumes by time or by matching the last rows."])
    elif not info["changed"]:
        emit(args, info, [f"{info['show']} is already schema_version {info['to']}; nothing to do"])
    else:
        emit(args, info, [f"{info['show']} upgraded from schema_version {info['from']} to {info['to']}",
                          f"backup: {info['backup']}"])
    return 0


def cmd_shows(args) -> int:
    rows = app.show_list()
    if not rows:
        print("no shows yet: chatcom new <export>")
    for r in rows:
        print(f"{r['slug']}\t{r['title'] or '(untitled)'}\tnext {r['next']}\tcast {r['cast']}")
    return 0


def cmd_doctor(args) -> int:
    ok = True
    for name, good, detail in app.doctor():
        print(f"{'ok ' if good else '-- '} {name}: {detail}")
        ok &= good or "not needed yet" in detail or "not set;" in detail
    return 0 if ok else 3


COMMANDS = {"new": cmd_new, "ingest": cmd_ingest, "episode": cmd_episode, "write-prep": cmd_write_prep,
            "write-submit": cmd_write_submit, "read-prep": cmd_read_prep, "read-submit": cmd_read_submit, "status": cmd_status, "migrate": cmd_migrate, "shows": cmd_shows,
            "doctor": cmd_doctor}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return COMMANDS[args.cmd](args)
    except ChatcomError as e:
        if args.json:
            if e.code == "CHATCOM_E_AWAITING_CONFIRMATION":
                import shlex
                argv_now = argv if argv is not None else sys.argv[1:]
                nxt = e.context.get("next_command")
                if not nxt:
                    nxt = "chatcom " + shlex.join(argv_now)
                    if "--accept-data-flow" not in argv_now:
                        nxt += " --accept-data-flow"
                print(json.dumps({"notice_text": e.context.get("notice", e.what),
                                  "data_flow": e.context.get("mode"), "next_command": nxt}, ensure_ascii=False))
            print(json.dumps(e.envelope(), ensure_ascii=False), file=sys.stderr)
        else:
            print(e.human(), file=sys.stderr)
            probs = (e.context.get("problems") or e.context.get("errors")) if isinstance(e.context, dict) else None
            if probs and e.code in ("CHATCOM_E_BEATS_INVALID", "CHATCOM_E_WRITER_GAVE_UP"):
                print(json.dumps(probs, ensure_ascii=False, indent=1), file=sys.stderr)
        return e.exit_code
    except KeyboardInterrupt:
        print("stopped", file=sys.stderr)
        return 130
    except Exception as e:  # unexpected: a chatcom bug. Never print a key.
        import traceback
        from chatcom.adapters import store
        try:
            log = store.RunLog(None, f"crash in {args.cmd}")
            log.write(traceback.format_exc())
            where = f" (traceback: {log.path})"
        except OSError:
            where = ""
        print(redact(f"unexpected error: {type(e).__name__}: {e}{where}. This is a chatcom bug; please report it."),
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
