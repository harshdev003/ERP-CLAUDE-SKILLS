#!/usr/bin/env python3
"""Nothing calls a cost movement unexplained before reading the ledger's `Impact`.

**The rule.** `Learnings/Aurie Cost Engineering` carries a `Cost Changes` ledger whose
`Impact` field is routinely the only place a cost movement's mechanism is written down.
So any skill that reports a cost movement — a per-day spike marker, a refreshed headline
metric — reads the rows that could account for it *before* writing the movement up as
unexplained or handing it off for root cause.

**The incident.** On 2026-09-21 a Cycle 45 harvest (`eng:harvest-cost-changes`) saw
`llm_cost_per_paying_msg` step $0.0208 → $0.0285, wrote "still unexplained" into the page
headline, and filed PROD-6326. Both halves were already rows *on the ledger it was
holding open*, with the mechanism and the dollar figure in their `Impact` fields
(`coach-prompter moved to chat-latest…`, +$228/day, 2026-08-20; `Prompt-testing spend
bills into Conversation [Production]`, 2026-08-24). PROD-6326 re-derived both from
scratch and closed as already-diagnosed. The skill read row *titles*; nothing told it to
read `Impact`. ENG-1365 / [#1083].

**Why a test and not another `diff -q`.** The sibling `eng:report-aurie-daily-costs` had
owned this rule since its ⚠ marker shipped; `eng:harvest-cost-changes` reimplemented the
same surface without it. That is exactly the case CLAUDE.md → *the drift guards compare
copies, never implementations* describes: a filename-keyed byte-diff cannot see a second
implementation of the same rule under a different filename, and the stated remedy is to
write the test rather than a manual cross-check. Three skills carry the rule today; a
fourth that reads or refreshes the Aurie Cost Engineering headline metrics could ship
with no ledger-check instruction and nothing would catch it.

**Why the marker is the one it is, and not a cheaper one.** The rule is prose, so the
assertion can only be a marker — and most candidate markers pass vacuously on the very
file the rule was written for. The pre-fix `harvest-cost-changes/SKILL.md` is kept as a
fixture and measured in `TheMarkerIsNotVacuous`: it hits a bare ledger grep **12 times**
and a bare precedence grep **9 times**, so either alone would have signed off on the
defect. What it does not contain, anywhere at any distance, is a *verdict* token
(`unexplained` / `escalat` / `root cause`). The marker is therefore all three token
classes — ledger, verdict, precedence — co-occurring inside one window: the sentence has
to order the ledger read *against* the act of calling something unexplained, which is
the rule and not a mention of the ledger.

The window is 400 characters of whitespace-collapsed text. The widest real passage needs
215 (`harvest-cost-changes`), so there is ~2x headroom, and 400 is ~2% of a
budget-length SKILL.md — still a genuine proximity constraint rather than "somewhere in
the file".

**Pointing satisfies it.** `data:run-cost-harvest-cadence` defers to the composed
skill's Step 3 instead of restating the rule, which is the shape CLAUDE.md prefers
(*orchestrators: point, don't restate*). A test that only accepted a restatement would
push every future composer into duplicating prose, so a link to a rule-owning sibling
plus a deferral verb is an accepted satisfaction — guarded against vacuity by
`PointingSatisfiesTheRule`, which requires a bare table-row link to fail.

**Why SKILL.md and not the whole skill folder.** SKILL.md is loaded in full on every
invocation; a `references/` file is loaded only if the model goes and opens it. A
behavioural guardrail that lives where the reader may never look is not in force. The
length ratchet does push authors to extract — the pointer branch is the pressure valve:
a skill under length pressure defers in one sentence, it does not move the rule out of
sight.

Stdlib `unittest`, no network, no keys. Reads markdown only.
"""
from __future__ import annotations

import pathlib
import re
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGINS = REPO_ROOT / "plugins"
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"

# `plugins/eng/skills/harvest-cost-changes/SKILL.md` at 655cd618^ — the last commit
# before #1083 added the rule. Byte-identical to the historical file, so the
# "would have caught it" claim is about the real artifact and not a reconstruction.
PRE_FIX = FIXTURES / "harvest_cost_changes_pre_eng1365.md"

# The Aurie Cost Engineering ledger, by every spelling a SKILL.md uses for it: the page
# path (declared in `knowledge:`), the page name, and the ledger's own name.
#
# Case-SENSITIVE, because both are proper nouns and the lowercase forms are ordinary
# English. `eng:store-aurie-user-daily-cost` says "Memory-pipeline cost changes are
# trendable" and has nothing to do with this ledger; a case-insensitive predicate swept
# it in and demanded it carry a rule it has no use for. A discovery predicate that
# over-matches gets narrowed by whoever it inconveniences, which is how a family guard
# stops guarding the family.
LEDGER_NAME = re.compile(r"Aurie Cost Engineering|Cost Changes")

# --- the three token classes the marker is made of -------------------------------
#
# LEDGER also admits a bare `Impact`, because the harvest skill phrases the rule in
# terms of the field rather than the page ("read the ledger's `Impact` fields before
# writing either"). On its own it is worthless — the pre-fix file says `Impact` three
# times — which is what the window and the other two classes are for.
LEDGER = re.compile(r"(?:Aurie Cost Engineering|Cost Changes)|(?i:\bimpact\b)")
# The verdict the rule is about: declaring a movement nobody has accounted for.
VERDICT = re.compile(r"unexplained|escalat|root[- ]cause", re.I)
# The ordering. Without it the sentence can name both subjects and still not say which
# comes first.
PRECEDENCE = re.compile(r"\bbefore\b|\bfirst\b|rather than", re.I)

# Deliberately NOT a deferral verb: a bare `per`. `run-cost-harvest-cadence`'s member
# table opens rows with "Per cost cycle", which would let a plain table row linking a
# rule-owning sibling satisfy the pointer branch without deferring anything.
DEFERRAL = re.compile(
    r"follow|belongs to|owns|owned by|defer|restat|handled by|delegat|as described in",
    re.I,
)

WINDOW = 400

SKILL_LINK = re.compile(r"\]\((?:\.\./)+(?:[\w.-]+/)*skills/([\w-]+)/SKILL\.md")

# Discovered by shape below; listed here so a narrowing of the predicate — a renamed
# page, a reworded declaration — fails loudly instead of leaving an empty set green.
EXPECTED_FAMILY = {
    "eng/skills/report-aurie-daily-costs",       # owns the rule (Step 3, the original)
    "eng/skills/harvest-cost-changes",           # owns it for the headline refresh (#1083)
    "data/skills/run-cost-harvest-cadence",      # points at the composed skill's rule
}


def collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def frontmatter(text: str) -> str:
    """The YAML slice only. A body example is documentation, not a declaration."""
    parts = text.split("---", 2)
    return parts[1] if len(parts) >= 3 else ""


def _windows(text: str, *classes: re.Pattern) -> list[tuple[int, int]]:
    """Positions where one match from every class falls inside a single window."""
    hits = [[m.start() for m in c.finditer(text)] for c in classes]
    if not all(hits):
        return []
    out = []
    # Small n by construction — a SKILL.md has tens of hits, not thousands.
    def walk(chosen, rest):
        if not rest:
            lo, hi = min(chosen), max(chosen)
            if hi - lo <= WINDOW:
                out.append((lo, hi))
            return
        for p in rest[0]:
            walk(chosen + [p], rest[1:])
    walk([], hits)
    return out


def carries_rule(text: str) -> bool:
    """The skill states the rule itself: ledger + verdict + ordering, in one breath."""
    return bool(_windows(collapse(text), LEDGER, VERDICT, PRECEDENCE))


def defers_to(text: str, owners: set[str]) -> bool:
    """The skill points at a sibling that owns the rule, rather than restating it.

    A link alone is not a deferral — the member tables in every cadence skill link
    their members. The window must also carry a deferral verb and name the subject.
    """
    flat = collapse(text)
    for m in SKILL_LINK.finditer(flat):
        if m.group(1) not in owners:
            continue
        lo = max(0, m.start() - WINDOW)
        hi = min(len(flat), m.end() + WINDOW)
        near = flat[lo:hi]
        if DEFERRAL.search(near) and (LEDGER_NAME.search(near) or VERDICT.search(near)):
            return True
    return False


def discover() -> dict[str, str]:
    """Every skill that touches the Aurie Cost Engineering ledger, by shape.

    Two axes, unioned, because either alone has a hole. Naming the ledger in the body
    misses a skill that declares the read and then says only "refresh the metrics" —
    the plausible shape of the next offender. Declaring it in `knowledge:` misses a
    skill that reports movements off a ledger a composed skill fetched for it.
    """
    found = {}
    for path in sorted(PLUGINS.glob("*/skills/*/SKILL.md")):
        text = path.read_text(encoding="utf-8")
        names_it = LEDGER_NAME.search(text)
        declares_it = "Learnings/Aurie Cost Engineering" in frontmatter(text)
        if names_it or declares_it:
            found[str(path.parent.relative_to(PLUGINS))] = text
    return found


class FamilyMembership(unittest.TestCase):
    """The assertion the CLAUDE.md `diff -q` guards structurally cannot make."""

    def test_discovered_set_matches_declared(self):
        found = set(discover())
        self.assertTrue(found, "the discovery predicate matched nothing — a renamed "
                               "page would otherwise leave this suite green on an "
                               "empty set")
        self.assertEqual(
            found,
            EXPECTED_FAMILY,
            "a skill joined or left the Aurie Cost Engineering ledger family — add it "
            "to EXPECTED_FAMILY deliberately (and make sure it orders the ledger "
            "check) rather than leaving it unguarded",
        )

    def test_every_frontmatter_declaration_is_in_the_family(self):
        """The two discovery axes must not disagree about who reads the ledger."""
        for path in sorted(PLUGINS.glob("*/skills/*/SKILL.md")):
            if "Learnings/Aurie Cost Engineering" not in frontmatter(
                path.read_text(encoding="utf-8")
            ):
                continue
            with self.subTest(skill=str(path.parent.relative_to(PLUGINS))):
                self.assertIn(str(path.parent.relative_to(PLUGINS)), EXPECTED_FAMILY)


class EveryLedgerSkillOrdersTheCheck(unittest.TestCase):
    """Own the rule, or point at the sibling that does. Silence is the defect."""

    def test_rule_is_stated_or_deferred(self):
        family = discover()
        owners = {
            name.split("/")[-1] for name, text in family.items() if carries_rule(text)
        }
        self.assertTrue(owners, "no skill states the rule, so nothing can point at it")

        for name, text in sorted(family.items()):
            with self.subTest(skill=name):
                self.assertTrue(
                    carries_rule(text) or defers_to(text, owners),
                    f"{name}/SKILL.md touches the Aurie Cost Engineering ledger but "
                    "never orders the `Impact` read against calling a movement "
                    "unexplained. State the rule, or point at the skill that owns it "
                    "(ENG-1365) — a metric that moved is not a metric nobody has "
                    "explained.",
                )

    def test_the_original_owner_still_owns_it(self):
        """`report-aurie-daily-costs` Step 3 is what the other two are measured against.

        If it ever degrades to a pointer the family has no owner, and
        `test_rule_is_stated_or_deferred` would pass on a circle of skills all
        deferring to each other.
        """
        text = (PLUGINS / "eng/skills/report-aurie-daily-costs/SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertTrue(carries_rule(text))


class TheMarkerIsNotVacuous(unittest.TestCase):
    """Run the detector on the file the rule was written for, before it had the rule.

    This is the load-bearing case, same shape as `test_workaround_notes`: a detector
    for a known defect that cannot find that defect is worth nothing, and only the
    real artifact can make the assertion.
    """

    def setUp(self):
        self.pre_fix = PRE_FIX.read_text(encoding="utf-8")
        self.flat = collapse(self.pre_fix)

    def test_the_pre_fix_harvest_skill_fails(self):
        self.assertFalse(
            carries_rule(self.pre_fix),
            "the detector signs off on the exact file that wrote 'still unexplained' "
            "over two diagnosed ledger rows",
        )

    def test_the_cheaper_markers_would_all_have_passed(self):
        """Why the marker is three classes and not a grep.

        Both of these fire freely on the pre-fix file, so either on its own is a gate
        that cannot fail on the defect it was written for.
        """
        self.assertGreaterEqual(len(LEDGER.findall(self.flat)), 12)
        self.assertGreaterEqual(len(PRECEDENCE.findall(self.flat)), 9)

    def test_the_discriminator_is_the_verdict_token(self):
        """…and it is absent at *any* window, so the red state is not a tuning artifact."""
        self.assertEqual(VERDICT.findall(self.flat), [])

    def test_a_verdict_far_from_the_ledger_is_not_the_rule(self):
        far = (
            "Refresh the headline metrics from BigQuery. Put the link in `Impact`, "
            "not in Refs, and check the watermark first. "
            + "Filler about the plan table. " * 40
            + "Write any movement up as unexplained."
        )
        self.assertFalse(carries_rule(far))

    def test_the_rule_as_actually_written_is_recognised(self):
        """The fix, in one sentence — the shortest thing that should pass."""
        self.assertTrue(
            carries_rule(
                "A metric that moved is not the same as a metric nobody has explained "
                "— read the ledger's `Impact` fields before writing it up as "
                "unexplained."
            )
        )


class PointingSatisfiesTheRule(unittest.TestCase):
    """CLAUDE.md prefers a pointer to a restatement. The test must not invert that."""

    OWNERS = {"report-aurie-daily-costs", "harvest-cost-changes"}

    def test_a_minimal_pointer_passes(self):
        pointer = (
            "### Step 3 — Daily trend report\n\n"
            "Run [`eng:report-aurie-daily-costs`]"
            "(../../../eng/skills/report-aurie-daily-costs/SKILL.md) and show its "
            "table. Spike handling belongs to that skill's Step 3 — follow it rather "
            "than escalating off the ⚠ marker. Don't restate its rules here.\n"
        )
        self.assertFalse(carries_rule(pointer), "fixture should exercise the pointer "
                                                "branch, not the restatement branch")
        self.assertTrue(defers_to(pointer, self.OWNERS))

    def test_a_bare_member_table_row_does_not(self):
        """The vacuity guard on the pointer branch.

        Every cadence skill links its members in a table. A row that happens to name
        the ledger is not an instruction to follow anyone's rule.
        """
        row = (
            "| Per cost cycle | [`eng:harvest-cost-changes`]"
            "(../../../eng/skills/harvest-cost-changes/SKILL.md) | Sweeps GitHub / "
            "Linear / Slack, updates the Aurie Cost Engineering ledger. "
            "Plan-before-apply. |\n"
        )
        self.assertFalse(defers_to(row, self.OWNERS))

    def test_pointing_at_a_skill_that_does_not_own_the_rule_does_not(self):
        pointer = (
            "Spike handling belongs to [`eng:report-aurie-weekly-costs`]"
            "(../../../eng/skills/report-aurie-weekly-costs/SKILL.md) — follow its "
            "Cost Changes handling rather than escalating here."
        )
        self.assertFalse(defers_to(pointer, self.OWNERS))


if __name__ == "__main__":
    unittest.main()
