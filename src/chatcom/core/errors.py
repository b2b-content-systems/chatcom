"""The error contract: every failure has a stable code, a what line with real
values, a fix, a recovery, its side effects and context. Exit codes:
0 success, 2 usage or setup, 3 preflight, 4 provider, 5 render or encode,
6 awaiting confirmation. docs/errors.md has an anchor for every code."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_PREFLIGHT = 3
EXIT_PROVIDER = 4
EXIT_RENDER = 5
EXIT_CONFIRM = 6

# code -> exit status. The docs anchor is the code in lower case.
CODES: dict[str, int] = {
    "CHATCOM_E_USAGE": EXIT_USAGE,
    "CHATCOM_E_NO_TERMINAL": EXIT_USAGE,
    "CHATCOM_E_EXPORT_NOT_FOUND": EXIT_USAGE,
    "CHATCOM_E_EXPORT_UNREADABLE": EXIT_USAGE,
    "CHATCOM_E_EXPORT_ZIP": EXIT_USAGE,
    "CHATCOM_E_UNPARSED_LINES": EXIT_USAGE,
    "CHATCOM_E_EMPTY_EXPORT": EXIT_USAGE,
    "CHATCOM_E_STALE_EXPORT": EXIT_USAGE,
    "CHATCOM_E_UNKNOWN_MEMBER": EXIT_USAGE,
    "CHATCOM_E_SETUP_INVALID": EXIT_USAGE,
    "CHATCOM_E_SHOW_EXISTS": EXIT_USAGE,
    "CHATCOM_E_SHOW_NOT_FOUND": EXIT_USAGE,
    "CHATCOM_E_SHOW_AMBIGUOUS": EXIT_USAGE,
    "CHATCOM_E_BIBLE_INVALID": EXIT_USAGE,
    "CHATCOM_E_SCHEMA_NEWER": EXIT_USAGE,
    "CHATCOM_E_SCHEMA_OLD": EXIT_USAGE,
    "CHATCOM_E_SOURCE_AMBIGUOUS": EXIT_USAGE,
    "CHATCOM_E_SOURCE_UNKNOWN": EXIT_USAGE,
    "CHATCOM_E_THREAD_REQUIRED": EXIT_USAGE,
    "CHATCOM_E_EXPORT_OUTSIDE_INBOX": EXIT_USAGE,
    "CHATCOM_E_TRANSCRIPT_INVALID": EXIT_USAGE,
    "CHATCOM_E_IMAGE_UNREADABLE": EXIT_USAGE,
    "CHATCOM_E_TOO_MANY_IMAGES": EXIT_USAGE,
    "CHATCOM_E_SPEAKER_UNMAPPED": EXIT_USAGE,
    "CHATCOM_E_IMPORT_IN_PROGRESS": EXIT_USAGE,
    "CHATCOM_E_LOCKED": EXIT_USAGE,
    "CHATCOM_E_STATE": EXIT_USAGE,
    "CHATCOM_E_BEATS_INVALID": EXIT_USAGE,
    "CHATCOM_E_TIMEZONE": EXIT_USAGE,
    "CHATCOM_E_AWAITING_CONFIRMATION": EXIT_CONFIRM,
    "CHATCOM_E_WRITER_GAVE_UP": EXIT_PROVIDER,
    "CHATCOM_E_WRITER_UNAVAILABLE": EXIT_PROVIDER,
    "CHATCOM_E_WRITER_BAD_REQUEST": EXIT_PROVIDER,
    "CHATCOM_E_PREFLIGHT": EXIT_PREFLIGHT,
}

RECOVERY = ("retry", "fix and retry", "ask")
SIDE_EFFECTS = ("none", "partial", "complete")

_KEY_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY")
_KEY_PATTERNS = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),
)


def redact(text: str) -> str:
    """Remove API key values from any text before it is printed or logged."""
    if not text:
        return text
    for var in _KEY_VARS:
        val = os.environ.get(var)
        if val and len(val) >= 8:
            text = text.replace(val, f"[{var} redacted]")
    for pat in _KEY_PATTERNS:
        text = pat.sub("[key redacted]", text)
    return text


@dataclass
class ChatcomError(Exception):
    code: str
    what: str
    fix: str
    recovery: str = "fix and retry"
    side_effects: str = "none"
    context: dict = field(default_factory=dict)
    trace_path: str | None = None

    def __post_init__(self) -> None:
        if self.code not in CODES:
            raise ValueError(f"unknown error code {self.code}")
        if self.recovery not in RECOVERY or self.side_effects not in SIDE_EFFECTS:
            raise ValueError("bad recovery or side_effects")
        super().__init__(self.what)

    @property
    def exit_code(self) -> int:
        return CODES[self.code]

    def envelope(self) -> dict:
        return {
            "code": self.code,
            "what": redact(self.what),
            "fix": redact(self.fix),
            "recovery": self.recovery,
            "side_effects": self.side_effects,
            "context": {k: redact(str(v)) if isinstance(v, str) else v for k, v in self.context.items()},
            "trace_path": self.trace_path,
        }

    def human(self) -> str:
        lines = [f"error {self.code}: {redact(self.what)}", f"  fix: {redact(self.fix)}",
                 f"  recovery: {self.recovery} · side effects: {self.side_effects}",
                 f"  docs: docs/errors.md#{self.code.lower()}"]
        if self.trace_path:
            lines.append(f"  log: {self.trace_path}")
        return "\n".join(lines)
