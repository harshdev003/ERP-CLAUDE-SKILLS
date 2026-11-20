"""flag-suspicious-traffic reads the Cloud Armor fields of the LB logs (ENGBACK-1917).

Payloads are in the shape prod writes (2026-10-30 sample): priorities come back as floats, a
VERBOSE preview deny carries matchedFieldValue/matchedFieldName, and rule 4000's rate-limit key
is the client address. None of those may reach the plan, the report or Slack.
"""
from __future__ import annotations

import datetime as dt
import importlib
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "plugins/eng/skills/flag-suspicious-traffic/scripts"
for _name in ("armor", "probe_rules", "incident_log", "apply", "detect", "slack_post"):
    sys.modules.pop(_name, None)
sys.path.insert(0, str(SCRIPTS))
armor = importlib.import_module("armor")
detect = importlib.import_module("detect")
apply_ = importlib.import_module("apply")

APP_IP, BROWSER_IP, BANNED = "203.0.113.7", "198.51.100.9", "192.0.2.44"
SECRET = "session=eyJhbGciOiJIUzI1NiJ9.secret"
BROWSER = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
APP = "Aura/3.62 CFNetwork/1568 Darwin/24.0"


def payload(enforced=None, preview=None, status="response_sent_by_backend"):
    p = {"@type": "type.googleapis.com/google.cloud.loadbalancing.type.LoadBalancerLogEntry",
         "statusDetails": status,
         "enforcedSecurityPolicy": enforced or {"name": "aura-web-edge", "outcome": "ACCEPT",
                                                "priority": 2147483647.0, "configuredAction": "ALLOW"}}
    if preview:
        p["previewSecurityPolicy"] = preview
    return p


def sqli_preview():
    return {"name": "aura-web-edge", "outcome": "DENY", "priority": 1000.0, "configuredAction": "DENY",
            "preview": True, "preconfiguredExprIds": ["owasp-crs-v042200-id942290-sqli"],
            "matchedFieldType": "COOKIE_VALUES", "matchedFieldName": "session", "matchedFieldValue": SECRET,
            "matchedFieldLength": 40, "matchedLength": 12, "matchedOffset": 3}


def rate(outcome, priority=4000.0, key=BANNED, enforced=True):
    sp = {"name": "aura-web-edge", "outcome": "DENY" if "BAN" in outcome else "ACCEPT", "priority": priority,
          "configuredAction": "RATE_BASED_BAN", "rateLimitAction": {"key": key, "outcome": outcome}}
    if not enforced:
        sp["preview"] = True
    return sp


def deny(priority=200.0):
    return {"name": "aura-web-edge", "outcome": "DENY", "priority": priority, "configuredAction": "DENY"}


def entry(p, ip=BROWSER_IP, ua=BROWSER, path="/users/AbCdEf1234567890xyz/settings",
          backend="aura-web-proxy-web-80-1a2b3c4d"):
    return {"ip": ip, "ua": ua, "path": path, "backend": backend, "armor": armor.fields(p)}


class Fields(unittest.TestCase):
    def test_keeps_the_safe_fields_only(self):
        f = armor.fields(payload(preview=sqli_preview()))
        self.assertEqual(f["preview"]["priority"], 1000)
        self.assertEqual(f["preview"]["signature"], "owasp-crs-v042200-id942290-sqli")
        self.assertEqual(f["preview"]["field"], "COOKIE_VALUES")
        self.assertEqual(f["enforced"]["priority"], 2147483647)
        self.assertNotIn(SECRET, json.dumps(f))
        self.assertNotIn("session", json.dumps(f))

    def test_a_rate_key_is_hashed(self):
        f = armor.fields(payload(enforced=rate("RATE_LIMIT_THRESHOLD_EXCEED")))
        self.assertNotIn(BANNED, json.dumps(f))
        self.assertEqual(len(f["enforced"]["rate_key"]), 12)

    def test_edge(self):
        self.assertEqual(armor.edge(armor.fields(payload(deny(), status="denied_by_security_policy"))), "blocked")
        self.assertEqual(armor.edge(armor.fields(payload(preview=sqli_preview()))), "reached")
        self.assertEqual(armor.edge(armor.fields(payload(status="client_disconnected_before_any_response"))), "other")
        self.assertEqual(armor.edge(armor.fields({})), "other")

    def test_masked_path(self):
        self.assertEqual(armor.mask_path("/users/AbCdEf1234567890xyz/settings?x=1"), "/users/:x")
        self.assertEqual(armor.backend_name("aura-web-proxy-web-80-1a2b3c4d"), "proxy-web")


class Summary(unittest.TestCase):
    def test_denies_by_rule_and_signature(self):
        s = armor.summarise([entry(payload(preview=sqli_preview()))] * 3
                            + [entry(payload(deny(), status="denied_by_security_policy"))])
        (pv,) = s["denies"]["preview"]
        self.assertEqual((pv["priority"], pv["signature"], pv["field"], pv["backend"], pv["path"], pv["n"]),
                         (1000, "owasp-crs-v042200-id942290-sqli", "COOKIE_VALUES", "proxy-web", "/users/:x", 3))
        self.assertEqual(s["denies"]["enforced"][0]["priority"], 200)
        self.assertEqual(s["deny_totals"], {"enforced": 1, "preview": 3})
        self.assertIn("  preview denies: 3 (top by", "\n".join(armor.report(s, 4)))

    def test_enforced_bans_and_saturated_throttles_alert_preview_does_not(self):
        es = ([entry(payload(enforced=rate("BAN_THRESHOLD_EXCEED", key=f"192.0.2.{i}"))) for i in range(3)]
              + [entry(payload(enforced=rate("RATE_LIMIT_THRESHOLD_EXCEED", 3100.0, key="t13d1516h2_8daaf6152771")))] * 2
              + [entry(payload(preview=rate("RATE_LIMIT_THRESHOLD_EXCEED", enforced=False)))] * 50)
        s = armor.summarise(es)
        # Per-client rules: one issue per client (rule + hashed key), so the three banned IPs are
        # three issues and the one JA4 fingerprint is one.
        self.assertEqual([(a["type"], a["priority"], a["requests"], a["shared"]) for a in s["alerts"]],
                         [("ban", 4000, 1, False)] * 3 + [("throttle", 3100, 2, False)])
        self.assertEqual(len({a["key"] for a in s["alerts"]}), 4)
        for a in s["alerts"]:
            self.assertNotIn("192.0.2", a["key"])
            self.assertNotIn("t13d1516h2", a["key"])
        self.assertIn({"kind": "preview", "priority": 4000, "outcome": "RATE_LIMIT_THRESHOLD_EXCEED",
                       "backend": "proxy-web", "n": 50, "keys": 1}, s["rate"])

    def test_a_shared_cap_rule_is_one_issue(self):
        es = [entry(payload(enforced=rate("RATE_LIMIT_THRESHOLD_EXCEED", 2910.0, key=None)))] * 4
        (a,) = armor.summarise(es)["alerts"]
        self.assertEqual((a["key"], a["requests"], a["shared"]), ("throttle|2910", 4, True))
        self.assertIn("shared cap", "\n".join(armor.report(armor.summarise(es), 4)))

    def test_own_caller_needs_a_success_elsewhere_not_just_the_user_agent(self):
        es = [entry(payload(preview=sqli_preview()), ip=APP_IP, ua=APP),
              entry(payload(preview=sqli_preview()), ip="203.0.113.8", ua=APP),
              entry(payload(preview=sqli_preview()), ip=BROWSER_IP, ua=BROWSER)]
        (own,) = armor.summarise(es, confirmed_ips={APP_IP})["own"]
        self.assertEqual((own["n"], own["ips"], own["confirmed"]), (2, 2, 1))
        (own,) = armor.summarise(es)["own"]
        self.assertEqual(own["confirmed"], 0)

    def test_incident_rules_join_the_blocklist(self):
        es = ([entry(payload(deny(120.0), status="denied_by_security_policy"))] * 4
              + [entry(payload(deny(121.0), status="denied_by_security_policy"))]
              + [entry(payload(deny(122.0), status="denied_by_security_policy"))])
        bl = {120: {"status": "Active", "expires": "2026-11-06"}, 122: {"status": "Expired"},
              130: {"status": "Active"}}
        rules = {r["priority"]: r for r in armor.summarise(es, blocklist=bl)["incident_rules"]}
        self.assertEqual((rules[120]["n"], rules[120]["note"]), (4, None))
        self.assertEqual(rules[121]["note"], "no Blocklist row for this rule")
        self.assertIn("still denying while its Blocklist row is Expired", rules[122]["note"])
        self.assertEqual(rules[130]["note"], "Active, no hits in this window")

    def test_a_skipped_join_flags_nothing(self):
        es = [entry(payload(deny(121.0), status="denied_by_security_policy"))]
        (r,) = armor.summarise(es, blocklist=None)["incident_rules"]
        self.assertEqual((r["priority"], r["n"], r["row"], r["note"]), (121, 1, None, None))

    def test_coverage_per_backend(self):
        s = armor.summarise([], coverage=[("aura-web-proxy-web-80-1a2b3c4d", True)] * 3
                            + [("aura-web-proxy-web-80-1a2b3c4d", False), ("photos-backend-bucket", False)])
        self.assertEqual(s["coverage"][0], {"backend": "proxy-web", "responses": 4, "with_policy": 3, "share": 0.75})

    def test_report_is_counts_only(self):
        es = ([entry(payload(preview=sqli_preview()), ip=APP_IP, ua=APP)]
              + [entry(payload(enforced=rate("BAN_THRESHOLD_EXCEED")))])
        s = armor.summarise(es, coverage=[("x", True)], confirmed_ips={APP_IP},
                            blocklist={120: {"status": "Active", "incidents": ["https://app.notion.com/p/abc"]}})
        text = "\n".join(armor.report(s, len(es))) + json.dumps(s)
        for leak in (APP_IP, BANNED, SECRET, "AbCdEf1234567890xyz"):
            self.assertNotIn(leak, text)
        self.assertIn("ALERT IP ban: rule 4000", text)


class BlocklistRows(unittest.TestCase):
    def test_reads_status_expiry_and_incident_never_the_range(self):
        def prop_rows():
            row = lambda pid, prio, status: {"id": pid, "properties": {
                "Range": {"type": "title", "title": [{"plain_text": "192.0.2.0/24"}]},
                "Reason": {"type": "rich_text", "rich_text": [{"plain_text": "scanner from 192.0.2.10"}]},
                "Rule priority": {"type": "number", "number": prio},
                "Status": {"type": "select", "select": {"name": status}},
                "Expires (UTC)": {"type": "date", "date": {"start": "2026-11-06"}},
                "Incident": {"type": "relation", "relation": [{"id": "abc-def"}]}}}
            return {"results": [row("p-1", 120, "Expired"), row("p-2", 120, "Active"), row("p-3", None, "Active")],
                    "has_more": False}
        calls = []
        out = armor.blocklist_rows(lambda m, path, body: calls.append((m, path, body)) or prop_rows())
        self.assertEqual(calls[0][1], f"/databases/{armor.BLOCKLIST_DB}/query")
        self.assertEqual(calls[0][2]["filter"], {"property": "Policy", "select": {"equals": "aura-web-edge"}})
        self.assertEqual(out[120]["status"], "Active")
        self.assertEqual(out[120]["incidents"], ["https://app.notion.com/p/abcdef"])
        self.assertNotIn("192.0.2", json.dumps(out))


T0 = dt.datetime(2026, 10, 30, 18, 0, tzinfo=dt.timezone.utc)


def lb_row(ip, i, p):
    return {"ts": T0 + dt.timedelta(seconds=i), "ip": ip, "ua": BROWSER, "method": "GET",
            "host": "www.aurahealth.io", "path": f"/app{i}/.env", "query": "", "status": 403, "size": 0,
            "backend": "", "latency": 0.01, "bytes": 0, "id": str(i), "armor": armor.fields(p)}


class CampaignEdge(unittest.TestCase):
    def test_blocked_vs_reached_per_campaign(self):
        rows = ([lb_row("203.0.113.5", i, payload(deny(), status="denied_by_security_policy")) for i in range(30)]
                + [lb_row("203.0.113.5", 30 + i, payload()) for i in range(10)]
                + [lb_row("203.0.113.6", i, payload(deny(), status="denied_by_security_policy")) for i in range(5)])
        a = detect.build_parser().parse_args([])
        flagged, _ips, _own = detect.analyse(rows, a, T0, T0 + dt.timedelta(hours=1))
        by_ip = {f["ip"]: f for f in flagged}
        f = by_ip["203.0.113.5"]
        self.assertEqual(f["edge"], {"blocked": 30, "reached": 10})
        self.assertEqual(f["edge_rules"], {"200": 30})
        self.assertEqual(detect.edge_line(f), "edge: 30 blocked by Cloud Armor (rules 200:30), 10 reached the backend")
        self.assertEqual(by_ip["203.0.113.6"]["edge"], {"blocked": 5})

    def test_a_merged_campaign_sums_its_members(self):
        rows = [lb_row(ip, i, payload(deny(), status="denied_by_security_policy") if i % 2 else payload())
                for ip in ("203.0.113.5", "203.0.113.6") for i in range(20)]
        (f,) = detect.analyse(rows, detect.build_parser().parse_args([]), T0, T0 + dt.timedelta(hours=1))[0]
        self.assertEqual(f["edge"], {"blocked": 20, "reached": 20})
        self.assertEqual(f["edge_rules"], {"200": 20})

    def test_rows_without_armor_fields_still_analyse(self):
        rows = [dict(lb_row("203.0.113.5", i, payload())) for i in range(60)]
        for r in rows:
            r.pop("armor")
        (f,) = detect.analyse(rows, detect.build_parser().parse_args([]), T0, T0 + dt.timedelta(hours=1))[0]
        self.assertEqual(f["edge"], {})


class FakeSlack:
    def __init__(self, ok=True):
        self.posts, self.ok, self.threads = [], ok, []

    def post(self, text, thread_ts=None):
        self.posts.append(text)
        self.threads.append(thread_ts)
        return {"ok": self.ok, "ts": f"{len(self.posts)}.0", "error": None if self.ok else "boom"}

    def permalink(self, ts):
        return f"https://slack.example/p{ts}"

    def top_level(self):
        return [t for t, th in zip(self.posts, self.threads) if th is None]

    def replies(self):
        return [t for t, th in zip(self.posts, self.threads) if th is not None]


SCHEMA = {"properties": {"Group key": {"type": "rich_text", "rich_text": {}},
                         "Severity": {"type": "select", "select": {"options": [
                             {"name": n} for n in ("Info", "Low", "Medium", "High")]}}}}


def notion(method, path, body=None):
    return SCHEMA


class ArmorAlertsToSlack(unittest.TestCase):
    def plan(self, until="2026-10-30T19:00:00Z"):
        return {"since": "2026-10-30T18:00:00Z", "until": until, "flagged": [],
                "armor": {"alerts": [{"key": "ban|4000", "type": "ban", "priority": 4000, "requests": 12, "keys": 2}]}}

    def run_apply(self, plan, state, slack, *extra):
        tmp = pathlib.Path(state)
        (tmp / "plan.json").write_text(json.dumps(plan))
        out = io.StringIO()
        with redirect_stdout(out):
            try:
                apply_.main([str(tmp / "plan.json"), "--state-dir", str(tmp), *extra], call=notion, slack=slack)
            except SystemExit as e:
                return e.code, out.getvalue()
        return 0, out.getvalue()

    def test_a_repeat_across_the_day_boundary_stays_in_one_thread(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict("os.environ", {}, clear=False):
            os.environ.pop("NOTION_TOKEN", None)
            s = FakeSlack()
            self.run_apply(self.plan(), d, s, "--apply")
            self.assertEqual(len(s.top_level()), 1)
            self.assertIn("IP ban", s.posts[0])
            self.assertNotIn(BANNED, s.posts[0])
            self.run_apply(self.plan("2026-10-30T20:00:00Z"), d, s, "--apply")  # within 6 h: held
            self.assertEqual(len(s.posts), 1)
            self.run_apply(self.plan("2026-10-31T01:00:00Z"), d, s, "--apply")  # next UTC day, 6 h on
            self.assertEqual((len(s.top_level()), len(s.replies())), (1, 1))
            self.assertIn("24 more requests", s.replies()[0])  # both repeats, summed

    def test_a_post_is_saved_before_a_later_alert_fails(self):
        class Flaky(FakeSlack):
            def post(self, text, thread_ts=None):
                if self.posts:
                    raise RuntimeError("socket closed")
                return super().post(text)
        plan = self.plan()
        plan["armor"]["alerts"].append({"key": "throttle|3100", "type": "throttle", "priority": 3100,
                                        "requests": 5, "keys": 1})
        with tempfile.TemporaryDirectory() as d, mock.patch.dict("os.environ", {}, clear=False):
            os.environ.pop("NOTION_TOKEN", None)
            s = Flaky()
            code, _ = self.run_apply(plan, d, s, "--apply")
            self.assertEqual(code, 3)
            saved = json.loads((pathlib.Path(d) / "history.json").read_text())["armor"]
            self.assertEqual(set(saved), {"ban|4000"})

    def test_a_clean_window_still_replies_with_held_counts(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict("os.environ", {}, clear=False):
            os.environ.pop("NOTION_TOKEN", None)
            (pathlib.Path(d) / "history.json").write_text(json.dumps({"armor": {"throttle|2910": {
                "ts": "1.0", "permalink": "p", "type": "throttle", "priority": 2910, "shared": True, "first": 100,
                "started": "2026-10-30T10:00:00Z", "last_alert": "2026-10-30T11:00:00Z",
                "last_reply": "2026-10-30T10:00:00Z",
                "pending": {"requests": 9, "runs": 1, "since": "2026-10-30T10:40:00Z"}}}}))
            plan = {"since": "2026-10-30T18:00:00Z", "until": "2026-10-30T19:00:00Z", "flagged": [], "armor": {}}
            s = FakeSlack()
            code, _ = self.run_apply(plan, d, s, "--apply")
            self.assertEqual((code, s.threads), (0, ["1.0"]))
            self.assertIn("9 more requests", s.posts[0])
            saved = json.loads((pathlib.Path(d) / "history.json").read_text())["armor"]["throttle|2910"]
            self.assertIsNone(saved["pending"])

    def test_dry_run_threshold_and_failure(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict("os.environ", {}, clear=False):
            os.environ.pop("NOTION_TOKEN", None)
            s = FakeSlack()
            _code, out = self.run_apply(self.plan(), d, s)
            self.assertEqual(s.posts, [])
            self.assertIn("cloud armor ban|4000 -> slack post", out)
            self.run_apply(self.plan(), d, s, "--apply", "--slack-min-severity", "high")
            self.assertEqual(s.posts, [])
            code, _ = self.run_apply(self.plan(), d, FakeSlack(ok=False), "--apply")
            self.assertEqual(code, 3)


def at(t):
    return f"2026-11-{t[:2]}T{t[3:]}:00Z"


def run_armor(slack, history, until, alerts, apply=True):
    since = (dt.datetime.fromisoformat(until.replace("Z", "+00:00")) - dt.timedelta(minutes=20)
             ).strftime("%Y-%m-%dT%H:%M:%SZ")
    args = mock.Mock(apply=apply, no_slack=False, slack_min_severity="medium")
    with redirect_stdout(io.StringIO()):
        return apply_.armor_alerts({"since": since, "until": until, "armor": {"alerts": alerts}},
                                   history, slack, args)


def shared(priority, n, what="throttle"):
    return {"key": f"{what}|{priority}", "type": what, "priority": priority, "requests": n, "keys": 0, "shared": True}


def client(priority, n, h, what="throttle"):
    return {"key": f"{what}|{priority}|{h}", "type": what, "priority": priority, "requests": n, "keys": 1,
            "shared": False}


# The 18 throttle messages the skill posted in #alerts-security, 11-11 00:19Z to 11-13 00:30Z
# (read back from the channel 2026-11-13): (time, rule, requests over the limit). Rule 4000 is
# per-IP; which client each was is not in the message (the key is never shown).
REPLAY = [("11T02:56", 2910, 257), ("11T02:56", 3000, 6), ("11T02:56", 3390, 32), ("11T03:18", 3010, 3237),
          ("11T03:18", 3320, 15), ("11T04:20", 4000, 596), ("11T05:18", 3310, 62), ("12T00:18", 2910, 158),
          ("12T00:18", 3000, 603), ("12T00:18", 3390, 82), ("12T01:18", 4000, 75), ("12T02:18", 3310, 27),
          ("12T17:18", 3320, 2), ("13T00:19", 2910, 205), ("13T00:19", 3000, 1500), ("13T00:19", 3310, 1625),
          ("13T00:19", 3390, 44), ("13T00:19", 4000, 2)]


class ArmorThreadsByIssue(unittest.TestCase):
    def test_a_crawler_capping_three_days_is_one_thread_with_replies(self):
        s, h = FakeSlack(), {}
        for day in ("11", "12", "13"):
            run_armor(s, h, at(f"{day}T00:18"), [shared(2910, 200)])
        self.assertEqual((len(s.top_level()), len(s.replies())), (1, 2))
        self.assertTrue(all(th == "1.0" for th in s.threads[1:]))
        self.assertIn("on its shared cap", s.posts[0])
        self.assertNotIn("distinct keys", s.posts[0])

    def test_48_hours_of_quiet_starts_a_new_thread(self):
        s, h = FakeSlack(), {}
        run_armor(s, h, at("11T00:18"), [shared(2910, 200)])
        run_armor(s, h, at("13T00:19"), [shared(2910, 200)])
        self.assertEqual(len(s.top_level()), 2)

    def test_a_thread_restarts_after_14_days_even_when_it_never_went_quiet(self):
        s, h = FakeSlack(), {}
        start = dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc)
        for d in range(16):
            run_armor(s, h, (start + dt.timedelta(days=d)).strftime("%Y-%m-%dT%H:%M:%SZ"), [shared(2910, 100)])
        self.assertEqual(len(s.top_level()), 2)

    def test_two_clients_are_two_threads_and_the_same_client_replies(self):
        s, h = FakeSlack(), {}
        run_armor(s, h, at("11T01:00"), [client(4000, 50, "aaa"), client(4000, 50, "bbb")])
        run_armor(s, h, at("11T08:00"), [client(4000, 40, "aaa")])
        self.assertEqual((len(s.top_level()), len(s.replies())), (2, 1))
        self.assertIn("from one client", s.posts[0])
        for text in s.posts:
            self.assertNotIn("aaa", text)
            self.assertNotIn("bbb", text)

    def test_a_ban_and_a_throttle_on_one_rule_are_separate(self):
        s, h = FakeSlack(), {}
        run_armor(s, h, at("11T01:00"), [shared(3000, 10), shared(3000, 10, "ban")])
        self.assertEqual(len(s.top_level()), 2)

    def test_six_alerts_within_6_hours_make_one_reply_with_summed_counts(self):
        s, h = FakeSlack(), {}
        run_armor(s, h, at("11T00:00"), [shared(2910, 100)])
        for hh in ("01", "02", "03", "04", "05", "06"):
            run_armor(s, h, at(f"11T{hh}:00"), [shared(2910, 10)])
        self.assertEqual((len(s.top_level()), len(s.replies())), (1, 1))
        self.assertIn("60 more requests", s.replies()[0])
        self.assertIn("across 6 run(s)", s.replies()[0])

    def test_held_counts_are_posted_once_the_issue_goes_quiet(self):
        s, h = FakeSlack(), {}
        run_armor(s, h, at("11T00:00"), [shared(2910, 100)])
        run_armor(s, h, at("11T01:00"), [shared(2910, 7)])
        self.assertEqual(len(s.replies()), 0)
        run_armor(s, h, at("11T07:00"), [])  # quiet run, 6 h after the thread opened
        self.assertEqual(len(s.replies()), 1)
        self.assertIn("7 more requests", s.replies()[0])
        run_armor(s, h, at("11T14:00"), [])
        self.assertEqual(len(s.replies()), 1)

    def test_a_10x_repeat_posts_top_level_linking_the_thread_once(self):
        s, h = FakeSlack(), {}
        run_armor(s, h, at("11T00:00"), [shared(3000, 600)])
        run_armor(s, h, at("11T07:00"), [shared(3000, 6000)])
        run_armor(s, h, at("11T14:00"), [shared(3000, 9000)])
        top = s.top_level()
        self.assertEqual(len(top), 2)
        self.assertIn("10x the first alert", top[1])
        self.assertIn("|this thread>", top[1])
        self.assertEqual(len(s.replies()), 2)

    def test_10x_under_5000_requests_is_not_an_escalation(self):
        s, h = FakeSlack(), {}
        run_armor(s, h, at("11T00:00"), [shared(3000, 6)])
        run_armor(s, h, at("11T07:00"), [shared(3000, 4999)])  # 833x, but under the floor
        self.assertEqual((len(s.top_level()), len(s.replies())), (1, 1))

    def test_more_than_3_new_clients_share_one_many_thread(self):
        s, h = FakeSlack(), {}
        five = [client(4000, n, f"k{n}") for n in (50, 40, 30, 20, 10)]
        run_armor(s, h, at("11T00:00"), five)
        top = s.top_level()
        self.assertEqual(len(top), 4)
        self.assertEqual(sum("from one client" in t for t in top), 3)
        many = [t for t in top if "more clients" in t]
        self.assertEqual(len(many), 1)
        self.assertIn("30 requests over the limit from 2 more clients", many[0])  # 20 + 10
        self.assertEqual(set(h["armor"]), {"throttle|4000|k50", "throttle|4000|k40", "throttle|4000|k30",
                                           "throttle|4000|many"})
        for text in s.posts:
            self.assertNotRegex(text, r"\bk\d+\b")
        # Next run, same clients 7 h on: no new threads; the three reply and the overflow replies.
        run_armor(s, h, at("11T07:00"), five)
        self.assertEqual((len(s.top_level()), len(s.replies())), (4, 4))
        self.assertIn("from 2 more clients", [r for r in s.replies() if "more clients" in r][0])

    def test_the_run_log_never_prints_a_client_hash(self):
        s, h = FakeSlack(), {}
        args = mock.Mock(apply=False, no_slack=False, slack_min_severity="medium")
        out = io.StringIO()
        with redirect_stdout(out):
            apply_.armor_alerts({"since": at("11T00:00"), "until": at("11T00:20"), "armor": {"alerts": [
                client(4000, 9, "0123456789ab")]}}, h, s, args)
        self.assertIn("cloud armor throttle|4000|client -> slack post", out.getvalue())
        self.assertNotIn("0123456789ab", out.getvalue())

    def test_a_many_reply_counts_every_client_behind_the_held_requests(self):
        s, h = FakeSlack(), {}
        def wave(tag):  # 5 new clients: 3 get threads, 2 go to the many issue
            return [client(4000, 90 - i, f"{tag}{i}") for i in range(3)] + [
                client(4000, 5, f"x{tag}a"), client(4000, 5, f"x{tag}b")]
        run_armor(s, h, at("11T00:00"), wave("p"))   # opens the many thread (2 clients)
        run_armor(s, h, at("11T02:00"), wave("q"))   # held: 2 more clients
        run_armor(s, h, at("11T04:00"), wave("r"))   # held: 2 more clients
        run_armor(s, h, at("11T07:00"), [])          # quiet: the held counts are replied
        many = [r for r in s.replies() if "more clients" in r]
        self.assertEqual(len(many), 1)
        self.assertIn("20 more requests over the limit from 4 more clients across 2 run(s)", many[0])
        for text in s.posts:
            self.assertNotRegex(text, r"\bx[pqr][ab]\b")

    def test_the_cap_is_per_rule(self):
        s, h = FakeSlack(), {}
        run_armor(s, h, at("11T00:00"), [client(4000, 9, f"a{i}") for i in range(3)]
                  + [client(3100, 9, f"b{i}") for i in range(3)])
        self.assertEqual(len(s.top_level()), 6)
        self.assertFalse(any("more clients" in t for t in s.posts))

    def test_a_failed_post_marks_nothing_sent_and_retries(self):
        s, h = FakeSlack(ok=False), {}
        self.assertEqual(run_armor(s, h, at("11T00:00"), [shared(2910, 100)]), 1)
        self.assertEqual(h["armor"], {})
        s.ok = True
        run_armor(s, h, at("11T00:20"), [shared(2910, 100)])
        self.assertEqual(len(s.top_level()), 2)  # the failed one and the retry
        self.assertIn("throttle|2910", h["armor"])

    def test_a_failed_reply_keeps_the_counts_for_the_next_reply(self):
        s, h = FakeSlack(), {}
        run_armor(s, h, at("11T00:00"), [shared(2910, 100)])
        s.ok = False
        self.assertEqual(run_armor(s, h, at("11T07:00"), [shared(2910, 5)]), 1)
        s.ok = True
        run_armor(s, h, at("11T14:00"), [shared(2910, 3)])
        self.assertIn("8 more requests", s.posts[-1])

    def test_a_per_day_entry_becomes_the_thread(self):
        posted = dt.datetime(2026, 11, 13, 0, 19, tzinfo=dt.timezone.utc).timestamp()
        s, h = FakeSlack(), {"armor": {"throttle|2910": {"day": "2026-11-13", "ts": f"{posted:.6f}"}}}
        run_armor(s, h, at("13T07:00"), [shared(2910, 100)])
        self.assertEqual((s.top_level(), s.threads), ([], [f"{posted:.6f}"]))  # a reply in today's message
        self.assertEqual(h["armor"]["throttle|2910"]["first"], 100)
        run_armor(s, h, at("13T14:00"), [shared(2910, 10000)])
        self.assertIn("100x the first alert in <https://slack.example/p", s.top_level()[0])
        h["armor"]["throttle|2910"]["pending"] = {"requests": 4, "runs": 1, "since": at("13T14:00")}
        run_armor(s, h, at("13T21:00"), [])  # the quiet-issue reply works on a migrated thread
        self.assertIn("4 more requests", s.posts[-1])

    def test_held_counts_go_to_the_old_thread_before_a_new_one_opens(self):
        s, h = FakeSlack(), {}
        run_armor(s, h, at("11T00:00"), [shared(2910, 100)])
        run_armor(s, h, at("11T01:00"), [shared(2910, 9)])  # held
        run_armor(s, h, at("13T05:00"), [shared(2910, 50)])  # 52 h quiet: a new thread
        self.assertEqual(s.threads, [None, "1.0", None])
        self.assertIn("9 more requests", s.posts[1])
        self.assertEqual(h["armor"]["throttle|2910"]["ts"], "3.0")

    def test_a_dry_run_previews_the_held_reply_and_the_new_thread(self):
        s, h = FakeSlack(), {}
        run_armor(s, h, at("11T00:00"), [shared(2910, 100)])
        run_armor(s, h, at("11T01:00"), [shared(2910, 9)])
        args = mock.Mock(apply=False, no_slack=False, slack_min_severity="medium")
        out = io.StringIO()
        with redirect_stdout(out):
            apply_.armor_alerts({"since": at("13T04:40"), "until": at("13T05:00"),
                                 "armor": {"alerts": [shared(2910, 50)]}}, h, s, args)
        self.assertIn("-> slack reply", out.getvalue())
        self.assertIn("-> slack post", out.getvalue())
        self.assertEqual(len(s.posts), 1)

    def test_slack_off_posts_nothing_not_even_held_counts(self):
        s, h = FakeSlack(), {}
        run_armor(s, h, at("11T00:00"), [shared(2910, 100)])
        run_armor(s, h, at("11T01:00"), [shared(2910, 9)])
        args = mock.Mock(apply=True, no_slack=True, slack_min_severity="medium")
        with redirect_stdout(io.StringIO()):
            apply_.armor_alerts({"since": at("11T06:40"), "until": at("11T07:00"), "armor": {"alerts": []}},
                                h, s, args)
        self.assertEqual(len(s.posts), 1)
        self.assertEqual(h["armor"]["throttle|2910"]["pending"]["requests"], 9)

    def test_dry_run_writes_no_state(self):
        s, h = FakeSlack(), {}
        run_armor(s, h, at("11T00:00"), [shared(2910, 100)], apply=False)
        self.assertEqual((s.posts, h["armor"]), ([], {}))

    def replay(self, same_client):
        s, h = FakeSlack(), {}
        by_time = {}
        for t, rule, n in REPLAY:
            a = client(rule, n, "c1" if same_client else f"c{t}") if rule == 4000 else shared(rule, n)
            by_time.setdefault(t, []).append(a)
        tops_at = {}
        for t in sorted(by_time):
            before = len(s.top_level())
            run_armor(s, h, at(t), by_time[t])
            tops_at[t] = s.top_level()[before:]
        return s, tops_at

    def test_replay_of_the_last_48h(self):
        for same_client, threads in ((True, 7), (False, 9)):
            with self.subTest(same_client=same_client):
                s, tops = self.replay(same_client)
                new_threads = [t for t in s.top_level() if "jumped to" not in t]
                escalations = [t for t in s.top_level() if "jumped to" in t]
                # 18 top-level messages before; now one thread per issue (6 shared-cap rules plus
                # rule 4000's clients). Rules 3000 (6 -> 603) and 3310 (62 -> 1625) are over 10x
                # but under the 5,000-request floor, so neither escalates.
                self.assertEqual((len(new_threads), len(escalations)), (threads, 0))
                self.assertEqual(len(tops["13T00:19"]), 0 if same_client else 1)  # the 00:19Z burst of 5


if __name__ == "__main__":
    unittest.main()
