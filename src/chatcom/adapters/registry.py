"""One registry of source adapters, and the sniffing that picks one.

Order (docs/design/any-group-chat.md, "Sniffing"):

    1. header  a text file whose first non-blank line is `# source:` or
               `# speakers:` is a transcript
    2. images  a folder of only images, or image files: screenshots
    3. container  zip and JSON by their contents (WhatsApp, Telegram)
    4. text    text by structure (WhatsApp Android and iOS lines)

Two matches in one step is ambiguous; no match is unknown. A text file with no
transcript header is never read as a transcript unless --source transcript is
passed: an arbitrary file (a config, a key) must not reach a model."""
from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from chatcom.adapters.source import ParseResult
from chatcom.core.errors import ChatcomError

TIERS = ("header", "images", "container", "text")
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".heic", ".heif"}
HEADER_PREFIXES = ("# source:", "# speakers:")
PEEK_BYTES = 64 * 1024


@dataclass
class Context:
    tz: str
    aliases: dict = field(default_factory=dict)
    key: bytes | None = None
    thread: str | None = None
    # screenshots only: reader, workdir, operator, speakers, reverse, anchor, max_images, cache_dir
    shots: dict | None = None


@dataclass
class Adapter:
    name: str
    # tier -> matcher; an adapter can match in more than one tier (WhatsApp
    # zips are containers, its .txt files are text)
    matchers: dict[str, Callable[[list[Path]], bool]]
    load: Callable[[list[Path], Context], tuple[ParseResult, str]]
    later: bool = False  # registered so sniffing can name it, but not built yet


ADAPTERS: dict[str, Adapter] = {}


def register(adapter: Adapter) -> None:
    if any(t not in TIERS for t in adapter.matchers):
        raise ValueError(adapter.matchers)
    ADAPTERS[adapter.name] = adapter


def peek(path: Path, n: int = PEEK_BYTES) -> str:
    """The start of a file as text, for sniffing; never the whole file."""
    with open(path, "rb") as fh:
        raw = fh.read(n)
    # The sample is cut at n bytes, often inside a multi-byte character, so a
    # strict decode would fail on a valid file. UTF-16 only with its BOM.
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="ignore")
    return raw.decode("utf-8-sig", errors="ignore")


def first_line(path: Path) -> str:
    try:
        text = peek(path, 4096)
    except OSError:
        return ""
    for ln in text.lstrip("﻿").splitlines():
        if ln.strip():
            return ln.strip()
    return ""


def has_transcript_header(path: Path) -> bool:
    return path.is_file() and first_line(path).lower().startswith(HEADER_PREFIXES)


def expand(exports: list[str]) -> list[Path]:
    paths = [Path(e).expanduser() for e in exports]
    for p in paths:
        if not p.exists():
            raise ChatcomError("CHATCOM_E_EXPORT_NOT_FOUND", f"no file or folder at {p}",
                               "Check the path. docs/whatsapp-export.md shows how to export a chat.",
                               context={"path": str(p)})
    return paths


def _is_images(paths: list[Path]) -> bool:
    files: list[Path] = []
    for p in paths:
        if p.is_dir():
            inner = [c for c in p.iterdir() if not c.name.startswith(".")]
            if not inner or any(c.is_dir() for c in inner):
                return False
            files += inner
        else:
            files.append(p)
    return bool(files) and all(f.suffix.lower() in IMAGE_EXT for f in files)


def sniff(paths: list[Path], source: str | None = None) -> Adapter:
    if source:
        if source not in ADAPTERS or source == "synthetic":
            raise ChatcomError("CHATCOM_E_USAGE", f"--source {source!r} is not a source",
                               f"Use one of: {', '.join(n for n in ADAPTERS)}.")
        return _ready(ADAPTERS[source])
    if len(paths) == 1 and has_transcript_header(paths[0]):
        return _ready(_by_name("transcript", "a transcript header"))
    if _is_images(paths):
        return _ready(_by_name("screenshots", "image files"))
    for tier in ("container", "text"):
        hits = [a for a in ADAPTERS.values() if tier in a.matchers and _safe_match(a.matchers[tier], paths)]
        if len(hits) > 1:
            names = [a.name for a in hits]
            raise ChatcomError("CHATCOM_E_SOURCE_AMBIGUOUS",
                               f"{paths[0].name} looks like more than one kind of export: {', '.join(names)}",
                               f"Pass --source {names[0]} (or another of {names}).", context={"candidates": names})
        if hits:
            return _ready(hits[0])
    raise ChatcomError("CHATCOM_E_SOURCE_UNKNOWN",
                       f"{paths[0].name} is not a chat export this chatcom knows",
                       "Pass a WhatsApp export (.zip or .txt), a Telegram result.json folder, screenshots, or a "
                       "transcript whose first line is `# source: paste` (docs/transcript-format.md). "
                       "A file without that header is never read as a transcript.",
                       context={"path": str(paths[0])})


def _safe_match(fn: Callable[[list[Path]], bool], paths: list[Path]) -> bool:
    try:
        return fn(paths)
    except (OSError, ValueError, zipfile.BadZipFile, UnicodeError):
        return False


def _by_name(name: str, what: str) -> Adapter:
    if name not in ADAPTERS:
        raise ChatcomError("CHATCOM_E_SOURCE_UNKNOWN", f"these look like {what}, which this chatcom cannot read yet",
                           "Update chatcom; until then, pass a WhatsApp export.", context={"source": name})
    return ADAPTERS[name]


def _ready(a: Adapter) -> Adapter:
    if a.later:
        raise ChatcomError("CHATCOM_E_SOURCE_UNKNOWN", f"{a.name} imports are not in this chatcom yet",
                           "Update chatcom; until then, pass a WhatsApp export or a transcript.",
                           context={"source": a.name})
    return a


def load(exports: list[str] | str, ctx: Context, source: str | None = None) -> tuple[ParseResult, str, Adapter]:
    """(parse result, display name for a default slug, adapter used)."""
    paths = expand([exports] if isinstance(exports, str) else list(exports))
    adapter = sniff(paths, source)
    res, display = adapter.load(paths, ctx)  # each adapter names itself (a screenshots transcript stays screenshots)
    return res, display, adapter


def _register_builtin() -> None:
    from chatcom.adapters import screenshots, telegram, transcript, whatsapp
    register(Adapter("whatsapp", {"container": whatsapp.matches_container, "text": whatsapp.matches_text},
                     whatsapp.load))
    # The header tier is checked first in sniff(); a transcript has no other matcher.
    register(Adapter("transcript", {}, transcript.load))
    register(Adapter("telegram", {"container": telegram.matches_container}, telegram.load))
    # The images tier is checked in sniff(); screenshots have no other matcher.
    register(Adapter("screenshots", {}, screenshots.load))


_register_builtin()
