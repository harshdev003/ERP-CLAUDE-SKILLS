#!/usr/bin/env python3
"""One `Signals/Cancellations` row contract, two writers, no `diff -q` that can see it.

**The rule.** `plugins/shared/skills/record-knowledge/references/schemas/cancellations.md`
is the canonical column contract for the table. Two scripts implement it —
`harvest-cancellation-feedback/scripts/harvest.py` creates the row,
`enrich-cancellation-feedback/scripts/enrich.py` fills it in place — and the second
*reads property names the first wrote*.

**Why a test and not a drift one-liner.** The two scripts are not copies of each
other, so no `diff -q` covers them: this is exactly CLAUDE.md's *The drift guards
compare copies, never implementations*. A rename in `harvest.py` makes
`enrich.py`'s `props.get("Reason", {})` return `{}`, the field resolves empty, the
enrichment writes a blank column, and nothing raises. Same for a new column added
to one script and never written into the schema — the contract silently stops
describing the table.

**The trap this pins, and it is not hypothetical.** Deriving the schema from the
write sites gets `Support Style` wrong. `harvest.py` has a
`props["Support Style"] = make_select(...)` line, but its profile builder hardcodes
`support_style` to `""`, so the branch is unreachable and the column is blank on
all 718 rows. `test_unowned_properties_are_not_claimed` holds the schema to the
honest reading.
"""

import ast
import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = REPO / "plugins/shared/skills/record-knowledge/references/schemas/cancellations.md"
WRITERS = {
    "harvest": REPO / "plugins/data/skills/harvest-cancellation-feedback/scripts/harvest.py",
    "enrich": REPO / "plugins/data/skills/enrich-cancellation-feedback/scripts/enrich.py",
}

# `"title"` is the lowercase property *id* the Notion API requires when creating a
# page under a data_source; the schema documents it as `Title`.
TITLE_ID = "title"


def schema_rows() -> dict[str, str]:
    """Parse the schema's property table → {property name: owner cell}."""
    rows: dict[str, str] = {}
    for line in SCHEMA.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\|\s*`([^`]+)`\s*\|\s*[^|]+\|\s*([^|]+?)\s*\|", line)
        if m:
            rows[m.group(1)] = m.group(2)
    return rows


def property_names(path: pathlib.Path) -> set[str]:
    """Every Notion property name the script writes into or reads off `props`.

    Covers the three shapes both scripts use: a `props = {...}` literal,
    `props["X"] = ...`, and `props.get("X", ...)` / `.get("X", {})` reads.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()

    def is_props(node: ast.AST) -> bool:
        return isinstance(node, ast.Name) and node.id == "props"

    for node in ast.walk(tree):
        # props = { "X": ... } and the annotated form `props: dict = { "X": ... }`
        # — both scripts use the annotated one, so missing it empties the result.
        dict_targets: list[ast.AST] = []
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            dict_targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.value, ast.Dict):
            dict_targets = [node.target]
        if dict_targets and any(is_props(t) for t in dict_targets):
            for k in node.value.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    names.add(k.value)
        # props["X"] = ...
        if isinstance(node, ast.Subscript) and is_props(node.value):
            if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                names.add(node.slice.value)
        # <anything>.get("X", ...) where X looks like a Notion property name
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "get" and node.args:
                a = node.args[0]
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    if re.fullmatch(r"[A-Z][A-Za-z]*(?: [A-Za-z]+)*", a.value):
                        names.add(a.value)
    return names


class TestSchemaCoversEveryWrittenProperty(unittest.TestCase):
    def test_schema_file_exists(self):
        self.assertTrue(SCHEMA.is_file(), f"missing canonical schema: {SCHEMA}")

    def test_writers_exist(self):
        for name, path in WRITERS.items():
            self.assertTrue(path.is_file(), f"{name} writer moved or renamed: {path}")

    def test_every_property_touched_by_a_script_is_in_the_schema(self):
        documented = set(schema_rows())
        self.assertTrue(documented, "parsed no rows out of the schema table")
        for name, path in WRITERS.items():
            for prop in sorted(property_names(path)):
                if prop == TITLE_ID:
                    continue
                self.assertIn(
                    prop, documented,
                    f"{name} touches property {prop!r}, which the canonical schema does "
                    f"not document. Add the row (with its owner) to {SCHEMA.name}.",
                )

    def test_title_is_written_by_its_api_id_not_its_display_name(self):
        """The title-by-ID gotcha: a create under a data_source needs `"title"`."""
        harvest = property_names(WRITERS["harvest"])
        self.assertIn(
            TITLE_ID, harvest,
            "harvest.py must key the title as the lowercase property id \"title\" on "
            "create; keying it by display name yields a generic "
            "`body.properties.<X>.id should be defined` 400.",
        )


class TestOwnersMatchTheScripts(unittest.TestCase):
    def test_owned_properties_are_touched_by_their_owner(self):
        for prop, owner in schema_rows().items():
            for who in ("harvest", "enrich"):
                if who in owner:
                    touched = property_names(WRITERS[who])
                    # The title is written by its API id, not its display name.
                    wanted = {prop, TITLE_ID} if prop == "Title" else {prop}
                    self.assertTrue(
                        wanted & touched,
                        f"schema says {who} owns {prop!r}, but {WRITERS[who].name} never "
                        f"touches it. Either the owner cell is stale or the write was lost.",
                    )

    def test_unowned_properties_are_not_claimed(self):
        """A column nothing populates must say so, even when a write site exists.

        `Support Style` is the case: `harvest.py` has a write for it behind a
        condition its own profile builder can never satisfy. Claiming an owner here
        would describe a column that is blank on every row.
        """
        rows = schema_rows()
        self.assertIn("Support Style", rows, "Support Style row missing from the schema")
        owner = rows["Support Style"]
        self.assertIn(
            "nobody", owner.lower(),
            "Support Style is blank on every row (the harvest branch is unreachable — "
            "its profile builder hardcodes support_style to \"\"). The schema must not "
            "name an owner for it.",
        )

    def test_every_documented_property_has_an_owner_cell(self):
        for prop, owner in schema_rows().items():
            self.assertTrue(
                owner.strip(), f"{prop!r} has an empty Owner cell; name the writing phase or 'nobody'."
            )


class TestNaturalKeyIsStated(unittest.TestCase):
    def test_schema_names_the_dedup_key_the_harvester_uses(self):
        """Harvest dedups on the Slack permalink; the schema must say so.

        Keying on the UID instead would collapse a user's second cancellation into
        the first — two events, one row, silently.
        """
        text = SCHEMA.read_text(encoding="utf-8")
        self.assertRegex(
            text, r"[Nn]atural key.{0,80}`Slack Thread URL`",
            "the schema must name `Slack Thread URL` as the natural key",
        )
        harvest_src = WRITERS["harvest"].read_text(encoding="utf-8")
        self.assertIn(
            "Slack Thread URL", harvest_src,
            "harvest.py no longer reads `Slack Thread URL` — the dedup key changed and "
            "the schema is now wrong",
        )



class TestTheRefusalIsDeclaredNotNarrated(unittest.TestCase):
    """`record-knowledge` must refuse this path on a checkable declaration.

    Before the schema existed the refusal was structural by accident: no schema
    file meant Step 2's "no schema registered" refusal fired, and an absent `Kind`
    defaulted a Signals path to `snapshot`, so `action: update` was rejected too.
    A resolvable `Kind: table` schema removes both, so the refusal has to be carried
    by something a caller cannot read past — hence `Writable: no` plus a Step 1 gate
    that runs first. Prose alone would sit next to three lines that name this path
    as an enrichable table.
    """

    SKILL = REPO / "plugins/shared/skills/record-knowledge/SKILL.md"

    def test_schema_declares_itself_unwritable(self):
        text = SCHEMA.read_text(encoding="utf-8")
        self.assertRegex(
            text, r"\*\*Writable:\*\*\s*`no`",
            "cancellations.md must carry a `**Writable:** `no`` declaration — the "
            "line record-knowledge's Step 1 gate reads.",
        )

    def test_step_1_gates_on_the_declaration(self):
        text = self.SKILL.read_text(encoding="utf-8")
        self.assertRegex(
            text, r"`Writable: no`.{0,120}refuse",
            "record-knowledge Step 1 must refuse on `Writable: no`",
        )

    def test_the_gate_precedes_the_other_action_guardrails(self):
        """Order is the whole point: the kind/action checks would otherwise allow it."""
        lines = self.SKILL.read_text(encoding="utf-8").split("\n")
        start = next(i for i, l in enumerate(lines) if "Action-specific guardrails" in l)
        guardrails = [l for l in lines[start + 1:start + 8] if l.strip().startswith("- ")]
        self.assertTrue(guardrails, "could not find the action guardrail bullets")
        self.assertIn(
            "Writable: no", guardrails[0],
            "the `Writable` gate must be the FIRST action guardrail; behind the "
            "kind/action checks a `table`-kind path is allowed through.",
        )

    def test_no_guardrail_still_advertises_the_path_as_updatable(self):
        """The three lists that named this path as enrichable are the contradiction.

        Any of them still offering `Signals/Cancellations` as an example of a path
        accepting `action: update` re-opens what the gate closes.
        """
        text = self.SKILL.read_text(encoding="utf-8")
        for line in text.split("\n"):
            if "Signals/Cancellations" not in line:
                continue
            if "Writable" in line:
                continue  # the gate itself, and the pointers that cite it
            self.assertNotRegex(
                line.replace("`Signals/Conversation Patterns`", ""),
                r"(accept|may be updated|are updatable)",
                f"line still presents Signals/Cancellations as writable:\n  {line[:200]}",
            )


if __name__ == "__main__":
    unittest.main()
