#!/usr/bin/env python3
"""Remove IDE/LLM vendor names from commit messages.

Used by infra/hooks/prepare-commit-msg and one-off history rewrites.
Commit messages must describe Matrix changes only — never name Cursor, Claude,
or other authoring tools.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

COAUTHOR_RE = re.compile(
    r"^Co-authored-by:\s*(Cursor|Claude|cursoragent@cursor\.com).*$",
    re.IGNORECASE,
)
REPLACEMENTS: tuple[tuple[str, str], ...] = (
    (r"Cursor CLI", "subscription CLI"),
    (r"Cursor LLM backend", "auto LLM backend"),
    (r"Cursor LLM", "auto LLM"),
    (r"Cursor Auto", "auto backend"),
    (r"Claude Code subscription", "subscription auth"),
    (r"Claude Code", "subscription auth"),
    (r"claude_agent_sdk", "agent SDK"),
    (r"Claude Opus", "Opus"),
    (r"Claude Haiku", "Haiku"),
    (r"Claude Sonnet", "Sonnet"),
    (r"docs\(claude\):", "docs:"),
    (r"\bCursor\b", ""),
    (r"\bClaude\b", ""),
    (r"\bclaude\b", ""),
)


def rewrite(msg: str) -> str:
    lines: list[str] = []
    for line in msg.splitlines():
        stripped = line.strip()
        if not stripped:
            lines.append("")
            continue
        if COAUTHOR_RE.match(stripped):
            continue
        text = line
        for pattern, repl in REPLACEMENTS:
            text = re.sub(pattern, repl, text, flags=re.IGNORECASE)
        text = re.sub(r",\s*,", ",", text)
        text = re.sub(r"\s{2,}", " ", text)
        text = re.sub(r":\s+,", ": ", text)
        text = re.sub(r",\s+\)", ")", text)
        text = text.strip()
        if text:
            lines.append(text)
    body = "\n".join(lines)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return (body + "\n") if body else "\n"


def main() -> None:
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
        path.write_text(rewrite(path.read_text()), encoding="utf-8")
    else:
        sys.stdout.write(rewrite(sys.stdin.read()))


if __name__ == "__main__":
    main()
