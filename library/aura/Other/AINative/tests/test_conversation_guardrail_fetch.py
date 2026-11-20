#!/usr/bin/env python3
"""`eng:investigate-conversation-guardrail` — the whole-conversation reader.

The skill exists to answer "what did a person at risk actually hear, across the
whole conversation" (PROD-7238). The defects worth catching are the ones that make
a guardrail reply silently absent, and every one of them lives in a branch real
data rarely reaches:

* the stored-transcript export does not contain the conversation (incognito,
  deleted) — the reader must rebuild from OpenAI, not return an empty transcript;
* the ledger is unreachable — the transcript must still come back, unattributed;
* GCP is unreachable — categories read "unknown", nothing else changes;
* a handoff whose reply is in no source — it must surface as an orphan, not vanish;
* every source fails — the result must be NOT ANALYSABLE, never an empty "clean"
  conversation (an empty transcript reads exactly like a no-risk one).

The four source readers (`fetch_ledger`, `fetch_export`, `fetch_handoffs`,
`hydrate_row`) are the script's network chokepoints, so each test stubs them and
makes them fail on demand. The resource-signal scan is pinned on the cases that
decide the crisis-reply check: a generic "licensed professional" is NOT a way to
reach a human, and "$1,000" is not Australia's emergency number.

Stdlib unittest, no network, no keys.
"""
from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from reader_harness import REPO_ROOT, load_reader  # noqa: E402

SCRIPT = REPO_ROOT / "plugins/eng/skills/investigate-conversation-guardrail/scripts/fetch_conversation.py"
# Every copy runs the whole suite: a copy can be byte-identical and still be the wrong canonical.
COPIES = sorted(REPO_ROOT.glob("plugins/*/skills/*/scripts/fetch_conversation.py"))

CID = "FE3502B4-0000-4000-8000-000000000001"
UID = "testUid0000000000000000000001"
TX_HANDOFF = "4D921891-0000-4000-8000-0000000000AA"


def _row(dialogue_id, prompter, tx, created, rid):
    return {
        "conversationId": CID, "userId": UID, "dialogueId": dialogue_id,
        "prompterName": prompter, "transaction": tx, "createdAt": created,
        "openaiAPI": "responses", "openaiId": rid,
    }


LEDGER = [
    _row("d-a1", "coach-prompter", "TX-1", "2026-10-07T20:01:20Z", "resp_1"),
    _row("d-a2", "guardrails-handler-prompter", TX_HANDOFF, "2026-10-07T20:07:05Z", "resp_2"),
    _row("d-a3", "coach-prompter", "TX-3", "2026-10-07T20:07:53Z", "resp_3"),
    # Side-effect prompters share the conversationId but are never transcript turns.
    _row("d-a3", "summary-prompter", "TX-3", "2026-10-07T20:08:00Z", "resp_9"),
]

EXPORT_CONV = {
    "id": CID.lower(),  # the export and the ledger disagree on casing in real data
    "turns": [
        {"id": "d-open", "role": "assistant", "message": "Hi, what's on your mind?", "createdAt": "2026-10-07T20:01:04Z"},
        {"id": "d-u1", "role": "user", "message": "first", "createdAt": "2026-10-07T20:01:20Z"},
        {"id": "d-a1", "role": "assistant", "message": "coach reply", "createdAt": "2026-10-07T20:01:20Z"},
        {"id": "d-u2", "role": "user", "message": "disclosure", "createdAt": "2026-10-07T20:07:05Z"},
        {"id": "d-a2", "role": "assistant", "message": "Okay. We can leave that there.", "createdAt": "2026-10-07T20:07:05Z"},
        {"id": "d-u3", "role": "user", "message": "more", "createdAt": "2026-10-07T20:07:53Z"},
        {"id": "D-A3", "role": "assistant", "message": "Please call 988 now.", "createdAt": "2026-10-07T20:07:53Z"},
    ],
}

HANDOFFS = [{"timestamp": "2026-10-07T20:07:07Z", "category": "crisis", "transaction": TX_HANDOFF.lower()}]


class Base(unittest.TestCase):
    SCRIPT = SCRIPT

    def setUp(self):
        self.mod = load_reader(self.SCRIPT, siblings=("responses_reader",))
        m = self.mod
        self.hydrated: list[str] = []

        def hydrate(row, api_key):
            self.hydrated.append(row["openaiId"])
            return {"user_text": f"user for {row['openaiId']}",
                    "assistant_text": f"reply for {row['openaiId']}"}

        m.fetch_ledger = lambda cid: list(LEDGER)
        m.fetch_export = lambda uid, since, until: {"conversations": [EXPORT_CONV]}
        m.fetch_handoffs = lambda cid, since: list(HANDOFFS)
        m.fetch_aura_user = lambda uid: {"_status": "ok", "countryCode": "US", "resourceMapKey": "US"}
        m.hydrate_row = hydrate
        self._env = dict(m.os.environ)
        m.os.environ["OPENAI_CONVERSATION_API_KEY"] = "sk-test"

    def tearDown(self):
        self.mod.os.environ.clear()
        self.mod.os.environ.update(self._env)

    def _fail(self, name):
        def boom(*a, **k):
            raise self.mod.FetchError(f"{name}: stubbed failure")
        setattr(self.mod, name, boom)

    def run_it(self, **kw):
        return self.mod.run(CID, kw.pop("user_id", UID), kw.pop("tx", TX_HANDOFF), **kw)


class ExportPath(Base):
    def test_whole_conversation_in_order_with_opener(self):
        r = self.run_it()
        self.assertTrue(r["analysable"])
        self.assertEqual([m["index"] for m in r["messages"]], list(range(1, 8)))
        self.assertEqual(r["messages"][0]["prompter"], None)  # opener has no ledger row
        self.assertIn("export", r["source"])

    def test_guardrail_turn_attributed_categorised_and_marked_alert(self):
        r = self.run_it()
        gh = [m for m in r["messages"] if m.get("guardrail_handled")]
        self.assertEqual(len(gh), 1)
        self.assertEqual(gh[0]["index"], 5)
        self.assertEqual(gh[0]["prompter"], "guardrails-handler-prompter")
        self.assertEqual(gh[0]["handoff_category"], "crisis")  # tx joined case-insensitively
        self.assertTrue(gh[0]["is_alert_turn"])
        self.assertEqual(gh[0]["drop_topic_signals"], ["leave that there"])
        self.assertEqual(gh[0]["resource_signals"], [])

    def test_turns_after_the_alert_are_kept(self):
        r = self.run_it()
        last = r["messages"][-1]
        self.assertEqual(last["prompter"], "coach-prompter")  # dialogue id joined case-insensitively
        self.assertIn("988", last["resource_signals"])

    def test_side_effect_prompters_never_become_turns(self):
        r = self.run_it()
        self.assertNotIn("summary-prompter", {m.get("prompter") for m in r["messages"]})
        self.assertEqual(r["conversation_row_count"], 3)

    def test_uid_derived_from_ledger_when_not_given(self):
        r = self.run_it(user_id=None)
        self.assertEqual(r["user_id"], UID)
        self.assertTrue(r["analysable"])


class KeyTurns(Base):
    """Mode C: the reviewer reads the key turns locally; the Slack reply carries numbers only."""

    def test_parse_turns_ranges_and_singles(self):
        self.assertEqual(self.mod.parse_turns("#26-#28, 31,40-39"), {26, 27, 28, 31, 39, 40})

    def test_parse_turns_accepts_the_en_dash_from_the_slack_line(self):
        self.assertEqual(self.mod.parse_turns("#26\u2013#28, #31"), {26, 27, 28, 31})

    def test_render_filters_to_the_requested_turns(self):
        r = self.run_it()
        out = self.mod.render(r, {2, 3})
        self.assertIn("## Key turns only (2 of 7", out)
        self.assertIn("### #2 ", out)
        self.assertIn("### #3 ", out)
        self.assertNotIn("### #5 ", out)

    def test_default_window_brackets_the_guardrail_turns(self):
        r = self.run_it()
        gh = [m["index"] for m in r["messages"] if m.get("guardrail_handled")]
        window = self.mod.default_key_turns(r)
        self.assertEqual(min(window), max(1, min(gh) - 2))
        self.assertEqual(max(window), max(gh) + 4)

    def test_whole_transcript_unchanged_without_turns(self):
        self.assertIn("## Transcript (whole conversation, oldest first)", self.mod.render(self.run_it()))


class Degradation(Base):
    def test_export_excludes_conversation_rebuilds_from_responses(self):
        self.mod.fetch_export = lambda uid, since, until: {"conversations": []}
        r = self.run_it()
        self.assertTrue(r["analysable"])
        self.assertIn("OpenAI responses", r["source"])
        self.assertEqual(self.hydrated, ["resp_1", "resp_2", "resp_3"])
        gh = [m for m in r["messages"] if m.get("guardrail_handled")]
        self.assertEqual([m["text"] for m in gh], ["reply for resp_2"])
        self.assertTrue(gh[0]["is_alert_turn"])

    def test_export_down_rebuilds_from_responses(self):
        self._fail("fetch_export")
        r = self.run_it()
        self.assertTrue(r["analysable"])
        self.assertTrue(any("export unreadable" in w for w in r["warnings"]))

    def test_ledger_down_still_returns_the_transcript_unattributed(self):
        self._fail("fetch_ledger")
        r = self.run_it()
        self.assertTrue(r["analysable"])
        self.assertEqual(len(r["messages"]), 7)
        self.assertFalse(any(m.get("guardrail_handled") for m in r["messages"]))
        self.assertTrue(any("Ledger unreadable" in w for w in r["warnings"]))
        # The handoff GCP saw has no reply to land on -> reported, not dropped.
        self.assertEqual(len(r["orphan_handoffs"]), 1)

    def _drop_guardrail_turn_from_export(self):
        conv = dict(EXPORT_CONV)
        conv["turns"] = [t for t in EXPORT_CONV["turns"] if t["id"] != "d-a2"]
        self.mod.fetch_export = lambda uid, since, until: {"conversations": [conv]}

    def test_absent_turns_without_openai_key_mark_incomplete(self):
        """BugBot 1239 re-review: with no key, absent ledger turns were never
        fetched yet the transcript still read as complete."""
        self._drop_guardrail_turn_from_export()
        del self.mod.os.environ["OPENAI_CONVERSATION_API_KEY"]
        r = self.run_it()
        self.assertTrue(r["analysable"])
        self.assertEqual(self.hydrated, [])
        self.assertFalse(r["transcript_complete"])
        reason = " ".join(r["incomplete_reasons"])
        self.assertIn("1 ledger turn(s) absent from the stored transcript and not fetched", reason)
        self.assertIn("OPENAI_CONVERSATION_API_KEY unset", reason)
        self.assertIn("TRANSCRIPT INCOMPLETE", self.mod.render(r))

    def test_absent_turn_fetch_failure_marks_incomplete(self):
        self._drop_guardrail_turn_from_export()
        self._fail("hydrate_row")
        r = self.run_it()
        self.assertFalse(r["transcript_complete"])
        self.assertIn("absent from the stored transcript and not fetched", " ".join(r["incomplete_reasons"]))

    def test_absent_turns_recovered_stay_complete(self):
        self._drop_guardrail_turn_from_export()
        r = self.run_it()
        self.assertEqual(self.hydrated, ["resp_2"])
        self.assertTrue(r["transcript_complete"])

    def test_orphan_handoff_marks_incomplete(self):
        self.mod.fetch_handoffs = lambda cid, since: HANDOFFS + [
            {"timestamp": "2026-10-07T20:30:00Z", "category": "crisis", "transaction": "TX-NOWHERE"}]
        r = self.run_it()
        self.assertEqual(len(r["orphan_handoffs"]), 1)
        self.assertFalse(r["transcript_complete"])

    def test_gcp_down_leaves_category_unknown_and_nothing_else_changes(self):
        self._fail("fetch_handoffs")
        r = self.run_it()
        gh = [m for m in r["messages"] if m.get("guardrail_handled")]
        self.assertEqual(len(gh), 1)
        self.assertTrue(gh[0]["handoff_category"].startswith("unknown"))

    def test_ledger_turn_missing_from_export_is_hydrated_not_lost(self):
        conv = dict(EXPORT_CONV)
        conv["turns"] = [t for t in EXPORT_CONV["turns"] if t["id"] != "d-a2"]
        self.mod.fetch_export = lambda uid, since, until: {"conversations": [conv]}
        r = self.run_it()
        self.assertEqual(self.hydrated, ["resp_2"])
        extra = [m for m in r["ledger_only_messages"] if m.get("guardrail_handled")]
        self.assertEqual(len(extra), 1)
        self.assertEqual(extra[0]["handoff_category"], "crisis")
        self.assertTrue(any("no message in the stored transcript" in w for w in r["warnings"]))

    def test_recovered_turns_keep_turn_numbers_and_reach_both_tables(self):
        """A removed guardrail reply was still seen by the person: it must be citable.

        BugBot 1239: recovered turns had `index = None` and were left out of the
        guardrail and signal tables, so the crisis-reply check had no `#N` and no
        signal hint for exactly the reply this path exists to recover.
        """
        conv = dict(EXPORT_CONV)
        conv["turns"] = [t for t in EXPORT_CONV["turns"] if t["id"] != "d-a2"]
        self.mod.fetch_export = lambda uid, since, until: {"conversations": [conv]}
        self.mod.hydrate_row = lambda row, key: {"user_text": "u", "assistant_text": "We can leave that there. Call 988."}
        r = self.run_it()
        n = len(r["messages"])
        rec = [m for m in r["ledger_only_messages"] if m["role"] == "assistant"][0]
        self.assertGreater(rec["index"], n)  # numbered after the stored transcript
        self.assertTrue(rec["absent_from_stored_transcript"])
        self.assertIn("988", rec["resource_signals"])
        self.assertEqual(rec["drop_topic_signals"], ["leave that there"])
        out = self.mod.render(r)
        guard = out.split("## Guardrail-handled turns")[1].split("## Resource")[0]
        signals = out.split("## Resource / drop-topic signals")[1].split("## Transcript")[0]
        self.assertIn(f"#{rec['index']}", guard)
        self.assertIn("absent from stored transcript", guard)
        self.assertIn(f"#{rec['index']}", signals)
        self.assertIn(f"### #{rec['index']} ", out)
        indexes = [m["index"] for m in r["messages"] + r["ledger_only_messages"]]
        self.assertEqual(len(indexes), len(set(indexes)))

    def test_ledger_down_is_marked_incomplete_and_requests_the_whole_history(self):
        """BugBot 1239: a ledger failure used to narrow the export to a guessed
        35-day window and still report the transcript as analysable and whole."""
        self._fail("fetch_ledger")
        seen = {}

        def export(uid, since, until):
            seen["since"], seen["until"] = since, until
            return {"conversations": [EXPORT_CONV]}

        self.mod.fetch_export = export
        r = self.run_it()
        self.assertEqual(seen, {"since": None, "until": None})
        self.assertTrue(r["analysable"])
        self.assertFalse(r["transcript_complete"])
        self.assertTrue(any("ledger unreadable" in x for x in r["incomplete_reasons"]))
        self.assertIn("TRANSCRIPT INCOMPLETE", self.mod.render(r).split("\n## ")[0])

    def test_empty_ledger_is_marked_incomplete(self):
        self.mod.fetch_ledger = lambda cid: []
        r = self.run_it()
        self.assertTrue(r["analysable"])
        self.assertFalse(r["transcript_complete"])

    def test_dated_ledger_narrows_the_window_and_is_complete(self):
        seen = {}

        def export(uid, since, until):
            seen["since"] = since
            return {"conversations": [EXPORT_CONV]}

        self.mod.fetch_export = export
        r = self.run_it()
        self.assertIsNotNone(seen["since"])
        self.assertTrue(r["transcript_complete"])
        self.assertNotIn("TRANSCRIPT INCOMPLETE", self.mod.render(r))

    def test_hydrate_failures_and_orphans_mark_incomplete(self):
        self.mod.fetch_export = lambda uid, since, until: {"conversations": []}
        self._fail("hydrate_row")
        r = self.run_it()
        self.assertFalse(r["analysable"])
        self.assertFalse(r["transcript_complete"])

    def test_every_source_failing_is_not_analysable(self):
        self._fail("fetch_ledger")
        self._fail("fetch_export")
        r = self.run_it()
        self.assertFalse(r["analysable"])
        self.assertEqual(r["messages"], [])

    def test_no_openai_key_and_no_export_is_not_analysable(self):
        self.mod.fetch_export = lambda uid, since, until: {"conversations": []}
        del self.mod.os.environ["OPENAI_CONVERSATION_API_KEY"]
        r = self.run_it()
        self.assertFalse(r["analysable"])
        self.assertTrue(any("OPENAI_CONVERSATION_API_KEY" in w for w in r["warnings"]))

    def test_main_exits_3_when_not_analysable(self):
        self._fail("fetch_ledger")
        self._fail("fetch_export")
        argv = sys.argv
        sys.argv = ["fetch_conversation.py", "--conversation-id", CID, "--user-id", UID, "--no-gcp"]
        try:
            import contextlib, io
            with contextlib.redirect_stdout(io.StringIO()) as out:
                code = self.mod.main()
        finally:
            sys.argv = argv
        self.assertEqual(code, 3)
        self.assertIn("NOT a no-risk verdict", out.getvalue())


class Signals(unittest.TestCase):
    """The scan is a hint for the crisis-reply check — pin the cases that decide it."""

    SCRIPT = SCRIPT

    @classmethod
    def setUpClass(cls):
        cls.mod = load_reader(cls.SCRIPT, siblings=("responses_reader",))

    def res(self, text):
        return self.mod.scan(text, self.mod.RESOURCE_PATTERNS)

    def test_real_lines_are_found(self):
        self.assertIn("988", self.res("You can call or text 988 any time."))
        self.assertIn("116 123 (Samaritans)", self.res("Samaritans are on 116 123."))
        self.assertIn("000 (AU)", self.res("Call 000 if you're in danger."))
        self.assertIn("emergency services", self.res("please contact local emergency services"))

    def test_professional_referral_alone_is_not_a_way_to_reach_a_human(self):
        self.assertEqual(self.res("It might help to talk to a licensed professional or a therapist."), [])
        self.assertEqual(self.res("Reach out to someone you trust."), [])

    def test_money_is_not_an_emergency_number(self):
        self.assertEqual(self.res("It cost $1,000 and then 2000 more."), [])

    def test_drop_topic(self):
        found = self.mod.scan("Okay. We can leave that there.", self.mod.DROP_TOPIC_PATTERNS)
        self.assertEqual(found, ["leave that there"])


class CopiesExist(unittest.TestCase):
    def test_canonical_is_among_the_copies(self):
        self.assertIn(SCRIPT, COPIES)

    def test_copies_are_byte_identical(self):
        for copy in COPIES:
            with self.subTest(copy=str(copy.relative_to(REPO_ROOT))):
                self.assertEqual(copy.read_bytes(), SCRIPT.read_bytes())


def _parameterise_over_copies():
    """Re-run every test class against each non-canonical copy."""
    for copy in COPIES:
        if copy == SCRIPT:
            continue
        skill = copy.parent.parent.name.replace("-", "_")
        for cls in [c for c in list(globals().values())
                    if isinstance(c, type) and issubclass(c, unittest.TestCase)
                    and getattr(c, "SCRIPT", None) == SCRIPT and c is not Base]:
            name = f"{cls.__name__}__{skill}"
            globals()[name] = type(name, (cls,), {"SCRIPT": copy})


_parameterise_over_copies()


if __name__ == "__main__":
    unittest.main()
