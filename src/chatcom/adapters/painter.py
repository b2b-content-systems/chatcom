"""Image generation for casting: the painter port, the Gemini painter and the
replay painter (CI and the demo).

Gemini rules (docs/design and PLAN.md, "Image errors by kind"):

    model gone (404)       -> the whole batch moves to the fallback model
    5xx / timeout          -> 3 retries with backoff, then the fallback model
    429 / quota            -> stop; images already made are kept in the cache
    content refusal        -> not an error: the caller casts that member from behavior
    no image (NO_IMAGE, or text only) -> asked once more, then as above

The operator's own GEMINI_API_KEY pays for every call; CHATCOM has no key."""
from __future__ import annotations

import hashlib
import json
import re
import socket
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from chatcom.adapters.writers import llm_fallback as lf
from chatcom.core.errors import ChatcomError, redact

DEFAULT_MODEL = "gemini-nano-banana-2.1"
FALLBACK_MODEL = "gemini-3.1-flash-image"
RETRIES = (2.0, 6.0, 15.0)
MAX_TOKENS = 16384      # Nano Banana 2.1 thinks before it draws; 4096 cut it off (live, 2026-11-23)
MAX_TOKENS_RETRY = 32768
TIMEOUT = 240           # an image reply can take minutes; llm-fallback's 60s gave up on calls Google still billed
_ACCOUNT = re.compile(r"API_KEY_INVALID|API key not valid|billing|permission_denied", re.I)
_QUOTA = re.compile(r"\(429\)|quota|RESOURCE_EXHAUSTED", re.I)


class MaybeBilled(lf.Unavailable):
    """The request was sent and the reply never came (a timeout or a dropped
    connection). Google may still finish and bill it, so the call stays counted."""


def image_transport(url: str, headers: dict, payload: dict) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=lf._ssl_context()) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read() or b"{}")
        except ValueError:
            body = {}
        return e.code, body
    except urllib.error.URLError as e:  # no connection was made: nothing was sent
        if isinstance(e.reason, (socket.timeout, TimeoutError)):
            raise MaybeBilled(f"network: {e}") from e
        raise lf.Unavailable(f"network: {e}") from e
    except (socket.timeout, TimeoutError, ConnectionError) as e:
        raise MaybeBilled(f"network: {e}") from e


class BudgetSpent(Exception):
    """The next call would go over the number of calls the operator approved."""


class SwitchModel(Exception):
    """This model cannot do the batch: rerun it on the fallback."""


@dataclass
class Painted:
    data: bytes | None      # None when the model refused
    mime: str
    model: str
    refused: bool = False


class GeminiPainter:
    mode = "gemini"

    def __init__(self, model: str | None = None, transport=None):
        self.requested = model
        self.model = model or DEFAULT_MODEL
        self.transport = transport or image_transport
        self.backoff = RETRIES
        self.calls = 0
        self.budget: int | None = None  # set by the caller to the approved count; never exceeded
        self._lock = threading.Lock()

    def paint(self, prompt: str, refs: list[tuple[bytes, str]], aspect: str) -> Painted:
        ans = self._ask(prompt, refs, aspect, MAX_TOKENS)
        if ans.cut_off:  # the model ran out of room before drawing: once more with more room
            ans = self._ask(prompt, refs, aspect, MAX_TOKENS_RETRY)
        elif not ans.images and (ans.reason == "NO_IMAGE" or ans.finish != "refused"):
            # a reply with no picture and no policy reason is often a one-off: once more before giving up
            ans = self._ask(prompt, refs, aspect, MAX_TOKENS)
        if ans.finish == "refused":
            return Painted(None, "", ans.model, refused=True)
        if not ans.images:
            raise SwitchModel(f"{self.model} returned no image (finish {ans.finish})")
        return Painted(ans.images[0].data, ans.images[0].mime, ans.model)

    def _ask(self, prompt: str, refs: list[tuple[bytes, str]], aspect: str, max_tokens: int) -> lf.Answer:
        images = [lf.Image(d, m) for d, m in refs]
        for wait in (*self.backoff, None):
            with self._lock:  # reserve the call before it is made, so the approved count is a hard cap
                if self.budget is not None and self.calls >= self.budget:
                    raise BudgetSpent(self.calls)
                self.calls += 1
            try:
                try:
                    return lf.ask_gemini(prompt, model=self.model, images=images, output="image",
                                         aspect_ratio=aspect, max_tokens=max_tokens, transport=self.transport)
                except MaybeBilled:
                    raise  # sent and maybe billed: the reserved call stays counted
                except (lf.ModelGone, lf.Unavailable, lf.BadRequest):
                    with self._lock:
                        self.calls -= 1  # answered with an error, or never sent: not billed, hand the call back
                    raise
            except lf.ModelGone as e:
                raise SwitchModel(redact(str(e))[:200]) from None
            except lf.Unavailable as e:
                msg = redact(str(e))
                if _QUOTA.search(msg) or "no GEMINI_API_KEY" in msg or "auth" in msg:
                    raise ChatcomError("CHATCOM_E_PAINTER_UNAVAILABLE", f"the image model stopped: {msg[:200]}",
                                       "Check GEMINI_API_KEY and its quota, then run `chatcom cast` again: images "
                                       "already made are kept and not paid for twice.", recovery="retry",
                                       side_effects="partial", context={"model": self.model}) from None
                if wait is None:
                    raise SwitchModel(msg[:200]) from None
                time.sleep(wait)
            except lf.BadRequest as e:
                msg = redact(str(e))
                if _ACCOUNT.search(msg):  # an invalid key or billing problem answers 400, not 401
                    raise ChatcomError("CHATCOM_E_PAINTER_UNAVAILABLE", f"the image model refused the key: {msg[:200]}",
                                       "Check GEMINI_API_KEY and its billing, then run `chatcom cast` again.",
                                       recovery="retry", context={"model": self.model}) from None
                raise ChatcomError("CHATCOM_E_PAINTER_UNAVAILABLE", msg[:300],
                                   "This is a chatcom bug: report it with the run log.", recovery="ask",
                                   context={"model": self.model}) from None
        raise AssertionError("unreachable")  # pragma: no cover


class ReplayPainter:
    """Deterministic pictures with no network: a coloured figure on chroma
    green for poses, a flat scene for the rest. `refuse_photos` refuses every
    drawing from a reference photo, to exercise the behavior fallback."""
    mode = "replay"

    def __init__(self, refuse_photos: bool = False):
        self.model = "replay-painter"
        self.refuse_photos = refuse_photos
        self.calls = 0
        self.budget: int | None = None

    def paint(self, prompt: str, refs: list[tuple[bytes, str]], aspect: str) -> Painted:
        if self.budget is not None and self.calls >= self.budget:
            raise BudgetSpent(self.calls)
        self.calls += 1
        if self.refuse_photos and "reference photo" in prompt:
            return Painted(None, "", self.model, refused=True)
        seed = hashlib.sha256(prompt.encode()).hexdigest()
        colour = f"0x{seed[:6]}"
        w, h = {"3:4": (300, 400), "16:9": (480, 270), "9:16": (270, 480)}.get(aspect, (300, 400))
        green = "On a flat, even, pure chroma-key green" in prompt
        bg = "0x00B140" if green else colour
        figure = f",drawbox=x={w // 3}:y={h // 5}:w={w // 3}:h={h * 3 // 5}:color={colour}:t=fill" if green else ""
        exe = shutil.which("ffmpeg")
        if not exe:
            raise ChatcomError("CHATCOM_E_PREFLIGHT", "ffmpeg is not installed", "Install ffmpeg.")
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "p.png"
            subprocess.run([exe, "-nostdin", "-v", "error", "-f", "lavfi", "-i", f"color=c={bg}:s={w}x{h}",
                            "-vf", f"format=rgb24{figure}", "-frames:v", "1", "-y", str(out)], check=True,
                           capture_output=True)
            return Painted(out.read_bytes(), "image/png", self.model)
