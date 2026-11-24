"""Runs the pinned HyperFrames on a composed project and checks the master.

    render/index.html -> hyperframes render --strict (telemetry off, Chrome from the runtime)
                      -> render/master.part.mp4 -> ffprobe: 60 s +- 0.1 -> render/master.mp4

A failed render or a wrong length is exit 5 (CHATCOM_E_RENDER) with the log
path; no master.mp4 is left behind, so a rerun starts the render again."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from chatcom.adapters import runtime, tools
from chatcom.core.errors import ChatcomError

TOLERANCE = 0.1         # seconds
RENDER_SECONDS = 1800   # a stuck Chrome ends here
STOP_GRACE = 10         # seconds between TERM (HyperFrames closes its Chrome) and KILL


def duration(path: Path) -> float:
    r = subprocess.run([tools.ffprobe(), "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                       capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    try:
        return float(json.loads(r.stdout)["format"]["duration"])
    except (ValueError, KeyError, TypeError):
        return -1.0


def _failed(what: str, log: Path, **context) -> ChatcomError:
    return ChatcomError("CHATCOM_E_RENDER", what,
                        f"Read the render log at {log}. Run `chatcom render <show>` again; if it fails the same "
                        "way, report it with that log.", recovery="retry", side_effects="none",
                        context={"log": str(log), **context}, trace_path=str(log))


def _stop(p: subprocess.Popen) -> None:
    """End a render: TERM first, so HyperFrames (puppeteer) closes the Chrome it
    started, then KILL after the grace period."""
    if p.poll() is not None:
        return
    p.terminate()
    try:
        p.wait(timeout=STOP_GRACE)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait()


def render(project: Path, master: Path, *, expected: float, log: Path, binary: Path | None = None) -> float:
    """Render `project` to `master` and return its length in seconds."""
    exe = binary or runtime.require()
    tools.ffprobe()
    part = master.with_name(master.stem + ".part.mp4")
    for p in (part, master):
        p.unlink(missing_ok=True)
    log.parent.mkdir(parents=True, exist_ok=True)
    cmd = [str(exe), "render", str(project), "--output", str(part), "--strict", "--fps", "30", "--quality", "looks"]
    with log.open("w", encoding="utf-8") as fh:
        fh.write("$ " + " ".join(cmd) + "\n")
        fh.flush()
        # Same process group as chatcom: Ctrl-C and a closed terminal reach HyperFrames
        # too, and its own handlers close Chrome. A timeout or an interrupt here stops it.
        p = subprocess.Popen(cmd, cwd=project, env=runtime.env(), stdout=fh, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL)
        try:
            code = p.wait(timeout=RENDER_SECONDS)
        except subprocess.TimeoutExpired:
            _stop(p)
            part.unlink(missing_ok=True)
            raise _failed(f"the render took longer than {RENDER_SECONDS / 60:g} minutes and was stopped", log) from None
        except BaseException:
            _stop(p)
            part.unlink(missing_ok=True)
            raise
    if code != 0 or not part.is_file():
        part.unlink(missing_ok=True)
        raise _failed(f"the render failed (HyperFrames exit {code})", log, exit=code)
    got = duration(part)
    if abs(got - expected) > TOLERANCE:
        part.unlink(missing_ok=True)
        raise _failed(f"the render is {got:.2f} s long, expected {expected:.1f} s +- {TOLERANCE}", log,
                      seconds=got)
    part.replace(master)
    return got
