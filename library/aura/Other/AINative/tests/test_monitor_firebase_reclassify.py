"""monitor-firebase re-classifies Incident Log lines the network check could not judge (ENG-1756).

When Known Team Networks can't be read, allowed reads are logged with their addresses and each line ends
"(unclassified: <reason>)" (ENG-1755). On a later pass where Notion answers, reclassify.py matches those
addresses to Known Team Networks, and the planner rewrites each tag as "(classified <date>: <verdict>)".
Lines logged before the tag existed (2026-11-03 from 02:33Z) are taken with --untagged-since.

Known Team Networks and whois are stubbed; the planner runs under node. Documentation addresses and ASNs
only.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
MONITOR = ROOT / "plugins/eng/skills/monitor-firebase/scripts"
PLANNER = ROOT / "plugins/eng/skills/monitor-rtdb-probes/scripts/plan-incident-log.js"
NODE = shutil.which("node")
sys.path.insert(0, str(MONITOR))
import common  # noqa: E402
import reclassify  # noqa: E402

TEAM, STRANGER, BARRED = "198.51.100.40", "203.0.113.9", "192.0.2.33"
NETS = {TEAM: ("64500", "PK", "EXAMPLE-ISP", True), STRANGER: ("64501", "US", "EXAMPLE-CLOUD", True),
        BARRED: ("64502", "US", "EXAMPLE-VPN", True)}
KNOWN = [{"asn": "64500", "cc": "PK", "status": "Confirmed", "who": "rose@example.com"},
         {"asn": "64502", "cc": "US", "status": "Not a teammate", "who": ""}]
NODE_GROUP = "Firebase RTDB|node-read|no-data"


def rt(s):
    return {"rich_text": [{"plain_text": s}]}


def row(page_id, what, group=NODE_GROUP, detected="2026-11-03T02:33:00.000Z"):
    return {"id": page_id, "properties": {
        "System": {"select": {"name": "Firebase RTDB"}}, "Group key": rt(group), "Severity": {"select": {"name": "Low"}},
        "Detected (UTC)": {"date": {"start": detected}}, "Ended (UTC)": {"date": {"start": detected}},
        "What they did": rt(what), "IP addresses": rt(""), "User agent": rt(""), "Route / path": rt(""),
        "Source network": rt(""), "Requests": {"number": 1}, "Found by": rt("eng:monitor-rtdb-probes")}}


def line(hhmm, ip, tag=" (unclassified: Notion rate-limited)"):
    return f"{hhmm}Z {ip}: 1 allowed realtime read of /coaches [Mozilla/5.0]{tag}"


ROWS = [
    row("p-team", line("02:33", TEAM)),
    row("p-old", "\n".join([line("02:20", STRANGER, ""), line("02:41", STRANGER, ""),
                            line("02:50", BARRED, " (classified 2026-11-03: x)")])),
    row("p-guess", line("02:45", STRANGER, ""), group="Firebase RTDB|guessed-name|denied"),
]


def run(rows, *args, known=lambda: (KNOWN, set())):
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        (d / "rows.json").write_text(json.dumps({"results": rows}), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = reclassify.main(["--rows", str(d / "rows.json"), "--out", str(d / "re.json"), *args],
                                   lookup=lambda ip: NETS.get(ip, ("?", "no asn", "?", False)), known=known)
        mode = stat.S_IMODE(os.stat(d / "re.json").st_mode)
        return code, json.loads((d / "re.json").read_text(encoding="utf-8")), out.getvalue(), mode


class Reclassify(unittest.TestCase):
    def test_tagged_lines_are_judged_against_known_team_networks(self):
        code, re_, out, mode = run(ROWS)
        self.assertEqual((code, mode), (0, 0o600))
        self.assertEqual([(x["page_id"], x["ip"]) for x in re_["lines"]], [("p-team", TEAM)])
        self.assertEqual(re_["ips"][TEAM], {"verdict": "teammate", "network": "AS64500 EXAMPLE-ISP, PK",
                                            "who": "Confirmed, rose@", "name": "rose@"})
        self.assertIn("re-classified 1 of 1", out)
        self.assertNotIn(TEAM, out)

    def test_untagged_whole_node_lines_since_a_time_are_taken_too(self):
        _, re_, out, _ = run(ROWS, "--untagged-since", "2026-11-03T02:30Z")
        picked = sorted((x["page_id"], x["line"][:5]) for x in re_["lines"])
        self.assertEqual(picked, [("p-old", "02:41"), ("p-team", "02:33")])  # not 02:20, not denied, not classified
        self.assertEqual(re_["ips"][STRANGER]["verdict"], "unknown")

    def test_a_not_a_teammate_network_alerts(self):
        r = [row("p-b", line("02:50", BARRED))]
        _, re_, out, _ = run(r)
        self.assertEqual(re_["ips"][BARRED]["verdict"], "not-a-teammate")
        self.assertEqual(json.loads(out.splitlines()[0])["level"], "alert")

    def test_still_unreadable_changes_nothing(self):
        def down():
            raise common.RateLimited("HTTP 429 rate-limited after 7 tries")
        _, re_, out, _ = run(ROWS, known=down)
        self.assertEqual(re_["lines"], [])
        finding = json.loads(out.splitlines()[0])
        self.assertEqual(finding["level"], "fail")
        self.assertIn("Notion rate-limited", finding["text"])

    def test_a_failed_whois_leaves_the_line_for_the_next_pass(self):
        r = [row("p-x", line("02:33", "192.0.2.200"))]
        _, re_, _, _ = run(r)
        self.assertEqual((re_["lines"], re_["ips"]), ([], {}))


@unittest.skipUnless(NODE, "node not installed")
class Planner(unittest.TestCase):
    def plan(self, rows, reclass, detail=()):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            (d / "d.jsonl").write_text("".join(json.dumps(x) + "\n" for x in detail), encoding="utf-8")
            (d / "r.json").write_text(json.dumps({"results": rows}), encoding="utf-8")
            (d / "re.json").write_text(json.dumps(reclass), encoding="utf-8")
            r = subprocess.run([NODE, str(PLANNER), str(d / "d.jsonl"), str(d / "r.json"), "--reclassify", str(d / "re.json")],
                               capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_tags_are_rewritten_and_untagged_lines_gain_one(self):
        _, re_, _, _ = run(ROWS, "--untagged-since", "2026-11-03T02:30Z")
        p = self.plan(ROWS, re_)
        what = {u["page_id"]: u["properties"]["What they did"]["rich_text"][0]["text"]["content"] for u in p["updates"]}
        team = what["p-team"]
        self.assertTrue(team.endswith(" (classified " + team.split("(classified ")[1]), team)
        self.assertIn("teammate network AS64500 EXAMPLE-ISP, PK, Known Team Networks Confirmed, rose@)", team)
        self.assertNotIn("unclassified", team)
        old = what["p-old"].split("\n")
        self.assertTrue(old[0].endswith("[Mozilla/5.0]"))  # 02:20 untouched
        self.assertIn("AS64501 EXAMPLE-CLOUD, US, not a known teammate network)", old[1])
        self.assertEqual(old[2], line("02:50", BARRED, " (classified 2026-11-03: x)"))
        self.assertEqual(p["summary"]["reclassified"], 2)
        self.assertEqual(sorted(x["verdict"] for x in p["reclassified"]), ["teammate", "unknown"])

    def test_a_teammate_sets_staff_but_never_over_a_staff_value(self):
        # ENGBACK-2614: the Staff column follows Actor. An empty or "Not staff" value becomes "Staff" when a
        # teammate match turns an unclassified Actor into "Staff"; "Staff" already set is left alone.
        _, re_, _, _ = run(ROWS, "--untagged-since", "2026-11-03T02:30Z")
        props = {u["page_id"]: u["properties"] for u in self.plan(ROWS, re_)["updates"]}
        self.assertEqual(props["p-team"]["Staff"], {"select": {"name": "Staff"}})
        self.assertEqual(props["p-old"]["Staff"], {"select": {"name": "Not staff"}})  # filled, not a teammate
        def with_staff(value):
            return [dict(r, properties={**r["properties"], "Staff": {"select": {"name": value}}}) for r in ROWS]
        props = {u["page_id"]: u["properties"] for u in self.plan(with_staff("Not staff"), re_)["updates"]}
        self.assertEqual(props["p-team"]["Staff"], {"select": {"name": "Staff"}})
        self.assertNotIn("Staff", props["p-old"])  # a value is never refilled
        props = {u["page_id"]: u["properties"] for u in self.plan(with_staff("Staff"), re_)["updates"]}
        self.assertNotIn("Staff", props["p-team"])
        self.assertNotIn("Staff", props["p-old"])  # a person's "Staff" on a non-teammate row stays

    def test_a_teammate_makes_actor_staff_with_the_name_in_notes_unless_actor_is_set(self):
        # ENG-1870 (Jerome, 2026-11-10): Actor is exactly "Staff" so the log filters on it; the likely
        # teammate's name goes in Notes, marked not confirmed.
        _, re_, _, _ = run(ROWS, "--untagged-since", "2026-11-03T02:30Z")
        self.assertEqual(re_["ips"][TEAM]["name"], "rose@")
        self.assertEqual(re_["ips"][STRANGER]["name"], "")
        p = self.plan(ROWS, re_)
        props = {u["page_id"]: u["properties"] for u in p["updates"]}
        self.assertEqual(props["p-team"]["Actor"]["rich_text"][0]["text"]["content"], "Staff")
        notes = "".join(x["text"]["content"] for x in props["p-team"]["Notes"]["rich_text"])
        self.assertIn("likely teammate rose@, not confirmed.", notes)
        self.assertNotIn("Actor", props["p-old"])  # an unknown network names nobody
        self.assertNotIn("Notes", props["p-old"])
        # A teammate row with no Who is still Staff; Notes says there was no name.
        re_["ips"][TEAM]["name"] = ""
        team = {u["page_id"]: u["properties"] for u in self.plan(ROWS, re_)["updates"]}["p-team"]
        self.assertEqual(team["Actor"]["rich_text"][0]["text"]["content"], "Staff")
        self.assertIn("likely teammate (no name", "".join(x["text"]["content"] for x in team["Notes"]["rich_text"]))
        named = [dict(ROWS[0], properties={**ROWS[0]["properties"], "Actor": rt("Likely: someone a human named")})]
        p = self.plan(named, re_)
        self.assertNotIn("Actor", p["updates"][0]["properties"])

    def test_the_actor_name_comes_from_the_verdicts_own_row(self):
        # The PK row matches the address but has no Who; a US row on the same network does.
        kn = [{"asn": "64500", "cc": "PK", "status": "Confirmed", "who": ""},
              {"asn": "64500", "cc": "US", "status": "Confirmed", "who": "someone@example.com"}]
        self.assertEqual(reclassify.verdict(kn, "64500", "PK"), ("teammate", "Confirmed", ""))
        self.assertEqual(reclassify.verdict(kn, "64500", "US"), ("teammate", "Confirmed, someone@", "someone@"))

    def test_a_line_that_changed_since_is_skipped(self):
        _, re_, _, _ = run(ROWS)
        re_["lines"][0]["line"] = "02:33Z something else"
        p = self.plan(ROWS, re_)
        self.assertEqual((p["updates"], p["summary"]["reclassified"]), ([], 0))

    def test_a_sweep_that_extends_the_line_keeps_the_classified_tag(self):
        r = row("p-team", line("02:33", TEAM))
        r["properties"]["IP addresses"] = rt(TEAM)
        _, re_, _, _ = run([r])
        read = {"node": "/coaches", "op": "listener-listen", "verdict": "Allowed", "kind": "Whole real node via listener",
                "tag": "rule exists", "client": "desktop/mac browser", "ua": "Mozilla/5.0", "ip": TEAM, "bytes": 0}
        detail = [dict(read, t="2026-11-03T02:33:00Z"), dict(read, t="2026-11-03T02:40:00Z")]
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            (d / "d.jsonl").write_text("".join(json.dumps(x) + "\n" for x in detail), encoding="utf-8")
            (d / "r.json").write_text(json.dumps({"results": [r]}), encoding="utf-8")
            (d / "re.json").write_text(json.dumps(re_), encoding="utf-8")
            (d / "v.json").write_text(json.dumps({"alert": [TEAM]}), encoding="utf-8")
            out = subprocess.run([NODE, str(PLANNER), str(d / "d.jsonl"), str(d / "r.json"), "--verdicts", str(d / "v.json"),
                                  "--reclassify", str(d / "re.json")], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(out.returncode, 0, out.stderr)
        p = json.loads(out.stdout)
        what = p["updates"][0]["properties"]["What they did"]["rich_text"][0]["text"]["content"]
        self.assertIn(f"02:33-02:40Z {TEAM}: 2 allowed", what)
        self.assertIn("(classified ", what)
        self.assertNotIn("unclassified", what)
        self.assertEqual(p["summary"]["reclassified"], 1)

    def test_the_finding_reaches_the_pass_findings(self):
        skill = (MONITOR.parent / "SKILL.md").read_text(encoding="utf-8")
        cmd = skill.split("reclassify.py --rows", 1)[1].split("\nnode ", 1)[0]
        self.assertIn('>> "$RUN/findings.jsonl"', cmd)

    def test_no_reclassify_file_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            (d / "d.jsonl").write_text("", encoding="utf-8")
            (d / "r.json").write_text(json.dumps({"results": ROWS}), encoding="utf-8")
            r = subprocess.run([NODE, str(PLANNER), str(d / "d.jsonl"), str(d / "r.json"),
                                "--reclassify", str(d / "missing.json")], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["updates"], [])


if __name__ == "__main__":
    unittest.main()
