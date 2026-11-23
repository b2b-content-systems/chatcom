"""Load the shipped JSON Schemas and validate documents against them.
Errors name the JSON path, the bad value and what was allowed."""
from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources

from jsonschema import Draft202012Validator, FormatChecker

from chatcom.core.errors import ChatcomError

NAMES = ("message", "bible", "beats", "setup", "cast", "read")


@lru_cache(maxsize=None)
def schema(name: str) -> dict:
    if name not in NAMES:
        raise KeyError(name)
    text = resources.files("chatcom").joinpath("schemas", f"{name}.schema.json").read_text("utf-8")
    return json.loads(text)


@lru_cache(maxsize=None)
def _validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(schema(name), format_checker=FormatChecker())


def _path(err) -> str:
    out = "$"
    for p in err.absolute_path:
        out += f"[{p}]" if isinstance(p, int) else f".{p}"
    return out


def problems(name: str, doc) -> list[dict]:
    """Every schema violation as {path, message}, sorted by path."""
    out = []
    for e in _validator(name).iter_errors(doc):
        bad = json.dumps(e.instance, ensure_ascii=False)
        if len(bad) > 80:
            bad = bad[:77] + "..."
        out.append({"path": _path(e), "message": e.message if len(e.message) < 300 else e.message[:297] + "...", "value": bad})
    return sorted(out, key=lambda p: p["path"])


def check(name: str, doc, *, code: str, what: str, fix: str) -> None:
    errs = problems(name, doc)
    if errs:
        first = errs[0]
        raise ChatcomError(code, f"{what}: {first['path']} {first['message']} ({len(errs)} problem(s))",
                           fix, context={"problems": errs[:20]})
