# Design: any group chat (Step 2.5, wave 1)

Status: approved 2026-11-22 (plan review: CEO, DX and Eng). This file is the one
current spec for wave 1. It replaces every earlier ingest rule where they
disagree. Review history is not kept here. `scripts/check_spec.py` fails the
build when a code, an exit status or a flag below has two meanings, or when
the code and this file disagree.

## What it does

CHATCOM takes any group chat. "Drop in your group chat" means one of:

| App | How to get it in | Wave |
|---|---|---|
| WhatsApp | export `.txt` or `.zip` | shipped |
| Telegram | Desktop export, machine-readable JSON (`result.json`) | 1 |
| Signal, Google Messages, Snapchat, Discord, anything else | screenshots | 1 |
| Anything | a transcript: typed, or pasted into Claude Code | 1 |
| Messenger / Instagram | "Download your information" JSON | 2 |
| GroupMe | export `message.json` (format to confirm with a real export) | 2 |
| iMessage | the operator runs `imessage-exporter` and passes its text output (never bundled) | 2 |

Wave 1 ships as 7 PRs, each green on its own:

1. Schema v2, keyed ids, the shared masking module, the source registry and sniffing, one consent flag.
2. The transcript adapter.
3. The Telegram adapter.
4. Screenshot post-processing and the ReplayReader.
5. The SessionReader (`read-prep` / `read-submit`).
6. The llm-fallback image release and the Gemini reader.
7. The reader eval, docs and positioning.

Casting starts when PRs 1-6 merge. The reader eval and the writer eval gate the
v0.1.0 release, not casting work.

## Messages (schema v2)

Every adapter writes the same message shape (`schemas/message.schema.json`):

- `time_precision`: `exact` (seconds or better), `minute`, `day` or `order` (no date).
- `source.adapter`: `whatsapp`, `telegram`, `transcript`, `screenshots`, `messenger`,
  `instagram`, `groupme`, `imessage` or `synthetic`. Screenshot rows carry
  `source.image` and `source.row`; `source.line` is set for text sources.
- `reactions`: a list of `{emoji, sender, count}`; `sender` is null when a
  screenshot shows only a count ("😂 2").
- `quote`: `{sender, text}` or null, for a quoted reply.
- `time` for `day` and `order` rows is synthetic: strictly increasing in chat
  order, to the millisecond, anchored to the date the source shows (or 12:00 on
  the anchor date when no time shows at all).

Bible and cast sheet are `schema_version: 2`. A v1 show is refused with
`CHATCOM_E_SCHEMA_OLD`, which names `chatcom migrate <slug>`. `migrate` backs
up the show, adds the v2 fields to stored message files, rebuilds the cursor
signatures from them and writes `bible.json` last, so a kill during migrate
leaves a v1 show that migrates again.

## Ids, signatures and the show key

Each show has a random secret in `CHATCOM_HOME/<slug>/secret` (mode 0600).
Every message id, every cursor signature and every `Unnamed xxxxxx` phone label
is an HMAC-SHA256 keyed by it, so a phone number cannot be brute-forced back
from an id or a label, and two shows give different ids for the same message. Labels change when a show gets a new secret (`migrate`, a lost secret), so ingest
refuses, before reading any message, an `exclude` entry that is a phone label
matching no sender in the export: an exclusion can never go stale silently.

Identity text is normalized harder than display text: NFC, case-folded,
punctuation and whitespace removed, every emoji mapped to one class. OCR noise
("K" against "k", a dropped comma) then gives the same id.

- `exact` / `minute` rows: `(raw label, UTC minute, ordinal within label-minute, identity text)`.
- `day` / `order` rows: `(raw label, day or "none", identity text, previous row's raw label and identity text, ordinal within a run of identical rows)`.
- The raw label is the name exactly as the source showed it, before aliases or
  `--speaker`. The operator's own screenshot bubbles use `@operator`. A row whose
  label is still `Speaker X` leaves the label out of its id.

## Where the next episode starts

One pure function, `core.resume.resume_point(cursor, rows)`, returns the new
rows, the method used and a warning. It tries, in order:

1. the cursor's message id;
2. time, compared in UTC, when both sides have `exact` or `minute` times; the
   cursor's own minute is an overlap zone, de-duplicated by identity text;
3. content alignment on the cursor's last rows, compared as keyed identity
   hashes (no text is stored). A match must be unique: 3 rows, then 5 rows,
   then 5 rows with one row allowed to differ; still ambiguous counts as no
   match;
4. nothing matched: the whole import is the window, with the warning
   `CHATCOM_W_WHOLE_IMPORT` and the import's date range.

The cursor stores two signatures: one on raw labels (same source) and one on
canonical names after aliases (across sources, text first, label as a
tiebreak). `order` imports have no 7-day or 14-day rule: with no match the
whole import is the window, capped by `--max-images`; `--since` does not apply
to them and says so.

## Sniffing

`--export` takes a file, a folder or several image files. The registry decides
the source in this order:

1. A text file whose first non-blank line (a byte-order mark is ignored) is
   `# source:` or `# speakers:` is a transcript.
2. A folder that holds only images, or image files: screenshots.
3. Zip and JSON by their contents: WhatsApp (`_chat.txt` or `WhatsApp Chat*.txt`
   inside), Telegram (`result.json` with `messages[].from`; read by peeking,
   never by loading a whole large file).
4. Text by structure: WhatsApp Android `date time - Name:`, WhatsApp iOS
   `[date, time] Name:`. ISO-date WhatsApp exports are WhatsApp.

Two matches in the same step: `CHATCOM_E_SOURCE_AMBIGUOUS` with the candidates.
No match: `CHATCOM_E_SOURCE_UNKNOWN`. A text file without a transcript header is
never read as a transcript unless `--source transcript` is passed. `--source`
overrides sniffing. A multi-chat export needs `--thread NAME`
(`CHATCOM_E_THREAD_REQUIRED`). The chosen source is printed.

**Session mode reads only allowed paths.** With `--writer session` (the skill),
`--export` must be the path given at `new` or a file inside
`CHATCOM_HOME/<slug>/inbox/`. Any other path exits 2 with
`CHATCOM_E_EXPORT_OUTSIDE_INBOX`. The skill saves a paste to
`inbox/paste-<time>.txt` with a `# source: paste` header and ingests it.

## The transcript format

The one text format an operator reads or edits (`docs/transcript-format.md`):

- Header lines: `# source: <adapter>` and `# speakers: Mike, Dave, Sam`.
- A message: `[YYYY-MM-DD HH:MM] Name: text` or `[HH:MM] Name: text` or `Name: text`.
  A `--- YYYY-MM-DD ---` line sets the date for the lines after it.
- A name is followed by `: `, is at most 40 characters, is not all digits and is
  not a URL scheme. With a `# speakers:` header only those names count; without
  one, a name must start 2 or more lines.
- Continuation lines are indented two spaces. Inside a message, a line that
  would read as a header, a date line or a known speaker starts with `\`.
- Placeholders: `<image>`, `<video>`, `<sticker>`, `<audio>`, `<deleted>`.
  Reactions end a message: `(reactions: 😂 Mike, ❤️ Dave, 👍 3)`.
  A quote starts a message: `> Mike: original text` on the line before.
- More than 2% unparsed lines fails, as for WhatsApp.

## Screenshots

1. `new` or `ingest` with `--accept-data-flow` writes an image manifest
   (original name, capture time from EXIF or the file time, order: capture time,
   then name), then copies each image into the episode folder. Copies are
   checked by magic bytes (PNG, JPEG, WebP, HEIC), their size is read from the
   header before decoding, and ffmpeg re-encodes them with a forced demuxer,
   `-protocol_whitelist file` and no metadata. HEIC goes through macOS `sips`;
   elsewhere it stops with `CHATCOM_E_IMAGE_UNREADABLE`. Tall images are split
   into overlapping tiles. Only the copies are ever read or sent.
2. The reader reads each image on its own and returns rows: side (left/right),
   visible name, text, visible time, quote, reactions, placeholders and edge
   flags (`partial` at the top or bottom), plus the chat name from the header.
   Its output is cached per (image hash, reader model, prompt version).
3. `read-submit` then joins the images with fixed rules: manifest order;
   reverse order detected from visible times or seam overlap (`--reverse`,
   `--no-reverse` force it); `Speaker A/B…` labels assigned across the import;
   senders carried across a seam; relative dates resolved against capture times
   (`--anchor-date` overrides); seam duplicates dropped (a run of 2 or more
   identical rows, or one row when either copy is `partial` or both show the
   same time); `transcript.txt` written with `# source: screenshots`; then
   masking, exclusions and the window, as for every source.
4. The right side is the operator unless `--speaker right=NAME` says otherwise.
   `--speaker A=Mike` maps a label for this import only.
5. `--review` stops after `transcript.txt` so the operator can correct it, then
   `ingest --export transcript.txt` continues from stage `read`.

Skill mode: `read-prep` hands out 8 images at a time; the session writes
`reader.json`; `read-submit` adds each batch and post-processes after the last.
CLI mode: the Gemini chain `gemini-3.6-flash`, then `gemini-3.8-flash` when
that model is gone (404), then Claude when Gemini cannot answer; up to 3 calls
in parallel; `responseSchema` set; a `MAX_TOKENS` finish re-tiles without
spending an attempt; Gemini 3.x requests send `thinkingLevel: "low"` and never
`temperature`, `topP` or `topK`. CI uses the ReplayReader.

Attempts: 3 submits per `read-prep` or `write-prep`; at most 3 prep cycles per
episode; then `CHATCOM_E_READER_GAVE_UP` or `CHATCOM_E_WRITER_GAVE_UP`, exit 4,
`side_effects: partial`, with the draft path.

## Episode stages

| Stage | Reached by | Next |
|---|---|---|
| `copied` | `ingest` (screenshots) | `read` |
| `read` | `read-submit`, last batch | `ingested` (automatic) |
| `ingested` | `ingest` (any text source), or after `read` | `drafted` |
| `drafted` | `write-submit` | `cast` |
| `cast`, `rendered`, `encoded`, `committed` | casting, render, encode (Next Steps 3-4) | — |

`ingest --export transcript.txt` on an episode at `read` replaces the reader
output. Any other `--export` on an episode at `copied` or `read` exits 2 with
`CHATCOM_E_IMPORT_IN_PROGRESS` unless `--replace` is passed. The bible changes
only at `committed`. `chatcom status <show>` prints the stage, attempt counts,
the lock holder and the literal next command.

## Consent

One flag, `--accept-data-flow`, answers the data-flow notice on every command
that hands chat data to a model. Without it, the command prints the notice and
exits 6. The episode state records `accepted_flow` (writer mode and reader
mode); a later command with a different mode exits 6 with the new notice. The
skill never passes `--accept-data-flow` unless the operator approved that exact
notice in this session.

Notice texts:

- Exports and transcripts, skill mode: the chat text goes to the model provider
  the Claude Code session uses.
- Exports and transcripts, CLI mode: the chat text goes to Anthropic with your
  key, and to Google if the fallback fires.
- Screenshots or a paste, skill mode: "In Claude Code, this session reads your
  screenshots (or your paste) in full, including phone numbers and anyone you
  exclude, and then writes the episode. For a group you do not fully trust, use
  CLI mode, where reading and writing are separate model calls and excluded
  people never reach the writer."
- Screenshots, CLI mode: "Your screenshots go to Google (Gemini), and to
  Anthropic if the fallback fires. Phone numbers and excluded people are
  removed after reading, before the episode is written."

## Errors

Every error follows the contract in `docs/errors.md`. `--json` prints errors as
an envelope on stderr, success as `{episode_path, stage, model, calls_billed}`
on stdout and a notice as `{notice_text, data_flow, next_command}` on stdout.

<!-- check_spec:codes -->
| Code | Exit | PR |
|---|---|---|
| `CHATCOM_E_USAGE` | 2 | shipped |
| `CHATCOM_E_NO_TERMINAL` | 2 | shipped |
| `CHATCOM_E_EXPORT_NOT_FOUND` | 2 | shipped |
| `CHATCOM_E_EXPORT_UNREADABLE` | 2 | shipped |
| `CHATCOM_E_EXPORT_ZIP` | 2 | shipped |
| `CHATCOM_E_UNPARSED_LINES` | 2 | shipped |
| `CHATCOM_E_EMPTY_EXPORT` | 2 | shipped |
| `CHATCOM_E_STALE_EXPORT` | 2 | shipped |
| `CHATCOM_E_UNKNOWN_MEMBER` | 2 | shipped |
| `CHATCOM_E_SETUP_INVALID` | 2 | shipped |
| `CHATCOM_E_SHOW_EXISTS` | 2 | shipped |
| `CHATCOM_E_SHOW_NOT_FOUND` | 2 | shipped |
| `CHATCOM_E_SHOW_AMBIGUOUS` | 2 | shipped |
| `CHATCOM_E_BIBLE_INVALID` | 2 | shipped |
| `CHATCOM_E_SCHEMA_NEWER` | 2 | shipped |
| `CHATCOM_E_LOCKED` | 2 | shipped |
| `CHATCOM_E_STATE` | 2 | shipped |
| `CHATCOM_E_BEATS_INVALID` | 2 | shipped |
| `CHATCOM_E_TIMEZONE` | 2 | shipped |
| `CHATCOM_E_AWAITING_CONFIRMATION` | 6 | shipped |
| `CHATCOM_E_WRITER_GAVE_UP` | 4 | shipped |
| `CHATCOM_E_WRITER_UNAVAILABLE` | 4 | shipped |
| `CHATCOM_E_WRITER_BAD_REQUEST` | 4 | shipped |
| `CHATCOM_E_PREFLIGHT` | 3 | shipped |
| `CHATCOM_E_SCHEMA_OLD` | 2 | 1 |
| `CHATCOM_E_SOURCE_AMBIGUOUS` | 2 | 1 |
| `CHATCOM_E_SOURCE_UNKNOWN` | 2 | 1 |
| `CHATCOM_E_THREAD_REQUIRED` | 2 | 1 |
| `CHATCOM_E_EXPORT_OUTSIDE_INBOX` | 2 | 1 |
| `CHATCOM_E_TRANSCRIPT_INVALID` | 2 | 2 |
| `CHATCOM_E_IMAGE_UNREADABLE` | 2 | 4 |
| `CHATCOM_E_TOO_MANY_IMAGES` | 2 | 4 |
| `CHATCOM_E_SPEAKER_UNMAPPED` | 2 | 4 |
| `CHATCOM_E_IMPORT_IN_PROGRESS` | 2 | 4 |
| `CHATCOM_E_READ_INVALID` | 2 | 5 |
| `CHATCOM_E_READER_GAVE_UP` | 4 | 5 |
| `CHATCOM_E_READER_UNAVAILABLE` | 4 | 6 |
<!-- /check_spec:codes -->

`CHATCOM_W_WHOLE_IMPORT` is a warning, not an error: the run continues.
`CHATCOM_E_LOCKED` names the holder's PID; a lock whose process is gone is
cleared automatically, so there is no unlock command.

## Flags

<!-- check_spec:flags -->
| Flag | Commands | Meaning | PR |
|---|---|---|---|
| `--accept-data-flow` | new, ingest, episode | answer the data-flow notice (on `new`, for screenshots) | 1 |
| `--source` | new, ingest, episode | force the source adapter | 1 |
| `--thread` | new, ingest, episode | pick one chat in a multi-chat export | 1 |
| `--writer` | new, episode | `anthropic` or `replay` (the skill's `ingest` is always session mode); on `new` it picks the screenshot reader and notice | shipped |
| `--speaker` | new, ingest, episode, read-submit | map a label for this import (`A=Mike`, `right=Dave`) | 4 |
| `--reverse` | new, episode, read-submit | images are newest first | 4 |
| `--no-reverse` | new, episode, read-submit | images are oldest first | 4 |
| `--anchor-date` | new, episode, read-submit | the date the first screenshot shows | 4 |
| `--review` | episode, read-submit | stop after `transcript.txt` | 4 |
| `--replace` | ingest, episode | start an import over | 4 |
| `--max-images` | new, ingest, episode | cap on images or tiles (default 40) | 4 |
| `--replay-reads` | new, episode | recorded screenshot reads for the ReplayReader (CI, the demo) | 4 |
| `--import` | read-prep, read-submit | a pending screenshot import from `new` in skill mode (no show yet) | 5 |
| `--reader-model` | new, episode | override the reader model | 6 |
<!-- /check_spec:flags -->

## Tests that must exist

- Sniff matrix over every fixture, including an ISO-date WhatsApp export, a
  Telegram folder, a header-less text file (refused) and a YAML file (refused).
- Hostile `--export` arguments in session mode: a path outside the inbox, `$(…)`, `;`.
- Two shows give different ids for the same message; the secret file is 0600.
- WhatsApp regression: parse output and the 40 leak probes are unchanged except
  for `id` and cursor fields.
- `resume_point` table test over every source × precision × outcome, OCR noise,
  a repeated triple, a cross-source alias and a DST fall-back.
- Transcript write → parse is the identity over a hostile corpus.
- Stage table property test: random command sequences never reach an undefined
  stage and never write the bible before `committed`.
- The keyless screenshot path end to end with the ReplayReader.

## Not in wave 1

Screen recordings, local OCR, `--approve-calls-max`, `chatcom config show`,
Messenger/Instagram, GroupMe and iMessage (wave 2).
