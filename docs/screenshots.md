# Screenshots

Any chat app works through screenshots: Signal, Google Messages, Snapchat, Discord, iMessage, anything that has
no export. CHATCOM reads each screenshot, joins them in order and drops what repeats where two screenshots
overlap.

**Status:** this release joins screenshots with fixed rules and reads them with a recorded (replay) reader, for
tests and the demo. Reading your own screenshots in Claude Code (skill mode) and with Gemini (CLI mode) arrive in
the next releases. Until then, paste or type the chat as a transcript ([transcript-format.md](transcript-format.md)).

## Taking the screenshots

- Scroll so each screenshot overlaps the last by a message or two. Overlap is how the screenshots are joined.
- Keep the day headers ("Today", "Monday", "Jan 9") in view when you can: they date the messages.
- Long scrolling screenshots are fine; they are split into overlapping tiles.
- Up to 40 images (or tiles) per import; `--max-images N` raises it.

## What happens to them

1. **Copies.** Each image is checked (PNG, JPEG, WebP or HEIC; size read from the header), then re-encoded by
   ffmpeg with no metadata (no location, no device). Only the copies are read or sent. HEIC needs a Mac.
2. **Reading.** Each copy is read on its own into rows: side, name, text, time, quote, reactions. Reads are
   cached, so a re-run never reads the same image twice.
3. **Joining.** Fixed rules, no model: oldest first (newest-first screenshots are detected, or pass
   `--reverse` / `--no-reverse`); repeated rows at a seam are dropped; your own bubbles (the right side) are you;
   a bubble with no name takes the name above it; no name at all is `Speaker A`. Day headers are resolved
   against when each screenshot was taken (`--anchor-date YYYY-MM-DD` overrides).
4. **Transcript.** The result is written as `transcript.txt` (`# source: screenshots`) in the episode's
   `import/` folder, then masked (phone numbers) and filtered (excluded people) like any other source.

## Fixing a read

`--review` stops after `transcript.txt`. Open it, fix any line, then import that file:

```bash
chatcom episode <show> --export ~/.chatcom/<show>/episodes/S01E02/import/transcript.txt
```

`--speaker A=Mike` names a `Speaker A` label for this import. `--speaker right=Dave` names your own bubbles if
the screenshots are someone else's phone. A new import of other screenshots on top of one that stopped at review
needs `--replace`.
