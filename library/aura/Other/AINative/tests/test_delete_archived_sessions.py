"""delete-archived-sessions plan.py — which sessions a cleanup deletes.

**What has to hold.** Deletion cannot be undone, so the planner must never pick a session
the user did not mean: not a live one, not a pinned one, not one after the cutoff. And a
session it leaves out must be counted by reason — `listed` always equals `to_delete` plus
the skipped counts — so a summary cannot hide a filter.

Rows use the exact keys `list_sessions` emits. Stdlib unittest, no network.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import os
import unittest
import unittest.mock

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "plugins/shared/skills/delete-archived-sessions/scripts/plan.py"

spec = importlib.util.spec_from_file_location("_delete_archived_plan", SCRIPT)
plan_mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = plan_mod
spec.loader.exec_module(plan_mod)


def session(n: int, archived=True, pinned=None, running=False, day="2026-10-15", pr_state=None) -> dict:
    row = {
        "sessionId": f"local_{n:08d}-0000-0000-0000-000000000000",
        "title": f"Session {n}",
        "cwd": "/Users/x/Development/Aura/AI/AINative",
        "isArchived": archived,
        "isRunning": running,
        "lastActivityAt": f"{day}T12:00:00.000Z",
        "group": None,
        "link": f"claude://claude.ai/epitaxy/local_{n}",
    }
    if pinned is not None:  # the key is omitted when the app never recorded a pin
        row["pinned"] = pinned
    if pr_state:
        row["prNumber"] = n
        row["prState"] = pr_state
    return row


class PlanTest(unittest.TestCase):
    def test_only_archived_unpinned_idle_sessions_are_deleted(self):
        rows = [session(1), session(2, archived=False), session(3, pinned=True),
                session(4, running=True), session(5, pinned=False)]
        r = plan_mod.plan(rows)
        self.assertEqual([i for b in r["batches"] for i in b],
                         [rows[0]["sessionId"], rows[4]["sessionId"]])
        self.assertEqual(r["skipped"], {"not_archived": 1, "pinned": 1, "running": 1})

    def test_every_listed_session_is_accounted_for(self):
        rows = [session(i, archived=i % 3 != 0, pinned=(i % 7 == 0) or None,
                        day="2026-09-01" if i % 2 else "2026-11-01",
                        pr_state="OPEN" if i % 5 == 0 else None) for i in range(1, 60)]
        r = plan_mod.plan(rows, before="2026-10-01", skip_open_prs=True)
        self.assertEqual(r["listed"], r["to_delete"] + sum(r["skipped"].values()))

    def test_cutoff_keeps_only_older_sessions(self):
        rows = [session(1, day="2026-09-30"), session(2, day="2026-10-01"), session(3, day="2026-10-02")]
        r = plan_mod.plan(rows, before="2026-10-01")
        self.assertEqual(r["batches"], [[rows[0]["sessionId"]]])
        self.assertEqual(r["skipped"], {"after_cutoff": 2})

    def test_open_prs_are_flagged_and_kept_unless_skipped(self):
        rows = [session(1, pr_state="OPEN"), session(2, pr_state="MERGED")]
        kept = plan_mod.plan(rows)
        self.assertEqual(kept["to_delete"], 2)
        self.assertEqual(kept["open_prs"], [{"title": "Session 1", "prNumber": 1}])
        skipped = plan_mod.plan(rows, skip_open_prs=True)
        self.assertEqual(skipped["to_delete"], 1)
        self.assertEqual(skipped["skipped"], {"open_pr": 1})

    def test_batches_hold_at_most_25(self):
        r = plan_mod.plan([session(i) for i in range(1, 58)])
        self.assertEqual([len(b) for b in r["batches"]], [25, 25, 7])

    def test_empty_archive_plans_nothing(self):
        r = plan_mod.plan([session(1, archived=False)])
        self.assertEqual((r["to_delete"], r["batches"], r["date_range"]), (0, [], None))


class ListingFileTest(unittest.TestCase):
    def test_reads_saved_tool_result_and_bare_json(self):
        rows = [session(1), session(2, archived=False)]
        with tempfile.TemporaryDirectory() as d:
            saved = pathlib.Path(d, "saved.txt")
            saved.write_text(json.dumps(rows, indent=2), encoding="utf-8")
            self.assertEqual(plan_mod.load_sessions(str(saved)), rows)
            wrapped = pathlib.Path(d, "wrapped.txt")
            wrapped.write_text("Result:\n" + json.dumps(rows) + "\n", encoding="utf-8")
            self.assertEqual(plan_mod.load_sessions(str(wrapped)), rows)

    def test_a_file_without_an_array_fails_loudly(self):
        with tempfile.TemporaryDirectory() as d:
            bad = pathlib.Path(d, "bad.txt")
            bad.write_text("Error: tool failed", encoding="utf-8")
            with self.assertRaises(SystemExit):
                plan_mod.load_sessions(str(bad))

    def test_cli_plan_then_batches_round_trip(self):
        rows = [session(i) for i in range(1, 30)]
        with tempfile.TemporaryDirectory() as d, no_hold(d):
            listing, out = pathlib.Path(d, "l.json"), pathlib.Path(d, "b.json")
            listing.write_text(json.dumps(rows), encoding="utf-8")
            for argv in (["plan", "--listing", str(listing), "--out", str(out)],
                         ["batches", "--file", str(out), "--start", "2"]):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf), unittest.mock.patch.object(sys, "argv", ["plan.py", *argv]):
                    plan_mod.main()
            lines = buf.getvalue().splitlines()
            self.assertEqual(len(lines), 1)
            n, total, ids = lines[0].split(" ", 2)
            self.assertEqual((n, total, len(json.loads(ids))), ("2", "2", 4))


def no_hold(home):
    """A HOME with no hold marker and no AURA_LEGAL_HOLD, whatever this machine has."""
    env = {k: v for k, v in os.environ.items() if k != "AURA_LEGAL_HOLD"}
    env["HOME"] = str(home)
    return unittest.mock.patch.dict(os.environ, env, clear=True)


class LegalHoldTest(unittest.TestCase):
    """Under an incident data hold no delete list may exist: plan and batches refuse, check still reads."""

    def run_cli(self, home, argv, env=None):
        buf, err = io.StringIO(), io.StringIO()
        with no_hold(home), unittest.mock.patch.dict(os.environ, env or {}), \
                contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err), \
                unittest.mock.patch.object(sys, "argv", ["plan.py", *argv]):
            try:
                plan_mod.main()
                code = 0
            except SystemExit as e:
                code = e.code
        return code, buf.getvalue(), err.getvalue()

    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp())
        self.listing = self.d / "l.json"
        self.listing.write_text(json.dumps([session(1), session(2)]), encoding="utf-8")
        self.batches = self.d / "b.json"
        self.batches.write_text(json.dumps([["s1", "s2"]]), encoding="utf-8")

    def assert_refused(self, env=None, marker=False):
        if marker:
            (self.d / ".claude" / "legal-hold").mkdir(parents=True)
            (self.d / ".claude" / "legal-hold" / "active").write_text("")
        out_file = self.d / "out.json"
        for argv in (["plan", "--listing", str(self.listing), "--out", str(out_file)],
                     ["batches", "--file", str(self.batches)]):
            code, out, err = self.run_cli(self.d, argv, env)
            self.assertEqual(code, plan_mod.HOLD_EXIT, argv)
            self.assertEqual(out, "")
            self.assertIn("incident data hold", err)
        self.assertFalse(out_file.exists())

    def test_marker_file_refuses(self):
        self.assert_refused(marker=True)

    def test_env_refuses(self):
        self.assert_refused(env={"AURA_LEGAL_HOLD": "1"})

    def test_env_zero_or_empty_is_no_hold(self):
        for v in ("0", ""):
            code, out, _ = self.run_cli(self.d, ["plan", "--listing", str(self.listing)], {"AURA_LEGAL_HOLD": v})
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out)["to_delete"], 2)

    def test_check_still_reads_under_a_hold(self):
        code, out, _ = self.run_cli(self.d, ["check", "--listing", str(self.listing)], {"AURA_LEGAL_HOLD": "1"})
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["archived_left"], 2)


class CheckTest(unittest.TestCase):
    def test_reports_what_is_still_archived(self):
        r = plan_mod.check([session(1, pinned=True), session(2, archived=False)])
        self.assertEqual(r["archived_left"], 1)
        self.assertTrue(r["sessions"][0]["pinned"])


import unittest.mock  # noqa: E402  (used by the CLI test)

if __name__ == "__main__":
    unittest.main()
