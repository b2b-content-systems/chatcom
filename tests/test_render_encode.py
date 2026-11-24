"""Render, part 3: the encode, the stills and the commit. The render itself is
a fake that writes a tiny real video with ffmpeg (tests/test_render_compose.py
has the real-runtime renders); the encoder and the commit run for real."""
import json
import shutil
from pathlib import Path

import pytest

from chatcom import app, casting, cli
from chatcom.adapters import encoder, hyperframes, runtime, store
from chatcom.adapters.painter import ReplayPainter
from chatcom.core.errors import ChatcomError
from conftest import LAKE
from test_render_compose import big_fonts, cast_show, fake_runner, run  # noqa: F401 (fixture)

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
pytestmark = needs_ffmpeg


@pytest.fixture
def fonts(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "fonts_dir", lambda version=runtime.VERSION: big_fonts(tmp_path))


def _never(project, master, *, expected, log):
    raise AssertionError("the render ran; only the encode should have")


def _encoded(show):
    return app.render(show, runner=fake_runner)


# ---- the encode ------------------------------------------------------------------------------------
# Value: protects=a cast episode ends as a numbered MP4 under the cap with its two stills, and a second encode never
# overwrites the first; fails_when=the encode writes over episode_001, skips the stills, or the CLI's last line is
# not the file to send; why_new=new encode stage; seam=none; no_claim=picture quality (CRF 23 is a choice)
def test_render_ends_with_a_numbered_mp4_under_the_cap_and_two_stills(cast_show, fonts, capsys, monkeypatch):
    monkeypatch.setattr(runtime, "require", lambda version=runtime.VERSION: Path("hyperframes"))
    monkeypatch.setattr(hyperframes, "render", lambda p, m, *, binary, **kw: fake_runner(p, m, **kw))
    code, out, err = run(capsys, "render", "lake-house")
    ep = cast_show.episode_dir("S01E01")
    first = ep / "episode_001.mp4"
    assert code == 0 and out.strip().splitlines()[-1] == str(first), err
    st = store.read_json(cast_show.state_path("S01E01"))
    assert st["stage"] == "encoded" and st["encoded"]["file"] == "episode_001.mp4"
    assert 0 < first.stat().st_size <= encoder.DEFAULT_MAX_BYTES == st["encoded"]["max_bytes"]
    assert st["stills"] == ["episodes/S01E01/stills/scene_1.jpg", "episodes/S01E01/stills/scene_3.jpg"]
    assert all((cast_show.dir / p).stat().st_size > 0 for p in st["stills"])
    assert (ep / "render" / "master.mp4").is_file() and not list(ep.glob("*.part.mp4"))
    again = app.render(cast_show, runner=fake_runner, max_bytes=encoder.parse_size("15.5MB"))
    assert again["encode_only"] and Path(again["mp4"]).name == "episode_002.mp4" and first.is_file()
    assert store.read_json(cast_show.state_path("S01E01"))["encoded"]["max_bytes"] == 15_500_000


@pytest.mark.parametrize("text,size", [("16MB", 16_000_000), ("15.5 mb", 15_500_000), ("900KB", 900_000),
                                       ("2000000", 2_000_000)])
def test_sizes_read_as_phones_show_them(text, size):
    assert encoder.parse_size(text) == size


@pytest.mark.parametrize("text", ["", "big", "-5MB", "50KB"])
def test_a_size_that_cannot_hold_a_video_is_refused(text):
    with pytest.raises(ChatcomError) as e:
        encoder.parse_size(text)
    assert e.value.code == "CHATCOM_E_USAGE"


# Value: protects=over the cap the encoder tries two-pass at falling bitrates, at most 3 times, then exits 5 naming
# every size and keeping nothing; fails_when=it loops, keeps an over-cap file, or forgets the sizes;
# why_new=new encoder; seam=encoder._run (writes a file of a chosen size); no_claim=ffmpeg's real sizes
def _sized(monkeypatch, sizes):
    calls = []

    def fake(cmd, log, what):
        calls.append(cmd)
        if "-pass" in cmd and cmd[cmd.index("-pass") + 1] == "1":
            return
        Path(cmd[-1]).write_bytes(b"x" * sizes[len([c for c in calls if c[-1] != "/dev/null"]) - 1])
    monkeypatch.setattr(encoder, "_run", fake)
    monkeypatch.setattr(encoder.tools, "ffmpeg", lambda why: "ffmpeg")
    return calls


def test_over_the_cap_the_encoder_tries_three_lower_bitrates_then_stops(tmp_path, monkeypatch):
    calls = _sized(monkeypatch, [9_000_000, 8_000_000, 7_000_000, 6_000_000])
    with pytest.raises(ChatcomError) as e:
        encoder.encode(tmp_path / "m.mp4", tmp_path / "ep", max_bytes=5_000_000, seconds=60, log=tmp_path / "l")
    assert e.value.code == "CHATCOM_E_SIZE_CAP" and e.value.exit_code == 5 and e.value.recovery == "ask"
    rates = [int(c[c.index("-b:v") + 1][:-1]) for c in calls if "-pass" in c and c[c.index("-pass") + 1] == "2"]
    assert len(rates) == encoder.TRIES and rates == sorted(rates, reverse=True)
    assert len(e.value.context["tries"]) == 4 and "9000000" in e.value.what and "--max-size" in e.value.fix
    assert not list((tmp_path / "ep").iterdir())


def test_a_two_pass_try_that_fits_is_kept(tmp_path, monkeypatch):
    _sized(monkeypatch, [9_000_000, 4_000_000])
    out, tries = encoder.encode(tmp_path / "m.mp4", tmp_path / "ep", max_bytes=5_000_000, seconds=60,
                                log=tmp_path / "l")
    assert out.name == "episode_001.mp4" and out.stat().st_size == 4_000_000 and len(tries) == 2


# Value: protects=a missed cap keeps the master at `rendered`, and the next render only encodes it; fails_when=the
# master is removed, the stage moves on, or the retry renders again; why_new=new encode stage; seam=encoder.encode
# (raises the cap error); no_claim=none
def test_a_missed_cap_keeps_the_master_and_the_retry_only_encodes(cast_show, fonts, monkeypatch, capsys):
    real = encoder.encode

    def over(*a, **kw):
        raise ChatcomError("CHATCOM_E_SIZE_CAP", "the MP4 stays over the cap", "Pass a bigger cap.", recovery="ask")
    monkeypatch.setattr(encoder, "encode", over)
    with pytest.raises(ChatcomError) as e:
        app.render(cast_show, runner=fake_runner)
    st = store.read_json(cast_show.state_path("S01E01"))
    assert st["stage"] == "rendered" and e.value.side_effects == "partial" and "master is kept" in e.value.fix
    assert app.status(cast_show)["next"] == "chatcom render lake-house --max-size <size>"
    monkeypatch.setattr(encoder, "encode", real)
    res = app.render(cast_show, runner=_never)
    assert res["encode_only"] and res["stage"] == "encoded"


# ---- the commit ------------------------------------------------------------------------------------
# Value: protects=commit writes the episode's drawings, then its log entry with the stills, then the state, after
# snapshots of both; fails_when=the order changes, a snapshot is missing, the stills or take ids are lost;
# why_new=new commit step; seam=none; no_claim=none
def test_commit_adds_the_episode_to_the_show(cast_show, fonts, capsys):
    _encoded(cast_show)
    ep = cast_show.episode_dir("S01E01")
    before_bible, before_cast = cast_show.bible_path.read_bytes(), cast_show.cast_path.read_bytes()
    code, out, _ = run(capsys, "commit", "lake-house")
    assert code == 0 and "S01E01: committed" in out
    bible = store.read_json(cast_show.bible_path)
    entry = bible["episodes"][-1]
    assert entry["code"] == "S01E01" and entry["stills"] == store.read_json(cast_show.state_path("S01E01"))["stills"]
    assert store.read_json(cast_show.cast_path) == store.read_json(ep / "cast.next.json")
    snaps = cast_show.dir / "snapshots"
    assert (snaps / "bible.S01E01.pre.json").read_bytes() == before_bible
    assert (snaps / "cast.S01E01.pre.json").read_bytes() == before_cast
    assert store.read_json(cast_show.state_path("S01E01"))["stage"] == "committed"
    assert app.status(cast_show)["episode"] == "S01E02" and app.status(cast_show)["stage"] == "not started"
    code, _, err = run(capsys, "commit", "lake-house")
    assert code == 2 and "CHATCOM_E_STATE" in err


# Value: protects=a recast's take and take_id reach cast.json at commit (casting leftover 2); fails_when=the commit
# rebuilds the sheet and drops the take fields; why_new=no commit test had a recast; seam=none; no_claim=none
def test_a_recast_before_the_commit_carries_its_take_into_the_show(cast_show, fonts):
    _encoded(cast_show)
    with pytest.raises(ChatcomError) as e:
        casting.recast(cast_show, ReplayPainter(), member="Mike", accepted=False, approve_calls=None,
                       say=lambda m: None)
    casting.recast(cast_show, ReplayPainter(), member="Mike", accepted=True, approve_calls=e.value.context["calls"],
                   say=lambda m: None)
    drawn = next(m for m in store.read_json(cast_show.episode_dir("S01E01") / "cast.next.json")["members"]
                 if m["name"] == "Mike")
    assert drawn["take"] == 1 and drawn["take_id"]
    _encoded(cast_show)
    app.commit(cast_show)
    kept = next(m for m in store.read_json(cast_show.cast_path)["members"] if m["name"] == "Mike")
    assert (kept["take"], kept["take_id"]) == (drawn["take"], drawn["take_id"])


# Value: protects=a commit stopped after cast.json, or after bible.json, finishes on the next run with the first
# snapshots; fails_when=a rerun snapshots the half-written files, double-commits, or leaves the state at encoded;
# why_new=new commit step; seam=store.write_json (raises at one path); no_claim=a stop inside one atomic write
@pytest.mark.parametrize("stop_at", ["bible", "state"])
def test_a_stopped_commit_finishes_on_the_next_run(cast_show, fonts, monkeypatch, stop_at):
    _encoded(cast_show)
    before_bible = cast_show.bible_path.read_bytes()
    target = cast_show.bible_path if stop_at == "bible" else cast_show.state_path("S01E01")
    real = store.write_json

    def stop(path, doc):
        if Path(path) == target and (stop_at == "bible" or doc.get("stage") == "committed"):
            raise KeyboardInterrupt
        real(path, doc)
    monkeypatch.setattr(store, "write_json", stop)
    with pytest.raises(KeyboardInterrupt):
        app.commit(cast_show)
    monkeypatch.setattr(store, "write_json", real)
    res = app.commit(cast_show)
    assert res["episode"] == "S01E01" and store.read_json(cast_show.state_path("S01E01"))["stage"] == "committed"
    bible = store.read_json(cast_show.bible_path)
    assert [e["code"] for e in bible["episodes"]] == ["S01E01"]
    assert (cast_show.dir / "snapshots" / "bible.S01E01.pre.json").read_bytes() == before_bible


def _next_week(show) -> Path:
    later = ["[1/23/26, 7:00:00 PM] Dave: ok who is bringing the grill",
             "[1/23/26, 7:01:10 PM] Mike: not me. last time it caught fire",
             "[1/23/26, 7:02:30 PM] Dave: that was the grill's fault"]
    p = show.inbox / "_chat.txt"
    p.write_text(LAKE.read_text("utf-8").rstrip("\n") + "\n" + "\n".join(later) + "\n", "utf-8")
    return p


# Value: protects=O12: starting the next episode commits a waiting encoded one first and says so, and the new
# window starts after its messages; fails_when=ingest refuses an encoded episode, skips the commit, or reads from
# the old cursor; why_new=O12 answer B; seam=none; no_claim=the writer step after ingest
def test_starting_the_next_episode_commits_the_waiting_one(cast_show, fonts, capsys):
    _encoded(cast_show)
    export = _next_week(cast_show)
    code, out, err = run(capsys, "ingest", "lake-house", "--export", str(export), "--accept-data-flow")
    assert code == 0, err
    assert "S01E01: committed" in err and out.startswith("S01E02: 3 messages")
    assert store.read_json(cast_show.state_path("S01E01"))["stage"] == "committed"
    assert store.read_json(cast_show.state_path("S01E02"))["stage"] == "ingested"


# ---- before the commit: recast and rewrite still work -------------------------------------------------
# Value: protects=a recast of an encoded episode goes back to drafted, drops the stale master and keeps the MP4s;
# fails_when=recast refuses past cast again, or the old master renders the new drawings' episode; why_new=R2 review
# note; seam=none; no_claim=what the painter draws
def test_recast_of_an_encoded_episode_redraws_and_drops_the_master(cast_show, fonts):
    _encoded(cast_show)
    ep = cast_show.episode_dir("S01E01")
    with pytest.raises(ChatcomError) as e:
        casting.recast(cast_show, ReplayPainter(), member="Mike", accepted=False, approve_calls=None,
                       say=lambda m: None)
    n = e.value.context["calls"]
    casting.recast(cast_show, ReplayPainter(), member="Mike", accepted=True, approve_calls=n, say=lambda m: None)
    st = store.read_json(cast_show.state_path("S01E01"))
    assert st["stage"] == "cast" and "encoded" not in st and "stills" not in st
    assert not (ep / "render").exists() and (ep / "episode_001.mp4").is_file()


# Value: protects=rewrite drops the sets this draft added, so a new draft that reuses a set id draws it again;
# fails_when=a stale set description survives into the next draft; why_new=R2 final review ("silent");
# seam=none; no_claim=the image cache's free redraw (test_casting covers the cache)
def test_rewrite_drops_the_sets_the_draft_added(cast_show, fonts):
    ep = cast_show.episode_dir("S01E01")
    sheet = store.read_json(ep / "cast.next.json")
    committed = {b["id"] for b in store.read_json(cast_show.cast_path)["backgrounds"]}
    sheet["backgrounds"].append({"id": "boathouse", "description": "a boathouse at dusk", "file": "x.png"})
    store.write_json(ep / "cast.next.json", sheet)
    _encoded(cast_show)
    app.rewrite(cast_show)
    kept = {b["id"] for b in store.read_json(ep / "cast.next.json")["backgrounds"]}
    assert "boathouse" not in kept and kept == committed & kept and kept
    assert (ep / "episode_001.mp4").is_file()


# ---- after the commit --------------------------------------------------------------------------------
# Value: protects=render --episode N makes a new MP4 of a committed episode and leaves its stage and the bible
# alone; rewrite --episode N restores both snapshots, and refuses once the next episode started; fails_when=either
# touches the wrong episode or a half-restored show is left; why_new=O5 and PLAN.md; seam=none; no_claim=none
def test_a_committed_episode_renders_again_and_can_be_written_again(cast_show, fonts, capsys):
    _encoded(cast_show)
    pre_bible, pre_cast = cast_show.bible_path.read_bytes(), cast_show.cast_path.read_bytes()
    app.commit(cast_show)
    after = cast_show.bible_path.read_bytes()
    res = app.render(cast_show, episode=1, runner=fake_runner)
    assert Path(res["mp4"]).name == "episode_002.mp4" and res["stage"] == "committed"
    assert cast_show.bible_path.read_bytes() == after
    code, out, _ = run(capsys, "--json", "rewrite", "lake-house", "--episode", "1")
    assert code == 0 and json.loads(out)["episode"] == "S01E01"
    assert cast_show.bible_path.read_bytes() == pre_bible and cast_show.cast_path.read_bytes() == pre_cast
    assert store.read_json(cast_show.state_path("S01E01"))["stage"] == "ingested"
    assert app.status(cast_show)["episode"] == "S01E01"


def test_a_committed_episode_is_not_rewritten_once_the_next_one_started(cast_show, fonts, capsys):
    _encoded(cast_show)
    export = _next_week(cast_show)
    assert run(capsys, "ingest", "lake-house", "--export", str(export), "--accept-data-flow")[0] == 0
    before = cast_show.bible_path.read_bytes()
    code, _, err = run(capsys, "rewrite", "lake-house", "--episode", "1")
    assert code == 2 and "S01E02 has started" in err and cast_show.bible_path.read_bytes() == before


# ---- small fixes from the R2 review ---------------------------------------------------------------------
def test_a_relative_chatcom_home_is_made_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHATCOM_HOME", "rel-home")
    assert store.home() == tmp_path / "rel-home"


def test_a_runtime_without_its_chrome_is_a_preflight_error(tmp_path, monkeypatch):
    rt = tmp_path / "rt"
    (rt / "node_modules" / ".bin").mkdir(parents=True)
    store.write_json(rt / "installed.json", {"hyperframes": runtime.VERSION})
    monkeypatch.setattr(runtime, "root", lambda version=runtime.VERSION: rt)
    monkeypatch.setattr(runtime, "node_version", lambda: 22)
    monkeypatch.setattr(runtime, "binary", lambda version=runtime.VERSION: rt / "installed.json")
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    for name in runtime.BIG_FONTS:
        (fonts / name).write_bytes(b"font")
    monkeypatch.setattr(runtime, "fonts_dir", lambda version=runtime.VERSION: fonts)
    with pytest.raises(ChatcomError) as e:
        runtime.require()
    assert e.value.code == "CHATCOM_E_PREFLIGHT" and "Chrome" in e.value.what
    shell = rt / "cache" / "chrome" / "chrome-headless-shell" / "linux-1" / "chrome-headless-shell-linux64"
    shell.mkdir(parents=True)
    (shell / "chrome-headless-shell").write_text("#!/bin/sh\n")
    (shell / "chrome-headless-shell").chmod(0o755)
    assert runtime.require() == rt / "installed.json"


def test_a_still_with_an_odd_suffix_never_reaches_the_page_as_text(cast_show, tmp_path):
    from test_render_compose import compose
    odd = cast_show.dir / "episodes" / "S00E00" / "stills" / "a.jpg');}x{"
    odd.parent.mkdir(parents=True)
    odd.write_bytes(b"\xff\xd8")
    _, index, html = compose(cast_show, tmp_path, lambda b, s: b.update(episode="S01E02"),
                             previous={"synopsis": "s", "stills": [odd.relative_to(cast_show.dir).as_posix()]})
    assert "');}x{" not in html and "assets/stills/0.jpg" in html


# ---- coverage audit (R3, pass 1) ---------------------------------------------------------------------
# Value: protects=an ffmpeg failure on the encode is a coded render error that names the log and leaves no .part file;
# fails_when=_run ignores the exit code, or the part file survives a failed encode; why_new=every encode test fakes
# _run or succeeds; seam=none; no_claim=the 15-minute timeout branch
def test_a_failed_encode_is_a_render_error_and_keeps_nothing(tmp_path):
    bad = tmp_path / "master.mp4"
    bad.write_bytes(b"not a video")
    log = tmp_path / "encode.log"
    with pytest.raises(ChatcomError) as e:
        encoder.encode(bad, tmp_path / "ep", max_bytes=16_000_000, seconds=60, log=log)
    assert e.value.code == "CHATCOM_E_RENDER" and e.value.recovery == "retry" and str(log) in e.value.fix
    assert "ffmpeg exit" in e.value.what and log.read_text("utf-8").startswith("$ ")
    assert not list((tmp_path / "ep").iterdir())


# Value: protects=a cap too small for the length stops after the CRF try with no two-pass at a bitrate under 100 kb/s;
# fails_when=the kbps floor goes and ffmpeg runs at zero or negative bitrate; why_new=the floor branch is untested;
# seam=encoder._run (writes a file of a chosen size); no_claim=ffmpeg's real sizes
def test_a_cap_too_small_for_the_length_stops_before_two_pass(tmp_path, monkeypatch):
    calls = _sized(monkeypatch, [9_000_000])
    with pytest.raises(ChatcomError) as e:
        encoder.encode(tmp_path / "m.mp4", tmp_path / "ep", max_bytes=1_000_000, seconds=600, log=tmp_path / "l")
    assert e.value.code == "CHATCOM_E_SIZE_CAP" and len(e.value.context["tries"]) == 1
    assert len(calls) == 1 and not list((tmp_path / "ep").iterdir())


# Value: protects=rewrite --episode N of a commit with no snapshots refuses and changes nothing; fails_when=the snapshot
# check moves after a write and leaves a half-restored show; why_new=only the happy restore is tested; seam=none;
# no_claim=snapshots from older chatcom versions
def test_rewrite_of_a_committed_episode_without_snapshots_changes_nothing(cast_show, fonts, capsys):
    _encoded(cast_show)
    app.commit(cast_show)
    (cast_show.dir / "snapshots" / "cast.S01E01.pre.json").unlink()
    before = (cast_show.bible_path.read_bytes(), cast_show.cast_path.read_bytes(),
              cast_show.state_path("S01E01").read_bytes())
    code, _, err = run(capsys, "rewrite", "lake-house", "--episode", "1")
    assert code == 2 and "no snapshot" in err
    after = (cast_show.bible_path.read_bytes(), cast_show.cast_path.read_bytes(),
             cast_show.state_path("S01E01").read_bytes())
    assert after == before


# Value: protects=render --episode N for an episode that is neither committed nor this week's refuses before any
# write; fails_when=_episode_for falls through to another episode or renders this week's; why_new=only N=1 committed
# is tested; seam=none; no_claim=the "no draft left" branch
def test_render_of_an_episode_that_does_not_exist_refuses(cast_show, fonts, capsys):
    before = cast_show.state_path("S01E01").read_bytes()
    with pytest.raises(ChatcomError) as e:
        app.render(cast_show, episode=7, runner=_never)
    assert e.value.code == "CHATCOM_E_STATE" and e.value.context["episode"] == 7
    assert cast_show.state_path("S01E01").read_bytes() == before
    assert not (cast_show.episode_dir("S01E01") / "render").exists()


# Value: protects=a commit stopped after bible.json is finished by the next ingest, which then starts the next
# episode; fails_when=ingest's _finish_commit branch goes and S01E01 stays `encoded` forever; why_new=only `commit`
# reruns are tested; seam=store.write_json (raises at the state); no_claim=a stop inside one atomic write
def test_ingest_finishes_a_commit_that_stopped_before_its_state(cast_show, fonts, capsys, monkeypatch):
    _encoded(cast_show)
    real = store.write_json
    target = cast_show.state_path("S01E01")

    def stop(path, doc):
        if Path(path) == target and doc.get("stage") == "committed":
            raise KeyboardInterrupt
        real(path, doc)
    monkeypatch.setattr(store, "write_json", stop)
    with pytest.raises(KeyboardInterrupt):
        app.commit(cast_show)
    monkeypatch.setattr(store, "write_json", real)
    assert store.read_json(target)["stage"] == "encoded"
    code, out, err = run(capsys, "ingest", "lake-house", "--export", str(_next_week(cast_show)), "--accept-data-flow")
    assert code == 0, err
    assert store.read_json(target)["stage"] == "committed"
    assert store.read_json(cast_show.state_path("S01E02"))["stage"] == "ingested"


# ---- review fixes (pass 1) ---------------------------------------------------------------------------
# Value: protects=a rewrite --episode N that stopped after its state or cast.json write finishes on a rerun;
# fails_when=the rerun refuses the half-undone episode; why_new=specialist p1#1; seam=store.write_atomic (raises);
# no_claim=a stop inside one atomic write
@pytest.mark.parametrize("stop_at", ["cast", "bible"])
def test_a_stopped_rewrite_of_a_committed_episode_finishes_on_rerun(cast_show, fonts, monkeypatch, stop_at):
    _encoded(cast_show)
    pre_bible, pre_cast = cast_show.bible_path.read_bytes(), cast_show.cast_path.read_bytes()
    app.commit(cast_show)
    target = cast_show.cast_path if stop_at == "cast" else cast_show.bible_path
    real = store.write_atomic

    def stop(path, data):
        if Path(path) == target:
            raise KeyboardInterrupt
        real(path, data)
    monkeypatch.setattr(store, "write_atomic", stop)
    with pytest.raises(KeyboardInterrupt):
        app.rewrite(cast_show, episode=1)
    monkeypatch.setattr(store, "write_atomic", real)
    res = app.rewrite(cast_show, episode=1)
    assert res["episode"] == "S01E01" and res["stage"] == "ingested"
    assert cast_show.bible_path.read_bytes() == pre_bible and cast_show.cast_path.read_bytes() == pre_cast


# Value: protects=after a failed encode of a committed episode the fix names `--episode N` and the real slug;
# fails_when=the retry acts on this week's episode; why_new=red team p1#1; seam=encoder.encode (raises);
# no_claim=ffmpeg's own failures
def test_a_failed_encode_of_an_earlier_episode_names_that_episode_in_the_fix(cast_show, fonts, monkeypatch):
    _encoded(cast_show)
    app.commit(cast_show)
    _sized(monkeypatch, [9_000_000])  # every encode comes out at 9 MB
    with pytest.raises(ChatcomError) as e:
        app.render(cast_show, episode=1, max_bytes=encoder.parse_size("100KB"), runner=_never)
    assert e.value.code == "CHATCOM_E_SIZE_CAP" and "after 1 try " in e.value.what
    assert "chatcom render lake-house --episode 1 --max-size <size>" in e.value.fix and "<show>" not in e.value.fix


# Value: protects=a master left at stage cast (a stopped rewrite or recast) is never encoded as the new episode;
# fails_when=--max-size at cast takes the encode-only path; why_new=red team p1#2; seam=none; no_claim=none
def test_a_stale_master_at_cast_is_rendered_again_even_with_max_size(cast_show, fonts):
    _encoded(cast_show)
    sp = cast_show.state_path("S01E01")
    st = store.read_json(sp)
    store.write_json(sp, {k: v for k, v in st.items() if k not in ("encoded", "stills")} | {"stage": "cast"})
    calls = []

    def counting(project, master, *, expected, log):
        calls.append(1)
        return fake_runner(project, master, expected=expected, log=log)
    res = app.render(cast_show, max_bytes=encoder.DEFAULT_MAX_BYTES, runner=counting)
    assert calls and not res["encode_only"] and res["stage"] == "encoded"


# Value: protects=a commit stopped after cast.json is undone by rewrite, so its new sets are still dropped;
# fails_when=rewrite compares with the half-written cast.json; why_new=red team p1#3; seam=store.write_json (raises);
# no_claim=none
def test_rewrite_after_a_commit_stopped_past_cast_json_puts_the_cast_back(cast_show, fonts, monkeypatch):
    ep = cast_show.episode_dir("S01E01")
    sheet = store.read_json(ep / "cast.next.json")
    sheet["backgrounds"].append(dict(sheet["backgrounds"][0], id="boathouse", description="a boathouse at dusk"))
    store.write_json(ep / "cast.next.json", sheet)
    _encoded(cast_show)
    pre_cast = cast_show.cast_path.read_bytes()
    real = store.write_json

    def stop(path, doc):
        if Path(path) == cast_show.bible_path:
            raise KeyboardInterrupt
        real(path, doc)
    monkeypatch.setattr(store, "write_json", stop)
    with pytest.raises(KeyboardInterrupt):
        app.commit(cast_show)
    monkeypatch.setattr(store, "write_json", real)
    assert cast_show.cast_path.read_bytes() != pre_cast   # the stop left cast.json ahead of the bible
    app.rewrite(cast_show)
    assert cast_show.cast_path.read_bytes() == pre_cast
    assert "boathouse" not in {b["id"] for b in store.read_json(ep / "cast.next.json")["backgrounds"]}


# ---- review notes fixed on request -------------------------------------------------------------------
# Value: protects=after a stopped `rewrite --episode N`, ingest and status point at the rewrite instead of starting
# the next episode on a half-restored show; fails_when=ingest goes on; why_new=review notes (3 readers); seam=store.
# write_atomic (raises); no_claim=none
def test_ingest_refuses_while_an_undo_of_a_commit_is_unfinished(cast_show, fonts, capsys, monkeypatch):
    _encoded(cast_show)
    app.commit(cast_show)
    real = store.write_atomic

    def stop(path, data):
        if Path(path) == cast_show.bible_path:
            raise KeyboardInterrupt
        real(path, data)
    monkeypatch.setattr(store, "write_atomic", stop)
    with pytest.raises(KeyboardInterrupt):
        app.rewrite(cast_show, episode=1)
    monkeypatch.setattr(store, "write_atomic", real)
    assert app.status(cast_show)["next"] == "chatcom rewrite lake-house --episode 1"
    code, _, err = run(capsys, "ingest", "lake-house", "--export", str(_next_week(cast_show)), "--accept-data-flow")
    assert code == 2 and "stopped halfway" in err and "chatcom rewrite lake-house --episode 1" in err
    assert not cast_show.state_path("S01E02").exists()


# Value: protects=an ingest that fails after its auto-commit says the episode was committed and how to undo it;
# fails_when=the error keeps side effects `none`; why_new=review notes; seam=none; no_claim=other failure codes
def test_a_failed_ingest_after_the_auto_commit_says_so(cast_show, fonts, capsys):
    _encoded(cast_show)
    code, out, err = run(capsys, "--json", "ingest", "lake-house", "--export", str(LAKE), "--accept-data-flow")
    assert code == 2 and "S01E01 was committed first" in err and "chatcom rewrite lake-house --episode 1" in err
    assert json.loads(err.splitlines()[-1])["side_effects"] == "partial"
    assert store.read_json(cast_show.state_path("S01E01"))["stage"] == "committed"
    assert app.rewrite(cast_show, episode=1)["stage"] == "ingested"   # and the fix it names works


def test_replace_on_an_encoded_episode_is_refused_before_the_commit(cast_show, fonts, capsys):
    _encoded(cast_show)
    before = cast_show.bible_path.read_bytes()
    code, _, err = run(capsys, "ingest", "lake-house", "--export", str(_next_week(cast_show)), "--accept-data-flow",
                       "--replace")
    assert code == 2 and "--replace" in err and cast_show.bible_path.read_bytes() == before
    assert store.read_json(cast_show.state_path("S01E01"))["stage"] == "encoded"
