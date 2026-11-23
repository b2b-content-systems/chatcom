"""Next Step 3: casting (the cast stage): plan, prompts, content keys, the
painter rules, cutouts, consent and the cache. No network: the replay painter
draws placeholders and the Gemini painter runs over a fake transport."""
import base64
import json
import shutil
import struct
from pathlib import Path

import pytest

from chatcom import app, casting, cli
from chatcom.adapters import cutout, store
from chatcom.adapters.painter import GeminiPainter, ReplayPainter, SwitchModel
from chatcom.adapters.writers import llm_fallback as lf
from chatcom.core import casting as rules
from chatcom.core.errors import ChatcomError
from chatcom.core.schema import problems
from conftest import LAKE, TZ
from test_screenshots import png

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")


def run(capsys, *argv):
    code = cli.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


@pytest.fixture
def not_a_tty(monkeypatch):
    monkeypatch.setattr(cli, "_tty", lambda: False)


def member(name, **kw):
    return {"name": name, "role": None, "traits": [], "texting_voice": None, "source": "behavior",
            "reason": "no_photo_supplied", "aliases": [], **kw}


def sheet(members, style=None):
    return {"schema_version": 2, "image_model": None, "style_sheet": style, "poses": list(rules.POSES),
            "members": [{"name": m["name"], "source": m["source"], "reason": m["reason"], "photo": m.get("photo"),
                         "poses": {}} for m in members],
            "backgrounds": [{"id": "home_base", "description": "home", "file": None, "key": None}]}


# ---- the plan and the prompts ----------------------------------------------------------------
# Value: protects=which images a show pays for and what the model is told; fails_when=a pose is skipped or drawn
# twice, a behavior prompt names a real person, or the episode cap is ignored; why_new=new step; seam=none;
# no_claim=what the image model draws

def test_the_plan_counts_every_image_once_in_order():
    ms = [member("Dave", role="The 2 AM Philosopher", traits=["sends essays at night"]), member("Mike")]
    locs = [{"id": "lake", "description": "a lake"}, {"id": "bar", "description": "a bar"},
            {"id": "pier", "description": "a pier"}]
    calls = rules.plan(sheet(ms), ms, "sitcom", "THE LAKE HOUSE BOYS", locs)
    kinds = [c.kind for c in calls]
    assert kinds[0] == "style" and kinds.count("pose") == 12 and kinds.count("background") == 1 + 2
    assert [c.pose for c in calls if c.member == "Dave"] == list(rules.POSES)
    assert calls[1].refs == ["style"] and calls[2].refs == ["style", "pose:Dave:neutral"]


def test_behavior_casting_invents_an_original_character():
    m = member("Dave", role="The 2 AM Philosopher", traits=["sends essays at night"], texting_voice="all lowercase")
    first = rules.pose_prompt("anime", m, "neutral", from_photo=False, first=True)
    assert "original character (not a real or famous person)" in first and "The 2 AM Philosopher" in first
    assert "Dave" not in first  # the name is never sent
    assert "chroma-key green" in first and "city-pop anime" in first
    photo = rules.pose_prompt("sitcom", m, "neutral", from_photo=True, first=True)
    assert "reference photo" in photo
    later = rules.pose_prompt("sitcom", m, "laughing", from_photo=True, first=False)
    assert "same character" in later and "reference photo" not in later


def test_the_content_key_names_everything_that_decides_the_picture():
    c = rules.Call("pose", "p", member="A", pose="neutral", refs=["style"])
    k = rules.content_key("m1", c, ["aa"])
    assert k == rules.content_key("m1", c, ["aa"]) and len(k) == 64
    assert k != rules.content_key("m2", c, ["aa"]) and k != rules.content_key("m1", c, ["bb"])


def test_drawn_poses_and_backgrounds_are_not_planned_again():
    ms = [member("Mike")]
    s = sheet(ms, style="cast/style.png")
    s["members"][0]["poses"] = {p: {"file": "x", "key": "0" * 64} for p in rules.POSES}
    s["backgrounds"][0].update(file="b.png", key="0" * 64)
    assert rules.plan(s, ms, "sitcom", None) == []


# ---- the cutout ---------------------------------------------------------------------------------

@needs_ffmpeg
def test_the_cutout_makes_the_green_transparent_and_keeps_blue(tmp_path):
    p = ReplayPainter().paint("On a flat, even, pure chroma-key green background", [], "3:4")
    src = tmp_path / "pose.png"
    src.write_bytes(p.data)
    cut = cutout.key(src, tmp_path / "cut.png")
    assert cut.read_bytes()[25] == 6  # PNG colour type 6: RGBA
    import subprocess
    raw = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(cut), "-f", "rawvideo", "-pix_fmt", "rgba",
                          "-"], capture_output=True, check=True).stdout
    w, h = 300, 400
    alpha = lambda x, y: raw[(y * w + x) * 4 + 3]  # noqa: E731
    assert alpha(0, 0) == 0 and alpha(w - 1, h - 1) == 0  # the green is gone
    assert alpha(w // 2, h // 2) == 255  # the figure stays
    r, g, b = (int(cutout.corner_colour(src)[i:i + 2], 16) for i in (2, 4, 6))
    assert g > 150 and r < 30  # the sampled key is the green


# ---- the Gemini painter over a fake transport ---------------------------------------------------

def image_ok(model):
    return 200, {"modelVersion": model, "candidates": [{"content": {"parts": [
        {"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(b"\xff\xd8\xffjpeg").decode()}}]},
        "finishReason": "STOP"}]}


def test_the_gemini_painter_rules(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    calls = []

    def t(url, headers, payload):
        calls.append(url)
        return image_ok("gemini-nano-banana-2.1")
    p = GeminiPainter(transport=t)
    got = p.paint("draw", [(b"ref", "image/png")], "3:4")
    assert got.data == b"\xff\xd8\xffjpeg" and got.mime == "image/jpeg" and "gemini-nano-banana-2.1" in calls[0]
    gone = GeminiPainter(transport=lambda u, h, pl: (404, {"error": {"message": "not found"}}))
    with pytest.raises(SwitchModel):
        gone.paint("draw", [], "3:4")
    busy = GeminiPainter(transport=lambda u, h, pl: (503, {}))
    busy.backoff = (0, 0, 0)
    with pytest.raises(SwitchModel):
        busy.paint("draw", [], "3:4")
    quota = GeminiPainter(transport=lambda u, h, pl: (429, {}))
    quota.backoff = (0,)
    with pytest.raises(ChatcomError) as e:
        quota.paint("draw", [], "3:4")
    assert e.value.code == "CHATCOM_E_PAINTER_UNAVAILABLE" and e.value.exit_code == 4
    refused = GeminiPainter(transport=lambda u, h, pl: (200, {"candidates": [{"finishReason": "IMAGE_SAFETY"}]}))
    assert refused.paint("draw", [], "3:4").refused


# ---- the cast stage, end to end -----------------------------------------------------------------
# Value: protects=consent, the exact call count, cast.json untouched until commit, the refused-photo fallback, the
# model switch and the cache; fails_when=any of those rules is skipped; why_new=new step; seam=ReplayPainter, fake
# transport; no_claim=image quality

def drafted(capsys, lake_show, tmp_path, beats_s01e01, photo_of=None):
    if photo_of:
        cast = json.loads(lake_show.cast_path.read_text("utf-8"))
        pic = tmp_path / "friend.png"
        pic.write_bytes(png(40, 50, shot="2026:01:09 19:00:00"))
        for m in cast["members"]:
            if m["name"] == photo_of:
                m.update(source="photo", reason=None, photo=str(pic))
        lake_show.cast_path.write_text(json.dumps(cast), "utf-8")
    beats = tmp_path / "beats.json"
    beats.write_text(json.dumps(beats_s01e01), "utf-8")
    assert run(capsys, "episode", "lake-house", "--export", str(LAKE), "--writer", "replay", "--replay",
               str(beats))[0] == 0


@needs_ffmpeg
def test_cast_asks_first_then_draws_everything_once(capsys, lake_show, tmp_path, beats_s01e01, not_a_tty):
    drafted(capsys, lake_show, tmp_path, beats_s01e01)
    before = lake_show.cast_path.read_bytes()
    code, out, _ = run(capsys, "--json", "cast", "lake-house", "--painter", "replay")
    env = json.loads(out)
    assert code == 6 and "nothing leaves" in env["notice_text"]
    n = int(env["next_command"].rsplit(" ", 1)[1])
    code, _, err = run(capsys, "--json", "cast", "lake-house", "--painter", "replay", "--accept-data-flow",
                       "--approve-calls", str(n - 1))
    assert code == 6 and f"--approve-calls {n}" in json.loads(err)["fix"]
    code, out, err = run(capsys, "--json", "cast", "lake-house", "--painter", "replay", "--accept-data-flow",
                         "--approve-calls", str(n))
    assert code == 0, err
    res = json.loads(out)
    assert res["stage"] == "cast" and res["paid"] == n and res["calls_billed"] == 0
    nxt = json.loads(Path(res["cast_next"]).read_text("utf-8"))
    assert problems("cast", nxt) == [] and nxt["image_model"] == "replay-painter"
    assert all(set(m["poses"]) == set(rules.POSES) for m in nxt["members"])
    assert any(b["id"] == "lake_house" and b["file"] for b in nxt["backgrounds"])
    for m in nxt["members"]:
        cut = (lake_show.dir / m["poses"]["laughing"]["file"]).read_bytes()
        assert cut[25] == 6
    assert lake_show.cast_path.read_bytes() == before  # cast.json changes only at committed
    st = json.loads(run(capsys, "--json", "status", "lake-house")[1])
    assert st["stage"] == "cast"
    code, out, _ = run(capsys, "--json", "cast", "lake-house", "--painter", "replay")
    assert code == 0 and json.loads(out)["calls"] == 0  # nothing left to draw


@needs_ffmpeg
def test_a_rerun_after_a_stop_pays_nothing_for_cached_images(capsys, lake_show, tmp_path, beats_s01e01, not_a_tty):
    drafted(capsys, lake_show, tmp_path, beats_s01e01)
    n = int(json.loads(run(capsys, "--json", "cast", "lake-house", "--painter", "replay")[1])["next_command"]
            .rsplit(" ", 1)[1])
    first = json.loads(run(capsys, "--json", "cast", "lake-house", "--painter", "replay", "--accept-data-flow",
                           "--approve-calls", str(n))[1])
    Path(first["cast_next"]).unlink()  # as if the run had stopped before writing
    st = lake_show.state_path("S01E01")
    s = json.loads(st.read_text("utf-8"))
    s["stage"] = "drafted"
    st.write_text(json.dumps(s), "utf-8")
    again = json.loads(run(capsys, "--json", "cast", "lake-house", "--painter", "replay", "--accept-data-flow",
                           "--approve-calls", str(n))[1])
    assert again["paid"] == 0 and again["calls"] == n


@needs_ffmpeg
def test_a_refused_photo_is_cast_from_behavior(lake_show, tmp_path, beats_s01e01, capsys, not_a_tty):
    drafted(capsys, lake_show, tmp_path, beats_s01e01, photo_of="Mike")
    said = []
    n = len(casting.plan_for(lake_show, json.loads((lake_show.episode_dir("S01E01") / "bible.next.json").read_text()),
                             json.loads(lake_show.cast_path.read_text()),
                             json.loads((lake_show.episode_dir("S01E01") / "beats.json").read_text())))
    painter = ReplayPainter(refuse_photos=True)
    with pytest.raises(ChatcomError) as e:  # the refusal and the redraw from behavior cost 1 more than approved
        casting.cast(lake_show, painter, accepted=True, approve_calls=n, say=said.append)
    assert e.value.code == "CHATCOM_E_AWAITING_CONFIRMATION" and painter.calls == n
    res = casting.cast(lake_show, painter, accepted=True, approve_calls=n, say=said.append)  # the rerun: cache
    assert painter.calls == n + 1  # only the one image the first run could not afford
    assert res["refused"] == ["Mike"] and any("declined Mike" in s for s in said)
    nxt = json.loads(Path(res["cast_next"]).read_text("utf-8"))
    mike = next(m for m in nxt["members"] if m["name"] == "Mike")
    assert (mike["source"], mike["reason"], mike["photo"]) == ("behavior", "provider_refused", None)
    ref = next((lake_show.dir / "cast" / "refs").rglob("*.png")).read_bytes()
    assert b"eXIf" not in ref  # the reference photo was a clean copy


@needs_ffmpeg
def test_a_gone_model_moves_the_whole_batch_to_the_fallback(lake_show, tmp_path, beats_s01e01, capsys, not_a_tty,
                                                             monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    drafted(capsys, lake_show, tmp_path, beats_s01e01)
    replay = ReplayPainter()
    used = []

    def t(url, headers, payload):
        model = url.split("/models/")[1].split(":")[0]
        used.append(model)
        if model == "gemini-nano-banana-2.1":
            return 404, {"error": {"message": "gone"}}
        prompt = payload["contents"][0]["parts"][-1]["text"]
        data = replay.paint(prompt, [], payload["generationConfig"].get("imageConfig", {}).get("aspectRatio", "3:4")).data
        return 200, {"modelVersion": model, "candidates": [{"content": {"parts": [
            {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(data).decode()}}]}, "finishReason": "STOP"}]}
    ep = lake_show.episode_dir("S01E01")
    n = len(casting.plan_for(lake_show, json.loads((ep / "bible.next.json").read_text()),
                             json.loads(lake_show.cast_path.read_text()), json.loads((ep / "beats.json").read_text())))
    p = GeminiPainter(transport=t)
    res = casting.cast(lake_show, p, accepted=True, approve_calls=n, say=lambda m: None)
    assert p.calls == n  # the 404 was not billed
    assert res["model"] == "gemini-3.1-flash-image" and used[0] == "gemini-nano-banana-2.1"
    assert set(used[1:]) == {"gemini-3.1-flash-image"}
    nxt = json.loads(Path(res["cast_next"]).read_text("utf-8"))
    assert nxt["image_model"] == "gemini-3.1-flash-image"


def test_cast_needs_a_draft(capsys, lake_show, not_a_tty):
    code, _, err = run(capsys, "--json", "cast", "lake-house", "--painter", "replay")
    assert code == 2 and json.loads(err)["code"] == "CHATCOM_E_STATE"



def test_members_cast_from_texting_start_from_different_looks():
    ms = [member(n) for n in ("A", "B", "C", "D", "E", "F", "G", "H")]
    firsts = [c.prompt for c in rules.plan(sheet(ms), ms, "sitcom", None) if c.pose == "neutral"]
    hints = [p.split("Start from this look: ")[1].split(";")[0] for p in firsts]
    assert len(set(hints)) == 8


def test_a_cut_off_image_is_retried_with_more_room_and_every_call_is_counted(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    replies = [(200, {"candidates": [{"content": {"parts": []}, "finishReason": "MAX_TOKENS"}]}),
               image_ok("gemini-nano-banana-2.1")]
    seen = []

    def t(url, headers, payload):
        seen.append(payload["generationConfig"]["maxOutputTokens"])
        return replies.pop(0)
    p = GeminiPainter(transport=t)
    assert p.paint("draw", [], "3:4").data and p.calls == 2 and seen[1] > seen[0]



# ---- the approved count is a hard cap (review: money) --------------------------------------------

@needs_ffmpeg
def test_billed_calls_never_exceed_the_approved_count(lake_show, tmp_path, beats_s01e01, capsys, not_a_tty,
                                                      monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    drafted(capsys, lake_show, tmp_path, beats_s01e01)
    replay = ReplayPainter()
    hits, billed = [], []

    def t(url, headers, payload):
        hits.append(1)
        model = url.split("/models/")[1].split(":")[0]
        if model == "gemini-nano-banana-2.1" and len(hits) > 3:
            return 404, {"error": {"message": "gone"}}  # a switch after 3 paid calls: the batch would restart
        billed.append(1)
        prompt = payload["contents"][0]["parts"][-1]["text"]
        data = replay.paint(prompt, [], "3:4").data
        return 200, {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png",
                     "data": base64.b64encode(data).decode()}}]}, "finishReason": "STOP"}]}
    ep = lake_show.episode_dir("S01E01")
    n = len(casting.plan_for(lake_show, json.loads((ep / "bible.next.json").read_text()),
                             json.loads(lake_show.cast_path.read_text()), json.loads((ep / "beats.json").read_text())))
    p = GeminiPainter(transport=t)
    with pytest.raises(ChatcomError) as e:
        casting.cast(lake_show, p, accepted=True, approve_calls=n, say=lambda m: None)
    assert e.value.code == "CHATCOM_E_AWAITING_CONFIRMATION" and e.value.side_effects == "partial"
    assert p.calls == len(billed) <= n  # answered calls are billed and capped; the 404s were not billed
    st = json.loads(lake_show.state_path("S01E01").read_text("utf-8"))
    assert st["image_calls"] == p.calls and st["stage"] == "drafted"  # the ledger is written even on a stop


@needs_ffmpeg
def test_a_quota_stop_still_records_the_calls_it_paid_for(lake_show, tmp_path, beats_s01e01, capsys, not_a_tty,
                                                          monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    drafted(capsys, lake_show, tmp_path, beats_s01e01)
    replay = ReplayPainter()
    hits = []

    def t(url, headers, payload):
        hits.append(1)
        if len(hits) > 2:
            return 429, {}
        data = replay.paint(payload["contents"][0]["parts"][-1]["text"], [], "3:4").data
        return 200, {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png",
                     "data": base64.b64encode(data).decode()}}]}, "finishReason": "STOP"}]}
    ep = lake_show.episode_dir("S01E01")
    n = len(casting.plan_for(lake_show, json.loads((ep / "bible.next.json").read_text()),
                             json.loads(lake_show.cast_path.read_text()), json.loads((ep / "beats.json").read_text())))
    p = GeminiPainter(transport=t)
    p.backoff = (0,)
    with pytest.raises(ChatcomError) as e:
        casting.cast(lake_show, p, accepted=True, approve_calls=n, say=lambda m: None)
    assert e.value.code == "CHATCOM_E_PAINTER_UNAVAILABLE"
    assert json.loads(lake_show.state_path("S01E01").read_text("utf-8"))["image_calls"] == p.calls == 2



# ---- casting review fixes -----------------------------------------------------------------------

def test_a_new_location_in_the_cold_open_is_drawn():
    beats = {"cold_open": {"background": "pier", "new_location": {"id": "pier", "description": "a pier"}},
             "scenes": [{"background": "lake", "new_location": {"id": "lake", "description": "a lake"}}]}
    assert [l["id"] for l in casting.new_locations(beats)] == ["pier", "lake"]


@needs_ffmpeg
def test_the_replay_painter_never_adds_to_a_show_cast_with_gemini(lake_show, tmp_path, beats_s01e01, capsys,
                                                                  not_a_tty):
    drafted(capsys, lake_show, tmp_path, beats_s01e01)
    cast = json.loads(lake_show.cast_path.read_text("utf-8"))
    cast["image_model"] = "gemini-nano-banana-2.1"
    lake_show.cast_path.write_text(json.dumps(cast), "utf-8")
    ep = lake_show.episode_dir("S01E01")
    n = len(casting.plan_for(lake_show, json.loads((ep / "bible.next.json").read_text()), cast,
                             json.loads((ep / "beats.json").read_text())))
    with pytest.raises(ChatcomError) as e:
        casting.cast(lake_show, ReplayPainter(), accepted=True, approve_calls=n, say=lambda m: None)
    assert e.value.code == "CHATCOM_E_USAGE" and not (lake_show.dir / "cache" / "images").exists()


def test_an_invalid_key_says_check_the_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    p = GeminiPainter(transport=lambda u, h, pl: (400, {"error": {"message": "API key not valid. API_KEY_INVALID"}}))
    with pytest.raises(ChatcomError) as e:
        p.paint("draw", [], "3:4")
    assert "GEMINI_API_KEY" in e.value.fix and "bug" not in e.value.fix and p.calls == 0


def test_an_unreadable_drawing_is_a_coded_error(tmp_path):
    bad = tmp_path / "x.png"
    bad.write_bytes(b"not an image")
    with pytest.raises(ChatcomError) as e:
        cutout.corner_colour(bad)
    assert e.value.code == "CHATCOM_E_IMAGE_UNREADABLE"


def test_look_hints_stay_distinct_past_eight_members():
    from chatcom.core import casting as rules
    hints = [rules.look_hint(i) for i in range(256)]
    assert len(set(hints)) == 256
    sparse = {"name": "x", "source": "behavior"}
    calls = rules.plan({"members": [], "backgrounds": [], "style_sheet": "s.png"},
                       [dict(sparse, name=f"m{i}") for i in range(9)], "sitcom", None)
    neutral = [c for c in calls if c.pose == "neutral"]
    assert len({rules.content_key("m", c, []) for c in neutral}) == 9



# ---- red-team fixes -----------------------------------------------------------------------------

def test_names_and_nicknames_never_reach_a_prompt():
    from chatcom.core import casting as rules
    out = rules.scrub("Dave's lake house, near DAVE and Davey; Big D was there", ["Dave", "Big D"])
    assert out == "a friend's lake house, near a friend and Davey; a friend was there"


def test_a_timed_out_image_call_stays_counted(monkeypatch):
    from chatcom.adapters import painter as painter_mod
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(painter_mod.time, "sleep", lambda s: None)

    def slow(u, h, pl):
        raise painter_mod.MaybeBilled("network: timed out")
    p = GeminiPainter(transport=slow)
    p.budget = 2
    with pytest.raises(painter_mod.BudgetSpent):
        p.paint("draw", [], "3:4")
    assert p.calls == 2  # each sent call counts, and the cap stops the retries


def test_an_error_stops_the_calls_not_yet_started():
    import threading
    from chatcom import casting as svc
    started, lock = [], threading.Lock()

    def fn(x):
        with lock:
            started.append(x)
        if x == 0:
            raise RuntimeError("stop")
        return x
    with pytest.raises(RuntimeError):
        svc._each(fn, list(range(50)), 1)
    assert started == [0]


@needs_ffmpeg
def test_prompts_sent_to_the_painter_hold_no_cast_name(lake_show, tmp_path, beats_s01e01, capsys, not_a_tty):
    drafted(capsys, lake_show, tmp_path, beats_s01e01)
    ep = lake_show.episode_dir("S01E01")
    bible = json.loads((ep / "bible.next.json").read_text())
    names = [n for m in bible["cast"] for n in (m["name"], *m.get("aliases", []))]
    cast = json.loads(lake_show.cast_path.read_text("utf-8"))
    n = len(casting.plan_for(lake_show, bible, cast, json.loads((ep / "beats.json").read_text())))
    seen = []
    painter = ReplayPainter()
    real = painter.paint
    painter.paint = lambda prompt, refs, aspect: (seen.append(prompt), real(prompt, refs, aspect))[1]
    casting.cast(lake_show, painter, accepted=True, approve_calls=n, say=lambda m: None)
    import re
    assert seen and not any(re.search(rf"(?<!\w){re.escape(nm)}(?!\w)", s, re.I) for s in seen for nm in names)


# ---- casting follow-ups: green check, despill, no-image retry, appearance notes -------------------
# Value: protects=a figure is never keyed out by a non-green corner, hair loses its green fringe while green inside
# the figure stays, a picture-less reply is asked once more, the operator's look notes reach the first drawing;
# fails_when=any of those regress; why_new=TODOS casting follow-ups; seam=ReplayPainter, fake transport, ffmpeg;
# no_claim=how a real model draws

def _rgba(path, w):
    import subprocess
    raw = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "rgba",
                          "-"], capture_output=True, check=True).stdout
    return lambda x, y: tuple(raw[(y * w + x) * 4:(y * w + x) * 4 + 4])


@needs_ffmpeg
def test_a_drawing_with_no_green_corner_is_not_keyed(tmp_path):
    src = tmp_path / "red.png"
    src.write_bytes(ReplayPainter().paint("a flat scene", [], "3:4").data)  # no green asked for: a flat colour
    if cutout.is_green(cutout.corner_colour(src)):
        pytest.skip("the replay colour happened to be green")
    with pytest.raises(cutout.NoGreen) as e:
        cutout.key(src, tmp_path / "cut.png")
    assert e.value.code == "CHATCOM_E_IMAGE_UNREADABLE" and not (tmp_path / "cut.png").exists()
    assert cutout.is_green("0x00b140") and not cutout.is_green("0x808080") and not cutout.is_green("0x2050a0")


@needs_ffmpeg
def test_the_despill_clears_the_green_edge_and_leaves_the_figure(tmp_path):
    import subprocess
    src = tmp_path / "pose.png"
    # a coral top, with a 4px green-tinted fringe on its left edge (like hair against the green)
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=c=0x00B140:s=300x400", "-vf",
                    "format=rgb24,drawbox=x=100:y=80:w=100:h=240:color=0xFF7F50:t=fill,"
                    "drawbox=x=96:y=76:w=4:h=248:color=0x60B070:t=fill", "-frames:v", "1", "-y", str(src)], check=True)
    px = _rgba(cutout.key(src, tmp_path / "cut.png"), 300)
    r, g, b, a = px(97, 200)
    assert a == 255 and g <= max(r, b)  # the fringe is no longer green
    assert all(abs(x - y) <= 2 for x, y in zip(px(150, 200), (255, 127, 80, 255)))  # inside: unchanged
    assert px(5, 5)[3] == 0


def test_no_starting_look_is_green_and_every_pose_asks_for_no_green():
    assert not any("green" in o for o in rules.OUTFIT)
    for pose in rules.POSES:
        assert "Nothing green on the character" in rules.pose_prompt("sitcom", member("A"), pose, from_photo=False,
                                                                       first=pose == "neutral")


def no_image(model="gemini-nano-banana-2.1"):
    return 200, {"modelVersion": model, "candidates": [{"content": {"parts": []}, "finishReason": "NO_IMAGE"}]}


def test_a_reply_with_no_image_is_asked_once_more(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    replies = [no_image(), image_ok("gemini-nano-banana-2.1")]
    p = GeminiPainter(transport=lambda u, h, pl: replies.pop(0))
    assert p.paint("draw", [], "3:4").data and p.calls == 2
    twice = [no_image(), no_image()]
    p = GeminiPainter(transport=lambda u, h, pl: twice.pop(0))
    assert p.paint("draw", [], "3:4").refused and p.calls == 2  # then it is cached as a refusal, never paid again
    text_only = [(200, {"candidates": [{"content": {"parts": [{"text": "here you go"}]}, "finishReason": "STOP"}]}),
                 image_ok("gemini-nano-banana-2.1")]
    p = GeminiPainter(transport=lambda u, h, pl: text_only.pop(0))
    assert p.paint("draw", [], "3:4").data and p.calls == 2
    safety = [(200, {"candidates": [{"finishReason": "IMAGE_SAFETY"}]})]
    p = GeminiPainter(transport=lambda u, h, pl: safety.pop(0))
    assert p.paint("draw", [], "3:4").refused and p.calls == 1  # a policy refusal is not asked again


@needs_ffmpeg
def test_a_pose_with_no_green_behind_it_is_drawn_once_more(lake_show, tmp_path, beats_s01e01, capsys, not_a_tty):
    drafted(capsys, lake_show, tmp_path, beats_s01e01)
    ep = lake_show.episode_dir("S01E01")
    n = len(casting.plan_for(lake_show, json.loads((ep / "bible.next.json").read_text()),
                             json.loads(lake_show.cast_path.read_text()), json.loads((ep / "beats.json").read_text())))
    import dataclasses
    import subprocess
    red = tmp_path / "red.png"
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=c=0xC03030:s=300x400",
                    "-frames:v", "1", "-y", str(red)], check=True)
    painter = ReplayPainter()
    real, bad = painter.paint, []

    def paint(prompt, refs, aspect):
        got = real(prompt, refs, aspect)  # counted and capped like any call
        if "Pose: standing relaxed" in prompt and not bad:  # the first neutral pose comes back with no green
            bad.append(prompt)
            return dataclasses.replace(got, data=red.read_bytes())
        return got
    painter.paint = paint
    said = []
    with pytest.raises(ChatcomError) as e:  # the redraw is one call over the exact approved count
        casting.cast(lake_show, painter, accepted=True, approve_calls=n, say=said.append)
    assert e.value.code == "CHATCOM_E_AWAITING_CONFIRMATION" and painter.calls == n
    assert any("no green background" in s for s in said)
    res = casting.cast(lake_show, painter, accepted=True, approve_calls=n, say=said.append)
    assert painter.calls == n + 1 and len(bad) == 1
    nxt = json.loads(Path(res["cast_next"]).read_text("utf-8"))
    assert all(len(m["poses"]) == len(rules.POSES) for m in nxt["members"])


@needs_ffmpeg
def test_appearance_notes_reach_the_first_drawing_with_no_name(lake_show, tmp_path, beats_s01e01, capsys,
                                                                not_a_tty):
    drafted(capsys, lake_show, tmp_path, beats_s01e01)
    setup = json.loads(lake_show.setup_path.read_text("utf-8"))
    setup["looks"] = {"mike": "beard, tall, Mike's red cap"}  # any case; a name inside the note is scrubbed
    lake_show.setup_path.write_text(json.dumps(setup), "utf-8")
    assert not problems("setup", {"operator_sender": "Dave", "looks": setup["looks"]})
    ep = lake_show.episode_dir("S01E01")
    bible = json.loads((ep / "bible.next.json").read_text())
    assert casting.looks_for(lake_show, bible["cast"]) == {"Mike": "beard, tall, Mike's red cap"}
    n = len(casting.plan_for(lake_show, bible, json.loads(lake_show.cast_path.read_text()),
                             json.loads((ep / "beats.json").read_text())))
    seen = []
    painter = ReplayPainter()
    real = painter.paint
    painter.paint = lambda prompt, refs, aspect: (seen.append(prompt), real(prompt, refs, aspect))[1]
    casting.cast(lake_show, painter, accepted=True, approve_calls=n, say=lambda m: None)
    noted = [s for s in seen if "beard, tall" in s]
    assert len(noted) == 1 and "standing relaxed" in noted[0]  # the neutral pose only
    assert "a friend's red cap" in noted[0] and "Mike" not in noted[0]


# ---- recast (casting part 2) ---------------------------------------------------------------------
# Value: protects=a redraw asks first with its exact count, never comes back from the cache, never overwrites an
# episode's pictures, and a whole recast can leave a retired model; fails_when=recast reuses the old drawing, skips
# consent, touches files an episode used or keeps the old pin; why_new=casting part 2; seam=ReplayPainter;
# no_claim=how a real model redraws

def cast_all(capsys, lake_show, tmp_path, beats_s01e01, photo_of=None):
    drafted(capsys, lake_show, tmp_path, beats_s01e01, photo_of=photo_of)
    ep = lake_show.episode_dir("S01E01")
    n = len(casting.plan_for(lake_show, json.loads((ep / "bible.next.json").read_text()),
                             json.loads(lake_show.cast_path.read_text()), json.loads((ep / "beats.json").read_text())))
    casting.cast(lake_show, ReplayPainter(), accepted=True, approve_calls=n, say=lambda m: None)
    return ep, n


def test_a_take_changes_the_key_and_no_take_changes_nothing():
    c = rules.Call("pose", "p", member="A", pose="neutral")
    assert rules.content_key("m", c, []) == rules.content_key("m", rules.Call("pose", "p", member="A", pose="neutral",
                                                                                take=""), [])
    assert rules.content_key("m", c, []) != rules.content_key("m", rules.Call("pose", "p", member="A",
                                                                                pose="neutral", take="ab12"), [])


@needs_ffmpeg
def test_recasting_one_member_asks_first_then_draws_a_new_take(lake_show, tmp_path, beats_s01e01, capsys,
                                                                not_a_tty):
    ep, _ = cast_all(capsys, lake_show, tmp_path, beats_s01e01)
    before = json.loads((ep / "cast.next.json").read_text())
    old = next(m for m in before["members"] if m["name"] == "Mike")["poses"]["neutral"]["file"]
    painter = ReplayPainter()
    with pytest.raises(ChatcomError) as e:
        casting.recast(lake_show, painter, member="mike", accepted=False, approve_calls=None, say=lambda m: None)
    assert e.value.code == "CHATCOM_E_AWAITING_CONFIRMATION" and e.value.context["calls"] == len(rules.POSES)
    assert "--member mike" in e.value.context["next_command"] and painter.calls == 0
    assert json.loads((ep / "cast.next.json").read_text()) == before  # asking wrote nothing
    res = casting.recast(lake_show, painter, member="mike", accepted=True, approve_calls=len(rules.POSES),
                         say=lambda m: None)
    assert painter.calls >= 1  # the neutral pose is never the cached one
    nxt = json.loads(Path(res["cast_next"]).read_text())
    mike = next(m for m in nxt["members"] if m["name"] == "Mike")
    assert mike["take"] == 1 and len(mike["poses"]) == len(rules.POSES)
    assert mike["poses"]["neutral"]["file"].endswith("neutral.t1.png") and (lake_show.dir / old).exists()
    others = [m for m in nxt["members"] if m["name"] != "Mike"]
    assert all(m["poses"] == next(b for b in before["members"] if b["name"] == m["name"])["poses"] for m in others)
    assert json.loads(lake_show.state_path("S01E01").read_text())["stage"] == "cast"
    assert not problems("cast", nxt)


@needs_ffmpeg
def test_recasting_a_member_from_a_photo_or_from_texting(lake_show, tmp_path, beats_s01e01, capsys, not_a_tty):
    ep, _ = cast_all(capsys, lake_show, tmp_path, beats_s01e01)
    pic = tmp_path / "mike.png"
    pic.write_bytes(png(40, 50, shot="2026:01:09 19:00:00"))
    res = casting.recast(lake_show, ReplayPainter(), member="Mike", photo=str(pic), accepted=True,
                         approve_calls=len(rules.POSES), say=lambda m: None)
    mike = next(m for m in json.loads(Path(res["cast_next"]).read_text())["members"] if m["name"] == "Mike")
    assert (mike["source"], mike["reason"], mike["photo"]) == ("photo", None, str(pic))
    res = casting.recast(lake_show, ReplayPainter(), member="Mike", no_photo=True, accepted=True,
                         approve_calls=len(rules.POSES), say=lambda m: None)
    mike = next(m for m in json.loads(Path(res["cast_next"]).read_text())["members"] if m["name"] == "Mike")
    assert (mike["source"], mike["reason"], mike["photo"], mike["take"]) == ("behavior", "operator_choice", None, 2)


@needs_ffmpeg
def test_a_whole_recast_redraws_everything_and_picks_the_model_again(lake_show, tmp_path, beats_s01e01, capsys,
                                                                      not_a_tty):
    ep, n = cast_all(capsys, lake_show, tmp_path, beats_s01e01)
    with pytest.raises(ChatcomError) as e:
        casting.recast(lake_show, ReplayPainter(), accepted=True, approve_calls=n - 1, say=lambda m: None)
    assert e.value.context["calls"] == n  # the whole show: style, every pose, every set
    res = casting.recast(lake_show, ReplayPainter(), accepted=True, approve_calls=n, say=lambda m: None)
    nxt = json.loads(Path(res["cast_next"]).read_text())
    assert nxt["take"] == 1 and nxt["style_sheet"].endswith("style.t1.png") and nxt["image_model"] == "replay-painter"
    assert all(b["file"].endswith(".t1.png") for b in nxt["backgrounds"] if b["file"])
    assert all(m["take"] == 1 and len(m["poses"]) == len(rules.POSES) for m in nxt["members"])


def test_recast_refuses_unclear_requests(capsys, lake_show, tmp_path, beats_s01e01, not_a_tty):
    drafted(capsys, lake_show, tmp_path, beats_s01e01)
    # Value (row added by /ship coverage audit): protects=--photo with --no-photo is refused even past argparse;
    # fails_when=the guard goes and --no-photo silently wins; why_new=no row passed both; seam=none; no_claim=argparse
    pic = tmp_path / "both.png"
    pic.write_bytes(png(40, 50))
    for kw, code in (({"member": "Nobody"}, "CHATCOM_E_UNKNOWN_MEMBER"), ({"no_photo": True}, "CHATCOM_E_USAGE"),
                     ({"member": "Mike", "photo": str(tmp_path / "missing.png")}, "CHATCOM_E_IMAGE_UNREADABLE"),
                     ({"member": "Mike", "photo": str(pic), "no_photo": True}, "CHATCOM_E_USAGE")):
        with pytest.raises(ChatcomError) as e:
            casting.recast(lake_show, ReplayPainter(), accepted=False, approve_calls=None, say=lambda m: None, **kw)
        assert e.value.code == code
    code, out, err = run(capsys, "recast", "lake-house", "--member", "Mike", "--painter", "replay")
    assert code == 6 and "Mike again (6 poses), plus the images this episode still needs" in out + err


# ---- recast coverage (added by the /ship coverage audit) -----------------------------------------

def test_recast_never_reopens_an_episode_past_cast(capsys, lake_show, tmp_path, beats_s01e01, not_a_tty):
    # Value: protects=recast refuses once an episode is rendered, and writes and pays nothing; fails_when=the stage
    # guard goes and a rendered episode drops back to drafted; why_new=every recast test runs at drafted or cast;
    # seam=none; no_claim=the render stage itself (stage set by hand in state.json)
    drafted(capsys, lake_show, tmp_path, beats_s01e01)
    sp = lake_show.state_path("S01E01")
    st = json.loads(sp.read_text("utf-8"))
    st["stage"] = "rendered"
    sp.write_text(json.dumps(st), "utf-8")
    before = sp.read_bytes()
    painter = ReplayPainter()
    with pytest.raises(ChatcomError) as e:
        casting.recast(lake_show, painter, member="Mike", accepted=True, approve_calls=len(rules.POSES),
                       say=lambda m: None)
    assert e.value.code == "CHATCOM_E_STATE" and e.value.context["stage"] == "rendered"
    assert sp.read_bytes() == before and painter.calls == 0
    assert not (lake_show.episode_dir("S01E01") / "cast.next.json").exists()


@needs_ffmpeg
def test_a_whole_recast_leaves_a_retired_model(lake_show, tmp_path, beats_s01e01, capsys, not_a_tty, monkeypatch):
    # Value: protects=a member recast keeps the pin (and its error names recast), a whole recast drops it and lands
    # on the fallback; fails_when=_reset keeps image_model or the fix text loses recast; why_new=the recast tests
    # use replay only, where the pin never changes; seam=none; no_claim=Google's real retirement (fake transport)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    drafted(capsys, lake_show, tmp_path, beats_s01e01)
    replay, retired = ReplayPainter(), []

    def t(url, headers, payload):
        model = url.split("/models/")[1].split(":")[0]
        if model in retired:
            return 404, {"error": {"message": "gone"}}
        data = replay.paint(payload["contents"][0]["parts"][-1]["text"], [], "3:4").data
        return 200, {"modelVersion": model, "candidates": [{"content": {"parts": [
            {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(data).decode()}}]},
            "finishReason": "STOP"}]}
    ep = lake_show.episode_dir("S01E01")
    n = len(casting.plan_for(lake_show, json.loads((ep / "bible.next.json").read_text()),
                             json.loads(lake_show.cast_path.read_text()), json.loads((ep / "beats.json").read_text())))
    assert casting.cast(lake_show, GeminiPainter(transport=t), accepted=True, approve_calls=n,
                        say=lambda m: None)["model"] == "gemini-nano-banana-2.1"
    retired.append("gemini-nano-banana-2.1")
    with pytest.raises(ChatcomError) as e:  # one member keeps the show's model, which is gone
        casting.recast(lake_show, GeminiPainter(transport=t), member="Mike", accepted=True,
                       approve_calls=len(rules.POSES), say=lambda m: None)
    assert e.value.code == "CHATCOM_E_PAINTER_UNAVAILABLE" and "chatcom recast lake-house" in e.value.fix
    with pytest.raises(ChatcomError) as e:
        casting.recast(lake_show, GeminiPainter(transport=t), accepted=False, approve_calls=None, say=lambda m: None)
    whole = e.value.context["calls"]
    p = GeminiPainter(transport=t)
    res = casting.recast(lake_show, p, accepted=True, approve_calls=whole, say=lambda m: None)
    assert res["model"] == "gemini-3.1-flash-image" and p.calls == whole  # the 404 was not billed
    nxt = json.loads(Path(res["cast_next"]).read_text("utf-8"))
    assert nxt["image_model"] == "gemini-3.1-flash-image" and not problems("cast", nxt)
    assert all(len(m["poses"]) == len(rules.POSES) for m in nxt["members"])


@needs_ffmpeg
def test_the_recast_command_the_notice_names_runs_as_written(capsys, lake_show, tmp_path, beats_s01e01, not_a_tty):
    # Value: protects=the next_command a recast notice prints parses and succeeds, and the CLI reports a replay
    # recast as unbilled; fails_when=the printed flags drift from the parser or cmd_recast misreads the result;
    # why_new=the CLI test covers only the exit-6 notice; seam=none; no_claim=the gemini painter from the CLI
    import shlex
    cast_all(capsys, lake_show, tmp_path, beats_s01e01)
    code, out, _ = run(capsys, "--json", "recast", "lake-house", "--member", "mike", "--no-photo",
                       "--painter", "replay")
    assert code == 6
    nxt = json.loads(out)["next_command"]
    assert nxt.endswith(f"--approve-calls {len(rules.POSES)}") and "--no-photo" in nxt
    argv = shlex.split(nxt)
    assert argv[0] == "chatcom"
    code, out, err = run(capsys, "--json", *argv[1:], "--painter", "replay")
    assert code == 0, err
    res = json.loads(out)
    assert res["stage"] == "cast" and res["calls"] == len(rules.POSES) and res["calls_billed"] == 0
    mike = next(m for m in json.loads(Path(res["cast_next"]).read_text())["members"] if m["name"] == "Mike")
    assert (mike["source"], mike["reason"], mike["take"]) == ("behavior", "operator_choice", 1)


@needs_ffmpeg
def test_a_recast_with_the_wrong_painter_changes_nothing(lake_show, tmp_path, beats_s01e01, capsys, not_a_tty,
                                                          monkeypatch):
    ep, _ = cast_all(capsys, lake_show, tmp_path, beats_s01e01)  # cast with the replay painter
    before = (ep / "cast.next.json").read_text()
    state = lake_show.state_path("S01E01").read_text()
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    p = GeminiPainter(transport=lambda u, h, pl: pytest.fail("no call may be made"))
    with pytest.raises(ChatcomError) as e:
        casting.recast(lake_show, p, member="Mike", accepted=True, approve_calls=len(rules.POSES), say=lambda m: None)
    assert e.value.code == "CHATCOM_E_USAGE"
    assert (ep / "cast.next.json").read_text() == before and lake_show.state_path("S01E01").read_text() == state



@needs_ffmpeg
def test_a_discarded_take_is_never_handed_back_from_the_cache(lake_show, tmp_path, beats_s01e01, capsys, not_a_tty):
    ep, _ = cast_all(capsys, lake_show, tmp_path, beats_s01e01)
    saved = (ep / "cast.next.json").read_text()
    first = casting.recast(lake_show, ReplayPainter(), member="Mike", accepted=True, approve_calls=len(rules.POSES),
                           say=lambda m: None)
    (ep / "cast.next.json").write_text(saved)  # the recast is discarded: the counter starts again from take 0
    p = ReplayPainter()
    again = casting.recast(lake_show, p, member="Mike", accepted=True, approve_calls=len(rules.POSES),
                           say=lambda m: None)
    assert first["paid"] >= 1 and p.calls >= 1  # take 1 again, yet the neutral pose is drawn, not read from cache


@needs_ffmpeg
def test_a_recast_with_a_missing_photo_changes_nothing(lake_show, tmp_path, beats_s01e01, capsys, not_a_tty):
    ep, n = cast_all(capsys, lake_show, tmp_path, beats_s01e01, photo_of="Mike")
    before = (ep / "cast.next.json").read_text()
    state = lake_show.state_path("S01E01").read_text()
    for f in tmp_path.glob("friend.png"):
        f.unlink()  # the reference photo has moved since the first cast
    with pytest.raises(ChatcomError) as e:
        casting.recast(lake_show, ReplayPainter(), accepted=True, approve_calls=n, say=lambda m: None)
    assert e.value.code == "CHATCOM_E_IMAGE_UNREADABLE"
    assert (ep / "cast.next.json").read_text() == before and lake_show.state_path("S01E01").read_text() == state


@needs_ffmpeg
def test_a_recast_photo_path_is_stored_in_full(lake_show, tmp_path, beats_s01e01, capsys, not_a_tty, monkeypatch):
    cast_all(capsys, lake_show, tmp_path, beats_s01e01)
    (tmp_path / "m.png").write_bytes(png(40, 50, shot="2026:01:09 19:00:00"))
    monkeypatch.chdir(tmp_path)
    res = casting.recast(lake_show, ReplayPainter(), member="Mike", photo="m.png", accepted=True,
                         approve_calls=len(rules.POSES), say=lambda m: None)
    mike = next(m for m in json.loads(Path(res["cast_next"]).read_text())["members"] if m["name"] == "Mike")
    assert Path(mike["photo"]).is_absolute() and mike["take_id"]
