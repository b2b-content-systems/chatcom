"""The show store on disk: CHATCOM_HOME (default ~/.chatcom, mode 0700), one
folder per show, atomic writes (temp file, fsync, os.replace), one lock per
show with stale-lock detection, redacted run logs, bible snapshots."""
from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from chatcom.core.errors import ChatcomError, redact

EMPTY_LOCK_GRACE_S = 10.0
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def home() -> Path:
    h = Path(os.environ.get("CHATCOM_HOME") or "~/.chatcom").expanduser()
    h.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(h, 0o700)
    except OSError:
        pass
    return h


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    return (s or "show")[:48].strip("-") or "show"


def write_atomic(path: Path, data: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = data.encode("utf-8") if isinstance(data, str) else data
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_json(path: Path, doc) -> None:
    write_atomic(path, json.dumps(doc, ensure_ascii=False, indent=2) + "\n")


def write_jsonl(path: Path, rows) -> None:
    write_atomic(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def read_json(path: Path):
    return json.loads(path.read_text("utf-8"))


def read_jsonl(path: Path) -> list:
    return [json.loads(ln) for ln in path.read_text("utf-8").splitlines() if ln.strip()]


class Show:
    def __init__(self, slug: str, root: Path | None = None):
        self.slug = slug
        self.dir = (root or home()) / slug

    # paths
    @property
    def bible_path(self) -> Path: return self.dir / "bible.json"
    @property
    def cast_path(self) -> Path: return self.dir / "cast.json"
    @property
    def setup_path(self) -> Path: return self.dir / "setup.json"

    def episode_dir(self, code: str) -> Path:
        return self.dir / "episodes" / code

    def state_path(self, code: str) -> Path:
        return self.episode_dir(code) / "state.json"

    def exists(self) -> bool:
        return self.bible_path.is_file()

    def create_dirs(self) -> None:
        for sub in ("", "episodes", "logs", "snapshots"):
            (self.dir / sub).mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.dir, 0o700)
        except OSError:
            pass

    @contextmanager
    def lock(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / ".lock"
        for _ in range(2):
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                break
            except FileExistsError:
                try:
                    pid = int(path.read_text().strip() or "0")
                    age = time.time() - path.stat().st_mtime
                except (OSError, ValueError):
                    pid, age = 0, 0.0
                # An empty lock is one another run just created and has not
                # written its pid into yet: held, unless it is old.
                held = _alive(pid) if pid else age < EMPTY_LOCK_GRACE_S
                if held:
                    raise ChatcomError("CHATCOM_E_LOCKED", f"show '{self.slug}' is in use by process {pid or 'starting up'}",
                                       "Wait for the other chatcom run to finish.", recovery="retry",
                                       context={"pid": pid}) from None
                path.unlink(missing_ok=True)  # stale: its process is gone
        else:
            raise ChatcomError("CHATCOM_E_LOCKED", f"could not lock show '{self.slug}'", "Retry.", recovery="retry")
        try:
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            yield
        finally:
            path.unlink(missing_ok=True)

    def snapshot_bible(self, code: str) -> Path:
        dst = self.dir / "snapshots" / f"bible.{code}.pre.json"
        if self.bible_path.is_file() and not dst.exists():
            write_atomic(dst, self.bible_path.read_bytes())
        return dst


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def list_shows(root: Path | None = None) -> list[str]:
    r = root or home()
    return sorted(p.name for p in r.iterdir() if (p / "bible.json").is_file()) if r.exists() else []


class RunLog:
    """One redacted log file per run: <show>/logs/<run-id>.log."""

    def __init__(self, show: Show | None, command: str):
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)
        base = show.dir / "logs" if show else home() / "logs"
        base.mkdir(parents=True, exist_ok=True)
        self.path = base / f"{self.run_id}.log"
        self.write(f"command: {command}")

    def write(self, line: str) -> None:
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(f"{now()} {redact(line)}\n")
