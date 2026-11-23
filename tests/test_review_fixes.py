"""Regression tests for the pre-landing review findings and the coverage gaps
the audit named. Each test's value card says what it protects."""
import json
import os
import time
from datetime import datetime, timezone

import pytest

from chatcom import app, cli
from chatcom.adapters import store, whatsapp
from chatcom.adapters.writers import llm_fallback
from chatcom.adapters.writers.anthropic import AnthropicWriter
from chatcom.adapters.writers.base import parse_json
from chatcom.adapters.writers.replay import ReplayWriter
from chatcom.core import bundle as bundle_ops
from chatcom.core import window
from chatcom.core.errors import ChatcomError
from conftest import LAKE, TZ, WA


def run(capsys, *argv):
    code = cli.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


@pytest.fixture
def not_a_tty(monkeypatch):
    monkeypatch.setattr(cli, "_tty", lambda: False)


# ---- privacy: phone numbers and exclusions ---------------------------------

# Value: protects=a phone-number sender keeps one label across weekly exports; fails_when=labels follow order of appearance again; why_new=red-team showed an excluded person's label moving to someone else; seam=none; no_claim=none beyond protects
def test_phone_labels_are_stable_across_exports():
    week1 = "[1/2/26, 10:00:00 AM] +1 555 010 1111: a\n[1/2/26, 10:01:00 AM] +1 555 020 2222: b\n"
    week2 = "[1/9/26, 10:00:00 AM] Alice: a2\n[1/9/26, 10:01:00 AM] +1 555 020 2222: secret\n"
    b1 = whatsapp.parse(week1, TZ).masked["+1 555 020 2222"]
    r2 = whatsapp.parse(week2, TZ, aliases={"Alice": ["+1 555 010 1111"]})
    assert r2.masked["+1 555 020 2222"] == b1
    w = window.select(r2.messages, None, exclude=[b1])
    assert all("secret" not in m["text"] for m in w.messages)


# Value: protects=phone numbers in message text and number-style @mentions never reach the writer; fails_when=masking covers only the sender field; why_new=targeted and red-team reviews both reproduced the leak; seam=none; no_claim=numbers under 10 digits
def test_phone_numbers_in_text_are_masked():
    t = ("[1/2/26, 10:00:00 AM] +1 (555) 010-2233: yo\n"
         "[1/2/26, 10:01:00 AM] Dave: @15550102233 call me at +1 555 010 2233 or +44 20 7946 0958\n"
         "[1/2/26, 10:02:00 AM] Dave: flight 2026-01-15 at 9:41, gate 12\n")
    r = whatsapp.parse(t, TZ)
    texts = " ".join(m["text"] for m in r.messages)
    for digits in ("5550102233", "2079460958"):
        assert digits not in texts.replace(" ", "").replace("-", "")
    label = whatsapp.phone_label("+1 (555) 010-2233")
    assert f"@{label} call me at {label}" in texts
    assert "2026-01-15 at 9:41, gate 12" in texts  # dates and times untouched
    r2 = whatsapp.parse(t, TZ, aliases={"Mike": ["+1 (555) 010-2233"]})
    assert "@Mike call me at Mike" in " ".join(m["text"] for m in r2.messages)


# Value: protects=aliases (often phone numbers) stay out of the writer prompt; fails_when=windowed() sends cast members whole; why_new=the specialist review found 010-2233 in render_prompt; seam=none; no_claim=none beyond protects
def test_aliases_never_reach_the_prompt():
    show, _ = app.new_show(str(LAKE), {"operator_sender": "Dave", "slug": "lh",
                                       "aliases": {"Mike": ["Michael R", "+1 (555) 010-2233"]}}, tz=TZ)
    b = json.loads(show.bible_path.read_text())
    s = json.loads(show.cast_path.read_text())
    assert b["cast"][1]["aliases"]  # kept locally
    prompt = bundle_ops.render_prompt(bundle_ops.build(b, s, []))
    assert "010-2233" not in prompt and "Michael R" not in prompt


# Value: protects=@mentions of an excluded person are blanked by alias and for names ending in an emoji; fails_when=mention match uses \b or only the canonical name; why_new=both reviews reproduced the miss; seam=none; no_claim=nicknames that are not aliases
def test_exclusion_blanks_alias_and_emoji_mentions():
    msgs = [{"id": "1" * 16, "sender": "Theo", "text": "@Michael R and @Mom ❤️ and @Mike. lol", "time": "2026-01-02T10:00:00+00:00"}]
    out, _ = window.apply_exclusions(msgs, ["Mike", "Mom ❤️"], aliases={"Mike": ["Michael R"]})
    assert out[0]["text"] == "@[someone] and @[someone] and @[someone]. lol"


# Value: protects=setup.exclude reaches ingest and the notice; fails_when=app.ingest stops passing setup.exclude or notice() drops it; why_new=only the core function was tested; seam=none; no_claim=none beyond protects
def test_excluded_member_never_reaches_messages_file(capsys, chatcom_home, tmp_path, not_a_tty):
    setup = tmp_path / "setup.json"
    setup.write_text(json.dumps({"operator_sender": "Dave", "exclude": ["Sam"]}))
    assert run(capsys, "new", str(LAKE), "--setup", str(setup), "--tz", TZ, "--show", "lake-house")[0] == 0
    code, _, err = run(capsys, "ingest", "lake-house", "--export", str(LAKE))
    assert code == 6 and "Excluded before any model call: Sam" in err
    run(capsys, "ingest", "lake-house", "--export", str(LAKE), "--accept-data-flow")
    rows = (chatcom_home / "lake-house" / "episodes" / "S01E01" / "messages.jsonl").read_text()
    assert '"sender": "Sam"' not in rows and "Priya" in rows


# ---- window edges ---------------------------------------------------------

# Value: protects=--since/--until narrow the window in the show's zone, and bad ranges fail as errors; fails_when=the filters drop, naive dates use the machine zone, or bottle+until crashes; why_new=no test passed since/until; seam=none; no_claim=none beyond protects
def test_since_until_and_empty_ranges(capsys, chatcom_home, not_a_tty):
    run(capsys, "new", str(LAKE), "--operator", "Dave", "--tz", TZ, "--show", "lake-house")
    code, out, _ = run(capsys, "episode", "lake-house", "--export", str(LAKE), "--dry-run",
                       "--since", "2026-01-05T00:00", "--until", "2026-01-06T00:00")
    w = json.loads(out)["window"]
    assert code == 0 and w["from"].startswith("2026-01-05T19:45") and w["to"].endswith("-05:00")
    code, _, err = run(capsys, "episode", "lake-house", "--export", str(LAKE), "--dry-run", "--until", "2025-01-01")
    assert code == 2 and "no messages in the chosen window" in err
    with pytest.raises(SystemExit) as e:  # argparse rejects it as usage, exit 2
        cli.main(["episode", "lake-house", "--export", str(LAKE), "--dry-run", "--since", "not-a-date"])
    assert e.value.code == 2 and "ISO 8601" in capsys.readouterr().err


def test_bottle_with_until_before_every_message_is_an_error_not_a_crash():
    from conftest import LAKE as lake
    text, _ = whatsapp.read_export(lake)
    msgs = whatsapp.parse(text, TZ).messages
    cur = {"minute": "2026-01-16T23:01Z", "hash": msgs[-1]["id"]}
    with pytest.raises(ChatcomError) as e:
        window.select(msgs, cur, bottle=True, until=datetime(2025, 1, 1, tzinfo=timezone.utc))
    assert e.value.code == "CHATCOM_E_STALE_EXPORT"


# ---- state, locking, slugs --------------------------------------------------

# Value: protects=one run per show; fails_when=a live lock is ignored, a fresh empty lock is stolen, or a dead one blocks forever; why_new=Show.lock had no tests and an empty-pid race; seam=none; no_claim=two real processes racing
def test_show_lock_live_empty_and_stale(lake_show):
    lock = lake_show.dir / ".lock"
    lock.write_text(str(os.getpid()))
    with pytest.raises(ChatcomError) as e:
        with lake_show.lock():
            pass
    assert e.value.code == "CHATCOM_E_LOCKED"
    lock.write_text("")  # just created, pid not written yet: held
    with pytest.raises(ChatcomError):
        with lake_show.lock():
            pass
    old = time.time() - 60
    os.utime(lock, (old, old))  # an old empty lock is stale
    with lake_show.lock():
        assert lock.read_text() == str(os.getpid())
    lock.write_text("999999")  # a dead process
    with lake_show.lock():
        pass
    assert not lock.exists()


def test_slug_with_a_path_is_refused(capsys, not_a_tty):
    code, _, err = run(capsys, "write-prep", "../etc")
    assert code == 2 and "is not a show slug" in err


# Value: protects=a drafted episode is never overwritten by a re-ingest, and a re-ingest before drafting restarts attempts; fails_when=ingest overwrites state at drafted or keeps a spent attempt counter; why_new=app.ingest's stage check never ran; seam=none; no_claim=the future rewrite command
def test_reingest_rules(capsys, chatcom_home, beats_s01e01, not_a_tty):
    run(capsys, "new", str(LAKE), "--operator", "Dave", "--tz", TZ, "--show", "lake-house")
    run(capsys, "ingest", "lake-house", "--export", str(LAKE), "--accept-data-flow")
    bad = chatcom_home / "bad.json"
    bad.write_text("{}")
    run(capsys, "write-submit", "lake-house", "--file", str(bad), "--model", "m")
    sp = chatcom_home / "lake-house" / "episodes" / "S01E01" / "state.json"
    assert json.loads(sp.read_text())["attempts"] == 1
    run(capsys, "ingest", "lake-house", "--export", str(LAKE), "--accept-data-flow")
    assert json.loads(sp.read_text())["attempts"] == 0
    good = chatcom_home / "good.json"
    good.write_text(json.dumps(beats_s01e01))
    assert run(capsys, "write-submit", "lake-house", "--file", str(good), "--model", "m")[0] == 0
    code, _, err = run(capsys, "ingest", "lake-house", "--export", str(LAKE), "--accept-data-flow")
    assert code == 2 and "CHATCOM_E_STATE" in err and "already drafted" in err


# ---- the writers ----------------------------------------------------------

def _fake(responses):
    calls = []

    def transport(url, headers, payload):
        calls.append((url, headers, payload))
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
    return transport, calls


# Value: protects=the main CLI writer path and "a bad request is not an outage"; fails_when=a 200 is parsed wrong, or a 400 silently goes to Gemini; why_new=llm_fallback's success and 400 paths never ran; seam=none; no_claim=real network or TLS
def test_claude_success_and_400_does_not_fall_back(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x" * 12)
    monkeypatch.setenv("GEMINI_API_KEY", "y" * 12)
    t, calls = _fake([(200, {"model": "claude-sonnet-5-5", "content": [{"type": "text", "text": "{}"}]})])
    d = AnthropicWriter(transport=t).draft("b", "p")
    assert d.model == "claude-sonnet-5-5" and d.fallback_reason is None and len(calls) == 1
    t, calls = _fake([(400, {"error": "bad"})])
    with pytest.raises(ChatcomError) as e:
        AnthropicWriter(transport=t).draft("b", "p")
    assert e.value.code == "CHATCOM_E_WRITER_BAD_REQUEST" and len(calls) == 1


def test_auth_failure_and_missing_key_fall_back(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "y" * 12)
    gem = (200, {"modelVersion": "gemini-3.6-flash", "candidates": [{"content": {"parts": [{"text": "{}"}]}}]})
    t, _ = _fake([gem])  # no ANTHROPIC_API_KEY: straight to Gemini
    assert "no ANTHROPIC_API_KEY" in AnthropicWriter(transport=t).draft("b", "p").fallback_reason
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x" * 12)
    t, _ = _fake([(401, {}), gem])
    assert "auth failed" in AnthropicWriter(transport=t).draft("b", "p").fallback_reason


def test_certificate_failure_says_what_to_do(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x" * 12)
    err = llm_fallback.Unavailable("network: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
    t, _ = _fake([err])
    with pytest.raises(ChatcomError) as e:
        AnthropicWriter(transport=t).draft("b", "p")
    assert "certifi" in e.value.fix


# Value: protects=real model replies are accepted when wrapped in a fence or prose; fails_when=extraction breaks and every wrapped reply burns an attempt; why_new=base.py's fallbacks never ran; seam=none; no_claim=several JSON objects in one reply
def test_parse_json_variants():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Here it is: {"a": {"b": 2}} hope that helps') == {"a": {"b": 2}}
    assert parse_json("no json here") is None


# Value: protects=the three-attempt cap in CLI mode; fails_when=run_writer swallows GAVE_UP and loops; why_new=the re-raise never ran; seam=none; no_claim=the Anthropic transport
def test_run_writer_gives_up_after_three_invalid_drafts(capsys, chatcom_home, tmp_path, not_a_tty):
    run(capsys, "new", str(LAKE), "--operator", "Dave", "--tz", TZ, "--show", "lake-house")
    rec = tmp_path / "r.json"
    rec.write_text(json.dumps({"responses": ["nope", "{}", "still nope"]}))
    code, out, err = run(capsys, "--json", "episode", "lake-house", "--export", str(LAKE), "--writer", "replay", "--replay", str(rec))
    env = json.loads(err.strip().splitlines()[-1])
    assert code == 4 and env["code"] == "CHATCOM_E_WRITER_GAVE_UP" and env["context"]["last_draft"].endswith("attempt-3.txt")
    assert not (chatcom_home / "lake-house" / "episodes" / "S01E01" / "beats.json").exists()


# ---- operator-visible errors ------------------------------------------------

# Value: protects=each operator mistake gets its code and exit 2, never a traceback; fails_when=a code or exit status changes; why_new=these branches never ran; seam=none; no_claim=fix-line wording
def test_operator_errors(capsys, chatcom_home, tmp_path, not_a_tty):
    assert run(capsys, "new", str(LAKE), "--operator", "Dave", "--tz", TZ, "--show", "lake-house")[0] == 0
    replay = tmp_path / "replay.json"
    replay.write_text(json.dumps({"responses": [{}]}))
    cases = [
        (("new", str(LAKE), "--operator", "Dave", "--tz", TZ, "--show", "lake-house"), "CHATCOM_E_SHOW_EXISTS"),
        (("write-prep", "nope"), "CHATCOM_E_SHOW_NOT_FOUND"),
        (("write-prep", "lake-house"), "CHATCOM_E_STATE"),
        (("episode", "lake-house", "--export", str(LAKE), "--writer", "session"), "CHATCOM_E_USAGE"),
        (("episode", "lake-house", "--export", str(LAKE), "--writer", "replay"), "CHATCOM_E_USAGE"),
        (("write-submit", "lake-house", "--file", str(tmp_path / "missing.json"), "--model", "m"), "CHATCOM_E_USAGE"),
        # Coverage audit pass 2. Value: protects=a missing --export through the CLI is EXPORT_NOT_FOUND exit 2 for new
        # and episode; fails_when=registry.expand stops checking and a later step crashes or says SOURCE_UNKNOWN;
        # why_new=only whatsapp.read_export's own check is tested; seam=none; no_claim=session-mode ingest (the inbox rule runs first)
        (("new", str(tmp_path / "missing.zip"), "--operator", "Dave", "--tz", TZ, "--show", "fresh"),
         "CHATCOM_E_EXPORT_NOT_FOUND"),
        (("episode", "lake-house", "--export", str(tmp_path / "gone"), "--writer", "replay", "--replay", str(replay)),
         "CHATCOM_E_EXPORT_NOT_FOUND"),
    ]
    for argv, code_name in cases:
        code, _, err = run(capsys, *argv)
        assert code == 2 and code_name in err, (argv, err)
    run(capsys, "new", str(WA / "ios_en_GB_24h.txt"), "--operator", "Dave", "--tz", TZ, "--show", "other")
    code, _, err = run(capsys, "write-prep")
    assert code == 2 and "CHATCOM_E_SHOW_AMBIGUOUS" in err


def test_unreadable_exports(tmp_path):
    bad = tmp_path / "latin1.txt"
    bad.write_bytes("[1/2/26, 10:00:00 AM] José: olá\n".encode("latin-1"))
    with pytest.raises(ChatcomError) as e:
        whatsapp.read_export(bad)
    assert e.value.code == "CHATCOM_E_EXPORT_UNREADABLE"


def test_unexpected_error_is_logged_not_dumped(capsys, monkeypatch, not_a_tty):
    monkeypatch.setattr(cli, "cmd_shows", lambda args: 1 / 0)
    monkeypatch.setitem(cli.COMMANDS, "shows", cli.cmd_shows)
    code, _, err = run(capsys, "shows")
    assert code == 1 and "chatcom bug" in err and "traceback:" in err and "Traceback" not in err


def test_doctor_runs(capsys):
    code, out, _ = run(capsys, "doctor")
    assert "python >= 3.10" in out and "CHATCOM_HOME writable" in out and code in (0, 3)


def test_replay_writer_reads_a_single_response(tmp_path):
    p = tmp_path / "one.json"
    p.write_text(json.dumps({"schema_version": 1}))
    assert ReplayWriter(p).draft("", "").text == '{"schema_version": 1}'


def test_store_writes_are_atomic_and_private(chatcom_home):
    target = store.home() / "x" / "f.json"
    store.write_json(target, {"a": 1})
    assert json.loads(target.read_text()) == {"a": 1}
    assert not list(target.parent.glob(".f.json.*.tmp"))
    assert oct(os.stat(store.home()).st_mode & 0o777) == "0o700"


# Value: protects=masking leaves dates, times, lists, URL ids and long account numbers alone; fails_when=the pattern widens back to any 10-digit run; why_new=the fix-check saw dates become 'Unnamed' people; seam=none; no_claim=every national phone format
@pytest.mark.parametrize("text", ["meet 2024-01-15 10:30", "meet 22.11.2026 10:30", "pi 3.1416",
                                  "see https://x.com/status/1876543210987654321", "at 10:30", "call 911",
                                  "code 12345", "season 2024-2025", "price 1.250.000", "score 3-1"])
def test_masking_spares_non_phone_numbers(text):
    assert whatsapp.mask_numbers(text, {}) == text


def test_garbage_lock_is_reclaimed_when_old(lake_show):
    lock = lake_show.dir / ".lock"
    lock.write_text("garbage")
    old = time.time() - 3600
    os.utime(lock, (old, old))
    with lake_show.lock():
        pass


def test_bottle_with_empty_range_points_at_until():
    text, _ = whatsapp.read_export(LAKE)
    msgs = whatsapp.parse(text, TZ).messages
    with pytest.raises(ChatcomError) as e:
        window.select(msgs, {"minute": "2026-01-16T23:01Z", "hash": msgs[-1]["id"]}, bottle=True,
                      until=datetime(2025, 1, 1, tzinfo=timezone.utc))
    assert "--since/--until" in e.value.fix


def test_same_number_with_or_without_country_code_is_one_label():
    assert whatsapp.mask_numbers("555.010.2233", {}) == whatsapp.mask_numbers("+1 (555) 010-2233", {})


def test_nine_digit_numbers_are_masked_decimals_are_not():
    out = whatsapp.mask_numbers("call 612 34 56 78, 612345678 or +34 612 345 678, pi is 3.1416", {})
    assert "612" not in out and "3.1416" in out


# Value: protects=a member quiet in episode 1 or new since can speak as chorus and is added to the bible; fails_when=chorus comes only from episode 1's window and new senders burn writer attempts; why_new=adversarial review; seam=none; no_claim=casting new members
def test_quiet_and_new_members_speak_as_chorus(beats_s01e01):
    import copy
    from chatcom.core import beats as beats_ops, bible as bible_ops
    show, info = app.new_show(str(LAKE), {"operator_sender": "Dave", "slug": "lh2", "cast": ["Dave", "Mike", "Theo", "Priya"]}, tz=TZ)
    assert "Sam" in info["chorus"]
    b = json.loads(show.bible_path.read_text())
    sheet = json.loads(show.cast_path.read_text())
    msgs = [{"sender": "Zoe", "text": "hi", "id": "z" * 16, "time": "2026-01-16T18:00:00-05:00"}]
    assert bible_ops.new_speakers(b, msgs) == ["Zoe"]
    d = copy.deepcopy(beats_s01e01)
    d["credits"] = [c for c in d["credits"] if c["name"] != "Sam"]
    for sc in [d["cold_open"]] + d["scenes"]:
        for bub in sc["bubbles"]:
            if bub["speaker"] == "Sam":
                bub["pose"] = None
    d["bible_update"]["cast"] = [c for c in d["bible_update"]["cast"] if c["name"] != "Sam"]
    d["scenes"][2]["bubbles"][1] = {"speaker": "Zoe", "text": "me", "pose": None, "sticker": None}
    assert beats_ops.validate(d, bible=b, cast_sheet=sheet, new_messages=1, extra_chorus=["Zoe"]) == []
    nxt = bible_ops.apply_beats(b, d, window_entry={"from": "2026-01-01T00:00:00+00:00", "to": "2026-01-02T00:00:00+00:00",
                                "cursor": {"minute": "2026-01-02T00:00Z", "hash": "a" * 16, "adapter": "whatsapp", "precision": "minute", "signature": {"raw": [], "canonical": []}}, "message_count": 1, "gap": None},
                                writer={"mode": "replay", "model": "r", "attempts": 1}, extra_chorus=["Zoe"])
    assert "Zoe" in nxt["chorus"]


def test_claude_billing_400_falls_back_and_bad_json_is_unavailable(monkeypatch):
    import http.client
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x" * 12)
    monkeypatch.setenv("GEMINI_API_KEY", "y" * 12)
    gem = (200, {"modelVersion": "gemini-3.6-flash", "candidates": [{"content": {"parts": [{"text": "{}"}]}}]})
    t, calls = _fake([(400, {"error": {"message": "Your credit balance is too low"}}), gem])
    d = AnthropicWriter(transport=t).draft("b", "p")
    assert d.model == "gemini-3.6-flash" and "account problem" in d.fallback_reason and len(calls) == 2
    t, _ = _fake([ValueError("Expecting value")])
    with pytest.raises(ChatcomError) as e:
        AnthropicWriter(transport=t).draft("b", "p")
    assert e.value.code == "CHATCOM_E_WRITER_UNAVAILABLE"
    t, _ = _fake([http.client.IncompleteRead(b"")])
    with pytest.raises(ChatcomError):
        AnthropicWriter(transport=t).draft("b", "p")


def test_episode_100_fits_the_schema():
    from chatcom.core.schema import schema
    import re as _re
    assert _re.match(schema("beats")["properties"]["episode"]["pattern"], "S01E100")


def test_corrupt_zip_member_is_a_coded_error(tmp_path):
    import zipfile
    p = tmp_path / "c.zip"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("_chat.txt", "[1/2/26, 10:00:00 AM] Dave: " + "hi " * 200)
    raw = bytearray(p.read_bytes())
    i = raw.find(b"_chat.txt") + 40
    raw[i:i + 20] = b"\x00" * 20  # break the compressed stream
    p.write_bytes(bytes(raw))
    with pytest.raises(ChatcomError) as e:
        whatsapp.read_export(p)
    assert e.value.code in ("CHATCOM_E_EXPORT_ZIP", "CHATCOM_E_EXPORT_UNREADABLE")


# Value: protects=masking fails closed: a phone followed by ':', after '=', next to another phone, or with trailing digits is still masked; fails_when=a spare rule widens and lets a number through; why_new=fix-check round 2 found these leaks; seam=none; no_claim=every national format
@pytest.mark.parametrize("text", ["my cell 555-010-2233: text me", "phone=5550102233", "5550102233 5550104455",
                                  "555-010-2233 2 3", "+34 612 345 678", "612 34 56 78"])
def test_masking_fails_closed(text):
    out = whatsapp.mask_numbers(text, {})
    assert "Unnamed" in out
    for d in ("0102233", "0104455", "612345678", "5550102233"):
        assert d not in out.replace(" ", "").replace("-", "")


# Value: protects=every chatcom command the skill tells a session to run parses with the real CLI; fails_when=a flag is renamed or made required and SKILL.md is not updated; why_new=fix-check found `episode --writer anthropic` without the required --export; seam=none; no_claim=the commands' runtime behavior
def test_every_skill_command_parses():
    import re as _re
    from pathlib import Path
    text = (Path(__file__).resolve().parent.parent / "skills" / "chatcom" / "SKILL.md").read_text("utf-8")
    src = Path(__file__).resolve().parent.parent / "src" / "chatcom"
    for f in ("core/state.py", "app.py", "cli.py", "adapters/whatsapp.py", "core/window.py"):
        text += (src / f).read_text("utf-8")
    cmds = {c for c in _re.findall(r"`chatcom ([^`]+)`", text) if not c.startswith("rollback")}
    assert len(cmds) >= 7
    fill = {"<show>": "lake-house", "<export>": "x.zip", "<beats.json path>": "b.json", "<your model name>": "m",
            "\"<their name in the chat>\"": "Dave", "\"<name>\"": "Sam",
            "<sitcom|anime|cartoon80s|truecrime|cutscene|facetime|flipphone|reality>": "sitcom",
            "{show.slug}": "lake-house", "{slug}": "lake-house", "<file>": "x.zip", "F": "x.zip",
            "<export.zip|export.txt>": "x.zip", "<export>": "x.zip"}
    parser = cli.build_parser()
    for c in cmds:
        for k, v in fill.items():
            c = c.replace(k, v)
        argv = c.split()
        if argv == ["--version"]:
            continue
        parser.parse_args(argv)  # SystemExit here means the skill names a command that cannot run


# Value: protects=no phone format the final fix-check tried leaks (slashes, italics, wa.me and tel: links, spaced pairs, one-dot, en dash, narrow spaces, glued letters); fails_when=masking goes back to a pattern with spare rules; why_new=the final fix-check's probe list; seam=none; no_claim=formats not listed
LEAK_PROBES = ["ruf an: 0151/12345678", "Tel. 030/1234567", "+49 151/12345678", "06/12/34/56/78", "_5550102233_",
               "_+1 555 010 2233_", "https://wa.me/15550102233", "555 010 2233 555 010 4455",
               "06 12 34 56 78 06 98 76 54 32", "0151.12345678", "0612.345678", "5550.102233", "11 91234\u20135678",
               "+1\u202f555\u202f010\u202f2233", "Telefon5550102233", "N\u00ba5550102233", "5550102233x",
               "my cell 555-010-2233: text me", "phone=5550102233", "612345678", "tel:+15550102233", "@15550102233 hi",
               # round 5: exotic separators, other apps' phone links, numbers glued to links, local numbers
               "555\u2009010\u20092233", "555\u2002010\u20022233", "555\u3000010\u30002233", "555\u2212010\u22122233",
               "555\u00b7010\u00b72233", "https://t.me/+15550102233", "https://signal.me/#p/+15550102233",
               "viber://chat?number=%2B15550102233", "facetime://+15550102233", "https://maps.google.com/?q=+15550102233",
               "see https://example.com/,+15550102233", "www.example.de/030-12345678", "call 555-0102",
               "liga no 3456-7890", "Durchwahl 12345678", "34.612345678", "1.8005550199", "12.345678901234567890"]


@pytest.mark.parametrize("text", LEAK_PROBES)
def test_no_probe_leaks(text):
    import re as _re
    out = whatsapp.mask_numbers(text, {})
    rest = _re.sub(r"Unnamed [0-9a-f]{6}", "", out)
    assert not _re.search(r"\d{7,}", _re.sub(r"\D", "", rest)), out



# Value: protects=no message text can crash masking (it runs on every message); fails_when=a placeholder or index scheme comes back; why_new=fix-check round 5 crashed it with NUL characters; seam=none; no_claim=none beyond protects
@pytest.mark.parametrize("text", ["x\x00y\x00 z", "\x00a\x00", "\x00\ue000\x00 https://a.com/1", "\x00\U0010ffff\x00", "\x00" * 5])
def test_masking_never_crashes(text):
    assert whatsapp.mask_numbers(text, {}) == text


def test_national_number_matches_its_country_code_alias():
    amap = whatsapp._alias_map({"Lucia": ["+34 612 34 56 78"]})
    assert whatsapp.mask_numbers("llama al 612 34 56 78", amap) == "llama al Lucia"


@pytest.mark.parametrize("text", ["flight 2026-01-15 at 9:41", "on 15.01.2026", "on 15/01/26", "on 1/15/2026 10:30"])
def test_dates_of_any_length_are_not_masked(text):
    assert whatsapp.mask_numbers(text, {}) == text


# Value: protects=a corrupt .zip given to `new` is a coded exit-2 error, never a traceback; fails_when=sniffing lets
# zipfile.BadZipFile escape (it is not an OSError or ValueError); why_new=test_missing_file_and_bad_zip calls
# read_export directly, which the registry now runs after sniffing; seam=none;
# no_claim=which of the two codes is right (base gave EXPORT_ZIP)
# Regression test, generated by the /ship coverage audit.
def test_corrupt_zip_on_the_command_line_is_a_coded_error(capsys, tmp_path, not_a_tty):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    code, _, err = run(capsys, "--json", "new", str(bad), "--operator", "Dave", "--tz", TZ, "--show", "z")
    assert code == 2, err
    assert json.loads(err.strip().splitlines()[-1])["code"] == "CHATCOM_E_EXPORT_ZIP"
