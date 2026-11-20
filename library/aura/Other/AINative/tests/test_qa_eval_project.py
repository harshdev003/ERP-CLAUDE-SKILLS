#!/usr/bin/env python3
"""QA/eval skills bill to `Evaluations [Production]`, never a product project.

The defect this guards
----------------------
`eng:qa-conversation` ran its eval inference through
`OPENAI_CONVERSATION_API_KEY`, i.e. a key inside the **product** project
`Conversation [Production]`. Nothing errored, and no cost report could show it:
OpenAI bills per project, never per API key, so the eval spend was
indistinguishable from product spend.

The damage was downstream. `eng:analyze-openai-cost-per-user` takes a project's
authoritative dollars and divides them across the users it finds in that
project's ledger rows. Eval traffic never reaches the `/ai/llm-calls` ledger, so
its dollars were divided across the *real* users — on 2026-08-21 that put $837
into the per-user store and moved reported per-user cost from a ~$0.06-0.09
baseline to $0.318.

Why `Evaluations` specifically, and not just "some non-product project"
----------------------------------------------------------------------
`Evaluations` is the sole member of `INTERNAL_PROJECT_BASE_NAMES`, which is the
*only* thing that routes a project's dollars to `skipped_project_dollars` —
excluded from per-user attribution **and** from the attribution coverage ratio.

Any other project (the standalone `Claude` project included) is *walked*: its
dollars resolve to no user and count as uncovered. That is not a cosmetic
difference. `store.py` refuses to write a day below
`MIN_ATTRIBUTION_COVERAGE_PCT = 75.0`, and on 2026-08-21 the eval spend was $837
of a $1,199.86 org day — 70%. Routing that anywhere non-excluded would have
driven coverage to ~30% and blocked the whole day's cost store. So the
assertions below pin the *destination*, not merely "moved off Conversation".

Filename-independent by design
------------------------------
The family is discovered by walking every QA/eval skill's scripts, so a new
script added to one of these skills is covered automatically and a rename cannot
quietly widen the set — the failure mode CLAUDE.md describes under *The drift
guards compare copies, never implementations*.
"""
from __future__ import annotations

import pathlib
import re
import unittest

from reader_harness import REPO_ROOT

EVAL_KEY = "OPENAI_EVALUATIONS_API_KEY"

# Skills whose OpenAI traffic is our own testing, not product traffic.
QA_SKILLS = ("qa-prompt", "qa-conversation")

# Product-project keys that must never appear in a QA skill. `OPENAI_ADMIN_API_KEY`
# is absent deliberately — it reads org-level cost/usage and spends nothing.
FORBIDDEN_KEY_RE = re.compile(
    r"OPENAI_(?!EVALUATIONS_API_KEY|ADMIN_API_KEY)[A-Z0-9_]*_API_KEY"
)


def _skill_dir(name: str) -> pathlib.Path:
    return REPO_ROOT / "plugins" / "eng" / "skills" / name


def _sources(name: str) -> list[pathlib.Path]:
    return sorted(
        f for f in _skill_dir(name).rglob("*")
        if f.is_file() and f.suffix in (".py", ".md")
    )


class QaSkillsBillToEvaluationsTests(unittest.TestCase):
    def test_the_qa_skills_exist(self):
        """Guard the guard: a rename must fail here, not silently pass."""
        for name in QA_SKILLS:
            with self.subTest(skill=name):
                self.assertTrue(_skill_dir(name).is_dir(),
                                f"{name} missing — update QA_SKILLS")

    def test_no_product_project_key_anywhere_in_a_qa_skill(self):
        for name in QA_SKILLS:
            for f in _sources(name):
                found = set(FORBIDDEN_KEY_RE.findall(f.read_text(encoding="utf-8")))
                with self.subTest(file=str(f.relative_to(REPO_ROOT))):
                    self.assertEqual(
                        found, set(),
                        f"{f.name} references {sorted(found)} — QA inference must "
                        f"bill to {EVAL_KEY} (Evaluations [Production]), the only "
                        "project excluded from per-user attribution AND the "
                        "coverage ratio. See ENG-1171.",
                    )

    def test_every_qa_script_that_reads_a_key_reads_the_eval_key(self):
        """A script doing inference must name the eval key explicitly."""
        for name in QA_SKILLS:
            for f in _sources(name):
                if f.suffix != ".py":
                    continue
                text = f.read_text(encoding="utf-8")
                if "_API_KEY" not in text:
                    continue  # script does no OpenAI auth at all
                if "OPENAI" not in text:
                    continue  # e.g. ANTHROPIC_API_KEY / BRAINTRUST_API_KEY only
                with self.subTest(file=str(f.relative_to(REPO_ROOT))):
                    self.assertIn(EVAL_KEY, text)


class ExclusionContractTests(unittest.TestCase):
    """The destination is only correct while `Evaluations` stays excluded."""

    ALLOCATOR = (REPO_ROOT / "plugins" / "eng" / "skills"
                 / "analyze-openai-cost-per-user" / "scripts" / "report.py")

    def test_evaluations_is_in_the_internal_project_exclusion(self):
        text = self.ALLOCATOR.read_text(encoding="utf-8")
        m = re.search(r"INTERNAL_PROJECT_BASE_NAMES\s*=\s*frozenset\(\{([^}]*)\}\)", text)
        self.assertIsNotNone(m, "INTERNAL_PROJECT_BASE_NAMES not found")
        names = {n.strip().strip("\"'") for n in m.group(1).split(",") if n.strip()}
        self.assertIn(
            "Evaluations", names,
            "Removing `Evaluations` from INTERNAL_PROJECT_BASE_NAMES silently "
            "re-contaminates per-user cost with every QA run, and can drive "
            "attribution coverage under store.py's 75% floor. See ENG-1171.",
        )

    def test_the_coverage_floor_still_exists(self):
        """The floor is why the destination has to be an *excluded* project."""
        store = (REPO_ROOT / "plugins" / "eng" / "skills"
                 / "store-aurie-user-daily-cost" / "scripts" / "store.py")
        self.assertRegex(
            store.read_text(encoding="utf-8"),
            r"MIN_ATTRIBUTION_COVERAGE_PCT\s*=\s*\d+",
        )


if __name__ == "__main__":
    unittest.main()
