#!/usr/bin/env python3
"""Tests for `scripts/build-agents-md.py`.

The generator exists because a blanket `s/Claude Code/Codex/` had already shipped
and corrupted twelve literals in AGENTS.md — `.Codex-plugin/marketplace.json`,
`~/.Codex/settings.json`, `.Codex/worktrees/`. None of those paths exist, and
nothing failed: the file still rendered, and a Codex agent following it would look
in the wrong place. So the assertions that matter here are the guard ones, not the
happy path — a generator that produces *some* output is not the thing being checked.

Run against the real CLAUDE.md, because a synthetic fixture cannot catch a rule that
has stopped matching the actual file, which is the failure mode with teeth.
"""
from __future__ import annotations

import importlib.util
import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "build-agents-md.py"


def _load():
    """Import the builder despite its hyphenated, non-importable filename."""
    spec = importlib.util.spec_from_file_location("build_agents_md", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Rules(unittest.TestCase):
    def setUp(self):
        self.mod = _load()

    def test_every_rule_matches_the_current_claude_md_exactly_once(self):
        """The whole point of the counted rule list. A rule that stops matching is
        how an un-retargeted AGENTS.md would ship silently."""
        src = self.mod.SOURCE.read_text(encoding="utf-8")
        for i, (count, old, _new) in enumerate(self.mod.RULES, 1):
            with self.subTest(rule=i, text=old[:60]):
                self.assertEqual(src.count(old), count)

    def test_no_rule_is_a_no_op(self):
        """A rule whose replacement equals its target does nothing but look like
        policy. Express 'deliberately left alone' in ALLOWED_CLAUDE_CODE instead."""
        for i, (_c, old, new) in enumerate(self.mod.RULES, 1):
            with self.subTest(rule=i):
                self.assertNotEqual(old, new)


class Guards(unittest.TestCase):
    """The rendered output, checked the way the blanket rewrite should have been.

    These call the script's own `guard_problems` rather than re-deriving the rules.
    A test that reimplements the guard is a second implementation that can drift
    from the one that actually runs — which is the failure mode CLAUDE.md describes
    under "The drift guards compare copies, never implementations"."""

    def setUp(self):
        self.mod = _load()
        self.src = self.mod.SOURCE.read_text(encoding="utf-8")
        self.out = self.mod.render()

    def test_the_rendered_output_passes_every_guard(self):
        self.assertEqual(self.mod.guard_problems(self.src, self.out), [])

    def test_guard_catches_a_corrupted_literal(self):
        """Failure injection: the guard has to FAIL on the exact corruption that
        shipped, or it is decoration. `.Codex-plugin/marketplace.json` was live on
        main and points at nothing."""
        broken = self.out.replace(".claude-plugin/", ".Codex-plugin/")
        problems = self.mod.guard_problems(self.src, broken)
        self.assertTrue(any(".Codex-plugin" in p for p in problems), problems[:3])

    def test_guard_catches_an_un_retargeted_host_mention(self):
        """A rule that stopped matching leaves 'Claude Code' where the text means
        'the host', and that must be loud rather than merely unconverted."""
        broken = self.out.replace("Codex loads `env` at startup",
                                  "Claude Code loads `env` at startup")
        problems = self.mod.guard_problems(self.src, broken)
        self.assertTrue(any("un-retargeted" in p for p in problems), problems[:3])

    def test_guard_catches_a_dropped_protected_literal(self):
        broken = self.out.replace("~/.claude/settings.json", "~/.codex/settings.json")
        problems = self.mod.guard_problems(self.src, broken)
        self.assertTrue(any("protected literal" in p for p in problems), problems[:3])

    def test_the_host_contrasting_sentence_is_preserved_verbatim(self):
        """'Hooks are Claude Code + Cursor only — Codex ignores hooks/' is TRUE as
        written for a Codex reader and becomes false if retargeted. This is the
        single sentence a blanket rewrite is most likely to invert."""
        sentence = "Hooks are Claude Code + Cursor only — Codex ignores `hooks/`"
        self.assertIn(sentence, self.src)
        self.assertIn(sentence, self.out)

    def test_prose_exemption_does_not_swallow_a_real_corruption(self):
        """The exemption exists so documentation may quote `.Codex-plugin`. It must
        stay scoped to those lines — a corruption anywhere else still has to fire."""
        self.assertTrue(self.mod.PROSE_ABOUT_THE_REWRITE)
        for marker in self.mod.PROSE_ABOUT_THE_REWRITE:
            with self.subTest(marker=marker[:40]):
                self.assertIn(marker, self.out)

    def test_output_carries_the_do_not_edit_banner(self):
        self.assertTrue(self.out.startswith(self.mod.BANNER))
        self.assertIn("do not edit by hand", self.out[:400])


class Committed(unittest.TestCase):
    """AGENTS.md on disk is what the generator produces."""

    def setUp(self):
        self.mod = _load()

    def test_committed_agents_md_is_not_stale(self):
        """The same assertion CI makes, so a local run catches it before the push."""
        self.assertEqual(
            self.mod.TARGET.read_text(encoding="utf-8"), self.mod.render(),
            "AGENTS.md is stale — run `python3 scripts/build-agents-md.py --write`",
        )

    def test_agents_md_carries_every_section_claude_md_has(self):
        """The drift that started this: 11 sections had gone missing from AGENTS.md.
        Headings are checked independently of the byte comparison because a heading
        is the unit a reader notices is absent."""
        import re
        def heads(text):
            return [l.rstrip() for l in text.split("\n") if re.match(r"^#{1,3} ", l)]
        src = heads(self.mod.SOURCE.read_text(encoding="utf-8"))
        out = heads(self.mod.TARGET.read_text(encoding="utf-8"))
        self.assertEqual(src, out)


if __name__ == "__main__":
    unittest.main()
