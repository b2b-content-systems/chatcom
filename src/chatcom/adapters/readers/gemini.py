"""CLI-mode screenshot reader: Gemini, with the operator's own GEMINI_API_KEY.

    gemini-3.6-flash --404 (model gone)--> gemini-3.8-flash --cannot answer--> Claude
                                                                  (ANTHROPIC_API_KEY)

One image per call, `responseJsonSchema` set to schemas/read.schema.json. A
reply that is not a valid read is asked for again with the problems listed (3
tries per image, then CHATCOM_E_READER_GAVE_UP). A reply cut off at the token
limit raises ReadTooLong: the caller splits the image and reads the halves,
which costs no try. Gemini 3.x requests send thinkingLevel "low" and never
temperature, topP or topK (llm_fallback.gemini_generation).

Keys are the operator's own, read from the environment; chatcom has none."""
from __future__ import annotations

import json
import re
import threading
import time
from collections import Counter
from functools import lru_cache
from importlib import resources
from pathlib import Path

from chatcom.adapters.writers import llm_fallback as lf
from chatcom.core.errors import ChatcomError, redact
from chatcom.core.schema import problems, schema

GEMINI_CHAIN = ("gemini-3.6-flash", "gemini-3.8-flash")
CLAUDE_MODEL = "claude-sonnet-5-5"
MAX_TOKENS = 6000
TRIES = 3
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")
_TRANSIENT = re.compile(r"\((408|429|5\d\d)\)")
BACKOFF = (2.0, 6.0)  # seconds before each retry of a transient Gemini error
_ACCOUNT = re.compile(r"credit balance|billing|quota|API_KEY_INVALID|API key not valid|permission_denied", re.I)


class ReadTooLong(Exception):
    """The reply hit the token limit: split the image and read the halves."""


@lru_cache(maxsize=1)
def _schema() -> dict:
    return {k: v for k, v in schema("read").items() if not k.startswith("$")}


@lru_cache(maxsize=1)
def instructions() -> str:
    brief = resources.files("chatcom").joinpath("prompts", "reader.md").read_text("utf-8")
    # the session brief, for one image: no files to write, no command to run
    body = brief.split("## Submitting", 1)[0]
    return (body + "\n## This call\n\nYou get ONE image. Answer with the read for it: one JSON object "
            '{"chat_name": ..., "rows": [...]} and nothing else.')


class GeminiReader:
    mode = "gemini"

    def __init__(self, model: str | None = None, transport=None, claude_model: str = CLAUDE_MODEL):
        self.models = [model] if model else list(GEMINI_CHAIN)
        self.model = self.models[0]       # names the read cache entries
        self.claude_model = claude_model
        self.transport = transport or lf.http_transport
        self.calls = 0
        self.answered_by: Counter = Counter()
        self.fallbacks: list[str] = []
        self._lock = threading.Lock()
        self.backoff = BACKOFF

    def read(self, image: Path, copy: dict) -> dict:
        data = image.read_bytes()
        feedback = ""
        for _ in range(TRIES):
            ans = self._ask(data, instructions() + feedback)
            if ans.cut_off:
                raise ReadTooLong(copy["file"])
            try:
                doc = json.loads(_FENCE.sub("", ans.text))
            except ValueError as e:
                feedback = f"\n\nYour last answer was not JSON ({e.msg}). Answer with the JSON object only."
                continue
            errs = problems("read", doc)
            if not errs:
                return doc
            feedback = ("\n\nYour last answer did not match the schema. Fix these and answer again:\n"
                        + "\n".join(f"- {e['path']}: {e['message']}" for e in errs[:20]))
        raise ChatcomError("CHATCOM_E_READER_GAVE_UP",
                           f"{copy['image']} could not be read as a chat screenshot in {TRIES} tries",
                           "Check that the image is a chat screenshot, or type that part of the chat as a "
                           "transcript (docs/transcript-format.md).", recovery="ask", side_effects="partial",
                           context={"image": copy["image"], "copy": copy["file"]})

    def _ask(self, data: bytes, prompt: str) -> lf.Answer:
        img = [lf.Image(data, "image/png")]
        common = dict(images=img, json_schema=_schema(), max_tokens=MAX_TOKENS, transport=self.transport)
        reasons: list[str] = []
        for m in self.models:
            try:
                return self._count(self._gemini_with_retry(prompt, m, common))
            except lf.ModelGone as e:
                reasons.append(redact(str(e))[:160])
                continue  # this model id is gone: the next one
            except lf.Unavailable as e:
                reasons.append(redact(str(e))[:160])
                break
            except lf.BadRequest as e:
                if not _ACCOUNT.search(str(e)):
                    raise ChatcomError("CHATCOM_E_READER_UNAVAILABLE", redact(str(e))[:300],
                                       "This is a chatcom bug: report it with the run log.", recovery="ask") from None
                reasons.append(redact(str(e))[:160])
                break
        try:
            ans = lf.ask_claude(prompt, model=self.claude_model, **common)
        except (lf.Unavailable, lf.BadRequest) as e:
            reasons.append(redact(str(e))[:160])
            raise ChatcomError("CHATCOM_E_READER_UNAVAILABLE", "no reader could answer: " + "; ".join(reasons),
                               "Set GEMINI_API_KEY (or ANTHROPIC_API_KEY for the fallback) and retry. Reads already "
                               "done are kept.", recovery="retry",
                               context={"env": "GEMINI_API_KEY, ANTHROPIC_API_KEY", "tried": self.models}) from None
        with self._lock:
            self.fallbacks.append("; ".join(reasons))
        return self._count(ans)

    def _gemini_with_retry(self, prompt: str, model: str, common: dict) -> lf.Answer:
        """A rate limit or server error is often brief: wait and ask the same
        model again, a bounded number of times, before falling back."""
        for wait in (*self.backoff, None):
            try:
                return lf.ask_gemini(prompt, model=model, **common)
            except lf.Unavailable as e:
                if wait is None or not _TRANSIENT.search(str(e)):
                    raise
                time.sleep(wait)
        raise AssertionError("unreachable")  # pragma: no cover

    def _count(self, ans: lf.Answer) -> lf.Answer:
        with self._lock:
            self.calls += 1
            self.answered_by[ans.model] += 1
        return ans
