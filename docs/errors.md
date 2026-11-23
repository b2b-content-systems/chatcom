# Errors

Every chatcom error has a stable code, a **what** line with the real values,
a **fix**, a **recovery** (`retry`, `fix and retry`, `ask`), its **side
effects** (`none`, `partial`, `complete`) and context. With `--json`, the
error prints as one JSON object on stderr:
`{code, what, fix, recovery, side_effects, context, trace_path}`.
API keys are redacted from every error and log line.

| Exit | Meaning |
|---|---|
| 0 | success |
| 2 | usage or setup |
| 3 | preflight (`chatcom doctor`) |
| 4 | model provider |
| 5 | render or encode |
| 6 | awaiting confirmation |

## chatcom_e_usage
A command was called with missing or conflicting arguments. Exit 2.

## chatcom_e_no_terminal
A question needs an answer and stdin is not a terminal, so chatcom will not wait. Pass the answer as a flag (`--operator`, `--accept-data-flow`) or a `setup.json`. Exit 2.

## chatcom_e_export_not_found
No file at the export path. Exit 2.

## chatcom_e_export_unreadable
The export is over the size limit or not UTF-8/UTF-16 text. Pass the WhatsApp export unchanged. Exit 2.

## chatcom_e_export_zip
The zip is not readable, or holds more than one chat `.txt`. Pass the `.txt` directly. Exit 2.

## chatcom_e_unparsed_lines
More than 2% of message lines did not parse. The error names the detected platform, date order and clock; the lines themselves go to a local report file under the show's `logs/`, never to the screen. Check [whatsapp-export.md](whatsapp-export.md), or accept the rate with `--max-unparsed`. Exit 2.

## chatcom_e_empty_export
The export holds no messages. Exit 2.

## chatcom_e_stale_export
No messages after the last episode's cursor. Export the chat again so it includes this week, or pass `--bottle` for a callback episode. No model was called. Exit 2.

## chatcom_e_unknown_member
A name in setup or a flag is not a sender in the chat. The fix lists the closest senders. Names match exactly, then case-folded. Exit 2.

## chatcom_e_setup_invalid
`setup.json` failed its schema. The error gives the JSON path, the bad value and what is allowed. Exit 2.

Ingest also stops here, before reading any message, when `setup.json` excludes an `Unnamed xxxxxx` phone label that matches no sender in the export: labels are keyed by the show's secret and change after `chatcom migrate` or a new secret, so a stale one would silently stop excluding that person. The error lists the labels the export uses.

## chatcom_e_show_exists
`chatcom new` was given a slug that already has a show. Exit 2.

## chatcom_e_show_not_found
No show with that slug in `CHATCOM_HOME`. Exit 2.

## chatcom_e_show_ambiguous
More than one show exists and none was named. Exit 2.

## chatcom_e_bible_invalid
`bible.json` or `cast.json` failed its schema, usually after a hand edit. The error names the path. Exit 2.

## chatcom_e_schema_newer
The bible was written by a newer chatcom. Upgrade chatcom. Exit 2.

## chatcom_e_schema_old
The show was made by an older chatcom. Run `chatcom migrate <show>`: it backs up the show, then upgrades it. Exit 2.

## chatcom_e_source_ambiguous
The export matches more than one kind of chat export. Pass `--source NAME` with one of the names listed. Exit 2.

## chatcom_e_source_unknown
The export is not a chat export chatcom knows, or its kind is not in this chatcom yet. A text file is read as a
transcript only when its first line is `# source:` or `# speakers:` (docs/transcript-format.md), or with
`--source transcript`. Exit 2.

## chatcom_e_thread_required
The export holds more than one chat. Pass `--thread NAME` with one of the chats listed. Exit 2.

## chatcom_e_transcript_invalid
A transcript has a line chatcom cannot read as written: a header other than `# source: WORD` or
`# speakers: A, B`, a speaker listed twice or not a valid name, or a date or time that does not exist
(`--- 2026-02-30 ---`, `[25:00]`). The error names the line. Fix it as
[transcript-format.md](transcript-format.md) shows. Exit 2.

## chatcom_e_export_outside_inbox
In skill mode (`chatcom ingest` for the session writer, and `chatcom episode --dry-run` without a terminal) chatcom
reads only the export given at `chatcom new` or files in the show's inbox (`~/.chatcom/<show>/inbox/`). Other
commands send nothing until `--accept-data-flow` is passed after the notice. Move the new export into the inbox and
pass that path. Exit 2.

## chatcom_e_locked
Another chatcom run holds this show's lock. A lock whose process is gone is cleared automatically. Exit 2.

## chatcom_e_state
A step ran out of order (for example `write-submit` before `ingest --accept-data-flow`, or on an episode that is already drafted). `chatcom status <show>` prints the next command. Exit 2.

## chatcom_e_beats_invalid
The writer's `beats.json` failed validation. Every problem is listed with its JSON path. Fix all of them and submit the whole file again. Attempts 1 and 2 exit 2; the third failure exits 4. Exit 2.

## chatcom_e_timezone
Unknown IANA timezone. Exit 2.

## chatcom_e_awaiting_confirmation
A command that would send chat text to a model stops here and prints the data-flow notice; no message is read or shown. `chatcom ingest` without `--accept-data-flow` always stops here, and so does `chatcom episode` with no terminal to ask. A command whose writer mode differs from the one the operator accepted at ingest stops here too, with the new notice. With `--json`, stdout carries `{notice_text, data_flow, next_command}`. The skill relays the notice to the operator and continues only after they agree. Exit 6.

## chatcom_e_writer_gave_up
The writer failed validation three times in one prep cycle (`write-prep` starts a new cycle; at most three per episode). Fixed message: "Writer step did not complete. No bible change. Draft kept at <path>. Ask the operator before switching modes: CLI mode sends chat text to Anthropic with your key, and to Google if the fallback fires." Side effects: partial (the draft and the episode state are kept). Exit 4.

## chatcom_e_writer_unavailable
Neither Claude nor the Gemini fallback could answer (no key, expired key, rate limit, outage). The context names the env variables, never their values. Exit 4.

## chatcom_e_writer_bad_request
A provider rejected the request itself (HTTP 400). This is a chatcom bug, not an outage, so there is no fallback. Exit 4.

## chatcom_e_preflight
`chatcom doctor` found a missing requirement. Exit 3.
