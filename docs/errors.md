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

## chatcom_e_locked
Another chatcom run holds this show's lock. A lock whose process is gone is cleared automatically. Exit 2.

## chatcom_e_state
A step ran out of order (for example `write-submit` before `ingest --confirmed`, or on an episode that is already drafted). Exit 2.

## chatcom_e_beats_invalid
The writer's `beats.json` failed validation. Every problem is listed with its JSON path. Fix all of them and submit the whole file again. Attempts 1 and 2 exit 2; the third failure exits 4. Exit 2.

## chatcom_e_timezone
Unknown IANA timezone. Exit 2.

## chatcom_e_awaiting_confirmation
`chatcom ingest` without `--confirmed` always stops here and prints the data-flow notice. No message is read or shown. The skill relays the notice to the operator and continues only after they agree. Exit 6.

## chatcom_e_writer_gave_up
The writer failed validation three times. Nothing was saved. Fixed message: "Writer step did not complete. Nothing was saved. Rerun the skill, or run `chatcom episode <show> --writer anthropic`." The last draft's path is in the context. Exit 4.

## chatcom_e_writer_unavailable
Neither Claude nor the Gemini fallback could answer (no key, expired key, rate limit, outage). The context names the env variables, never their values. Exit 4.

## chatcom_e_writer_bad_request
A provider rejected the request itself (HTTP 400). This is a chatcom bug, not an outage, so there is no fallback. Exit 4.

## chatcom_e_preflight
`chatcom doctor` found a missing requirement. Exit 3.
