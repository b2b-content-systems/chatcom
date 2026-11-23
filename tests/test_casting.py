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
