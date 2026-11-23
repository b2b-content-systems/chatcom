"""Step 2.5 PR 3: the Telegram adapter (Telegram Desktop's machine-readable JSON export)."""
import json
import os
import shutil
from pathlib import Path

import pytest

from chatcom import cli
from chatcom.adapters import registry, telegram as TG
from chatcom.core.errors import ChatcomError
from chatcom.core.messages import UNKEYED
from chatcom.core.schema import problems
from conftest import ROOT, TZ

FIX = ROOT / "tests" / "fixtures" / "telegram"
CHAT = FIX / "ChatExport_lake"
ACCOUNT = FIX / "account"


def run(capsys, *argv):
    code = cli.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


@pytest.fixture
def not_a_tty(monkeypatch):
    monkeypatch.setattr(cli, "_tty", lambda: False)


def load(path, thread=None, tz=TZ):
    res, _, _ = registry.load([str(path)], registry.Context(tz=tz, key=UNKEYED, thread=thread))
    return res


# ---- parsing ------------------------------------------------------------------
# Value: protects=every Telegram field the engine reads; fails_when=a field maps differently; why_new=new adapter;
# seam=none; no_claim=export features not in the fixture (games, invoices, stories)

def test_one_chat_export_parses():
    res = load(CHAT)
    m = res.messages
    assert res.adapter == "telegram" and res.group_name == "Lake House Boys"
    assert res.system_lines == 2 and res.unparsed == [] and res.fmt == {"export": "chat", "clock": "utc"}
    assert [x["sender"] for x in m] == ["Dave", "Mike", "Theo", "Sam", "Mike", "Dave", "Theo", "Deleted Account",
                                        "Sam", "Mike"]
    assert all(x["time_precision"] == "exact" and problems("message", x) == [] for x in m)
    assert m[0]["time"] == "2026-01-09T19:02:11-05:00"
    assert m[1]["text"] == "not me, I brought it last year"  # text entities joined
    assert m[1]["reactions"] == [{"emoji": "😂", "sender": "Theo", "count": 1},
                                 {"emoji": "😂", "sender": None, "count": 2}]
    assert [x["media"] for x in m] == [None, None, "image", None, "sticker", None, "poll", None, "audio", None]
    assert m[3]["reply_to"] == m[1]["id"] and m[3]["quote"] == {"sender": "Mike", "text": m[1]["text"]}
    assert m[5]["edited"] and "555" not in m[5]["text"]
    assert m[6]["text"] == "Grill or no grill?"


def test_times_follow_the_show_zone():
    a = load(CHAT).messages[0]["time"]
    b = load(CHAT, tz="Europe/Berlin").messages[0]["time"]
    assert a == "2026-01-09T19:02:11-05:00" and b == "2026-01-10T01:02:11+01:00"


def test_an_export_without_unix_times_uses_local_dates(tmp_path):
    doc = json.loads((CHAT / "result.json").read_text("utf-8"))
    for m in doc["messages"]:
        m.pop("date_unixtime", None)
    d = tmp_path / "ChatExport_old"
    d.mkdir()
    (d / "result.json").write_text(json.dumps(doc), "utf-8")
    res = load(d)
    assert res.fmt["clock"] == "local" and res.messages[0]["time"] == "2026-01-09T19:02:11-05:00"


def test_ids_are_stable_across_exports_and_differ_across_shows():
    a, b = load(CHAT).messages, load(CHAT / "result.json").messages
    assert [m["id"] for m in a] == [m["id"] for m in b]
    keyed, _, _ = registry.load([str(CHAT)], registry.Context(tz=TZ, key=b"another-show"))
    assert not {m["id"] for m in a} & {m["id"] for m in keyed.messages}


def test_phone_numbers_in_names_are_masked(tmp_path):
    doc = json.loads((CHAT / "result.json").read_text("utf-8"))
    doc["messages"][1]["from"] = "+1 555 010 2233"
    doc["messages"][2]["from"] = "Mike 555-010-2244"
    doc["messages"][2]["reactions"][0]["recent"][0]["from"] = "+1 555 010 2255"
    d = tmp_path / "x"
    d.mkdir()
    (d / "result.json").write_text(json.dumps(doc), "utf-8")
    blob = json.dumps(load(d).messages)
    for n in ("2233", "2244", "2255"):
        assert n not in blob


# ---- whole-account exports ------------------------------------------------------------
# Value: protects=a whole-account export reads one chat and nothing else; fails_when=--thread is ignored or
# personal information leaks into messages; why_new=spec THREAD_REQUIRED; seam=none; no_claim=exports with
# thousands of chats (the list is capped at 20 names)

def test_an_account_export_needs_a_thread():
    with pytest.raises(ChatcomError) as e:
        load(ACCOUNT)
    assert e.value.code == "CHATCOM_E_THREAD_REQUIRED"
    assert "Lake House Boys" in e.value.fix and "Book Club" in e.value.fix
    res = load(ACCOUNT, thread="lake house boys")
    assert res.fmt["export"] == "account" and len(res.messages) == 10
    assert len(load(ACCOUNT, thread="4400777").messages) == 2
    with pytest.raises(ChatcomError) as e:
        load(ACCOUNT, thread="Nope")
    assert e.value.code == "CHATCOM_E_THREAD_REQUIRED" and "no chat named" in e.value.what


def test_an_account_export_never_reads_personal_information():
    blob = json.dumps(load(ACCOUNT, thread="Lake House Boys").messages)
    for secret in ("Operator", "0199", "Secret", "0188", "test_operator", "Book Club", "chapter 3"):
        assert secret not in blob


# ---- sniffing ---------------------------------------------------------------------------
# Value: protects=only a Telegram export is read as one; fails_when=another JSON or folder is claimed; why_new=PR 1
# deferred the matcher here (D2); seam=none; no_claim=Telegram's HTML export (refused as unknown)

def test_sniff_finds_telegram_and_refuses_other_json(tmp_path):
    for p in (CHAT, CHAT / "result.json", ACCOUNT, ACCOUNT / "result.json"):
        assert registry.sniff([p]).name == "telegram", p
    pkg = tmp_path / "package.json"
    pkg.write_text('{"name": "x", "version": "1.0.0", "scripts": {"test": "jest"}}', "utf-8")
    html = tmp_path / "ChatExport_html"
    html.mkdir()
    (html / "messages.html").write_text("<html></html>", "utf-8")
    other = tmp_path / "data.json"
    other.write_text('{"messages": [{"role": "user", "content": "hi"}]}', "utf-8")
    for p in (pkg, html, other, ROOT / "src" / "chatcom" / "schemas" / "message.schema.json"):
        with pytest.raises(ChatcomError) as e:
            registry.sniff([p])
        assert e.value.code == "CHATCOM_E_SOURCE_UNKNOWN", p


def test_sniff_peeks_and_never_reads_a_big_file_whole(tmp_path, monkeypatch):
    big = tmp_path / "result.json"
    big.write_text('{"name": "x", "type": "private_group", "id": 1, "messages": [' +
                   ",".join(['{"id": 1, "type": "message", "date": "2026-01-01T00:00:00", "from": "A", "text": "x"}']
                            * 20000) + "]}", "utf-8")
    reads = []
    real = open

    def spy(path, mode="r", *a, **kw):
        fh = real(path, mode, *a, **kw)
        if "b" in mode and Path(path) == big:
            orig = fh.read
            fh.read = lambda n=-1: reads.append(n) or orig(n)
        return fh
    monkeypatch.setattr("builtins.open", spy)
    assert registry.sniff([big]).name == "telegram"
    assert reads and all(0 < n <= registry.PEEK_BYTES for n in reads)


def test_a_result_json_that_links_outside_its_folder_is_refused(tmp_path):
    d = tmp_path / "ChatExport_link"
    d.mkdir()
    os.symlink(CHAT / "result.json", d / "result.json")
    with pytest.raises(ChatcomError) as e:
        load(d)
    assert e.value.code == "CHATCOM_E_EXPORT_UNREADABLE"


@pytest.mark.parametrize("body", ["{not json", "[1, 2]"])
def test_a_broken_result_json_is_unreadable(tmp_path, body):
    d = tmp_path / "ChatExport_bad"
    d.mkdir()
    (d / "result.json").write_text(body, "utf-8")
    with pytest.raises(ChatcomError) as e:
        TG.load([d], registry.Context(tz=TZ))
    assert e.value.code == "CHATCOM_E_EXPORT_UNREADABLE"


# ---- the CLI ------------------------------------------------------------------------------

def test_a_telegram_folder_becomes_a_show_and_resumes_by_id(capsys, chatcom_home, tmp_path, not_a_tty):
    code, out, err = run(capsys, "--json", "new", str(CHAT), "--show", "lake-tg", "--operator", "Dave", "--tz", TZ)
    assert code == 0, err
    info = json.loads(out)
    assert info["source"] == "telegram"
    assert run(capsys, "ingest", "lake-tg", "--export", str(CHAT), "--accept-data-flow")[0] == 0
    week2 = Path(info["inbox"]) / "ChatExport_week2"
    shutil.copytree(CHAT, week2)
    doc = json.loads((week2 / "result.json").read_text("utf-8"))
    last = dict(doc["messages"][-1], id=99, text="see you saturday",
                date_unixtime=str(int(doc["messages"][-1]["date_unixtime"]) + 7 * 86400))
    doc["messages"].append(last)
    (week2 / "result.json").write_text(json.dumps(doc), "utf-8")
    code, out, err = run(capsys, "--json", "episode", "lake-tg", "--dry-run", "--export", str(week2))
    assert code == 0, err
    assert json.loads(out)["source"] == "telegram"


def test_next_weeks_export_resumes_after_the_last_message_by_id():
    from chatcom.core import resume
    week1 = load(CHAT).messages
    cursor = resume.make_cursor(week1, "telegram", UNKEYED)
    doc = json.loads((CHAT / "result.json").read_text("utf-8"))
    doc["messages"].append(dict(doc["messages"][-1], id=99, text="see you saturday",
                                date_unixtime=str(int(doc["messages"][-1]["date_unixtime"]) + 86400)))
    week2 = TG.parse(doc, TZ, key=UNKEYED).messages
    r = resume.resume_point(cursor, week2, UNKEYED, "telegram")
    assert r.method == "id" and [m["text"] for m in r.rows] == ["see you saturday"]


def test_an_account_export_in_the_cli_asks_for_a_thread(capsys, chatcom_home, not_a_tty):
    code, _, err = run(capsys, "--json", "new", str(ACCOUNT), "--operator", "Dave", "--tz", TZ)
    env = json.loads(err)
    assert code == 2 and env["code"] == "CHATCOM_E_THREAD_REQUIRED" and "--thread" in env["fix"]
    code, out, err = run(capsys, "--json", "new", str(ACCOUNT), "--thread", "Lake House Boys", "--operator", "Dave",
                         "--tz", TZ)
    assert code == 0, err
