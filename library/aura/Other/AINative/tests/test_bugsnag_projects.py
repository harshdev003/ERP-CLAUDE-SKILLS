#!/usr/bin/env python3
"""One Bugsnag slug → suspect-repo mapping, five copies and a prose mirror.

**The rule.** `PROJECTS[slug].repo` is the repo `eng:code-investigator` clones and
greps (Mode 2.5). A wrong value there does not raise: the subagent searches the
wrong codebase and returns a confident "no matching code found", which reads
exactly like a clean investigation. So the mapping is verified against Bugsnag's
own `api_key` per project rather than inferred from the slug.

**The regression this pins.** `aura-ios` / `aura-android` mapped to
`AuraHealth/AuraConsumerApp` — Aurie's Swift app — while carrying `stack`
`react-native`, a contradiction on a single line. Their api_keys are committed in
`AuraHealth/Aura` (`ios/Aura/Info.plist`, `android/app/src/main/AndroidManifest.xml`).
Three months, five copies, no error. ENG-1169.

**Why a test and not another `diff -q`.** Two reasons, and CLAUDE.md's *The drift
guards compare copies, never implementations* names both:

1. A byte-diff of `projects.py` is green whenever the copies agree — including when
   they agree on a wrong value, which is the state this test was written to end.
   It also cannot see that `plugins/eng/skills/investigate-openai-trace/scripts/projects.py`
   shares the filename and nothing else; that module is excluded here by shape,
   not by a path exclusion that a rename would defeat.
2. The mapping is implemented **twice** — in Python, and as a prose table in
   `references/app-family-routing.md` whose sync procedure was a numbered list of
   manual steps. `test_reference_table_matches_projects` parses the table and holds
   it to `PROJECTS` row for row, which is the difference between a checked mirror
   and a stale one.

**The load-bearing assertion is `test_stack_and_repo_do_not_contradict`.** Every
other property here held while the bug shipped: all five copies agreed, every slug
resolved, the doc matched the code. What no check covered was a `react-native` slug
pointing at a repo with no React Native in it.

Stdlib `unittest`, no network, no keys. `projects.py` is pure data — importing it
runs no transport.
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import sys
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGINS = REPO_ROOT / "plugins"

# The canonical copy is the hub skill's — the one eng:code-investigator is spawned from.
CANONICAL = PLUGINS / "eng/skills/investigate-bugsnag-errors/scripts/projects.py"

# Every skill shipping the Bugsnag routing table. Skills are self-contained
# (CLAUDE.md principle 1), so each carries its own byte-identical copy.
# `test_family_membership_is_complete` fails when a sixth appears undeclared.
COPIES = [
    CANONICAL,
    PLUGINS / "eng/skills/report-bugsnag-errors/scripts/projects.py",
    PLUGINS / "eng/skills/search-bugsnag-by-user/scripts/projects.py",
    PLUGINS / "eng/skills/analyze-bugsnag-releases/scripts/projects.py",
    PLUGINS / "eng/skills/resolve-bugsnag-errors/scripts/projects.py",
]

REFERENCE_DOC = (
    PLUGINS / "eng/skills/investigate-bugsnag-errors/references/app-family-routing.md"
)

# The two entries whose repo is proven by a committed api_key match, and whose
# previous value was the defect. Kept explicit so a "cleanup" cannot quietly
# reintroduce the Aurie repo on a React Native row.
KEY_VERIFIED = {
    "aura-ios": "AuraHealth/Aura",
    "aura-android": "AuraHealth/Aura",
    "ai-aurie-ios": "AuraHealth/AuraConsumerApp",
    "aura-businessweb": "AuraHealth/BusinessWeb",
    "aura-investweb": "AuraHealth/InvestWeb",
}

# Repos with no React Native in them. A `react-native` slug pointing at one of
# these is the ENG-1169 shape regardless of which pair of names it involves.
NON_REACT_NATIVE_REPOS = {
    "AuraHealth/AuraConsumerApp",   # Aurie, Swift
    "AuraHealth/ConsumerAIDesktop",  # Aurie desktop, Electron
    "AuraHealth/AIDesktop",          # Swift
    "AuraHealth/AIDesktopPOC",       # Swift
}


def _load(path: pathlib.Path):
    name = f"_bugsnag_projects_{abs(hash(str(path)))}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # Registered before exec: @dataclass resolves its own module out of
    # sys.modules while processing the class body, and fails on a module that
    # is not there yet.
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return mod


def _is_routing_module(path: pathlib.Path) -> bool:
    """Membership by shape: a module exposing a PROJECTS map of AppFamily records.

    Filename is not the criterion — `investigate-openai-trace/scripts/projects.py`
    is an OpenAI project-discovery module that happens to share the name, and a
    path-based exclusion would stop working the day either file is renamed.
    """
    try:
        mod = _load(path)
    except Exception:
        return False
    projects = getattr(mod, "PROJECTS", None)
    if not isinstance(projects, dict) or not projects:
        return False
    sample = next(iter(projects.values()))
    return all(hasattr(sample, f) for f in ("family", "stack", "repo", "high_pii"))


class BugsnagProjectMappingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.canonical = _load(CANONICAL).PROJECTS

    # --- the regression -------------------------------------------------

    def test_key_verified_repos(self) -> None:
        """Each api_key-proven slug resolves to the repo holding that key."""
        for copy in COPIES:
            projects = _load(copy).PROJECTS
            for slug, repo in KEY_VERIFIED.items():
                with self.subTest(copy=copy.parent.parent.name, slug=slug):
                    self.assertEqual(projects[slug].repo, repo)

    def test_stack_and_repo_do_not_contradict(self) -> None:
        """No react-native slug may point at a repo with no React Native in it.

        The load-bearing assertion. It catches the ENG-1169 shape without naming
        the specific pair, so a future mis-paste between two other repos fails too.
        """
        for copy in COPIES:
            projects = _load(copy).PROJECTS
            for slug, fam in projects.items():
                if fam.stack != "react-native":
                    continue
                with self.subTest(copy=copy.parent.parent.name, slug=slug):
                    self.assertNotIn(
                        fam.repo,
                        NON_REACT_NATIVE_REPOS,
                        f"{slug} is stack=react-native but maps to {fam.repo}, "
                        "which contains no React Native. This is ENG-1169.",
                    )

    def test_no_slug_maps_to_a_repo_that_does_not_exist(self) -> None:
        """Every non-None repo is `AuraHealth/<Name>` with a plausible name.

        Cheap shape check — a typo'd repo behaves exactly like a wrong one
        (clone fails or greps nothing) and is equally silent.
        """
        for slug, fam in self.canonical.items():
            if fam.repo is None:
                continue
            with self.subTest(slug=slug):
                self.assertRegex(fam.repo, r"^AuraHealth/[A-Za-z0-9._-]+$")

    # --- the copies -----------------------------------------------------

    def test_copies_agree_with_canonical(self) -> None:
        """All five copies resolve every slug identically.

        Stronger than a byte-diff in one direction and weaker in another: it
        tolerates a docstring differing, and it compares what the skill actually
        reads rather than the bytes around it.
        """
        for copy in COPIES[1:]:
            projects = _load(copy).PROJECTS
            with self.subTest(copy=copy.parent.parent.name):
                self.assertEqual(
                    {s: (f.family, f.stack, f.repo, f.high_pii)
                     for s, f in projects.items()},
                    {s: (f.family, f.stack, f.repo, f.high_pii)
                     for s, f in self.canonical.items()},
                )

    def test_family_membership_is_complete(self) -> None:
        """A sixth routing module may not appear undeclared, under any filename."""
        found = {
            p for p in PLUGINS.rglob("*.py")
            if p.name == "projects.py" and _is_routing_module(p)
        }
        self.assertEqual(
            found,
            set(COPIES),
            "A Bugsnag routing table appeared or moved. Add it to COPIES so the "
            "mapping is held to the canonical, then propagate the current values.",
        )

    def test_openai_trace_module_is_not_a_member(self) -> None:
        """The same-named OpenAI module must stay out of the family.

        Pinned explicitly: reconciling it into this family would mean overwriting
        project-discovery logic with a Bugsnag routing table.
        """
        other = PLUGINS / "eng/skills/investigate-openai-trace/scripts/projects.py"
        self.assertTrue(other.exists(), "fixture moved; update this test")
        self.assertFalse(_is_routing_module(other))

    # --- the prose mirror -----------------------------------------------

    def test_reference_table_matches_projects(self) -> None:
        """The markdown routing table agrees with PROJECTS, row for row.

        Two implementations of one mapping in two languages — the exact case no
        `diff -q` can cover, and the reason the doc's sync procedure was a list of
        manual steps nobody was holding.
        """
        rows = {}
        for line in REFERENCE_DOC.read_text().splitlines():
            m = re.match(r"^\|\s*`([a-z0-9-]+)`\s*\|([^|]*)\|([^|]*)\|([^|]*)\|", line)
            if not m:
                continue
            slug, _family, _stack, repo = (g.strip() for g in m.groups())
            repo = repo.strip("`").strip()
            rows[slug] = None if repo == "TBD" else repo

        self.assertTrue(rows, "no table rows parsed — did the doc's format change?")
        self.assertEqual(
            set(rows), set(self.canonical),
            "the doc and PROJECTS cover different slugs",
        )
        for slug, repo in rows.items():
            with self.subTest(slug=slug):
                self.assertEqual(repo, self.canonical[slug].repo)


if __name__ == "__main__":
    unittest.main()
