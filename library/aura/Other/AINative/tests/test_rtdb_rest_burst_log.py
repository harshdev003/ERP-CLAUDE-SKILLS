"""REST bursts in the Security Incident Log (ENG-1745).

The first live REST-burst ALERT (2026-11-03 00:49Z, about 286 reads of /channels from one okhttp
client) wrote no Incident Log row and lost its IP: the planner only took probe detail, and the raw
profiler log that held the address was deleted when the profile ended. Now rest-reads.js writes the
per-source detail while the raw log exists, evaluate_profile.py keeps the burst's sources, and the
planner writes one row per IP + user agent + UTC day.

Security's rules (2026-11-03): Outcome `Allowed (public data)` / `Data accessed` / `Allowed (no data)`;
Low for public-by-rule paths only, Medium for any non-public path, shallow or listing reads or more than
1,000 reads in 10 min from one IP, High for /users, a non-public path with large sent bytes, or an
address already on a probe row. Confirmed teammates are never logged.

The end-to-end case feeds each stage its real producer's output. Fixtures use documentation addresses
(RFC 5737) and ASNs (RFC 5398) only.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / "plugins/eng/skills/profile-firebase-rtdb/scripts"
MONITOR = ROOT / "plugins/eng/skills/monitor-firebase/scripts"
PROBES = ROOT / "plugins/eng/skills/monitor-rtdb-probes/scripts"
NODE = shutil.which("node")

SCRAPER = "203.0.113.77"
NETS = {SCRAPER: {"asn": "64510", "owner": "EXAMPLE-HOSTING"}}
OKHTTP = {"userAgent": {"platform": "unknown", "os": "unknown", "userAgentString": "okhttp/3.4.1"},
          "remoteAddress": {"address": SCRAPER}}
T0 = 1793666940000  # 2026-11-03T00:49:00Z
PUBLIC_GROUP = "Firebase RTDB|rest-burst|/channels|public-data"


def raw(name, path, client, i, **extra):
    return "data " + json.dumps({"name": name, "allowed": True, "timestamp": T0 + i * 150, "path": path,
                                 "client": client, **extra})


def channels_burst() -> list[str]:
    """The 00:49Z scrape (102 + 92 + 92 reads of /channels), plus the app's own traffic from 7 IPs."""
    lines = [raw("rest-read", ["channels", f"-Ko{i:05d}"], OKHTTP, i, bytes=400) for i in range(102)]
    lines += [raw("rest-read", ["channels", f"-Ko{i:05d}", "analytics"], OKHTTP, i, bytes=90) for i in range(92)]
    lines += [raw("rest-read", ["channels", f"-Ko{i:05d}", "new"], OKHTTP, i, bytes=60) for i in range(92)]
    for k in range(7):
        app = {"userAgent": {"platform": "mobile", "os": "android", "userAgentString": "Dalvik/2.1.0"},
               "remoteAddress": {"address": f"198.51.100.{10 + k}"}}
        lines += [raw("rest-read", ["coaches", f"c{i}"], app, i, bytes=300) for i in range(21 if k < 6 else 20)]
    return lines


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", **kw)
    if r.returncode:
        raise AssertionError(f"{cmd[1]} exited {r.returncode}: {r.stderr}")
    return r.stdout


def burst(ip=SCRAPER, ua="okhttp/3.4.1", t0="2026-11-03T00:49:00.000Z", t1="2026-11-03T00:49:42.000Z", n=286,
          shapes=None, nonpublic=(), shallow=0, listing=0, root=0, bytes_=60000, sized=None, minutes=10):
    return {"ip": ip, "ua": ua, "client": f"unknown/unknown {ua.split('/')[0]}", "day": t0[:10], "t0": t0, "t1": t1,
            "n": n, "shallow": shallow, "listing": listing, "root": root, "bytes": bytes_,
            "sized": n if sized is None else sized, "shapes": shapes or {"/channels/$id": n},
            "minutes": minutes, "nonpublic": list(nonpublic)}


def text(prop):
    return "".join(x.get("plain_text", x.get("text", {}).get("content", "")) for x in prop["rich_text"])


def as_read_back(rows, plan):
    """The table after incident-log.js apply --apply, as rows.json reads it back."""
    out = [json.loads(json.dumps(r)) for r in rows]
    for u in plan["updates"]:
        next(r for r in out if r["id"] == u["page_id"])["properties"].update(u["properties"])
    for i, c in enumerate(plan["creates"]):
        out.append({"id": f"new-{len(out)}-{i}", "properties": json.loads(json.dumps(c["properties"]))})
    for r in out:
        for v in r["properties"].values():
            for item in v.get("rich_text", []) + v.get("title", []):
                item.setdefault("plain_text", item.get("text", {}).get("content", ""))
    return out


@unittest.skipUnless(NODE, "node not installed")
class Planner(unittest.TestCase):
    def plan(self, bursts, rows=(), verdicts=None, detail=()):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "d.jsonl").write_text("".join(json.dumps(l) + "\n" for l in detail), encoding="utf-8")
        (tmp / "r.json").write_text(json.dumps({"results": list(rows)}), encoding="utf-8")
        (tmp / "n.json").write_text(json.dumps(NETS), encoding="utf-8")
        (tmp / "b.jsonl").write_text("".join(json.dumps(b) + "\n" for b in bursts), encoding="utf-8")
        (tmp / "v.json").write_text(json.dumps(verdicts or {}), encoding="utf-8")
        return json.loads(run([NODE, str(PROBES / "plan-incident-log.js"), str(tmp / "d.jsonl"), str(tmp / "r.json"),
                               str(tmp / "n.json"), "--verdicts", str(tmp / "v.json"), "--rest-bursts", str(tmp / "b.jsonl")]))

    def only_create(self, p):
        self.assertEqual(len(p["creates"]), 1, p)
        return p["creates"][0]["properties"]

    def test_the_channels_burst_end_to_end_keeps_its_ip_and_is_low(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "profiler").mkdir()
        (tmp / "profiler" / "a.log").write_text("\n".join(channels_burst()) + "\n", encoding="utf-8")
        section = run([NODE, str(PROFILE / "rest-reads.js"), str(tmp / "profiler"),
                       "--source-detail", str(tmp / "rest-sources.jsonl")])
        self.assertNotIn(SCRAPER, section)
        self.assertEqual(stat.S_IMODE(os.stat(tmp / "rest-sources.jsonl").st_mode), 0o600)
        (tmp / "profile.txt").write_text(
            "Captured 100000 operations.\nProfile 2026-11-03T00:40:00Z -> 2026-11-03T00:50:00Z (10m)\n\n"
            "Permission denied: 0 of 100000 rule-checked operations\n\n" + section + "\n", encoding="utf-8")
        out = run([sys.executable, str(MONITOR / "evaluate_profile.py"), "--profile", str(tmp / "profile.txt"),
                   "--minutes", "10", "--rest-detail", str(tmp / "rest-sources.jsonl"),
                   "--rest-bursts", str(tmp / "rest-bursts.jsonl")])
        alert = next(json.loads(l) for l in out.splitlines() if '"profile-rest-burst"' in l)
        self.assertIn("432 from 8 IPs", alert["text"])
        self.assertIn("1 source(s) at 100+ reads for the Incident Log", alert["text"])
        self.assertNotIn(SCRAPER, out)
        self.assertEqual(stat.S_IMODE(os.stat(tmp / "rest-bursts.jsonl").st_mode), 0o600)
        bursts = [json.loads(l) for l in (tmp / "rest-bursts.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([(b["ip"], b["n"], b["nonpublic"]) for b in bursts], [(SCRAPER, 286, [])])

        p = self.plan(bursts)
        props = self.only_create(p)
        self.assertEqual(text(props["IP addresses"]), SCRAPER)
        self.assertEqual(text(props["Group key"]), PUBLIC_GROUP)
        self.assertEqual(props["Outcome"]["select"]["name"], "Allowed (public data)")
        self.assertEqual(props["Category"]["select"]["name"], "Scraping / abuse")
        self.assertEqual(props["Severity"]["select"]["name"], "Low")
        self.assertEqual(props["Status"]["select"]["name"], "Monitoring")
        self.assertEqual(props["Requests"]["number"], 286)
        self.assertEqual(text(props["User agent"]), "okhttp/3.4.1 (286)")
        self.assertEqual(text(props["Source network"]), "EXAMPLE-HOSTING AS64510 203.0.0.0/16")
        self.assertIn(f"00:49Z {SCRAPER}: 286 allowed REST reads of /channels/$id x102, /channels/$id/analytics x92, "
                      "/channels/$id/new x92", text(props["What they did"]))
        self.assertEqual(p["alerts"], [])
        self.assertEqual(p["summary"]["rest_burst_sources"], 1)
        self.assertNotIn("198.51.100", json.dumps(p))

    def test_a_repeat_the_same_day_merges_into_the_row(self):
        first = self.plan([burst()])
        rows = as_read_back([], first)
        p = self.plan([burst(t0="2026-11-03T01:20:00.000Z", t1="2026-11-03T01:23:00.000Z", n=150)], rows)
        self.assertEqual(p["creates"], [])
        props = p["updates"][0]["properties"]
        self.assertEqual(props["Requests"]["number"], 436)
        self.assertEqual(props["Ended (UTC)"]["date"]["start"], "2026-11-03T01:23:00.000Z")
        self.assertEqual(text(props["User agent"]), "okhttp/3.4.1 (436)")
        self.assertEqual(len(text(props["What they did"]).splitlines()), 2)

    def test_a_burst_on_a_resolved_row_opens_a_new_row(self):
        # ENG-1856: a row a person Resolved is never appended to.
        rows = as_read_back([], self.plan([burst()]))
        rows[0]["properties"]["Status"] = {"select": {"name": "Resolved"}}
        later = burst(t0="2026-11-03T01:20:00.000Z", t1="2026-11-03T01:23:00.000Z", n=150)
        p = self.plan([later], rows)
        self.assertEqual(p["updates"], [])
        self.assertEqual(self.only_create(p)["Requests"]["number"], 150)
        self.assertEqual(p["reopened"], [{"page_id": rows[0]["id"], "group_key": PUBLIC_GROUP}])
        # The same pass planned again writes nothing, and the burst already on the Resolved row stays there.
        again = as_read_back(rows, p)
        self.assertEqual(self.plan([burst(), later], again)["summary"]["already_applied"], 2)

    def test_replanning_a_logged_pass_writes_nothing(self):
        rows = as_read_back([], self.plan([burst()]))
        p = self.plan([burst()], rows)
        self.assertEqual((p["creates"], p["updates"], p["summary"]["already_applied"]), ([], [], 1))

    def test_two_ips_with_different_uas_in_one_family_are_one_row(self):
        # ENG-1759 (Security and Jerome, 2026-11-03): one row per path family + outcome within 2 days.
        p = self.plan([burst(), burst(ip="198.51.100.88", ua="Dalvik/2.1.0")])
        props = self.only_create(p)
        self.assertEqual(sorted(text(props["IP addresses"]).split(", ")), ["198.51.100.88", SCRAPER])
        self.assertEqual((props["Max IPs"]["number"], props["Requests"]["number"]), (2, 572))
        self.assertEqual(sorted(text(props["User agent"]).split("\n")), ["Dalvik/2.1.0 (286)", "okhttp/3.4.1 (286)"])
        self.assertEqual(props["Incident"]["title"][0]["text"]["content"], "RTDB REST read bursts /channels (public-data)")

    def test_a_later_pass_within_2_days_joins_and_after_opens_a_new_row(self):
        rows = as_read_back([], self.plan([burst()]))
        p = self.plan([burst(ip="198.51.100.88", ua="Dalvik/2.1.0", t0="2026-11-04T20:00:00.000Z",
                             t1="2026-11-04T20:05:00.000Z")], rows)
        self.assertEqual(p["creates"], [])
        u = p["updates"][0]["properties"]
        self.assertEqual(text(u["IP addresses"]), f"{SCRAPER}, 198.51.100.88")
        self.assertEqual(u["Max IPs"]["number"], 1)  # the peak in one window, not the running total
        self.assertIn("\n2026-11-04 20:00-20:05Z 198.51.100.88: 286 allowed", text(u["What they did"]))
        self.only_create(self.plan([burst(t0="2026-11-06T01:00:00.000Z", t1="2026-11-06T01:01:00.000Z")], rows))

    def test_another_ua_in_the_same_minute_and_a_later_ip_before_ended_both_join(self):
        # Dedup is the exact source line, not IP+minute or the row's stored Ended.
        p = self.plan([burst(), burst(ua="Dalvik/2.1.0")])
        props = self.only_create(p)
        self.assertEqual(props["Requests"]["number"], 572)
        rows = as_read_back([], self.plan([burst(t1="2026-11-03T00:58:00.000Z")]))
        later = self.plan([burst(ip="198.51.100.88", t0="2026-11-03T00:50:00.000Z", t1="2026-11-03T00:51:00.000Z")], rows)
        self.assertEqual(later["summary"]["already_applied"], 0)
        self.assertIn("198.51.100.88", text(later["updates"][0]["properties"]["IP addresses"]))
        again = self.plan([burst(t1="2026-11-03T00:58:00.000Z")], rows)
        self.assertEqual((again["updates"], again["summary"]["already_applied"]), ([], 1))

    def test_the_same_burst_shape_on_the_next_day_is_not_a_rerun(self):
        rows = as_read_back([], self.plan([burst()]))
        nxt = self.plan([burst(t0="2026-11-04T00:49:00.000Z", t1="2026-11-04T00:49:42.000Z")], rows)
        self.assertEqual(nxt["summary"]["already_applied"], 0)
        self.assertEqual(nxt["updates"][0]["properties"]["Requests"]["number"], 572)

    def test_an_earlier_day_never_re_dates_the_rows_lines(self):
        rows = as_read_back([], self.plan([burst(t0="2026-11-04T00:49:00.000Z", t1="2026-11-04T00:49:42.000Z")]))
        p = self.plan([burst(ip="198.51.100.88", t0="2026-11-03T23:10:00.000Z", t1="2026-11-03T23:11:00.000Z")], rows)
        u = p["updates"][0]["properties"]
        self.assertNotIn("Detected (UTC)", u)
        self.assertIn("\n2026-11-03 23:10-23:11Z 198.51.100.88", text(u["What they did"]))
        rows = as_read_back(rows, p)
        again = self.plan([burst(t0="2026-11-04T00:49:00.000Z", t1="2026-11-04T00:49:42.000Z")], rows)
        self.assertEqual((again["updates"], again["summary"]["already_applied"]), ([], 1))

    def test_another_path_family_is_another_row(self):
        p = self.plan([burst(), burst(ip="198.51.100.88", shapes={"/coaches/$id": 286})])
        self.assertEqual(sorted(text(c["properties"]["Group key"]) for c in p["creates"]),
                         ["Firebase RTDB|rest-burst|/channels|public-data", "Firebase RTDB|rest-burst|/coaches|public-data"])

    def severity(self, b, rows=()):
        p = self.plan([b], rows)
        props = self.only_create(p)
        return props["Severity"]["select"]["name"], props["Outcome"]["select"]["name"], [a["severity"] for a in p["alerts"]]

    def test_severity(self):
        self.assertEqual(self.severity(burst(shallow=5)), ("Medium", "Allowed (public data)", ["Medium"]))
        self.assertEqual(self.severity(burst(listing=3)), ("Medium", "Allowed (public data)", ["Medium"]))
        self.assertEqual(self.severity(burst(n=1200, shapes={"/channels/$id": 1200})),
                         ("Medium", "Allowed (public data)", ["Medium"]))
        self.assertEqual(self.severity(burst(n=1200, minutes=30, shapes={"/channels/$id": 1200})),
                         ("Low", "Allowed (public data)", []))
        self.assertEqual(self.severity(burst(shapes={"/userListened/$id": 286}, nonpublic=["userListened"])),
                         ("Medium", "Data accessed", ["Medium"]))
        self.assertEqual(self.severity(burst(shapes={"/users/$id/profile": 286}, nonpublic=["users"])),
                         ("High", "Data accessed", ["High"]))
        self.assertEqual(self.severity(burst(shapes={"/posts/$id": 286}, nonpublic=["posts"], bytes_=2_000_000)),
                         ("High", "Data accessed", ["High"]))
        self.assertEqual(self.severity(burst(shapes={"/": 286}, root=286)), ("Medium", "Data accessed", ["Medium"]))
        self.assertEqual(self.severity(burst(bytes_=0)), ("Low", "Allowed (no data)", []))
        self.assertEqual(self.severity(burst(bytes_=0, sized=0))[1], "Allowed (public data)")

    def test_an_address_already_on_a_probe_row_is_high(self):
        probe = {"id": "page-probe", "properties": {
            "System": {"select": {"name": "Firebase RTDB"}}, "Severity": {"select": {"name": "Low"}},
            "Group key": {"rich_text": [{"plain_text": "Firebase RTDB|guessed-name|denied"}]},
            "IP addresses": {"rich_text": [{"plain_text": SCRAPER}]},
            "Detected (UTC)": {"date": {"start": "2026-10-30T10:00:00.000Z"}},
            "Ended (UTC)": {"date": {"start": "2026-10-30T10:05:00.000Z"}}}}
        self.assertEqual(self.severity(burst(), [probe])[0], "High")

    def test_a_confirmed_teammate_and_our_own_sessions_are_never_logged(self):
        p = self.plan([burst(), burst(ip="198.51.100.40", ua="AuraClaude/eng:monitor-firebase@1.8.0 (session x)")],
                      verdicts={"rest_teammate": [SCRAPER]})
        self.assertEqual((p["creates"], p["updates"]), ([], []))
        self.assertEqual([a["ip"] for a in p["attributed"]], [SCRAPER])
        self.assertEqual([a["internal"] for a in p["allowed_flags"]], [True])
        self.assertEqual(p["summary"]["rest_burst_teammate"], 1)

    def test_the_network_lookup_takes_the_burst_addresses(self):
        out = run([NODE, "-e", (
            "const { loggedIps } = require(process.argv[1]);"
            "const b = JSON.parse(process.argv[2]);"
            "console.log(JSON.stringify([...loggedIps([], {}, b)], null, 0),"
            " JSON.stringify([...loggedIps([], { rest_teammate: [b[0].ip] }, b)]));"),
            str(PROBES / "plan-incident-log.js"), json.dumps([burst()])])
        self.assertEqual(out.split(), [f'["{SCRAPER}"]', "[]"])

    def test_a_pass_with_a_burst_and_no_probe_detail_still_plans(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "r.json").write_text('{"results": []}', encoding="utf-8")
        (tmp / "b.jsonl").write_text(json.dumps(burst()) + "\n", encoding="utf-8")
        p = json.loads(run([NODE, str(PROBES / "plan-incident-log.js"), str(tmp / "missing.jsonl"), str(tmp / "r.json"),
                            "--rest-bursts", str(tmp / "b.jsonl")]))
        self.assertEqual(len(p["creates"]), 1)


class Evaluator(unittest.TestCase):
    def test_no_burst_writes_an_empty_file(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "profile.txt").write_text(
                "Profile 2026-11-03T00:40:00Z -> 2026-11-03T00:50:00Z (10m)\n\n"
                "Permission denied: 0 of 100 rule-checked operations\n\n"
                "Allowed REST reads by path shape, counts only:\n  total 40 (0 shallow) from 2 IPs\n", encoding="utf-8")
            (d / "src.jsonl").write_text(json.dumps(burst(n=40)) + "\n", encoding="utf-8")
            run([sys.executable, str(MONITOR / "evaluate_profile.py"), "--profile", str(d / "profile.txt"),
                 "--rest-detail", str(d / "src.jsonl"), "--rest-bursts", str(d / "out.jsonl")])
            self.assertEqual((d / "out.jsonl").read_text(encoding="utf-8"), "")


class NetworkCheck(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(MONITOR))
        self.addCleanup(sys.path.remove, str(MONITOR))
        spec = importlib.util.spec_from_file_location("check_networks_burst", MONITOR / "check_networks.py")
        self.cn = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.cn)

    def test_only_a_confirmed_network_is_a_teammate(self):
        kn = [{"asn": "64510", "cc": "US", "status": "Confirmed", "who": "rose@example.com"},
              {"asn": "64511", "cc": "US", "status": "Likely", "who": ""}]
        nets = {SCRAPER: ("64510", "US", "EXAMPLE", True), "198.51.100.5": ("64511", "US", "X", True),
                "198.51.100.6": ("?", "whois down", "?", False)}
        bursts = [burst(), burst(ip="198.51.100.5"), burst(ip="198.51.100.6")]
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(self.cn.burst_teammates(bursts, kn, nets.get), {SCRAPER})
        self.assertNotIn(SCRAPER, out.getvalue())
        self.assertIn("Confirmed (rose@)", out.getvalue())

    def test_our_own_sessions_are_left_out(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            f.write(json.dumps(burst(ua="AuraClaude/eng:monitor-firebase@1.8.0 (session x)")) + "\n")
        self.addCleanup(os.unlink, f.name)
        self.assertEqual(self.cn.read_bursts(f.name), [])


class Wiring(unittest.TestCase):
    def test_the_pass_writes_and_hands_on_the_burst_sources(self):
        run_pass = (MONITOR / "run_pass.sh").read_text(encoding="utf-8")
        self.assertIn('--rest-detail "$RUN/rest-sources.jsonl"', run_pass)
        self.assertIn('--rest-bursts "$RUN/rest-bursts.jsonl"', run_pass.split('"$D/evaluate_profile.py"')[1])
        self.assertIn('--rest-bursts "$RUN/rest-bursts.jsonl"', run_pass.split('"$D/check_networks.py"')[1])
        self.assertIn('--source-detail "$REST_DETAIL"', (PROFILE / "run-profile.sh").read_text(encoding="utf-8"))

    def test_the_incident_log_step_takes_the_bursts(self):
        skill = (ROOT / "plugins/eng/skills/monitor-firebase/SKILL.md").read_text(encoding="utf-8")
        for script in ("lookup-networks.js", "plan-incident-log.js"):
            cmd = skill.split(script, 1)[1].split("\nnode ", 1)[0]
            self.assertIn('--rest-bursts "$RUN/rest-bursts.jsonl"', cmd, script)


if __name__ == "__main__":
    unittest.main()
