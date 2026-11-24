"""The render runtime: the pinned HyperFrames, its Chrome and the large fallback
fonts, installed once into CHATCOM_HOME/runtime/<version>/.

    runtime/0.9.86/
        package.json, package-lock.json   copied from the package (pinned exactly)
        node_modules/.bin/hyperframes     `npm ci`
        cache/chrome/...                  `hyperframes browser ensure` (HYPERFRAMES_CACHE_DIR)
        fonts/NotoColorEmoji-Regular.ttf  downloaded, sha256-checked
        fonts/NotoSansSC[wght].ttf, NotoSansKR[wght].ttf, OFL-*.txt
        installed.json                    written last: the install is complete

`install()` is the only step that uses the network. A render reads only what
is here; `require()` raises a coded preflight error naming the install command
when anything is missing."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.request
from importlib import resources
from pathlib import Path

from chatcom.adapters import store, tools
from chatcom.core.errors import ChatcomError

VERSION = "0.9.86"   # HyperFrames; the shipped package.json pins the same (a test checks)
NODE_MAJOR = 22      # HyperFrames' engines field
INSTALL_COMMAND = "chatcom doctor --install"
_FONTS_AT = "https://raw.githubusercontent.com/google/fonts/06bfe0d62a98375cb09c942fa18e68bd32cff057/ofl/"

# name in fonts/ -> (path under _FONTS_AT, sha256). OFL 1.1, each with its OFL text.
BIG_FONTS = {
    "NotoColorEmoji-Regular.ttf": ("notocoloremoji/NotoColorEmoji-Regular.ttf",
                                   "4d82a18d8d95f60ba883ce242bbadbf84a576e987745dd7ba38d71c67cff2d73"),
    "NotoSansSC[wght].ttf": ("notosanssc/NotoSansSC%5Bwght%5D.ttf",
                             "a3041811a78c361b1de50f953c805e0244951c21c5bd412f7232ef0d899af0da"),
    "NotoSansKR[wght].ttf": ("notosanskr/NotoSansKR%5Bwght%5D.ttf",
                             "194018e6b2b293a7964f037b25c0249ce1418bc9ab3c971060a03aa57861e252"),
    "OFL-notocoloremoji.txt": ("notocoloremoji/OFL.txt",
                               "ac564676d10054a8445923dfc2dfb13c042d97888bd27c1b6ec6dfe89a9d8d62"),
    "OFL-notosanscjk.txt": ("notosanssc/OFL.txt",
                            "1c05c68c34f9708415aada51f17e1b0092d2cea709bf4a94cd38114f9e73d7d9"),
}
DOWNLOAD_SECONDS = 300


def root(version: str = VERSION) -> Path:
    return store.home() / "runtime" / version


_SECRET_NAME = re.compile(r"(API_?KEY|TOKEN|SECRET|PASSWORD|CREDENTIALS?)$|^(AWS|GOOGLE_APPLICATION)_", re.I)


def env(version: str = VERSION) -> dict:
    """The environment every HyperFrames and npm child runs with: no telemetry,
    its Chrome cache inside the runtime folder, and no key or token variable
    (npm runs third-party install scripts; the render needs no key)."""
    e = {k: v for k, v in os.environ.items() if not _SECRET_NAME.search(k)}
    e["HYPERFRAMES_NO_TELEMETRY"] = "1"
    e["HYPERFRAMES_CACHE_DIR"] = str(root(version) / "cache")
    return e


def binary(version: str = VERSION) -> Path:
    return root(version) / "node_modules" / ".bin" / "hyperframes"


def fonts_dir(version: str = VERSION) -> Path:
    return root(version) / "fonts"


def _missing(what: str) -> ChatcomError:
    return ChatcomError("CHATCOM_E_PREFLIGHT", f"the render runtime is not installed: {what}",
                        f"Run `{INSTALL_COMMAND}` once (it downloads HyperFrames {VERSION}, its Chrome and the "
                        "emoji and CJK fonts, about 400 MB), then render again.",
                        context={"runtime": str(root())})


def require(version: str = VERSION) -> Path:
    """The HyperFrames binary, after checking the install finished."""
    node_version()
    marker = root(version) / "installed.json"
    if not marker.is_file():
        raise _missing(f"{marker} is missing")
    if not binary(version).is_file():
        raise _missing(f"{binary(version)} is missing")
    for name in BIG_FONTS:
        if not (fonts_dir(version) / name).is_file():
            raise _missing(f"font {name} is missing")
    return binary(version)


def node_version() -> int:
    exe = tools.node()
    r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30)
    try:
        major = int(r.stdout.strip().lstrip("v").split(".")[0])
    except ValueError:
        major = 0
    if major < NODE_MAJOR:
        raise ChatcomError("CHATCOM_E_PREFLIGHT", f"node {r.stdout.strip() or '?'} is too old; HyperFrames "
                           f"{VERSION} needs {NODE_MAJOR} or newer",
                           f"Install Node.js {NODE_MAJOR} or newer (https://nodejs.org, or `brew install node`).",
                           context={"node": r.stdout.strip()})
    return major


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _open(url: str, timeout: float):
    from chatcom.adapters.writers.llm_fallback import _ssl_context  # certifi's CA list (python.org macOS builds)
    return urllib.request.urlopen(url, timeout=timeout, context=_ssl_context())


def fetch_font(name: str, dest: Path, opener=_open) -> None:
    """Download one font file and check its sha256 before it is kept."""
    path, want = BIG_FONTS[name]
    if dest.is_file() and _sha256(dest) == want:
        return
    tmp = dest.with_name(dest.name + ".part")
    try:
        with opener(_FONTS_AT + path, timeout=DOWNLOAD_SECONDS) as resp, tmp.open("wb") as fh:
            shutil.copyfileobj(resp, fh)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        raise ChatcomError("CHATCOM_E_PREFLIGHT", f"could not download font {name}: {e}",
                           f"Check the network and run `{INSTALL_COMMAND}` again.", recovery="retry",
                           context={"font": name}) from None
    got = _sha256(tmp)
    if got != want:
        tmp.unlink(missing_ok=True)
        raise ChatcomError("CHATCOM_E_PREFLIGHT", f"font {name} did not match its fingerprint "
                           f"(sha256 {got[:12]}..., expected {want[:12]}...)",
                           f"Run `{INSTALL_COMMAND}` again; if it fails again, report it: the pinned file changed "
                           "at its source.", context={"font": name, "sha256": got})
    os.replace(tmp, dest)


def _run(cmd: list[str], cwd: Path, what: str, log: Path) -> None:
    # npm reads no user ~/.npmrc: the lockfile pins every URL and hash, and a user config
    # that refers to a token stripped from env() (`${NPM_TOKEN}`) would abort npm
    empty_rc = cwd / ".npmrc-empty"
    empty_rc.write_text("", "utf-8")
    child = {**env(), "npm_config_userconfig": str(empty_rc)}
    with log.open("a", encoding="utf-8") as fh:
        fh.write(f"$ {' '.join(cmd)}\n")
        fh.flush()
        r = subprocess.run(cmd, cwd=cwd, env=child, stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        raise ChatcomError("CHATCOM_E_PREFLIGHT", f"{what} failed (exit {r.returncode})",
                           f"Read {log}, fix the cause (often the network or disk space), then run "
                           f"`{INSTALL_COMMAND}` again.", recovery="retry", side_effects="partial",
                           context={"log": str(log)}, trace_path=str(log))


def install(say=lambda m: None, version: str = VERSION) -> Path:
    """Install the runtime. Safe to run again: finished parts are kept."""
    node_version()
    npm = tools.find("npm", "the render runtime installs HyperFrames with it")
    rt = root(version)
    rt.mkdir(parents=True, exist_ok=True)
    (rt / "installed.json").unlink(missing_ok=True)
    log = rt / "install.log"
    shipped = resources.files("chatcom") / "runtime"
    for name in ("package.json", "package-lock.json"):
        store.write_atomic(rt / name, (shipped / name).read_bytes())
    say(f"installing HyperFrames {version} into {rt}")
    _run([npm, "ci", "--no-audit", "--no-fund"], rt, "npm ci", log)
    say("installing the pinned Chrome")
    _run([str(binary(version)), "browser", "ensure"], rt, "hyperframes browser ensure", log)
    fd = fonts_dir(version)
    fd.mkdir(exist_ok=True)
    for name in BIG_FONTS:
        say(f"downloading {name}")
        fetch_font(name, fd / name)
    store.write_json(rt / "installed.json", {"hyperframes": version, "installed": store.now()})
    return rt
