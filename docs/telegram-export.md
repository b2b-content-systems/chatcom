# Exporting a Telegram chat

CHATCOM reads Telegram Desktop's **machine-readable JSON** export. The phone app cannot export a chat; use
Telegram Desktop (Windows, macOS or Linux).

1. Open the group in Telegram Desktop.
2. Open the menu (⋮) and choose **Export chat history**.
3. Untick photos, videos, voice messages, stickers and files. CHATCOM never reads media, and the export is much
   smaller without it.
4. Set **Format** to **Machine-readable JSON**. (HTML is not read.)
5. Pick the date range and export. Telegram makes a folder named `ChatExport_<date>` holding `result.json`.

Pass the folder, or the `result.json` inside it:

```bash
chatcom new ~/Downloads/Telegram\ Desktop/ChatExport_2026-01-16 --operator "Dave" --look sitcom
```

For a later week, export again and save the folder into the show's inbox (`~/.chatcom/<show>/inbox/`).

## A whole-account export

**Settings → Advanced → Export Telegram data** exports every chat at once. CHATCOM reads only the chat you name
with `--thread`, and never your profile, contacts or other chats. Without `--thread` it lists the chats
(`CHATCOM_E_THREAD_REQUIRED`). Exporting just the one chat (above) is simpler and keeps everything else on
your computer.

## What is kept

- Every message with its sender, exact time, text, and whether it was edited.
- Media becomes a placeholder kind (image, video, audio, sticker, GIF, file, contact, location, poll).
- Reactions, with the names Telegram lists and a count for the rest.
- Replies: the reply points at the message it answers, with the start of its text as a quote.
- Joins, pins, title changes and other service lines are dropped.
- Phone numbers in names and text are masked, as for WhatsApp.
