# Reader eval

The reader eval measures how close a screenshot reader gets to the real chat. It reads a set of screenshots,
joins them with CHATCOM's fixed rules, and compares the result with a transcript a person checked by hand.

```bash
python scripts/eval_reader.py CASES_DIR --reader replay
python scripts/eval_reader.py CASES_DIR --reader gemini --accept-data-flow
```

`--reader gemini` sends the screenshots to Google with your `GEMINI_API_KEY`, and to Anthropic with your
`ANTHROPIC_API_KEY` if Gemini cannot answer. These are paid calls. Reads are cached in `~/.chatcom/eval-cache/`
(outside the repository), so a re-run never pays twice. The eval reads through the same pipeline as an import:
clean copies, the split for long replies, the join.

## A case

A case is a folder:

| File | What it is |
| --- | --- |
| `images/` | The screenshots, as the phone saved them |
| `gold.txt` | The true chat as a transcript ([transcript-format.md](transcript-format.md)), checked by a person |
| `reads.json` | Recorded reads, only for `--reader replay` |
| `case.json` | Optional: `operator`, `speakers`, `reverse`, `anchor`, as the import would get them |

The repository has one synthetic case, `tests/fixtures/eval/lake`, so CI runs the harness. Real screenshots and
their scores stay on the machine that ran them. Never commit a real chat.

## What it reports

Messages are paired by their text (case, punctuation and spacing do not count), whatever their order. When the
same text comes from several people ("lol", a photo), the pairing prefers the same sender.

| Column | Meaning |
| --- | --- |
| `recall` | Gold messages found with the right sender |
| `precision` | Found messages that are in gold with the right sender |
| `sender` | Of the paired messages, the share with the right sender |
| `order` | Of the paired messages, the longest run in gold's order, as a share |
| `dates` | Of the paired messages gold dates, the share on the right day |
| `phones` | Found messages that still hold a phone-length run of digits. Must be 0 |

The synthetic case scores below 1.000 on purpose. One bubble shows no name, so the join gives it the name above
it, and gold says otherwise. That is the kind of miss the eval exists to show.

## Release gate

The reader eval and the writer eval gate the v0.1.0 release, not casting work. The targets before release, on at
least 10 real cases across 3 or more apps, both light and dark mode:

- recall and precision at or above 0.95 for each reader;
- order at or above 0.98;
- `phones` at 0 in every case.

These are targets, not results. No score is published until a real run is done and checked.
