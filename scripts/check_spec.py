"""Fail when docs/design/any-group-chat.md and the code disagree.

Checks, for the error-code and flag tables between the check_spec markers:
- every code and every flag appears once (one meaning only);
- every code the code defines is in the spec, with the same exit status;
- every code and flag the spec says is built (PR "shipped" or <= BUILT_PR)
  exists in the code (CODES, docs/errors.md, the argparse commands named).

Run: python scripts/check_spec.py   (exit 0 clean, 1 with a list of problems)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "docs" / "design" / "any-group-chat.md"
BUILT_PR = 3  # raise as each wave-1 PR lands

sys.path.insert(0, str(ROOT / "src"))


def _table(text: str, name: str) -> list[list[str]]:
    m = re.search(rf"<!-- check_spec:{name} -->\n(.*?)<!-- /check_spec:{name} -->", text, re.S)
    if not m:
        raise SystemExit(f"spec has no {name} table")
    rows = []
    for ln in m.group(1).strip().splitlines()[2:]:  # skip header and rule
        rows.append([c.strip().strip("`") for c in ln.strip().strip("|").split("|")])
    return rows


def _built(pr: str) -> bool:
    return pr == "shipped" or (pr.isdigit() and int(pr) <= BUILT_PR)


def problems() -> list[str]:
    from chatcom.cli import build_parser
    from chatcom.core.errors import CODES

    text = SPEC.read_text("utf-8")
    errors_doc = (ROOT / "docs" / "errors.md").read_text("utf-8")
    out: list[str] = []

    codes = _table(text, "codes")
    seen: dict[str, str] = {}
    for code, exit_s, pr in codes:
        if code in seen:
            out.append(f"{code} is in the spec twice")
        seen[code] = exit_s
        if _built(pr):
            if code not in CODES:
                out.append(f"{code} is built per the spec (PR {pr}) but not in core/errors.py")
            elif str(CODES[code]) != exit_s:
                out.append(f"{code} exits {CODES[code]} in code but {exit_s} in the spec")
            if f"## {code.lower()}" not in errors_doc:
                out.append(f"{code} has no anchor in docs/errors.md")
    for code in CODES:
        if code not in seen:
            out.append(f"{code} is in core/errors.py but not in the spec")

    parser = build_parser()
    sub = next(a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction")
    flag_seen: set[str] = set()
    for flag, commands, _meaning, pr in _table(text, "flags"):
        if flag in flag_seen:
            out.append(f"{flag} is in the spec twice")
        flag_seen.add(flag)
        if not _built(pr):
            continue
        for cmd in (c.strip() for c in commands.split(",")):
            sp = sub.choices.get(cmd)
            if sp is None:
                out.append(f"{flag}: no command '{cmd}'")
            elif flag not in sp._option_string_actions:
                out.append(f"{flag} is built per the spec (PR {pr}) but `chatcom {cmd}` has no such flag")
    return out


def main() -> int:
    found = problems()
    for p in found:
        print(f"spec: {p}")
    if not found:
        print("spec and code agree")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
