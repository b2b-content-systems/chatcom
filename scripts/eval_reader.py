"""Score a screenshot reader against hand-checked transcripts.

    python scripts/eval_reader.py CASES_DIR --reader replay
    python scripts/eval_reader.py CASES_DIR --reader gemini     # your own GEMINI_API_KEY; paid calls

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
    a = p.parse_args(argv)
    cases = sorted(c for c in a.cases.iterdir() if (c / "gold.txt").is_file())
    if not cases:
        print(f"no cases in {a.cases} (a case is a folder with images/ and gold.txt)", file=sys.stderr)
        return 2
    scores = []
    for c in cases:
        if a.reader == "replay":
            from chatcom.adapters.readers.replay import ReplayReader
            reader = ReplayReader(c / "reads.json")
        else:
            from chatcom.adapters.readers.gemini import GeminiReader
            reader = GeminiReader(model=a.reader_model)
        scores.append(reader_eval.run_case(c, reader, tz=a.tz, operator=a.operator))
    print(reader_eval.to_json(scores) if a.json else reader_eval.table(scores))
    return 0


if __name__ == "__main__":
    sys.exit(main())
