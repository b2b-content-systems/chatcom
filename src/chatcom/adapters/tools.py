"""One place that finds the command-line tools chatcom runs: ffmpeg, ffprobe and
node. A missing tool is a coded preflight error (exit 3) with the install
hint, never a raw FileNotFoundError."""
from __future__ import annotations

import shutil

from chatcom.core.errors import ChatcomError

_HINT = {
    "ffmpeg": "Install ffmpeg (macOS: `brew install ffmpeg`; Debian/Ubuntu: `apt install ffmpeg`).",
    "ffprobe": "Install ffmpeg, which includes ffprobe (macOS: `brew install ffmpeg`; Debian/Ubuntu: "
               "`apt install ffmpeg`).",
    "node": "Install Node.js 22 or newer (https://nodejs.org, or `brew install node`).",
    "npm": "Install Node.js 22 or newer, which includes npm (https://nodejs.org, or `brew install node`).",
}


def find(tool: str, why: str) -> str:
    """The tool's path. `why` finishes the sentence "<tool> is not installed; ..."."""
    exe = shutil.which(tool)
    if not exe:
        raise ChatcomError("CHATCOM_E_PREFLIGHT", f"{tool} is not installed; {why}", _HINT[tool],
                           context={"tool": tool})
    return exe


def ffmpeg(why: str = "chatcom needs it for images and video") -> str:
    return find("ffmpeg", why)


def ffprobe(why: str = "the render checks the video's length with it") -> str:
    return find("ffprobe", why)


def node(why: str = "the render runs HyperFrames on it") -> str:
    return find("node", why)
