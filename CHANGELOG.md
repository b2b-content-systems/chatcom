# Changelog

## 0.1.0.dev8 (2026-11-23)

Next Step 3, part 1: casting.

### Added
- `chatcom cast <show>`: after the draft, draws each cast member in 6 poses (neutral, talking, laughing, typing,
  facepalm, shrug), a style frame for the look, and the sets (3 starters, up to 2 new locations per episode). Members
  with a reference photo are drawn from it; the rest are original characters from how they text, never a guess at a
  real face, and never sent a name.
- Consent and spend: the notice says what goes to Google; `--approve-calls N` must equal the planned count.
- `gemini-nano-banana-2.1` by default, `gemini-3.1-flash-image` as the fallback; a show keeps one model. A model that is
  gone or keeps failing moves the whole batch; a quota stop keeps what was drawn; a refused photo casts that member from
  how they text.
- Images are content-addressed in the show's cache: a rerun never pays twice. Characters are cut out of the green with
  ffmpeg, keyed on the colour sampled from the corners.
- Results go to `cast.next.json`; `cast.json` changes only when the episode is committed.
- `CHATCOM_E_PAINTER_UNAVAILABLE` (exit 4). llm-fallback re-vendored (image output, refusals as answers).

## 0.1.0.dev7 (2026-11-23)

Any group chat, PR 7 of 7: the reader eval, docs and positioning.

### Added
- The reader eval (`docs/reader-eval.md`, `scripts/eval_reader.py`): scores a screenshot reader against a
  hand-checked transcript (recall, precision, sender, order, dates). One synthetic case runs in CI; real cases and
  scores stay off the repository. The release targets are written as targets, not results.
- `AGENTS.md`: the rules for agents working in this repository (the chat is data, no real chats, no keys,
  consent and paths).

### Changed
- README rewritten around what a person needs: what it reads, how to install, the two modes, where the chat
  goes, what it costs, the commands, how to remove a show.
- The site says which chats work (WhatsApp, Telegram, screenshots of any app, a paste) and who reads the chat.

## 0.1.0.dev6 (2026-11-23)

Any group chat, PR 6 of 7: CLI mode reads screenshots.

### Added
- The Gemini reader: each screenshot copy goes to `gemini-3.6-flash` with your own `GEMINI_API_KEY`
  (`gemini-3.8-flash` when that model is gone, Claude with your `ANTHROPIC_API_KEY` when Gemini cannot answer),
  one image per call with the read schema as `responseJsonSchema`; Gemini 3.x calls send `thinkingLevel: low` and
  never `temperature`, `topP` or `topK`.
- Up to 3 reads at once; a read that does not match the schema is asked for again with the problems listed
  (3 tries per image, then `CHATCOM_E_READER_GAVE_UP`); a reply cut off at the token limit is split into halves
  without spending a try; `CHATCOM_E_READER_UNAVAILABLE` (exit 4) when no reader can answer.
- `--reader-model` on `new` and `episode`; `calls_billed` counts screenshot reads.
- llm-fallback (vendored, upstream b2b-content-systems/llm-fallback#1): images, JSON schemas, finish reasons,
  `ModelGone` for a model id that no longer exists.

## 0.1.0.dev5 (2026-11-23)

Any group chat, PR 5 of 7: the SessionReader (skill mode reads screenshots).

### Added
- `chatcom read-prep` and `chatcom read-submit`: in Claude Code the session reads screenshots, 8 at a time, from
  the copies only (`prompts/reader.md`: the images are data, not instructions). chatcom checks every read against
  `schemas/read.schema.json` and joins them with the fixed rules.
- A first-time screenshot import waits as a pending import (`--import ID`) until its transcript makes the show
  (`chatcom new <transcript.txt> --setup …`); a weekly import goes on to `ingested` by itself.
- `CHATCOM_E_READ_INVALID` lists bad reads by JSON path; 3 submits per `read-prep` and 3 `read-prep`s per batch,
  then `CHATCOM_E_READER_GAVE_UP` (exit 4) with the reads kept.
- Stage `copied` is written in skill mode; `chatcom status` prints `chatcom read-prep <show>`.

### Changed
- A name no longer carries across a gap between screenshots that do not overlap.
- Times that run backwards stop the join (more than 5 minutes, other than past midnight with no day header).

## 0.1.0.dev4 (2026-11-23)

Any group chat, PR 4 of 7: screenshot post-processing and the replay reader.

### Added
- Screenshot intake (`docs/screenshots.md`): a manifest in capture order (EXIF time, else the file time); each
  image checked by its first bytes and header size before anything decodes it; clean copies re-encoded by ffmpeg
  (forced demuxer, file protocol only, no metadata); HEIC through macOS `sips`; tall images split into
  overlapping tiles.
- The reader port, a read cache per (copy, model, prompt version), the read schema
  (`schemas/read.schema.json`) and the ReplayReader (`--replay-reads`) for CI and the demo.
- Fixed join rules (`core/screens.py`): newest-first detection, seam duplicates dropped (a whole copy replaces
  a cut-off one), names carried across seams, `Speaker A` labels, day headers resolved against capture dates.
  The result is a `# source: screenshots` transcript, read by the transcript parser.
- `new` takes `--accept-data-flow` and `--writer` (screenshots are read only after consent);
  `--speaker`, `--reverse`/`--no-reverse`, `--anchor-date`, `--review`, `--replace`, `--max-images`.
- Stages `copied` and `read`; `CHATCOM_E_IMPORT_IN_PROGRESS`, `CHATCOM_E_IMAGE_UNREADABLE`,
  `CHATCOM_E_TOO_MANY_IMAGES`, `CHATCOM_E_SPEAKER_UNMAPPED`.
- A dry run lists screenshots in reading order and never reads them.
- A property test drives random command sequences through the stage table.

### Not yet
- Reading screenshots in skill mode (PR 5) and with Gemini in CLI mode (PR 6): both refuse before any notice.

## 0.1.0.dev3 (2026-11-23)

Any group chat, PR 3 of 7: Telegram.

### Added
- Telegram Desktop exports (`docs/telegram-export.md`): pass the `ChatExport_…` folder or its `result.json`.
  Exact times, edits, media kinds, polls, reactions with names and counts, and replies (with a quote of the
  message they answer). Service lines are dropped.
- A whole-account export is read one chat at a time with `--thread NAME` (or the chat id); without it,
  `CHATCOM_E_THREAD_REQUIRED` lists the chats. Profile, contacts and the messages of other chats are never stored, shown or sent.
- Sniffing recognizes Telegram exports by peeking at the start of `result.json`, never loading a large file
  whole. Other JSON files and Telegram's HTML export are refused.

## 0.1.0.dev2 (2026-11-23)

Any group chat, PR 2 of 7: the transcript adapter.

### Added
- Transcripts: a plain-text chat you type, edit or paste (`docs/transcript-format.md`). Any app with no export
  (Signal, iMessage, Discord) now works. A file is read as a transcript only when it starts with `# source:` or
  `# speakers:`, or with `--source transcript`.
- Dates, times with or without seconds, day lines, undated lines, media placeholders, reactions with names or
  counts, quotes and escapes. Writing a transcript and reading it back gives the same messages.
- The skill saves a pasted chat as a transcript and imports it.
- `CHATCOM_E_TRANSCRIPT_INVALID` names the line of a bad header, date or time.

### Changed
- Excluding a person also removes their name from reactions (the count stays) and blanks `@mentions` of them in
  quotes.

## 0.1.0.dev1 (2026-11-23)

Any group chat, not only WhatsApp (Step 2.5, wave 1, PR 1 of 7: the shared core).

### Added
- A source registry: `chatcom` works out which app an export came from (header, images, container, text), and `--source NAME` picks one by hand. Telegram, pasted transcripts and screenshots are registered and arrive in later PRs.
- `--export` takes several files; `--thread NAME` picks one chat from an export that holds several.
- Schema v2 messages: `time_precision`, `source.label`, `quote`, reactions with counts.
- A per-show secret (`~/.chatcom/<show>/secret`, mode 0600). Message ids, the resume marker and the `Unnamed` phone labels are keyed hashes made with it.
- Resume that works across apps and without exact times: message id, then UTC time, then content alignment, then the whole import with a `CHATCOM_W_WHOLE_IMPORT` warning.
- `chatcom status <show>` prints the episode's stage and the next command. `chatcom migrate <show>` upgrades a v1 show (with a backup).
- `--json` before any command: results as JSON on stdout, errors as JSON on stderr with `code` and `fix`.
- The show inbox (`~/.chatcom/<show>/inbox/`). In skill mode, `ingest` and a dry run with no terminal read only the export given at `new` or a file in the inbox.

### Changed
- `ingest --confirmed` is now `ingest --accept-data-flow`. The acceptance is tied to one writer flow; a different flow, at write-prep or write-submit, asks again (exit 6).
- At most 3 writer attempts per prep and 3 prep cycles per episode.
- `episode` with no terminal exits 6 instead of asking.
- An `exclude` entry that no longer matches any sender (for example after migrate) stops the ingest. Nothing is sent.
- New error codes: `CHATCOM_E_SCHEMA_OLD`, `CHATCOM_E_SOURCE_AMBIGUOUS`, `CHATCOM_E_SOURCE_UNKNOWN`, `CHATCOM_E_THREAD_REQUIRED`, `CHATCOM_E_EXPORT_OUTSIDE_INBOX`.

### Fixed
- A 64 KB sniff read that split a multibyte character no longer refuses a valid export.
- A corrupt zip reports `CHATCOM_E_EXPORT_ZIP`.
