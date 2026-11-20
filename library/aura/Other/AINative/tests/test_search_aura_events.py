#!/usr/bin/env python3
"""The SQL `eng:search-aura-events` builds has three properties that fail silently.

`aura-main.firebase.raw_events` punishes three plausible mistakes by returning the
wrong rows rather than an error, so none of them surfaces as a failure anywhere:

1. **Querying `firebase.events` instead.** A different flat table with no
   user-lifecycle events — its only delete-ish name is `ContentDeleted`. A lookup
   for a deletion answers "no events", which reads as "it never happened".
2. **Filtering on `createdAt` alone.** The table is MONTH-partitioned on
   *ingestion* time (`time_partitioning.field IS NULL`), so `createdAt` prunes
   nothing: 4.274 GB either way, against 1.808 GB once `_PARTITIONTIME` is added.
   This one costs money rather than correctness — but it is the predicate the
   third property hangs off.
3. **Writing a bare `_PARTITIONTIME >= …`.** Streaming-buffer rows carry a NULL
   `_PARTITIONTIME` and hold the newest events, so the filter that makes a query
   affordable is the same one that discards today's data. `INFORMATION_SCHEMA`
   reports that partition as `total_rows: 0` while it holds thousands, so nothing
   else in the stack would warn.

`bq.py`'s copies are drift-checked, but a `diff -q` compares copies of a *file* —
it cannot see a property of the SQL this script assembles. Hence assertions over
the generated query string.

The third property is checked over the **whole source**, not over one call: every
`_PARTITIONTIME` comparison the file contains must be guarded, including ones in
code paths this suite does not otherwise exercise. A test that only inspected
`build_sql`'s output would have passed while `mode_sample` built its own unguarded
predicate.

Also pinned here: five behaviours Bugbot caught on
[#1123](https://github.com/AuraHealth/AINative/pull/1123), each a silent
wrong-answer of the same family — a `--key role=null` that matched nothing while
the docs made `role=null` the headline signal, a `--limit` that kept the oldest
rows, a hidden `userId` on the pivots where it is the answer, a `--lifecycle`
narrower than the vocabulary reference it points at, and a `--json` path that
emitted what the table renderer redacts.

Stdlib `unittest`, no network, no credentials: every assertion is over a string
`build_sql` returns or over the module source.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import pathlib
import re
import sys
import unittest
import uuid

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO_ROOT / "plugins/eng/skills/search-aura-events"
SCRIPT = SKILL / "scripts/search_events.py"
VOCAB = SKILL / "references/event-vocabulary.md"


def load():
    """Import the script by path. It imports `bq` as a sibling, which pulls in no
    Google library at module scope, so this works with no GCP_KEY set."""
    name = f"_search_events_{uuid.uuid4().hex}"
    saved = list(sys.path)
    try:
        sys.path.insert(0, str(SCRIPT.parent))
        spec = importlib.util.spec_from_file_location(name, SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path[:] = saved


M = load()
SOURCE = SCRIPT.read_text()
TREE = ast.parse(SOURCE)


def code_strings() -> str:
    """Every string literal the module can actually emit, docstrings excluded.

    Scanning raw source would match the module docstring, which *describes* the
    traps below in prose — `A bare _PARTITIONTIME >= … drops the newest rows` is
    documentation, not a predicate. The SQL fragments live in ordinary string
    literals, so those are what to check.
    """
    docs = set()
    for node in ast.walk(TREE):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and \
                    isinstance(body[0].value, ast.Constant) and \
                    isinstance(body[0].value.value, str):
                docs.add(id(body[0].value))
    return "\n".join(
        node.value for node in ast.walk(TREE)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and id(node) not in docs
    )


CODE_STRINGS = code_strings()


def cli_flags() -> set:
    """Flag names the argparse parser actually declares."""
    flags = set()
    for node in ast.walk(TREE):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "add_argument":
            for a in node.args:
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    flags.add(a.value)
    return flags


def args(**kw):
    """An argparse-shaped namespace with the parser's own defaults."""
    base = dict(uid=None, name=None, experiment=None, key=None, lifecycle=False,
                since=None, until=None, order="asc", limit=200, fields=None,
                show_pii=False, json=False)
    base.update(kw)
    return argparse.Namespace(**base)


def sql(**kw):
    return M.build_sql(args(**kw), {})


def day(s):
    from datetime import datetime
    return datetime.strptime(s, "%Y-%m-%d").date()


class TestRoutesToRawEvents(unittest.TestCase):
    """Property 1 — the decoy table answers "no events" instead of erroring."""

    def test_table_constant_is_raw_events(self):
        self.assertEqual(M.TABLE, "aura-main.firebase.raw_events")

    def test_generated_sql_never_names_the_decoy(self):
        q = sql(uid="abc123")
        self.assertIn("aura-main.firebase.raw_events", q)
        # Not `assertNotIn(DECOY)`: "…firebase.events" is a substring of
        # "…firebase.raw_events" only in the other direction, but a future
        # `FROM \`…events\`` would still have to be caught, so match the FROM.
        self.assertNotRegex(q, r"FROM\s+`aura-main\.firebase\.events`")

    def test_no_table_flag_exists(self):
        """The routing is not a default a caller can override — it is a constant.

        Checked against the flags argparse declares, not the source text: the
        module docstring says "there is no `--table` flag", so a grep matches the
        sentence promising the flag's absence.
        """
        flags = cli_flags()
        self.assertIn("--uid", flags, "parsed no CLI flags — did main() change?")
        self.assertNotIn("--table", flags)

    def test_the_decoy_is_named_only_to_warn_about_it(self):
        self.assertIn("ContentDeleted", M.ROUTING_NOTE)
        self.assertIn(M.DECOY, M.ROUTING_NOTE)


class TestPartitionPruning(unittest.TestCase):
    """Property 2 — `createdAt` does not prune; only `_PARTITIONTIME` does."""

    def test_since_emits_a_partition_floor(self):
        q = sql(uid="abc123", since=day("2026-09-15"))
        self.assertIn("_PARTITIONTIME", q)
        self.assertIn("createdAt >= TIMESTAMP(@since)", q)

    def test_the_floor_is_the_month_start_not_the_day(self):
        """Partitions are MONTH-grained, so a mid-month floor prunes nothing extra
        and an un-floored one can exclude the partition holding the window."""
        self.assertIn("TIMESTAMP('2026-09-01')", sql(uid="a1b2c3", since=day("2026-09-15")))

    def test_until_emits_no_partition_ceiling(self):
        """Ingestion time is not event time — ~1,168 rows carry a pre-2026
        `createdAt` — so a ceiling on ingestion drops rows inside the event-time
        window the caller asked for."""
        q = sql(uid="abc123", until=day("2026-09-20"))
        self.assertIn("createdAt < TIMESTAMP(@until)", q)
        self.assertNotRegex(q, r"_PARTITIONTIME\s*<")

    def test_no_partition_predicate_without_since(self):
        """Nothing to prune to: an unbounded query must not silently narrow."""
        self.assertNotIn("_PARTITIONTIME", sql(uid="abc123"))


class TestStreamingBufferSurvives(unittest.TestCase):
    """Property 3 — the load-bearing one, and the reason this file exists.

    Checked over the whole module source rather than one call's output: the read
    set is discovered, not listed, so a predicate built in a code path this suite
    never invokes is still covered.
    """

    #  `_PARTITIONTIME` followed by a comparison operator — the dangerous form.
    COMPARISON = re.compile(r"_PARTITIONTIME\s*(?:>=|>|<=|<|=)(?!\s*=)")

    def guarded(self, text: str) -> bool:
        """True when every `_PARTITIONTIME` comparison sits in a clause that also
        admits NULL."""
        for m in self.COMPARISON.finditer(text):
            # Look back to the start of the enclosing parenthesised clause.
            window = text[max(0, m.start() - 220):m.start()]
            if "_PARTITIONTIME IS NULL OR" not in window:
                return False
        return True

    def test_every_comparison_in_the_source_is_guarded(self):
        self.assertTrue(self.COMPARISON.search(CODE_STRINGS),
                        "no _PARTITIONTIME comparison found — did the file move?")
        self.assertTrue(
            self.guarded(CODE_STRINGS),
            "a _PARTITIONTIME comparison is not OR'd with IS NULL: the streaming "
            "buffer holds the NEWEST events and would be dropped silently.",
        )

    def test_generated_sql_is_guarded(self):
        q = sql(uid="abc123", since=day("2026-09-01"))
        self.assertIn("_PARTITIONTIME IS NULL OR", q)
        self.assertTrue(self.guarded(q))

    def test_the_check_rejects_a_bare_comparison(self):
        """The assertion above is worth nothing if it cannot fail. This is the
        negative case ENG-1380 asks for: an unguarded predicate must be caught."""
        self.assertFalse(self.guarded(
            "WHERE name = 'X'\n  AND _PARTITIONTIME >= TIMESTAMP('2026-09-01')"))
        self.assertTrue(self.guarded(
            "WHERE (_PARTITIONTIME IS NULL OR _PARTITIONTIME >= TIMESTAMP('2026-09-01'))"))

    def test_information_schema_read_excludes_the_null_partition(self):
        """`__NULL__` is reported as 0 rows while holding thousands, so it must not
        be treated as a calendar month when deriving the retention floor."""
        self.assertIn(r"\d{6}", CODE_STRINGS)


class TestJsonNullMatching(unittest.TestCase):
    """`--key role=null` is what the SKILL.md tells you to type for a de-role."""

    def test_bare_null_emits_is_null(self):
        q = sql(key=["role=null"], name=["RoleUpdated"])
        self.assertIn("JSON_VALUE(data, '$.role') IS NULL", q)
        self.assertNotIn("= @k0", q)

    def test_an_ordinary_value_is_still_bound(self):
        params = {}
        M.build_sql(args(key=["role=service"]), params)
        self.assertEqual(params["k0"], "service")

    def test_the_uid_is_bound_not_interpolated(self):
        params = {}
        q = M.build_sql(args(uid="abc123"), params)
        self.assertEqual(params["uid"], "abc123")
        self.assertIn("@uid", q)
        self.assertNotIn("abc123", q)


class TestIdentifiersAreNotOverValidated(unittest.TestCase):
    """Both are bound parameters, so a charset check buys nothing against injection
    and only refuses real ids — verified against the live corpus in #1123."""

    def test_provider_prefixed_uid_is_accepted(self):
        sql(uid="google:111555896239269076429")

    def test_short_and_punctuated_experiment_ids_are_accepted(self):
        for exp in ("bm1", "quiz", "subscription50%OffLabel"):
            with self.subTest(exp=exp):
                sql(experiment=exp)

    def test_interpolated_positions_stay_strict(self):
        """An event name and a JSON key cannot be parameterized, so those are the
        two that must reject."""
        with self.assertRaises(M.Refused):
            sql(name=["UserDeleted; DROP TABLE x"])
        with self.assertRaises(M.Refused):
            sql(key=["role'); --=x"])

    def test_an_empty_bound_value_is_refused(self):
        with self.assertRaises(M.Refused):
            sql(uid="   ")


class TestCapKeepsNewest(unittest.TestCase):
    """`ORDER BY createdAt ASC` + LIMIT keeps the OLDEST N, dropping the recent
    UserDeleted / RoleUpdated most questions here are about."""

    def test_a_capped_ascending_query_orders_desc_in_sql(self):
        q = sql(uid="abc123", order="asc", limit=50)
        self.assertRegex(q, r"ORDER BY createdAt DESC\s*\nLIMIT 50")

    def test_an_uncapped_query_keeps_the_requested_order(self):
        self.assertIn("ORDER BY createdAt ASC", sql(uid="abc123", limit=0))


class TestRefusesAnUnfilteredScan(unittest.TestCase):
    def test_no_filter_is_refused(self):
        with self.assertRaises(M.Refused):
            sql()

    def test_since_alone_is_enough(self):
        sql(since=day("2026-09-01"))


class TestPiiRedaction(unittest.TestCase):
    """Both output paths must go through one allow-list — `--json` leaked what the
    table renderer redacted."""

    ROW = json.dumps({"userId": "u1", "email": "a@b.com", "givenName": "Ada",
                      "locale": "en"})

    def test_json_path_redacts_by_default(self):
        out = M.redact_data(self.ROW, show_pii=False, event="UserCreated")
        self.assertEqual(out["email"], "<redacted>")
        self.assertEqual(out["givenName"], "<redacted>")
        self.assertEqual(out["locale"], "en")

    def test_json_path_honours_show_pii(self):
        self.assertEqual(
            M.redact_data(self.ROW, show_pii=True, event="UserCreated")["email"],
            "a@b.com")

    def test_table_path_redacts_the_same_keys(self):
        text, redacted = M.flatten(self.ROW, None, show_pii=False, event="UserCreated")
        self.assertTrue(redacted)
        self.assertNotIn("a@b.com", text)
        self.assertNotIn("Ada", text)

    def test_both_paths_agree_on_every_pii_key(self):
        """The drift this guards against is one path gaining a key the other lacks."""
        for key in sorted(M.PII_KEYS):
            with self.subTest(key=key):
                row = json.dumps({"userId": "u1", key: "SECRET"})
                self.assertEqual(M.redact_data(row, False, "UserUpdated")[key],
                                 "<redacted>")
                text, _ = M.flatten(row, None, False, "UserUpdated")
                self.assertNotIn("SECRET", text)

    def test_name_is_an_entity_name_except_on_coach_events(self):
        """A blanket rule hid the experiment identity on every ExperimentUpdated
        row; `$.name` is a person only on Coach*.

        `experimentId` deliberately carries a DIFFERENT value from `name` here. On a
        real row the two are equal, and a fixture copying that shape cannot fail:
        redacting `name` still leaves the string in `experimentId`, so the assertion
        passes on exactly the defect it exists to catch.
        """
        row = json.dumps({"name": "aiPriceIncrease2", "experimentId": "expId"})
        text = M.flatten(row, None, False, "ExperimentUpdated")[0]
        self.assertIn("name=aiPriceIncrease2", text)
        self.assertNotIn("<redacted>", text)

        coach = json.dumps({"name": "A Real Person", "coachId": "c1"})
        coach_text = M.flatten(coach, None, False, "CoachUpdated")[0]
        self.assertIn("name=<redacted>", coach_text)
        self.assertNotIn("A Real Person", coach_text)


class TestUserIdVisibility(unittest.TestCase):
    """Hidden when you pivoted ON uid (it is the constant you asked for); shown
    otherwise, where it is the answer to "who?"."""

    ROW = json.dumps({"userId": "u1", "role": None, "at": "t", "origin": "o"})

    def test_shown_on_a_non_uid_pivot(self):
        text, _ = M.flatten(self.ROW, None, False, "RoleUpdated", ("at", "origin"))
        self.assertIn("userId=u1", text)

    def test_hidden_on_the_uid_pivot(self):
        text, _ = M.flatten(self.ROW, None, False, "RoleUpdated",
                            ("at", "origin", "userId"))
        self.assertNotIn("userId", text)

    def test_noise_keys_are_always_hidden(self):
        text, _ = M.flatten(self.ROW, None, False, "RoleUpdated")
        self.assertNotIn("origin=", text)
        self.assertNotIn("at=", text)

    def test_a_json_null_renders_as_null(self):
        """`role=null` is the de-role signal; Python's `None` would not match the
        `--key role=null` the docs tell you to type."""
        self.assertIn("role=null", M.flatten(self.ROW, None, False, "RoleUpdated")[0])


class TestLifecycleMatchesItsOwnReference(unittest.TestCase):
    """`--lifecycle` used a closed list narrower than the vocabulary page it points
    at, so follow history vanished from a lifecycle timeline silently."""

    def user_names_in_the_reference(self) -> set:
        text = VOCAB.read_text()
        head = text.index("## Account lifecycle")
        body = text[head:text.index("## ", head + 3)]
        return set(re.findall(r"`(User[A-Za-z]+)`", body))

    def test_every_documented_user_event_is_selected(self):
        documented = self.user_names_in_the_reference()
        self.assertTrue(documented, "parsed no User* names — did the heading change?")
        missing = sorted(documented - set(M.LIFECYCLE_NAMES))
        self.assertEqual(missing, [],
                         f"documented as lifecycle but not selected: {missing}")

    def test_subscription_families_are_matched_by_prefix(self):
        """A closed list is what failed for User*; the Subscription families are
        matched by prefix so a new verb cannot go missing the same way."""
        q = sql(uid="abc123", lifecycle=True)
        for prefix in ("Subscription", "AISubscription"):
            self.assertIn(f"STARTS_WITH(name, '{prefix}')", q)

    def test_lifecycle_narrows_the_query(self):
        self.assertIn("UserDeleted", sql(uid="abc123", lifecycle=True))


class TestRetentionIsReadNotComputed(unittest.TestCase):
    """The 45-day expiry is on INGESTION, so `today - 45` is the wrong boundary."""

    PARTS = [{"partition_id": "202608", "total_rows": 1},
             {"partition_id": "202609", "total_rows": 1}]

    def test_floor_is_the_oldest_live_partition(self):
        self.assertEqual(M.retention_floor(self.PARTS).isoformat(), "2026-08-01")

    def test_a_window_wholly_before_the_floor_says_so(self):
        w = M.retention_warning(day("2026-05-01"), day("2026-06-01"),
                                M.retention_floor(self.PARTS))
        self.assertIn("WHOLLY", w)
        self.assertIn("NOT that nothing happened", w)

    def test_a_window_partly_before_the_floor_says_so(self):
        w = M.retention_warning(day("2026-07-01"), None, M.retention_floor(self.PARTS))
        self.assertIn("PARTLY", w)

    def test_a_window_inside_retention_is_silent(self):
        self.assertEqual(
            M.retention_warning(day("2026-09-01"), None, M.retention_floor(self.PARTS)),
            "")


if __name__ == "__main__":
    unittest.main()
