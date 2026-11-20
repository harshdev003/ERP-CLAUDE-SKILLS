"""`eng:profile-firebase-rtdb` rest-reads.js and `eng:monitor-firebase`'s REST-read finding.

On 2026-10-31 at 23:19Z a burst of about 1,145 REST gets and 309 shallow gets hit prod RTDB. They were
allowed exact-path reads below the top level, which no profile section recorded, so the caller could not
be attributed afterwards (ENG-1720). rest-reads.js now groups every allowed REST read by op, path shape
and client, counts only, and evaluate_profile.py turns it into a note, or an alert above 300 per 10 min.

The evaluator is fed rest-reads.js's real output, never a hand-typed section.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
PROFILE = REPO / "plugins/eng/skills/profile-firebase-rtdb/scripts"
EVALUATE = REPO / "plugins/eng/skills/monitor-firebase/scripts/evaluate_profile.py"
T0 = 1793488740000  # 2026-10-31T23:19:00Z

SCRIPT = {"userAgent": {"platform": "admin", "os": "linux", "userAgentString": "python-requests/2.31.0"},
          "remoteAddress": {"address": "198.51.100.7"}}
BROWSER = {"userAgent": {"platform": "web", "os": "mac", "userAgentString": "Mozilla/5.0 (Macintosh)"},
           "remoteAddress": {"address": "198.51.100.8"}}
APP = {"userAgent": {"platform": "native", "os": "ios", "userAgentString": "Aura/3.2"},
       "remoteAddress": {"address": "198.51.100.9"}}


def op(name, path, client, allowed=True, i=0, **extra):
    d = {"name": name, "allowed": allowed, "timestamp": T0 + i * 100, "client": client, **extra}
    if path is not None:
        d["path"] = path
    return "data " + json.dumps(d)


def rest_reads(lines: list[str]) -> str:
    with tempfile.TemporaryDirectory() as d:
        pathlib.Path(d, "a.log").write_text("\n".join(lines) + "\n")
        return subprocess.run(["node", str(PROFILE / "rest-reads.js"), d],
                              capture_output=True, text=True, check=True).stdout


def findings(section: str, minutes: float = 10) -> list[dict]:
    text = ("Captured 12000 operations.\nProfile 2026-10-31T23:15:00Z -> 2026-10-31T23:25:00Z (10m)\n\n"
            "Permission denied: 4 of 11000 rule-checked operations\n\n" + section + "\n")
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(text)
    out = subprocess.run([sys.executable, str(EVALUATE), "--profile", f.name, "--minutes", str(minutes)],
                         capture_output=True, text=True, check=True).stdout
    return [json.loads(l) for l in out.splitlines() if l.strip()]


def burst() -> list[str]:
    lines = [op("rest-read", ["users", f"uid{i:05d}", "profile"], SCRIPT, i=i) for i in range(1145)]
    lines += [op("rest-read", ["userListened", f"uid{i:05d}"], SCRIPT, i=i, querySet=[{"shallow": True}])
              for i in range(309)]
    return lines


@unittest.skipUnless(shutil.which("node"), "node not installed")
class RestReads(unittest.TestCase):
    def test_burst_is_grouped_by_shape_and_client(self):
        out = rest_reads(burst())
        self.assertIn("total 1454 (309 shallow) from 1 IPs", out)
        self.assertIn("1145\trest-read /users/$id/profile (admin/linux python-requests/2.31.0): 1145 ids, 1 IPs, 23:19-23:20 UTC", out)
        self.assertIn("309\trest-read (shallow) /userListened/$id (admin/linux python-requests/2.31.0): 309 ids, 1 IPs", out)

    def test_counts_only(self):
        out = rest_reads(burst())
        self.assertNotIn("198.51.100", out)
        self.assertNotIn("uid0", out)

    def test_denied_sdk_and_writes_are_not_counted(self):
        out = rest_reads([
            op("rest-read", ["users", "uidA"], BROWSER, allowed=False),
            op("listener-listen", ["users", "uidA"], APP),
            op("realtime-read", ["users", "uidA"], APP),
            op("rest-write", ["users", "uidA"], SCRIPT),
            op("rest-read", ["coaches", "c1"], BROWSER),
        ])
        self.assertIn("total 1 (0 shallow) from 1 IPs", out)
        self.assertIn("rest-read /coaches/$id (web/mac browser)", out)

    def test_root_and_top_level_reads_count_too(self):
        out = rest_reads([op("rest-read", None, BROWSER), op("rest-read", ["appConfig"], BROWSER)])
        self.assertIn("total 2 (0 shallow)", out)
        self.assertIn("rest-read / (web/mac browser)", out)
        self.assertIn("rest-read /appConfig (web/mac browser)", out)

    def test_a_shallow_op_name_is_counted_as_shallow(self):
        out = rest_reads([op("rest-shallow-read", ["users"], SCRIPT)])
        self.assertIn("total 1 (1 shallow)", out)
        self.assertIn("rest-shallow-read /users ", out)

    def test_our_own_sessions_are_labelled_internal(self):
        ours = {"userAgent": {"platform": "admin", "os": "darwin",
                              "userAgentString": "AuraClaude/eng:monitor-firebase@1.11.0 (session ab12cd34)"}}
        self.assertIn("(admin/darwin internal (eng:monitor-firebase))", rest_reads([op("rest-read", ["a", "b"], ours)]))

    def test_none(self):
        out = rest_reads([op("listener-listen", ["users", "uidA"], APP)])
        self.assertIn("Allowed REST reads by path shape, counts only:\n  none", out)
        self.assertEqual([f for f in findings(out) if "rest" in f["key"]], [])

    def test_more_than_ten_shapes_are_summed(self):
        out = rest_reads([op("rest-read", [f"node{i}"], SCRIPT) for i in range(12)])
        self.assertIn("(+ 2 more shapes, 2 ops)", out)

    def test_the_burst_alerts_and_names_the_caller(self):
        fs = findings(rest_reads(burst()))
        f = next(f for f in fs if f["key"] == "profile-rest-burst")
        self.assertEqual(f["level"], "alert")
        self.assertIn("1454 (309 marked shallow) from 1 IPs", f["text"])
        self.assertIn("/users/$id/profile (admin/linux python-requests/2.31.0)", f["text"])
        self.assertNotIn("profile-rest-reads", [x["key"] for x in fs])

    def test_normal_volume_is_a_note(self):
        fs = findings(rest_reads([op("rest-read", ["coaches", f"c{i}"], BROWSER, i=i) for i in range(40)]))
        self.assertEqual([(f["key"], f["level"]) for f in fs if "rest" in f["key"]], [("profile-rest-reads", "note")])

    def test_no_marker_never_reads_as_zero_shallow(self):
        # 2026-11-09 13:00Z: ~100 app GETs sent ?shallow=true and the raw ops carried no marker.
        f = next(f for f in findings(rest_reads([op("rest-read", ["channels", f"ch{i}"], APP, i=i) for i in range(100)]))
                 if f["key"] == "profile-rest-reads")
        self.assertNotIn("shallow", f["text"])

    def test_threshold_scales_with_the_window(self):
        out = rest_reads([op("rest-read", ["coaches", f"c{i}"], BROWSER, i=i) for i in range(200)])
        self.assertEqual(next(f["level"] for f in findings(out, 5) if "rest" in f["key"]), "alert")
        self.assertEqual(next(f["level"] for f in findings(out, 10) if "rest" in f["key"]), "note")


class Wiring(unittest.TestCase):
    def test_run_profile_and_peek_print_the_section(self):
        for script in ("run-profile.sh", "peek.sh"):
            self.assertIn('node "$DIR/rest-reads.js" "$RUN/profiler"', (PROFILE / script).read_text(), script)


if __name__ == "__main__":
    unittest.main()
