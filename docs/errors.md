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
A name in setup or a flag is not a sender in the chat. The fix lists the closest senders. Names match exactly, then case-folded. `chatcom recast --member NAME` needs a cast member's name or nickname, in any case; its fix lists the cast. Exit 2.

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

## chatcom_e_image_unreadable
A screenshot is not a PNG, JPEG, WebP or HEIC image, is over 40 MB or 60 million pixels, could not be decoded,
or is HEIC on a computer other than a Mac. The error names the file. Pass the screenshots as your phone saved
them, or save that one as PNG. `chatcom recast --photo PATH` stops here too when no file is at that path. Exit 2.

## chatcom_e_too_many_images
More screenshots (or tiles: a very tall screenshot is split into several) than the limit, 40 by default. Import
the newest ones, or raise the limit with `--max-images N`. Exit 2.

## chatcom_e_speaker_unmapped
`--speaker LABEL=NAME` names a label that is not in these screenshots. The error lists the labels it found
(`Speaker A`, the names shown above bubbles). Use one of them, or `right=NAME` for your own bubbles. Exit 2.

## chatcom_e_import_in_progress
This episode has a screenshot import that stopped part way (for example after `--review`). Continue it by
importing the `transcript.txt` it wrote, or pass `--replace` to start the import over. Exit 2.

## chatcom_e_read_invalid
The session's `reader.json` does not match the read format: an image of the batch has no read, a field is
missing or wrong, or a read is for an image not in this batch. The error lists each problem by JSON path. Fix
every one and submit the whole file again. Three tries per `read-prep`. Exit 2.

## chatcom_e_reader_gave_up
The screenshot reads failed their checks three times in each of three `read-prep` rounds for one batch. The
images read so far are kept. Ask the operator: they can type or paste the chat as a transcript instead
([transcript-format.md](transcript-format.md)), or use CLI mode. Exit 4.

## chatcom_e_reader_unavailable
No reader could answer in CLI mode: Gemini (with your `GEMINI_API_KEY`) and then Claude (with your
`ANTHROPIC_API_KEY`) both failed (no key, expired key, rate limit, outage). The error names each reason. Set a
key and run the command again: reads already done are cached and not paid for twice. Exit 4.

## chatcom_e_painter_unavailable
Casting could not draw an image. Either the image model stopped (no `GEMINI_API_KEY`, a revoked key, or an
exhausted quota), or neither the default model nor the fallback could finish the batch. Images already drawn are
kept in the show's cache and are not paid for twice: fix the key or wait for the quota, then run `chatcom cast`
again. A show keeps the image model it was first cast with. If Google has retired that model,
`chatcom recast <show>` draws the whole show again on the current one. Exit 4.

## chatcom_e_export_outside_inbox
In skill mode (`chatcom ingest` for the session writer, and `chatcom episode --dry-run` without a terminal) chatcom
reads only the export given at `chatcom new` or files in the show's inbox (`~/.chatcom/<show>/inbox/`). Other
commands send nothing until `--accept-data-flow` is passed after the notice. Move the new export into the inbox and
pass that path. Exit 2.

## chatcom_e_locked
Another chatcom run holds this show's lock. A lock whose process is gone is cleared automatically. Exit 2.

## chatcom_e_state
A step ran out of order (for example `write-submit` before `ingest --accept-data-flow`, or on an episode that is already drafted, or `recast` before the draft or after the render). `chatcom status <show>` prints the next command. Exit 2.

## chatcom_e_beats_invalid
The writer's `beats.json` failed validation. Every problem is listed with its JSON path. Fix all of them and submit the whole file again. Attempts 1 and 2 exit 2; the third failure exits 4. Exit 2.

## chatcom_e_timezone
Unknown IANA timezone. Exit 2.

## chatcom_e_awaiting_confirmation
A command that would send chat text to a model stops here and prints the data-flow notice; no message is read or shown. `chatcom ingest` without `--accept-data-flow` always stops here, and so does `chatcom episode` with no terminal to ask. A command whose writer mode differs from the one the operator accepted at ingest stops here too, with the new notice. With `--json`, stdout carries `{notice_text, data_flow, next_command}`. The skill relays the notice to the operator and continues only after they agree. `chatcom cast` and `chatcom recast` stop here with the image notice and the exact number of image calls, and again when `--approve-calls` is not that number; the `next_command` carries both flags. Exit 6.

## chatcom_e_writer_gave_up
The writer failed validation three times in one prep cycle (`write-prep` starts a new cycle; at most three per episode). Fixed message: "Writer step did not complete. No bible change. Draft kept at <path>. Ask the operator before switching modes: CLI mode sends chat text to Anthropic with your key, and to Google if the fallback fires." Side effects: partial (the draft and the episode state are kept). Exit 4.

## chatcom_e_writer_unavailable
Neither Claude nor the Gemini fallback could answer (no key, expired key, rate limit, outage). The context names the env variables, never their values. Exit 4.

## chatcom_e_writer_bad_request
A provider rejected the request itself (HTTP 400). This is a chatcom bug, not an outage, so there is no fallback. Exit 4.

## chatcom_e_preflight
`chatcom doctor` found a missing requirement. Exit 3.
