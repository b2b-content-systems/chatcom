
## Design System
Read DESIGN.md before visual or UI work: it defines the fonts, colors, spacing, and
aesthetic direction. Ask the user before departing from it. When reviewing or QA-ing
UI, flag code that doesn't match DESIGN.md.

## Testing
Python engine in `src/chatcom/`, tests in `tests/` (pytest). Set up once with
`python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'`, then run
`.venv/bin/python -m pytest -q`. CI runs the same suite on Python 3.10 and 3.13,
plus `scripts/check_licences.py`. Fixtures are synthetic; never commit a real chat.
