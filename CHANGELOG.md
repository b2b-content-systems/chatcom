# Changelog

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
