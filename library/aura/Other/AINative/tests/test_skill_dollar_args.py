#!/usr/bin/env python3
"""No unescaped `$<digit>` in a SKILL.md or command body.

When a skill or slash command is invoked with arguments, Claude Code replaces
`$N` in the body with the N-th argument (0-indexed) before the model reads it.
A dollar amount is indistinguishable from a positional: invoked with ~20 words,
`eng:build-aurie-cost-report` read `$2,287.50` as "since-beginning,287.50".
Nothing errors and the file looks right to its author, so only a test sees it.

Rule, probed on Claude Code 2.1.285 (slash command and Skill tool alike):

- `$N` is replaced when N < the argument count; out-of-range `$N` is left as is,
  so the risk grows with how many arguments a caller passes. `$0` breaks on any.
- Digits are read greedily (`$10` is argument 10, never argument 1 + "0").
- Code spans and fenced blocks are substituted too — an awk `$2` included.
- `\\$` is the escape: it reaches the model as a plain `$`, with or without
  arguments. `${1}` is untouched; `$$1` becomes `$` + argument 1.
- Frontmatter is not part of the injected body, and `references/` files are
  read with the Read tool, raw — neither is substituted.

`$ARGUMENTS` / `$ARGUMENTS[N]` are substituted as well, but they are only ever
written on purpose, so they are not flagged here.

Stdlib only, no network.
"""
from __future__ import annotations

import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
FILES = sorted((REPO / "plugins").rglob("SKILL.md")) + sorted(
    (REPO / "plugins").glob("*/commands/*.md")
)

UNESCAPED_POSITIONAL = re.compile(r"(?<!\\)\$\d")

# A file that relies on `$1`-style arguments on purpose goes here, with the
# reason. Never to excuse a dollar amount — escape it as `\$` instead.
ALLOWLIST: dict[str, str] = {}


def body_of(text: str) -> tuple[int, str]:
    """Return (first body line number, body), frontmatter excluded."""
    lines = text.split("\n")
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return i + 2, "\n".join(lines[i + 1 :])
    return 1, text


def hits(text: str) -> list[tuple[int, str]]:
    start, body = body_of(text)
    return [
        (start + n, line.strip())
        for n, line in enumerate(body.split("\n"))
        if UNESCAPED_POSITIONAL.search(line)
    ]


class DetectorSelfTest(unittest.TestCase):
    def test_catches_the_incident_shape(self):
        doc = "---\nname: x\n---\nIt was $2,287.50, of which LLM $1,266.\n"
        self.assertEqual([n for n, _ in hits(doc)], [4])

    def test_catches_zero_code_and_shell_positionals(self):
        for line in ("a well-formed $0", "`$0.01/min`", "awk '{print $2}'", "$$1"):
            self.assertTrue(hits(f"---\nname: x\n---\n{line}\n"), line)

    def test_escape_and_frontmatter_are_clean(self):
        doc = "---\nname: x\ndescription: costs $5\n---\nIt was \\$2,287.50 · ${1} · $ARGUMENTS\n"
        self.assertEqual(hits(doc), [])


class NoUnescapedDollarDigit(unittest.TestCase):
    def test_files_found(self):
        self.assertGreater(len(FILES), 100)

    def test_bodies(self):
        bad = []
        for path in FILES:
            rel = str(path.relative_to(REPO))
            if rel in ALLOWLIST:
                continue
            for n, line in hits(path.read_text(encoding="utf-8")):
                bad.append(f"{rel}:{n}: {line[:120]}")
        self.assertFalse(
            bad,
            "`$<digit>` is replaced by a skill argument at run time; write `\\$` "
            "instead:\n" + "\n".join(bad),
        )


if __name__ == "__main__":
    unittest.main()
