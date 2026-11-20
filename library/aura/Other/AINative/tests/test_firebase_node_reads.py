#!/usr/bin/env python3
"""Allowed reads of one whole real RTDB node: probe detail, never the incident log, named by the network check.

The case behind it (2026-10-27): a Firebase-console read of /userSubscriptions at 16:59Z showed only as a
probe note. root-attempts.js wrote allowed attempts to probe detail only for the root or a guessed name,
so check_networks.py never saw it.

What has to hold:
- root-attempts.js writes an allowed rest-read or listener-listen of a whole real node to probe detail,
  unless the client is the apps' own SDK (`native`) or the node is public by rule;
- an SDK listen stays out of the probe counts, as before;
- the incident-log planner, fed that real detail, logs nothing;
- check_networks.py notes a known network, alerts on an unknown one, and names the console account from
  the audit log (browser calls only).
"""
from __future__ import annotations

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
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
ATTEMPTS = ROOT / "plugins/eng/skills/profile-firebase-rtdb/scripts/root-attempts.js"
PLANNER = ROOT / "plugins/eng/skills/monitor-rtdb-probes/scripts/plan-incident-log.js"
NETWORKS = ROOT / "plugins/eng/skills/monitor-firebase/scripts/check_networks.py"
NODE = shutil.which("node")
MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
TS = 1793120340000  # 2026-10-27T16:59:00Z


def op(path, name, ip, platform, ua, ts, allowed=True, bytes_=0):
    d = {"name": name, "allowed": allowed, "timestamp": ts, "bytes": bytes_, "path": path,
         "client": {"remoteAddress": {"address": ip},
                    "userAgent": {"platform": platform, "os": "os_x", "userAgentString": ua}}}
    return "x " + json.dumps(d)


@unittest.skipUnless(NODE, "node not installed")
class ProbeDetail(unittest.TestCase):
    def run_attempts(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        prof = tmp / "profiler"
        prof.mkdir()
        rules = tmp / "rules.json"
        rules.write_text(json.dumps({"rules": {"userSubscriptions": {}, "coaches": {".read": True}, "catalog": {}}}))
        (prof / "firebase.log").write_text("\n".join([
            op(["userSubscriptions"], "rest-read", "198.51.100.9", "desktop", MAC, TS, bytes_=2048),
            op(["userSubscriptions"], "listener-listen", "198.51.100.9", "desktop", MAC, TS + 1000),
            op(["coaches"], "rest-read", "203.0.113.20", "web", "Mozilla/5.0", TS + 2000),
            op(["catalog"], "listener-listen", "203.0.113.21", "native", "Aura/3.2", TS + 3000),
            op(["userSubscriptions"], "listener-listen", "203.0.113.22", "native", "Aura/3.2", TS + 4000),
        ]) + "\n")
        out = tmp / "detail.jsonl"
        r = subprocess.run([NODE, str(ATTEMPTS), str(prof), str(rules), "", "--probe-detail", str(out)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout, out

    def test_a_console_read_of_a_real_node_reaches_probe_detail(self):
        _, out = self.run_attempts()
        lines = [json.loads(l) for l in out.read_text().splitlines()]
        self.assertEqual([(l["node"], l["op"], l["tag"], l["kind"]) for l in lines], [
            ("/userSubscriptions", "rest-read", "rule exists", "Whole real node via REST"),
            ("/userSubscriptions", "listener-listen", "rule exists", "Whole real node via SDK listen"),
        ])
        self.assertEqual(lines[0]["bytes"], 2048)

    def test_an_outside_sdk_open_is_an_attempt_and_app_listens_are_routine(self):
        # ENGBACK-2627: the console's SDK open of a whole real node is now a Probe Log attempt; the apps'
        # own listens are summarized as routine (they still reach the probe JSON, as "Routine app read").
        stdout, _ = self.run_attempts()
        self.assertIn("listener-listen /userSubscriptions (desktop/", stdout)
        self.assertIn("rest-read /userSubscriptions", stdout)
        self.assertNotIn("(native/", stdout)
        self.assertNotIn("198.51.100.9", stdout)

    def test_the_planner_never_logs_them(self):
        _, out = self.run_attempts()
        tmp = out.parent
        (tmp / "rows.json").write_text(json.dumps({"results": []}))
        (tmp / "nets.json").write_text(json.dumps({"198.51.100.9": {"asn": "64500", "owner": "EXAMPLE"}}))
        r = subprocess.run([NODE, str(PLANNER), str(out), str(tmp / "rows.json"), str(tmp / "nets.json")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        p = json.loads(r.stdout)
        self.assertEqual((p["creates"], p["updates"], p["alerts"], p["allowed_flags"]), ([], [], [], []))
        self.assertEqual(p["summary"]["node_reads_not_logged"], 2)
        self.assertEqual(p["summary"]["allowed_logged"], 0)


def load_networks():
    sys.path.insert(0, str(NETWORKS.parent))
    try:
        spec = importlib.util.spec_from_file_location("check_networks_under_test", NETWORKS)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(NETWORKS.parent))


ROSE, YAWAR, EVE = "<redacted-email>", "<redacted-email>", "eve@example.com"
ALLOW = {ROSE, YAWAR}


def net_row(status, accounts=(), city="Vancouver", rid="row-1"):
    return {"id": rid, "network": "AS64500 EXAMPLE-ISP", "asn": "64500", "cc": "CA", "city": city,
            "status": status, "who": "Rose", "accounts": set(accounts)}


class NetworkCheck(unittest.TestCase):
    """The four console-account branches, run through main() with Notion, whois and the audit log stubbed."""

    ROW = {"t": "2026-10-27T16:59:31.000Z", "node": "/userSubscriptions", "op": "rest-read", "verdict": "Allowed",
           "kind": "Whole real node via REST", "tag": "rule exists", "client": "desktop/os_x browser",
           "ua": MAC, "ip": "198.51.100.9", "bytes": 2048}

    GEO = {"country_code": "CA", "country": "Canada", "region": "British Columbia", "city": "Vancouver",
           "isp": "Example ISP Inc.", "asn": "64500", "reason": "ok"}
    COLUMNS = {"Network", "Country", "City", "Region", "ISP", "Who", "Console accounts", "Status",
               "First seen", "Last seen", "Notes"}

    WHOIS = ("64500", "CA", "EXAMPLE-ISP")

    def run_check(self, rows, audit, *, row=None, write_fails=False, detail=None, columns=None,
                  token_fn=None, sleep=None, now=None):
        mod = load_networks()
        tmp = Path(tempfile.mkdtemp())
        self.verdicts_path = tmp / "v.json"
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "d.jsonl").write_text("".join(json.dumps(r) + "\n" for r in (detail or [row or self.ROW])))
        self.writes = []
        self.mod = mod

        def notion(path, *, data=None, method=None):
            if write_fails:
                raise RuntimeError("HTTP 403")
            self.writes.append((method or "POST", path, data))
            return {"id": f"new-{len(self.writes)}"}

        def known():
            if self.KNOWN_ERROR:
                raise RuntimeError(self.KNOWN_ERROR)
            mod.COLUMNS.clear()
            mod.COLUMNS.update(self.COLUMNS if columns is None else columns)
            return rows, ALLOW

        buf = io.StringIO()
        with mock.patch.object(mod, "known", known), \
                mock.patch.object(mod.ip_location, "lookup", lambda ip: dict(self.GEO)), \
                mock.patch.object(mod, "_notion", notion), \
                mock.patch.object(mod, "cymru", self.cymru), \
                mock.patch.object(mod.time, "sleep", sleep or (lambda s: None)), \
                mock.patch.object(mod.time, "time", (lambda: now) if now is not None else mod.time.time), \
                mock.patch.object(mod, "console_accounts", audit), \
                mock.patch.object(mod, "token_calls", token_fn or (lambda t0, t1: list(self.TOKENS))), \
                mock.patch.object(sys, "argv", ["check_networks.py", "--detail", str(tmp / "d.jsonl"),
                                                "--verdicts", str(self.verdicts_path)]), \
                redirect_stdout(buf):
            mod.main()
        return [json.loads(l) for l in buf.getvalue().splitlines()]

    TOKENS: list = []
    KNOWN_ERROR = None

    def cymru(self, ip):
        return self.WHOIS

    @staticmethod
    def by(who):
        return lambda t0, t1: [(1793120371.0, who)]

    def test_1_allow_listed_account_on_its_row_is_a_quiet_note_whatever_the_status(self):
        for status in ("Confirmed", "Unconfirmed"):
            (f,) = self.run_check([net_row(status, [ROSE])], self.by(ROSE))
            self.assertEqual(f["level"], "note")
            self.assertIn("known: rose@, Vancouver", f["text"])
            (w,) = self.writes
            self.assertEqual((w[0], w[1]), ("PATCH", "/pages/row-1"))
            props = w[2]["properties"]
            self.assertEqual(props["Last seen"], {"date": {"start": "2026-10-27"}})
            # The row already has City and Country; only the blank Region and ISP are filled.
            self.assertNotIn("City", props)
            self.assertNotIn("Country", props)
            self.assertEqual(props["Region"]["rich_text"][0]["text"]["content"], "British Columbia")
            self.assertEqual(props["ISP"]["rich_text"][0]["text"]["content"], "Example ISP Inc.")

    def test_2a_allow_listed_account_new_to_a_known_network_is_added_to_its_row(self):
        (f,) = self.run_check([net_row("Likely", [YAWAR])], self.by(ROSE))
        self.assertEqual(f["level"], "note")
        self.assertIn("added rose@ to the AS64500 EXAMPLE-ISP row", f["text"])
        (w,) = self.writes
        self.assertEqual(w[1], "/pages/row-1")
        self.assertEqual(w[2]["properties"]["Console accounts"]["multi_select"], [{"name": ROSE}, {"name": YAWAR}])

    def test_2b_allow_listed_account_on_a_new_network_creates_one_row(self):
        (f,) = self.run_check([], self.by(ROSE))
        self.assertEqual(f["level"], "note")
        self.assertIn("new Known Team Networks row (Likely) for rose@", f["text"])
        (w,) = self.writes
        props = w[2]["properties"]
        self.assertEqual((w[0], w[1]), ("POST", "/pages"))
        self.assertEqual(props["Network"]["title"][0]["text"]["content"], "AS64500")
        self.assertEqual(props["ISP"]["rich_text"][0]["text"]["content"], "Example ISP Inc.")
        self.assertEqual(props["Status"]["select"]["name"], "Likely")
        self.assertEqual(props["Console accounts"]["multi_select"], [{"name": ROSE}])
        self.assertNotIn("Notes", props)
        self.assertNotIn("198.51.100.9", json.dumps(w))

    def vpn(self, rows):
        # 2026-10-28: AS206092 (VPN/hosting) is registered in NL; ipgeolocation puts it in San Jose, US.
        self.GEO = {**self.GEO, "country_code": "US", "region": "California", "city": "San Jose"}
        self.WHOIS = ("64500", "NL", "EXAMPLE-VPN")
        return self.run_check(rows, self.by(ROSE))

    def test_country_comes_from_the_lookup_and_whois_goes_to_notes(self):
        (f,) = self.vpn([])
        (w,) = self.writes
        props = w[2]["properties"]
        self.assertEqual(props["Country"], {"select": {"name": "US"}})
        self.assertEqual(props["City"]["rich_text"][0]["text"]["content"], "San Jose")
        self.assertEqual(props["Notes"]["rich_text"][0]["text"]["content"], "whois country: NL")

    def test_a_row_stored_under_the_whois_country_is_not_duplicated(self):
        row = {**net_row("Likely", [ROSE], city="San Jose"), "cc": "NL", "region": "California"}
        (f,) = self.vpn([row])
        (w,) = self.writes
        self.assertEqual((w[0], w[1]), ("PATCH", "/pages/row-1"))
        self.assertNotIn("Country", w[2]["properties"])

    def test_a_row_under_the_lookup_country_matches_too(self):
        row = {**net_row("Likely", [ROSE], city="San Jose"), "cc": "US"}
        self.vpn([row])
        (w,) = self.writes
        self.assertEqual(w[1], "/pages/row-1")

    def test_no_notes_when_the_countries_agree(self):
        self.run_check([], self.by(ROSE))
        self.assertNotIn("Notes", self.writes[0][2]["properties"])

    def test_lines_and_who_never_carry_a_full_email(self):
        (f,) = self.run_check([], self.by(ROSE))
        self.assertEqual(self.writes[0][2]["properties"]["Who"]["rich_text"][0]["text"]["content"], "rose@")
        row = {**net_row("Confirmed", [YAWAR]), "who": "Rose <<redacted-email>>"}
        (f,) = self.run_check([row], self.by(EVE))
        self.assertNotIn("aurahealth.io", f["text"])
        self.assertNotIn("example.com", f["text"])

    def test_3_unlisted_or_unattributed_account_alerts_and_writes_nothing(self):
        for audit, phrase in ((self.by(EVE), "eve@ is not on the console-account allow-list"),
                              (lambda t0, t1: [], "no console account in the audit log")):
            (f,) = self.run_check([net_row("Confirmed", [ROSE])], audit)
            self.assertEqual(f["level"], "alert")
            self.assertIn(phrase, f["text"])
            self.assertIn("of /userSubscriptions (2048 bytes)", f["text"])
            self.assertEqual(self.writes, [])

    def test_4_not_a_teammate_always_alerts_even_for_an_allow_listed_account(self):
        (f,) = self.run_check([net_row("Not a teammate", [ROSE])], self.by(ROSE))
        self.assertEqual(f["level"], "alert")
        self.assertIn("marked Not a teammate", f["text"])
        self.assertEqual(self.writes, [])

    def test_same_network_in_a_new_city_gets_a_new_row(self):
        # 2026-10-28: AS9541 set by hand to Faisalabad; the lookup said Karachi, Sindh.
        self.GEO = {**self.GEO, "city": "Karachi", "region": "Sindh"}
        row = {**net_row("Likely", [ROSE], city="Faisalabad"), "region": "Punjab", "isp": "Cyber Internet"}
        (f,) = self.run_check([row], self.by(ROSE))
        self.assertEqual(f["level"], "note")
        (w,) = self.writes
        self.assertEqual((w[0], w[1]), ("POST", "/pages"))
        props = w[2]["properties"]
        self.assertEqual(props["City"]["rich_text"][0]["text"]["content"], "Karachi")
        self.assertEqual(props["Region"]["rich_text"][0]["text"]["content"], "Sindh")
        self.assertEqual(props["Console accounts"]["multi_select"], [{"name": ROSE}])

    def test_same_network_same_city_is_no_duplicate(self):
        row = {**net_row("Likely", [ROSE], city="vancouver"), "region": "British Columbia", "isp": ""}
        self.run_check([row], self.by(ROSE))
        (w,) = self.writes
        self.assertEqual((w[0], w[1]), ("PATCH", "/pages/row-1"))

    def test_a_field_a_person_set_is_kept(self):
        row = {**net_row("Likely", [ROSE], city="Vancouver"), "region": "", "isp": "Set by hand"}
        (f,) = self.run_check([row], self.by(ROSE))
        self.assertIn("known: rose@, Vancouver", f["text"])
        props = self.writes[0][2]["properties"]
        self.assertNotIn("ISP", props)
        self.assertNotIn("City", props)
        self.assertEqual(props["Region"]["rich_text"][0]["text"]["content"], "British Columbia")

    def test_an_add_keeps_set_fields_too(self):
        row = {**net_row("Likely", [YAWAR], city="Vancouver"), "isp": "Set by hand"}
        self.run_check([row], self.by(ROSE))
        (w,) = self.writes
        props = w[2]["properties"]
        self.assertEqual(w[1], "/pages/row-1")
        self.assertNotIn("ISP", props)
        self.assertIn("Console accounts", props)

    def test_no_location_falls_back_to_the_network_row(self):
        self.GEO = {**self.GEO, "city": "", "region": "", "isp": "", "reason": "no_key"}
        self.run_check([net_row("Likely", [ROSE], city="Faisalabad")], self.by(ROSE))
        (w,) = self.writes
        self.assertEqual(w[1], "/pages/row-1")

    def test_not_a_teammate_in_another_city_still_alerts(self):
        self.GEO = {**self.GEO, "city": "Karachi"}
        (f,) = self.run_check([net_row("Not a teammate", [], city="Faisalabad")], self.by(ROSE))
        self.assertEqual(f["level"], "alert")
        self.assertEqual(self.writes, [])

    def test_a_blank_field_is_filled(self):
        row = {**net_row("Likely", [ROSE], city=""), "region": "", "isp": ""}
        self.run_check([row], self.by(ROSE))
        props = self.writes[0][2]["properties"]
        self.assertEqual({k: props[k]["rich_text"][0]["text"]["content"] for k in ("City", "Region", "ISP")},
                         {"City": "Vancouver", "Region": "British Columbia", "ISP": "Example ISP Inc."})

    def test_two_addresses_on_one_new_network_make_one_row(self):
        second = {**self.ROW, "ip": "198.51.100.10", "t": "2026-10-27T16:59:40.000Z"}
        fs = self.run_check([], self.by(ROSE), detail=[self.ROW, second])
        self.assertEqual([w[0] for w in self.writes], ["POST", "PATCH"])
        self.assertEqual(self.writes[1][1], "/pages/new-1")
        self.assertEqual([f["level"] for f in fs], ["note", "note"])

    def test_an_added_account_is_not_lost_on_a_second_add(self):
        second = {**self.ROW, "ip": "198.51.100.10", "t": "2026-10-27T16:59:40.000Z"}
        audit = lambda t0, t1: [(1793120371.0, ROSE), (1793120440.0 + 600, YAWAR)]
        self.run_check([net_row("Likely", [])], audit, detail=[self.ROW, {**second, "t": "2026-10-27T17:09:40.000Z"}])
        last = self.writes[-1][2]["properties"]["Console accounts"]["multi_select"]
        self.assertEqual(last, [{"name": ROSE}, {"name": YAWAR}])

    def test_missing_columns_are_not_written(self):
        self.run_check([net_row("Likely", [ROSE])], self.by(ROSE),
                       columns={"Network", "Country", "City", "Last seen"})
        props = self.writes[0][2]["properties"]
        self.assertNotIn("Region", props)
        self.assertNotIn("ISP", props)

    def test_a_failed_write_is_said_and_the_note_still_goes_out(self):
        (f,) = self.run_check([], self.by(ROSE), write_fails=True)
        self.assertIn("Known Team Networks write failed (HTTP 403)", f["text"])

    def test_non_browser_read_alerts_if_any_city_row_is_not_a_teammate(self):
        curl = {**self.ROW, "ua": "curl/8.0", "client": "unknown/unknown curl/8.0"}
        rows = [net_row("Confirmed", city="Vancouver"), net_row("Not a teammate", city="Toronto", rid="row-2")]
        (f,) = self.run_check(rows, self.by(ROSE), row=curl)
        self.assertEqual(f["level"], "alert")
        self.assertIn("Not a teammate", f["text"])

    def test_non_browser_reads_keep_the_status_rule(self):
        curl = {**self.ROW, "ua": "curl/8.0", "client": "unknown/unknown curl/8.0"}
        (f,) = self.run_check([net_row("Confirmed")], self.by(ROSE), row=curl)
        self.assertEqual(f["level"], "note")
        (f,) = self.run_check([], self.by(ROSE), row=curl)
        self.assertEqual(f["level"], "alert")
        self.assertEqual(self.writes, [])

    def test_audit_log_failure_is_said_not_hidden(self):
        def boom(t0, t1):
            raise RuntimeError("audit log read failed as claude@")
        (f,) = self.run_check([net_row("Confirmed", [ROSE])], boom)
        self.assertEqual(f["level"], "alert")
        self.assertIn("account not found (audit log read failed as claude@)", f["text"])

    def test_a_full_audit_page_raises_instead_of_reading_as_no_account(self):
        mod = load_networks()
        entry = {"timestamp": "2026-10-27T16:59:31Z", "protoPayload": {
            "authenticationInfo": {"principalEmail": "x@example.com"},
            "requestMetadata": {"callerSuppliedUserAgent": "FirebaseCLI/15"}}}
        ran = mock.Mock(returncode=0, stdout=json.dumps([entry] * mod.AUDIT_LIMIT))
        with mock.patch.object(mod.gcloud_sa, "run", return_value=ran), self.assertRaises(RuntimeError) as ctx:
            mod.console_accounts("2026-10-27T16:59:00Z", "2026-10-27T17:00:00Z")
        self.assertIn("cap", str(ctx.exception))

    def verdicts(self):
        return json.loads(self.verdicts_path.read_text())

    def v3(self):
        """The three pre-ENG-1715 verdicts; `alert`, `fail`, `rest_teammate` and `fail_reason` have their own tests."""
        return {k: v for k, v in self.verdicts().items() if k not in ("alert", "fail", "rest_teammate", "fail_reason", "confirmed")}

    def test_verdicts_name_a_known_teammate_on_a_confirmed_or_likely_row(self):
        for rows in ([net_row("Confirmed", [ROSE])], [net_row("Likely", [YAWAR])], []):
            self.run_check(rows, self.by(ROSE))
            self.assertEqual(self.v3(), {"attributed": ["198.51.100.9"], "console": ["198.51.100.9"], "teammate": []})
            self.assertEqual(stat.S_IMODE(os.stat(self.verdicts_path).st_mode), 0o600)

    def test_verdicts_never_attribute_an_unconfirmed_row_or_an_unlisted_account(self):
        for rows, who in (([net_row("Unconfirmed", [ROSE])], ROSE), ([net_row("Confirmed", [ROSE])], EVE),
                          ([net_row("Not a teammate", [ROSE])], ROSE)):
            self.run_check(rows, self.by(who))
            self.assertEqual(self.v3(), {"attributed": [], "console": ["198.51.100.9"], "teammate": []})

    def test_no_console_account_means_no_console_verdict(self):
        self.run_check([net_row("Confirmed", [ROSE])], lambda t0, t1: [])
        self.assertEqual(self.v3(), {"attributed": [], "console": [], "teammate": []})

    def test_verdicts_are_written_even_with_nothing_to_classify(self):
        self.run_check([], self.by(ROSE), detail=[{**self.ROW, "verdict": "Denied"}])
        self.assertEqual(self.v3(), {"attributed": [], "console": [], "teammate": []})

    # ENG-1715: an unconfirmed read stays in the Incident Log with its address (Jerome, 2026-10-30).
    def test_every_alerted_address_is_in_the_alert_verdict(self):
        for rows, who in (([net_row("Confirmed", [ROSE])], EVE), ([net_row("Not a teammate", [ROSE])], ROSE),
                          ([], None)):
            (f,) = self.run_check(rows, self.by(who) if who else (lambda t0, t1: []))
            self.assertEqual(f["level"], "alert", (rows, who))
            self.assertEqual(self.verdicts()["alert"], ["198.51.100.9"])

    def test_a_non_browser_alert_is_in_the_alert_verdict(self):
        curl = {**self.ROW, "ua": "curl/8.0", "client": "unknown/unknown curl/8.0"}
        (f,) = self.run_check([], lambda t0, t1: [], row=curl)
        self.assertEqual((f["level"], self.verdicts()["alert"]), ("alert", ["198.51.100.9"]))

    def test_notes_are_never_in_the_alert_verdict(self):
        for rows in ([net_row("Confirmed", [ROSE])], [net_row("Likely", [YAWAR])]):
            (f,) = self.run_check(rows, self.by(ROSE))
            self.assertEqual((f["level"], self.verdicts()["alert"]), ("note", []))
        self.run_check([], self.by(ROSE), detail=[{**self.ROW, "verdict": "Denied"}])
        self.assertEqual(self.verdicts()["alert"], [])

    @unittest.skipUnless(NODE, "node not installed")
    def test_the_planner_logs_an_alerted_whole_node_read_with_its_address(self):
        # The planner fed this check's own verdicts file: alerted → a node-read row carrying the address;
        # the same read attributed to an allow-listed teammate → no row.
        read = {**self.ROW, "bytes": 0}

        def plan():
            tmp = self.verdicts_path.parent
            (tmp / "d.jsonl").write_text(json.dumps(read) + "\n")
            (tmp / "r.json").write_text(json.dumps({"results": []}))
            (tmp / "n.json").write_text(json.dumps({"198.51.100.9": {"asn": "64500", "owner": "EXAMPLE-ISP"}}))
            r = subprocess.run([NODE, str(PLANNER), str(tmp / "d.jsonl"), str(tmp / "r.json"), str(tmp / "n.json"),
                                "--verdicts", str(self.verdicts_path)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            return json.loads(r.stdout)

        (f,) = self.run_check([], self.by(EVE), detail=[read])
        self.assertEqual(f["level"], "alert")
        p = plan()
        (c,) = p["creates"]
        props = c["properties"]
        self.assertEqual(props["Group key"]["rich_text"][0]["text"]["content"], "Firebase RTDB|node-read|no-data")
        self.assertIn("198.51.100.9", props["IP addresses"]["rich_text"][0]["text"]["content"])
        self.assertIn("198.51.100.9", "".join(x["text"]["content"] for x in props["What they did"]["rich_text"]))
        self.assertEqual(p["summary"]["node_reads_not_logged"], 0)

        (f,) = self.run_check([net_row("Confirmed", [ROSE])], self.by(ROSE), detail=[read])
        self.assertEqual(f["level"], "note")
        p = plan()
        self.assertEqual((p["creates"], p["updates"]), ([], []))
        self.assertEqual(p["summary"]["node_reads_not_logged"], 1)

    # 2026-10-29 19:23Z: a teammate's repeat /butons test re-posted the networks ALERT (ENG-1698).
    BUTONS = {"t": "2026-10-29T19:23:05.000Z", "node": "/butons", "op": "listener-listen", "verdict": "Allowed",
              "kind": "Guessed name (scanner)", "client": "web/os_x browser", "ua": MAC, "ip": "198.51.100.9",
              "bytes": 0}
    NO_ACCOUNT = staticmethod(lambda t0, t1: [])

    def test_a_confirmed_teammate_0_byte_read_with_no_console_account_is_a_note(self):
        (f,) = self.run_check([net_row("Confirmed")], self.NO_ACCOUNT, row=self.BUTONS)
        self.assertEqual(f["level"], "note")
        self.assertIn("teammate read, Known Team Networks status Confirmed (Rose), no console account", f["text"])
        self.assertEqual(self.v3(), {"attributed": [], "console": [], "teammate": ["198.51.100.9"]})
        self.assertEqual(self.writes, [])

    def test_only_an_address_on_a_confirmed_row_is_in_the_confirmed_verdict(self):
        # ENG-1855: a staff read still logged shows "see Known Team Networks", never the address.
        (f,) = self.run_check([net_row("Confirmed")], self.NO_ACCOUNT, row={**self.BUTONS, "bytes": 512})
        self.assertEqual((f["level"], self.verdicts()["confirmed"]), ("alert", ["198.51.100.9"]))
        for rows in ([net_row("Likely")], [net_row("Confirmed"), net_row("Not a teammate", city="Toronto", rid="row-2")],
                     []):
            self.run_check(rows, self.NO_ACCOUNT, row={**self.BUTONS, "bytes": 512})
            self.assertEqual(self.verdicts()["confirmed"], [], rows)

    # 2026-11-09: whois gave SC for a Nayatel address whose Confirmed row is PK, so a teammate's console
    # reads alerted as Unknown (ENG-1857).
    NAYATEL = {"t": "2026-11-09T21:22:00.000Z", "node": "/messageCenter", "op": "listener-listen",
               "verdict": "Allowed", "kind": "Whole real node", "client": "desktop/os_x browser", "ua": MAC,
               "ip": "198.51.100.9", "bytes": 0}

    def nayatel(self, status, cc="PK", rid="row-1"):
        return {**net_row(status, rid=rid), "network": "AS23674 Nayatel", "asn": "23674", "cc": cc,
                "city": "Islamabad", "who": "Naveed"}

    def test_a_confirmed_row_matches_on_the_asn_whatever_country_whois_gives(self):
        self.WHOIS = ("23674", "SC", "NAYATEL")
        (f,) = self.run_check([self.nayatel("Confirmed")], self.NO_ACCOUNT, row=self.NAYATEL)
        self.assertEqual(f["level"], "note", f)
        self.assertIn("teammate read, Known Team Networks status Confirmed (Naveed)", f["text"])
        self.assertEqual(self.verdicts()["teammate"], ["198.51.100.9"])
        self.assertEqual(self.verdicts()["confirmed"], ["198.51.100.9"])
        self.assertEqual(self.writes, [])
        # A non-browser client reads the row's status the same way.
        (f,) = self.run_check([self.nayatel("Confirmed")], self.NO_ACCOUNT,
                              row={**self.NAYATEL, "ua": "node", "client": "server"})
        self.assertIn("Known Team Networks status Confirmed (Naveed)", f["text"])

    def test_only_a_confirmed_row_matches_on_the_asn_alone(self):
        self.WHOIS = ("23674", "SC", "NAYATEL")
        for rows in ([self.nayatel("Unconfirmed")], [self.nayatel("Likely")],
                     # Not a teammate on the network, in any country, still wins.
                     [self.nayatel("Confirmed"), self.nayatel("Not a teammate", cc="AE", rid="row-2")]):
            (f,) = self.run_check(rows, self.NO_ACCOUNT, row=self.NAYATEL)
            self.assertEqual(f["level"], "alert", (rows, f))
            self.assertEqual((self.verdicts()["teammate"], self.verdicts()["confirmed"]), ([], []), rows)
            # A non-browser read alerts too.
            (f,) = self.run_check(rows, self.NO_ACCOUNT, row={**self.NAYATEL, "ua": "node", "client": "server"})
            self.assertEqual(f["level"], "alert", (rows, f))
        # A Likely row in whois's own country still matches as before.
        (f,) = self.run_check([self.nayatel("Likely", cc="SC")], self.NO_ACCOUNT,
                              row={**self.NAYATEL, "ua": "node", "client": "server"})
        self.assertEqual(f["level"], "note", f)

    def test_an_unknown_network_still_alerts(self):
        (f,) = self.run_check([], self.NO_ACCOUNT, row=self.BUTONS)
        self.assertEqual(f["level"], "alert")
        self.assertEqual(self.verdicts()["teammate"], [])

    def test_likely_bytes_not_a_teammate_or_an_unread_audit_log_still_alert(self):
        def boom(t0, t1):
            raise RuntimeError("audit log read failed as claude@")
        for rows, row, audit in (
                ([net_row("Likely")], self.BUTONS, self.NO_ACCOUNT),
                ([net_row("Confirmed")], {**self.BUTONS, "bytes": 512}, self.NO_ACCOUNT),
                ([net_row("Confirmed"), net_row("Not a teammate", city="Toronto", rid="row-2")], self.BUTONS,
                 self.NO_ACCOUNT),
                ([net_row("Confirmed")], self.BUTONS, boom),
                ([net_row("Confirmed")], self.BUTONS, lambda t0, t1: [(self.mod._epoch(self.BUTONS["t"]), EVE)])):
            (f,) = self.run_check(rows, audit, row=row)
            self.assertEqual(f["level"], "alert", (rows, row, f))
            self.assertEqual(self.verdicts()["teammate"], [])

    # 2026-11-03 21:41Z: an open console tab reconnected from a Likely row (ENG-1776). A reconnect makes no
    # new ListDatabaseInstances, so no console account was in the audit log and it alerted.
    RECONNECT = {"t": "2026-11-03T21:41:12.000Z", "node": "/", "op": "listener-listen", "verdict": "Allowed",
                 "kind": "Root", "client": "desktop/os_x browser", "ua": MAC, "ip": "198.51.100.9", "bytes": 0}

    def test_a_0_byte_root_listen_on_a_row_listing_a_console_account_is_a_probable_reconnect(self):
        for status in ("Likely", "Confirmed"):
            rows = [{**net_row(status, [YAWAR]), "who": "Yawar"}]
            (f,) = self.run_check(rows, self.NO_ACCOUNT, row=self.RECONNECT)
            if status == "Confirmed":
                # A Confirmed network's 0-byte read was already a teammate read; that rule still comes first.
                self.assertIn("teammate read", f["text"])
                continue
            self.assertEqual(f["level"], "note", f)
            self.assertIn("probable console reconnect, Known Team Networks status Likely (Yawar)", f["text"])
            self.assertIn("console account on the row: yawar@; no console account in the audit log", f["text"])
            self.assertNotIn("aurahealth.io", f["text"])
            v = self.verdicts()
            self.assertEqual((v["console"], v["alert"], v["attributed"]), (["198.51.100.9"], [], []))
            self.assertEqual(self.writes, [])

    def test_anything_else_about_a_root_listen_still_alerts(self):
        def boom(t0, t1):
            raise RuntimeError("audit log read failed as claude@")
        likely = [net_row("Likely", [YAWAR])]
        rest = {**self.RECONNECT, "op": "rest-read"}
        for rows, detail, audit in (
                (likely, [{**self.RECONNECT, "bytes": 512}], self.NO_ACCOUNT),
                (likely, [rest], self.NO_ACCOUNT),
                (likely, [self.RECONNECT, rest], self.NO_ACCOUNT),
                (likely, [{**self.RECONNECT, "node": "/users"}], self.NO_ACCOUNT),
                (likely, [{**self.RECONNECT, "client": "web/os_x browser"}], self.NO_ACCOUNT),
                ([net_row("Likely")], [self.RECONNECT], self.NO_ACCOUNT),
                ([net_row("Likely", [EVE])], [self.RECONNECT], self.NO_ACCOUNT),
                ([net_row("Unconfirmed", [YAWAR])], [self.RECONNECT], self.NO_ACCOUNT),
                (likely + [net_row("Not a teammate", city="Toronto", rid="row-2")], [self.RECONNECT], self.NO_ACCOUNT),
                ([], [self.RECONNECT], self.NO_ACCOUNT),
                (likely, [self.RECONNECT], boom)):
            (f,) = self.run_check(rows, audit, detail=detail)
            self.assertEqual(f["level"], "alert", (rows, detail, f))
            self.assertNotIn("probable console reconnect", f["text"])
            self.assertEqual(self.verdicts()["console"], [])

    @unittest.skipUnless(NODE, "node not installed")
    def test_the_planner_counts_a_probable_reconnect_as_the_console_and_logs_nothing(self):
        # The planner fed this check's own verdicts file. Before, the alert verdict was the same read the
        # planner logged as a root attempt.
        def plan():
            tmp = self.verdicts_path.parent
            (tmp / "d.jsonl").write_text(json.dumps(self.RECONNECT) + "\n")
            (tmp / "r.json").write_text(json.dumps({"results": []}))
            (tmp / "n.json").write_text(json.dumps({"198.51.100.9": {"asn": "64500", "owner": "EXAMPLE-ISP"}}))
            r = subprocess.run([NODE, str(PLANNER), str(tmp / "d.jsonl"), str(tmp / "r.json"), str(tmp / "n.json"),
                                "--verdicts", str(self.verdicts_path)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            return json.loads(r.stdout)

        (f,) = self.run_check([net_row("Likely", [YAWAR])], self.NO_ACCOUNT, row=self.RECONNECT)
        self.assertEqual(f["level"], "note")
        p = plan()
        self.assertEqual((p["creates"], p["updates"]), ([], []))
        self.assertEqual(p["summary"]["console_reads"], 1)

    def test_a_non_browser_0_byte_read_from_a_confirmed_network_is_a_teammate_read(self):
        curl = {**self.BUTONS, "ua": "curl/8.0", "client": "unknown/unknown curl/8.0"}
        (f,) = self.run_check([net_row("Confirmed")], self.NO_ACCOUNT, row=curl)
        self.assertEqual(f["level"], "note")
        self.assertEqual(self.verdicts()["teammate"], ["198.51.100.9"])
        self.run_check([net_row("Likely")], self.NO_ACCOUNT, row=curl)
        self.assertEqual(self.verdicts()["teammate"], [])
        # A Likely row listed before the Confirmed one doesn't hide it.
        self.run_check([net_row("Likely", rid="row-0"), net_row("Confirmed")], self.NO_ACCOUNT, row=curl)
        self.assertEqual(self.verdicts()["teammate"], ["198.51.100.9"])

    @unittest.skipUnless(NODE, "node not installed")
    def test_its_zero_byte_repeat_is_never_logged(self):
        # The planner fed the network check's own verdicts file, not a hand-built one.
        self.run_check([net_row("Confirmed")], self.NO_ACCOUNT, row=self.BUTONS)
        tmp = self.verdicts_path.parent
        (tmp / "d.jsonl").write_text(json.dumps(self.BUTONS) + "\n")
        (tmp / "n.json").write_text(json.dumps({"198.51.100.9": {"asn": "64500", "owner": "EXAMPLE-ISP"}}))

        def plan(status):
            rt = lambda s: {"rich_text": [{"plain_text": s}]}
            row = {"id": "page-butons", "properties": {
                "System": {"select": {"name": "Firebase RTDB"}}, "Status": {"select": {"name": status}},
                "Group key": rt("Firebase RTDB|guessed-name|obtained"), "Severity": {"select": {"name": "High"}},
                "Detected (UTC)": {"date": {"start": "2026-10-29T17:02:00.000+00:00"}},
                "Ended (UTC)": {"date": {"start": "2026-10-29T17:02:00.000+00:00"}},
                "IP addresses": rt("198.51.100.9"), "What they did": rt(
                    "17:02Z 198.51.100.9: 1 allowed realtime read of /butons [Mozilla/5.0]"),
                "Found by": rt("eng:monitor-rtdb-probes"), "Requests": {"number": 1}}}
            (tmp / "r.json").write_text(json.dumps({"results": [row]}))
            r = subprocess.run([NODE, str(PLANNER), str(tmp / "d.jsonl"), str(tmp / "r.json"), str(tmp / "n.json"),
                                "--verdicts", str(self.verdicts_path)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            return json.loads(r.stdout)

        # A teammate's 0-byte read from a Confirmed network is never logged, whatever the status of the
        # older row: counted and listed with the attributed reads, like a REST burst from that network (ENG-1810).
        for status in ("Resolved", "Open"):
            p = plan(status)
            self.assertEqual((p["creates"], p["updates"], p["alerts"], p["reopened"]), ([], [], [], []))
            self.assertEqual(p["summary"]["teammate_reads"], 1)
            self.assertEqual([a["ip"] for a in p["attributed"]], ["198.51.100.9"])

    def whois_answers(self, *answers):
        calls = list(answers)
        self.cymru = lambda ip: calls.pop(0)
        self.addCleanup(lambda: self.assertEqual(calls, [], "whois was not called as often as expected"))

    def test_a_whois_failure_is_retried_once(self):
        # 2026-10-28 19:16Z: one transient whois failure FAILed a known teammate's console read.
        self.whois_answers(("?", "?", "?"), self.WHOIS)
        (f,) = self.run_check([net_row("Confirmed", [ROSE])], self.by(ROSE))
        self.assertEqual(f["level"], "note")
        self.assertIn("known: rose@, Vancouver", f["text"])

    def test_whois_failing_twice_falls_back_to_the_ipgeolocation_asn(self):
        self.whois_answers(("?", "?", "?"), ("?", "?", "?"))
        (f,) = self.run_check([net_row("Confirmed", [ROSE])], self.by(ROSE))
        self.assertEqual(f["level"], "note")
        self.assertIn("known: rose@, Vancouver", f["text"])
        self.assertEqual(f["key"], "networks-AS64500-CA-/userSubscriptions")

    def test_the_fallback_names_its_source_on_an_alert(self):
        self.whois_answers(("?", "?", "?"), ("?", "?", "?"))
        (f,) = self.run_check([], self.by(EVE))
        self.assertEqual(f["level"], "alert")
        self.assertIn("AS64500 Example ISP Inc. (whois down, from ipgeolocation), CA", f["text"])

    def nl_row(self, status, accounts=()):
        return {**net_row(status, accounts, city="", rid="row-nl"), "cc": "NL"}

    def test_with_whois_down_a_row_under_the_whois_country_is_not_duplicated(self):
        self.whois_answers(("?", "?", "?"), ("?", "?", "?"))
        (f,) = self.run_check([self.nl_row("Likely", [ROSE])], self.by(ROSE))
        self.assertIn("known: rose@", f["text"])
        (w,) = self.writes
        self.assertEqual((w[0], w[1]), ("PATCH", "/pages/row-nl"))

    def test_with_whois_down_an_add_goes_to_the_row_in_the_lookup_country(self):
        self.whois_answers(("?", "?", "?"), ("?", "?", "?"))
        ca = {**net_row("Likely", [YAWAR], city="", rid="row-ca")}
        (f,) = self.run_check([self.nl_row("Likely", [YAWAR]), ca], self.by(ROSE))
        self.assertIn("added rose@", f["text"])
        (w,) = self.writes
        self.assertEqual(w[1], "/pages/row-ca")

    def test_with_whois_down_not_a_teammate_in_another_country_still_alerts(self):
        for ua in (MAC, "curl/8.0"):
            self.whois_answers(("?", "?", "?"), ("?", "?", "?"))
            (f,) = self.run_check([self.nl_row("Not a teammate")], self.by(ROSE), row={**self.ROW, "ua": ua})
            self.assertEqual(f["level"], "alert")
            self.assertEqual(self.writes, [])

    # ENG-1717: a read check_networks can't classify stays in the Incident Log with its address too.
    def test_an_unclassified_read_is_in_the_fail_verdict(self):
        self.whois_answers(("?", "?", "?"), ("?", "?", "?"))
        self.GEO = {**self.GEO, "asn": "", "country_code": "", "reason": "quota"}
        (f,) = self.run_check([net_row("Confirmed", [ROSE])], self.by(ROSE))
        self.assertEqual(f["level"], "fail")
        self.assertEqual((self.verdicts()["fail"], self.verdicts()["alert"]), (["198.51.100.9"], []))

    def test_a_known_team_networks_failure_puts_every_reader_but_ours_in_fail(self):
        self.KNOWN_ERROR = "HTTP 502"
        ours = {**self.ROW, "ip": "203.0.113.20", "ua": "AuraClaude/eng:x@1 (session s)"}
        other = {**self.ROW, "ip": "203.0.113.21", "ua": "curl/8.0"}
        (f,) = self.run_check([], self.by(ROSE), detail=[self.ROW, ours, other])
        self.assertEqual(f["key"], "networks-notion")
        self.assertEqual(self.verdicts()["fail"], ["198.51.100.9", "203.0.113.21"])

    def test_a_classified_read_is_never_in_fail(self):
        self.run_check([net_row("Confirmed", [ROSE])], self.by(ROSE))
        self.assertEqual(self.verdicts()["fail"], [])

    @unittest.skipUnless(NODE, "node not installed")
    def test_the_planner_logs_an_unclassified_whole_node_read_with_its_address(self):
        # Fed the check's own verdicts file after both lookups failed: a node-read row carrying the address.
        self.whois_answers(("?", "?", "?"), ("?", "?", "?"))
        self.GEO = {**self.GEO, "asn": "", "country_code": "", "reason": "quota"}
        read = {**self.ROW, "bytes": 0}
        (f,) = self.run_check([], self.by(EVE), detail=[read])
        self.assertEqual(f["level"], "fail")
        tmp = self.verdicts_path.parent
        (tmp / "d.jsonl").write_text(json.dumps(read) + "\n")
        (tmp / "r.json").write_text(json.dumps({"results": []}))
        (tmp / "n.json").write_text(json.dumps({}))
        r = subprocess.run([NODE, str(PLANNER), str(tmp / "d.jsonl"), str(tmp / "r.json"), str(tmp / "n.json"),
                            "--verdicts", str(self.verdicts_path)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        (c,) = json.loads(r.stdout)["creates"]
        self.assertEqual(c["properties"]["Group key"]["rich_text"][0]["text"]["content"],
                         "Firebase RTDB|node-read|no-data")
        self.assertIn("198.51.100.9", c["properties"]["IP addresses"]["rich_text"][0]["text"]["content"])

    def test_both_lookups_failing_is_a_fail(self):
        self.whois_answers(("?", "?", "?"), ("?", "?", "?"))
        self.GEO = {**self.GEO, "asn": "", "country_code": "", "reason": "quota"}
        (f,) = self.run_check([net_row("Confirmed", [ROSE])], self.by(ROSE))
        self.assertEqual(f["level"], "fail")
        self.assertIn("network lookup failed (whois twice, ipgeolocation quota)", f["text"])
        self.assertEqual(self.writes, [])

    # ENG-1706, AS153596: the console's data viewer fetches a token (SettingsService.GetFirebaseTokens,
    # Cloud Audit Log, same address), then opens whole-node listeners, with no ListDatabaseInstances call.
    LISTEN = {**ROW, "op": "listener-listen", "bytes": 0, "t": "2026-10-27T16:59:33.000Z"}
    TOKEN_AT = 1793120371.0  # 2026-10-27T16:59:31Z, 2 s before the listener

    def test_a_listener_after_a_token_call_from_the_same_address_is_the_console(self):
        self.TOKENS = [(self.TOKEN_AT, self.ROW["ip"])]
        (f,) = self.run_check([], lambda t0, t1: [], row=self.LISTEN)
        self.assertEqual(f["level"], "note")
        self.assertIn("console data viewer", f["text"])
        self.assertNotIn("@", f["text"])
        self.assertNotIn(self.ROW["ip"], f["text"])
        self.assertEqual(json.loads(self.verdicts_path.read_text())["console"], [self.ROW["ip"]])
        self.assertEqual(self.writes, [])

    def test_without_a_token_call_the_listener_is_unchanged(self):
        # None, another address, 61 min before (past the token's hour), 6 min after (past the 5 min).
        for tokens in ([], [(self.TOKEN_AT, "192.0.2.1")], [(self.TOKEN_AT + 2 - 61 * 60, self.ROW["ip"])],
                       [(self.TOKEN_AT + 2 + 6 * 60, self.ROW["ip"])]):
            self.TOKENS = tokens
            (f,) = self.run_check([], lambda t0, t1: [], row=self.LISTEN)
            self.assertEqual(f["level"], "alert", tokens)
            self.assertNotIn("console data viewer", f["text"])

    def test_a_reconnect_on_a_30_minute_old_token_is_the_console(self):
        # 2026-10-30: a 00:30Z listener's nearest token calls were 00:12:53 and 00:36:36.
        for at in (self.TOKEN_AT + 2 - 30 * 60, self.TOKEN_AT + 1.6):
            self.TOKENS = [(at, self.ROW["ip"])]
            (f,) = self.run_check([], lambda t0, t1: [], row=self.LISTEN)
            self.assertEqual(f["level"], "note", at)

    # ENGBACK-2377, 2026-11-06 14:30Z: a Mac browser read the missing nodes /genders and /guidances (allowed,
    # 0 bytes); the staff console's token fetch came 30 s later. It was logged as a guessed-name incident.
    _GUESS = {**ROW, "op": "listener-listen", "verdict": "Allowed", "kind": "Guessed name (scanner)",
              "client": "desktop/os_x chrome browser", "ua": MAC, "bytes": 0, "tag": "no rule"}
    GUESSED = [{**_GUESS, "node": "/genders", "t": "2026-11-06T14:30:25.000Z"},
               {**_GUESS, "node": "/guidances", "t": "2026-11-06T14:30:26.000Z"}]
    READ_AT = 1793975425.0  # 2026-11-06T14:30:25Z

    def test_a_token_fetch_30_s_after_a_console_shaped_read_is_the_console(self):
        self.TOKENS = [(self.READ_AT + 30, self.ROW["ip"])]
        (f,) = self.run_check([], lambda t0, t1: [], detail=self.GUESSED)
        self.assertEqual(f["level"], "note")
        self.assertIn("console data viewer", f["text"])
        self.assertIn("5 minutes after", f["text"])
        self.assertEqual(json.loads(self.verdicts_path.read_text())["console"], [self.ROW["ip"]])

    def test_a_late_token_needs_the_console_shape(self):
        # Not a desktop browser (curl, or a phone's web view), or a read that returned data: a token fetch
        # after it doesn't make it the console.
        for change in ({"ua": "curl/8.0", "client": "unknown/unknown curl/8.0"}, {"client": "web/os_x browser"},
                       {"bytes": 50000}):
            self.TOKENS = [(self.READ_AT + 30, self.ROW["ip"])]
            detail = [{**r, **change} for r in self.GUESSED]
            (f,) = self.run_check([], lambda t0, t1: [], detail=detail)
            self.assertEqual(f["level"], "alert", change)
            self.assertNotIn("console data viewer", f["text"])

    @unittest.skipUnless(NODE, "node not installed")
    def test_the_planner_logs_no_incident_for_the_14_30_case(self):
        # End to end: the network check's own verdicts file, fed to the incident planner, leaves no row.
        self.TOKENS = [(self.READ_AT + 30, self.ROW["ip"])]
        self.run_check([], lambda t0, t1: [], detail=self.GUESSED)
        tmp = self.verdicts_path.parent
        (tmp / "d.jsonl").write_text("".join(json.dumps(r) + "\n" for r in self.GUESSED))
        (tmp / "n.json").write_text(json.dumps({self.ROW["ip"]: {"asn": "64500", "owner": "EXAMPLE-ISP"}}))
        (tmp / "r.json").write_text(json.dumps({"results": []}))
        r = subprocess.run([NODE, str(PLANNER), str(tmp / "d.jsonl"), str(tmp / "r.json"), str(tmp / "n.json"),
                            "--verdicts", str(self.verdicts_path)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        p = json.loads(r.stdout)
        self.assertEqual((p["creates"], p["updates"], p["alerts"]), ([], [], []))
        self.assertEqual(p["summary"]["console_reads"], 2)

    # 2026-11-09 14:45Z and 16:24Z: the staff console's token call landed a minute or two after the pass read
    # the audit log, so the read was flagged "console read by no console account" and logged.
    def check_late(self, now, tokens):
        reads, slept = [], []

        def token_fn(t0, t1):
            reads.append(t1)
            if len(reads) == 1:
                return []
            if isinstance(tokens, Exception):
                raise tokens
            return list(tokens)
        found = self.run_check([], lambda t0, t1: [], detail=self.GUESSED, token_fn=token_fn,
                               sleep=slept.append, now=now)
        return found, reads, slept

    def test_a_token_call_after_the_pass_read_the_audit_log_is_found_on_the_re_read(self):
        late = [(self.READ_AT + 90, self.ROW["ip"])]
        (f,), reads, slept = self.check_late(now=self.READ_AT + 60, tokens=late)
        self.assertEqual((len(reads), slept), (2, [150]))
        self.assertEqual(f["level"], "note")
        self.assertIn("console data viewer", f["text"])
        self.assertEqual(json.loads(self.verdicts_path.read_text())["console"], [self.ROW["ip"]])

    def test_no_re_read_once_the_window_after_the_read_has_passed(self):
        (f,), reads, slept = self.check_late(now=self.READ_AT + 6 * 60, tokens=[(self.READ_AT + 90, self.ROW["ip"])])
        self.assertEqual((len(reads), slept), (1, []))
        self.assertEqual(f["level"], "alert")

    def test_still_no_token_call_on_the_re_read_stays_an_alert(self):
        (f,), reads, slept = self.check_late(now=self.READ_AT + 60, tokens=[])
        self.assertEqual((len(reads), slept), (2, [150]))
        self.assertEqual(f["level"], "alert")

    def test_a_failed_re_read_keeps_the_first_answer(self):
        (f,), reads, slept = self.check_late(now=self.READ_AT + 60, tokens=RuntimeError("audit read failed"))
        self.assertEqual(len(reads), 2)
        self.assertEqual(f["level"], "alert")
        self.assertNotIn("token-call lookup failed", f["text"])

    def test_a_token_fetch_before_a_console_shaped_read_on_any_node_is_the_console(self):
        self.TOKENS = [(self.READ_AT - 120, self.ROW["ip"])]
        (f,) = self.run_check([], lambda t0, t1: [], detail=self.GUESSED)
        self.assertEqual(f["level"], "note")

    def test_a_token_call_does_not_clear_a_not_a_teammate_network(self):
        self.TOKENS = [(self.TOKEN_AT, self.ROW["ip"])]
        (f,) = self.run_check([net_row("Not a teammate")], lambda t0, t1: [], row=self.LISTEN)
        self.assertEqual(f["level"], "alert")

    def test_token_calls_reads_the_activity_log_and_keeps_only_successful_calls(self):
        mod = load_networks()
        entry = lambda ip, ts, status=None: {"timestamp": ts, "protoPayload": {
            "requestMetadata": {"callerIp": ip, "callerSuppliedUserAgent": MAC},
            "authenticationInfo": {"principalEmail": "rose@example.com"}, "status": status or {}}}
        stdout = json.dumps([entry("198.51.100.9", "2026-10-27T16:59:31Z"),
                             entry("198.51.100.10", "2026-10-27T16:59:32Z", {"code": 7}),
                             {"timestamp": "2026-10-27T16:59:33Z", "protoPayload": {}}])
        with mock.patch.object(mod.gcloud_sa, "run", return_value=mock.Mock(returncode=0, stdout=stdout)) as run:
            got = mod.token_calls("2026-10-27T16:59:33Z", "2026-10-27T17:00:00.400Z")
        self.assertEqual(got, [(1793120371.0, "198.51.100.9")])
        q = run.call_args[0][0][2]
        self.assertIn('protoPayload.serviceName="mobilesdk-pa.googleapis.com"', q)
        self.assertIn("cloudaudit.googleapis.com%2Factivity", q)
        self.assertIn('timestamp>="2026-10-27T15:59:33Z"', q)
        # The window runs to 5 minutes after the last read (the console can fetch its token just after),
        # rounded up a second so a call in the read's own second is inside it.
        self.assertIn('timestamp<="2026-10-27T17:05:01Z"', q)

    def test_only_browser_calls_name_an_account(self):
        mod = load_networks()
        entry = lambda ua, email, ts: {"timestamp": ts, "protoPayload": {
            "authenticationInfo": {"principalEmail": email}, "requestMetadata": {"callerSuppliedUserAgent": ua}}}
        stdout = json.dumps([entry(MAC, "rose@example.com", "2026-10-27T16:59:31Z"),
                             entry("FirebaseCLI/15.28.1", "jerome@example.com", "2026-10-27T17:02:39Z")])
        ran = mock.Mock(returncode=0, stdout=stdout)
        with mock.patch.object(mod.gcloud_sa, "run", return_value=ran) as run:
            got = mod.console_accounts("2026-10-27T16:59:00Z", "2026-10-27T17:00:00Z")
        self.assertEqual([who for _, who in got], ["rose@example.com"])
        self.assertIn('timestamp>="2026-10-27T16:57:00Z"', run.call_args[0][0][2])


if __name__ == "__main__":
    unittest.main()
