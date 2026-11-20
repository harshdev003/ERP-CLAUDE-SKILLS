"""eng:map-vendor-data-flows: field names and counts only, read from git objects, tag rather than drop (ENG-1919)."""
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "plugins/eng/skills/map-vendor-data-flows/scripts"
REFS = SCRIPTS.parent / "references"
# Values the fixture code carries. None may appear in any output file.
VALUES = ["SECRET_VALUE_123", "jane.doe@example.com", "Ab3dEf6hIj9kLm2nOp5qRs8tUv1w", "10.20.30.40", "obj.notes"]


def _load(name):
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(f"mvdf_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


scan = _load("scan")
guard = _load("guard")
reach = _load("reach")
render = _load("render")

AURA_FILES = {
    "src/logic/Trackings.js": "const Trackings = (title, data = {}) => {\n  Mixpanel.trackWithProperties(title, data)\n}\n",
    "src/screens/Mood.js": (
        "import Trackings from '../logic/Trackings'\n"
        "const save = (obj) => {\n"
        "  Trackings('Add Mood', { Level: obj.level, Notes: obj.notes, Owner: 'jane.doe@example.com' })\n"
        "}\n"
        "class Screen {\n"
        "  logEvent = (event, data) => {\n"
        "    Trackings(event, { ...data, Screen: 'mood' })\n"
        "  }\n"
        "  tap() {\n"
        "    this.logEvent('Mood Tapped', { Emotion: 'SECRET_VALUE_123', 'Message ID': 4 })\n"
        "    /*\n    this.logEvent('Commented Forwarder Event', { Notes: n })\n    */\n"
        "  }\n"
        "}\n"
        "Mixpanel.set({ $email: user.email, 'Last emotion': key })\n"
        "Mixpanel.identify('Ab3dEf6hIj9kLm2nOp5qRs8tUv1w')\n"
        "const ip = '10.20.30.40'\n"
        "Trackings(name, { Search: q })\n"
        "Trackings('Invited jane.doe@example.com', { Plan: p })\n"
    ),
    "src/setup.js": (
        "Mixpanel.init(token, { trackAutomaticEvents: false })\nFacebook.setAdvertiserTrackingEnabled(true)\n"
        "// Mixpanel.optOutTracking()\n/*\n  Mixpanel.init(t, { recordSessionsPercent: 100 })\n*/\n"
    ),
    "ios/Aura/Info.plist": (
        "<dict>\n<key>FacebookAutoLogAppEventsEnabled</key>\n<false/>\n"
        "<!-- <key>FirebaseAutomaticScreenReportingEnabled</key><true/> -->\n</dict>\n"
    ),
    "android/app/src/main/AndroidManifest.xml": (
        '<meta-data android:name="google_analytics_adid_collection_enabled"\n    android:value="false" />\n'
    ),
    "src/__tests__/Mood.test.js": "Trackings('Test Only Event', { Email: 'x' })\n",
}


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "GIT_AUTHOR_DATE": "2026-01-02T00:00:00Z",
                        "GIT_COMMITTER_DATE": "2026-01-02T00:00:00Z"})


def make_repo(base, rel, files, dirty=None):
    """A clone at base/rel whose origin is a bare repo named like the clone; optional uncommitted edits."""
    name = pathlib.Path(rel).name
    work = pathlib.Path(base) / "_src" / name
    work.mkdir(parents=True)
    git(work, "init", "-q", "-b", "main")
    for path, text in files.items():
        p = work / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    git(work, "add", "-A")
    git(work, "commit", "-qm", "init")
    bare = pathlib.Path(base) / "_origin" / f"{name}.git"
    bare.parent.mkdir(parents=True, exist_ok=True)
    git(base, "clone", "-q", "--bare", str(work), str(bare))
    clone = pathlib.Path(base) / rel
    clone.parent.mkdir(parents=True, exist_ok=True)
    git(base, "clone", "-q", str(bare), str(clone))
    for path, text in (dirty or {}).items():
        (clone / path).write_text(text)
    return clone


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.out = os.path.join(self.tmp, "out")

    def scan(self, *extra):
        argv = ["scan.py", "--out", self.out, "--base", self.tmp, "--no-fetch", *extra]
        with mock.patch.object(sys, "argv", argv), mock.patch("sys.stdout"), mock.patch("sys.stderr"):
            scan.main()
        with open(os.path.join(self.out, "scan.json")) as f:
            return json.load(f)

    def all_output(self):
        text = ""
        for name in os.listdir(self.out):
            with open(os.path.join(self.out, name), encoding="utf-8") as f:
                text += f.read()
        return text


class TestScan(Base):
    def setUp(self):
        super().setUp()
        # An uncommitted edit in the clone must not be read: the scan reads origin's default branch.
        make_repo(self.tmp, "Mobile/Aura", AURA_FILES, dirty={"src/setup.js": "Trackings('Local Only', {})\n"})
        self.data = self.scan()
        self.rows = self.data["rows"]

    def pairs(self, vendor="mixpanel"):
        return {(r["event"], r["property"]) for r in self.rows if r["vendor"] == vendor}

    def test_wrapper_fans_out_to_its_vendors_with_its_added_properties(self):
        self.assertIn(("Add Mood", "Notes"), self.pairs("mixpanel"))
        self.assertIn(("Add Mood", "Notes"), self.pairs("bugsnag"))
        self.assertIn(("Add Mood", "Day N"), self.pairs("iterable"))

    def test_local_forwarder_is_followed_to_its_call_sites(self):
        self.assertIn(("Mood Tapped", "Emotion"), self.pairs())
        self.assertIn(("Mood Tapped", "Screen"), self.pairs())

    def test_value_in_code_is_masked_and_counted(self):
        self.assertIn("Invited <masked:email>", {r["event"] for r in self.rows})
        self.assertGreaterEqual(self.data["masked"].get("email", 0), 1)

    def test_forwarder_call_in_a_comment_block_is_skipped(self):
        self.assertNotIn("Commented Forwarder Event", {r["event"] for r in self.rows})

    def test_dynamic_event_is_tagged_not_dropped(self):
        self.assertIn(("<dynamic>", "Search"), self.pairs())

    def test_reads_origin_not_the_working_tree_and_skips_tests(self):
        events = {r["event"] for r in self.rows}
        self.assertNotIn("Local Only", events)
        self.assertNotIn("Test Only Event", events)
        self.assertIn(("mixpanel", "automatic events"), {(s["vendor"], s["flag"]) for s in self.data["settings"]})

    def test_settings_read_plist_and_xml_values_and_skip_comments(self):
        got = {s["setting"]: s["value"] for s in self.data["settings"]}
        self.assertEqual(got["FacebookAutoLogAppEventsEnabled"], "false")
        self.assertEqual(got["google_analytics_adid_collection_enabled"], "false")
        self.assertEqual(got["trackAutomaticEvents"], "false")
        for commented in ("optOutTracking", "recordSessionsPercent", "FirebaseAutomaticScreenReportingEnabled"):
            self.assertNotIn(commented, got)

    def test_classes(self):
        cls = {(r["property"]): r["class"] for r in self.rows if r["property"]}
        self.assertEqual(cls["Notes"], "free_text")
        self.assertEqual(cls["$email"], "contact")
        self.assertEqual(cls["Emotion"], "health_adjacent")
        self.assertEqual(cls["Message ID"], "other")
        self.assertEqual(cls["<identifier>"], "identifier")
        self.assertEqual(cls["Level"], "other")

    def test_no_value_reaches_any_output_file(self):
        text = self.all_output()
        for value in VALUES:
            self.assertNotIn(value, text)

    def test_history_dates_sensitive_names(self):
        added = {r["property"]: r.get("added") for r in self.rows if r["sensitive"] and r["property"] == "Notes"}
        self.assertEqual(added["Notes"], "2026-01-02")

    def test_previous_run_lists_what_was_removed(self):
        prev = os.path.join(self.tmp, "prev.json")
        old = dict(self.data, rows=self.data["rows"] + [dict(self.rows[0], event="Old Event", property="Old Email",
                                                                sensitive=True)])
        with open(prev, "w") as f:
            json.dump(old, f)
        data = self.scan("--previous", prev)
        self.assertIn(("Old Event", "Old Email"), {(r["event"], r["property"]) for r in data["removed"]})


class TestBase(Base):
    def test_no_repos_dir_exits_naming_the_setting(self):
        env = {k: v for k, v in os.environ.items() if k != "AURA_REPOS_DIR"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(sys, "argv", ["scan.py", "--out", self.out]), self.assertRaises(SystemExit) as e:
            scan.main()
        self.assertIn("AURA_REPOS_DIR", str(e.exception.code))


class TestGuard(unittest.TestCase):
    def test_masks_and_counts(self):
        seen = {}
        self.assertEqual(guard.mask("a jane@x.io b", seen), "a <masked:email> b")
        self.assertEqual(seen, {"email": 1})

    def test_write_refuses_value_like_text(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(guard.GuardError):
                guard.write_text(os.path.join(d, "x.md"), "uid Ab3dEf6hIj9kLm2nOp5qRs8tUv1w")
            self.assertFalse(os.path.exists(os.path.join(d, "x.md")))

    def test_ordinary_names_pass(self):
        guard.check("AI Subscription Purchase Button Tapped | Religious Preference List | 2026-11-13 | 12.5%")


class TestReach(unittest.TestCase):
    def scan_doc(self):
        return {"rows": [{"vendor": "mixpanel", "kind": "event", "event": "A | B"},
                         {"vendor": "mixpanel", "kind": "event", "event": "<dynamic>"},
                         {"vendor": "bugsnag", "kind": "event", "event": "C"}]}

    def test_counts_per_event_and_lib(self):
        def post(script, params):
            if "selectors" in params:
                self.assertEqual(sorted(s["event"] for s in params["selectors"]), ["A", "B"])
                return [{"key": ["A", "iphone"], "value": 5}, {"key": ["B", "swift"], "value": 2}]
            return [{"key": ["iphone"], "value": 50}, {"key": ["swift"], "value": 20}]
        got = reach.measure(reach.event_names(self.scan_doc()), "2026-01-01", "2026-03-01", post)
        self.assertEqual(got["events"], {"A": {"iphone": 5}, "B": {"swift": 2}})
        self.assertEqual(got["totals"], {"iphone": 50, "swift": 20})

    def test_cached_window_missing_a_new_event_is_measured_again(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "scan.json"), "w") as f:
                json.dump(self.scan_doc(), f)
            with open(os.path.join(d, "reach.json"), "w") as f:
                json.dump({"90d": {"from": "x", "to": "y", "names": ["A"], "events": {"A": {"swift": 1}},
                                   "totals": {"swift": 1}}}, f)
            calls = []
            def fake(names, start, end, post=None):
                calls.append(names)
                return {"from": start, "to": end, "names": names, "events": {}, "totals": {}}
            with mock.patch.object(reach, "measure", fake), \
                    mock.patch.object(sys, "argv", ["reach.py", "--out", d, "--windows", "90d"]), mock.patch("sys.stdout"):
                reach.main()
            self.assertEqual(calls, [["A", "B"]])

    def test_window_ends_on_the_project_calendar(self):
        import datetime as dt
        utc_evening = dt.datetime(2026, 11, 14, 3, 0, tzinfo=dt.timezone.utc)  # 2026-11-13 19:00 Pacific
        class Fixed(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return utc_evening.astimezone(tz)
        with mock.patch.object(reach.datetime, "datetime", Fixed):
            self.assertEqual(reach.project_today(), dt.date(2026, 11, 13))

    def test_failed_window_is_unmeasured_not_zero(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "scan.json"), "w") as f:
                json.dump(self.scan_doc(), f)
            env = {k: v for k, v in os.environ.items() if not k.startswith("MIXPANEL_SERVICE_ACCOUNT")}
            with mock.patch.dict(os.environ, env, clear=True), \
                    mock.patch.object(sys, "argv", ["reach.py", "--out", d, "--windows", "90d"]), mock.patch("sys.stdout"):
                reach.main()
            with open(os.path.join(d, "reach.json")) as f:
                got = json.load(f)
            self.assertIn("unmeasured", got["90d"])
            self.assertNotIn("events", got["90d"])


class TestRender(Base):
    def test_table_settings_and_reach(self):
        make_repo(self.tmp, "Mobile/Aura", AURA_FILES)
        self.scan()
        guard.write_json(os.path.join(self.out, "reach.json"), {
            "90d": {"from": "a", "to": "b", "events": {"Add Mood": {"iphone": 10, "android": 5}},
                    "totals": {"iphone": 100, "android": 50}},
            "all": {"from": "a", "to": "b", "unmeasured": "JQL HTTP 500"}})
        with mock.patch.object(sys, "argv", ["render.py", "--out", self.out]), mock.patch("sys.stdout"):
            render.main()
        with open(os.path.join(self.out, "data-map.md")) as f:
            md = f.read()
        self.assertIn("| Aura | Add Mood | Notes | free text the user typed |", md)
        self.assertIn("| 15 | 10.0% | unmeasured |", md)
        self.assertIn("## SDK settings", md)
        self.assertEqual(render.reach_cell("New Event", "Aura", {"names": ["Add Mood"], "events": {}, "totals": {"iphone": 9}},
                                           {"iphone": "Aura"}), ("unmeasured", ""))
        self.assertIn("`trackAutomaticEvents` | false", md)
        for value in VALUES:
            self.assertNotIn(value, self.all_output())


class TestReferences(unittest.TestCase):
    def test_every_pattern_compiles_and_every_wrapper_names_known_vendors(self):
        import re
        sinks = json.loads((REFS / "sinks.json").read_text())
        for v in sinks["vendors"].values():
            for c in v["calls"]:
                re.compile(c["re"])
        for w in sinks["wrappers"]:
            re.compile(w["re"])
            self.assertTrue(set(w["vendors"]) <= set(sinks["vendors"]), w)
        classes = json.loads((REFS / "classes.json").read_text())
        self.assertEqual(set(classes["order"]), set(classes["classes"]))


if __name__ == "__main__":
    unittest.main()
