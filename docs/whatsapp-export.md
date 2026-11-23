# Exporting a WhatsApp chat

**iPhone:** open the chat, tap its name, scroll down, **Export Chat**, **Without Media**. You get a `.zip` holding `_chat.txt`.
**Android:** open the chat, ⋮, **More**, **Export chat**, **Without media**. You get `WhatsApp Chat with <name>.txt` (sometimes zipped).

Pass the `.zip` or the `.txt` as exported. CHATCOM reads only the chat text inside a zip; it never unpacks or reads media.

## What the parser understands
- iOS `[15/01/2026, 21:41:05] Name: text` and Android `15/01/2026, 21:41 - Name: text`
- 12- and 24-hour clocks (including `p. m.` and the narrow space newer exports put before AM/PM), dots, slashes or dashes, 2- or 4-digit years, year-first dates, no comma after the date
- day/month order, inferred over the whole file
- multi-line messages; system lines (joins, renames, the encryption notice) are dropped
- media, deleted and "edited" placeholders in English, German, Spanish, Portuguese and French
- times resolved in the show's timezone (`--tz`), including the repeated hour when clocks go back

If more than 2% of message lines do not parse, the run stops before any model call and writes the lines to a local report file (they are never printed). Tested formats are in `tests/fixtures/whatsapp/`; an export that fails is a bug worth reporting with the detected format line from the error (not the chat).

## What reaches the writer
Sender, time, text and a media marker per message. Location links are dropped. A sender who appears only as a phone number is shown to the writer as `Unnamed 1` until you name them with `aliases` in `setup.json`. Anyone in `exclude` is removed before any model call, and `@mentions` of them are blanked.
