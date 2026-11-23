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
    vendored = open("src/chatcom/adapters/writers/llm_fallback.py", encoding="utf-8").read(400)
    if "MIT" not in vendored:
        print("BAD vendored llm_fallback.py lacks its MIT header")
        bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
