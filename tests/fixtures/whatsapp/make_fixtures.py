"""Writes the WhatsApp fixture matrix. Every chat here is synthetic.

Run from this folder: python3 make_fixtures.py
The files are committed; this script documents exactly which bytes they hold
(narrow no-break spaces, direction marks, BOMs), which an editor would hide.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

HERE = Path(__file__).parent
NNBSP = " "   # iOS 17+ puts this before AM/PM
LRM = "‎"     # iOS marks system lines and placeholders with it
FIX = {}

FIX["ios_en_US_12h.txt"] = "\n".join([
    f"[1/15/26, 9:02:11{NNBSP}PM] Lake House: {LRM}Messages and calls are end-to-end encrypted. Only people in this chat can read, listen to, or share them.",
    f"[1/15/26, 9:02:11{NNBSP}PM] Lake House: {LRM}Dave created group “Lake House”",
    f"[1/15/26, 9:03:40{NNBSP}PM] Dave: ok who is actually coming in march",
    f"[1/15/26, 9:04:02{NNBSP}PM] Mike: me",
    f"[1/15/26, 9:04:05{NNBSP}PM] Mike: probably",
    f"[1/15/26, 9:05:30{NNBSP}PM] Theo: {LRM}image omitted",
    f"[1/15/26, 9:06:00{NNBSP}PM] Theo: here is the list",
    "1. dave",
    "2. mike (probably)",
    "3. me",
    f"[1/15/26, 9:07:15{NNBSP}PM] Sam: {LRM}This message was deleted.",
    f"[1/15/26, 9:08:44{NNBSP}PM] Dave: I said MARCH not may {LRM}<This message was edited>",
    f"[1/16/26, 7:30:01{NNBSP}AM] {LRM}‪+1 (555) 010-2233‬: who is this group",
    f"[1/16/26, 12:15:09{NNBSP}PM] Sam: {LRM}<attached: 00000042-PHOTO-2026-01-16-12-15-09.jpg>",
    f"[1/16/26, 12:15:40{NNBSP}PM] Lake House: {LRM}Mike changed the group name to “Lake House (Tentative)”",
    "",
])

FIX["ios_en_GB_24h.txt"] = "\n".join([
    f"[15/01/2026, 21:03:40] Dave: ok who is actually coming in march",
    f"[15/01/2026, 21:04:02] Mike: me",
    f"[15/01/2026, 21:05:30] Theo: {LRM}GIF omitted",
    f"[16/01/2026, 07:30:01] Sam: morning",
    f"[16/01/2026, 23:59:59] Dave: last one",
    "",
])

FIX["ios_de_DE.txt"] = "\n".join([
    f"[15.01.26, 21:03:40] Lea: Wer kommt im März?",
    f"[15.01.26, 21:04:02] Jonas: {LRM}Bild weggelassen",
    f"[15.01.26, 21:04:30] Mia: {LRM}Diese Nachricht wurde gelöscht.",
    f"[15.01.26, 21:05:00] Lea: Ok, ich mache eine Tabelle",
    "",
])

FIX["ios_pt_BR.txt"] = "\n".join([
    f"[15/01/2026 21:03:40] Ana: quem vai em março?",
    f"[15/01/2026 21:04:02] Bruno: {LRM}imagem ocultada",
    f"[15/01/2026 21:05:00] Ana: ok vou fazer uma planilha",
    "",
])

FIX["android_en_US_12h.txt"] = "\n".join([
    f"1/15/26, 9:02{NNBSP}PM - Messages and calls are end-to-end encrypted. Only people in this chat can read, listen to, or share them. Learn more.",
    f"1/15/26, 9:02{NNBSP}PM - Dave created group \"Lake House\"",
    f"1/15/26, 9:03{NNBSP}PM - Dave: ok who is actually coming in march",
    f"1/15/26, 9:04{NNBSP}PM - Mike: me",
    f"1/15/26, 9:04{NNBSP}PM - Mike: probably",
    f"1/15/26, 9:05{NNBSP}PM - Theo: <Media omitted>",
    f"1/15/26, 9:06{NNBSP}PM - Theo: here is the list",
    "1. dave",
    "2. mike (probably)",
    f"1/15/26, 9:07{NNBSP}PM - Sam: This message was deleted",
    f"1/15/26, 9:08{NNBSP}PM - Dave changed the subject from \"Lake House\" to \"Lake House: March?\"",
    f"1/15/26, 9:09{NNBSP}PM - Dave added Priya",
    f"1/15/26, 9:10{NNBSP}PM - Priya: hi!! what did I miss",
    f"1/15/26, 9:11{NNBSP}PM - Mike: location: https://maps.google.com/?q=44.1,-72.3",
    f"1/15/26, 9:12{NNBSP}PM - Dave: POLL:",
    "March 6 or March 13?",
    "OPTION: March 6 (2 votes)",
    "OPTION: March 13 (1 vote)",
    "",
])

FIX["android_de_DE_24h.txt"] = "\n".join([
    "15.01.26, 21:03 - Lea: Wer kommt im März?",
    "15.01.26, 21:04 - Jonas: <Medien ausgeschlossen>",
    "15.01.26, 21:05 - Lea hat Mia hinzugefügt",
    "15.01.26, 21:06 - Mia: Hallo!",
    "",
])

FIX["android_es_ES.txt"] = "\n".join([
    "15/1/26, 21:03 - Lucía: ¿quién viene en marzo?",
    "15/1/26, 21:04 - Pablo: <Multimedia omitido>",
    "15/1/26, 21:05 - Pablo: Se eliminó este mensaje.",
    "15/1/26, 21:06 - Lucía: vale, hago una hoja",
    "",
])

FIX["android_es_MX_12h.txt"] = "\n".join([
    "15/1/26, 9:03 p. m. - Lucía: ¿quién viene en marzo?",
    "15/1/26, 9:04 p. m. - Pablo: yo",
    "16/1/26, 8:15 a. m. - Lucía: buenos días",
    "",
])

FIX["android_ymd.txt"] = "\n".join([
    "2026-01-15, 21:03 - Ella: who is coming",
    "2026-01-15, 21:04 - Finn: me",
    "",
])

# Every day is 12 or under; the order only shows from time running forward.
FIX["ambiguous_dates_dmy.txt"] = "\n".join([
    "[03/01/2026, 10:00:00] Ava: day one",
    "[04/01/2026, 10:00:00] Ryan: day two",
    "[05/01/2026, 10:00:00] Ava: day three",
    "[06/01/2026, 10:00:00] Ryan: day four",
    "[01/02/2026, 10:00:00] Ava: a month later",
    "",
])

# America/New_York falls back at 2:00 EDT on 2025-11-02; 1:00-1:59 happens twice.
FIX["dst_fallback_new_york.txt"] = "\n".join([
    f"[11/2/25, 1:30:00{NNBSP}AM] Ava: still up",
    f"[11/2/25, 1:55:00{NNBSP}AM] Ryan: same",
    f"[11/2/25, 1:05:00{NNBSP}AM] Ava: wait my phone just went back an hour",
    f"[11/2/25, 1:40:00{NNBSP}AM] Ryan: free hour",
    f"[11/2/25, 2:10:00{NNBSP}AM] Ava: ok bed",
    "",
])

# The same person sends "k" three times in one minute; none may collapse.
FIX["same_minute_k.txt"] = "\n".join([
    "[1/15/26, 9:00:01 PM] Mike: k",
    "[1/15/26, 9:00:02 PM] Mike: k",
    "[1/15/26, 9:00:40 PM] Mike: k",
    "[1/15/26, 9:00:50 PM] Dave: k?",
    "",
])

FIX["invisible_marks.txt"] = "﻿" + "\n".join([
    f"{LRM}[1/15/26, 9:00:01 PM] ⁨Dave⁩: hello​ there",
    f"[1/15/26, 9:00:05 PM] Mike: ‏ok",
    "",
])

# Mostly not a WhatsApp export: must fail the 2% unparsed rule.
FIX["garbage.txt"] = "\n".join([
    "[1/15/26, 9:00:01 PM] Dave: one real line",
    "15 January 2026 9pm Dave said hi",
    "2026/01/15 21:00 Mike: wrong separator style",
    "[13/45/26, 9:00:01 PM] Theo: impossible date",
    "[1/15/26, 9:01:00 PM] Mike: another real line",
    "",
])

# Zips: iOS names the chat _chat.txt; Android names it after the chat.
ZIPS = {
    "ios_export.zip": [("_chat.txt", "ios_en_US_12h.txt"), ("00000042-PHOTO-2026-01-16-12-15-09.jpg", None)],
    "WhatsApp Chat - Lake House.zip": [("WhatsApp Chat with Lake House.txt", "android_en_US_12h.txt")],
}


def main() -> None:
    for name, text in FIX.items():
        (HERE / name).write_text(text, encoding="utf-8")
    for zname, members in ZIPS.items():
        with zipfile.ZipFile(HERE / zname, "w", zipfile.ZIP_DEFLATED) as zf:
            for arc, src in members:
                data = FIX[src].encode("utf-8") if src else b"\xff\xd8\xff\xe0not-a-real-jpeg"
                info = zipfile.ZipInfo(arc, date_time=(2026, 1, 16, 12, 0, 0))
                zf.writestr(info, data)
    print(f"wrote {len(FIX)} chats and {len(ZIPS)} zips")


if __name__ == "__main__":
    main()
