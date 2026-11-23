"""The episode state machine. Each stage resumes from the last completed one;
the bible and cast sheet change only at `committed`.

    ingested -> drafted -> cast -> rendered -> encoded -> committed

Screenshot imports add `copied -> read` before `ingested` (docs/design/any-group-chat.md).
This release stops at `rendered` (beats.json, bible.next.json, cast.next.json
and render/master.mp4 are written; the bible and cast sheet are not). Encode
and commit arrive next. `chatcom recast` moves `cast` back to `drafted`;
`chatcom rewrite` moves a drafted, cast or rendered episode back to `ingested`.

Attempts: 3 submits per prep call, at most 3 prep cycles per episode. The
data flow the operator accepted is recorded; a command with another flow
needs a new acceptance."""
from __future__ import annotations

from chatcom.core.errors import ChatcomError

STAGES = ("copied", "read", "ingested", "drafted", "cast", "rendered", "encoded", "committed")
IMPORTING = ("copied", "read")
MAX_WRITER_ATTEMPTS = 3
MAX_PREP_CYCLES = 3


def new(code: str, window_entry: dict | None, mode: str, now: str, *, stage: str = "ingested",
        reader: str | None = None) -> dict:
    if stage not in STAGES:
        raise ValueError(stage)
    return {"episode": code, "stage": stage, "writer_mode": mode, "attempts": 0, "prep_cycles": 0,
            "accepted_flow": {"writer": mode, "reader": reader},
            "last_errors": None, "writer_model": None, "window": window_entry,
            "created": now, "updated": now}


def require(state: dict, *stages: str) -> None:
    if state["stage"] not in stages:
        raise ChatcomError("CHATCOM_E_STATE", f"{state['episode']} is at stage '{state['stage']}', expected {list(stages)}",
                           "Run the steps in order: ingest --accept-data-flow, write-prep, write-submit. "
                           "`chatcom status` prints the next command.",
                           context={"stage": state["stage"]})


def require_flow(state: dict, writer: str) -> None:
    """The writer mode must be the one the operator accepted at ingest."""
    accepted = (state.get("accepted_flow") or {}).get("writer", state.get("writer_mode"))
    if accepted != writer:
        raise ChatcomError("CHATCOM_E_AWAITING_CONFIRMATION",
                           f"the operator accepted the '{accepted}' data flow for {state['episode']}, not '{writer}'",
                           "Ask the operator. If they agree to the new flow, ingest again with that --writer and "
                           "--accept-data-flow.",
                           recovery="ask", context={"accepted": accepted, "requested": writer})


def advance(state: dict, stage: str, now: str) -> dict:
    if STAGES.index(stage) != STAGES.index(state["stage"]) + 1:
        raise ChatcomError("CHATCOM_E_STATE", f"cannot go from '{state['stage']}' to '{stage}'",
                           "Run the steps in order.", context={"stage": state["stage"]})
    return {**state, "stage": stage, "updated": now}


def reopen_cast(state: dict, now: str) -> dict:
    """`chatcom recast`: the episode goes back to `drafted` until it is cast again."""
    if state["stage"] not in ("drafted", "cast"):
        raise ChatcomError("CHATCOM_E_STATE", f"{state['episode']} is at stage '{state['stage']}'; recast works "
                           "after the draft and before the render", "Recast with the next episode's draft.",
                           context={"stage": state["stage"]})
    return {**state, "stage": "drafted", "updated": now}


def reopen_draft(state: dict, now: str) -> dict:
    """`chatcom rewrite`: the episode goes back to `ingested` with a fresh writer
    budget. Everything else in the state (data flow, window, paid-call counts) stays."""
    if state["stage"] not in ("drafted", "cast", "rendered"):
        raise ChatcomError("CHATCOM_E_STATE", f"{state['episode']} is at stage '{state['stage']}'; rewrite works "
                           "after the draft and before the encode", "`chatcom status` prints the next command.",
                           context={"stage": state["stage"]})
    return {**state, "stage": "ingested", "attempts": 0, "prep_cycles": 0, "last_errors": None,
            "writer_model": None, "updated": now}


def gave_up(state: dict, draft_path: str | None) -> ChatcomError:
    return ChatcomError("CHATCOM_E_WRITER_GAVE_UP",
                        f"the writer failed validation {state['attempts']} times in prep cycle "
                        f"{state.get('prep_cycles', 1)} of {MAX_PREP_CYCLES} for {state['episode']}",
                        f"Writer step did not complete. No bible change. Draft kept at {draft_path or 'the drafts folder'}. "
                        "Ask the operator before switching modes: CLI mode sends chat text to Anthropic with your key, "
                        "and to Google if the fallback fires.",
                        recovery="ask", side_effects="partial",
                        context={"last_draft": draft_path, "errors": state["last_errors"],
                                 "prep_cycles": state.get("prep_cycles", 1)})


def check_attempts_left(state: dict, draft_path: str | None = None) -> None:
    if state["attempts"] >= MAX_WRITER_ATTEMPTS:
        raise gave_up(state, draft_path)


def start_cycle(state: dict, now: str) -> dict:
    """A prep call. The first one, or one after submits, starts a new cycle of
    3 attempts; a repeat with no submit in between does not."""
    cycles = state.get("prep_cycles", 0)
    if cycles == 0 or state["attempts"] > 0:
        if cycles >= MAX_PREP_CYCLES:
            raise gave_up(state, None)
        return {**state, "prep_cycles": cycles + 1, "attempts": 0, "updated": now}
    return state


def record_attempt(state: dict, *, ok: bool, errors: list[dict] | None, model: str | None, now: str) -> dict:
    return {**state, "attempts": state["attempts"] + 1, "last_errors": None if ok else errors,
            "writer_model": model or state["writer_model"], "updated": now}


def next_command(state: dict | None, slug: str) -> str:
    """The literal next command for `chatcom status`."""
    if state is None:
        return f"chatcom ingest {slug} --export <file>"
    stage = state["stage"]
    if stage == "read" and state.get("transcript"):
        # a runnable command; the CLI says to check the transcript first
        import shlex
        mode = state.get("writer_mode", "anthropic")
        if mode == "session":
            return f"chatcom ingest {slug} --export {shlex.quote(state['transcript'])} --accept-data-flow"
        tail = f" --writer {mode}" + (" --replay <file>" if mode == "replay" else "")
        return f"chatcom episode {slug} --export {shlex.quote(state['transcript'])}{tail}"
    if stage == "copied" and state.get("writer_mode") == "session":
        return f"chatcom read-prep {slug}"
    if stage in ("copied", "read"):
        return f"chatcom {'ingest' if state.get('writer_mode') == 'session' else 'episode'} {slug} --export <file> --replace"
    if stage == "ingested":
        if state.get("writer_mode") == "session":
            return f"chatcom write-prep {slug}"
        return f"chatcom episode {slug} --export <file> --writer {state.get('writer_mode', 'anthropic')}"
    if stage == "drafted":
        return f"chatcom cast {slug}"
    if stage == "cast":
        return f"chatcom render {slug}"
    if stage == "rendered":
        return "encode arrives with the next release; the master is in render/master.mp4"
    return f"chatcom status {slug}"
