"""Ports and adapters: the core imports no adapter, no provider SDK and no network."""
import ast
from pathlib import Path

CORE = Path(__file__).resolve().parent.parent / "src" / "chatcom" / "core"
BANNED = ("chatcom.adapters", "chatcom.app", "chatcom.cli", "urllib", "http", "socket", "ssl", "requests",
          "anthropic", "google", "subprocess")


def test_core_imports_no_adapter_or_network():
    bad = []
    for f in sorted(CORE.glob("*.py")):
        for node in ast.walk(ast.parse(f.read_text("utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for n in names:
                if any(n == b or n.startswith(b + ".") for b in BANNED):
                    bad.append(f"{f.name}: {n}")
    assert not bad, bad
