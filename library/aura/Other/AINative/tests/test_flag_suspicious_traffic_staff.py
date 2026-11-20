#!/usr/bin/env python3
"""`eng:flag-suspicious-traffic`: our own tooling from a team network is Staff, and no row carries an email.

**The incident (2026-11-13, 11:18 pm Vancouver).** A `Scraping (refused)` row appeared: about 32,000
requests under the CLI's `AuraClaude/...` user agent from an IP on a Confirmed Known Team Networks row,
written `Not staff`, with one user's email address in the Route / path cell
(`/users/email/<address>`). Librarian corrected the row by hand.

**The rules pinned here.**
- Ids and email addresses in a path become `:id` and `:email` before anything is written, so
  `/users/email/:email`, never an address. An older cell is re-templated when its row is updated.
- An IP on a Confirmed Known Team Networks row is staff: group key `Web|<type>|staff|<outcome>`,
  Actor `Staff`, Staff `Staff`, no Category, never joined with outside IPs, never a ban candidate.
- The IP decides, never the user agent: `AuraClaude/...` or `AuraCLI/...` from any other IP stays an
  outside client (`Not staff`). A failed lookup also leaves the IP outside.

The plan is built by `detect.analyse` over synthetic load-balancer rows shaped like the incident,
with the network lookup stubbed at `team_networks.staff_ips`'s seams.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_flag_suspicious_traffic import (  # noqa: E402
    SCHEMA, T0, FakeNotion, Harness, args, blocklist, detect, log, row, rules)
import importlib  # noqa: E402

team_networks = importlib.import_module("team_networks")

TEAM_IP, OUTSIDE_IP = "198.51.100.23", "203.0.113.50"
CLAUDE_UA = "AuraClaude/someone/adhoc:cli-users (session abc12345)"
CLI_UA = "AuraCLI/someone"
EMAIL = "person.name@example.com"
STAFF_SCHEMA = {"properties": {**SCHEMA["properties"], "Staff": {"type": "select", "select": {"options": [
    {"name": "Staff"}, {"name": "Not staff"}]}}}}
KNOWN = {"results": [
    {"properties": {"Network": {"title": [{"plain_text": "AS64500 Team ISP"}]},
                    "Status": {"select": {"name": "Confirmed"}}}},
    {"properties": {"Network": {"title": [{"plain_text": "AS64501 Shared ISP"}]},
                    "Status": {"select": {"name": "Confirmed"}}}},
    {"properties": {"Network": {"title": [{"plain_text": "AS64501 Shared ISP"}]},
                    "Status": {"select": {"name": "Not a teammate"}}}},
    {"properties": {"Network": {"title": [{"plain_text": "AS64502 Maybe"}]},
                    "Status": {"select": {"name": "Likely"}}}}], "has_more": False}
ASN = {TEAM_IP: "64500", OUTSIDE_IP: "64999", "198.51.100.99": "64501", "198.51.100.77": "64502"}


def scrape_rows(ip, ua, n=320):
    """The incident's shape: hundreds of uid lookups and some email lookups, every one refused."""
    uids = [f"/users/U{i:05d}abcdefghijklmnopqrstuv" for i in range(n)]
    emails = [f"/users/email/{EMAIL}", "/users/email/other%40example.org"]
    return [row(ip, p, status=404, host="services.aurahealth.io", ua=ua, minute=i // 60)
            for i, p in enumerate(uids + emails * 7)]


def known_call(method, path, body=None):
    assert (method, path) == ("POST", f"/databases/{team_networks.KNOWN_DB}/query"), (method, path)
    return KNOWN


def lookup(ips):
    staff, _ = team_networks.staff_ips(ips, known_call, asn_of=ASN.get)
    return staff


def plan(rows):
    flagged, ips, own = detect.analyse(rows, args(), T0, T0 + dt.timedelta(hours=1), None, lookup)
    return {"project": "test", "since": detect.iso(T0), "until": detect.iso(T0 + dt.timedelta(hours=1)),
            "requests": len(rows), "ips": ips, "own_traffic": own, "flagged": flagged}


class TemplatePath(unittest.TestCase):
    def test_emails_and_ids_become_placeholders(self):
        for raw, want in [(f"/users/email/{EMAIL}", "/users/email/:email"),
                          ("/users/email/a%40b.io", "/users/email/:email"),
                          ("/users/AbCdEfGhIjKlMnOpQrStUvWxYz12", "/users/:id"),
                          (f"/x?email={EMAIL}&y=1", "/x?email=:email&y=1"),
                          ("/users/session", "/users/session")]:
            self.assertEqual(rules.template_path(raw), want)


class TeamNetworks(unittest.TestCase):
    def test_only_confirmed_asns_without_a_not_a_teammate_row(self):
        self.assertEqual(team_networks.confirmed_asns(known_call), {"64500"})

    def test_lookup_failures_leave_ips_outside_and_are_counted(self):
        staff, stats = team_networks.staff_ips([TEAM_IP, "192.0.2.1"], known_call, asn_of=ASN.get)
        self.assertEqual((staff, stats["lookup_failed"]), ({TEAM_IP}, 1))

    def test_an_unreadable_list_makes_nobody_staff(self):
        def boom(*_):
            raise RuntimeError("502")
        staff, stats = team_networks.staff_ips([TEAM_IP], boom, asn_of=ASN.get)
        self.assertEqual(staff, set())
        self.assertIn("unreadable", stats["error"])


class StaffRows(Harness):
    def setUp(self):
        super().setUp()
        self.notion = FakeNotion(STAFF_SCHEMA)

    def only_row(self):
        (page,) = self.notion.rows()
        return page

    def test_the_incident_is_a_staff_row_with_no_email(self):
        code, _ = self.apply(plan(scrape_rows(TEAM_IP, CLAUDE_UA)))
        self.assertEqual(code, 0)
        page = self.only_row()
        p = page["properties"]
        self.assertEqual(self.notion.text(page, "Group key"), "Web|scraping|staff|refused")
        self.assertEqual(self.notion.text(page, "Actor"), "Staff")
        self.assertEqual(p["Staff"]["select"]["name"], "Staff")
        self.assertNotIn("Category", p)
        self.assertEqual(p["Incident"]["title"][0]["text"]["content"], "Scraping, Known Team Networks (refused)")
        routes = self.notion.text(page, "Route / path")
        self.assertIn("/users/email/:email", routes)
        self.assertIn("/users/:id", routes)
        for page_ in self.notion.pages.values():
            blob = repr(page_)
            self.assertNotIn(EMAIL, blob)
            self.assertNotIn("%40example", blob)

    def test_the_cli_agent_from_a_team_ip_is_staff_too(self):
        self.apply(plan(scrape_rows(TEAM_IP, CLI_UA)))
        self.assertEqual(self.notion.text(self.only_row(), "Group key"), "Web|scraping|staff|refused")

    def test_our_agents_from_an_outside_ip_stay_outside(self):
        for ua in (CLI_UA, CLAUDE_UA):
            with self.subTest(ua=ua):
                self.notion = FakeNotion(STAFF_SCHEMA)
                self.apply(plan(scrape_rows(OUTSIDE_IP, ua)))
                page = self.only_row()
                self.assertEqual(self.notion.text(page, "Group key"), "Web|scraping|refused")
                self.assertEqual(self.notion.text(page, "Actor"), "Unknown")
                self.assertEqual(page["properties"]["Staff"]["select"]["name"], "Not staff")
                self.assertNotIn(EMAIL, self.notion.text(page, "Route / path"))

    def test_staff_and_outside_ips_never_share_a_campaign(self):
        flagged = plan(scrape_rows(TEAM_IP, CLI_UA) + scrape_rows(OUTSIDE_IP, CLI_UA))["flagged"]
        self.assertEqual({f["ip"]: f["staff"] for f in flagged}, {TEAM_IP: True, OUTSIDE_IP: False})
        self.apply(plan(scrape_rows(TEAM_IP, CLI_UA) + scrape_rows(OUTSIDE_IP, CLI_UA)))
        keys = sorted(self.notion.text(p, "Group key") for p in self.notion.rows())
        self.assertEqual(keys, ["Web|scraping|refused", "Web|scraping|staff|refused"])
        out = blocklist.collect(log.open_rows(self.notion, now=T0 + dt.timedelta(hours=2)), call=self.notion)
        self.assertEqual([x["ip"] for x in out["ips"]], [OUTSIDE_IP], "a staff row is never a ban candidate")

    def test_an_older_cell_with_an_email_is_retemplated_on_update(self):
        self.notion.pages["b" * 32] = {"id": "b" * 32, "parent": {"database_id": "db"}, "archived": False,
                                       "children": [], "properties": {
            "Group key": log.rich("Web|scraping|refused"), "Severity": {"select": {"name": "Info"}},
            "Ended (UTC)": {"date": {"start": "2026-10-23T09:00:00Z"}}, "Requests": {"number": 5},
            "IP addresses": log.rich(""), "User agent": log.rich(""), "Notes": log.rich(""),
            "What they did": log.rich(""),
            "Route / path": log.rich(f"5x GET services.aurahealth.io/users/email/{EMAIL} -> 404")}}
        self.apply(plan(scrape_rows(OUTSIDE_IP, CLI_UA)))
        routes = self.notion.text(self.notion.pages["b" * 32], "Route / path")
        self.assertNotIn(EMAIL, routes)
        self.assertIn("/users/email/:email", routes)

    def test_without_a_lookup_nobody_is_staff(self):
        flagged, *_ = detect.analyse(scrape_rows(TEAM_IP, CLAUDE_UA), args(), T0, T0 + dt.timedelta(hours=1))
        self.assertEqual([f["staff"] for f in flagged], [False])


if __name__ == "__main__":
    unittest.main()
