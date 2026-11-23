"""Characters off the green: ffmpeg's chromakey on the colour sampled from the
image's corners (a fixed #00B140 also keyed blue clothes, verified 2026-11-23).
No matting model or weights ship."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from chatcom.core.errors import ChatcomError

SIMILARITY = 0.10
BLEND = 0.03


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise ChatcomError("CHATCOM_E_PREFLIGHT", "ffmpeg is not installed; casting keys characters off green with it",
                           "Install ffmpeg (macOS: `brew install ffmpeg`; Debian/Ubuntu: `apt install ffmpeg`).")
    return exe


def corner_colour(src: Path) -> str:
    """The average colour of the top-left 8x8 pixels, as 0xRRGGBB."""
    out = subprocess.run([_ffmpeg(), "-nostdin", "-v", "error", "-i", str(src), "-vf", "crop=8:8:0:0,scale=1:1",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return "0x" + out[:3].hex()


def key(src: Path, dst: Path) -> Path:
    """A PNG with the background made transparent."""
    c = corner_colour(src)
    r = subprocess.run([_ffmpeg(), "-nostdin", "-v", "error", "-i", str(src), "-vf",
                        f"chromakey={c}:{SIMILARITY}:{BLEND},format=rgba", "-frames:v", "1", "-y", str(dst)],
                       capture_output=True)
    if r.returncode != 0:
        raise ChatcomError("CHATCOM_E_IMAGE_UNREADABLE", f"{src.name}: the cutout failed",
                           "Run `chatcom cast` again; the image is remade.", context={"image": src.name})
    return dst
