import json

import pytest

from chatcom import app
from chatcom.core import bible as bible_ops
from chatcom.core.errors import ChatcomError
from chatcom.core.schema import NAMES, problems, schema
from jsonschema import Draft202012Validator


@pytest.mark.parametrize("name", NAMES)
def test_schemas_are_valid_json_schema(name):
    Draft202012Validator.check_schema(schema(name))


def _bible():
    return bible_ops.new_bible(slug="lake-house", title=None, look="sitcom", operator_sender="Dave",
                               timezone="America/New_York", created="2026-01-16T00:00:00+00:00",
                               cast=["Dave", "Mike"], chorus=["Sam"], photos={"Dave": "/x/dave.jpg"}, no_photo=["Mike"])


def test_new_bible_and_cast_sheet_validate():
    b = _bible()
    assert b["cast"][0]["source"] == "photo" and b["cast"][1]["reason"] == "operator_choice"
    assert problems("cast", bible_ops.new_cast_sheet(b, {"Dave": "/x/dave.jpg"})) == []


def test_setup_template_validates():
    tpl = {k: v for k, v in app.setup_template().items()}
    assert problems("setup", tpl) == []


def test_unknown_key_is_an_error_and_underscore_is_a_comment():
    b = _bible()
    b["_note"] = "a comment"
    assert problems("bible", b) == []
    b["show"]["titel"] = "typo"
    assert any("titel" in p["message"] for p in problems("bible", b))


def test_newer_schema_is_refused_with_migrate_hint():
    b = _bible()
    b["schema_version"] = 2
    with pytest.raises(ChatcomError) as e:
        bible_ops.validate(b)
    assert e.value.code == "CHATCOM_E_SCHEMA_NEWER"


def test_hand_edit_errors_name_the_path():
    b = _bible()
    b["cast"][0]["role"] = "x" * 40
    with pytest.raises(ChatcomError) as e:
        bible_ops.load(json.dumps(b))
    assert "$.cast[0].role" in e.value.what


def test_message_schema_accepts_parser_output():
    from chatcom.adapters import whatsapp
    from conftest import WA
    text, _ = whatsapp.read_export(WA / "ios_en_US_12h.txt")
    for m in whatsapp.parse(text, "America/New_York").messages:
        assert problems("message", m) == []


def test_every_error_code_is_documented():
    from pathlib import Path
    from chatcom.core.errors import CODES
    doc = (Path(__file__).resolve().parent.parent / "docs" / "errors.md").read_text("utf-8")
    missing = [c for c in CODES if f"## {c.lower()}" not in doc]
    assert not missing, missing
