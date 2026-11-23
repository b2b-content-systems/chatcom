"""The reader eval: how close a screenshot reader gets to a hand-checked
transcript of the same screenshots (docs/reader-eval.md).

A case is a folder:

    images/      the screenshots
    gold.txt     the true chat, as a transcript (docs/transcript-format.md)
    reads.json   recorded reads (only for the replay reader)
    case.json    optional: {"operator", "speakers", "reverse", "anchor"} as the import would get them

For each case the reader reads the images, chatcom joins them with its fixed
rules, and the result is compared with gold.txt, message by message:

    recall     gold messages found (same sender, same identity text)
    precision  messages found that are in gold
    sender     of the messages whose text matches, the share with the right sender
    order      of the paired messages, the longest run in gold's order / all pairs
    dates      of matched dated messages, the share with the right day
    phones     found messages that still hold a phone-length run of digits (must be 0)

No result is stored in the repository: real screenshots and their scores stay
on the machine that ran them."""
from __future__ import annotations

import json
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from chatcom.adapters import images as image_ops, transcript
from chatcom.core import screens
from chatcom.core.messages import identity_text


@dataclass
class Score:
    case: str
    gold: int
    found: int
    recall: float
    precision: float
    sender: float
    order: float
    dates: float | None
    phones: int
    reader: str
    error: str | None = None


def _key(m: dict) -> tuple:
    return (identity_text(m["text"]), m["media"], m["deleted"])


def _in_order(seq: list[int]) -> int:
    """Length of the longest increasing subsequence."""
    import bisect
    tails: list[int] = []
    for x in seq:
        i = bisect.bisect_left(tails, x)
        tails[i:i + 1] = [x]
    return len(tails)


def compare(found: list[dict], gold: list[dict]) -> dict:
    """Message-level scores of `found` against `gold` (both parsed transcripts).
    Messages are paired by text (identity text, media, deleted), whatever their
    order; order is then scored on its own."""
    free: dict[tuple, list[int]] = {}
    for gi, g in enumerate(gold):
        free.setdefault(_key(g), []).append(gi)
    pairs: list[tuple[dict, dict, int]] = []
    for f in found:
        slots = free.get(_key(f))
        if slots:
            # the same text can come from several people ("lol", a photo): prefer the same sender
            pick = next((gi for gi in slots if gold[gi]["sender"] == f["sender"]), slots[0])
            slots.remove(pick)
            pairs.append((f, gold[pick], pick))
    right = sum(f["sender"] == g["sender"] for f, g, _ in pairs)
    dated = [(f, g) for f, g, _ in pairs if g["time_precision"] in ("exact", "minute", "day")]
    right_day = sum(datetime.fromisoformat(f["time"]).date() == datetime.fromisoformat(g["time"]).date()
                    for f, g in dated)
    return {"gold": len(gold), "found": len(found),
            "recall": right / len(gold) if gold else 1.0,
            "precision": right / len(found) if found else 1.0,
            "sender": right / len(pairs) if pairs else 0.0,
            "order": _in_order([gi for *_, gi in pairs]) / len(pairs) if pairs else 0.0,
            "dates": right_day / len(dated) if dated else None}


def phones(found: list[dict]) -> int:
    """Found messages with a run of 9 or more digits: a number that was not masked."""
    import re
    run = re.compile(r"\d(?:[\d ()\-.]*\d){8,}")
    return sum(bool(run.search(m["text"])) or bool(run.search(m["sender"])) for m in found)


def run_case(case: Path, reader, *, tz: str, operator: str, cache_dir: Path | None) -> Score:
    """Read the case's screenshots through the shipped pipeline (copies, reads with the split for
    long replies, the join, the transcript), then score it against gold.txt."""
    from chatcom.adapters.screenshots import read_copies
    from chatcom.core.masking import mask_numbers
    opts = json.loads((case / "case.json").read_text("utf-8")) if (case / "case.json").exists() else {}
    with tempfile.TemporaryDirectory() as td:
        folder = Path(td) / "images"
        copies = image_ops.copy_all(image_ops.manifest([case / "images"]), folder)
        reads = read_copies(copies, folder, reader, cache_dir)
        anchor = datetime.fromisoformat(opts["anchor"]).date() if opts.get("anchor") else None
        joined = screens.join(reads, tz=ZoneInfo(tz), operator=opts.get("operator", operator),
                              speakers=opts.get("speakers"), reverse=opts.get("reverse"), anchor=anchor)
        msgs = [{"sender": m["label"], "time": m["time"], "time_precision": m["time_precision"], "text": m["text"],
                 "media": m["media"], "deleted": m["deleted"], "reactions": m["reactions"], "quote": m["quote"],
                 "source": {"label": m["label"]}} for m in joined.messages]
        found = transcript.parse(transcript.write(msgs, tz, source="screenshots"), tz).messages
    gold = transcript.parse((case / "gold.txt").read_text("utf-8"), tz).messages
    # gold is written by a person with real numbers in it; compare masked to masked
    for g in gold:
        g["text"] = mask_numbers(g["text"], {}, None)
    s = compare(found, gold)
    return Score(case.name, s["gold"], s["found"], round(s["recall"], 3), round(s["precision"], 3),
                 round(s["sender"], 3), round(s["order"], 3), None if s["dates"] is None else round(s["dates"], 3),
                 phones(found), _answered(reader))


def failed(case: Path, err: Exception) -> Score:
    what = getattr(err, "code", type(err).__name__)
    return Score(case.name, 0, 0, 0.0, 0.0, 0.0, 0.0, None, 0, "-", error=f"{what}: {str(err)[:160]}")


def _answered(reader) -> str:
    """The models that really answered (a fallback may have read everything)."""
    by = getattr(reader, "answered_by", None)
    if by:
        return ", ".join(f"{m} x{n}" for m, n in by.most_common())
    return getattr(reader, "model", "?")


def table(scores: list[Score]) -> str:
    head = ("| case | gold | found | recall | precision | sender | order | dates | phones | reader |\n"
            "|---|---|---|---|---|---|---|---|---|---|")
    rows = [f"| {s.case} | error | | | | | | | | {s.error} |" if s.error else
            f"| {s.case} | {s.gold} | {s.found} | {s.recall:.3f} | {s.precision:.3f} | {s.sender:.3f} | "
            f"{s.order:.3f} | {'-' if s.dates is None else f'{s.dates:.3f}'} | {s.phones} | {s.reader} |"
            for s in scores]
    return "\n".join([head, *rows])


def to_json(scores: list[Score]) -> str:
    return json.dumps([asdict(s) for s in scores], indent=2)
