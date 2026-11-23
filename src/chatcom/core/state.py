"""The episode state machine. Each stage resumes from the last completed one;
the bible and cast sheet change only at `committed`.

    ingested -> drafted -> cast -> rendered -> encoded -> committed

Step 2 of the build stops at `drafted` (beats.json and bible.next.json are
written, the bible is not). Casting, render and encode arrive in Next Steps 3-4.
"""
from __future__ import annotations

from chatcom.core.errors import ChatcomError

STAGES = ("ingested", "drafted", "cast", "rendered", "encoded", "committed")
MAX_WRITER_ATTEMPTS = 3


def new(code: str, window_entry: dict, mode: str, now: str) -> dict:
    return {"episode": code, "stage": "ingested", "writer_mode": mode, "attempts": 0,
            "last_errors": None, "writer_model": None, "window": window_entry,
            "created": now, "updated": now}


def require(state: dict, *stages: str) -> None:
    if state["stage"] not in stages:
        raise ChatcomError("CHATCOM_E_STATE", f"{state['episode']} is at stage '{state['stage']}', expected {list(stages)}",
                           "Run the steps in order: ingest --confirmed, write-prep, write-submit.",
                           context={"stage": state["stage"]})


def advance(state: dict, stage: str, now: str) -> dict:
    if STAGES.index(stage) != STAGES.index(state["stage"]) + 1:
        raise ChatcomError("CHATCOM_E_STATE", f"cannot go from '{state['stage']}' to '{stage}'",
                           "Run the steps in order.", context={"stage": state["stage"]})
    return {**state, "stage": stage, "updated": now}


def check_attempts_left(state: dict, draft_path: str | None = None) -> None:
    if state["attempts"] >= MAX_WRITER_ATTEMPTS:
        raise ChatcomError("CHATCOM_E_WRITER_GAVE_UP",
                           f"the writer failed validation {state['attempts']} times for {state['episode']}",
                           "Writer step did not complete. Nothing was saved. Rerun the skill, or run "
                           "`chatcom episode <show> --writer anthropic`.",
                           recovery="ask", context={"last_draft": draft_path, "errors": state["last_errors"]})


def record_attempt(state: dict, *, ok: bool, errors: list[dict] | None, model: str | None, now: str) -> dict:
    return {**state, "attempts": state["attempts"] + 1, "last_errors": None if ok else errors,
            "writer_model": model or state["writer_model"], "updated": now}
