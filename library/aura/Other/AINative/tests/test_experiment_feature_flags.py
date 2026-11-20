"""Feature-flag records are never treated as ended experiments (ENG-1877).

Aura's Edit email kill switch is stored as an experiment record, `editEmailEnabled`, with one `active`
letter: the "ended, winner a" shape. Every ended-experiment tool would act on it: sweep would file a
card to remove its code, archive would delete the record (Edit email gone for everyone), monitor would
analyse it, sync would end-date it in Learnings. The rule lives in three scripts (and two SKILL.md
steps), so one test runs every implementation against the same records, then each tool's own path
against a record exactly like the real one.
"""

import contextlib
import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = {
    "archive": REPO / "plugins/eng/skills/archive-ended-experiments/scripts/reconcile.py",
    "monitor": REPO / "plugins/data/skills/monitor-all-experiments/scripts/resolve_running.py",
    "sync": REPO / "plugins/data/skills/sync-experiments/scripts/reconcile.py",
}


def _load(name, path):
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location(f"_ff_{name}", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(path.parent))


MODS = {k: _load(k, p) for k, p in SCRIPTS.items()}

# The record MobileAura will create (ENGMOB-3182).
EDIT_EMAIL = {"id": "editEmailEnabled", "active": "a", "endedAt": None, "deletedAt": None, "featureFlag": True,
              "notes": "FEATURE FLAG — not an A/B test: Edit email kill switch (ENGMOB-3182)"}
CASES = [
    (EDIT_EMAIL, True),
    ({"active": "a", "notes": "FEATURE FLAG — kill switch"}, True),           # what ExperimentService stores today
    ({"active": "a", "notes": "  feature flag: lowercase, leading spaces"}, True),
    ({"active": "a", "description": "FEATURE FLAG"}, True),
    ({"active": "a", "featureFlag": True}, True),
    ({"active": "a", "notes": "Winner a. Not a FEATURE FLAG."}, False),       # the words, not at the start
    ({"active": "a", "featureFlag": "true"}, False),                         # only a real boolean
    ({"active": "ab", "notes": None}, False),
    (None, False),
]


class OneRuleEverywhereTest(unittest.TestCase):
    def test_every_implementation_agrees(self):
        for name, mod in MODS.items():
            for row, want in CASES:
                self.assertEqual(mod.is_feature_flag(row), want, f"{name}: {row}")


class ArchiveTest(unittest.TestCase):
    def test_a_feature_flag_is_never_an_archive_candidate(self):
        m = MODS["archive"]
        export = {"editEmailEnabled": {"active": "a"},             # the RTDB copy: no notes
                  "oldTest": {"active": "b"}, "running": {"active": "ab"}}
        with tempfile.TemporaryDirectory() as d:
            ex, out = pathlib.Path(d) / "x.json", pathlib.Path(d) / "t.json"
            ex.write_text(json.dumps(export), encoding="utf-8")
            err = io.StringIO()
            with mock.patch.object(m, "load_catalog", return_value={"editEmailEnabled": EDIT_EMAIL}), \
                    mock.patch.object(m, "load_knowledge", return_value={}), \
                    mock.patch.object(m, "CLIENTS", {}), \
                    mock.patch.object(sys, "argv", ["reconcile.py", "--export", str(ex), "--out", str(out)]), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                m.main()
            payload = json.loads(out.read_text(encoding="utf-8"))
        listed = [k for keys in payload["tranches"].values() for k in keys]
        self.assertEqual(listed, ["oldTest"])
        self.assertEqual(payload["totals"]["skipped"], {"featureFlag": 1})
        self.assertIn("editEmailEnabled", err.getvalue())


    def test_no_catalogue_refuses_rather_than_risk_archiving_a_flag(self):
        m = MODS["archive"]
        with tempfile.TemporaryDirectory() as d:
            ex, out = pathlib.Path(d) / "x.json", pathlib.Path(d) / "t.json"
            ex.write_text(json.dumps({"editEmailEnabled": {"active": "a"}}), encoding="utf-8")
            with mock.patch.object(m, "load_catalog", return_value={}), \
                    mock.patch.object(m, "load_knowledge", return_value={}), mock.patch.object(m, "CLIENTS", {}), \
                    mock.patch.object(sys, "argv", ["reconcile.py", "--export", str(ex), "--out", str(out)]), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as e:
                    m.main()
            self.assertIn("feature flag", str(e.exception.code))
            self.assertFalse(out.exists())


class MonitorTest(unittest.TestCase):
    def test_a_feature_flag_is_never_in_the_running_set(self):
        m = MODS["monitor"]
        rows = [dict(EDIT_EMAIL), {"id": "realTest", "active": "ab", "endedAt": None, "deletedAt": None}]
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(m, "fetch_all", return_value=rows), mock.patch.object(m, "load_token", return_value="t"), \
                mock.patch.object(sys, "argv", ["resolve_running.py", "--json"]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            m.main()
        self.assertEqual([e["id"] for e in json.loads(out.getvalue())], ["realTest"])  # still a list
        self.assertIn("skipped.featureFlag 1: editEmailEnabled", err.getvalue())


class SyncTest(unittest.TestCase):
    def test_a_feature_flag_is_never_synced_or_end_dated(self):
        m = MODS["sync"]
        b = m.classify([dict(EDIT_EMAIL)], {})
        self.assertEqual(b["skipped_feature_flag"], ["editEmailEnabled"])
        self.assertEqual(b["missing_row"], [])  # no Learnings row created for it
        self.assertEqual(m.disagreement_count(b), 0)


class SweepTest(unittest.TestCase):
    def test_the_sweep_steps_drop_feature_flags(self):
        # sweep-ended-experiments has no script: its Step 3 text is the implementation.
        text = (REPO / "plugins/eng/skills/sweep-ended-experiments/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Feature flags are never swept", text)
        self.assertIn("skipped.featureFlag", text)
        self.assertIn('starts with "FEATURE FLAG"', text)


if __name__ == "__main__":
    unittest.main()
