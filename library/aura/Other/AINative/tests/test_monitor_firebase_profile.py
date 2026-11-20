"""`eng:monitor-firebase` — which RTDB profile findings page someone.

A signed-out app listener on an `auth != null` node is denied a handful of times per window, so a
flat "5 denies" bar turned a known pattern into an alert. Non-`/users` watched nodes now alert at
max(20 per 10 min, 0.5% of allowed); `/users` keeps its own bar. An allowed whole read of a node that
is public by rule is the rule working, so it is not listed as a probe; a denied one still is.

The profile text is the shape `eng:profile-firebase-rtdb` prints (watched-nodes.js, root-attempts.js).
The public-read case is also fed through the real root-attempts.js when node is installed.
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
SCRIPTS = REPO / "plugins/eng/skills/monitor-firebase/scripts"
ROOT_ATTEMPTS = REPO / "plugins/eng/skills/profile-firebase-rtdb/scripts/root-attempts.js"
WATCHED = REPO / "plugins/eng/skills/profile-firebase-rtdb/scripts/watched-nodes.js"


def profile(watched: str = "", root: str = "  none (no denied, root, guessed-name or REST whole-node reads)") -> str:
    return f"""Captured 12000 operations.
Profile 2026-10-26T20:00:00Z -> 2026-10-26T20:10:00Z (10m)

Permission denied: 45 of 11000 rule-checked operations

Root / whole top-level node read attempts (allowed Admin SDK excluded), counts only:
{root}
  (+ 40 allowed app-SDK listens on 6 whole catalog/config nodes, expected)

Signed-out writes to users/{{id}}:
  none

Watched nodes, rule-checked ops:
{watched}
Denied on watched nodes:
"""


def findings(text: str, minutes: float = 10) -> list[dict]:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(text)
    out = subprocess.run([sys.executable, str(SCRIPTS / "evaluate_profile.py"), "--profile", f.name,
                          "--minutes", str(minutes)], capture_output=True, text=True, check=True)
    return [json.loads(line) for line in out.stdout.splitlines() if line.strip()]


def level(fs: list[dict], key: str) -> str | None:
    return next((f["level"] for f in fs if f["key"] == key), None)


class WatchedDenies(unittest.TestCase):
    def test_signed_out_listener_pattern_is_a_note(self):
        fs = findings(profile("  userSubscriptions: 5736 allowed, 5 denied"))
        self.assertEqual(level(fs, "profile-watched-deny-userSubscriptions"), "note")

    def test_a_real_break_is_an_alert(self):
        fs = findings(profile("  userSubscriptions: 5000 allowed, 40 denied"))
        self.assertEqual(level(fs, "profile-watched-deny-userSubscriptions"), "alert")

    def test_share_floor_lifts_the_bar_on_a_busy_node(self):
        fs = findings(profile("  userSubscriptions: 10000 allowed, 40 denied"))  # bar = 50
        self.assertEqual(level(fs, "profile-watched-deny-userSubscriptions"), "note")

    def test_users_keeps_its_own_bar(self):
        self.assertEqual(level(findings(profile("  users: 9000 allowed, 100 denied")), "profile-watched-deny-users"), "alert")
        self.assertEqual(level(findings(profile("  users: 9000 allowed, 60 denied")), "profile-watched-deny-users"), "note")


    # 2026-10-28 23:12Z: one signed-out device retrying in a loop made a 340-deny /users alert.
    def test_one_looping_client_is_a_note(self):
        fs = findings(profile("  users: 17 allowed, 340 denied (from 1 ids, 1 IPs)"))
        self.assertEqual(level(fs, "profile-watched-deny-users"), "note")
        text = next(f["text"] for f in fs if f["key"] == "profile-watched-deny-users")
        self.assertIn("from 1 ids and 1 IPs (one client retrying, not a breakage)", text)

    def test_many_sources_still_alert(self):
        for line in ("  users: 9000 allowed, 340 denied (from 40 ids, 35 IPs)",
                     "  users: 9000 allowed, 340 denied (from 3 ids, 1 IPs)",   # one address, many ids
                     "  users: 9000 allowed, 340 denied (from 1 ids, 3 IPs)"):
            self.assertEqual(level(findings(profile(line)), "profile-watched-deny-users"), "alert", line)

    def test_a_looping_client_below_the_bar_stays_a_plain_note(self):
        fs = findings(profile("  users: 9000 allowed, 60 denied (from 1 ids, 1 IPs)"))
        text = next(f["text"] for f in fs if f["key"] == "profile-watched-deny-users")
        self.assertNotIn("retrying", text)

    def test_the_counts_come_from_the_profiler(self):
        """watched-nodes.js's real output, fed to evaluate_profile.py."""
        if not shutil.which("node"):
            self.skipTest("node not installed")
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        dev = {"userAgent": {"platform": "native", "os": "ios", "userAgentString": "Aura/3.2"},
               "remoteAddress": {"address": "203.0.113.5"}}
        op = lambda path, allowed, client: "data " + json.dumps(
            {"name": "listener-listen", "path": path, "allowed": allowed, "client": client})
        lines = [op(["users", "uidA", "profile"], False, dev) for _ in range(150)]
        lines.append(op(["users", "uidB"], True, dev))
        (tmp / "a.log").write_text("\n".join(lines) + "\n")
        out = subprocess.run(["node", str(WATCHED), str(tmp), "users"], capture_output=True, text=True, check=True).stdout
        self.assertIn("users: 1 allowed, 150 denied (from 1 ids, 1 IPs)", out)
        self.assertNotIn("203.0.113.5", out)
        self.assertNotIn("uidA", out)
        fs = findings(profile(out.split("Watched nodes, rule-checked ops:\n")[1].split("Denied on")[0].rstrip("\n")))
        self.assertEqual(level(fs, "profile-watched-deny-users"), "note")

        many = [op(["users", f"uid{i}"], False, {**dev, "remoteAddress": {"address": f"198.51.100.{i}"}})
                for i in range(150)]
        (tmp / "a.log").write_text("\n".join(many) + "\n")
        out = subprocess.run(["node", str(WATCHED), str(tmp), "users"], capture_output=True, text=True, check=True).stdout
        self.assertIn("(from 150 ids, 150 IPs)", out)
        fs = findings(profile(out.split("Watched nodes, rule-checked ops:\n")[1].split("Denied on")[0].rstrip("\n")))
        self.assertEqual(level(fs, "profile-watched-deny-users"), "alert")


ROW = "  3\t{verdict} rest-read /{node} [rule exists] (web/mac Mozilla/5.0): 2 IPs, 20:01-20:04 UTC"


class PublicNodes(unittest.TestCase):
    def probes(self, root: str) -> list[str]:
        return [f["text"] for f in findings(profile(root=root)) if f["key"] == "profile-probe"]

    def test_allowed_public_read_is_not_listed(self):
        self.assertEqual(self.probes(ROW.format(verdict="allowed", node="coaches")), [])

    def test_denied_public_read_still_listed(self):
        self.assertEqual(len(self.probes(ROW.format(verdict="DENIED ", node="coaches"))), 1)

    def test_allowed_private_read_still_listed(self):
        self.assertEqual(len(self.probes(ROW.format(verdict="allowed", node="userSubscriptions"))), 1)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_real_producer_line_for_a_public_read(self):
        with tempfile.TemporaryDirectory() as d:
            prof = pathlib.Path(d, "profiler")
            prof.mkdir()
            op = {"name": "rest-read", "path": ["coaches"], "allowed": True, "timestamp": 1793044860000,
                  "client": {"userAgent": {"platform": "web", "os": "mac", "userAgentString": "Mozilla/5.0"},
                             "remoteAddress": {"address": "203.0.113.9"}}}
            (prof / "ops.log").write_text("x " + json.dumps(op) + "\n")
            rules = pathlib.Path(d, "rules.json")
            rules.write_text(json.dumps({"rules": {"coaches": {".read": True}}}))
            out = subprocess.run(["node", str(ROOT_ATTEMPTS), str(prof), str(rules)],
                                 capture_output=True, text=True, check=True).stdout
        # An allowed read of a public node is a "Public node read": not in the attempt summary, so no finding.
        self.assertEqual([l for l in out.splitlines() if "/coaches" in l], [], out)
        self.assertEqual(self.probes(out), [])


if __name__ == "__main__":
    unittest.main()
