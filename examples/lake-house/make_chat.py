"""Writes the synthetic demo chat for THE LAKE HOUSE BOYS (an iOS WhatsApp
export). Every member and line is made up. Run: python3 make_chat.py"""
from pathlib import Path

NNBSP, LRM = " ", "‎"
LINES = [
    # (date m/d/yy, time, sender, text)
    ("1/3/26", "8:12:04 PM", "Lake House", f"{LRM}Messages and calls are end-to-end encrypted. Only people in this chat can read, listen to, or share them."),
    ("1/3/26", "8:12:04 PM", "Lake House", f"{LRM}Dave created group “Lake House”"),
    ("1/3/26", "8:13:10 PM", "Dave", "ok it is happening. lake house. march. no excuses this year"),
    ("1/3/26", "8:13:55 PM", "Theo", "I will make a spreadsheet"),
    ("1/3/26", "8:14:02 PM", "Mike", "probably in"),
    ("1/3/26", "8:14:40 PM", "Sam", "k"),
    ("1/3/26", "8:15:12 PM", "Dave", "probably is not a word mike"),
    ("1/3/26", "8:15:30 PM", "Mike", "it is literally a word"),
    ("1/3/26", "8:15:41 PM", "Theo", "lol"),
    ("1/3/26", "8:16:20 PM", "Priya", "wait which march"),
    ("1/3/26", "8:16:44 PM", "Dave", "THIS march priya"),
    ("1/4/26", "10:02:13 AM", "Theo", "spreadsheet is live. tabs: dates, rooms, food, who owes who, excuses"),
    ("1/4/26", "10:03:01 AM", "Mike", "why is there an excuses tab"),
    ("1/4/26", "10:03:20 AM", "Theo", "historical data"),
    ("1/4/26", "10:03:31 AM", "Priya", "😂😂"),
    ("1/4/26", "10:03:35 AM", "Dave", "💀"),
    ("1/5/26", "7:45:00 PM", "Dave", "options: march 6 or march 13. vote"),
    ("1/5/26", "7:46:10 PM", "Priya", "6"),
    ("1/5/26", "7:46:12 PM", "Theo", "13"),
    ("1/5/26", "7:47:00 PM", "Mike", "either probably"),
    ("1/5/26", "7:47:30 PM", "Sam", "k"),
    ("1/5/26", "7:48:02 PM", "Dave", "sam k is not a date"),
    ("1/5/26", "7:48:40 PM", "Sam", "13"),
    ("1/5/26", "7:49:00 PM", "Dave", "13 it is. deposit is $400 due friday the 9th. $80 each"),
    ("1/6/26", "9:30:11 PM", "Mike", f"{LRM}image omitted"),
    ("1/6/26", "9:30:40 PM", "Mike", "found a boat rental. pontoon. it has a slide"),
    ("1/6/26", "9:31:02 PM", "Priya", "a SLIDE"),
    ("1/6/26", "9:31:30 PM", "Theo", "adding a boat tab"),
    ("1/6/26", "9:32:00 PM", "Dave", "mike you have not paid the deposit but you found a boat"),
    ("1/6/26", "9:32:20 PM", "Mike", "priorities"),
    ("1/6/26", "9:32:31 PM", "Sam", "lol"),
    ("1/7/26", "6:05:44 PM", "Theo", "deposit status: dave paid, priya paid, theo paid. mike and sam: pending"),
    ("1/7/26", "6:06:30 PM", "Sam", "k"),
    ("1/7/26", "6:07:10 PM", "Theo", "k is not a payment method"),
    ("1/7/26", "6:07:22 PM", "Priya", "🤣"),
    ("1/8/26", "11:58:02 PM", "Mike", "real question. is the lake cold in march"),
    ("1/8/26", "11:58:40 PM", "Dave", "it is a lake in march mike"),
    ("1/8/26", "11:59:10 PM", "Mike", "so the slide is decorative"),
    ("1/8/26", "11:59:20 PM", "Theo", "lmao"),
    ("1/9/26", "4:50:00 PM", "Dave", "deposit due in 10 min. mike. sam."),
    ("1/9/26", "4:58:31 PM", "Sam", "paid"),
    ("1/9/26", "4:59:59 PM", "Mike", "paid!! (i was at the gym)"),
    ("1/9/26", "5:00:20 PM", "Theo", "mike's gym is the excuses tab now"),
    ("1/9/26", "5:00:31 PM", "Priya", "😂"),
    ("1/11/26", "2:14:09 PM", "Dave", "made the playlist. 41 songs. all bangers"),
    ("1/11/26", "2:16:40 PM", "Priya", "it is 30 songs from one album"),
    ("1/11/26", "2:17:02 PM", "Dave", "a very good album"),
    ("1/12/26", "8:20:15 PM", "Theo", "room assignments are in the spreadsheet. no trades"),
    ("1/12/26", "8:21:00 PM", "Mike", "why am I on the couch"),
    ("1/12/26", "8:21:30 PM", "Theo", "see excuses tab, row 4"),
    ("1/12/26", "8:21:44 PM", "Dave", "💀💀"),
    ("1/13/26", "9:10:00 AM", "Priya", "my sister might come is that ok"),
    ("1/13/26", "9:12:00 AM", "Dave", "the more the merrier"),
    ("1/13/26", "9:12:30 AM", "Theo", "she gets the couch, mike gets the floor"),
    ("1/13/26", "9:12:41 AM", "Mike", "this is a hate crime against mike"),
    ("1/13/26", "9:12:50 AM", "Sam", "lol"),
    ("1/14/26", "7:40:00 PM", "Mike", "update: I can't do the 13th anymore"),
    ("1/14/26", "7:40:30 PM", "Dave", "MIKE"),
    ("1/14/26", "7:41:00 PM", "Mike", "jk"),
    ("1/14/26", "7:41:10 PM", "Mike", "probably jk"),
    ("1/14/26", "7:41:20 PM", "Theo", "adding to excuses tab"),
    ("1/14/26", "7:41:33 PM", "Priya", "🤣🤣"),
    ("1/15/26", "10:22:02 PM", "Dave", "who is driving"),
    ("1/15/26", "10:23:10 PM", "Sam", "me"),
    ("1/15/26", "10:23:30 PM", "Priya", "sam said a word that wasn't k"),
    ("1/15/26", "10:23:40 PM", "Theo", "historic"),
    ("1/15/26", "10:23:45 PM", "Mike", "lol"),
    ("1/16/26", "6:00:00 PM", "Dave", "8 weeks out. nobody cancel. I mean it"),
    ("1/16/26", "6:01:02 PM", "Mike", "probably won't"),
]


def main() -> None:
    out = []
    for d, t, who, text in LINES:
        t = t.replace(" ", NNBSP)
        out.append(f"[{d}, {t}] {who}: {text}")
    Path(__file__).with_name("_chat.txt").write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"wrote {len(LINES)} lines")


if __name__ == "__main__":
    main()
