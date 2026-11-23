# The transcript format

A transcript is a plain text chat that you can type, edit or paste. Use it for any app that has no export:
Signal, iMessage, Discord, a work chat. CHATCOM reads it like any other export.

```text
# source: paste
# speakers: Dave, Mike, Theo
--- 2026-01-09 ---
[2026-01-09 19:02] Dave: who is bringing the grill this year
[2026-01-09 19:03] Mike: not me (reactions: 😂 Theo, 👍 2)
> Mike: not me
[19:05] Theo: <image> found the receipt
  it was 2023
Dave: ok fine
```

## The first line

The file must start with a header line. A text file without one is never read as a transcript (so a stray
file cannot reach a model), unless you pass `--source transcript`.

- `# source: WORD`: where the chat came from, one word (`paste`, `signal`, `imessage`).
- `# speakers: A, B, C`: everyone who speaks. Only these names start a message. Without this line a name
  must start 2 or more lines to count. A name that contains a comma is written `Dave\, Jr.`.

## Messages

| Line | Meaning |
|---|---|
| `[2026-01-09 19:02] Name: text` | a message with a date and time |
| `[19:02] Name: text` | a time on the date above it |
| `[19:02:30] Name: text` | with seconds |
| `Name: text` | no time: it takes the date above it |
| `--- 2026-01-09 ---` | the date for the lines after it |

A full `[date time]` line also sets the date for the lines after it. A line with no time keeps its place:
it goes just after the line before it on the same day (or just before the next timed line, or at 12:00).
Lines with no date at all (before the first date) are placed on the day before the first date shown, or on
the day the file was saved when no date shows anywhere. A `[19:02]` time with no date above it is kept in
order but its time is not used, so add a `--- YYYY-MM-DD ---` line when you know the date.

A name is at most 40 characters, is followed by `: `, is not all digits and is not a web address scheme.

## Inside a message

- **More lines:** indent them two spaces.
- **A line that looks like structure** (a message, a date line, a header, a quote): start it with `\`. The
  backslash is removed.
- **Placeholders:** the message is only `<image>`, `<video>`, `<audio>`, `<sticker>`, `<gif>`,
  `<document>`, `<contact>`, `<location>`, `<poll>`, `<other>` or `<deleted>`. Text after a media
  placeholder is its caption. CHATCOM never reads the media itself.
- **Reactions:** at the end of the message, `(reactions: 😂 Mike, ❤️ Dave, 👍 3)`. A name is one person;
  a number is a count with no names; `👍 Mike ×2` is both.
- **A quote:** `> Mike: original text` on the line before the message that replies to it. The name must be a
  speaker (list them in `# speakers:`); otherwise the whole line is the quoted text. More quoted
  lines start `>` and two spaces. `> text` with no name is a quote from someone not named.

## Limits

- UTF-8 text, at most 64 MB.
- More than 2% of message lines that do not read as messages stops the import
  (`CHATCOM_E_UNPARSED_LINES`). The lines go to a report under the show's `logs/`, not to the screen.
- A header, date or time that is wrong stops the import with `CHATCOM_E_TRANSCRIPT_INVALID` and the line
  number.
- Phone numbers in names and text are masked, as for WhatsApp.
