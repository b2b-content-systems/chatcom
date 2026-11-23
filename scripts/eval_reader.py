"""Score a screenshot reader against hand-checked transcripts.

    python scripts/eval_reader.py CASES_DIR --reader replay
    python scripts/eval_reader.py CASES_DIR --reader gemini --accept-data-flow   # paid calls, your own keys

Each case folder holds images/, gold.txt and (for --reader replay) reads.json.
Real screenshots and their scores stay on your machine: never commit them.
See docs/reader-eval.md."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from chatcom import reader_eval  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("cases", type=Path)
    p.add_argument("--reader", choices=["replay", "gemini"], default="replay")
    p.add_argument("--reader-model", help="one Gemini model (default: the chain chatcom uses)")
    p.add_argument("--tz", default="America/New_York")
    p.add_argument("--operator", default="Dave", help="the name of the phone owner in the gold transcripts")
    p.add_argument("--json", action="store_true")
    p.add_argument("--cache", type=Path, default=Path.home() / ".chatcom" / "eval-cache",
                   help="where paid reads are kept, so a re-run never pays twice (outside the repository)")
    p.add_argument("--accept-data-flow", action="store_true",
                   help="you have read where the screenshots go (--reader gemini) and agree")
    a = p.parse_args(argv)
    if a.reader == "gemini" and not a.accept_data_flow:
        print("The screenshots in these cases go to Google (Gemini) with your GEMINI_API_KEY, and to Anthropic with "
              "your ANTHROPIC_API_KEY if Gemini cannot answer. These are paid calls. Run again with "
              "--accept-data-flow to go ahead.", file=sys.stderr)
        return 6
    if not a.cases.is_dir():
        print(f"no folder at {a.cases}", file=sys.stderr)
        return 2
    cases = sorted(c for c in a.cases.iterdir() if (c / "gold.txt").is_file())
    if not cases:
        print(f"no cases in {a.cases} (a case is a folder with images/ and gold.txt)", file=sys.stderr)
        return 2
    scores = []
    for c in cases:
        try:  # one bad case is its own row; the others still run
            if a.reader == "replay":
                from chatcom.adapters.readers.replay import ReplayReader
                reader, cache = ReplayReader(c / "reads.json"), None
            else:
                from chatcom.adapters.readers.gemini import GeminiReader
                reader, cache = GeminiReader(model=a.reader_model), a.cache
            scores.append(reader_eval.run_case(c, reader, tz=a.tz, operator=a.operator, cache_dir=cache))
        except Exception as e:  # noqa: BLE001 - reported per case
            scores.append(reader_eval.failed(c, e))
    print(reader_eval.to_json(scores) if a.json else reader_eval.table(scores))
    return 0


if __name__ == "__main__":
    sys.exit(main())
