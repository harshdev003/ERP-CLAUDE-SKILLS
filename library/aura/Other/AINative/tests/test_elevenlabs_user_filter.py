"""Tests for the server-side `user_id` filter on the ElevenLabs per-user cost walk.

`report-elevenlabs-user-cost` attributes voice cost to one Firebase UID. The
`userId` lives only on per-conversation *detail*, so the walk needs a detail
fetch per candidate — but it does NOT need to detail-fetch the whole workspace.
`/v1/convai/conversations` filters server-side on `user_id`, which turns the walk
from `O(workspace voice volume)` into `O(that user's calls)`.

Two properties are pinned here, and the second matters more than the first:

1. The `user_id` parameter is actually sent on every page of the list walk.
   Without it the walk silently reverts to O(workspace) and a per-canceller cost
   run takes over an hour, which is what made the Group 4 cadence time-box fire.

2. **A silently-ignored filter degrades to slow, never to wrong.** ElevenLabs
   ignores unknown query params without erroring — a bogus param returns 200 with
   a full unfiltered page (verified against the live API 2026-08-18). So if
   `user_id` is ever renamed, the list comes back unfiltered and every workspace
   conversation would be attributed to the requested user. The per-detail `userId`
   check is what prevents that, so these tests assert the over-count *cannot*
   happen and that the degradation is reported rather than absorbed.

The failure this guards against is silent in both directions — a dropped filter
produces a correct number slowly, and a dropped client check produces a wrong
number quickly. Neither raises. No network: the HTTP layer is stubbed at its
single chokepoint (`requests.get`).
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import time
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "plugins/eng/skills/report-elevenlabs-user-cost/scripts/report.py"

TARGET = "TargetUser000000000000000001"
OTHER = "OtherUser00000000000000000002"

# Every test below drives the report with `--window last-7d`, so the stubbed
# conversations must fall inside a *rolling* window. A fixed epoch works on the
# day it is written and then silently empties the window forever after — two
# days back from now keeps the rows in range without sitting on either boundary.
IN_WINDOW_TS = int(time.time()) - 2 * 86_400


def load():
    spec = importlib.util.spec_from_file_location("el_user_cost_report", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["el_user_cost_report"] = mod
    spec.loader.exec_module(mod)
    return mod


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.ok = 200 <= status < 300
        self.text = "stub"

    def json(self):
        return self._payload


def detail(uid, duration=60.0, burst=False):
    return {
        "agent_id": "agent_x",
        "agent_name": "Prod - Aura Coach Agent",
        "conversation_initiation_client_data": {"dynamic_variables": {"userId": uid}},
        "metadata": {
            "start_time_unix_secs": IN_WINDOW_TS,
            "call_duration_secs": duration,
            "charging": {"is_burst": burst, "call_charge": 1.0},
        },
    }


class Stub:
    """Stands in for `requests.get`, recording every list call's params."""

    def __init__(self, *, honour_user_filter=True, workspace=None, scrubbed=()):
        self.honour = honour_user_filter
        # conversation_id -> owning uid
        self.workspace = workspace or {}
        # conversation_ids whose detail 404s (purged by ElevenLabs retention)
        self.scrubbed = set(scrubbed)
        self.list_calls: list[dict] = []
        self.detail_fetches: list[str] = []

    def __call__(self, url, headers=None, params=None, timeout=None):
        params = params or {}
        if url.endswith("/convai/conversations"):
            self.list_calls.append(dict(params))
            items = sorted(self.workspace)
            if self.honour and params.get("user_id"):
                items = [c for c in items if self.workspace[c] == params["user_id"]]
            return FakeResponse(
                {
                    "conversations": [
                        {
                            "conversation_id": c,
                            "start_time_unix_secs": IN_WINDOW_TS,
                        }
                        for c in items
                    ],
                    "has_more": False,
                }
            )
        cid = url.rsplit("/", 1)[-1]
        self.detail_fetches.append(cid)
        if cid in self.scrubbed:
            return FakeResponse({"detail": "not found"}, status=404)
        return FakeResponse(detail(self.workspace[cid]))


def run(mod, stub, uid=TARGET, argv_extra=()):
    """Drive main() with the HTTP layer stubbed, returning the parsed report."""
    import json
    import tempfile

    out = pathlib.Path(tempfile.mkdtemp()) / "r.json"
    orig_get, orig_argv, orig_env = mod.requests.get, sys.argv, dict(mod.os.environ)
    mod.requests.get = stub
    mod.os.environ["ELEVENLABS_ADMIN_API_KEY"] = "stub-key"
    sys.argv = ["report.py", uid, "--window", "last-7d", "--json", str(out), *argv_extra]
    try:
        mod.main()
    except SystemExit as e:  # argparse/normal exit
        if e.code not in (0, None):
            raise
    finally:
        mod.requests.get, sys.argv = orig_get, orig_argv
        mod.os.environ.clear()
        mod.os.environ.update(orig_env)
    return json.loads(out.read_text())


class ServerSideFilterIsRequested(unittest.TestCase):
    def test_user_id_is_sent_on_the_list_call(self):
        """The whole point of the fix: the list walk is narrowed server-side."""
        mod = load()
        stub = Stub(workspace={f"c{i}": (TARGET if i < 2 else OTHER) for i in range(50)})
        run(mod, stub)
        self.assertTrue(stub.list_calls, "no list call was issued")
        for params in stub.list_calls:
            self.assertEqual(
                params.get("user_id"),
                TARGET,
                "list page issued without user_id — walk reverts to O(workspace)",
            )

    def test_only_the_users_conversations_are_detail_fetched(self):
        """O(this user), not O(workspace) — 2 fetches out of a 50-call workspace."""
        mod = load()
        stub = Stub(workspace={f"c{i}": (TARGET if i < 2 else OTHER) for i in range(50)})
        report = run(mod, stub)
        self.assertEqual(len(stub.detail_fetches), 2)
        self.assertEqual(report["total"]["call_count"], 2)
        self.assertEqual(report["scanned_conversations"], 2)


class IgnoredFilterDegradesToSlowNotWrong(unittest.TestCase):
    """The load-bearing half. A renamed param must not become an over-count."""

    def test_cost_is_unchanged_when_the_filter_is_ignored(self):
        mod = load()
        workspace = {f"c{i}": (TARGET if i < 2 else OTHER) for i in range(50)}
        honoured = run(mod, Stub(honour_user_filter=True, workspace=workspace))
        ignored = run(mod, Stub(honour_user_filter=False, workspace=workspace))
        self.assertEqual(
            honoured["total"]["cost_usd"],
            ignored["total"]["cost_usd"],
            "an ignored server-side filter changed the cost figure",
        )
        self.assertEqual(ignored["total"]["call_count"], 2)

    def test_other_users_calls_are_never_attributed_to_the_target(self):
        """Without the client-side check this would report all 50 calls."""
        mod = load()
        stub = Stub(
            honour_user_filter=False,
            workspace={f"c{i}": (TARGET if i < 2 else OTHER) for i in range(50)},
        )
        report = run(mod, stub)
        self.assertEqual(report["total"]["call_count"], 2)
        self.assertEqual(report["rejected_by_uid_check"], 48)

    def test_the_degradation_is_reported_not_absorbed(self):
        mod = load()
        stub = Stub(
            honour_user_filter=False,
            workspace={f"c{i}": (TARGET if i < 2 else OTHER) for i in range(50)},
        )
        report = run(mod, stub)
        self.assertFalse(
            report["server_side_user_filter_effective"],
            "an ineffective filter was not flagged — the run just gets slower silently",
        )
        self.assertTrue(
            any("user_id filter" in n for n in report.get("notes", [])),
            "no caveat explained why the walk degraded",
        )

    def test_a_working_filter_is_not_falsely_flagged(self):
        """The negative case: don't cry wolf on a healthy run."""
        mod = load()
        stub = Stub(workspace={f"c{i}": (TARGET if i < 2 else OTHER) for i in range(50)})
        report = run(mod, stub)
        self.assertTrue(report["server_side_user_filter_effective"])
        self.assertEqual(report["rejected_by_uid_check"], 0)
        self.assertFalse([n for n in report.get("notes", []) if "user_id filter" in n])

    def test_retention_scrubbed_rows_do_not_dilute_the_detector(self):
        """Bugbot finding: 404s bump seen_total but can never bump `rejected`.

        On a wide (scrubbed-heavy) window an ignored filter would otherwise sit
        under a seen_total-based threshold and go unreported — the guard failing
        in exactly the direction it exists to catch. The denominator must be the
        conversations we actually got a verdict on.
        """
        mod = load()
        # 200 listed: 160 scrubbed by retention, 40 attributable (2 ours, 38 not).
        # rejected=38 is < 200//2, so a seen_total-based check would NOT fire.
        workspace = {f"c{i}": (TARGET if i < 2 else OTHER) for i in range(200)}
        scrubbed = [f"c{i}" for i in range(40, 200)]
        stub = Stub(honour_user_filter=False, workspace=workspace, scrubbed=scrubbed)
        report = run(mod, stub)
        self.assertEqual(report["scrubbed_conversations"], 160)
        self.assertEqual(report["rejected_by_uid_check"], 38)
        self.assertLess(
            report["rejected_by_uid_check"],
            report["scanned_conversations"] // 2,
            "test no longer exercises the dilution case",
        )
        self.assertFalse(
            report["server_side_user_filter_effective"],
            "scrubbed rows diluted the ratio and hid an ignored filter",
        )
        self.assertEqual(report["total"]["call_count"], 2)

    def test_a_small_mixed_page_is_not_flagged_as_ignored(self):
        """A handful of rejections on a tiny walk is noise, not a renamed param."""
        mod = load()
        stub = Stub(
            honour_user_filter=False,
            workspace={f"c{i}": (TARGET if i < 3 else OTHER) for i in range(6)},
        )
        report = run(mod, stub)
        self.assertTrue(
            report["server_side_user_filter_effective"],
            "flagged the filter as ignored on too small a sample",
        )


if __name__ == "__main__":
    unittest.main()
