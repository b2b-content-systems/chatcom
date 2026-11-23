# Adding a chat adapter

An adapter turns one app's export into CHATCOM's message schema
([`src/chatcom/schemas/message.schema.json`](src/chatcom/schemas/message.schema.json)). Everything after that
(the window, the bible, the writer, casting, render) is shared, so an adapter is one module, one entry in the
registry and its fixtures. WhatsApp ([`src/chatcom/adapters/whatsapp.py`](src/chatcom/adapters/whatsapp.py)) is the
reference; [`docs/design/any-group-chat.md`](docs/design/any-group-chat.md) is the spec every source follows.

## What an adapter must do

1. **Read the export without unpacking media.** Read only the text. Never open, decode or upload attachments; a
   placeholder becomes a `media` kind (`image`, `video`, `audio`, …).
2. **Emit the schema, nothing else.** Every message has `id, sender, time, time_precision, text, media, deleted,
   edited, reply_to, reactions, quote, source`. `reply_to`, `reactions` and `quote` are `null` when the format does
   not carry them. `time_precision` says how much of the time the export really shows (`exact`, `minute`, `day`,
   `order`); `source.label` is the sender exactly as the export shows it, before aliases.
3. **Drop system lines** (joins, renames, encryption notices, missed calls). They are not messages.
4. **Resolve time in the show's zone.** Exports without an offset are local time; use the show's IANA zone and
   keep time moving forward through a DST fall-back (`chatcom.core.clock.localize`). Rows with no clock time get a synthetic time from `clock.Synthetic`.
5. **Hash messages the same way, with the show's key:** `chatcom.core.messages.message_hash(label, utc_minute,
   ordinal, text, media, deleted, key)` for timed rows, `context_hash(...)` for rows with no time. Hash the raw
   label, not the aliased name, so renaming never changes an id. The same chat imported twice must give the same
   ids.
6. **Protect people.** Mask a sender shown only as a phone number with `chatcom.core.masking.phone_label(number,
   key)`, and run message text (and quoted text) through `masking.mask_numbers(text, alias_map, key)`. No phone
   number may reach the writer.
7. **Report what did not parse.** Return the unparsed lines with their line numbers; the app fails above 2%
   and writes them to a local report, never to the screen.
8. **Fail with the error contract.** Raise `ChatcomError` with a code from `chatcom.core.errors.CODES` (add one
   and its anchor in [`docs/errors.md`](docs/errors.md) if needed). An adapter never prints a message's text.

## Registering the adapter

Add one `Adapter` in `src/chatcom/adapters/registry.py`: a name, a matcher per sniffing tier (`container` for zip
and JSON exports, `text` for text exports) and a `load(paths, ctx)` that returns `(ParseResult, display name)`.
A matcher reads only the start of a file (`registry.peek`), never a whole large export. Add your fixtures to the
sniff matrix in `tests/test_any_source_core.py`; a fixture that two adapters both claim must fail as ambiguous.

## Rules for the code

- Standard library only, or a dependency under an MIT-compatible licence (CI runs `scripts/check_licences.py`;
  GPL and AGPL fail). No provider SDK.
- Adapters live in `src/chatcom/adapters/`. Nothing in `src/chatcom/core/` may import them
  (`tests/test_architecture.py` enforces it).
- A format that needs the user's account token or session cookie (for example most Discord exporters) is out of
  scope: it breaks the app's terms.

## Fixtures and tests

- Every fixture is **synthetic**. Never commit a real chat, a real name or a real phone number (use 555-01xx).
  Generate fixtures from a script, like `tests/fixtures/whatsapp/make_fixtures.py`, so invisible characters are
  visible in review.
- Cover at least: two platforms or versions if they differ, three locales, 12- and 24-hour clocks, multi-line
  messages, system lines, media and deleted placeholders, invisible direction marks, and a file that should fail
  the unparsed check.
- Add a parametrized matrix test like `tests/test_whatsapp.py::test_matrix`, a schema check of the output, and a
  test that the same messages hash the same across two exports.
- Run `.venv/bin/python -m pytest -q` before opening a PR.
