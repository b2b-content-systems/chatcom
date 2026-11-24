"""The encoder: the master video -> the MP4 that goes in the group chat.

    render/master.mp4 -> one fixed-quality H.264 encode (CRF) -> fits --max-size? done
                      -> else two-pass at a target bitrate, up to 3 tries, each lower
                      -> episodes/<code>/episode_NNN.mp4 (a new number each time; older files stay)

The master is never changed or removed, so a missed cap can be retried with a
bigger --max-size and no new render. Also takes the two "Previously on" stills
(D15: 0.5 s into scenes 1 and 3) from the master."""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

from chatcom.adapters import tools
from chatcom.core.errors import ChatcomError

DEFAULT_MAX_BYTES = 16 * 1000 * 1000   # WhatsApp's video limit as of v0.1 (check on a device before release)
CRF = 23
TRIES = 3
SHRINK = 0.85          # each two-pass try aims this much lower than the one before
AUDIO_KBPS = 128
ENCODE_SECONDS = 900
STILL_AT = 0.5         # seconds into a scene (D15)


def parse_size(text: str) -> int:
    """'16MB', '16M', '15.5 MB', '900KB' or a plain byte count -> bytes (decimal units, as phones show them)."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kKmMgG]?)[bB]?\s*", text or "")
    if not m:
        raise ChatcomError("CHATCOM_E_USAGE", f"not a size: {text!r}", "Pass a size like 16MB, 15.5MB or 900KB.")
    unit = {"": 1, "k": 1000, "m": 1000 ** 2, "g": 1000 ** 3}[m.group(2).lower()]
    size = int(float(m.group(1)) * unit)
    if size < 100_000:
        raise ChatcomError("CHATCOM_E_USAGE", f"--max-size {text} is too small for a 60-second video",
                           "Pass at least 100KB; the group chat default is 16MB.")
    return size


def next_path(folder: Path) -> Path:
    """episode_001.mp4, then _002, ...: an encode never overwrites an earlier one."""
    taken = [int(m.group(1)) for p in folder.glob("episode_*.mp4") if (m := re.fullmatch(r"episode_(\d{3,})\.mp4", p.name))]
    return folder / f"episode_{max(taken, default=0) + 1:03d}.mp4"


def _run(cmd: list[str], log: Path, what: str) -> None:
    with log.open("a", encoding="utf-8") as fh:
        fh.write("$ " + " ".join(cmd) + "\n")
        fh.flush()
        try:
            r = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                               timeout=ENCODE_SECONDS)
        except subprocess.TimeoutExpired:
            raise ChatcomError("CHATCOM_E_RENDER", f"{what} took longer than {ENCODE_SECONDS // 60} minutes",
                               f"Read {log}; run `chatcom render <show> --max-size <size>` again.", recovery="retry",
                               context={"log": str(log)}, trace_path=str(log)) from None
    if r.returncode != 0:
        raise ChatcomError("CHATCOM_E_RENDER", f"{what} failed (ffmpeg exit {r.returncode})",
                           f"Read {log}; run `chatcom render <show> --max-size <size>` again.", recovery="retry",
                           context={"log": str(log)}, trace_path=str(log))


def _base(ffmpeg: str, master: Path) -> list[str]:
    return [ffmpeg, "-nostdin", "-v", "error", "-y", "-i", str(master), "-map", "0:v:0", "-map", "0:a?",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium", "-c:a", "aac", "-b:a", f"{AUDIO_KBPS}k",
            "-movflags", "+faststart"]


def encode(master: Path, folder: Path, *, max_bytes: int, seconds: float, log: Path) -> tuple[Path, list[str]]:
    """Encode `master` into the next episode_NNN.mp4 under `folder`. Returns
    the path and a line per try. Over the cap after every try: exit 5, nothing kept."""
    ffmpeg = tools.ffmpeg("the encode makes the group-chat MP4 with it")
    folder.mkdir(parents=True, exist_ok=True)
    out = next_path(folder)
    part = out.with_name(out.stem + ".part.mp4")
    tries: list[str] = []
    try:
        _run(_base(ffmpeg, master) + ["-crf", str(CRF), str(part)], log, "the encode")
        size = part.stat().st_size
        tries.append(f"crf {CRF}: {size} bytes")
        kbps = int((max_bytes * 8 * 0.95 / max(seconds, 1)) / 1000) - AUDIO_KBPS
        for _ in range(TRIES):
            if size <= max_bytes:
                part.replace(out)
                return out, tries
            if kbps < 100:
                break
            with tempfile.TemporaryDirectory() as td:
                passlog = str(Path(td) / "pass")
                common = _base(ffmpeg, master) + ["-b:v", f"{kbps}k", "-passlogfile", passlog]
                _run(common + ["-pass", "1", "-an", "-f", "mp4", os.devnull], log, "the first pass")
                _run(common + ["-pass", "2", str(part)], log, "the second pass")
            size = part.stat().st_size
            tries.append(f"two-pass {kbps} kb/s: {size} bytes")
            kbps = int(kbps * SHRINK)
        if size <= max_bytes:
            part.replace(out)
            return out, tries
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    part.unlink(missing_ok=True)
    raise ChatcomError("CHATCOM_E_SIZE_CAP", f"the MP4 stays over the {max_bytes} byte cap after {len(tries)} "
                       f"{'try' if len(tries) == 1 else 'tries'} "
                       f"({'; '.join(tries)})",
                       "Pass a bigger cap with `chatcom render <show> --max-size <size>` (the render is kept, so only "
                       "the encode runs), or check the group chat's video limit.", recovery="ask",
                       context={"max_bytes": max_bytes, "tries": tries, "master": str(master)})


def stills(master: Path, scene_starts: dict[str, float], folder: Path, log: Path) -> list[Path]:
    """One JPEG per scene, STILL_AT seconds in: stills/scene_1.jpg, stills/scene_3.jpg."""
    ffmpeg = tools.ffmpeg("the encode takes the 'Previously on' stills with it")
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for name, start in scene_starts.items():
        dest = folder / f"{name}.jpg"
        _run([ffmpeg, "-nostdin", "-v", "error", "-y", "-ss", f"{start + STILL_AT:.3f}", "-i", str(master),
              "-frames:v", "1", "-q:v", "3", str(dest)], log, f"the {name} still")
        out.append(dest)
    return out
