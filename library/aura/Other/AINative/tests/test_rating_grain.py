#!/usr/bin/env python3
"""The `Signals/Conversation Patterns` grain is one row per RATING, not per thread.

ENG-1115: dedup ran on `Conversation ID`, which is not unique per rating — a thread
can be rated repeatedly and `chat` rates per message — so distinct ratings collapsed
onto one row and the rest were discarded as duplicates. A 7-day harvest lost 53 rows
to 43 repeated ids; `chat` lost ~21% of its ratings.

**Why this needs a test and hand-verification cannot cover it.** The failure is a
*silent absence*. A harvest that drops 53 ratings prints the same shape of success as
one that keeps them, the rows it did write are all correct, and the loss is only
visible if you already know the number you should have seen. Nothing errors. So the
assertions here are mostly negative ones — two ratings on one thread must NOT collapse
— which is exactly the assertion a per-thread key passes only by accident.

Two producers, one contract:
  * `fetch_sweep_analyses.py` (harvest) must emit a distinct `rating_ts` per rating
    message and must not treat a repeated `source_id` as a duplicate.
  * `sample.py` (report) must collapse a repeatedly-rated thread down to one entry for
    the qualitative pass, in BOTH frames — the table frame spells the thread id
    `source_id`, the Slack fallback splits it across three other fields.

Stdlib `unittest`, no network, no keys. `fetch_sweep_analyses.py` is loaded with its
Slack transport untouched but never called: only the pure parse/aggregate functions are
exercised.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
import uuid

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
HARVEST = REPO_ROOT / "plugins/data/skills/harvest-conversation-patterns/scripts/fetch_sweep_analyses.py"
SAMPLE = REPO_ROOT / "plugins/data/skills/report-conversation-patterns/scripts/sample.py"


def load(path: pathlib.Path):
    """Import a skill script under a unique name, with its own dir on sys.path.

    Unique name + wholesale sys.path restore, same reasoning as `reader_harness`:
    these scripts import siblings from their own directory, and a leaked path entry
    would let a later load resolve against an earlier script's folder.
    """
    name = f"_grain_{uuid.uuid4().hex}"
    saved = list(sys.path)
    try:
        sys.path.insert(0, str(path.parent))
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path[:] = saved


CONV = "68CA0387-1111-4222-8333-444455556666"


def chat_msg(ts: str, uid: str = "FhE3nMkP8yQ2wLxV0tRb9dJc", *, conv: str = CONV,
             feedback: str = "Helpful", emoji: str = ":thumbsup:") -> dict:
    """A k8bot `*CHAT MESSAGE RATED*` message — the shape ENG-1115 hit hardest."""
    return {
        "ts": ts,
        "text": (
            f"*CHAT MESSAGE RATED* {emoji}\n"
            f"```{feedback}```\n"
            f"`[dialogue: 11111111-2222-3333-4444-555555555555, conversation: {conv}, "
            f"user: {uid}, prompt:aurie-chat:68CA0387]`"
        ),
    }


class HarvestGrain(unittest.TestCase):
    """The harvest producer keys on the rating, not the rated object."""

    @classmethod
    def setUpClass(cls):
        cls.mod = load(HARVEST)

    def test_rating_ts_is_the_slack_ts(self):
        p = self.mod.parse_rated_message(chat_msg("1783937040.769999"))
        self.assertEqual(p["rating_ts"], "1783937040.769999")

    def test_two_ratings_on_one_thread_stay_distinct(self):
        """The regression. Same conversation, two messages → two distinct keys.

        A per-thread key makes `source_id` the identity, and then these two entries
        are one. Asserting the keys differ *while* the source ids match is what pins
        the grain down; asserting only that both parse would pass either way.
        """
        a = self.mod.parse_rated_message(chat_msg("1783937040.769999", feedback="Loved it"))
        b = self.mod.parse_rated_message(
            chat_msg("1783937999.111111", feedback="Actually it missed the point",
                     emoji=":thumbsdown:")
        )
        self.assertEqual(a["source_id"], b["source_id"])          # one thread…
        self.assertNotEqual(a["rating_ts"], b["rating_ts"])       # …two ratings
        self.assertNotEqual(a["polarity"], b["polarity"])         # with opposite polarity

    def test_rating_ts_never_empty_so_no_rating_is_unrecordable(self):
        """Every shape carries a ts, so no entry can lack a dedup key.

        `source_id` can legitimately be empty (a changed upstream footer). If the key
        were `source_id`, such a rating would be unrecordable and silently dropped;
        keyed on the ts it is still written, just without its grouping column.
        """
        no_footer_id = {
            "ts": "1783937040.769999",
            "text": ("*CHAT MESSAGE RATED* :thumbsup:\n```ok```\n"
                     "`[dialogue: 11111111-2222-3333-4444-555555555555, "
                     "user: FhE3nMkP8yQ2wLxV0tRb9dJc]`"),
        }
        p = self.mod.parse_rated_message(no_footer_id)
        self.assertEqual(p["source_id"], "")
        self.assertTrue(p["rating_ts"])

    def test_permalink_round_trips_the_ts(self):
        """`Slack URL` must remain a recoverable source for `Rating ID`.

        The ENG-1115 migration recovered a ts from every existing row by inverting
        this transform, and re-running a historical window depends on the two
        directions agreeing. A permalink that dropped or reformatted digits would
        make that recovery impossible — and only for old rows, so a fresh harvest
        would look perfectly healthy.
        """
        ts = "1783937040.769999"
        url = self.mod.permalink("C07MB0TMF5J", ts)
        self.assertTrue(url.endswith("/p1783937040769999"), url)
        digits = url.rsplit("/p", 1)[1]
        self.assertEqual(f"{digits[:-6]}.{digits[-6:]}", ts)


class HarvestRepeatDiagnostics(unittest.TestCase):
    """The batch must report how many rows a per-thread key discards.

    Reported precisely so a regression cannot be silent: the count is the size of the
    loss a per-thread key causes, measured on live data every run.
    """

    def setUp(self):
        self.mod = load(HARVEST)

    def _counts(self, entries):
        src = {}
        for e in entries:
            if e.get("source_id"):
                src[e["source_id"]] = src.get(e["source_id"], 0) + 1
        repeated = {s: c for s, c in src.items() if c > 1}
        return len(src), len(repeated), sum(c - 1 for c in repeated.values())

    def test_repeat_counts_match_the_incident_arithmetic(self):
        """Three ratings on one thread + one on another → 1 repeated id, 2 extra rows."""
        entries = [
            self.mod.parse_rated_message(chat_msg("1783937040.700001")),
            self.mod.parse_rated_message(chat_msg("1783937040.700002")),
            self.mod.parse_rated_message(chat_msg("1783937040.700003")),
            self.mod.parse_rated_message(chat_msg("1783937040.700004", conv="AAAA0001-1111-4222-8333-444455556666")),
        ]
        distinct, repeated, beyond = self._counts(entries)
        self.assertEqual((distinct, repeated, beyond), (2, 1, 2))


class SampleThreadCollapse(unittest.TestCase):
    """The report reads a thread once, however many times it was rated."""

    @classmethod
    def setUpClass(cls):
        cls.mod = load(SAMPLE)

    def test_thread_key_reads_the_table_frame(self):
        self.assertEqual(self.mod._thread_key({"source_id": CONV}), CONV)

    def test_thread_key_reads_every_slack_fallback_spelling(self):
        """The fallback frame has no `source_id` at all.

        `conversation_id` on the conversation shapes, `journal_id` on journal,
        `rating_id` on profile. Reading only `source_id` would turn the collapse into
        a no-op on the entire fallback path — the path taken exactly when the table is
        empty, i.e. on the runs least likely to be watched.
        """
        self.assertEqual(self.mod._thread_key({"conversation_id": CONV}), CONV)
        self.assertEqual(self.mod._thread_key({"journal_id": "j-1"}), "j-1")
        self.assertEqual(self.mod._thread_key({"rating_id": "r-1"}), "r-1")

    def test_thread_key_empty_when_nothing_names_a_thread(self):
        """An entry naming no thread must not join a bucket with other such entries."""
        self.assertEqual(self.mod._thread_key({}), "")
        self.assertEqual(self.mod._thread_key({"conversation_id": None, "journal_id": ""}), "")

    def test_quality_key_prefers_a_fetchable_transcript_then_text(self):
        """The collapse keeps the best row per thread, so ranking has to be right."""
        rich = {"conversation_id": CONV, "feedback": "said a lot"}
        thin = {"conversation_id": None, "feedback": ""}
        self.assertGreater(self.mod._quality_key(rich), self.mod._quality_key(thin))


class SampleEndToEnd(unittest.TestCase):
    """Run `sample.py` for real: two users, one shared thread, one thread each."""

    def _run(self, entries, n=10):
        with tempfile.TemporaryDirectory() as d:
            src = pathlib.Path(d) / "in.json"
            out = pathlib.Path(d) / "out.json"
            src.write_text(json.dumps({"window": "7d", "entries": entries}))
            r = subprocess.run(
                [sys.executable, str(SAMPLE), "--input", str(src), "--n", str(n),
                 "--output", str(out)],
                capture_output=True, text=True,
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            return json.loads(out.read_text())

    @staticmethod
    def _entry(uid, ts, conv, feedback="ok", polarity="positive"):
        return {"uid": uid, "ts": ts, "rating_ts": ts, "source_id": conv,
                "conversation_id": conv, "polarity": polarity, "shape": "chat",
                "feedback": feedback}

    def test_one_thread_rated_by_two_users_yields_one_entry(self):
        """Where the per-user dedup does NOT save us, and the collapse must.

        Two distinct users on one thread pass the per-user pass as two entries, so this
        is the only case that actually exercises the thread collapse — and the only
        case where a thread could otherwise be read and quoted twice in one report.
        """
        out = self._run([
            self._entry("UserAAAAAAAAAAAAAAAAAAAAAAAA", "1783937040.700001", CONV),
            self._entry("UserBBBBBBBBBBBBBBBBBBBBBBBB", "1783937040.700002", CONV),
        ])
        self.assertEqual(out["summary"]["thread_collapsed"], 1)
        self.assertEqual(len(out["entries"]), 1)

    def test_distinct_threads_are_all_kept(self):
        """The collapse must not become a general-purpose deduper."""
        other = "AAAA0001-1111-4222-8333-444455556666"
        out = self._run([
            self._entry("UserAAAAAAAAAAAAAAAAAAAAAAAA", "1783937040.700001", CONV),
            self._entry("UserBBBBBBBBBBBBBBBBBBBBBBBB", "1783937040.700002", other),
        ])
        self.assertEqual(out["summary"]["thread_collapsed"], 0)
        self.assertEqual(len(out["entries"]), 2)

    def test_entries_naming_no_thread_are_not_collapsed_together(self):
        """A blank thread id is "unknown", not "the same thread"."""
        out = self._run([
            {"uid": "UserAAAAAAAAAAAAAAAAAAAAAAAA", "ts": "1783937040.700001",
             "source_id": "", "conversation_id": None, "polarity": "positive",
             "shape": "chat", "feedback": "a"},
            {"uid": "UserBBBBBBBBBBBBBBBBBBBBBBBB", "ts": "1783937040.700002",
             "source_id": "", "conversation_id": None, "polarity": "positive",
             "shape": "chat", "feedback": "b"},
        ])
        self.assertEqual(out["summary"]["thread_collapsed"], 0)
        self.assertEqual(len(out["entries"]), 2)

    def test_polarity_is_per_shape_with_no_pooled_number(self):
        """A pooled polarity rate is invalid, so the summary must not offer one.

        `Chat` / `Journal` are two-option (no neutral); the rest are three-option. A
        blended rate measures which surfaces can express a neutral, not quality. The
        assertion that matters is the negative one: no key carries a pooled figure, so
        a report cannot quote one by reaching for the obvious field name.
        """
        out = self._run([
            self._entry("UserAAAAAAAAAAAAAAAAAAAAAAAA", "1783937040.700001", CONV),
            dict(self._entry("UserBBBBBBBBBBBBBBBBBBBBBBBB", "1783937040.700002",
                             "BBBB0001-1111-4222-8333-444455556666", polarity="neutral"),
                 shape="conversation"),
        ])
        s = out["summary"]
        self.assertIn("by_polarity_by_shape_in_window", s)
        self.assertNotIn("by_polarity_in_window", s)
        self.assertEqual(s["by_polarity_by_shape_in_window"]["chat"], {"positive": 1})
        self.assertEqual(s["by_polarity_by_shape_in_window"]["conversation"], {"neutral": 1})

    def test_no_full_uid_survives_the_sample(self):
        """PII rule, re-asserted on the path the collapse now also touches."""
        out = self._run([
            self._entry("UserAAAAAAAAAAAAAAAAAAAAAAAA", "1783937040.700001", CONV),
        ])
        blob = json.dumps(out)
        self.assertNotIn("UserAAAAAAAAAAAAAAAAAAAAAAAA", blob)
        self.assertNotIn("uid\":", blob.replace("uid_prefix\":", ""))


if __name__ == "__main__":
    unittest.main()
