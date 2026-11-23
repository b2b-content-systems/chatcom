"""Step 2.5 PR 1: schema v2, keyed ids, shared masking, the source registry,
resume_point, the session-mode path rule, status and migrate."""
import json
import os
import re
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from chatcom import app, cli
from chatcom.adapters import registry, store, whatsapp
from chatcom.core import masking, resume, window
from chatcom.core.errors import ChatcomError
from chatcom.core.messages import context_hash, identity_text, message_hash
from chatcom.core.schema import problems
from conftest import LAKE, ROOT, TZ, WA


def run(capsys, *argv):
    code = cli.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


@pytest.fixture
def not_a_tty(monkeypatch):
    monkeypatch.setattr(cli, "_tty", lambda: False)


# ---- WhatsApp regression ---------------------------------------------------
# Value: protects=the shipped WhatsApp parse; fails_when=the v2 refactor changes any parsed field;
# why_new=ids change under HMAC so the old id assertions cannot catch it; seam=none;
# no_claim=formats outside the fixture matrix

def test_whatsapp_parse_is_unchanged_from_v1_except_ids():
    golden = json.loads((WA / "golden-v1-parse.json").read_text("utf-8"))
    for name, want in golden.items():
        path = LAKE if name == "_chat.txt" else WA / name
        text, _ = whatsapp.read_export(path)
        r = whatsapp.parse(text, TZ, aliases={"Mike": ["+1 (555) 010-2233"]})
        rows = []
        for m in r.messages:
            assert m["time_precision"] == "minute" and m["quote"] is None and m["source"]["label"]
            assert not problems("message", m), (name, problems("message", m))
            m = {k: v for k, v in m.items() if k not in ("id", "time_precision", "quote")}
            m["source"] = {k: v for k, v in m["source"].items() if k != "label"}
            m["sender"] = "Unnamed" if m["sender"].startswith("Unnamed ") else m["sender"]
            m["text"] = re.sub(r"Unnamed [0-9a-f]{6}", "Unnamed", m["text"])
            rows.append(m)
        assert rows == want["messages"], name
        assert (r.system_lines, len(r.unparsed), r.fmt) == (want["system"], want["unparsed"], want["fmt"]), name


def test_whatsapp_still_reexports_masking():
    assert whatsapp.mask_numbers is masking.mask_numbers and whatsapp.phone_label is masking.phone_label


# ---- keyed ids --------------------------------------------------------------

def test_two_shows_give_different_ids_and_labels_for_the_same_chat(chatcom_home):
    a, _ = app.new_show(str(WA / "android_en_US_12h.txt"), {"operator_sender": "Dave", "slug": "one"}, tz=TZ)
    b, _ = app.new_show(str(WA / "android_en_US_12h.txt"), {"operator_sender": "Dave", "slug": "two"}, tz=TZ)
    assert a.key() != b.key()
    text, _ = whatsapp.read_export(WA / "android_en_US_12h.txt")
    ids_a = [m["id"] for m in whatsapp.parse(text, TZ, key=a.key()).messages]
    ids_b = [m["id"] for m in whatsapp.parse(text, TZ, key=b.key()).messages]
    assert ids_a and not set(ids_a) & set(ids_b)
    assert masking.phone_label("+1 555 010 2233", a.key()) != masking.phone_label("+1 555 010 2233", b.key())
    mode = stat.S_IMODE(os.stat(a.secret_path).st_mode)
    assert mode == 0o600


def test_identity_text_absorbs_ocr_noise():
    assert identity_text("K") == identity_text("k") == identity_text("k.")
    assert identity_text("lol!!") == identity_text("lol")
    assert identity_text("😂😂") == identity_text("🤣") != identity_text("k")
    assert identity_text("see you at 10") != identity_text("see you at 11")


def test_context_ids_do_not_depend_on_where_an_import_starts():
    rows = [("Mike", "k"), ("Dave", "k"), ("Mike", "k"), ("Sam", "who's driving")]
    def ids(seq, prev=("", "")):
        out, pl, pt = [], prev[0], prev[1]
        for label, text in seq:
            out.append(context_hash(label, None, text, None, False, pl, identity_text(pt), 0))
            pl, pt = label, text
        return out
    full = ids(rows)
    assert ids(rows[1:], prev=rows[0]) == full[1:]
    assert len(set(full)) == 4  # repeated "k" from different contexts stay distinct


# ---- registry and sniffing ---------------------------------------------------

def _p(*names):
    return [Path(n) for n in names]


def test_sniff_matrix(tmp_path):
    for f in sorted(WA.glob("*.txt")) + sorted(WA.glob("*.zip")):
        assert registry.sniff([f]).name == "whatsapp", f.name
    iso = tmp_path / "iso.txt"  # an ISO-date Android export (sv-SE style)
    iso.write_text("2026-01-15 21:41 - Ava: hej\n2026-01-15 21:42 - Ben: hej hej\n", "utf-8")
    assert registry.sniff([iso]).name == "whatsapp"
    yaml = tmp_path / "config"
    yaml.write_text("apiVersion: v1\nkind: Config\nclusters:\n- cluster:\n    server: https://x\n", "utf-8")
    plain = tmp_path / "notes.txt"
    plain.write_text("Mike: hi\nDave: hey\nMike: what's up\n", "utf-8")
    for f in (yaml, plain, ROOT / "README.md", ROOT / "pyproject.toml"):
        with pytest.raises(ChatcomError) as e:
            registry.sniff([f])
        assert e.value.code == "CHATCOM_E_SOURCE_UNKNOWN", f
        assert "never read as a transcript" in e.value.fix


@pytest.mark.parametrize("pad", range(40))
def test_sniff_accepts_a_large_export_whose_sample_cut_splits_a_character(tmp_path, pad):
    # Value: protects=a valid WhatsApp .txt over 64 KB is recognized wherever the sniff cut lands; fails_when=peek
    # decodes the cut sample strictly or falls to UTF-16; why_new=QA probes 012/013 and review found it; seam=none;
    # no_claim=encodings other than UTF-8 and BOM UTF-16
    body = "1/15/26, 9:02 PM - Ann: " + "a" * pad + "\n" + "1/15/26, 9:04 PM - Mike: café 😂\n" * 3000
    f = tmp_path / "big.txt"
    f.write_text(body, "utf-8")
    assert registry.sniff([f]).name == "whatsapp"


def test_sniff_names_sources_that_are_not_built_yet(tmp_path, monkeypatch):
    # every wave-1 source is built now; a registered-but-later adapter still refuses by name
    monkeypatch.setitem(registry.ADAPTERS, "screenshots",
                        registry.Adapter("screenshots", {}, registry.ADAPTERS["screenshots"].load, later=True))
    shots = tmp_path / "shots"
    shots.mkdir()
    (shots / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    with pytest.raises(ChatcomError) as e:
        registry.sniff([shots])
    assert e.value.code == "CHATCOM_E_SOURCE_UNKNOWN" and "not in this chatcom yet" in e.value.what


def test_two_matches_in_one_tier_are_ambiguous(monkeypatch, tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("anything", "utf-8")
    fake = registry.Adapter("other", {"text": lambda paths: True}, lambda p, c: None)
    monkeypatch.setitem(registry.ADAPTERS, "other", fake)
    monkeypatch.setitem(registry.ADAPTERS, "whatsapp",
                        registry.Adapter("whatsapp", {"text": lambda paths: True}, lambda p, c: None))
    with pytest.raises(ChatcomError) as e:
        registry.sniff([f])
    assert e.value.code == "CHATCOM_E_SOURCE_AMBIGUOUS" and set(e.value.context["candidates"]) == {"whatsapp", "other"}
    assert registry.sniff([f], "other").name == "other"
    with pytest.raises(ChatcomError) as e:
        registry.sniff([f], "nope")
    assert e.value.code == "CHATCOM_E_USAGE"


# ---- session-mode paths --------------------------------------------------------
# Value: protects=an injected command cannot hand another file to the model; fails_when=the
# allowlist check is removed or skipped; why_new=new rule; seam=none; no_claim=harness command matching

@pytest.mark.parametrize("bad", ["~/.ssh/id_rsa", "/etc/passwd", "$(cat ~/.kube/config)", "x; rm -rf ~",
                                 "{inbox}/../../secret", "{inbox}/../setup.json"])
def test_session_ingest_reads_only_the_new_path_or_the_inbox(capsys, lake_show, not_a_tty, bad):
    path = bad.format(inbox=lake_show.inbox)
    code, out, err = run(capsys, "ingest", "lake-house", "--export", path)
    assert code == 2 and "CHATCOM_E_EXPORT_OUTSIDE_INBOX" in err, err
    assert out == ""


def test_session_ingest_accepts_the_new_path_and_inbox_files(capsys, lake_show, not_a_tty):
    assert run(capsys, "ingest", "lake-house", "--export", str(LAKE))[0] == 6
    inbox_copy = lake_show.inbox / "week2.txt"
    inbox_copy.write_bytes(LAKE.read_bytes())
    assert run(capsys, "ingest", "lake-house", "--export", str(inbox_copy))[0] == 6



# Value: protects=a dry run, which asks for no consent, cannot copy an outside file into the show folder when an agent
# typed it; fails_when=the dry-run path check is removed; why_new=pass 2 red team; seam=none; no_claim=TTY detection
def test_dry_run_without_a_terminal_reads_only_the_new_path_or_the_inbox(capsys, lake_show, not_a_tty, tmp_path):
    other = tmp_path / "notes.txt"
    other.write_text("token=abc123\npassword hunter2\n")
    code, out, err = run(capsys, "episode", "lake-house", "--dry-run", "--source", "whatsapp", "--export", str(other))
    assert code == 2 and "CHATCOM_E_EXPORT_OUTSIDE_INBOX" in err and out == ""
    assert not any("hunter2" in f.read_text() for f in lake_show.dir.rglob("*.txt"))
    assert run(capsys, "episode", "lake-house", "--dry-run", "--export", str(LAKE))[0] == 0


# Value: protects=write-submit honours the flow the operator accepted at ingest; fails_when=submit skips
# require_flow; why_new=pass 2 red team; seam=none; no_claim=the notice wording
def test_session_submit_against_another_accepted_flow_asks_again(capsys, lake_show, beats_s01e01, not_a_tty):
    assert run(capsys, "ingest", "lake-house", "--export", str(LAKE), "--accept-data-flow")[0] == 0
    sp = lake_show.dir / "episodes" / "S01E01" / "state.json"
    st = store.read_json(sp)
    st["accepted_flow"]["writer"] = "anthropic"
    sp.write_text(json.dumps(st))
    beats = lake_show.dir / "beats.json"
    beats.write_text(json.dumps(beats_s01e01))
    code, _, err = run(capsys, "write-submit", "lake-house", "--file", str(beats), "--model", "claude-test")
    assert code == 6 and "CHATCOM_E_AWAITING_CONFIRMATION" in err, err
    assert store.read_json(sp)["stage"] == "ingested"

# ---- resume_point -----------------------------------------------------------------

T0 = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)


def _msg(i, sender, text, *, minutes=None, precision="minute", label=None, adapter="whatsapp", key=None):
    t = T0 + timedelta(minutes=i if minutes is None else minutes)
    if precision == "order":
        t = T0 + timedelta(milliseconds=i)
    mid = message_hash(label or sender, t.strftime("%Y-%m-%dT%H:%MZ"), 0, text, None, False, key) \
        if precision in ("minute", "exact") else context_hash(label or sender, None, text, None, False, "", "", i, key)
    return {"id": mid, "sender": sender, "time": t.isoformat(), "time_precision": precision, "text": text,
            "media": None, "deleted": False, "edited": False, "reply_to": None, "reactions": None, "quote": None,
            "source": {"adapter": adapter, "line": i + 1, "label": label or sender}}


CHAT = [_msg(i, s, t) for i, (s, t) in enumerate(
    [("Mike", "who's driving"), ("Dave", "not me"), ("Sam", "k"), ("Mike", "lol"), ("Dave", "fine I'll drive"),
     ("Sam", "k"), ("Mike", "leaving at 6"), ("Dave", "6 is early"), ("Sam", "k"), ("Mike", "deal")])]


def test_resume_by_id():
    cur = resume.make_cursor(CHAT[:5], "whatsapp", None)
    r = resume.resume_point(cur, CHAT)
    assert (r.method, r.rows) == ("id", CHAT[5:])


def test_a_migrated_cursor_skips_its_whole_minute():
    # Value: protects=the first episode after migrate repeats nothing from the cursor minute; fails_when=an empty
    # signature keeps cursor-minute rows; why_new=migrated cursors have no signature; seam=none; no_claim=none beyond protects
    cur = {**resume.make_cursor(CHAT[:5], "whatsapp", None), "hash": "f" * 16, "signature": {"raw": [], "canonical": []}}
    later_same_minute = {**_msg(4, "Theo", "wait for me", minutes=4), "id": "e" * 16}
    rows = sorted([{**m, "id": "0" * 15 + str(i)} for i, m in enumerate(CHAT)] + [later_same_minute],
                  key=lambda m: m["time"])
    r = resume.resume_point(cur, rows)
    assert r.method == "time" and [m["text"] for m in r.rows] == [m["text"] for m in CHAT[5:]]


def test_resume_by_time_keeps_new_rows_in_the_cursor_minute():
    cur = resume.make_cursor(CHAT[:5], "whatsapp", None)
    later_same_minute = {**_msg(4, "Theo", "wait for me", minutes=4), "id": "f" * 16}
    rows = [{**m, "id": "0" * 15 + str(i)} for i, m in enumerate(CHAT)] + [later_same_minute]
    rows.sort(key=lambda m: m["time"])
    r = resume.resume_point(cur, rows)
    assert r.method == "time"
    assert [m["text"] for m in r.rows][0] == "wait for me" and len(r.rows) == 6


_IMPORTS = iter(range(10_000))


def _order(rows, adapter="screenshots", noise=False, relabel=None):
    """Rows of a fresh import: ids never repeat across imports (the real
    context ids of noisy re-reads would not match either), so resume must align."""
    out, nonce = [], next(_IMPORTS)
    for i, m in enumerate(rows):
        text = m["text"].upper() + "." if noise and i % 2 else m["text"]
        sender = (relabel or {}).get(m["sender"], m["sender"])
        row = _msg(i, sender, text, precision="order", adapter=adapter, label=m["source"]["label"])
        row["id"] = f"{nonce:08x}{i:08x}"
        out.append(row)
    return out


def test_resume_by_alignment_survives_ocr_noise():
    first = _order(CHAT[:6])
    cur = resume.make_cursor(first, "screenshots", None)
    second = _order(CHAT[3:], noise=True)  # overlaps rows 3-5, re-read with OCR noise
    r = resume.resume_point(cur, second, adapter="screenshots")
    assert r.method == "align" and [m["text"] for m in r.rows] == [m["text"] for m in second[3:]]


def test_a_repeated_triple_widens_the_signature():
    rows = [("Sam", "k"), ("Mike", "lol"), ("Sam", "k"), ("Mike", "lol"), ("Sam", "k"), ("Dave", "ok"),
            ("Sam", "k"), ("Mike", "lol"), ("Sam", "k"), ("Theo", "hi")]
    chat = [_msg(i, s, t, precision="order", adapter="screenshots") for i, (s, t) in enumerate(rows)]
    cur = resume.make_cursor(chat[:9], "screenshots", None)  # last 3 (k, lol, k) occur 3 times
    r = resume.resume_point(cur, _order(chat), adapter="screenshots")
    assert r.method == "align" and [m["text"] for m in r.rows] == ["hi"]


def test_a_signature_that_never_becomes_unique_reads_the_whole_import():
    chat = [_msg(i, s, t, precision="order", adapter="screenshots")
            for i, (s, t) in enumerate([("Sam", "k"), ("Mike", "lol")] * 8)]
    cur = resume.make_cursor(chat[:6], "screenshots", None)
    r = resume.resume_point(cur, _order(chat), adapter="screenshots")
    assert r.method == "whole" and r.warning["code"] == "CHATCOM_W_WHOLE_IMPORT"


def test_cross_source_resume_uses_canonical_names():
    first = _order(CHAT[:6], adapter="screenshots")
    for m in first:
        m["source"]["label"] = {"Mike": "Mike T."}.get(m["sender"], m["sender"])
    cur = resume.make_cursor(first, "screenshots", None)
    second = [{**m, "time_precision": "order", "id": "e" * 16} for m in _order(CHAT[3:], adapter="transcript")]
    r = resume.resume_point(cur, second, adapter="transcript")
    assert r.method == "align" and r.rows[0]["text"] == "leaving at 6"


def test_no_overlap_reads_the_whole_import_with_a_warning():
    cur = resume.make_cursor(_order(CHAT[:3]), "screenshots", None)
    other = _order([_msg(0, "Zoe", "new chat entirely"), _msg(1, "Ann", "hi")])
    r = resume.resume_point(cur, other, adapter="screenshots")
    assert r.method == "whole" and r.rows == other and r.warning["code"] == "CHATCOM_W_WHOLE_IMPORT"


def test_quotes_by_excluded_people_are_removed():
    rows = [dict(m) for m in CHAT[:3]]
    rows[2] = {**rows[2], "quote": {"sender": "Dave", "text": "not me"}}
    w = window.select(rows, None, exclude=["Dave"])
    assert all(m["sender"] != "Dave" for m in w.messages) and w.messages[-1]["quote"] is None


def test_order_imports_skip_the_day_rules():
    chat = _order(CHAT)
    w = window.select(chat, None, adapter="screenshots")
    assert len(w.messages) == len(chat) and w.cursor["precision"] == "order" and w.cursor["minute"] is None
    with pytest.raises(ChatcomError) as e:
        window.select(chat, None, since=T0, adapter="screenshots")
    assert e.value.code == "CHATCOM_E_USAGE"


# ---- status and migrate ---------------------------------------------------------

def test_status_names_the_next_command(capsys, lake_show):
    code, out, _ = run(capsys, "status", "lake-house")
    assert code == 0 and "not started" in out and "next: chatcom ingest lake-house --export <file>" in out
    run(capsys, "ingest", "lake-house", "--export", str(LAKE), "--accept-data-flow")
    code, out, _ = run(capsys, "--json", "status", "lake-house")
    info = json.loads(out)
    assert info["stage"] == "ingested" and info["next"] == "chatcom write-prep lake-house" and info["lock"] is None


def _downgrade(show):
    """Make a v2 show look like one written by the shipped v1."""
    b = store.read_json(show.bible_path)
    b["schema_version"] = 1
    for ep in b["episodes"]:
        ep["window"]["cursor"] = {k: ep["window"]["cursor"][k] for k in ("minute", "hash")}
    store.write_json(show.bible_path, b)
    c = store.read_json(show.cast_path)
    c["schema_version"] = 1
    store.write_json(show.cast_path, c)
    for f in show.dir.glob("episodes/*/messages.jsonl"):
        rows = store.read_jsonl(f)
        for m in rows:
            m.pop("time_precision"), m.pop("quote"), m["source"].pop("label")
        store.write_jsonl(f, rows)
    for sp in show.dir.glob("episodes/*/state.json"):
        st = store.read_json(sp)
        st.pop("accepted_flow"), st.pop("prep_cycles")
        st["window"]["cursor"] = {k: st["window"]["cursor"][k] for k in ("minute", "hash")}
        store.write_json(sp, st)
    show.secret_path.unlink()
    if show.inbox.is_dir() and not any(show.inbox.iterdir()):
        show.inbox.rmdir()  # v1 shows had no inbox


def test_migrate_upgrades_a_v1_show_and_is_idempotent(capsys, lake_show):
    run(capsys, "ingest", "lake-house", "--export", str(LAKE), "--accept-data-flow")
    _downgrade(lake_show)
    code, _, err = run(capsys, "status", "lake-house")
    assert code == 2 and "CHATCOM_E_SCHEMA_OLD" in err and "chatcom migrate lake-house" in err
    code, out, _ = run(capsys, "migrate", "lake-house")
    assert code == 0 and "upgraded from schema_version 1 to 2" in out
    backup = Path(out.strip().splitlines()[-1].split("backup: ")[1])
    assert json.loads((backup / "bible.json").read_text())["schema_version"] == 1
    assert run(capsys, "status", "lake-house")[0] == 0
    assert lake_show.inbox.is_dir()  # the skill's weekly path works after migrate
    for f in lake_show.dir.glob("episodes/*/messages.jsonl"):
        assert all(not problems("message", m) for m in store.read_jsonl(f))
    code, out, _ = run(capsys, "migrate", "lake-house")
    assert code == 0 and "nothing to do" in out


def test_a_kill_during_migrate_leaves_a_v1_show_that_migrates_again(capsys, lake_show, monkeypatch):
    run(capsys, "ingest", "lake-house", "--export", str(LAKE), "--accept-data-flow")
    _downgrade(lake_show)
    real = store.write_json

    def dies_on_bible(path, doc):
        if path.name == "bible.json":
            raise KeyboardInterrupt
        real(path, doc)
    monkeypatch.setattr(store, "write_json", dies_on_bible)
    with pytest.raises(KeyboardInterrupt):
        app.migrate("lake-house")
    monkeypatch.setattr(store, "write_json", real)
    assert store.read_json(lake_show.bible_path)["schema_version"] == 1
    assert app.migrate("lake-house")["changed"] is True
    assert store.read_json(lake_show.bible_path)["schema_version"] == 2


# ---- the spec ---------------------------------------------------------------------

def test_spec_and_code_agree():
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_spec.py")], capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr


# ---- coverage audit, pass 1 (generated by the /ship coverage audit) ------------------

# Value: protects=a symlink in the inbox cannot hand a file from elsewhere to the session model; fails_when=the
# path check compares the unresolved path; why_new=the hostile-path table has no links; seam=none;
# no_claim=hard links and races between the check and the read
def test_session_ingest_refuses_an_inbox_link_to_a_file_elsewhere(capsys, lake_show, not_a_tty, tmp_path):
    outside = tmp_path / "private.txt"
    outside.write_bytes(LAKE.read_bytes())  # a valid chat, so a missing check would ingest it
    link = lake_show.inbox / "week2.txt"
    link.symlink_to(outside)
    code, out, err = run(capsys, "ingest", "lake-house", "--export", str(link), "--accept-data-flow")
    assert code == 2 and "CHATCOM_E_EXPORT_OUTSIDE_INBOX" in err and out == ""
    assert not (lake_show.episode_dir("S01E01") / "messages.jsonl").exists()


# Value: protects=write-prep hands chat data to the session model only after the operator accepted that flow;
# fails_when=write_prep drops its require_flow check; why_new=only run_writer's check is tested; seam=none;
# no_claim=the reader flow (PR 4)
def test_write_prep_refuses_an_episode_accepted_for_another_flow(capsys, lake_show, not_a_tty):
    app.ingest(lake_show, str(LAKE), accepted=True, mode="anthropic")
    code, _, err = run(capsys, "write-prep", "lake-house")
    assert code == 6 and "CHATCOM_E_AWAITING_CONFIRMATION" in err
    assert not (lake_show.episode_dir("S01E01") / "writer" / "bundle.json").exists()
    assert store.read_json(lake_show.state_path("S01E01"))["prep_cycles"] == 0


# Value: protects=migrate upgrades a v1 show that has a drafted episode (bible.next.json) to a valid v2;
# fails_when=the bible.next.json branch skips the cursor or the version; why_new=the migrate tests stop at
# ingested; seam=none; no_claim=v1 bibles with committed episodes (commit is not built)
def test_migrate_upgrades_a_drafted_v1_episode(capsys, lake_show, beats_s01e01, not_a_tty):
    from chatcom.core import bible as bible_ops
    run(capsys, "ingest", "lake-house", "--export", str(LAKE), "--accept-data-flow")
    assert run(capsys, "write-prep", "lake-house")[0] == 0
    good = lake_show.dir / "good.json"
    good.write_text(json.dumps(beats_s01e01))
    assert run(capsys, "write-submit", "lake-house", "--file", str(good), "--model", "m")[0] == 0
    _downgrade(lake_show)
    nxt_path = lake_show.episode_dir("S01E01") / "bible.next.json"
    nxt = store.read_json(nxt_path)
    nxt["schema_version"] = 1
    for ep in nxt["episodes"]:
        ep["window"]["cursor"] = {k: ep["window"]["cursor"][k] for k in ("minute", "hash")}
    store.write_json(nxt_path, nxt)
    assert run(capsys, "migrate", "lake-house")[0] == 0
    nxt = store.read_json(nxt_path)
    bible_ops.validate(nxt)
    # A migrated cursor carries no signature: v1 rows cannot match a v2 parse,
    # so resume keeps the v1 rule (test_a_migrated_cursor_skips_its_whole_minute).
    assert nxt["schema_version"] == 2
    assert nxt["episodes"][-1]["window"]["cursor"]["signature"] == {"raw": [], "canonical": []}
    code, out, _ = run(capsys, "--json", "status", "lake-house")
    assert code == 0 and json.loads(out)["stage"] == "drafted"


# Value: protects=the fix line for a missing show secret names a command that makes the show usable again;
# fails_when=migrate returns 'nothing to do' on a v2 show without creating the secret; why_new=no test
# removes the secret from a v2 show; seam=none; no_claim=whether a new key or a restore is the right repair
def test_a_lost_secret_is_repaired_by_the_command_its_error_names(capsys, lake_show, not_a_tty):
    lake_show.secret_path.unlink()
    code, _, err = run(capsys, "--json", "ingest", "lake-house", "--export", str(LAKE), "--accept-data-flow")
    env = json.loads(err.strip().splitlines()[-1])
    assert code == 2 and env["code"] == "CHATCOM_E_BIBLE_INVALID" and "chatcom migrate lake-house" in env["fix"]
    assert run(capsys, "migrate", "lake-house")[0] == 0
    code, _, err = run(capsys, "ingest", "lake-house", "--export", str(LAKE), "--accept-data-flow")
    assert code == 0, err


# ---- coverage audit, pass 2 (generated by the /ship coverage audit) ------------------

# Value: protects=the --json keys the skill reads from new, ingest, write-prep and write-submit, and the source and
# resume fields in state.json; fails_when=a key is renamed, dropped or points at a missing file; why_new=only the
# human output and error envelopes are tested; seam=none; no_claim=the keys the skill does not read
def test_json_success_output_has_the_keys_the_skill_reads(capsys, chatcom_home, beats_s01e01, not_a_tty):
    code, out, _ = run(capsys, "--json", "new", str(LAKE), "--show", "lake-house", "--operator", "Dave", "--tz", TZ)
    info = json.loads(out)
    assert code == 0 and info["slug"] == "lake-house" and info["source"] == "whatsapp"
    assert Path(info["bible"]).is_file() and info["inbox"] == str(chatcom_home / "lake-house" / "inbox")
    code, out, _ = run(capsys, "--json", "ingest", "lake-house", "--export", str(LAKE), "--accept-data-flow")
    ing = json.loads(out)
    assert code == 0 and (ing["episode"], ing["stage"], ing["source"], ing["resume"]) == \
        ("S01E01", "ingested", "whatsapp", "first")
    assert Path(ing["messages_path"]).is_file() and ing["next_command"] == "chatcom write-prep lake-house"
    st = store.read_json(chatcom_home / "lake-house" / "episodes" / "S01E01" / "state.json")
    assert st["source"] == "whatsapp" and st["resume"] == {"method": "first", "warning": None}
    code, out, _ = run(capsys, "--json", "write-prep", "lake-house")
    bundle = json.loads(out)["bundle"]
    assert code == 0 and json.loads(Path(bundle).read_text())["episode"] == "S01E01"
    beats = Path(json.loads(Path(bundle).read_text())["write_to"])
    beats.write_text(json.dumps(beats_s01e01))
    code, out, _ = run(capsys, "--json", "write-submit", "lake-house", "--file", str(beats), "--model", "claude-test")
    sub = json.loads(out)
    assert code == 0 and (sub["stage"], sub["model"]) == ("drafted", "claude-test")
    assert Path(sub["episode_path"]).is_file() and sub["episode_path"].endswith("beats.json")


# Value: protects=time resume compares UTC across a DST fall-back, so the repeated 01:xx hour is neither replayed
# nor lost; fails_when=resume compares wall-clock minutes or drops the offset; why_new=the design doc requires the
# row and no resume test crosses DST; seam=none; no_claim=zones other than America/New_York
def test_resume_by_time_across_a_dst_fall_back():
    from zoneinfo import ZoneInfo
    ny = ZoneInfo("America/New_York")
    wall = [(1, 20, 0, "Mike", "first 1:20"), (1, 30, 0, "Theo", "first 1:30"), (1, 40, 0, "Dave", "first 1:40"),
            (1, 10, 1, "Sam", "second 1:10"), (1, 30, 1, "Mike", "second 1:30"),  # cursor ends here
            (1, 35, 1, "Dave", "second 1:35"), (2, 5, 0, "Sam", "after 2")]
    rows = []
    for i, (h, mi, fold, sender, text) in enumerate(wall):
        t = datetime(2026, 11, 1, h, mi, tzinfo=ny, fold=fold)
        m = _msg(i, sender, text)
        rows.append({**m, "time": t.isoformat()})
    assert [r["time"][-6:] for r in rows] == ["-04:00"] * 3 + ["-05:00"] * 4
    cur = resume.make_cursor(rows[:5], "whatsapp", None)
    fresh = [{**m, "id": f"{i:016x}"} for i, m in enumerate(rows)]  # a new export: ids do not match
    r = resume.resume_point(cur, fresh)
    assert r.method == "time" and [m["text"] for m in r.rows] == ["second 1:35", "after 2"]


# Value: protects=status reports a live lock holder and the literal next command for a CLI-mode ingest and a
# drafted episode; fails_when=lock_holder or next_command branches break, or status blocks on the lock;
# why_new=status is tested only for not-started and session-ingested; seam=none; no_claim=locks held by another user
def test_status_lock_holder_and_next_for_cli_and_drafted(capsys, lake_show, beats_s01e01, not_a_tty):
    app.ingest(lake_show, str(LAKE), accepted=True, mode="anthropic")
    code, out, _ = run(capsys, "--json", "status", "lake-house")
    nxt = json.loads(out)["next"]
    assert code == 0 and nxt == "chatcom episode lake-house --export <file> --writer anthropic"
    args = cli.build_parser().parse_args(nxt.replace("<file>", str(LAKE)).split()[1:])
    assert (args.cmd, args.writer) == ("episode", "anthropic")
    lock = lake_show.dir / ".lock"
    lock.write_text(str(os.getpid()))
    code, out, _ = run(capsys, "status", "lake-house")
    assert code == 0 and f"lock: held by process {os.getpid()} since " in out
    code, out, _ = run(capsys, "--json", "status", "lake-house")
    assert json.loads(out)["lock"]["pid"] == os.getpid()
    lock.unlink()
    run(capsys, "ingest", "lake-house", "--export", str(LAKE), "--accept-data-flow")
    assert run(capsys, "write-prep", "lake-house")[0] == 0
    good = lake_show.dir / "good.json"
    good.write_text(json.dumps(beats_s01e01))
    assert run(capsys, "write-submit", "lake-house", "--file", str(good), "--model", "m")[0] == 0
    code, out, _ = run(capsys, "--json", "status", "lake-house")
    info = json.loads(out)
    assert (info["stage"], info["lock"]) == ("drafted", None) and "beats.json" in info["next"]


# Value: protects=migrate without a slug picks the only show and refuses 0 or 2, and a newer bible is refused with
# nothing written; fails_when=the no-slug branch or the v>2 guard changes; why_new=migrate is tested only with a
# slug on v1/v2; seam=none; no_claim=bibles newer than 3
def test_migrate_without_a_slug_and_on_a_newer_bible(capsys, chatcom_home, not_a_tty):
    code, _, err = run(capsys, "--json", "migrate")
    assert code == 2 and json.loads(err)["code"] == "CHATCOM_E_SHOW_NOT_FOUND"
    one, _ = app.new_show(str(LAKE), {"operator_sender": "Dave", "slug": "lake-house"}, tz=TZ)
    code, out, _ = run(capsys, "--json", "migrate")
    info = json.loads(out)
    assert code == 0 and (info["show"], info["changed"]) == ("lake-house", False)
    app.new_show(str(WA / "ios_en_GB_24h.txt"), {"operator_sender": "Dave", "slug": "other"}, tz=TZ)
    code, _, err = run(capsys, "--json", "migrate")
    env = json.loads(err)
    assert code == 2 and env["code"] == "CHATCOM_E_SHOW_AMBIGUOUS" and set(env["context"]["shows"]) == {"lake-house", "other"}
    b = store.read_json(one.bible_path)
    b["schema_version"] = 3
    store.write_json(one.bible_path, b)
    before = {p: p.read_bytes() for p in one.dir.rglob("*") if p.is_file()}
    code, _, err = run(capsys, "--json", "migrate", "lake-house")
    assert code == 2 and json.loads(err)["code"] == "CHATCOM_E_SCHEMA_NEWER"
    assert {p: p.read_bytes() for p in one.dir.rglob("*") if p.is_file()} == before
    assert not any((one.dir / "snapshots").iterdir()) and not (one.dir / ".lock").exists()


# Value: protects=an excluded unnamed number is never silently included after its label changes; fails_when=ingest
# applies a stale exclude label that matches no one; why_new=red-team found migrate renames labels; seam=none;
# no_claim=exclusions by display name (matched at new)
def test_a_stale_excluded_label_stops_ingest_before_anything_is_written(capsys, lake_show, not_a_tty):
    setup = store.read_json(lake_show.setup_path)
    setup["exclude"] = ["Unnamed 0a1eb2"]
    store.write_json(lake_show.setup_path, setup)
    code, _, err = run(capsys, "--json", "ingest", "lake-house", "--export", str(LAKE), "--accept-data-flow")
    env = json.loads(err.strip().splitlines()[-1])
    assert code == 2 and env["code"] == "CHATCOM_E_SETUP_INVALID" and "Nothing was sent" in env["fix"]
    assert env["context"]["stale"] == ["Unnamed 0a1eb2"]
    assert not (lake_show.episode_dir("S01E01") / "messages.jsonl").exists()
