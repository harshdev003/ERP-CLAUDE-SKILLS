"""find_sessions.py — which sessions a daily distill run reads.

**What has to hold.** The routine runs unattended, so its only evidence that it read
the whole day is the finder: a session it never lists is a session nobody distils,
and the miss has no shape. Three properties carry that:

- A turn counts as human only when the transcript says so. Scheduled runs, cadence
  sweeps and the routine's own `claude -p` runs must never become candidates —
  otherwise the routine distils itself and its own sweeps every morning.
- The window applies to turns, not files. Sessions run for days; a file-level window
  would re-read a week of turns daily, or miss yesterday's turns in an old session.
- The ledger makes a re-run a no-op, and a session it skips is counted as
  `already_distilled` rather than vanishing.

Stdlib unittest, no network; transcripts are written to a temp dir.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import importlib.util
import io
import json
import pathlib
import sys
import os
import tempfile
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "plugins/shared/skills/distill-sessions-daily/scripts/find_sessions.py"

spec = importlib.util.spec_from_file_location("_distill_find", SCRIPT)
fs = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = fs
spec.loader.exec_module(fs)

SINCE = "2026-10-08T00:00:00+00:00"
UNTIL = "2026-10-09T00:00:00+00:00"
ROOT = "/Users/x/Development/Aura"


def turn(ts: str, origin: str = "human", text: str = "do the thing", **extra) -> dict:
    return {"type": "user", "turnOrigin": origin, "timestamp": ts,
            "cwd": f"{ROOT}/AI/AINative", "gitBranch": "main",
            "message": {"role": "user", "content": text}, **extra}


class FinderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.projects = pathlib.Path(self.tmp.name) / "projects"
        self.ledger = pathlib.Path(self.tmp.name) / "ledger.json"
        # Never the machine's real hold marker or AURA_LEGAL_HOLD: a test reads only its own.
        self.hold = pathlib.Path(self.tmp.name) / "legal-hold"
        self.hold.mkdir()
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("AURA_LEGAL_HOLD", None)
        (self.projects / "proj").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, session: str, records: list[dict]) -> None:
        path = self.projects / "proj" / f"{session}.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

    def run_cli(self, *argv: str) -> str:
        out = io.StringIO()
        old = sys.argv
        sys.argv = ["find_sessions.py", "--projects", str(self.projects),
                    "--ledger", str(self.ledger), "--hold-dir", str(self.hold), *argv]
        try:
            with contextlib.redirect_stdout(out):
                fs.main()
        finally:
            sys.argv = old
        return out.getvalue()

    def find(self) -> dict:
        return json.loads(self.run_cli("find", "--since", SINCE, "--until", UNTIL, "--root", ROOT))

    def test_scheduled_and_headless_sessions_are_counted_not_candidates(self):
        in_window = "2026-10-08T10:00:00Z"
        self.write("sched", [turn(in_window, origin="scheduled", scheduledTaskId="t")] * 5)
        self.write("sdk", [turn(in_window, origin="sdk")] * 5)
        self.write("human", [turn(in_window)] * 3)
        out = self.find()
        self.assertEqual([c["session_id"] for c in out["candidates"]], ["human"])
        self.assertEqual(out["skipped"]["scheduled"], 1)
        self.assertEqual(out["skipped"]["no_human_turns"], 1)

    def test_desktop_scheduled_prompt_marked_human_is_not_a_human_turn(self):
        # The desktop app labels a scheduled task's launching prompt turnOrigin=human,
        # with no scheduledTaskId. Counted as human, the routine would distil its own runs.
        prompt = '<scheduled-task name="distill-sessions-daily" file="x">run it</scheduled-task>'
        self.write("own-run", [turn("2026-10-08T08:00:00Z", text=prompt)] * 3)
        out = self.find()
        self.assertEqual(out["candidates"], [])
        self.assertEqual(out["skipped"]["scheduled"], 1)

    def test_scheduled_run_someone_talked_to_keeps_its_human_turns(self):
        records = [turn("2026-10-08T09:00:00Z", origin="scheduled", scheduledTaskId="t")]
        records += [turn("2026-10-08T10:00:00Z")] * 3
        self.write("joined", records)
        self.assertEqual([c["session_id"] for c in self.find()["candidates"]], ["joined"])

    def test_window_applies_to_turns_not_the_file(self):
        old = [turn("2026-10-01T10:00:00Z")] * 10
        self.write("long", old + [turn("2026-10-08T10:00:00Z")] * 3)
        (cand,) = self.find()["candidates"]
        self.assertEqual(cand["human_turns"], 3)
        self.assertEqual(cand["first_turn"], "2026-10-08T10:00:00Z")

    def test_ledger_makes_a_rerun_a_noop_and_counts_the_skip(self):
        self.write("s1", [turn("2026-10-08T10:00:00Z")] * 3)
        self.run_cli("mark", "s1", "--through", "2026-10-08T10:00:00Z",
                     "--run", "2026-10-09", "--outcome", "learnings")
        out = self.find()
        self.assertEqual(out["candidates"], [])
        self.assertEqual(out["skipped"]["already_distilled"], 1)

    def test_turns_after_the_ledger_mark_are_still_read(self):
        self.write("s1", [turn("2026-10-08T08:00:00Z")] * 3 + [turn("2026-10-08T20:00:00Z")] * 3)
        self.run_cli("mark", "s1", "--through", "2026-10-08T08:00:00Z",
                     "--run", "2026-10-08", "--outcome", "nothing")
        (cand,) = self.find()["candidates"]
        self.assertEqual(cand["human_turns"], 3)

    def test_failed_session_is_retried_after_the_window_moves_on(self):
        self.write("s1", [turn("2026-10-08T10:00:00Z")] * 3)
        self.run_cli("mark", "s1", "--outcome", "failed", "--run", "2026-10-09",
                     "--retry-from", SINCE)
        # The next day's window no longer contains the session's turns.
        out = json.loads(self.run_cli("find", "--since", "2026-10-09T00:00:00+00:00",
                                      "--until", "2026-10-10T00:00:00+00:00", "--root", ROOT))
        (cand,) = out["candidates"]
        self.assertTrue(cand["retry"])
        self.assertEqual(cand["human_turns"], 3)
        extracted = self.run_cli("extract", "s1", "--since", "2026-10-09T00:00:00+00:00",
                                 "--until", "2026-10-10T00:00:00+00:00")
        self.assertIn("do the thing", extracted)

    def test_success_after_failure_clears_the_retry(self):
        self.write("s1", [turn("2026-10-08T10:00:00Z")] * 3)
        self.run_cli("mark", "s1", "--outcome", "failed", "--run", "2026-10-09",
                     "--retry-from", SINCE)
        self.run_cli("mark", "s1", "--outcome", "learnings", "--run", "2026-10-10",
                     "--through", "2026-10-08T10:00:00Z")
        out = json.loads(self.run_cli("find", "--since", "2026-10-09T00:00:00+00:00",
                                      "--until", "2026-10-10T00:00:00+00:00", "--root", ROOT))
        self.assertEqual(out["candidates"], [])

    def test_sessions_outside_root_and_short_sessions_are_listed_with_a_reason(self):
        self.write("short", [turn("2026-10-08T10:00:00Z")] * 2)
        self.write("elsewhere", [dict(turn("2026-10-08T10:00:00Z"), cwd="/tmp/other")] * 3)
        out = self.find()
        reasons = {s["session_id"]: s["reason"] for s in out["skipped_sessions"]}
        self.assertEqual(reasons, {"short": "few_human_turns", "elsewhere": "outside_root"})

    def test_worktree_cwd_maps_to_its_main_checkout(self):
        self.assertEqual(fs.repo_root(f"{ROOT}/AI/AINative/.claude/worktrees/foo/plugins"),
                         f"{ROOT}/AI/AINative")

    def test_monday_window_reaches_back_to_friday(self):
        monday = dt.datetime(2026, 10, 12, 8, 0).astimezone()
        since, until = fs.default_window(monday)
        self.assertEqual((until - since).days, 3)
        self.assertEqual(since.weekday(), 4)

    def test_extract_prints_only_in_window_turns(self):
        self.write("s1", [turn("2026-10-07T10:00:00Z", text="old turn"),
                          turn("2026-10-08T10:00:00Z", text="new turn")])
        out = self.run_cli("extract", "s1", "--since", SINCE, "--until", UNTIL)
        self.assertIn("new turn", out)
        self.assertNotIn("old turn", out)




GUARDS = [REPO / f"plugins/shared/skills/{skill}/scripts/incident_guard.py"
          for skill in ("distill-sessions-daily", "distill-session")]


def titled(title: str) -> dict:
    return {"type": "custom-title", "customTitle": title}


class IncidentSessionTest(FinderTest):
    """An incident session is never read: a distill writes to AI Knowledge, Linear and PRs, none
    privileged. It is counted, never named, and `extract` refuses it."""

    def three(self, session: str, *extra: dict) -> None:
        self.write(session, [*extra, *[turn("2026-10-08T10:00:00Z", text=f"{session} secret")] * 3])

    def test_a_privileged_title_is_counted_and_listed_nowhere(self):
        for i, title in enumerate(("[Privileged] counsel prep", "[privileged & confidential] notes",
                                   "  [PRIVILEGE] x")):
            self.three(f"priv{i}", titled(title))
        self.three("plain", titled("Fix the cost store [Privileged] later"))
        out = self.find()
        self.assertEqual([c["session_id"] for c in out["candidates"]], ["plain"])
        self.assertEqual(out["skipped"]["privileged"], 3)
        for leak in ("priv0", "priv1", "priv2", "counsel prep", "priv0 secret"):
            self.assertNotIn(leak, json.dumps(out))

    def test_an_excluded_id_is_counted_and_listed_nowhere(self):
        (self.hold / "exclude-sessions").write_text("# incident sessions\nheld-one  # a note\n", encoding="utf-8")
        self.three("held-one")
        self.three("other")
        out = self.find()
        self.assertEqual([c["session_id"] for c in out["candidates"]], ["other"])
        self.assertEqual(out["skipped"]["incident"], 1)
        self.assertNotIn("held-one", json.dumps(out))

    def test_a_hold_with_no_exclude_file_reads_nothing(self):
        # With no list to go by, every session is held back rather than guessed at.
        (self.hold / "active").write_text("hold", encoding="utf-8")
        self.three("a")
        self.three("b")
        out = self.find()
        self.assertEqual((out["candidates"], out["skipped"]["incident"]), ([], 2))
        # With the list, only the listed session is held back.
        (self.hold / "exclude-sessions").write_text("a\n", encoding="utf-8")
        out = self.find()
        self.assertEqual(([c["session_id"] for c in out["candidates"]], out["skipped"]["incident"]), (["b"], 1))

    def test_extract_refuses_an_incident_session_and_prints_nothing_of_it(self):
        (self.hold / "exclude-sessions").write_text("held-one\n", encoding="utf-8")
        self.three("held-one")
        self.three("priv", titled("[Privileged] x"))
        for session in ("held-one", "priv"):
            with self.assertRaises(SystemExit) as e:
                self.run_cli("extract", session, "--since", SINCE, "--until", UNTIL)
            self.assertEqual(e.exception.code, 4)
        self.assertIn("other secret", self.extract_ok("other"))

    def extract_ok(self, session: str) -> str:
        self.three(session)
        return self.run_cli("extract", session, "--since", SINCE, "--until", UNTIL)

    def test_aura_legal_hold_marks_a_hold_like_the_file(self):
        os.environ["AURA_LEGAL_HOLD"] = "1"
        self.three("a")
        self.assertEqual(self.find()["skipped"]["incident"], 1)
        os.environ["AURA_LEGAL_HOLD"] = "0"
        self.assertEqual([c["session_id"] for c in self.find()["candidates"]], ["a"])

    def test_the_guard_cli_skips_by_the_transcript_title_and_the_exclude_file(self):
        import subprocess
        self.three("priv", titled("[Privileged] x"))
        self.three("held-one")
        self.three("other", titled("ordinary work"))
        (self.hold / "exclude-sessions").write_text("held-one\n", encoding="utf-8")
        for guard in GUARDS:
            codes = {sid: subprocess.run([sys.executable, str(guard), "--projects", str(self.projects),
                                          "--hold-dir", str(self.hold), "check", sid],
                                         capture_output=True, text=True).returncode
                     for sid in ("priv", "held-one", "other")}
            self.assertEqual(codes, {"priv": 4, "held-one": 4, "other": 0}, guard)

    def test_both_guard_copies_match(self):
        a, b = (g.read_text(encoding="utf-8") for g in GUARDS)
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
