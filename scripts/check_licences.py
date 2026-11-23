"""CI licence check: every runtime dependency (and its dependencies) must be
under a licence an MIT package can ship with. GPL and AGPL fail the build."""
from __future__ import annotations

import re
import sys
from importlib.metadata import PackageNotFoundError, distribution

ALLOWED = re.compile(r"\b(MIT|BSD|Apache|ISC|PSF|Python Software Foundation|MPL|Unlicense)\b", re.I)
BANNED = re.compile(r"\b(A?GPL|GNU General Public)\b", re.I)


def licence(dist) -> str:
    md = dist.metadata
    parts = [md.get("License-Expression") or "", md.get("License") or ""]
    parts += [c for c in md.get_all("Classifier") or [] if c.startswith("License ::")]
    return " | ".join(p for p in parts if p)[:200]


def walk(name: str, seen: dict) -> None:
    key = name.lower().replace("_", "-")
    if key in seen:
        return
    try:
        dist = distribution(name)
    except PackageNotFoundError:
        # A dependency gated by an environment marker (for example
        # python_version < "3.13") is not installed here and not shipped here.
        seen[key] = None
        return
    seen[key] = licence(dist)
    for req in dist.requires or []:
        if "extra ==" in req:
            continue
        walk(re.split(r"[ ;<>=!~\[(]", req, maxsplit=1)[0], seen)


ASSET_LICENCES = {"OFL-1.1", "CC0-1.0"}


def check_assets(root: str = "src/chatcom/assets") -> int:
    """Every shipped asset is in assets/manifest.json, unchanged, under an
    allowed licence, with its licence text shipped beside it."""
    import hashlib
    import json
    from pathlib import Path
    base = Path(root)
    rows = {r["path"]: r for r in json.loads((base / "manifest.json").read_text("utf-8"))["files"]}
    bad = 0
    for f in sorted(p for p in base.rglob("*") if p.is_file() and p.name != "manifest.json"):
        rel = f.relative_to(base).as_posix()
        if f.name.startswith("OFL-") and f.suffix == ".txt":
            continue
        r = rows.get(rel)
        problem = ("not in assets/manifest.json" if r is None
                   else f"licence {r['licence']} is not one of {sorted(ASSET_LICENCES)}" if r["licence"] not in ASSET_LICENCES
                   else "changed since the manifest was written" if hashlib.sha256(f.read_bytes()).hexdigest() != r["sha256"]
                   else f"its licence text {r['licence_file']} is missing" if r["licence"] == "OFL-1.1" and not (base / r["licence_file"]).is_file()
                   else None)
        print(f"{'BAD' if problem else 'ok '} asset {rel}: {problem or r['licence']}")
        bad += problem is not None
    for rel in rows:
        if not (base / rel).is_file():
            print(f"BAD asset {rel}: in the manifest but not shipped")
            bad += 1
    return bad


def main() -> int:
    seen: dict[str, str | None] = {}
    walk("chatcom", seen)
    bad = 0
    for name, lic in sorted(seen.items()):
        if lic is None:
            print(f"--  {name}: not installed for this Python (marker-gated); checked where it installs")
            continue
        ok = name == "chatcom" or (ALLOWED.search(lic) and not BANNED.search(lic))
        bad += not ok
        print(f"{'ok ' if ok else 'BAD'} {name}: {lic or 'unknown'}")
    bad += check_assets()
    vendored = open("src/chatcom/adapters/writers/llm_fallback.py", encoding="utf-8").read(400)
    if "MIT" not in vendored:
        print("BAD vendored llm_fallback.py lacks its MIT header")
        bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
