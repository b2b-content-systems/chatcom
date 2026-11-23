"""Render, part 2: the composer, the runtime, the runner, `chatcom render` and
`chatcom rewrite`. Unit tests use no HyperFrames: the runner is a fake that
writes a video of a chosen length with ffmpeg. The end-to-end renders at the
bottom need the installed runtime (CHATCOM_RENDER_RUNTIME_HOME) and run in the
CI render job, inside a sandbox with no network."""
import http.server
import json
import os
import re
import shutil
import socketserver
import stat
import subprocess
import threading
from pathlib import Path

import pytest

from chatcom import app, cli
from chatcom.adapters import composer, hyperframes, runtime, store, tools
from chatcom.adapters.painter import ReplayPainter
from chatcom import casting
from chatcom.core import render as rt
from chatcom.core.errors import ChatcomError
from conftest import LAKE

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
RUNTIME_HOME = os.environ.get("CHATCOM_RENDER_RUNTIME_HOME")
needs_runtime = pytest.mark.skipif(
    not (RUNTIME_HOME and (Path(RUNTIME_HOME) / "runtime" / runtime.VERSION / "installed.json").is_file()),
    reason="the render runtime is not installed (set CHATCOM_RENDER_RUNTIME_HOME to a CHATCOM_HOME after "
           "`chatcom doctor --install`)")
if os.environ.get("CHATCOM_RENDER_REQUIRED") and needs_runtime.args[0]:  # the CI render job never skips quietly
    raise RuntimeError(f"CHATCOM_RENDER_REQUIRED is set but the runtime is missing: {needs_runtime.kwargs['reason']}")
HOSTILE = ['</div><script>alert(1)</script>', '"><img src=x onerror=alert(1)>', "‮evil.exe",
           "zero​width", "x" * 5000, "http://127.0.0.1:9/leak.png"]


def run(capsys, *argv):
    code = cli.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


@pytest.fixture
def cast_show(lake_show, tmp_path, beats_s01e01, capsys, monkeypatch):
    """The synthetic show, drafted from the recorded writer and cast with the replay painter."""
    monkeypatch.setattr(cli, "_tty", lambda: False)
    beats = tmp_path / "beats.json"
    beats.write_text(json.dumps(beats_s01e01), "utf-8")
    assert run(capsys, "episode", "lake-house", "--export", str(LAKE), "--writer", "replay", "--replay",
               str(beats))[0] == 0
    ep = lake_show.episode_dir("S01E01")
    n = len(casting.plan_for(lake_show, store.read_json(ep / "bible.next.json"), store.read_json(lake_show.cast_path),
                             store.read_json(ep / "beats.json")))
    casting.cast(lake_show, ReplayPainter(), accepted=True, approve_calls=n, say=lambda m: None)
    return lake_show


def big_fonts(tmp_path) -> Path:
    d = tmp_path / "bigfonts"
    d.mkdir(exist_ok=True)
    for _, name in composer.BIG_FONTS:
        (d / name).write_bytes(b"font")
    return d


def compose(show, tmp_path, mutate=None, previous=None):
    ep = show.episode_dir("S01E01")
    beats, sheet = store.read_json(ep / "beats.json"), store.read_json(ep / "cast.next.json")
    if mutate:
        mutate(beats, sheet)
    tl = rt.timeline(beats, sheet, previous, show_title="LAKE HOUSE")
    index = composer.write_project(tl, sheet, show.dir, ep / "render", big_fonts=big_fonts(tmp_path))
    return tl, index, index.read_text("utf-8")


# ---- the composer --------------------------------------------------------------------------------
# Value: protects=chat text can never become markup or a request, and nothing outside the show reaches the build;
# fails_when=a template interpolates raw text, links a URL, drops the CSP, or copies a listed path without checking
# it; why_new=new composer; seam=none; no_claim=what Chrome does with the page (the CI render job covers that)

@needs_ffmpeg
def test_hostile_chat_text_lands_as_literal_text_in_one_safe_page(cast_show, tmp_path):
    def hostile(beats, sheet):
        for i, text in enumerate(HOSTILE):
            scene = beats["scenes"][i % 3]
            k = i // 3  # each string in its own bubble
            scene["bubbles"][k] = dict(scene["bubbles"][k], text=text)
        beats["title"] = "<b>bold</b>"
        beats["credits"][0]["role"] = "</style><script>x</script>"
    _, index, page = compose(cast_show, tmp_path, hostile)
    assert page.count("<script") == 1  # only the shipped motion script
    assert "&lt;/div&gt;&lt;script&gt;alert(1)&lt;/script&gt;" in page and "<img src=x" not in page
    assert "‮evil.exe" in page and "x" * 5000 in page and "&lt;b&gt;bold&lt;/b&gt;" in page
    assert "&lt;/style&gt;&lt;script&gt;x&lt;/script&gt;" in page
    assert 'http-equiv="Content-Security-Policy"' in page and "default-src 'self'" in page
    # nothing in the page points outside the build: every link is assets/..., and no scheme or // appears
    # outside escaped chat text
    bare = re.sub(r"&[a-z]+;|http://127\.0\.0\.1:9/leak\.png", "", page)  # the hostile bubble text itself
    assert not re.search(r"https?:|file:|//", bare)
    links = re.findall(r"""(?:src="|url\(')([^"')]+)""", page)
    assert links and all(link.startswith("assets/") for link in links)
    for link in links:
        assert (index.parent / link).is_file()
    built = [p for p in (index.parent / "assets").rglob("*") if p.is_file()]
    assert built and all(not p.is_symlink() and p.resolve().is_relative_to(index.parent) for p in built)


@needs_ffmpeg
def test_a_picture_outside_the_show_folder_is_refused(cast_show, tmp_path):
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"\x89PNG")

    def escape_by_path(beats, sheet):
        sheet["members"][0]["poses"]["talking"]["file"] = "../../" + outside.name
    with pytest.raises(ChatcomError) as e:
        compose(cast_show, tmp_path, escape_by_path)
    assert e.value.code == "CHATCOM_E_STATE" and "outside the show folder" in e.value.what
    link = cast_show.dir / "cast" / "sneaky.png"  # a link inside the show that points out of it
    link.symlink_to(outside)

    def escape_by_link(beats, sheet):
        sheet["backgrounds"][0]["file"] = "cast/sneaky.png"
    with pytest.raises(ChatcomError) as e:
        compose(cast_show, tmp_path, escape_by_link)
    assert "outside the show folder" in e.value.what
    with pytest.raises(ChatcomError) as e:  # a 'Previously on' still from a hand-edited bible
        compose(cast_show, tmp_path, lambda b, s: b.update(episode="S01E02"),
                previous={"synopsis": "s", "stills": [str(outside)]})
    assert "outside the show folder" in e.value.what


# Value: protects=the layout rules (who stands where, which pose shows when, where bubbles and stickers go) and
# sound-off readability on a 360-px phone; fails_when=a speaker has no figure, a pose window overlaps another, the
# chorus gets a drawing, or the dialogue type shrinks below 16 px at 360 wide; why_new=new composer; seam=none;
# no_claim=how it looks (checked by eye on the CI render's snapshot)
@needs_ffmpeg
def test_figures_poses_bubbles_and_type_follow_the_layout_rules(cast_show, tmp_path):
    def chorus(beats, sheet):
        beats["cold_open"]["bubbles"][1] = dict(beats["cold_open"]["bubbles"][1], speaker="Jonah", pose=None)
    tl, _, page = compose(cast_show, tmp_path, chorus)
    for seg in (tl.segment(n) for n in rt.SCENE_SEGMENTS):
        sec = page.split(f'id="sg-{seg.name}"', 1)[1].split("</section>", 1)[0]
        assert sec.count('class="fig"') == len(seg.cast)
        assert sec.count('class="bub ') == len(seg.bubbles)
        assert sec.count('class="stk"') == sum(1 for b in seg.bubbles if b.sticker)
        for fig in re.findall(r'<div class="fig".*?</div></div>', sec):  # a figure's pose windows tile its scene
            wins = sorted((float(a), float(b)) for w in re.findall(r'data-show="([^"]+)"', fig)
                          for a, b in (x.split(",") for x in w.split(";")))
            assert wins[0][0] == pytest.approx(seg.start, abs=1e-3)
            assert wins[-1][1] == pytest.approx(seg.start + seg.length, abs=1e-3)
            assert all(b0 == pytest.approx(a1, abs=1e-3) for (_, b0), (a1, _) in zip(wins, wins[1:]))
        figs = composer._figures(seg, composer._members(store.read_json(cast_show.episode_dir("S01E01") /
                                                                         "cast.next.json")))
        where = {f["name"]: f["cx"] for f in figs}
        for m in re.finditer(r'class="bub (l|r)" style="[^"]*--tail:(\d+)px[^"]*"[^>]*><span class="who txt">([^<]+)<',
                             sec):
            side, tail, who = m.group(1), int(m.group(2)), m.group(3)
            tip = (composer.BUB_EDGE + tail + composer.TAIL_HALF if side == "l"
                   else composer.W - composer.BUB_EDGE - tail - composer.TAIL_HALF)
            assert tail == composer.TAIL_MIN or tip == where[who]  # the tail points at the speaker
            assert (side == "l") == (where[who] < composer.W // 2)
    cold = page.split('id="sg-cold_open"', 1)[1].split("</section>", 1)[0]
    assert 'class="bub chorus"' in cold and "Jonah" in cold
    css = (Path(composer.__file__).parent.parent / "render_templates" / "episode.css").read_text("utf-8")
    size = int(re.search(r"\.bub \{[^}]*font-size: (\d+)px", css).group(1))
    assert size * 360 / composer.W >= 16  # dialogue on a 360-px-wide phone


def test_every_sticker_the_writer_can_pick_has_a_look():
    schema = json.loads((Path(composer.__file__).parent.parent / "schemas" / "beats.schema.json").read_text())
    enum = schema["$defs"]["scene"]["properties"]["bubbles"]["items"]["properties"]["sticker"]["enum"]
    assert set(enum) - {None} == set(composer.STICKERS)  # R4 adds the sound list to this check


@needs_ffmpeg
def test_previously_on_shows_the_last_episodes_stills_and_synopsis(cast_show, tmp_path):
    stills = cast_show.dir / "episodes" / "S01E01" / "stills"
    stills.mkdir(parents=True)
    for n in (1, 3):
        (stills / f"{n}.jpg").write_bytes(b"jpg")
    prev = {"synopsis": "The trip is off. Again.", "stills": ["episodes/S01E01/stills/1.jpg",
                                                                "episodes/S01E01/stills/3.jpg"]}
    tl, index, page = compose(cast_show, tmp_path, lambda b, s: b.update(episode="S01E02"), previous=prev)
    sec = page.split('id="sg-previously_on"', 1)[1].split("</section>", 1)[0]
    assert "The trip is off. Again." in sec and sec.count('class="shot"') == 2
    assert (index.parent / "assets" / "stills" / "0.jpg").is_file()
    _, _, page = compose(cast_show, tmp_path, lambda b, s: b.update(episode="S01E02"),
                         previous={"synopsis": "s", "stills": []})
    assert 'class="shot"' not in page.split('id="sg-previously_on"', 1)[1].split("</section>", 1)[0]


# ---- the runtime ---------------------------------------------------------------------------------
# Value: protects=every user renders with exactly the pinned HyperFrames, telemetry off, and fonts that match their
# fingerprints; fails_when=the pin drifts between the module and the shipped lockfile, a bad download is kept, or a
# missing runtime gives a raw crash; why_new=new installer; seam=network (fetch faked); no_claim=npm's own install

def test_the_pin_is_the_same_in_the_module_and_the_shipped_lockfile():
    shipped = Path(runtime.__file__).parent.parent / "runtime"
    pkg = json.loads((shipped / "package.json").read_text())
    lock = json.loads((shipped / "package-lock.json").read_text())
    assert pkg["dependencies"]["hyperframes"] == runtime.VERSION
    assert lock["packages"]["node_modules/hyperframes"]["version"] == runtime.VERSION
    env = runtime.env()
    assert env["HYPERFRAMES_NO_TELEMETRY"] == "1" and env["HYPERFRAMES_CACHE_DIR"].startswith(str(runtime.root()))


class _Body:
    def __init__(self, data): self.data, self.done = data, False
    def read(self, n=-1):
        if self.done:
            return b""
        self.done = True
        return self.data
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_a_font_that_does_not_match_its_fingerprint_is_not_kept(tmp_path, monkeypatch):
    name = "OFL-notosanscjk.txt"
    dest = tmp_path / name
    with pytest.raises(ChatcomError) as e:
        runtime.fetch_font(name, dest, opener=lambda url, timeout: _Body(b"tampered"))
    assert e.value.code == "CHATCOM_E_PREFLIGHT" and "fingerprint" in e.value.what
    assert not dest.exists() and not list(tmp_path.glob("*.part"))
    monkeypatch.setitem(runtime.BIG_FONTS, name, (runtime.BIG_FONTS[name][0],
                                                   __import__("hashlib").sha256(b"good").hexdigest()))
    runtime.fetch_font(name, dest, opener=lambda url, timeout: _Body(b"good"))
    assert dest.read_bytes() == b"good"

    # Value: protects=a dropped download is a retryable preflight error and leaves no .part; fails_when=the OSError
    # handler goes or stops cleaning up; why_new=only the fingerprint branch was tested; seam=network (opener faked);
    # no_claim=real network behaviour
    dest.unlink()

    def offline(url, timeout):
        raise OSError("network is unreachable")
    with pytest.raises(ChatcomError) as e:
        runtime.fetch_font(name, dest, opener=offline)
    assert e.value.code == "CHATCOM_E_PREFLIGHT" and e.value.recovery == "retry" and "could not download" in e.value.what
    assert not dest.exists() and not list(tmp_path.glob("*.part"))


def test_a_missing_runtime_or_tool_is_a_preflight_error_with_the_fix(monkeypatch, capsys):
    if shutil.which("node"):
        with pytest.raises(ChatcomError) as e:
            runtime.require()
        assert e.value.code == "CHATCOM_E_PREFLIGHT" and runtime.INSTALL_COMMAND in e.value.fix
    monkeypatch.setattr(shutil, "which", lambda tool: None if tool == "ffprobe" else "/bin/" + tool)
    with pytest.raises(ChatcomError) as e:
        tools.ffprobe()
    assert e.value.code == "CHATCOM_E_PREFLIGHT" and e.value.exit_code == 3 and "brew install ffmpeg" in e.value.fix


# ---- the runner ----------------------------------------------------------------------------------
# Value: protects=a master that is not 60 s +- 0.1 or a failed render never advances the episode, and the child
# runs with telemetry off; fails_when=the duration check, the exit check or the env goes; why_new=new runner;
# seam=HyperFrames (a fake binary that writes a video with ffmpeg); no_claim=HyperFrames' own output

def fake_hyperframes(tmp_path, seconds: float, code: int = 0) -> Path:
    exe = tmp_path / "hyperframes"
    exe.write_text(f"""#!/bin/sh
env > "{tmp_path}/env.txt"
out=""; while [ $# -gt 0 ]; do [ "$1" = "--output" ] && out="$2"; shift; done
[ {code} -ne 0 ] && echo "boom" && exit {code}
exec "{shutil.which('ffmpeg')}" -v error -y -f lavfi -i color=c=black:s=108x192:r=30 -t {seconds} "$out"
""")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe


@needs_ffmpeg
def test_the_runner_checks_the_length_and_the_exit_and_turns_telemetry_off(tmp_path):
    project = tmp_path / "render"
    project.mkdir()
    master, log = project / "master.mp4", project / "render.log"
    got = hyperframes.render(project, master, expected=60.0, log=log, binary=fake_hyperframes(tmp_path, 60.0))
    assert got == pytest.approx(60.0, abs=0.1) and master.is_file()
    assert "HYPERFRAMES_NO_TELEMETRY=1" in (tmp_path / "env.txt").read_text()
    with pytest.raises(ChatcomError) as e:
        hyperframes.render(project, master, expected=60.0, log=log, binary=fake_hyperframes(tmp_path, 58.0))
    assert e.value.code == "CHATCOM_E_RENDER" and e.value.exit_code == 5 and "58.00 s" in e.value.what
    assert not master.exists() and not list(project.glob("*.part.mp4"))
    with pytest.raises(ChatcomError) as e:
        hyperframes.render(project, master, expected=60.0, log=log, binary=fake_hyperframes(tmp_path, 60.0, 1))
    assert e.value.trace_path == str(log) and "boom" in log.read_text()


# ---- chatcom render and chatcom rewrite ------------------------------------------------------------
# Value: protects=render moves cast -> rendered and can run again, refuses other stages and old over-limit drafts
# before writing anything, and rewrite sends an episode back to the writer without losing paid work;
# fails_when=a stage check, the ordering of the preflight, or what rewrite keeps changes; why_new=new commands;
# seam=HyperFrames (fake runner); no_claim=the real render (end-to-end below)

def fake_runner(project, master, *, expected, log):
    master.write_bytes(b"mp4")
    return expected


@needs_ffmpeg
def test_render_moves_a_cast_episode_to_rendered_and_can_run_again(cast_show, monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "fonts_dir", lambda version=runtime.VERSION: big_fonts(tmp_path))
    res = app.render(cast_show, runner=fake_runner)
    assert res["stage"] == "rendered" and Path(res["master"]).is_file() and res["seconds"] == 60.0
    assert app.status(cast_show)["next"].startswith("encode arrives")
    again = app.render(cast_show, runner=fake_runner)
    assert again["stage"] == "rendered"


@needs_ffmpeg
def test_render_refuses_before_writing_anything(cast_show, capsys, monkeypatch, tmp_path):
    ep = cast_show.episode_dir("S01E01")
    if shutil.which("node"):  # no runtime installed in this CHATCOM_HOME: exit 3 with the install command
        code, _, err = run(capsys, "render", "lake-house")
        assert code == 3 and runtime.INSTALL_COMMAND in err and not (ep / "render").exists()
    beats = store.read_json(ep / "beats.json")  # a draft from before the bubble limit
    beats["cold_open"]["bubbles"] = [dict(beats["cold_open"]["bubbles"][0], text=w, sticker=None) for w in "abcdef"]
    store.write_json(ep / "beats.json", beats)
    with pytest.raises(ChatcomError) as e:
        app.render(cast_show, runner=fake_runner)
    assert e.value.code == "CHATCOM_E_BEATS_INVALID" and e.value.recovery == "ask"
    assert "chatcom rewrite lake-house" in e.value.fix and "--export" in e.value.fix
    assert not (ep / "render").exists()
    st = store.read_json(cast_show.state_path("S01E01"))
    store.write_json(cast_show.state_path("S01E01"), {**st, "stage": "drafted"})
    with pytest.raises(ChatcomError) as e:
        app.render(cast_show, runner=fake_runner)
    assert e.value.code == "CHATCOM_E_STATE"


@needs_ffmpeg
def test_rewrite_sends_the_episode_back_and_keeps_the_paid_work(cast_show, capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "fonts_dir", lambda version=runtime.VERSION: big_fonts(tmp_path))
    app.render(cast_show, runner=fake_runner)
    ep = cast_show.episode_dir("S01E01")
    before = store.read_json(cast_show.state_path("S01E01"))
    code, out, _ = run(capsys, "--json", "rewrite", "lake-house", "--episode", "2")
    assert code == 2  # not this week's episode
    code, out, _ = run(capsys, "--json", "rewrite", "lake-house")
    res = json.loads(out)
    assert code == 0 and res["stage"] == "ingested" and "--writer replay" in res["next_command"]
    st = store.read_json(cast_show.state_path("S01E01"))
    assert (st["attempts"], st["prep_cycles"], st["image_calls"]) == (0, 0, before["image_calls"])
    assert st["accepted_flow"] == before["accepted_flow"] and st["window"] == before["window"]
    assert not (ep / "beats.json").exists() and not (ep / "bible.next.json").exists() and not (ep / "render").exists()
    assert (ep / "messages.jsonl").is_file() and (ep / "cast.next.json").is_file()
    code, _, err = run(capsys, "rewrite", "lake-house")
    assert code == 2 and "rewrite works" in err and "CHATCOM_E_STATE" in err


# ---- end to end: the real runtime (CI render job) ----------------------------------------------------

@pytest.fixture
def real_runtime(monkeypatch):
    home = Path(RUNTIME_HOME)
    monkeypatch.setattr(runtime, "root", lambda version=runtime.VERSION: home / "runtime" / version)
    return home


# Value: protects=a cast episode renders through the pinned HyperFrames to a 60 s master, and a snapshot at 360 px
# is kept for review; fails_when=the composer emits a page HyperFrames refuses, or the length drifts;
# why_new=no render existed; seam=none; no_claim=visual quality (the snapshot is for eyes)
@needs_runtime
@needs_ffmpeg
def test_a_cast_episode_renders_to_a_sixty_second_master(cast_show, real_runtime, tmp_path):
    res = app.render(cast_show)
    assert hyperframes.duration(Path(res["master"])) == pytest.approx(60.0, abs=hyperframes.TOLERANCE)
    out = Path(os.environ.get("CHATCOM_RENDER_SNAPSHOT_DIR") or tmp_path)
    out.mkdir(parents=True, exist_ok=True)
    tl = rt.timeline(store.read_json(cast_show.episode_dir("S01E01") / "beats.json"),
                     store.read_json(cast_show.episode_dir("S01E01") / "cast.next.json"), None, show_title="x")
    b = tl.segment("scene_1").bubbles[0]
    snap = out / "snapshot-360.png"
    subprocess.run([tools.ffmpeg(), "-v", "error", "-y", "-ss", f"{(b.start + b.end) / 2:.2f}", "-i", res["master"],
                    "-frames:v", "1", "-vf", "scale=360:-1", str(snap)], check=True)
    assert snap.stat().st_size > 1000


# Value: protects=the page rule blocks a real request during a render (S2/O7), and the test can fail: the same
# page without the rule does reach the listener; fails_when=the CSP is dropped or loosened; why_new=new runner;
# seam=none; no_claim=requests HyperFrames itself makes outside the page (the CI job's no-network sandbox covers it)
@needs_runtime
def test_the_page_rule_blocks_a_real_request_during_a_render(real_runtime, tmp_path):
    hits = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(404)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        for rule, want in ((True, 0), (False, 1)):
            project = tmp_path / f"probe-{rule}"
            project.mkdir()
            meta = f'<meta http-equiv="Content-Security-Policy" content="{composer.CSP}" />' if rule else ""
            (project / "index.html").write_text(
                f'<!doctype html><html><head><meta charset="UTF-8" />{meta}</head><body>'
                '<div id="root" data-composition-id="main" data-start="0" data-duration="1" data-width="108" '
                'data-height="192" data-no-timeline><section class="clip" data-start="0" data-duration="1">'
                f'<img id="probe" alt="" src="http://127.0.0.1:{port}/probe.png"></section></div></body></html>',
                "utf-8")
            hits.clear()
            subprocess.run([str(runtime.binary()), "render", str(project), "--output", str(project / "o.mp4"),
                            "--quality", "draft"], env=runtime.env(), capture_output=True, timeout=300)
            assert (len(hits) > 0) == bool(want), (rule, hits)
    finally:
        srv.shutdown()


# Value: protects=every shipped font or sound is listed, unchanged and under OFL or CC0 (S1/S5); fails_when=a file
# is added without a manifest row, edited after it, or listed under another licence; why_new=the licence check read
# only Python packages; seam=none; no_claim=that the manifest's licence claims are true at the source
def test_the_licence_check_catches_an_unlisted_or_changed_asset(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("check_licences", Path(__file__).parent.parent / "scripts" /
                                                  "check_licences.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    shipped = Path(composer.__file__).parent.parent / "assets"
    assert mod.check_assets(str(shipped)) == 0
    copy = tmp_path / "assets"
    shutil.copytree(shipped, copy)
    (copy / "fonts" / "Stray.woff2").write_bytes(b"x")
    assert mod.check_assets(str(copy)) == 1
    (copy / "fonts" / "Stray.woff2").unlink()
    (copy / "fonts" / "SpaceGrotesk-Bold.woff2").write_bytes(b"changed")
    assert mod.check_assets(str(copy)) == 1


# Value: protects=a failed install never marks the runtime installed, and an old node is refused with the fix;
# fails_when=installed.json is written before every step passes, the npm exit is ignored, or the node check goes;
# why_new=install() and node_version() had no test; seam=none (fake node and npm on PATH);
# no_claim=npm's own install and HyperFrames' Chrome download (CI render job)
def _fake_tools(tmp_path, monkeypatch, node="v22.11.0", npm_exit=1) -> Path:
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for tool, body in (("node", f'echo "{node}"'), ("npm", f'echo "npm broke userconfig=$npm_config_userconfig token=${{NPM_TOKEN:-none}}"; exit {npm_exit}')):
        f = bin_ / tool
        f.write_text(f"#!/bin/sh\n{body}\n")
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_}{os.pathsep}/usr/bin{os.pathsep}/bin")
    return bin_


def test_a_failed_install_is_not_marked_installed(tmp_path, monkeypatch):
    _fake_tools(tmp_path, monkeypatch)
    monkeypatch.setenv("NPM_TOKEN", "x")  # a user ~/.npmrc may refer to it; npm must not read that file
    rt_dir = runtime.root()
    rt_dir.mkdir(parents=True)
    (rt_dir / "installed.json").write_text("{}")  # a stale marker from an earlier install
    with pytest.raises(ChatcomError) as e:
        runtime.install()
    assert e.value.code == "CHATCOM_E_PREFLIGHT" and "npm ci failed" in e.value.what
    assert e.value.side_effects == "partial" and runtime.INSTALL_COMMAND in e.value.fix
    assert not (rt_dir / "installed.json").exists() and "npm broke" in (rt_dir / "install.log").read_text()
    assert (rt_dir / "package-lock.json").is_file()
    # Value: protects=npm ci runs with no user ~/.npmrc and no token, so a user config that refers to a stripped
    # token cannot abort the install; fails_when=npm_config_userconfig goes; why_new=review pass 2 (red-team#4);
    # seam=none (fake npm on PATH); no_claim=real npm's config parsing
    log = (rt_dir / "install.log").read_text()
    assert "token=none" in log and f"userconfig={rt_dir / '.npmrc-empty'}" in log
    assert (rt_dir / ".npmrc-empty").read_text() == ""


def test_an_old_node_is_refused_with_the_fix(tmp_path, monkeypatch):
    _fake_tools(tmp_path, monkeypatch, node="v18.19.0")
    for step in (runtime.require, runtime.install):
        with pytest.raises(ChatcomError) as e:
            step()
        assert e.value.code == "CHATCOM_E_PREFLIGHT" and "too old" in e.value.what and "22" in e.value.fix
    assert not runtime.root().exists() or not (runtime.root() / "install.log").exists()


# Value: protects=doctor reports the render runtime and a missing runtime alone does not fail it; fails_when=the
# runtime row goes or the "needed from" pass rule breaks; why_new=test_doctor_runs accepts exit 0 or 3;
# seam=none (shutil.which faked); no_claim=the other doctor rows
def test_doctor_lists_the_runtime_and_a_missing_one_is_not_a_failure(capsys, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda tool: "/bin/" + tool)
    doctor = app.doctor
    rows = {name: (good, detail) for name, good, detail in doctor()}
    row = next(v for k, v in rows.items() if k.startswith("render runtime"))
    assert row[0] is False and runtime.INSTALL_COMMAND in row[1]
    monkeypatch.setattr(app, "doctor", lambda: [(n, g, d) for n, (g, d) in rows.items()])
    code, out, _ = run(capsys, "doctor")
    assert "render runtime" in out and code == (0 if all(g or "needed from" in d or "not set;" in d
                                                         for g, d in rows.values()) else 3)
    runtime.root().mkdir(parents=True)
    (runtime.root() / "installed.json").write_text("{}")
    assert next(g for n, g, _ in doctor() if n.startswith("render runtime")) is True


# ---- pre-landing review fixes (pass 1) ---------------------------------------------------------------
# Value: protects=a failed re-render never leaves `rendered` with no master, and says what it removed;
# fails_when=the stage is left at rendered after the old master is wiped; why_new=review found it (QA replayed it);
# seam=HyperFrames (a runner that fails); no_claim=a real Chrome crash
@needs_ffmpeg
def test_a_failed_rerender_goes_back_to_cast_and_says_so(cast_show, monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "fonts_dir", lambda version=runtime.VERSION: big_fonts(tmp_path))
    app.render(cast_show, runner=fake_runner)

    def boom(project, master, *, expected, log):
        raise ChatcomError("CHATCOM_E_RENDER", "the render failed (HyperFrames exit 1)", "Run it again.",
                           recovery="retry")
    with pytest.raises(ChatcomError) as e:
        app.render(cast_show, runner=boom)
    st = store.read_json(cast_show.state_path("S01E01"))
    assert st["stage"] == "cast" and not (cast_show.episode_dir("S01E01") / "render" / "master.mp4").exists()
    assert e.value.side_effects == "partial" and "back at stage 'cast'" in e.value.fix
    assert app.status(cast_show)["next"] == "chatcom render lake-house"
    assert app.render(cast_show, runner=fake_runner)["stage"] == "rendered"  # and it renders again

    def interrupted(project, master, *, expected, log):  # Ctrl-C during a re-render (review pass 2)
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        app.render(cast_show, runner=interrupted)
    assert store.read_json(cast_show.state_path("S01E01"))["stage"] == "cast"


# Value: protects=no provider key or token reaches npm's install scripts or HyperFrames; fails_when=env() goes back
# to copying os.environ; why_new=review found the keys passed through; seam=none; no_claim=what a child does with
# the variables that remain
def test_the_render_children_get_no_keys_or_tokens(monkeypatch):
    for name in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "GH_TOKEN", "NPM_TOKEN", "SOME_SECRET", "AWS_PROFILE"):
        monkeypatch.setenv(name, "x")
    monkeypatch.setenv("PATH", os.environ.get("PATH", "/bin"))
    env = runtime.env()
    assert not {"GEMINI_API_KEY", "ANTHROPIC_API_KEY", "GH_TOKEN", "NPM_TOKEN", "SOME_SECRET", "AWS_PROFILE"} & set(env)
    assert env["PATH"] and env["HYPERFRAMES_NO_TELEMETRY"] == "1"


# Value: protects=a render that hangs past the limit, or is interrupted, is stopped: TERM first so HyperFrames can
# close its Chrome, KILL if it ignores TERM; fails_when=the timeout or interrupt path stops nothing, or skips TERM;
# why_new=review passes 1-2 (orphaned Chrome, Ctrl-C); seam=HyperFrames (a fake that starts a child and hangs);
# no_claim=puppeteer's own Chrome cleanup
def _hanging(tmp_path, honour_term: bool) -> tuple[Path, Path]:
    pids = tmp_path / "pids"
    exe = tmp_path / ("hf-term" if honour_term else "hf-stubborn")
    trap = "trap 'kill $CHILD; exit 143' TERM" if honour_term else "trap '' TERM"
    exe.write_text(f'#!/bin/sh\n{trap}\nsleep 120 &\nCHILD=$!\necho "$$ $CHILD" > "{pids}"\n'
                   'while :; do sleep 1; done\n')
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe, pids


def _gone(pid: int) -> bool:
    import time
    for _ in range(30):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        try:  # reap it if it is our own zombie
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass
        time.sleep(0.1)
    return False


@pytest.mark.parametrize("honour_term", [True, False])
def test_a_hung_render_is_stopped(tmp_path, monkeypatch, honour_term):
    monkeypatch.setattr(hyperframes, "RENDER_SECONDS", 1)
    monkeypatch.setattr(hyperframes, "STOP_GRACE", 1)
    monkeypatch.setattr(tools, "ffprobe", lambda why="": "/bin/true")
    exe, pids = _hanging(tmp_path, honour_term)
    project = tmp_path / "render"
    project.mkdir()
    with pytest.raises(ChatcomError) as e:
        hyperframes.render(project, project / "master.mp4", expected=60.0, log=project / "render.log", binary=exe)
    assert e.value.code == "CHATCOM_E_RENDER" and "was stopped" in e.value.what
    shell, child = (int(x) for x in pids.read_text().split())
    assert _gone(shell)  # TERM, or KILL after the grace period
    if honour_term:
        assert _gone(child)  # the renderer closed what it started (as puppeteer closes Chrome)
    else:
        os.kill(child, 9)  # a stubborn renderer's own child: clean up the test's leftover


def test_an_interrupted_render_is_stopped(tmp_path, monkeypatch):
    monkeypatch.setattr(hyperframes, "STOP_GRACE", 1)
    monkeypatch.setattr(tools, "ffprobe", lambda why="": "/bin/true")
    exe, pids = _hanging(tmp_path, True)
    project = tmp_path / "render"
    project.mkdir()
    real_wait, calls = subprocess.Popen.wait, []

    def interrupted(self, timeout=None):
        calls.append(timeout)
        if len(calls) == 1:
            import time
            for _ in range(50):  # let the fake start its child first
                if pids.exists() and pids.read_text().strip():
                    break
                time.sleep(0.05)
            raise KeyboardInterrupt
        return real_wait(self, timeout=timeout)
    monkeypatch.setattr(subprocess.Popen, "wait", interrupted)
    with pytest.raises(KeyboardInterrupt):
        hyperframes.render(project, project / "master.mp4", expected=60.0, log=project / "render.log", binary=exe)
    shell, child = (int(x) for x in pids.read_text().split())
    assert _gone(shell) and _gone(child) and not (project / "master.part.mp4").exists()
