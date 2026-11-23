"""Fit a week of chat into the writer's input budget. Messages are ranked by
what makes a scene (reply bursts, lines the group laughed at, length, who has
not been heard from yet) and sampled to the token budget; the sample keeps
chronological order."""
from __future__ import annotations

import re
from datetime import datetime, timedelta

DEFAULT_TOKEN_BUDGET = 12_000
_LAUGH = re.compile(r"^(l+o+l+|lmf?ao+|ha(ha)+h?|hehe+|dead|crying|i'?m dead|jaja+|kkk+|mdr|😂+|🤣+|💀+|😭+)[!. ]*$", re.I)
_BURST = timedelta(minutes=2)
_REACT = timedelta(minutes=3)


def _t(m: dict) -> datetime:
    return datetime.fromisoformat(m["time"])


def compact(m: dict) -> dict:
    """The writer's view of one message: no source line, no nulls."""
    out = {"id": m["id"], "t": m["time"][:16].replace("T", " "), "from": m["sender"], "text": m["text"]}
    if m["media"]:
        out["media"] = m["media"]
    if m["deleted"]:
        out["deleted"] = True
    return out


def estimate_tokens(m: dict) -> int:
    return 12 + (len(m["text"]) + len(m["sender"])) // 4


def score(messages: list[dict]) -> list[float]:
    scores = []
    for i, m in enumerate(messages):
        s = 1.0
        if m["deleted"]:
            s = 0.1
        elif m["media"] and not m["text"]:
            s = 0.3
        elif _LAUGH.match(m["text"].strip()):
            s = 0.4
        else:
            s += min(len(m["text"]) / 80, 1.5)
        if i and messages[i - 1]["sender"] != m["sender"] and _t(m) - _t(messages[i - 1]) <= _BURST:
            s += 0.5
        laughs = 0
        for nxt in messages[i + 1:i + 8]:
            if _t(nxt) - _t(m) > _REACT:
                break
            if nxt["sender"] != m["sender"] and _LAUGH.match(nxt["text"].strip()):
                laughs += 1
        s += min(laughs, 3)
        scores.append(s)
    return scores


def sample(messages: list[dict], token_budget: int = DEFAULT_TOKEN_BUDGET) -> tuple[list[dict], dict]:
    """(sampled messages in time order, stats)."""
    total = sum(estimate_tokens(m) for m in messages)
    if total <= token_budget:
        return list(messages), {"kept": len(messages), "of": len(messages), "tokens": total, "sampled": False}
    scores = score(messages)
    order = sorted(range(len(messages)), key=lambda i: -scores[i])
    chosen: set[int] = set()
    used = 0
    # Variety first: every sender's best line, best senders first.
    best_by_sender: dict[str, int] = {}
    for i in order:
        best_by_sender.setdefault(messages[i]["sender"], i)
    for i in best_by_sender.values():
        cost = estimate_tokens(messages[i])
        if used + cost <= token_budget:
            chosen.add(i)
            used += cost
    for i in order:
        if i in chosen:
            continue
        cost = estimate_tokens(messages[i])
        if used + cost > token_budget:
            continue
        chosen.add(i)
        used += cost
    kept = [messages[i] for i in sorted(chosen)]
    return kept, {"kept": len(kept), "of": len(messages), "tokens": used, "sampled": True}
