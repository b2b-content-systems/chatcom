import zipfile

import pytest

from chatcom.adapters import whatsapp
from chatcom.core.errors import ChatcomError
from conftest import WA, TZ

# file: (platform, date_order, clock, messages, system lines, first message time)
MATRIX = {
    "ios_en_US_12h.txt": ("ios", "mdy", "12h", 9, 3, "2026-01-15T21:03:40-05:00"),
    "ios_en_GB_24h.txt": ("ios", "dmy", "24h", 5, 0, "2026-01-15T21:03:40-05:00"),
    "ios_de_DE.txt": ("ios", "dmy", "24h", 4, 0, "2026-01-15T21:03:40-05:00"),
    "ios_pt_BR.txt": ("ios", "dmy", "24h", 3, 0, "2026-01-15T21:03:40-05:00"),
    "android_en_US_12h.txt": ("android", "mdy", "12h", 9, 4, "2026-01-15T21:03:00-05:00"),
    "android_de_DE_24h.txt": ("android", "dmy", "24h", 3, 1, "2026-01-15T21:03:00-05:00"),
    "android_es_ES.txt": ("android", "dmy", "24h", 4, 0, "2026-01-15T21:03:00-05:00"),
    "android_es_MX_12h.txt": ("android", "dmy", "12h", 3, 0, "2026-01-15T21:03:00-05:00"),
    "android_ymd.txt": ("android", "ymd", "24h", 2, 0, "2026-01-15T21:03:00-05:00"),
    "ambiguous_dates_dmy.txt": ("ios", "dmy", "24h", 5, 0, "2026-01-03T10:00:00-05:00"),
    "invisible_marks.txt": ("ios", "mdy", "12h", 2, 0, "2026-01-15T21:00:01-05:00"),
    "ios_export.zip": ("ios", "mdy", "12h", 9, 3, "2026-01-15T21:03:40-05:00"),
    "WhatsApp Chat - Lake House.zip": ("android", "mdy", "12h", 9, 4, "2026-01-15T21:03:00-05:00"),
}


def parse(name, **kw):
    text, display = whatsapp.read_export(WA / name)
    return whatsapp.parse(text, TZ, **kw), display


@pytest.mark.parametrize("name", sorted(MATRIX))
def test_matrix(name):
    platform, order, clock, n, system, first = MATRIX[name]
    r, _ = parse(name)
    assert r.fmt == {"platform": platform, "date_order": order, "clock": clock}
    assert len(r.messages) == n
    assert r.system_lines == system
    assert r.messages[0]["time"] == first
    assert r.unparsed_rate == 0.0
    assert all(len(m["id"]) == 16 for m in r.messages)


def test_ios_details():
    r, _ = parse("ios_en_US_12h.txt")
    by_text = {m["text"]: m for m in r.messages}
    assert r.group_name == "Lake House"
    assert by_text["I said MARCH not may"]["edited"] is True
    assert by_text["here is the list\n1. dave\n2. mike (probably)\n3. me"]["sender"] == "Theo"
    kinds = [m["media"] for m in r.messages if m["media"]]
    assert kinds == ["image", "image"]
    assert sum(m["deleted"] for m in r.messages) == 1
    # a phone-number sender is masked before anything reaches a model
    assert r.masked == {"+1 (555) 010-2233": "Unnamed 1"}
    assert "Unnamed 1" in r.senders()


def test_android_system_lines_with_colons_are_not_senders():
    r, _ = parse("android_en_US_12h.txt")
    senders = set(r.senders())
    assert senders == {"Dave", "Mike", "Theo", "Sam", "Priya"}
    loc = [m for m in r.messages if m["media"] == "location"][0]
    assert loc["text"] == ""  # the maps link is dropped
    poll = [m for m in r.messages if m["media"] == "poll"][0]
    assert poll["text"].startswith("March 6 or March 13?")


def test_same_chat_hashes_the_same_from_ios_and_android():
    ios, _ = parse("ios_en_US_12h.txt")
    android, _ = parse("android_en_US_12h.txt")
    # Android has no seconds, so compare messages whose minute and text agree.
    ios_ids = {m["id"] for m in ios.messages}
    shared = [m for m in android.messages if m["id"] in ios_ids]
    texts = {m["text"] or ("<deleted>" if m["deleted"] else "<media>") for m in shared}
    assert {"ok who is actually coming in march", "me", "probably", "<media>", "<deleted>"} <= texts


def test_same_minute_repeats_survive():
    r, _ = parse("same_minute_k.txt")
    ks = [m for m in r.messages if m["sender"] == "Mike"]
    assert len(ks) == 3 and len({m["id"] for m in ks}) == 3


def test_dst_fall_back_keeps_time_moving_forward():
    r, _ = parse("dst_fallback_new_york.txt")
    times = [m["time"] for m in r.messages]
    assert times == ["2025-11-02T01:30:00-04:00", "2025-11-02T01:55:00-04:00", "2025-11-02T01:05:00-05:00",
                     "2025-11-02T01:40:00-05:00", "2025-11-02T02:10:00-05:00"]


def test_invisible_marks_are_removed():
    r, _ = parse("invisible_marks.txt")
    assert [(m["sender"], m["text"]) for m in r.messages] == [("Dave", "hello there"), ("Mike", "ok")]


def test_garbage_reports_unparsed_rate_over_threshold():
    r, _ = parse("garbage.txt")
    assert r.fmt["date_order"] == "mdy"  # one impossible date does not flip the rest
    assert [m["text"] for m in r.messages][0] == "one real line\n15 January 2026 9pm Dave said hi"
    assert r.unparsed_rate > 0.02
    assert [n for n, _ in r.unparsed] == [3, 4]


def test_aliases_merge_names_and_numbers():
    r, _ = parse("ios_en_US_12h.txt", aliases={"Michael": ["Mike", "+15550102233"]})
    assert "Michael" in r.senders() and "Mike" not in r.senders()
    assert r.masked == {} and "Unnamed 1" not in r.senders()


def test_zip_display_names():
    _, d1 = parse("WhatsApp Chat - Lake House.zip")
    _, d2 = parse("ios_export.zip")
    assert d1 == "Lake House" and d2 == "ios_export"


def test_missing_file_and_bad_zip(tmp_path):
    with pytest.raises(ChatcomError) as e:
        whatsapp.read_export(tmp_path / "nope.txt")
    assert e.value.code == "CHATCOM_E_EXPORT_NOT_FOUND"
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    with pytest.raises(ChatcomError) as e:
        whatsapp.read_export(bad)
    assert e.value.code == "CHATCOM_E_EXPORT_ZIP"
    two = tmp_path / "two.zip"
    with zipfile.ZipFile(two, "w") as zf:
        zf.writestr("a.txt", "x")
        zf.writestr("b.txt", "y")
    with pytest.raises(ChatcomError) as e:
        whatsapp.read_export(two)
    assert e.value.code == "CHATCOM_E_EXPORT_ZIP" and "2 chat text files" in e.value.what


def test_bad_timezone():
    with pytest.raises(ChatcomError) as e:
        whatsapp.parse("", "Mars/Olympus")
    assert e.value.code == "CHATCOM_E_TIMEZONE"


def test_utf16_export(tmp_path):
    p = tmp_path / "u16.txt"
    p.write_bytes("[1/15/26, 9:00:01 PM] Dave: hi\n".encode("utf-16"))
    text, _ = whatsapp.read_export(p)
    assert whatsapp.parse(text, TZ).messages[0]["text"] == "hi"
