"""monitor-rtdb-probes' Security Incident Log planner and the profiler's --probe-detail output.

The planner decides every write to a log that holds full IPs and user agents, so its rules are
pinned here. Rows are grouped by attack type and outcome, not by IP (Jerome, 2026-10-23: "same type of
attack and no change in what's been gathered or leaked is the same"), with the `Group key` format
shared with eng:flag-suspicious-traffic: `Firebase RTDB|<root-read|guessed-name|node-read>|<outcome>`.

- **Sweeps and rows:** sweeps still split per IP on a 30-minute gap. Each group has one open row, and a
  new IP joins it. A group with no hit for 2 days closes (14 until 2026-11-03).
- **Severity:** only goes up. Denied is Low, a denied flood is Medium, and an allowed read that is
  neither the console nor ours is High. Only Medium and High become alerts.
- **Re-runs:** re-planning an applied run writes nothing.

Fixtures use documentation addresses (RFC 5737 / RFC 3849) and ASNs (RFC 5398) only.
"""
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLANNER = ROOT / "plugins/eng/skills/monitor-rtdb-probes/scripts/plan-incident-log.js"
INCIDENT_LOG = ROOT / "plugins/eng/skills/monitor-rtdb-probes/scripts/incident-log.js"
ATTEMPTS = ROOT / "plugins/eng/skills/profile-firebase-rtdb/scripts/root-attempts.js"
NODE = shutil.which("node")

NETS = {
    "203.0.113.5": {"asn": "64500", "owner": "EXAMPLE-MOBILE"},
    "198.51.100.17": {"asn": "64500", "owner": "EXAMPLE-MOBILE"},
    "198.51.100.40": {"asn": "64501", "owner": "EXAMPLE-CLOUD"},
}
GUESS = "Firebase RTDB|guessed-name|denied"
ROOT_READ = "Firebase RTDB|root-read|denied"


def attempt(t, ip, node="/clients", op="rest-read", verdict="Denied", kind="Guessed name (scanner)",
            client="unknown/unknown curl/8.0", ua="curl/8.0", bytes_=None):
    return {"t": t, "node": node, "op": op, "verdict": verdict, "kind": kind, "client": client,
            "ua": ua, "ip": ip, "bytes": bytes_}


def row(page_id, ips, detected, ended, what, network, requests=1, group=GUESS, severity="Low",
        category="Forced browsing", found_by="eng:monitor-rtdb-probes", max_ips=None):
    rt = lambda s: {"rich_text": [{"plain_text": s}]}
    props = {
        "Incident": {"title": [{"plain_text": "RTDB guessed node names (denied)"}]},
        "System": {"select": {"name": "Firebase RTDB"}},
        "Category": {"select": {"name": category}},
        "Detected (UTC)": {"date": {"start": detected}},
        "Ended (UTC)": {"date": {"start": ended}},
        "IP addresses": rt(", ".join(ips)),
        "Max IPs": {"number": len(ips) if max_ips is None else max_ips},
        "User agent": rt("curl/8.0 (1)"),
        "Route / path": rt("/clients via REST"),
        "Source network": rt(network),
        "What they did": rt(what),
        "Requests": {"number": requests},
        "Found by": rt(found_by),
    }
    if group is not None:
        props["Group key"] = rt(group)
        props["Severity"] = {"select": {"name": severity}}
    return {"id": page_id, "properties": props}


def text(prop):
    return "".join(x.get("plain_text", x.get("text", {}).get("content", "")) for x in prop["rich_text"])


def apply(rows, plan):
    """What incident-log.js apply --apply does to the table, in memory."""
    out = [json.loads(json.dumps(r)) for r in rows]
    for u in plan["updates"]:
        page = next(r for r in out if r["id"] == u["page_id"])
        page["properties"].update(u["properties"])
    for i, c in enumerate(plan["creates"]):
        out.append({"id": f"new-{i}", "properties": c["properties"]})
    return out


def bash_commands(skill_md):
    """Every command in a SKILL.md's ```bash blocks, continuation lines joined, in order."""
    cmds, block, cur = [], False, ""
    for line in skill_md.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            block = line.strip() == "```bash"
            continue
        if not block:
            continue
        cur += line.split("  #")[0].rstrip()
        if cur.endswith("\\"):
            cur = cur[:-1]
            continue
        if cur.strip():
            cmds.append(cur.strip())
        cur = ""
    return cmds


class VerdictsWiring(unittest.TestCase):
    """Both callers hand the planner and the whois lookup the network check's verdicts. Without them a
    teammate's listen-only console view is logged as a High incident (2026-10-28 22:06Z)."""

    VERDICTS = '--verdicts "$RUN/verdicts.json"'

    def check(self, skill, producer_first):
        cmds = bash_commands(ROOT / f"plugins/eng/skills/{skill}/SKILL.md")
        users = [i for i, c in enumerate(cmds) if "plan-incident-log.js" in c or "lookup-networks.js" in c]
        self.assertEqual(len(users), 2, cmds)
        for i in users:
            self.assertIn(self.VERDICTS, cmds[i])
        if producer_first:
            made = [i for i, c in enumerate(cmds) if "check_networks.py" in c and self.VERDICTS in c]
            self.assertTrue(made and made[0] < min(users), cmds)

    def test_monitor_firebase_pass(self):
        self.check("monitor-firebase", producer_first=False)
        run_pass = (ROOT / "plugins/eng/skills/monitor-firebase/scripts/run_pass.sh").read_text(encoding="utf-8")
        self.assertRegex(run_pass, r'check_networks\.py" --detail "\$RUN/detail\.jsonl" --verdicts "\$RUN/verdicts\.json"')

    def test_monitor_rtdb_probes_daily_run(self):
        self.check("monitor-rtdb-probes", producer_first=True)


@unittest.skipUnless(NODE, "node not installed")
class PlannerTest(unittest.TestCase):
    def plan(self, lines, rows=(), nets=NETS, verdicts=None):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "d.jsonl").write_text("".join(json.dumps(l) + "\n" for l in lines))
        (tmp / "r.json").write_text(json.dumps({"results": list(rows)}))
        (tmp / "n.json").write_text(json.dumps(nets))
        extra = []
        if verdicts is not None:
            if verdicts != "missing":
                (tmp / "v.json").write_text(json.dumps(verdicts))
            extra = ["--verdicts", str(tmp / "v.json")]
        r = subprocess.run([NODE, str(PLANNER), str(tmp / "d.jsonl"), str(tmp / "r.json"), str(tmp / "n.json"), *extra],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_the_person_in_our_user_agent_never_reaches_a_row(self):
        ours = "AuraClaude/jdoe/eng:delete-coach@1.2.0 (session s)"
        p = self.plan([attempt("2026-10-22T10:00:00Z", "203.0.113.5", node="/", kind="Root read",
                               client="unknown/unknown AuraClaude/eng:delete-coach@1.2.0", ua=ours)])
        out = json.dumps(p)
        self.assertNotIn("jdoe", out)
        self.assertIn("AuraClaude/eng:delete-coach@1.2.0", out)

    def test_an_adhoc_agent_loses_the_person_too(self):
        p = self.plan([attempt("2026-10-22T10:00:00Z", "203.0.113.5", node="/", kind="Root read",
                               client="unknown/unknown AuraClaude/adhoc:ENG-1", ua="AuraClaude/jdoe/adhoc:ENG-1 (session s)")])
        out = json.dumps(p)
        self.assertNotIn("jdoe", out)
        self.assertIn("AuraClaude/adhoc:ENG-1", out)

    def group_row(self):
        # The open guessed-name group: one address of a mobile network, last hit 16:17Z.
        return row("page-guess", ["2001:db8:d408::856"], "2026-10-22T05:12:00.000+00:00",
                   "2026-10-22T16:17:00.000+00:00",
                   "05:12Z 2001:db8:d408::856: 1 denied unauthenticated REST read of /clients [curl/8.0]\n"
                   "16:17Z 2001:db8:d408::856: 3 denied unauthenticated REST reads of /sms, /otp, /AllSms [curl/8.0]",
                   "EXAMPLE-MOBILE AS64500 2001::/16", requests=4)

    def test_one_row_per_group_with_a_line_per_ip_sweep(self):
        p = self.plan([
            attempt("2026-10-22T10:00:00Z", "203.0.113.5"),
            attempt("2026-10-22T10:29:00Z", "203.0.113.5", node="/env"),   # 29 min: same sweep
            attempt("2026-10-22T11:00:00Z", "203.0.113.5", node="/config"),  # 31 min: new sweep
            attempt("2026-10-22T10:05:00Z", "198.51.100.40"),             # other IP and network: same group
        ])
        self.assertEqual((p["summary"]["sweeps"], p["summary"]["groups"]), (3, 1))
        (c,) = p["creates"]
        props = c["properties"]
        self.assertEqual(text(props["Group key"]), GUESS)
        self.assertEqual(props["Severity"]["select"]["name"], "Low")
        self.assertEqual(props["Category"]["select"]["name"], "Forced browsing")
        self.assertEqual(props["Outcome"]["select"]["name"], "Denied by rules")
        self.assertEqual(props["Status"]["select"]["name"], "Monitoring")
        self.assertEqual(props["Incident"]["title"][0]["text"]["content"], "RTDB guessed node names (denied)")
        self.assertEqual(text(props["What they did"]).split("\n"), [
            "10:00-10:29Z 203.0.113.5: 2 denied unauthenticated REST reads of /clients, /env [curl/8.0]",
            "10:05Z 198.51.100.40: 1 denied unauthenticated REST read of /clients [curl/8.0]",
            "11:00Z 203.0.113.5: 1 denied unauthenticated REST read of /config [curl/8.0]",
        ])
        self.assertEqual(text(props["IP addresses"]), "203.0.113.5, 198.51.100.40")
        self.assertEqual((props["Max IPs"]["number"], props["Requests"]["number"]), (2, 4))
        self.assertEqual(text(props["Source network"]),
                         "EXAMPLE-MOBILE AS64500 203.0.0.0/16; EXAMPLE-CLOUD AS64501 198.51.0.0/16")
        self.assertEqual(p["alerts"], [])

    def test_one_sweep_of_two_attack_types_splits_into_two_groups(self):
        p = self.plan([attempt("2026-10-22T10:00:00Z", "203.0.113.5", node="/", kind="Root read"),
                       attempt("2026-10-22T10:01:00Z", "203.0.113.5", node="/sms"),
                       attempt("2026-10-22T10:02:00Z", "203.0.113.5", node="/users", kind="Whole real node via REST")])
        got = {text(c["properties"]["Group key"]): c["properties"]["Category"]["select"]["name"] for c in p["creates"]}
        self.assertEqual(got, {ROOT_READ: "Other unrecognized access", GUESS: "Forced browsing",
                               "Firebase RTDB|node-read|denied": "Other unrecognized access"})

    def test_an_update_never_touches_a_human_set_actor(self):
        # Jerome, 2026-11-02: a human may set "Likely: <name>"; the loop keeps it.
        old = self.group_row()
        old["properties"]["Actor"] = {"rich_text": [{"plain_text": "Likely: a named teammate"}]}
        p = self.plan([attempt("2026-10-24T09:10:00Z", "198.51.100.40", node="/otp")], [old])
        (u,) = p["updates"]
        self.assertNotIn("Actor", u["properties"])
        (after,) = apply([old], p)
        self.assertEqual(text(after["properties"]["Actor"]), "Likely: a named teammate")

    def test_staff_is_set_on_creates_and_filled_only_where_empty(self):
        # ENGBACK-2614: a staff group row is "Staff", any other "Not staff". An existing row this skill
        # updates gets a value only when it has none, so a person's value stays.
        read = attempt("2026-11-09T23:44:00Z", "203.0.113.5", node="/", op="rest-read", verdict="Allowed",
                       kind="Root read", client="web/os_x browser", ua="Mozilla/5.0", bytes_=512)
        staff = {"attributed": [], "console": [], "teammate": [], "confirmed": ["203.0.113.5"]}
        (c,) = self.plan([read], verdicts=staff)["creates"]
        self.assertEqual(c["properties"]["Staff"], {"select": {"name": "Staff"}})
        (c,) = self.plan([attempt("2026-10-22T10:00:00Z", "198.51.100.40")])["creates"]
        self.assertEqual(c["properties"]["Staff"], {"select": {"name": "Not staff"}})
        probe = [attempt("2026-10-24T09:10:00Z", "198.51.100.40", node="/otp")]
        (u,) = self.plan(probe, [self.group_row()])["updates"]
        self.assertEqual(u["properties"]["Staff"], {"select": {"name": "Not staff"}})
        for value in ("Staff", "Not staff"):
            old = self.group_row()
            old["properties"]["Staff"] = {"select": {"name": value}}
            (u,) = self.plan(probe, [old])["updates"]
            self.assertNotIn("Staff", u["properties"])
        # A row another writer or a person made is never given a value.
        other = row("page-hand", ["2001:db8::1"], "2026-10-22T05:12:00.000+00:00", "2026-10-22T16:17:00.000+00:00",
                    "05:12Z 2001:db8::1: 1 denied unauthenticated REST read of /clients [curl/8.0]",
                    "EXAMPLE-MOBILE AS64500 2001::/16", found_by="a person")
        for u in self.plan(probe, [other])["updates"]:
            self.assertNotIn("Staff", u["properties"])

    def test_a_new_ip_updates_the_open_group_row(self):
        p = self.plan([attempt("2026-10-24T09:10:00Z", "198.51.100.40", node="/otp")], [self.group_row()])
        self.assertEqual(p["creates"], [])
        (u,) = p["updates"]
        self.assertEqual(u["page_id"], "page-guess")
        props = u["properties"]
        self.assertEqual(text(props["IP addresses"]), "2001:db8:d408::856, 198.51.100.40")
        self.assertEqual((props["Max IPs"]["number"], props["Requests"]["number"]), (2, 5))
        self.assertEqual(props["Ended (UTC)"]["date"]["start"], "2026-10-24T09:10:00.000Z")
        self.assertTrue(text(props["What they did"]).endswith(
            "\n2026-10-24 09:10Z 198.51.100.40: 1 denied unauthenticated REST read of /otp [curl/8.0]"))
        self.assertIn("/otp", text(props["Route / path"]))
        self.assertNotIn("Detected (UTC)", props)
        self.assertNotIn("Severity", props)  # Low stays Low

    def test_two_ips_in_one_group_within_the_window_are_one_row(self):
        # ENG-1759: never one row per IP.
        p = self.plan([attempt("2026-10-22T10:00:00Z", "203.0.113.5"),
                       attempt("2026-10-22T11:30:00Z", "198.51.100.17", node="/sms")])
        (c,) = p["creates"]
        self.assertEqual(text(c["properties"]["IP addresses"]), "203.0.113.5, 198.51.100.17")
        self.assertEqual(c["properties"]["Max IPs"]["number"], 2)

    def test_a_group_quiet_for_2_days_closes(self):
        # Jerome, 2026-11-03: the window was 14 days.
        within = self.plan([attempt("2026-10-24T16:00:00Z", "198.51.100.40")], [self.group_row()])
        self.assertEqual((len(within["creates"]), len(within["updates"])), (0, 1))
        later = self.plan([attempt("2026-10-24T16:18:00Z", "198.51.100.40")], [self.group_row()])
        self.assertEqual((len(later["creates"]), len(later["updates"])), (1, 0))
        self.assertEqual(text(later["creates"][0]["properties"]["Group key"]), GUESS)

    def test_a_flood_is_medium_alerts_and_severity_never_drops(self):
        flood = [attempt(f"2026-10-23T10:{i // 60:02d}:{i % 60:02d}Z", "198.51.100.40") for i in range(100)]
        p = self.plan(flood, [self.group_row()])
        (u,) = p["updates"]
        self.assertEqual(u["properties"]["Severity"]["select"]["name"], "Medium")
        (a,) = p["alerts"]
        self.assertEqual({k: a[k] for k in ("group_key", "severity", "attempts", "ip_count", "row")},
                         {"group_key": GUESS, "severity": "Medium", "attempts": 100, "ip_count": 1, "row": "page-guess"})
        self.assertEqual(a["ips"], ["198.51.100.40"])  # ENGBACK-2146: the alert names its readers
        # 99 is below the threshold; a Low sweep never lowers a Medium row.
        self.assertEqual(self.plan(flood[:99], [self.group_row()])["alerts"], [])
        medium = self.group_row()
        medium["properties"]["Severity"]["select"]["name"] = "Medium"
        (u,) = self.plan([attempt("2026-10-23T10:00:00Z", "198.51.100.40")], [medium])["updates"]
        self.assertNotIn("Severity", u["properties"])

    def test_console_data_viewer_is_not_logged(self):
        browser = dict(client="desktop/os_x browser", ua="Mozilla/5.0 (Macintosh)", verdict="Allowed", kind="Root read")
        p = self.plan([
            attempt("2026-10-22T12:00:00Z", "198.51.100.40", node="/", op="listener-listen", **browser),
            attempt("2026-10-22T12:00:01Z", "198.51.100.40", node="/", op="rest-read", bytes_=0, **browser),
        ])
        self.assertEqual((p["creates"], p["updates"], p["allowed_flags"], p["alerts"]), ([], [], [], []))
        self.assertEqual(p["summary"]["console_reads"], 2)
        self.assertEqual(len(p["console"]), 1)

    # 2026-10-28 22:06Z: Jerome's console view of the root sent only the listener-listen, and was logged High.
    BROWSER = dict(client="desktop/os_x browser", ua="Mozilla/5.0 (Macintosh)", verdict="Allowed", kind="Root read")

    def listen_only(self):
        return [attempt("2026-10-28T22:06:00Z", "198.51.100.40", node="/", op="listener-listen", **self.BROWSER)]

    def test_a_lone_listen_with_a_console_account_in_the_audit_log_is_the_console(self):
        p = self.plan(self.listen_only(), verdicts={"attributed": [], "console": ["198.51.100.40"]})
        self.assertEqual((p["creates"], p["updates"], p["alerts"]), ([], [], []))
        self.assertEqual(p["summary"]["console_reads"], 1)

    def test_a_lone_listen_without_the_audit_log_is_still_logged(self):
        for verdicts in (None, {"attributed": [], "console": ["203.0.113.5"]}):
            (c,) = self.plan(self.listen_only(), verdicts=verdicts)["creates"]
            self.assertEqual(text(c["properties"]["Group key"]), "Firebase RTDB|root-read|obtained")
            self.assertEqual(c["properties"]["Severity"]["select"]["name"], "High")

    # 2026-10-30 17:13Z: a teammate's console session opened /buttons and the typo /butons. check_networks
    # named the address console, yet the /butons listen became a High guessed-name row.
    def typo_listen(self, bytes_=None):
        return [attempt("2026-10-30T17:13:00Z", "198.51.100.40", node="/butons", op="listener-listen",
                        client="desktop/os_x browser", ua="Mozilla/5.0 (Macintosh)", verdict="Allowed",
                        bytes_=bytes_)]

    def test_a_console_address_listening_on_a_guessed_name_is_not_logged(self):
        p = self.plan(self.typo_listen(), verdicts={"attributed": [], "console": ["198.51.100.40"], "teammate": []})
        self.assertEqual((p["creates"], p["updates"], p["alerts"]), ([], [], []))
        self.assertEqual(p["summary"]["console_reads"], 1)

    def test_a_guessed_name_listen_still_logs_without_the_verdict_or_with_data(self):
        for lines, verdicts in ((self.typo_listen(), None),
                                (self.typo_listen(), {"attributed": [], "console": ["203.0.113.5"]}),
                                (self.typo_listen(bytes_=512), {"attributed": [], "console": ["198.51.100.40"]})):
            (c,) = self.plan(lines, verdicts=verdicts)["creates"]
            self.assertIn("RTDB guessed node names", c["properties"]["Incident"]["title"][0]["text"]["content"])

    def test_a_console_address_reading_with_curl_still_logs(self):
        lines = [attempt("2026-10-30T17:13:00Z", "198.51.100.40", node="/butons", verdict="Allowed", bytes_=0)]
        self.assertEqual(len(self.plan(lines, verdicts={"attributed": [], "console": ["198.51.100.40"]})["creates"]), 1)

    # ENG-1715 / ENG-1717: a whole real node read is logged with its address when the network check raised an
    # ALERT on it or could not classify it; a confirmed or console read stays out.
    def node_read(self, bytes_=0):
        return [attempt("2026-10-30T18:00:00Z", "198.51.100.40", node="/userSubscriptions", op="rest-read",
                        verdict="Allowed", kind="Whole real node via REST", client="desktop/os_x browser",
                        ua="Mozilla/5.0 (Macintosh)", bytes_=bytes_) | {"tag": "rule exists"}]

    def test_a_node_read_with_a_fail_verdict_is_logged_with_its_address(self):
        for key in ("fail", "alert"):
            p = self.plan(self.node_read(), verdicts={"attributed": [], "console": [], "teammate": [], key: ["198.51.100.40"]})
            (c,) = p["creates"]
            self.assertEqual(text(c["properties"]["Group key"]), "Firebase RTDB|node-read|no-data")
            self.assertEqual(c["properties"]["Severity"]["select"]["name"], "Low")
            self.assertIn("198.51.100.40", text(c["properties"]["IP addresses"]))
            self.assertEqual(p["summary"]["node_reads_not_logged"], 0)

    def test_a_node_read_without_alert_or_fail_or_attributed_stays_out(self):
        for verdicts in (None, {"attributed": [], "console": [], "fail": ["203.0.113.5"]},
                         {"attributed": ["198.51.100.40"], "console": [], "fail": ["198.51.100.40"]}):
            p = self.plan(self.node_read(), verdicts=verdicts)
            self.assertEqual((p["creates"], p["updates"]), ([], []), verdicts)
            self.assertEqual(p["summary"]["node_reads_not_logged"], 1)

    def test_a_fail_node_read_that_returned_data_is_data_accessed(self):
        (c,) = self.plan(self.node_read(4096), verdicts={"attributed": [], "console": [], "fail": ["198.51.100.40"]})["creates"]
        self.assertEqual(text(c["properties"]["Group key"]), "Firebase RTDB|node-read|data-accessed")

    def test_a_read_attributed_to_a_known_teammate_is_never_logged(self):
        lines = self.listen_only() + [attempt("2026-10-28T22:06:05Z", "198.51.100.40", node="/", op="rest-read",
                                              verdict="Allowed", kind="Root read", bytes_=4096)]
        p = self.plan(lines, verdicts={"attributed": ["198.51.100.40"], "console": ["198.51.100.40"]})
        self.assertEqual((p["creates"], p["updates"], p["alerts"], p["console"]), ([], [], [], []))
        self.assertEqual(p["summary"]["attributed_reads"], 2)
        self.assertEqual(len(p["attributed"]), 1)

    def test_an_unattributed_read_is_still_logged(self):
        lines = self.listen_only() + [attempt("2026-10-28T22:06:05Z", "203.0.113.5", node="/", op="rest-read",
                                              verdict="Allowed", kind="Root read", bytes_=4096)]
        p = self.plan(lines, verdicts={"attributed": ["198.51.100.40"], "console": []})
        (c,) = p["creates"]
        self.assertEqual(text(c["properties"]["Group key"]), "Firebase RTDB|root-read|data-accessed")
        self.assertEqual(text(c["properties"]["IP addresses"]), "203.0.113.5")
        self.assertEqual(p["summary"]["attributed_reads"], 1)

    def test_a_missing_or_broken_verdicts_file_logs_rather_than_drops(self):
        for verdicts in ("missing", ["not", "an", "object"]):
            self.assertEqual(len(self.plan(self.listen_only(), verdicts=verdicts)["creates"]), 1)

    def test_whois_never_gets_a_known_teammates_address(self):
        lines = self.listen_only() + [attempt("2026-10-28T22:06:00Z", "203.0.113.5")]
        r = subprocess.run([NODE, "-e", f"const m=require({json.dumps(str(PLANNER))});"
                            "process.stdout.write(JSON.stringify([...m.loggedIps(JSON.parse(process.argv[1]),"
                            " {attributed: ['198.51.100.40'], console: []})]))",
                            json.dumps(lines)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), ["203.0.113.5"])

    def test_an_allowed_read_by_anyone_else_is_high(self):
        listen = attempt("2026-10-22T12:00:00Z", "203.0.113.5", node="/", op="listener-listen", verdict="Allowed",
                         kind="Root read", client="desktop/os_x browser", ua="Mozilla/5.0")
        data = attempt("2026-10-22T12:00:01Z", "203.0.113.5", node="/", op="rest-read", verdict="Allowed",
                       kind="Root read", bytes_=5120)
        p = self.plan([listen, data])
        got = {text(c["properties"]["Group key"]): c["properties"] for c in p["creates"]}
        self.assertEqual(set(got), {"Firebase RTDB|root-read|obtained", "Firebase RTDB|root-read|data-accessed"})
        accessed = got["Firebase RTDB|root-read|data-accessed"]
        self.assertEqual(accessed["Severity"]["select"]["name"], "High")
        self.assertEqual(accessed["Outcome"]["select"]["name"], "Data accessed")
        self.assertEqual(accessed["Status"]["select"]["name"], "Open")
        self.assertIn("1 allowed unauthenticated REST read of /", text(accessed["What they did"]))
        self.assertEqual(got["Firebase RTDB|root-read|obtained"]["Outcome"]["select"]["name"], "Partial impact")
        self.assertEqual(sorted(a["severity"] for a in p["alerts"]), ["High", "High"])
        self.assertEqual(p["summary"]["allowed_logged"], 2)

    def test_a_teammate_read_is_never_logged_and_resolved_alone_never_quiets(self):
        # ENG-1810: a 0-byte realtime read from a Confirmed network is a teammate's, never logged, counted
        # (2026-11-05 05:24Z: a teammate's own root read reached the log as Actor Unknown). A row groups every
        # network's reads of one type and outcome, so Resolved alone never quiets anyone else's alert.
        read = attempt("2026-10-22T18:00:00Z", "203.0.113.5", node="/butons", op="listener-listen",
                       verdict="Allowed", client="web/os_x browser", ua="Mozilla/5.0", bytes_=None)
        resolved = row("page-obt", ["203.0.113.5"], "2026-10-22T12:00:00.000+00:00", "2026-10-22T12:00:00.000+00:00",
                       "12:00Z 203.0.113.5: 1 allowed realtime read of /butons [Mozilla/5.0]", "EXAMPLE-MOBILE AS64500",
                       group="Firebase RTDB|guessed-name|obtained", severity="High")
        resolved["properties"]["Status"] = {"select": {"name": "Resolved"}}
        mate = {"attributed": [], "console": [], "teammate": ["203.0.113.5"]}
        p = self.plan([read], rows=[resolved], verdicts=mate)
        self.assertEqual((p["creates"], p["updates"], p["alerts"]), ([], [], []))
        self.assertEqual((p["summary"]["teammate_reads"], p["summary"]["allowed_logged"]), (1, 0))
        self.assertEqual([a["ip"] for a in p["attributed"]], ["203.0.113.5"])
        # The same address reading data is not a teammate's 0-byte test: it is logged.
        data = attempt("2026-10-22T18:00:00Z", "203.0.113.5", node="/butons", op="listener-listen",
                       verdict="Allowed", client="web/os_x browser", ua="Mozilla/5.0", bytes_=500)
        p = self.plan([data], rows=[resolved], verdicts=mate)
        self.assertEqual((p["summary"]["teammate_reads"], p["summary"]["allowed_logged"]), (0, 1))
        p = self.plan([read], rows=[resolved], verdicts={**mate, "teammate": []})
        self.assertEqual(len(p["alerts"]), 1)

    def test_a_resolved_row_is_never_written_again(self):
        # ENG-1856 (Jerome, 2026-11-09, reversing ENG-1698): a sweep on a group whose row a person Resolved
        # opens a new row and lists the Resolved one in `reopened`; the Resolved row gets no update.
        resolved = row("page-res", ["203.0.113.5"], "2026-10-22T12:00:00.000+00:00", "2026-10-22T12:05:00.000+00:00",
                       "12:00-12:05Z 203.0.113.5: 3 denied unauthenticated REST reads of /clients x3 [curl/8.0]",
                       "EXAMPLE-MOBILE AS64500", requests=3)
        resolved["properties"]["Status"] = {"select": {"name": "Resolved"}}
        later = [attempt("2026-10-22T18:00:00Z", "198.51.100.17")]
        p = self.plan(later, rows=[resolved])
        self.assertEqual(p["updates"], [])
        (c,) = p["creates"]
        self.assertEqual(text(c["properties"]["What they did"]),
                         "18:00Z 198.51.100.17: 1 denied unauthenticated REST read of /clients [curl/8.0]")
        self.assertEqual(p["reopened"], [{"page_id": "page-res", "group_key": GUESS}])
        self.assertEqual(p["summary"]["resolved_reopened"], 1)
        # A sweep that kept going past the Resolved row's Ended: only the later attempts go to the new row.
        grown = [attempt(f"2026-10-22T12:0{m}:00Z", "203.0.113.5") for m in (0, 3, 5, 9)]
        p = self.plan(grown, rows=[resolved])
        self.assertEqual(p["updates"], [])
        (c,) = p["creates"]
        self.assertEqual((c["properties"]["Requests"]["number"], text(c["properties"]["What they did"])),
                         (1, "12:09Z 203.0.113.5: 1 denied unauthenticated REST read of /clients [curl/8.0]"))
        # A later pass that still sees the sweep from its start extends the new row's line (BugBot on #1538).
        new = {"id": "page-new", "properties": c["properties"]}
        longer = grown + [attempt("2026-10-22T12:15:00Z", "203.0.113.5")]
        p = self.plan(longer, rows=[resolved, new])
        self.assertEqual((p["creates"], [u["page_id"] for u in p["updates"]]), ([], ["page-new"]))
        u = p["updates"][0]["properties"]
        self.assertEqual((u["Requests"]["number"], text(u["What they did"])),
                         (2, "12:09-12:15Z 203.0.113.5: 2 denied unauthenticated REST reads of /clients x2 [curl/8.0]"))
        # Nothing after its Ended: nothing is written and nothing is reopened.
        p = self.plan(grown[:3], rows=[resolved])
        self.assertEqual((p["creates"], p["updates"], p["reopened"]), ([], [], []))
        # Any other status is still appended to.
        resolved["properties"]["Status"] = {"select": {"name": "Monitoring"}}
        p = self.plan(later, rows=[resolved])
        self.assertEqual((len(p["creates"]), len(p["updates"]), p["reopened"]), (0, 1, []))

    def test_a_staff_read_that_is_logged_never_shows_its_address(self):
        # ENG-1855 (Jerome, 2026-11-09, reversing 2026-10-30): a data-returning read from a Confirmed Known
        # Team Networks row is still logged, with "see Known Team Networks" in place of the IP.
        read = attempt("2026-11-09T14:45:00Z", "203.0.113.5", node="/", op="rest-read", verdict="Allowed",
                       kind="Root read", client="web/os_x browser", ua="Mozilla/5.0", bytes_=512)
        staff = {"attributed": [], "console": [], "teammate": [], "confirmed": ["203.0.113.5"]}
        (c,) = self.plan([read], verdicts=staff)["creates"]
        props = c["properties"]
        self.assertNotIn("203.0.113.5", json.dumps(props))
        self.assertEqual(text(props["IP addresses"]), "see Known Team Networks")
        self.assertEqual(text(props["What they did"]),
                         "14:45Z see Known Team Networks: 1 allowed unauthenticated REST read of / [Mozilla/5.0]")
        self.assertIn("EXAMPLE-MOBILE AS64500", text(props["Source network"]))
        # Re-running the plan writes nothing; a second staff address on another network joins the row, and
        # both networks are named.
        logged = {"id": "page-staff", "properties": props}
        p = self.plan([read], rows=[logged], verdicts=staff)
        self.assertEqual((p["creates"], p["updates"], p["summary"]["already_applied"]), ([], [], 1))
        other = attempt("2026-11-09T16:24:00Z", "198.51.100.40", node="/", op="rest-read", verdict="Allowed",
                        kind="Root read", client="web/os_x browser", ua="Mozilla/5.0", bytes_=64)
        p = self.plan([read, other], rows=[logged], verdicts={**staff, "confirmed": ["203.0.113.5", "198.51.100.40"]})
        (u,) = p["updates"]
        self.assertEqual(p["creates"], [])
        self.assertNotIn("198.51.100.40", json.dumps(u))
        self.assertEqual(text(u["properties"]["What they did"]).split("\n")[-1],
                         "16:24Z see Known Team Networks: 1 allowed unauthenticated REST read of / [Mozilla/5.0]")
        self.assertIn("EXAMPLE-CLOUD AS64501", text(u["properties"]["Source network"]))
        self.assertEqual(p["summary"]["known_network_reads"], 2)
        # A denied sweep from the same address in the pass is written the same way.
        probe = attempt("2026-11-09T14:50:00Z", "203.0.113.5")
        p = self.plan([read, probe], verdicts=staff)
        self.assertNotIn("203.0.113.5", json.dumps(p["creates"]))
        self.assertEqual(p["summary"]["known_network_reads"], 2)
        # Anyone else keeps their address.
        (c,) = self.plan([read], verdicts={**staff, "confirmed": []})["creates"]
        self.assertEqual(text(c["properties"]["IP addresses"]), "203.0.113.5")

    def test_a_staff_read_row_has_no_outside_category_and_names_the_network(self):
        # ENG-1868: a teammate's console read was filed as an outside-access category with "Find out whose
        # request it was", on a row whose network was already a Confirmed Known Team Networks row.
        read = attempt("2026-11-09T23:44:00Z", "203.0.113.5", node="/", op="rest-read", verdict="Allowed",
                       kind="Root read", client="web/os_x browser", ua="Mozilla/5.0", bytes_=512)
        staff = {"attributed": [], "console": [], "teammate": [], "confirmed": ["203.0.113.5"]}
        (c,) = self.plan([read], verdicts=staff)["creates"]
        props = c["properties"]
        self.assertNotIn("Category", props)
        self.assertEqual(text(props["Actor"]), "Staff")
        self.assertNotIn("whose request", text(props["Notes"]))
        self.assertIn("EXAMPLE-MOBILE AS64500", text(props["Notes"]))
        self.assertEqual(text(props["Group key"]), "Firebase RTDB|root-read|staff|data-accessed")
        # An outside reader's read of the same type and outcome keeps its own row, category and prompt.
        outside = attempt("2026-11-09T23:50:00Z", "198.51.100.40", node="/", op="rest-read", verdict="Allowed",
                          kind="Root read", client="web/os_x browser", ua="Mozilla/5.0", bytes_=64)
        creates = {text(c["properties"]["Group key"]): c["properties"]
                   for c in self.plan([read, outside], verdicts=staff)["creates"]}
        other = creates["Firebase RTDB|root-read|data-accessed"]
        self.assertEqual(other["Category"]["select"]["name"], "Other unrecognized access")
        self.assertEqual(text(other["Actor"]), "Unknown")
        self.assertIn("whose request", text(other["Notes"]))
        self.assertNotIn("see Known Team Networks", text(other["What they did"]))
        # A denied staff sweep stays off the outside scanners' denied row too.
        probe = attempt("2026-11-09T23:55:00Z", "203.0.113.5")
        keys = {text(c["properties"]["Group key"]) for c in self.plan([probe], verdicts=staff)["creates"]}
        self.assertEqual(keys, {"Firebase RTDB|guessed-name|staff|denied"})

    @unittest.skipUnless(NODE, "node not installed")
    def test_the_writer_keeps_the_old_other_label_until_the_option_is_renamed(self):
        # ENG-1868: "Other attack" is renamed in place to "Other unrecognized access". A write naming an
        # option the database lacks makes Notion create it, so the writer names whichever the live list has.
        writer = ROOT / "plugins/eng/skills/monitor-rtdb-probes/scripts/incident-log.js"
        script = """
const w = require(process.argv[1]);
const ds = (cats) => ({ properties: Object.fromEntries(Object.entries(w.REQUIRED).map(([k, t]) => [k,
  { type: t, select: { options: (k === 'Category' ? cats : (w.OPTIONS[k] || [])).map((name) => ({ name })) } }])) });
const base = w.OPTIONS.Category;
const out = {};
for (const [k, cats] of Object.entries({ old: [...base, w.OLD_OTHER], new: [...base, w.OTHER], none: base,
  british: [...base, 'Other unrecognised access'] })) {
  out[k] = { problems: w.checkSchema(ds(cats)).length, name: w.liveCategory(ds(cats), w.OTHER),
             forced: w.liveCategory(ds(cats), 'Forced browsing') };
}
console.log(JSON.stringify(out));
"""
        got = json.loads(subprocess.run([NODE, "-e", script, str(writer)], capture_output=True, text=True,
                                        check=True).stdout)
        self.assertEqual(got["old"], {"problems": 0, "name": "Other attack", "forced": "Forced browsing"})
        self.assertEqual(got["new"], {"problems": 0, "name": "Other unrecognized access", "forced": "Forced browsing"})
        self.assertEqual(got["none"]["problems"], 1)
        # ENG-1869: the live option is spelled with a "z"; the British spelling is not it, so a list holding
        # only that is refused rather than written to.
        self.assertEqual(got["british"]["problems"], 1)

    def test_a_teammates_realtime_root_read_from_a_confirmed_network_is_counted_not_logged(self):
        # ENG-1810, the 2026-11-05 05:24Z row: a 0-byte realtime root listen from Mac Chrome on a Confirmed
        # network, no console account in the audit log. check_networks put the address in `teammate`; the
        # planner still wrote "RTDB root reads (no-data)" with Actor Unknown.
        listen = attempt("2026-11-05T05:24:10Z", "198.51.100.77", node="/", op="listener-listen",
                         verdict="Allowed", kind="Root read", client="desktop/os_x chrome browser",
                         ua="Mozilla/5.0 (Macintosh) Chrome/142", bytes_=0)
        mate = {"attributed": [], "console": [], "teammate": ["198.51.100.77"]}
        p = self.plan([listen], verdicts=mate)
        self.assertEqual((p["creates"], p["updates"], p["alerts"]), ([], [], []))
        self.assertEqual(p["summary"]["teammate_reads"], 1)
        (entry,) = p["attributed"]
        self.assertEqual((entry["ip"], entry["paths"], entry["ops"]), ("198.51.100.77", ["/"], ["listener-listen"]))
        # Without the teammate verdict it is logged, as before.
        p = self.plan([listen], verdicts={**mate, "teammate": []})
        self.assertEqual(len(p["creates"]), 1)
        self.assertEqual(p["summary"]["teammate_reads"], 0)

    def test_our_own_allowed_read_is_flagged_never_logged(self):
        ours = dict(node="/", op="rest-read", verdict="Allowed", kind="Root read", bytes_=10,
                    client="unknown/unknown internal (eng:delete-coach)", ua="AuraClaude/eng:delete-coach@1.2.0 (session s)")
        p = self.plan([attempt("2026-10-22T12:00:00Z", "203.0.113.5", **ours)])
        self.assertEqual((p["creates"], p["updates"], p["alerts"]), ([], [], []))
        self.assertEqual([f["internal"] for f in p["allowed_flags"]], [True])
        self.assertEqual(p["summary"]["internal_flags"], 1)
        # One unlabelled attempt in the sweep makes the whole sweep someone else's: logged, High.
        p = self.plan([attempt("2026-10-22T12:00:00Z", "203.0.113.5", **ours),
                       attempt("2026-10-22T12:00:01Z", "203.0.113.5", node="/", op="rest-read", verdict="Allowed",
                               kind="Root read", bytes_=10)])
        self.assertEqual(p["allowed_flags"], [])
        (c,) = p["creates"]
        self.assertEqual(c["properties"]["Requests"]["number"], 2)

    def test_a_sweep_already_on_a_pre_grouping_row_is_not_logged_again(self):
        legacy = row("page-old", ["203.0.113.5"], "2026-10-22T10:00:00Z", "2026-10-22T10:00:00Z",
                     "10:00Z 203.0.113.5: 1 denied unauthenticated REST read of /clients [curl/8.0]",
                     "EXAMPLE-MOBILE AS64500 203.0.0.0/16", group=None)
        p = self.plan([attempt("2026-10-22T10:00:00Z", "203.0.113.5")], [legacy])
        self.assertEqual((p["creates"], p["updates"], p["summary"]["already_applied"]), ([], [], 1))

    def test_a_mixed_sweep_logged_before_grouping_is_not_logged_again(self):
        # The old per-IP line keyed the whole sweep at 10:00; its guessed-name attempts start at 10:05.
        legacy = row("page-old", ["203.0.113.5"], "2026-10-22T10:00:00Z", "2026-10-22T10:05:00Z",
                     "10:00-10:05Z 203.0.113.5: 2 denied unauthenticated REST reads of /, /sms [curl/8.0]",
                     "EXAMPLE-MOBILE AS64500 203.0.0.0/16", group=None)
        p = self.plan([attempt("2026-10-22T10:00:00Z", "203.0.113.5", node="/", kind="Root read"),
                       attempt("2026-10-22T10:05:00Z", "203.0.113.5", node="/sms")], [legacy])
        self.assertEqual((p["creates"], p["updates"], p["summary"]["already_applied"]), ([], [], 2))

    def test_whois_gets_only_the_addresses_a_plan_logs(self):
        browser = dict(client="desktop/os_x browser", ua="Mozilla/5.0", verdict="Allowed", kind="Root read", node="/")
        ours = dict(node="/", verdict="Allowed", kind="Root read", bytes_=10,
                    client="unknown/unknown internal (eng:delete-coach)", ua="AuraClaude/eng:delete-coach@1.2.0 (session s)")
        lines = [
            attempt("2026-10-22T12:00:00Z", "203.0.113.5"),                                     # denied probe
            attempt("2026-10-22T12:00:00Z", "203.0.113.6", node="/", verdict="Allowed", kind="Root read", bytes_=9),
            attempt("2026-10-22T12:00:00Z", "198.51.100.40", op="listener-listen", **browser),  # console
            attempt("2026-10-22T12:00:01Z", "198.51.100.40", op="rest-read", bytes_=0, **browser),
            attempt("2026-10-22T12:00:00Z", "198.51.100.41", **ours),                            # our session
            attempt("2026-10-22T12:00:00Z", "198.51.100.42", node="/users", kind="Denied read of a real node",
                    client="native/ios Aura/3.2", ua="Aura/3.2"),                                # signed-out app
        ]
        r = subprocess.run([NODE, "-e", f"const m=require({json.dumps(str(PLANNER))});"
                            "process.stdout.write(JSON.stringify([...m.loggedIps(JSON.parse(process.argv[1]))].sort()))",
                            json.dumps(lines)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), ["203.0.113.5", "203.0.113.6"])

    def test_sweeps_up_to_the_regroup_are_never_logged_again(self):
        # The merged rows hold every sweep up to 2026-10-24 05:16Z, some without a line, some (root reads)
        # in the guessed-name row.
        merged = row("page-guess", ["2001:db8:d408::856"], "2026-10-22T00:38:00Z", "2026-10-24T05:16:00Z",
                     "00:38Z 2001:db8:d408::856: 1 denied unauthenticated REST read of /clients [curl/8.0]",
                     "EXAMPLE-MOBILE AS64500 2001::/16", found_by="RTDB profiler, ipwatch")
        p = self.plan([attempt("2026-10-24T05:10:00Z", "198.51.100.40", node="/sms"),
                       attempt("2026-10-24T05:16:00Z", "198.51.100.41", node="/", kind="Root read")], [merged])
        self.assertEqual((p["creates"], p["updates"], p["summary"]["already_applied"]), ([], [], 2))
        p = self.plan([attempt("2026-10-24T05:17:00Z", "198.51.100.40", node="/sms")], [merged])
        self.assertEqual((len(p["creates"]), len(p["updates"])), (0, 1))

    def test_a_partial_apply_never_hides_another_groups_sweep(self):
        lines = [attempt("2026-10-25T10:00:00Z", "198.51.100.40", node="/sms"),
                 attempt("2026-10-25T10:05:00Z", "198.51.100.41", node="/", kind="Root read"),
                 attempt("2026-10-25T10:20:00Z", "198.51.100.40", node="/otp")]
        first = self.plan(lines)
        self.assertEqual(len(first["creates"]), 2)
        # Only the guessed-name create was written (Ended 10:20, later than the root read at 10:05).
        written = apply([], {"updates": [], "creates": [c for c in first["creates"]
                                                        if text(c["properties"]["Group key"]) == GUESS]})
        again = self.plan(lines, written)
        (c,) = again["creates"]
        self.assertEqual(text(c["properties"]["Group key"]), ROOT_READ)
        self.assertEqual(again["updates"], [])

    def test_replan_after_apply_is_a_no_op(self):
        lines = [
            attempt("2026-10-22T17:00:00Z", "203.0.113.5"),
            attempt("2026-10-22T18:00:00Z", "203.0.113.5", node="/config"),
            attempt("2026-10-22T19:10:00Z", "198.51.100.17"),
            attempt("2026-10-22T17:05:00Z", "198.51.100.40", node="/", kind="Root read"),
        ]
        rows = [self.group_row()]
        first = self.plan(lines, rows)
        self.assertEqual((len(first["creates"]), len(first["updates"])), (1, 1))
        again = self.plan(lines, apply(rows, first))
        self.assertEqual((again["creates"], again["updates"]), ([], []))
        self.assertEqual(again["summary"]["already_applied"], first["summary"]["sweeps"])

    def test_a_sweep_that_grew_after_apply_rewrites_its_line(self):
        first_read = attempt("2026-10-23T05:20:10Z", "198.51.100.40", node="/", kind="Root read")
        first = self.plan([first_read])
        applied = apply([], first)
        later_read = attempt("2026-10-23T05:38:30Z", "198.51.100.40", node="/", kind="Root read", ua="curl/8.1")
        grown = self.plan([first_read, later_read], applied)
        self.assertEqual(grown["creates"], [])
        (u,) = grown["updates"]
        props = u["properties"]
        self.assertEqual(props["Requests"]["number"], 2)
        self.assertEqual(props["Ended (UTC)"]["date"]["start"], "2026-10-23T05:38:00.000Z")
        self.assertEqual(text(props["What they did"]).split("\n"),
                         ["05:20-05:38Z 198.51.100.40: 2 denied unauthenticated REST reads of / x2 [curl/8.0; curl/8.1]"])
        self.assertEqual(text(props["User agent"]), "curl/8.0 (1)\ncurl/8.1 (1)")
        self.assertEqual((grown["summary"]["extended"], grown["summary"]["already_applied"]), (1, 0))
        reapplied = apply(applied, grown)
        self.assertEqual(self.plan([first_read, later_read], reapplied)["updates"], [])
        self.assertEqual(self.plan([later_read], reapplied)["updates"], [])

    def test_a_sweep_split_across_two_runs_is_folded_not_counted_twice(self):
        early = attempt("2026-10-25T06:34:10Z", "198.51.100.40", node="/", kind="Root read")
        late = attempt("2026-10-25T06:48:20Z", "198.51.100.40", node="/", kind="Root read", ua="curl/8.1")
        # Two windowed runs, each seeing one attempt: two lines.
        rows = apply([], self.plan([early]))
        rows = apply(rows, self.plan([late], rows))
        self.assertEqual(len(text(rows[0]["properties"]["What they did"]).split("\n")), 2)
        # A run that sees both: one sweep. The 06:48 attempt is already counted.
        p = self.plan([early, late], rows)
        (u,) = p["updates"]
        props = u["properties"]
        self.assertEqual(text(props["What they did"]).split("\n"),
                         ["06:34-06:48Z 198.51.100.40: 2 denied unauthenticated REST reads of / x2 [curl/8.0; curl/8.1]"])
        self.assertNotIn("Requests", props)
        self.assertNotIn("User agent", props)
        # Applied, it's a no-op; a third attempt in the sweep adds exactly one.
        folded = apply(rows, p)
        self.assertEqual(self.plan([early, late], folded)["updates"], [])
        third = attempt("2026-10-25T06:55:00Z", "198.51.100.40", node="/", kind="Root read")
        (u,) = self.plan([early, late, third], folded)["updates"]
        self.assertEqual(u["properties"]["Requests"]["number"], 3)

    def test_a_window_starting_mid_sweep_never_relogs_its_tail(self):
        ip = "198.51.100.40"
        sweep = [attempt(f"2026-10-25T{t}Z", ip, node="/", kind="Root read")
                 for t in ("06:34:00", "06:50:00", "07:05:00", "07:10:00", "07:20:00", "07:29:00")]
        rows = apply([], self.plan(sweep))
        # Written by another session's run, as on the live log: only the lines prove what's covered.
        rows[0]["properties"]["Found by"] = {"rich_text": [{"plain_text": "RTDB profiler, ipwatch"}]}
        line = text(rows[0]["properties"]["What they did"])
        self.assertEqual(line, f"06:34-07:29Z {ip}: 6 denied unauthenticated REST reads of / x6 [curl/8.0]")
        # A window from 07:00 sees a sweep starting 07:05, inside the logged line: nothing new.
        p = self.plan(sweep[2:], rows)
        self.assertEqual((p["creates"], p["updates"], p["summary"]["already_applied"]), ([], [], 1))
        # The same window with one later attempt adds exactly that one, and stretches the line.
        later = attempt("2026-10-25T07:40:00Z", ip, node="/clients", kind="Root read", ua="curl/8.1")
        p = self.plan(sweep[2:] + [later], rows)
        (u,) = p["updates"]
        self.assertEqual(u["properties"]["Requests"]["number"], 7)
        self.assertEqual(text(u["properties"]["What they did"]),
                         f"06:34-07:40Z {ip}: 7 denied unauthenticated REST reads of / x6, /clients [curl/8.0; curl/8.1]")
        self.assertEqual(p["summary"]["extended"], 1)
        # Applied, a re-plan of either window is a no-op.
        stretched = apply(rows, p)
        self.assertEqual(self.plan(sweep[2:] + [later], stretched)["updates"], [])
        self.assertEqual(self.plan(sweep + [later], stretched)["updates"], [])

    def test_text_properties_fit_and_replan_still_no_op(self):
        ua = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) " + "x" * 120
        lines = [attempt(f"2026-10-22T{h:02d}:00:42Z", "203.0.113.5", ua=ua) for h in range(0, 24)]
        lines += [attempt("2026-10-22T23:30:17Z", f"198.51.100.{i}", ua=ua) for i in range(256)]
        nets = {f"198.51.100.{i}": {"asn": "64500", "owner": "EXAMPLE-MOBILE"} for i in range(256)}
        nets["203.0.113.5"] = {"asn": "64500", "owner": "EXAMPLE-MOBILE"}
        first = self.plan(lines, nets=nets)
        (c,) = first["creates"]  # one group, however many addresses
        for name, prop in c["properties"].items():
            if "rich_text" in prop:
                self.assertTrue(all(len(x["text"]["content"]) <= 1990 for x in prop["rich_text"]), name)
        # Never truncated: every address and user agent survives, split across rich_text items.
        ips = text(c["properties"]["IP addresses"]).split(", ")
        self.assertEqual(sorted(ips), sorted({l["ip"] for l in lines}))
        self.assertGreater(len(c["properties"]["IP addresses"]["rich_text"]), 1)
        self.assertEqual(c["properties"]["Max IPs"]["number"], 257)
        self.assertEqual(c["properties"]["Requests"]["number"], 280)
        applied = apply([], first)
        again = self.plan(lines, applied, nets=nets)
        self.assertEqual((again["creates"], again["updates"]), ([], []))
        later = self.plan([attempt("2026-10-23T09:00:00Z", "203.0.113.5", ua=ua)], applied, nets=nets)
        (u,) = later["updates"]
        self.assertEqual(u["properties"]["Requests"]["number"], 281)

    def test_user_agents_are_a_union_one_per_line_never_dropped(self):
        old = self.group_row()
        old["properties"]["User agent"] = {"rich_text": [{"plain_text":
                                                           "curl/8.0 (3); python-requests/2.31 (1)\nhand-written note\nhand-written note\ncurl/8.0"}]}
        many = [attempt("2026-10-24T10:00:00Z", "198.51.100.40", ua=f"scanner/{i}.0 " + "z" * 60) for i in range(60)]
        (u,) = self.plan(many + [attempt("2026-10-24T10:01:00Z", "198.51.100.40", ua="curl/8.0")], [old])["updates"]
        uas = text(u["properties"]["User agent"]).split("\n")
        self.assertEqual(uas[:2], ["hand-written note", "curl/8.0 (4)"])
        self.assertIn("python-requests/2.31 (1)", uas)
        self.assertEqual(len(uas), 63)
        self.assertGreater(len(u["properties"]["User agent"]["rich_text"]), 1)

    def test_lower_case_verdicts_from_older_files_count(self):
        p = self.plan([
            attempt("2026-10-22T10:00:00Z", "203.0.113.5", verdict="denied"),
            attempt("2026-10-22T10:01:00Z", "203.0.113.5", node="/env", verdict="Denied"),
        ])
        self.assertEqual(p["summary"]["sweeps"], 1)
        self.assertEqual(p["creates"][0]["properties"]["Requests"]["number"], 2)

    def test_native_lines_are_dropped_by_the_planner_too(self):
        p = self.plan([attempt("2026-10-22T10:00:00Z", "203.0.113.5", node="/users", kind="Denied read of a real node",
                               client="native/ios Aura/3.2", ua="Aura/3.2")])
        self.assertEqual((p["creates"], p["summary"]["native_excluded"]), ([], 1))

    def test_rows_of_other_systems_are_ignored(self):
        lb = self.group_row()
        lb["properties"]["System"]["select"]["name"] = "UserWeb"
        p = self.plan([attempt("2026-10-22T18:00:00Z", "203.0.113.5")], [lb])
        self.assertEqual((len(p["creates"]), p["updates"]), (1, []))


@unittest.skipUnless(NODE, "node not installed")
class Engback2146(unittest.TestCase):
    """2026-11-01 10:18Z: a teammate-looking 0-byte realtime listen of /butons, then /buttons, from AS23674.

    The planner appended it to the 10-26 AS206092 /userSubscriptions row (matched on the group key alone),
    scored it High / obtained, and its alert carried no address (ENGBACK-2146).
    """
    MAC = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
           "Chrome/155.0.0.0 Safari/537.36")
    NETS = {"198.51.100.20": {"asn": "206092", "owner": "SECFIREWALLAS"},
            "203.0.113.30": {"asn": "23674", "owner": "NAYATEL-PK"},
            "203.0.113.31": {"asn": "23674", "owner": "NAYATEL-PK"},
            "192.0.2.40": {"asn": "64500", "owner": "EXAMPLE-ISP"}}
    OBTAINED = "Firebase RTDB|node-read|obtained"
    plan = PlannerTest.plan
    group_row = PlannerTest.group_row

    def old_row(self):
        return row("page-1026", ["198.51.100.20"], "2026-10-31T03:38:00.000+00:00", "2026-10-31T03:38:00.000+00:00",
                   "03:38Z 198.51.100.20: 1 allowed unauthenticated REST read of /userSubscriptions [Mozilla/5.0]",
                   "SECFIREWALLAS AS206092 198.51.0.0/16", group=self.OBTAINED, severity="High",
                   category="Other unrecognized access")

    def listen(self, t, node, ip="203.0.113.30", bytes_=0, guessed=False):
        a = attempt(t, ip, node=node, op="listener-listen", verdict="Allowed",
                    kind="Guessed name (scanner)" if guessed else "Whole real node via SDK listen",
                    client="desktop/os_x browser", ua=self.MAC, bytes_=bytes_)
        return a if guessed else a | {"tag": "rule exists"}

    def test_the_1101_shape(self):
        lines = [self.listen("2026-11-01T10:18:05Z", "/butons", guessed=True),
                 self.listen("2026-11-01T10:18:20Z", "/buttons")]
        p = self.plan(lines, [self.old_row()], nets=self.NETS,
                      verdicts={"attributed": [], "console": [], "teammate": [], "alert": ["203.0.113.30"]})
        self.assertEqual(p["updates"], [])  # the AS206092 row is untouched
        got = {text(c["properties"]["Group key"]): c["properties"] for c in p["creates"]}
        self.assertEqual(set(got), {"Firebase RTDB|guessed-name|no-data", "Firebase RTDB|node-read|no-data"})
        for props in got.values():
            self.assertEqual(props["Severity"]["select"]["name"], "Low")
            self.assertEqual(props["Outcome"]["select"]["name"], "Allowed (no data)")
            self.assertEqual(props["Status"]["select"]["name"], "Monitoring")
            self.assertEqual(text(props["IP addresses"]), "203.0.113.30")
            self.assertIn("AS23674", text(props["Source network"]))
            self.assertNotIn("AS206092", text(props["Source network"]))
            self.assertNotIn("155.0.0.0", text(props["IP addresses"]))
        self.assertEqual(p["alerts"], [])

    def test_an_allowed_read_joins_only_its_own_networks_row(self):
        later = attempt("2026-11-01T10:18:00Z", "203.0.113.30", node="/userSubscriptions", verdict="Allowed",
                        kind="Whole real node via REST", ua=self.MAC) | {"tag": "rule exists"}
        mate = {"attributed": [], "console": [], "teammate": [], "alert": ["203.0.113.30", "198.51.100.21"]}
        p = self.plan([later], [self.old_row()], nets=self.NETS, verdicts=mate)
        self.assertEqual(p["updates"], [])
        (c,) = p["creates"]
        self.assertEqual(text(c["properties"]["Group key"]), self.OBTAINED)
        (a,) = p["alerts"]
        self.assertEqual((a["severity"], a["row"], a["ips"]), ("High", "new", ["203.0.113.30"]))
        # The same network (another address in AS206092) still extends the old row.
        same = later | {"ip": "198.51.100.21"}
        p = self.plan([same], [self.old_row()], nets={**self.NETS, "198.51.100.21": self.NETS["198.51.100.20"]},
                      verdicts=mate)
        self.assertEqual(([u["page_id"] for u in p["updates"]], p["creates"]), (["page-1026"], []))
        self.assertEqual(p["alerts"][0]["ips"], ["198.51.100.21"])

    def test_two_networks_in_one_run_get_two_rows_and_two_alerts(self):
        reads = [attempt("2026-11-01T10:00:00Z", ip, node="/", op="rest-read", verdict="Allowed", kind="Root read",
                         bytes_=512) for ip in ("203.0.113.30", "192.0.2.40")]
        p = self.plan(reads, nets=self.NETS)
        self.assertEqual(len(p["creates"]), 2)
        self.assertEqual(sorted(a["ips"][0] for a in p["alerts"]), ["192.0.2.40", "203.0.113.30"])

    def test_denied_rows_still_hold_every_network(self):
        p = self.plan([attempt("2026-10-23T16:00:00Z", "203.0.113.31")], [self.group_row()], nets=self.NETS)
        self.assertEqual(([u["page_id"] for u in p["updates"]], p["creates"]), (["page-guess"], []))

    def test_a_missing_byte_count_stays_obtained_and_high(self):
        p = self.plan([self.listen("2026-11-01T10:18:20Z", "/buttons", bytes_=None)], nets=self.NETS,
                      verdicts={"attributed": [], "console": [], "teammate": [], "alert": ["203.0.113.30"]})
        (c,) = p["creates"]
        self.assertEqual((text(c["properties"]["Group key"]), c["properties"]["Severity"]["select"]["name"]),
                         (self.OBTAINED, "High"))

    def test_a_whois_miss_matches_its_own_16_not_a_longer_one(self):
        # 10.20.0.0/16 is a substring of 110.20.0.0/16; the read must not join that row.
        old = row("page-110", ["110.20.3.4"], "2026-10-31T03:38:00.000+00:00", "2026-10-31T03:38:00.000+00:00",
                  "03:38Z 110.20.3.4: 1 allowed unauthenticated REST read of /userSubscriptions [Mozilla/5.0]",
                  "110.20.0.0/16", group=self.OBTAINED, severity="High", category="Other unrecognized access")
        read = attempt("2026-11-01T10:18:00Z", "10.20.3.4", node="/userSubscriptions", verdict="Allowed",
                       kind="Whole real node via REST", ua=self.MAC) | {"tag": "rule exists"}
        mate = {"attributed": [], "console": [], "teammate": [], "alert": ["10.20.3.4", "110.20.9.9"]}
        p = self.plan([read], [old], nets={}, verdicts=mate)
        self.assertEqual((p["updates"], len(p["creates"])), ([], 1))
        # The same /16 still joins.
        p = self.plan([read | {"ip": "110.20.9.9"}], [old], nets={}, verdicts=mate)
        self.assertEqual([u["page_id"] for u in p["updates"]], ["page-110"])

    def test_a_dotted_browser_version_is_never_an_address(self):
        # A legacy row whose IP column picked up user-agent text: only the real address survives a merge.
        old = self.old_row()
        old["properties"]["IP addresses"] = {"rich_text": [{"plain_text":
            "198.51.100.20; Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/140.0.0.0 Safari/537.36"}]}
        same = attempt("2026-11-01T10:18:00Z", "198.51.100.21", node="/userSubscriptions", verdict="Allowed",
                       kind="Whole real node via REST", ua=self.MAC) | {"tag": "rule exists"}
        p = self.plan([same], [old], nets={**self.NETS, "198.51.100.21": self.NETS["198.51.100.20"]},
                      verdicts={"attributed": [], "console": [], "teammate": [], "alert": ["198.51.100.21"]})
        (u,) = p["updates"]
        self.assertEqual(text(u["properties"]["IP addresses"]), "198.51.100.20, 198.51.100.21")
        self.assertEqual(u["properties"]["Max IPs"]["number"], 2)


@unittest.skipUnless(NODE, "node not installed")
class ProbeDetailTest(unittest.TestCase):
    def test_detail_lines_mode_and_native_exclusion(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        prof = tmp / "profiler"
        prof.mkdir()
        rules = tmp / "rules.json"
        rules.write_text(json.dumps({"rules": {"users": {}, "catalog": {}}}))

        def op(path, name, allowed, ip, platform, ua, ts, bytes_=0):
            d = {"name": name, "allowed": allowed, "timestamp": ts, "bytes": bytes_,
                 "client": {"remoteAddress": {"address": ip},
                            "userAgent": {"platform": platform, "os": "unknown", "userAgentString": ua}}}
            if path is not None:
                d["path"] = path
            return "[?25l" + json.dumps(d)

        ts = 1792663200000
        (prof / "firebase.log").write_text("\n".join([
            op(["clients"], "rest-read", False, "203.0.113.5", "unknown", "curl/8.0", ts),
            op(None, "rest-read", False, "203.0.113.6", "desktop", "Mozilla/5.0 (X11)", ts + 1000),
            op(["users"], "listener-listen", False, "198.51.100.1", "native", "Aura/3.2", ts + 2000),
            op(None, "listener-listen", True, "198.51.100.9", "desktop", "Mozilla/5.0 (Macintosh)", ts + 3000),
            op(["catalog"], "listener-listen", True, "198.51.100.2", "native", "Aura/3.2", ts + 4000),
        ]) + "\n")
        out = tmp / "detail.jsonl"
        r = subprocess.run([NODE, str(ATTEMPTS), str(prof), str(rules), "", "--probe-detail", str(out)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(stat.S_IMODE(os.stat(out).st_mode), 0o600)
        lines = [json.loads(l) for l in out.read_text().splitlines()]
        self.assertEqual([(l["node"], l["verdict"], l["ip"]) for l in lines], [
            ("/clients", "Denied", "203.0.113.5"),
            ("/", "Denied", "203.0.113.6"),
            ("/", "Allowed", "198.51.100.9"),
        ])
        self.assertEqual(lines[0]["ua"], "curl/8.0")
        self.assertEqual(lines[0]["kind"], "Guessed name (scanner)")
        self.assertEqual(set(lines[0]), {"t", "node", "op", "verdict", "kind", "tag", "client", "ua", "ip", "bytes"})
        self.assertNotIn("198.51.100.1", out.read_text())
        # stdout stays counts-only.
        for ip in ("203.0.113.5", "203.0.113.6", "198.51.100.9"):
            self.assertNotIn(ip, r.stdout)

    def test_the_consoles_typo_node_from_the_producer_is_not_an_incident(self):
        # root-attempts.js output for the 2026-10-30 17:13Z session, fed to the planner with check_networks'
        # console verdict: /buttons is a real node (never logged), /butons a guessed name.
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        prof = tmp / "profiler"
        prof.mkdir()
        rules = tmp / "rules.json"
        rules.write_text(json.dumps({"rules": {"buttons": {}, "users": {}}}))
        ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"

        def listen(node, ts, bytes_):
            return json.dumps({"name": "listener-listen", "allowed": True, "timestamp": ts, "bytes": bytes_,
                               "path": [node], "client": {"remoteAddress": {"address": "198.51.100.40"},
                                                          "userAgent": {"platform": "desktop", "os": "os_x",
                                                                        "userAgentString": ua}}})

        ts = 1793121180000
        (prof / "firebase.log").write_text(listen("buttons", ts, 2048) + "\n" + listen("butons", ts + 4000, 0) + "\n")
        out = tmp / "detail.jsonl"
        r = subprocess.run([NODE, str(ATTEMPTS), str(prof), str(rules), "", "--probe-detail", str(out)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = [json.loads(l) for l in out.read_text().splitlines()]
        self.assertEqual(sorted(l["tag"] for l in lines), ["NO RULE (guessed name)", "rule exists"])
        (tmp / "r.json").write_text("[]")
        (tmp / "n.json").write_text(json.dumps(NETS))
        for console, creates in ((["198.51.100.40"], 0), ([], 1)):
            (tmp / "v.json").write_text(json.dumps({"attributed": [], "console": console, "teammate": []}))
            r = subprocess.run([NODE, str(PLANNER), str(out), str(tmp / "r.json"), str(tmp / "n.json"),
                                "--verdicts", str(tmp / "v.json")], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(len(json.loads(r.stdout)["creates"]), creates, console)

    def test_user_agent_is_a_label_not_trust(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        prof = tmp / "profiler"
        prof.mkdir()
        rules = tmp / "rules.json"
        rules.write_text(json.dumps({"rules": {"users": {}}}))
        ours = "AuraClaude/jdoe/eng:delete-coach@1.2.0 (session s)"  # the person stays out of the label

        def op(path, allowed, ip, ua, ts):
            return json.dumps({"name": "rest-read", "allowed": allowed, "timestamp": ts, "bytes": 0, "path": path,
                               "client": {"remoteAddress": {"address": ip},
                                          "userAgent": {"platform": "unknown", "os": "unknown", "userAgentString": ua}}})

        ts = 1792663200000
        (prof / "firebase.log").write_text("\n".join([
            op(["users"], False, "203.0.113.5", "axios/1.6.0", ts),
            op(["users"], False, "203.0.113.6", "firebase-admin-node/12.0", ts + 1000),
            op([], False, "203.0.113.7", ours, ts + 2000),
            op([], True, "198.51.100.9", ours, ts + 3000),
            op([], True, "198.51.100.8", "firebase-admin-node/12.0", ts + 4000),
        ]) + "\n")
        out = tmp / "detail.jsonl"
        r = subprocess.run([NODE, str(ATTEMPTS), str(prof), str(rules), "", "--probe-detail", str(out)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        got = [(l["ip"], l["verdict"], l["client"]) for l in map(json.loads, out.read_text().splitlines())]
        self.assertEqual(got, [
            # Denied Admin-SDK-looking agents are kept: anyone can send that header.
            ("203.0.113.5", "Denied", "unknown/unknown axios/1.6.0"),
            ("203.0.113.6", "Denied", "unknown/unknown firebase-admin-node/12.0"),
            # Our agent on a denied request is not internal.
            ("203.0.113.7", "Denied", "unknown/unknown AuraClaude/eng:delete-coach@1.2.0"),
            ("198.51.100.9", "Allowed", "unknown/unknown internal (eng:delete-coach)"),
        ])

    def test_detail_file_exists_even_with_no_attempts(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "profiler").mkdir()
        (tmp / "profiler" / "firebase.log").write_text("")
        out = tmp / "detail.jsonl"
        r = subprocess.run([NODE, str(ATTEMPTS), str(tmp / "profiler"), "", "", "--probe-detail", str(out)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(out.exists())
        self.assertEqual(out.read_text(), "")



@unittest.skipUnless(NODE, "node not installed")
class StaffColumnApply(unittest.TestCase):
    """incident-log.js writes Staff only once the column exists with that option (ENGBACK-2614)."""

    def fit(self, schema_props, properties):
        js = ("const m = require(process.argv[1]); const ds = JSON.parse(process.argv[2]);"
              "const p = JSON.parse(process.argv[3]); const s = m.fitStaff(ds, p);"
              "console.log(JSON.stringify({ skipped: s, p }));")
        r = subprocess.run([NODE, "-e", js, str(INCIDENT_LOG), json.dumps({"properties": schema_props}),
                            json.dumps(properties)], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_staff_waits_for_the_column_and_its_option(self):
        props = {"Actor": {"rich_text": []}, "Staff": {"select": {"name": "Not staff"}}}
        col = lambda *o: {"Staff": {"type": "select", "select": {"options": [{"name": x} for x in o]}}}
        for schema, kept in (({}, False), ({"Staff": {"type": "rich_text"}}, False), (col("Staff"), False),
                             (col("Staff", "Not staff"), True)):
            with self.subTest(schema=schema):
                got = self.fit(schema, props)
                self.assertEqual("Staff" in got["p"], kept)
                self.assertEqual(got["skipped"], not kept)
                self.assertIn("Actor", got["p"])
        self.assertEqual(self.fit({}, {"Actor": {"rich_text": []}})["skipped"], False)

    def test_the_schema_check_never_requires_staff(self):
        js = ("const m = require(process.argv[1]); const props = {};"
              "for (const [k, t] of Object.entries(m.REQUIRED)) props[k] = { type: t, [t]: { options: [] } };"
              "for (const [k, o] of Object.entries(m.OPTIONS)) props[k].select.options = o.map((name) => ({ name }));"
              "props.Category.select.options.push({ name: m.OTHER });"
              "console.log(JSON.stringify(m.checkSchema({ properties: props })));")
        r = subprocess.run([NODE, "-e", js, str(INCIDENT_LOG)], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(json.loads(r.stdout), [])


if __name__ == "__main__":
    unittest.main()
