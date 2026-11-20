"""The `user_id` narrowing rule, asserted across every ElevenLabs single-user walk.

`/v1/convai/conversations` returns no dynamic variables, so attributing a call to a
Firebase uid needs a detail fetch per candidate. It does NOT need a detail fetch per
*workspace* conversation: the list endpoint filters server-side on `user_id`, which is
the difference between `O(that user's calls)` and `O(workspace voice volume)`.

Two skills walk that endpoint for a single user, under two different filenames:

- `eng:report-elevenlabs-user-cost` — cost attribution. Dropping the filter made a
  per-canceller run take 65.9 min (10,667 conversations walked to attribute 1 call),
  which is what made the Group 4 cadence time-box fire (ENG-1156 / ENG-1165).
- `eng:investigate-voice-issue` — finding the vendor record behind a complaint. Its
  walk is bounded by `--max-detail-fetches`, so an unfiltered list spends the budget
  on other users' calls and reports a miss it never earned (ENG-1174).

**This file exists because a filename-keyed `diff -q` cannot see across those two.**
Per CLAUDE.md → *the drift guards compare copies, never implementations*, the rule is
implemented twice under two names, so the guard discovers implementations by **shape**
and fails when an undeclared third one appears.

The asymmetry worth keeping in mind while reading the assertions: because ElevenLabs
ignores unknown query params **silently**, a renamed parameter does not raise. It
returns 200 with a full unfiltered page. So the filter is a performance narrowing and
never a correctness boundary — identity must still be decided on the detail record.
Dropping the filter is slow; trusting it for identity is wrong.
"""

from __future__ import annotations

import pathlib
import re
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGINS = REPO_ROOT / "plugins"

# Single-user walks of /v1/convai/conversations. Discovered by shape below; listed here
# so a new one has to be declared rather than silently unguarded.
SINGLE_USER_WALKS = [
    PLUGINS / "eng/skills/report-elevenlabs-user-cost/scripts/report.py",
    PLUGINS / "eng/skills/investigate-voice-issue/scripts/elevenlabs.py",
]

# Deliberately OUT: these walk the same endpoint but legitimately need *every* user, so
# narrowing by uid would defeat their purpose. A reason, not a silent non-match.
#
# Verified rather than assumed: none of these accepts a single-user argument. They are
# aggregate or rank-N reports whose question *is* "across all users" — a uid filter would
# not make them faster, it would make them wrong. Their unfiltered walk is the cost of
# the answer they give, not an oversight.
NEEDS_EVERY_USER = {
    PLUGINS / "eng/skills/analyze-elevenlabs-cost-per-user/scripts/report.py",
    PLUGINS / "eng/skills/analyze-elevenlabs-latency/scripts/report.py",
    PLUGINS / "eng/skills/report-elevenlabs-costs/scripts/report.py",
    PLUGINS / "eng/skills/report-top-conversation-users/scripts/report.py",
    PLUGINS / "eng/skills/report-aurie-weekly-costs/scripts/report.py",
    PLUGINS / "eng/skills/store-aurie-user-daily-cost/scripts/store.py",
    PLUGINS / "data/skills/report-aurie-conversations/scripts/report.py",
}


def _text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _load_elevenlabs():
    """Import investigate-voice-issue's elevenlabs.py under its own sys.path."""
    import importlib.util
    import sys
    path = PLUGINS / "eng/skills/investigate-voice-issue/scripts/elevenlabs.py"
    saved = list(sys.path)
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location("_voice_elevenlabs", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_voice_elevenlabs"] = mod   # dataclasses needs this
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path[:] = saved


TARGET = "TargetUser000000000000000001"
OTHER = "OtherUser00000000000000000002"


class _Resp:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeSession:
    """Stubs the one HTTP chokepoint. `honour_filter=False` emulates the real
    failure mode: ElevenLabs ignoring an unknown query param and returning a
    full, unfiltered page with a 200."""

    def __init__(self, convs, honour_filter=True, honour_time=True,
                 page_one_at_a_time=False):
        self.convs = convs
        self.honour_filter = honour_filter
        self.honour_time = honour_time
        self.page_one_at_a_time = page_one_at_a_time
        self.list_calls = []
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        params = params or {}
        if url.endswith("/v1/convai/conversations"):
            self.list_calls.append(dict(params))
            items = self.convs
            if self.honour_filter and params.get("user_id"):
                items = [c for c in items if c["_uid"] == params["user_id"]]
            if self.honour_time and params.get("call_start_after_unix") is not None:
                items = [c for c in items if c["ts"] >= params["call_start_after_unix"]]
            if self.honour_time and params.get("call_start_before_unix") is not None:
                items = [c for c in items if c["ts"] <= params["call_start_before_unix"]]
            # One item per page when paging is emulated, so max_pages is reachable.
            if self.page_one_at_a_time:
                start = int(params.get("cursor") or 0)
                page = items[start:start + 1]
                more = start + 1 < len(items)
                return _Resp({"conversations": [
                    {"conversation_id": c["id"], "start_time_unix_secs": c["ts"]} for c in page
                ], "has_more": more, "next_cursor": str(start + 1) if more else None})
            return _Resp({"conversations": [
                {"conversation_id": c["id"], "start_time_unix_secs": c["ts"]} for c in items
            ], "has_more": False, "next_cursor": None})
        cid = url.rsplit("/", 1)[-1]
        c = next(x for x in self.convs if x["id"] == cid)
        return _Resp({
            "metadata": {"start_time_unix_secs": c["ts"]},
            "conversation_initiation_client_data": {
                "dynamic_variables": {"userId": c["_uid"], "conversationId": c.get("aura", "")}
            },
            "transcript": [],
        })


def _client(convs, honour_filter=True, honour_time=True, page_one_at_a_time=False):
    el = _load_elevenlabs()
    c = el.ElevenLabsClient(key="test-key")
    c.session = _FakeSession(convs, honour_filter, honour_time, page_one_at_a_time)
    return el, c


class UserFilterIsSent(unittest.TestCase):
    def test_every_single_user_walk_sends_user_id(self):
        """The narrowing must actually be requested, in each implementation."""
        for path in SINGLE_USER_WALKS:
            with self.subTest(script=str(path.relative_to(REPO_ROOT))):
                self.assertRegex(
                    _text(path), r"""["']user_id["']\s*\]?\s*=""",
                    "this walk does not set a `user_id` list param — without it the walk "
                    "reverts to O(workspace voice volume)",
                )

    def test_voice_issue_sends_user_id_on_the_list_call(self):
        """Asserted on the wire, not by grepping for the string."""
        convs = [{"id": "c1", "ts": 1000, "_uid": TARGET}]
        el, c = _client(convs)
        c.find_conversation(user_id=TARGET, start_unix=0, end_unix=9999)
        self.assertTrue(c.session.list_calls, "no list call was issued")
        for call in c.session.list_calls:
            self.assertEqual(call.get("user_id"), TARGET)


class IgnoredFilterDegradesToSlowNeverToWrong(unittest.TestCase):
    """The asymmetry that matters: a dropped filter is slow, a trusted one is wrong.

    ElevenLabs answers an unknown query param with 200 and a full page, so a renamed
    parameter cannot be detected from the response envelope. Identity therefore has to
    be decided on the detail record. These tests hold with the filter ignored.
    """

    def test_a_foreign_user_is_never_returned(self):
        convs = [
            {"id": "c1", "ts": 1000, "_uid": OTHER},
            {"id": "c2", "ts": 1001, "_uid": OTHER},
            {"id": "c3", "ts": 1002, "_uid": TARGET},
        ]
        el, c = _client(convs, honour_filter=False)
        got = c.find_conversation(user_id=TARGET, start_unix=0, end_unix=9999)
        self.assertTrue(got.found)
        self.assertEqual(got.conversation.aura_user_id, TARGET,
                         "returned another user's conversation — the filter was treated "
                         "as a correctness boundary, which it cannot be")

    def test_target_absent_yields_no_match_not_someone_else(self):
        convs = [{"id": "c1", "ts": 1000, "_uid": OTHER}]
        el, c = _client(convs, honour_filter=False)
        got = c.find_conversation(user_id=TARGET, start_unix=0, end_unix=9999)
        self.assertFalse(got.found)

    def test_the_degradation_is_reported(self):
        convs = [
            {"id": "c1", "ts": 1000, "_uid": OTHER},
            {"id": "c2", "ts": 1001, "_uid": TARGET},
        ]
        el, c = _client(convs, honour_filter=False)
        got = c.find_conversation(user_id=TARGET, start_unix=0, end_unix=9999)
        self.assertIs(got.server_side_user_filter_effective, False)

    def test_a_healthy_run_is_not_flagged_as_degraded(self):
        """The negative case — a working filter must not report itself broken."""
        convs = [
            {"id": "c1", "ts": 1000, "_uid": OTHER},
            {"id": "c2", "ts": 1001, "_uid": TARGET},
        ]
        el, c = _client(convs, honour_filter=True)
        got = c.find_conversation(user_id=TARGET, start_unix=0, end_unix=9999)
        self.assertTrue(got.found)
        self.assertIs(got.server_side_user_filter_effective, True)
        self.assertEqual(got.detail_fetches, 1,
                         "a filtered list should cost one detail fetch, not a workspace walk")


class CappedIsNotAbsent(unittest.TestCase):
    """A bounded search that stops early must not look like a clean miss."""

    def test_hitting_the_cap_is_flagged_and_worded_as_not_evidence(self):
        convs = [{"id": f"c{i}", "ts": 1000 + i, "_uid": OTHER} for i in range(10)]
        convs.append({"id": "target", "ts": 1100, "_uid": TARGET})
        el, c = _client(convs, honour_filter=False)
        got = c.find_conversation(user_id=TARGET, start_unix=0, end_unix=9999,
                                  max_detail_fetches=3)
        self.assertFalse(got.found)
        self.assertTrue(got.capped)
        reason = got.miss_reason("last 7d", el.RETENTION_DAYS)
        self.assertIn("NOT evidence", reason)
        self.assertNotIn("no match in", reason,
                         "a capped search must not be phrased as an absence")

    def test_an_exhausted_search_is_not_flagged_as_capped(self):
        convs = [{"id": "c1", "ts": 1000, "_uid": OTHER}]
        el, c = _client(convs, honour_filter=False)
        got = c.find_conversation(user_id=TARGET, start_unix=0, end_unix=9999,
                                  max_detail_fetches=40)
        self.assertFalse(got.found)
        self.assertFalse(got.capped)
        self.assertIn("no match in", got.miss_reason("last 7d", el.RETENTION_DAYS))


class FamilyMembership(unittest.TestCase):
    """The assertion a per-filename `diff -q` structurally cannot make."""

    def _convai_list_walkers(self):
        """Files that page /v1/convai/conversations — by shape, not by filename."""
        for path in sorted(PLUGINS.rglob("*.py")):
            text = path.read_text(encoding="utf-8", errors="replace")
            if "convai/conversations" not in text:
                continue
            # A *list* walk pages the collection; a detail-only fetch does not.
            if re.search(r"page_size|next_cursor|has_more", text):
                yield path

    def test_family_membership_is_complete(self):
        found = set(self._convai_list_walkers())
        declared = set(SINGLE_USER_WALKS) | NEEDS_EVERY_USER
        undeclared = {p.relative_to(REPO_ROOT) for p in found - declared}
        self.assertEqual(
            undeclared, set(),
            "a /v1/convai/conversations list walk exists outside this guard — add it to "
            "SINGLE_USER_WALKS (and send `user_id`), or to NEEDS_EVERY_USER with a reason",
        )
        self.assertEqual(
            {p.relative_to(REPO_ROOT) for p in declared - found}, set(),
            "a declared walker no longer pages this endpoint — update the lists",
        )


class GiveUpIsNotAnAbsence(unittest.TestCase):
    """`investigate-voice-issue` bounds its walk, so it can stop before matching.

    That outcome must stay distinguishable from "no such call". Collapsing them is what
    made a capped search report `no match … retention is ~10 days` — a false negative
    that reads as a finding, and names the wrong cause while doing it.
    """

    SCRIPT = PLUGINS / "eng/skills/investigate-voice-issue/scripts/elevenlabs.py"

    def test_search_result_exposes_capped(self):
        text = _text(self.SCRIPT)
        self.assertIn("capped", text)
        self.assertRegex(
            text, r"class ConversationSearch",
            "the bounded walk must return a result type carrying why it stopped, not a "
            "bare Optional that erases the difference",
        )

    def test_caller_branches_on_capped_before_reporting_a_miss(self):
        caller = _text(PLUGINS / "eng/skills/investigate-voice-issue/scripts/investigate.py")
        self.assertIn(
            "miss_reason", caller,
            "the caller renders its own miss string instead of asking the search why it "
            "stopped — that is how a give-up becomes an apparent absence",
        )



class PageLimitIsNotExhaustion(unittest.TestCase):
    """The second budget. Running out of list pages is also a give-up.

    Summaries come back newest-first. Without server-side time bounds the walk has to
    page forward from *now* to reach an older window, and the page budget can run out
    before it arrives — zero candidates examined, and a `capped`-only check would call
    that a clean exhaustion and blame retention. Same false negative as the detail-fetch
    cap, one layer down, which is why `exhaustive` has to consider both.
    """

    def test_time_bounds_are_sent_server_side(self):
        """The narrowing that stops the page budget mattering in the first place."""
        el, c = _client([{"id": "c1", "ts": 500, "_uid": TARGET}])
        c.find_conversation(user_id=TARGET, start_unix=400, end_unix=600)
        call = c.session.list_calls[0]
        self.assertEqual(call.get("call_start_after_unix"), 400)
        self.assertEqual(call.get("call_start_before_unix"), 600)

    def test_running_out_of_pages_is_flagged_and_not_called_exhaustion(self):
        # Target sits far past the page budget; server ignores the time bounds, so the
        # walk has to page through everything newer to reach it.
        convs = [{"id": f"n{i}", "ts": 9000 - i, "_uid": OTHER} for i in range(30)]
        convs.append({"id": "target", "ts": 500, "_uid": TARGET})
        el, c = _client(convs, honour_filter=False, honour_time=False,
                        page_one_at_a_time=True)
        got = c.find_conversation(user_id=TARGET, start_unix=400, end_unix=600,
                                  max_detail_fetches=1000)
        self.assertFalse(got.found)
        self.assertTrue(got.page_limit_hit)
        self.assertFalse(got.exhaustive)
        reason = got.miss_reason("last 7d", el.RETENTION_DAYS)
        self.assertIn("NOT evidence", reason)
        self.assertNotIn("to exhaustion", reason)

    def test_a_genuinely_exhausted_walk_is_still_called_exhaustive(self):
        """Negative case — the flag must not fire on a walk that simply finished."""
        el, c = _client([{"id": "c1", "ts": 500, "_uid": OTHER}],
                        page_one_at_a_time=True)
        got = c.find_conversation(user_id=TARGET, start_unix=400, end_unix=600,
                                  max_detail_fetches=1000)
        self.assertFalse(got.found)
        self.assertFalse(got.page_limit_hit)
        self.assertTrue(got.exhaustive)
        self.assertIn("to exhaustion", got.miss_reason("last 7d", el.RETENTION_DAYS))


if __name__ == "__main__":
    unittest.main()
