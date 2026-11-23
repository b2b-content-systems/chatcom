import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"
WA = FIX / "whatsapp"
LAKE = ROOT / "examples" / "lake-house" / "_chat.txt"
TZ = "America/New_York"


@pytest.fixture(autouse=True)
def chatcom_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("CHATCOM_HOME", str(home))
    for var in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    return home


@pytest.fixture
def beats_s01e01():
    return json.loads((FIX / "writer" / "lake-house-S01E01.json").read_text("utf-8"))


@pytest.fixture
def lake_show():
    """A created show from the synthetic demo chat."""
    from chatcom import app
    show, _ = app.new_show(str(LAKE), {"operator_sender": "Dave", "slug": "lake-house"}, tz=TZ)
    return show
