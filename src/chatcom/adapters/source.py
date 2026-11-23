"""What every source adapter returns: normalized messages plus the facts the
caller needs to judge the parse (unparsed lines, format, group name)."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field


@dataclass
class ParseResult:
    messages: list[dict]
    system_lines: int
    unparsed: list[tuple[int, str]]
    header_lines: int
    fmt: dict
    group_name: str | None
    masked: dict[str, str]
    adapter: str = "whatsapp"
    artifacts: dict = field(default_factory=dict)  # files an adapter wrote (screenshots: transcript.txt)

    @property
    def unparsed_rate(self) -> float:
        total = self.header_lines + len(self.unparsed)
        return len(self.unparsed) / total if total else 1.0

    def senders(self) -> Counter:
        return Counter(m["sender"] for m in self.messages)

    def describe(self) -> str:
        """One line for messages: the adapter and its format facts."""
        facts = ", ".join(str(v) for v in self.fmt.values() if v)
        return f"{self.adapter}" + (f" ({facts})" if facts else "")
