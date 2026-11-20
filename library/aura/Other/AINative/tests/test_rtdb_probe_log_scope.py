"""What reaches the RTDB Probe Log (ENGBACK-2627): every read of the root or a whole top-level node,
routine ones included and marked as such, and every DENIED operation at any depth; plus allowed REST
reads below a node from outside the apps. Not: the apps' allowed reads below a node, allowed writes, or
allowed Admin SDK traffic. A path below a node is templated, so no key (a uid, an email) is ever written,
and no IP. The profiler output is fed through the real scripts and the real planner, which keeps one
row per path + op + verdict.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "plugins/eng/skills/profile-firebase-rtdb/scripts"
PLANNER = ROOT / "plugins/eng/skills/monitor-rtdb-probes/scripts/plan-probe-log.js"
NODE = shutil.which("node")
TS = 1793120340000  # 2026-10-27T16:59:00Z
UID = "Za0DIgYucXRZduFOOlHKvjPb01"
ALPHA_UID = "ZaDIgYucXRZduFOOlHKvjPbQwErT"  # 28 letters, no digit
ALPHA_UID_LOWER = "zaDIgyuCXrZduFOOlHKvjPbQwErT"  # 28 letters, lowercase first
EMAIL_KEY = "person@example"
LONG_FIELD = "fpMembersListenedShareDisabled"
MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"


def op(path, name, ip, platform, ua, ts, allowed=True):
    d = {"name": name, "allowed": allowed, "timestamp": ts, "bytes": 0, "path": path,
         "client": {"remoteAddress": {"address": ip},
                    "userAgent": {"platform": platform, "os": "os_x", "userAgentString": ua}}}
    return "x " + json.dumps(d)


LOG = [
    op(["catalog"], "listener-listen", "203.0.113.21", "native", "Aura/3.2", TS),             # routine app read
    op(["userSubscriptions"], "listener-listen", "198.51.100.9", "desktop", MAC, TS + 1000),  # console open
    op(["clients"], "rest-read", "198.51.100.7", "desktop", "curl/8.0", TS + 2000, allowed=False),  # guessed
    op(["users", UID, "email"], "realtime-read", "198.51.100.8", "web", MAC, TS + 3000, allowed=False),
    op(["users", UID], "realtime-update", "198.51.100.8", "web", MAC, TS + 4000, allowed=False),
    op(["users", UID, "email"], "realtime-read", "203.0.113.30", "native", "Aura/3.2", TS + 5000),  # app: out
    op(["users", UID], "realtime-update", "203.0.113.30", "native", "Aura/3.2", TS + 6000),          # write: out
    op(["userListened", UID], "rest-read", "198.51.100.10", "desktop", "curl/8.0", TS + 7000),        # REST deep
    op(["userListened", UID], "rest-read", "10.0.0.1", "unknown", "AdminNode/12.0", TS + 8000),       # admin: out
    op(["coaches"], "rest-read", "203.0.113.20", "web", "Mozilla/5.0", TS + 9000),                    # whole node
    op(["catalog"], "listener-listen", "198.51.100.11", "desktop", MAC, TS + 10000),          # outside, same node
    op(["users"], "realtime-read", "198.51.100.12", "desktop", MAC, TS + 11000),              # one-shot SDK get
    op(["coachClients", UID, ALPHA_UID], "realtime-read", "198.51.100.13", "web", MAC, TS + 12000, allowed=False),
    op(["coachClients", UID, ALPHA_UID], "rest-read", "198.51.100.14", "desktop", "curl/8.0", TS + 13000),
    op(["emailIndex", UID, EMAIL_KEY], "realtime-read", "198.51.100.15", "web", MAC, TS + 14000, allowed=False),
    op(["coachClients", UID, ALPHA_UID_LOWER], "realtime-read", "198.51.100.16", "web", MAC, TS + 15000, allowed=False),
    op(["users", UID, LONG_FIELD], "realtime-read", "198.51.100.17", "web", MAC, TS + 16000, allowed=False),
]


@unittest.skipUnless(NODE, "node not installed")
class ProbeLogScope(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        prof = self.tmp / "profiler"
        prof.mkdir()
        (prof / "firebase.log").write_text("\n".join(LOG) + "\n")
        rules = self.tmp / "rules.json"
        rules.write_text(json.dumps({"rules": {"catalog": {}, "userSubscriptions": {}, "users": {},
                                               "userListened": {}, "coaches": {".read": True}}}))
        self.probes = self.tmp / "probes.jsonl"
        for cmd in ([str(SCRIPTS / "root-attempts.js"), str(prof), str(rules), str(self.probes)],
                    [str(SCRIPTS / "rest-reads.js"), str(prof), "--probe-json", str(self.probes)]):
            r = subprocess.run([NODE, *cmd], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
        self.lines = [json.loads(l) for l in self.probes.read_text().splitlines()]

    def keys(self):
        return {(l["path"], l["op"], l["verdict"], l["kind"]) for l in self.lines}

    def test_every_top_level_read_and_every_denied_operation(self):
        self.assertEqual(self.keys(), {
            ("/catalog", "listener-listen", "Allowed", "Routine app read"),
            ("/catalog", "listener-listen", "Allowed", "Whole real node via SDK listen"),
            ("/users", "realtime-read", "Allowed", "Whole real node via SDK read"),
            ("/coachClients/$id/$id", "realtime-read", "Denied", "Denied read below a node"),
            ("/coachClients/$id/$id", "rest-read", "Allowed", "REST read below a node"),
            ("/emailIndex/$id/$id", "realtime-read", "Denied", "Denied read below a node"),
            # Deliberate: a field name of 20+ characters is templated too. A letters-only uid can't be told
            # from one, and the Probe Log is shared, so no key may reach it (the local text summaries differ).
            ("/users/$id/$id", "realtime-read", "Denied", "Denied read below a node"),
            ("/userSubscriptions", "listener-listen", "Allowed", "Whole real node via SDK listen"),
            ("/clients", "rest-read", "Denied", "Guessed name (scanner)"),
            ("/users/$id/email", "realtime-read", "Denied", "Denied read below a node"),
            ("/users/$id", "realtime-update", "Denied", "Denied write"),
            ("/userListened/$id", "rest-read", "Allowed", "REST read below a node"),
            ("/coaches", "rest-read", "Allowed", "Public node read"),
        })

    def test_no_key_and_no_address_is_written(self):
        text = self.probes.read_text()
        for key in (UID, ALPHA_UID, ALPHA_UID_LOWER, EMAIL_KEY, "@"):
            self.assertNotIn(key, text)
        for ip in ("198.51.100", "203.0.113", "10.0.0.1"):
            self.assertNotIn(ip, text)
        self.assertTrue(all(isinstance(l["ips"], int) for l in self.lines))

    def test_the_planner_keeps_one_row_per_path_op_verdict(self):
        rows = self.tmp / "rows.json"
        rows.write_text(json.dumps({"results": []}))
        r = subprocess.run([NODE, str(PLANNER), str(self.probes), str(rows)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        plan = json.loads(r.stdout)
        # The routine /catalog row and the outside /catalog open are separate rows.
        self.assertEqual(len(plan["creates"]), 13)
        catalog = [c["properties"]["Kind"] for c in plan["creates"] if c["properties"]["Path"] == "/catalog"]
        self.assertEqual(sorted(catalog), ["Routine app read", "Whole real node via SDK listen"])
        # Second pass, later attempts: every key is an update of its row, never a new row.
        existing = [{"url": f"https://app.notion.com/{i:032x}", "Path": c["properties"]["Path"],
                     "Verdict": c["properties"]["Verdict"], "Op": c["properties"]["Op"],
                     "Kind": c["properties"]["Kind"], "Client": c["properties"]["Client"],
                     "date:First seen:start": "2026-10-27 16:59:00Z", "date:Last seen:start": "2026-10-27 16:59:00Z",
                     "Attempts seen": 1, "Days seen": 1, "Max IPs in a window": 1}
                    for i, c in enumerate(plan["creates"], 1)]
        rows.write_text(json.dumps({"results": existing}))
        later = self.tmp / "later.jsonl"
        later.write_text("".join(json.dumps({**l, "t0": "2026-10-28T10:00:00.000Z", "t1": "2026-10-28T10:00:00.000Z"}) + "\n"
                                 for l in self.lines))
        r = subprocess.run([NODE, str(PLANNER), str(later), str(rows)], capture_output=True, text=True)
        plan = json.loads(r.stdout)
        self.assertEqual((len(plan["creates"]), len(plan["updates"])), (0, 13))


if __name__ == "__main__":
    unittest.main()
