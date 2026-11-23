"""Screenshot intake: the manifest, the checks and the clean copies.

    originals --manifest--> [name, capture time, size]   (EXIF time, else the file time;
                                                           order: capture time, then name)
              --copy------> 001.png, 002-1.png, 002-2.png (ffmpeg re-encode: forced demuxer,
                                                           file protocol only, no metadata;
                                                           tall images split into tiles)

Only the copies are ever read or sent. An original is opened only to read its
first bytes (kind and size) and its EXIF capture time; it is never decoded by
anything but ffmpeg (or macOS `sips` for HEIC) with a forced input format."""
from __future__ import annotations

import hashlib
import math
import shutil
import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from chatcom.core.errors import ChatcomError

MAX_IMAGES = 40
MAX_IMAGE_BYTES = 40 * 1024 * 1024
MAX_PIXELS = 60_000_000
TILE_RATIO = 2.4      # taller than this many widths: split
TILE_HEIGHT = 2.0     # each tile is this many widths tall
TILE_OVERLAP = 0.15   # share of a tile repeated in the next one
FFMPEG_SECONDS = 60
EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".heic", ".heif"}
_DEMUXER = {"png": "png_pipe", "jpeg": "jpeg_pipe", "webp": "webp_pipe"}


@dataclass
class Entry:
    name: str
    path: Path
    sha256: str
    kind: str
    width: int | None
    height: int | None
    captured: datetime
    captured_from: str  # exif | file


def _unreadable(name: str, why: str) -> ChatcomError:
    return ChatcomError("CHATCOM_E_IMAGE_UNREADABLE", f"{name}: {why}",
                        "Pass the screenshot files as your phone saved them (PNG, JPEG, WebP or HEIC).",
                        context={"image": name})


def kind_of(head: bytes) -> str | None:
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[4:8] == b"ftyp" and head[8:12] in (b"heic", b"heix", b"mif1", b"msf1", b"hevc", b"heim", b"heis"):
        return "heic"
    return None


def dimensions(data: bytes, kind: str) -> tuple[int, int] | None:
    """Width and height from the header, before anything decodes the pixels."""
    try:
        if kind == "png":
            w, h = struct.unpack(">II", data[16:24])
            return w, h
        if kind == "jpeg":
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                seg = struct.unpack(">H", data[i + 2:i + 4])[0]
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return w, h
                i += 2 + seg
            return None
        if kind == "webp":
            chunk = data[12:16]
            if chunk == b"VP8X":
                w = 1 + int.from_bytes(data[24:27], "little")
                h = 1 + int.from_bytes(data[27:30], "little")
                return w, h
            if chunk == b"VP8 ":
                w, h = struct.unpack("<HH", data[26:30])
                return w & 0x3FFF, h & 0x3FFF
            if chunk == b"VP8L":
                b = data[21:25]
                return 1 + (((b[1] & 0x3F) << 8) | b[0]), 1 + (((b[3] & 0xF) << 10) | (b[2] << 2) | ((b[1] & 0xC0) >> 6))
    except (struct.error, IndexError):
        return None
    return None


# ---- EXIF capture time --------------------------------------------------------------

def _tiff_time(t: bytes) -> datetime | None:
    """DateTimeOriginal (or DateTime) from a TIFF/EXIF block."""
    if len(t) < 8 or t[:2] not in (b"II", b"MM"):
        return None
    e = "<" if t[:2] == b"II" else ">"

    def entries(off: int):
        if off + 2 > len(t):
            return
        n = struct.unpack(e + "H", t[off:off + 2])[0]
        for k in range(min(n, 512)):
            p = off + 2 + 12 * k
            if p + 12 > len(t):
                return
            tag, typ, count, val = struct.unpack(e + "HHII", t[p:p + 12])
            yield tag, typ, count, val, p + 8

    def text(count: int, val: int, inline: int) -> str:
        raw = t[inline:inline + count] if count <= 4 else t[val:val + count]
        return raw.split(b"\0", 1)[0].decode("ascii", "ignore")

    ifd0 = struct.unpack(e + "I", t[4:8])[0]
    found: dict[int, str] = {}
    exif_ifd = None
    for tag, typ, count, val, inline in entries(ifd0):
        if tag == 0x8769:
            exif_ifd = val
        elif tag == 0x0132 and typ == 2:
            found[tag] = text(count, val, inline)
    if exif_ifd:
        for tag, typ, count, val, inline in entries(exif_ifd):
            if tag in (0x9003, 0x9004) and typ == 2:
                found[tag] = text(count, val, inline)
    for tag in (0x9003, 0x9004, 0x0132):
        try:
            return datetime.strptime(found[tag].strip(), "%Y:%m:%d %H:%M:%S")
        except (KeyError, ValueError):
            continue
    return None


def exif_time(data: bytes, kind: str) -> datetime | None:
    try:
        if kind == "jpeg":
            i = 2
            while i + 4 < len(data) and data[i] == 0xFF:
                marker = data[i + 1]
                seg = struct.unpack(">H", data[i + 2:i + 4])[0]
                if marker == 0xE1 and data[i + 4:i + 10] == b"Exif\0\0":
                    return _tiff_time(data[i + 10:i + 2 + seg])
                if marker == 0xDA:
                    return None
                i += 2 + seg
        elif kind == "png":
            i = 8
            while i + 8 <= len(data):
                n = struct.unpack(">I", data[i:i + 4])[0]
                typ = data[i + 4:i + 8]
                if typ == b"eXIf":
                    return _tiff_time(data[i + 8:i + 8 + n])
                if typ == b"IDAT":
                    return None
                i += 12 + n
        elif kind == "webp":
            i = 12
            while i + 8 <= len(data):
                typ, n = data[i:i + 4], struct.unpack("<I", data[i + 4:i + 8])[0]
                if typ == b"EXIF":
                    blk = data[i + 8:i + 8 + n]
                    return _tiff_time(blk[6:] if blk.startswith(b"Exif\0\0") else blk)
                i += 8 + n + (n & 1)
    except (struct.error, IndexError):
        return None
    return None


# ---- manifest -------------------------------------------------------------------------

def image_files(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        if p.is_dir():
            out += sorted(c for c in p.iterdir() if not c.name.startswith(".") and c.is_file())
        else:
            out.append(p)
    return out


def manifest(paths: list[Path], max_images: int = MAX_IMAGES) -> list[Entry]:
    files = image_files(paths)
    if len(files) > max_images:
        raise ChatcomError("CHATCOM_E_TOO_MANY_IMAGES", f"{len(files)} images, over the limit of {max_images}",
                           f"Import the newest {max_images} (or pass --max-images N).",
                           context={"images": len(files), "limit": max_images})
    entries = []
    for f in files:
        size = f.stat().st_size
        if size > MAX_IMAGE_BYTES:
            raise _unreadable(f.name, f"{size} bytes, over the {MAX_IMAGE_BYTES} byte limit")
        data = f.read_bytes()
        kind = kind_of(data[:16])
        if kind is None:
            raise _unreadable(f.name, "not a PNG, JPEG, WebP or HEIC image")
        dims = dimensions(data, kind)
        if dims and dims[0] * dims[1] > MAX_PIXELS:
            raise _unreadable(f.name, f"{dims[0]}x{dims[1]} pixels, over the {MAX_PIXELS} pixel limit")
        if dims and (dims[0] == 0 or dims[1] == 0):
            raise _unreadable(f.name, "the header gives an empty image")
        shot = exif_time(data, kind)
        entries.append(Entry(f.name, f, hashlib.sha256(data).hexdigest(), kind, dims[0] if dims else None,
                             dims[1] if dims else None, shot or datetime.fromtimestamp(f.stat().st_mtime),
                             "exif" if shot else "file"))
    entries.sort(key=lambda x: (x.captured, x.name))
    return entries


# ---- copies -------------------------------------------------------------------------------

def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise ChatcomError("CHATCOM_E_PREFLIGHT", "ffmpeg is not installed; chatcom re-encodes every screenshot with it",
                           "Install ffmpeg (macOS: `brew install ffmpeg`; Debian/Ubuntu: `apt install ffmpeg`).")
    return exe


def _run(cmd: list[str], name: str) -> None:
    try:
        r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=FFMPEG_SECONDS)
    except subprocess.TimeoutExpired:
        raise _unreadable(name, "the re-encode took too long") from None
    if r.returncode != 0:
        raise _unreadable(name, "the image could not be decoded")


def _heic_to_png(src: Path, tmp: Path) -> Path:
    if sys.platform != "darwin" or not shutil.which("sips"):
        raise _unreadable(src.name, "HEIC is read only on macOS; save the screenshot as PNG or JPEG")
    out = tmp / (hashlib.sha256(str(src).encode()).hexdigest()[:12] + ".png")
    _run(["sips", "-s", "format", "png", str(src.resolve()), "--out", str(out)], src.name)
    return out


def _encode(src: Path, kind: str, dst: Path, name: str, crop: tuple[int, int, int, int] | None = None) -> None:
    cmd = [_ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error", "-protocol_whitelist", "file",
           "-f", _DEMUXER[kind], "-i", f"file:{src}", "-map_metadata", "-1", "-frames:v", "1"]
    if crop:
        w, h, x, y = crop
        cmd += ["-vf", f"crop={w}:{h}:{x}:{y}"]
    cmd += ["-c:v", "png", "-f", "image2", "-update", "1", "-y", f"file:{dst}"]
    _run(cmd, name)
    strip_png(dst)


# The only PNG chunks a copy keeps: what it takes to draw the pixels. Newer ffmpeg builds carry
# EXIF (eXIf) and other frame data through -map_metadata -1, so the copy is cleaned here, whatever
# the ffmpeg version.
_KEEP = {b"IHDR", b"PLTE", b"tRNS", b"IDAT", b"IEND", b"gAMA", b"sRGB", b"cHRM", b"sBIT"}


def strip_png(path: Path) -> None:
    data = path.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise _unreadable(path.name, "the copy is not a PNG")
    out, i = [data[:8]], 8
    while i + 12 <= len(data):
        n = struct.unpack(">I", data[i:i + 4])[0]
        typ = data[i + 4:i + 8]
        if typ in _KEEP:
            out.append(data[i:i + 12 + n])
        i += 12 + n
        if typ == b"IEND":
            break
    path.write_bytes(b"".join(out))


def tiles(width: int, height: int) -> list[tuple[int, int]]:
    """(y, height) of each tile of a tall image; one tile when it is not tall."""
    if height <= TILE_RATIO * width:
        return [(0, height)]
    th = int(TILE_HEIGHT * width)
    step = max(1, int(th * (1 - TILE_OVERLAP)))
    n = math.ceil((height - th) / step) + 1
    return [(min(k * step, height - th), th) for k in range(n)]


def copy_all(entries: list[Entry], dest: Path, max_images: int = MAX_IMAGES) -> list[dict]:
    """Re-encode each image (and split tall ones) into `dest`. Returns one
    record per copy, in manifest order."""
    if dest.exists():  # a --replace import never keeps copies from the one before
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    out: list[dict] = []
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for i, e in enumerate(entries, start=1):
            if hashlib.sha256(e.path.read_bytes()).hexdigest() != e.sha256:
                raise _unreadable(e.name, "the file changed after it was listed")
            src, kind, w, h = e.path, e.kind, e.width, e.height
            if kind == "heic":
                src, kind = _heic_to_png(e.path, tmp), "png"
                w, h = dimensions(src.read_bytes()[:32], "png") or (None, None)
                if w and h and w * h > MAX_PIXELS:  # HEIC has no size in its header: check after conversion
                    raise _unreadable(e.name, f"{w}x{h} pixels, over the {MAX_PIXELS} pixel limit")
            if not w or not h:
                raise _unreadable(e.name, "the size cannot be read from the header")
            parts = tiles(w, h)
            if len(out) + len(parts) > max_images:
                raise ChatcomError("CHATCOM_E_TOO_MANY_IMAGES",
                                   f"the images split into more than {max_images} tiles (tall screenshots count "
                                   "once per tile)", f"Import fewer screenshots, or pass --max-images N.",
                                   context={"limit": max_images})
            for k, (y, th) in enumerate(parts, start=1):
                fname = f"{i:03d}.png" if len(parts) == 1 else f"{i:03d}-{k}.png"
                _encode(src, kind, dest / fname, e.name, None if len(parts) == 1 else (w, th, 0, y))
                out.append({"file": fname, "image": e.name, "tile": k, "tiles": len(parts),
                            "captured": e.captured.isoformat(timespec="seconds"), "captured_from": e.captured_from,
                            "sha256": hashlib.sha256((dest / fname).read_bytes()).hexdigest()})
    return out
