"""`eng:profile-firebase-rtdb` realtime-reads.js and `eng:monitor-firebase`'s realtime-read note (ENG-1765).

On 2026-11-03 the "RTDB realtime read surge" alert fired (14:33-14:52Z, realtime_get about 43k per 10 min)
and nothing could attribute it afterwards: each pass deletes the raw profile stream, and no summary broke
allowed realtime reads down by path. realtime-reads.js now groups every allowed realtime get, query and
listen by op, path shape, auth (admin or client) and client, counts only, and evaluate_profile.py prints it
as a note on every pass. The evaluator is fed realtime-reads.js's real output.
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
T0 = 1793717580000  # 2026-11-03T14:53:00Z

APP = {"userAgent": {"platform": "native", "os": "ios", "userAgentString": "Aura/3.2"},
       "remoteAddress": {"address": "198.51.100.9"}}
# The Admin SDK as the profiler sees it: a desktop platform, an AdminNode user agent.
ADMIN = {"userAgent": {"platform": "desktop", "os": "linux", "userAgentString": "Firebase/5/12.6.0 AdminNode/v20.11.0"},
         "remoteAddress": {"address": "198.51.100.10"}}


def op(name, path, client, allowed=True, i=0, **extra):
    d = {"name": name, "allowed": allowed, "timestamp": T0 + i * 100, "client": client, **extra}
    if path is not None:
        d["path"] = path
    return "data " + json.dumps(d)


def realtime_reads(lines: list[str]) -> str:
    with tempfile.TemporaryDirectory() as d:
        pathlib.Path(d, "a.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return subprocess.run(["node", str(PROFILE / "realtime-reads.js"), d],
                              capture_output=True, text=True, encoding="utf-8", check=True).stdout


def findings(section: str) -> list[dict]:
    text = ("Captured 50000 operations.\nProfile 2026-11-03T14:43:00Z -> 2026-11-03T14:53:00Z (10m)\n\n"
            "Permission denied: 0 of 40000 rule-checked operations\n\n" + section + "\n")
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(text)
    out = subprocess.run([sys.executable, str(EVALUATE), "--profile", f.name, "--minutes", "10"],
                         capture_output=True, text=True, encoding="utf-8", check=True).stdout
    pathlib.Path(f.name).unlink()
    return [json.loads(l) for l in out.splitlines() if l.strip()]


def surge() -> list[str]:
    lines = [op("listener-listen", ["userListened", f"uid{i:05d}"], APP, i=i, bytes=200) for i in range(900)]
    lines += [op("realtime-read", ["meditations", f"m{i % 40}", "audio"], APP, i=i, bytes=50) for i in range(300)]
    lines += [op("realtime-query", ["users", f"uid{i:05d}"], ADMIN, i=i, bytes=1000) for i in range(25)]
    return lines


@unittest.skipUnless(shutil.which("node"), "node not installed")
class RealtimeReads(unittest.TestCase):
    def test_a_surge_is_grouped_by_shape_auth_and_client(self):
        out = realtime_reads(surge())
        self.assertIn("total 1225 ops (25 admin), 220000 bytes, from 2 IPs", out)
        self.assertIn("900\tlistener-listen /userListened/$id client (native/ios Aura/3.2): 180000 bytes, 900 ids, 1 IPs", out)
        self.assertIn("300\trealtime-read /meditations/$id/audio client (native/ios Aura/3.2): 15000 bytes, 40 ids", out)
        self.assertIn("25\trealtime-query /users/$id admin (desktop/linux Firebase/5/12.6.0)", out)

    def test_admin_is_read_from_the_user_agent_like_root_attempts(self):
        js = (PROFILE / "root-attempts.js").read_text(encoding="utf-8")
        self.assertIn("AdminNode|firebase-admin|Node\\.js|node-fetch|axios", js)
        self.assertIn("AdminNode|firebase-admin|Node\\.js|node-fetch|axios", (PROFILE / "realtime-reads.js").read_text(encoding="utf-8"))
        browser = {"userAgent": {"platform": "desktop", "os": "mac", "userAgentString": "Mozilla/5.0 (Macintosh)"}}
        out = realtime_reads([op("realtime-read", ["coaches", "c1"], browser)])
        self.assertIn("total 1 ops (0 admin)", out)

    def test_counts_only(self):
        out = realtime_reads(surge())
        self.assertNotIn("198.51.100", out)
        self.assertNotIn("uid0", out)

    def test_denied_writes_and_rest_ops_are_not_counted(self):
        out = realtime_reads([
            op("listener-listen", ["users", "uidA"], APP, allowed=False),
            op("realtime-write", ["users", "uidA"], APP),
            op("rest-read", ["users", "uidA"], APP),
            op("realtime-read", ["coaches", "c1"], APP),
        ])
        self.assertIn("total 1 ops (0 admin)", out)

    def test_none(self):
        out = realtime_reads([op("rest-read", ["a"], APP)])
        self.assertIn("Allowed realtime reads by path shape, counts only:\n  none", out)
        self.assertEqual([f for f in findings(out) if f["key"] == "profile-realtime-reads"], [])

    def test_every_pass_gets_a_counts_only_note(self):
        fs = findings(realtime_reads(surge()))
        (f,) = [f for f in fs if f["key"] == "profile-realtime-reads"]
        self.assertEqual(f["level"], "note")
        self.assertIn("allowed realtime reads: 1225 ops (25 admin), 220000 bytes from 2 IPs 14:43-14:53Z", f["text"])
        self.assertIn("/userListened/$id client", f["text"])
        self.assertNotIn("198.51.100", f["text"])


class Wiring(unittest.TestCase):
    def test_run_profile_and_peek_print_the_section(self):
        for script in ("run-profile.sh", "peek.sh"):
            self.assertIn('node "$DIR/realtime-reads.js" "$RUN/profiler"', (PROFILE / script).read_text(encoding="utf-8"), script)


if __name__ == "__main__":
    unittest.main()
