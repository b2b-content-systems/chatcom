"""Characters off the green: ffmpeg's chromakey on the colour sampled from the
image's corner (a fixed #00B140 also keyed blue clothes, verified 2026-11-23),
then a despill on the cut edge only, so hair loses its green fringe and green
inside the figure is left alone. No matting model or weights ship.

    corner colour --is it green?--> no: NoGreen (the caller redraws once)
                  --yes--> chromakey -> despill where alpha is near 0 -> PNG"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from chatcom.core.errors import ChatcomError

SIMILARITY = 0.10
BLEND = 0.03
EDGE = 3  # pixels into the figure the despill reaches


class NoGreen(ChatcomError):
    """The corner is not green: keying on it would cut that colour out of the figure."""


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise ChatcomError("CHATCOM_E_PREFLIGHT", "ffmpeg is not installed; casting keys characters off green with it",
                           "Install ffmpeg (macOS: `brew install ffmpeg`; Debian/Ubuntu: `apt install ffmpeg`).")
    return exe


def corner_colour(src: Path) -> str:
    """The average colour of the top-left 8x8 pixels, as 0xRRGGBB."""
    r = subprocess.run([_ffmpeg(), "-nostdin", "-v", "error", "-i", str(src), "-vf", "crop=8:8:0:0,scale=1:1",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True)
    if r.returncode != 0 or len(r.stdout) < 3:
        raise ChatcomError("CHATCOM_E_IMAGE_UNREADABLE", f"{src.name}: the drawing could not be read",
                           "Run `chatcom cast` again; the image is drawn again.", context={"image": src.name})
    return "0x" + r.stdout[:3].hex()


def is_green(colour: str) -> bool:
    """A chroma-key green: green clearly above red and blue (#00B140 is 0,177,64)."""
    r, g, b = (int(colour[i:i + 2], 16) for i in (2, 4, 6))
    return g >= 90 and g >= 1.4 * max(r, b) + 20


def key(src: Path, dst: Path) -> Path:
    """A PNG with the background made transparent."""
    c = corner_colour(src)
    if not is_green(c):
        raise NoGreen("CHATCOM_E_IMAGE_UNREADABLE", f"{src.name}: the drawing has no green background (corner {c})",
                      "Run `chatcom cast` again; the image is drawn again.", context={"image": src.name, "corner": c})
    edge = ",".join(["dilation"] * EDGE)  # the mask: the background, grown EDGE pixels into the figure
    graph = (f"[0]chromakey={c}:{SIMILARITY}:{BLEND},format=rgba,split=3[k][k2][k3];[k2]despill=type=green[d];"
             f"[k3]alphaextract,negate,{edge},format=rgba[m];[k][d][m]maskedmerge=planes=7")
    r = subprocess.run([_ffmpeg(), "-nostdin", "-v", "error", "-i", str(src), "-filter_complex", graph,
                        "-frames:v", "1", "-y", str(dst)], capture_output=True)
    if r.returncode != 0:
        raise ChatcomError("CHATCOM_E_IMAGE_UNREADABLE", f"{src.name}: the cutout failed",
                           "Run `chatcom cast` again; the image is remade.", context={"image": src.name})
    return dst
