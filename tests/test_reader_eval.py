"""Step 2.5 PR 7: the reader eval harness."""
import json
import shutil
import subprocess
import sys

import pytest

from chatcom import reader_eval as E
from chatcom.adapters import transcript as T
from chatcom.adapters.readers.replay import ReplayReader
from conftest import ROOT, TZ

CASES = ROOT / "tests" / "fixtures" / "eval"
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")


def msgs(text):
    return T.parse("# source: paste\n# speakers: A, B\n--- 2026-01-09 ---\n" + text, TZ).messages


# Value: protects=the eval scores mean what docs/reader-eval.md says; fails_when=a metric counts the wrong thing;
# why_new=new harness; seam=none; no_claim=any real reader's score (not stored in the repo)

def test_identical_transcripts_score_perfectly():
    g = msgs("A: hi\nB: yo\nA: <image>\n")
    assert E.compare(g, g) == {"gold": 3, "found": 3, "recall": 1.0, "precision": 1.0, "sender": 1.0,
                               "order": 1.0, "dates": 1.0}


def test_each_metric_counts_its_own_mistake():
    gold = msgs("A: one\nB: two\nA: three\nB: four\n")
    wrong_sender = msgs("A: one\nA: two\nA: three\nB: four\n")
    s = E.compare(wrong_sender, gold)
    assert s["recall"] == 0.75 and s["sender"] == 0.75 and s["order"] == 1.0
    missing = msgs("A: one\nA: three\nB: four\n")
    s = E.compare(missing, gold)
    assert s["recall"] == 0.75 and s["precision"] == 1.0
    extra = msgs("A: one\nB: two\nB: two\nA: three\nB: four\n")
    assert E.compare(extra, gold)["precision"] == 0.8
    swapped = msgs("A: three\nB: four\nA: one\nB: two\n")
    assert E.compare(swapped, gold)["order"] == 0.5
    case_noise = msgs("A: ONE!\nB: two\nA: three\nB: four\n")
    assert E.compare(case_noise, gold)["recall"] == 1.0  # identity text: case and punctuation do not count


@needs_ffmpeg
def test_the_fixture_case_reports_the_carried_name_it_gets_wrong():
    s = E.run_case(CASES / "lake", ReplayReader(CASES / "lake" / "reads.json"), tz=TZ, operator="Dave")
    # "not me" shows no name in the third screenshot: the join carries Theo's; gold says Mike
    assert (s.gold, s.found, s.recall, s.sender, s.order, s.dates) == (11, 11, 0.909, 0.909, 1.0, 1.0)


@needs_ffmpeg
def test_the_script_prints_a_table_and_json():
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "eval_reader.py"), str(CASES)],
                         capture_output=True, text=True, check=True).stdout
    assert out.startswith("| case | gold | found |") and "| lake | 11 | 11 |" in out
    js = subprocess.run([sys.executable, str(ROOT / "scripts" / "eval_reader.py"), str(CASES), "--json"],
                        capture_output=True, text=True, check=True).stdout
    assert json.loads(js)[0]["case"] == "lake"
