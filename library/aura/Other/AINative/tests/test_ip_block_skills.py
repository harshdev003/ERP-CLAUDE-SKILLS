"""eng:block-ip and eng:review-ip-blocks (ENGBACK-2114).

Both ship armor_blocklist.py, adapted from AuraInfra bin/armor-blocklist.py; the copies are
byte-identical. GCP (gcloud_sa.run) and Notion are stubbed at their one call each, and every loaded module
refuses both by default (`load`), so a test that falls through to a write fails instead of reaching prod
(ENG-1746: a host-date "today" let `extend --apply` call live gcloud). Dates are the scripts' own UTC
`utc_today()`, never the host's. The load-bearing
assertions are the refusals (no incident row, too broad, one of ours, not expired, too far out) and
that no output names an address: addresses belong only in the Incident Log and the Blocklist.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import live_guard  # noqa: E402

live_guard.install()

PLUGINS = pathlib.Path(__file__).resolve().parents[1] / "plugins/eng/skills"
BLOCK = PLUGINS / "block-ip/scripts"
REVIEW = PLUGINS / "review-ip-blocks/scripts"
IP = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b|\b2001:db8\b")
ROW = "40175e23af20815181d3e4fc52e9d6bc"
URL = f"https://app.notion.com/p/Scanner-{ROW}"


def load(directory, name):
    for mod in ("armor_blocklist", "gcloud_sa", "slack_post"):
        sys.modules.pop(mod, None)
    sys.path.insert(0, str(directory))
    try:
        spec = importlib.util.spec_from_file_location(f"{directory.parent.name}_{name}", directory / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        refuse_live(mod)
        return mod
    finally:
        sys.path.remove(str(directory))


def refuse_live(mod):
    """A loaded module's GCP and Notion calls raise unless a test stubs them on purpose. Defaults bound at
    definition (`run=gcloud_sa.run`, `call=ab.notion`) are swapped too, or a call that omits them would
    still reach the real one."""
    def live(*a, **k):
        raise AssertionError("a test reached a live GCP or Notion call (ENG-1746)")
    ab = getattr(mod, "ab", mod)
    real = set()
    for m in {mod, ab}:
        if hasattr(m, "gcloud_sa"):
            real.add(m.gcloud_sa.run)
            m.gcloud_sa.run = live
    for name in ("notion", "mark_rows"):
        if hasattr(ab, name):
            real.add(getattr(ab, name))
            setattr(ab, name, live)
    for m in {mod, ab}:
        for f in vars(m).values():
            if callable(f) and getattr(f, "__defaults__", None):
                f.__defaults__ = tuple(live if d in real else d for d in f.__defaults__)


def done(stdout="", code=0, stderr=""):
    return subprocess.CompletedProcess(["gcloud"], code, stdout, stderr)


class Copies(unittest.TestCase):
    def test_armor_blocklist_is_byte_identical(self):
        self.assertEqual((BLOCK / "armor_blocklist.py").read_bytes(), (REVIEW / "armor_blocklist.py").read_bytes())


class Ranges(unittest.TestCase):
    def setUp(self):
        self.ab = load(BLOCK, "armor_blocklist")

    def refused(self, raw):
        with self.assertRaises(SystemExit) as c:
            self.ab.check_ranges(raw)
        return str(c.exception)

    def test_refusals_name_the_position_never_the_address(self):
        for raw, why in (("8.8.8.8,10.1.2.3", "range #2 is not a public"),
                         ("198.51.0.0/15", "range #1 (/15) is too broad"),
                         ("2001:db8::/47", "range #1"),
                         ("nope", "range #1 is not an address")):
            msg = self.refused(raw)
            self.assertIn(why, msg)
            self.assertIsNone(IP.search(msg), msg)
        self.assertIn("1 to 10 ranges", self.refused([f"8.8.{i}.0/24" for i in range(11)]))

    def test_public_ranges_pass(self):
        self.assertEqual(self.ab.check_ranges("8.8.8.8, 1.1.1.0/24"), ["8.8.8.8/32", "1.1.1.0/24"])

    def test_plan_add_refuses_overlap_and_takes_the_first_free_priority(self):
        exp = self.ab.utc_today() + dt.timedelta(days=30)
        live = [{"priority": 100, "ranges": ["1.1.1.0/24"], "expires": "2026-12-01"}]
        with self.assertRaises(SystemExit) as c:
            self.ab.plan_add(["1.1.1.7/32"], exp, ROW, live)
        self.assertIn("range #1 is already blocked by rule 100", str(c.exception))
        self.assertIsNone(IP.search(str(c.exception)))
        self.assertEqual(self.ab.plan_add(["8.8.8.8/32"], exp, ROW, live), (101, f"incident exp={exp} {ROW}"))
        with self.assertRaises(SystemExit):
            self.ab.plan_add(["8.8.8.8/32"], self.ab.utc_today() + dt.timedelta(days=91), ROW, [])
        full = [{"priority": p, "ranges": [], "expires": None} for p in range(100, 150)]
        with self.assertRaises(SystemExit):
            self.ab.plan_add(["8.8.8.8/32"], exp, ROW, full)

    def test_ranges_come_from_the_plan_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"ranges": ["8.8.8.8"]}, f)
        a = self.ab.build_parser().parse_args(["add", "--ranges-file", f.name, "--expires", "2026-12-01",
                                               "--incident", URL, "--reason", "x"])
        self.assertEqual(self.ab.read_ranges(a), ["8.8.8.8/32"])
        os.unlink(f.name)

    def test_an_incident_row_is_required(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SystemExit) as c:
                self.ab.check_incident(ROW)
        self.assertIn("NOTION_TOKEN not set", str(c.exception))
        with mock.patch.dict(os.environ, {"NOTION_TOKEN": "x"}), \
                mock.patch.object(self.ab, "notion", lambda *a: {"parent": {"data_source_id": "other"}}):
            with self.assertRaises(SystemExit) as c:
                self.ab.check_incident(ROW)
        self.assertIn("not a Security Incident Log row", str(c.exception))

    def test_page_id_from_a_peek_link(self):
        self.assertEqual(self.ab.page_id(f"https://www.notion.so/db?v=1&p={ROW}"), ROW)


def incident_page(text):
    return {"parent": {"data_source_id": "40175e23-af20-8197-8d7e-000b96ac48bf"},
            "properties": {"IP addresses": {"rich_text": [{"plain_text": text}]}}}


class FakeNotion:
    def __init__(self, page, team=None):
        self.page, self.team, self.calls = page, team or [], []

    def __call__(self, method, path, body=None):
        self.calls.append((method, path))
        if path.startswith("/pages/"):
            return self.page
        if path.startswith("/databases/"):
            return {"data_sources": [{"id": "known-ds"}]}
        if path == "/data_sources/known-ds/query":
            return {"results": [{"properties": {"Network": {"title": [{"plain_text": f"AS{a} Example"}]},
                                                "Status": {"select": {"name": s}}}} for a, s in self.team],
                    "has_more": False}
        if path.startswith("/blocks/"):
            return {"results": [{"type": "code", "code": {"rich_text": [{"plain_text": json.dumps(
                {"ips": {"8.8.4.4": {"n": 3}}, "uas": {"Mozilla/5.0 (via 9.9.9.9 proxy)": 2}})}]}}], "has_more": False}
        raise AssertionError(path)


def gcloud(lb=(), addresses=("35.1.2.3",), uptime=("34.5.6.7",), fail_logs=False):
    def run(args, **kw):
        if args[:2] == ["compute", "addresses"]:
            return done(json.dumps([{"address": a} for a in addresses]))
        if args[:2] == ["monitoring", "uptime"]:
            return done(json.dumps([{"ipAddress": a} for a in uptime]))
        if args[0] == "logging":
            if fail_logs:
                return done("", 1, "PERMISSION_DENIED")
            return done(json.dumps(list(lb)))
        raise AssertionError(args)
    return run


def lb(ip, status=200, path="/", denied=False):
    return {"httpRequest": {"remoteIp": ip, "status": status, "requestUrl": "https://www.aurahealth.io" + path},
            "jsonPayload": {"statusDetails": "denied_by_security_policy" if denied else "response_sent_by_backend"}}


class Preflight(unittest.TestCase):
    def setUp(self):
        self.pf = load(BLOCK, "preflight")
        self.pf._KNOWN_DS.clear()
        self.env = mock.patch.dict(os.environ, {"NOTION_TOKEN": "x"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def run_pf(self, text, run, team=(), asns=None, ranges=None):
        notion = FakeNotion(incident_page(text), list(team))
        with tempfile.TemporaryDirectory() as d, mock.patch.object(self.pf.ab, "notion", notion):
            plan = pathlib.Path(d) / "plan.json"
            out, err = io.StringIO(), io.StringIO()
            argv = ["--incident", URL, "--plan", str(plan)] + (["--ranges", ranges] if ranges else [])
            with redirect_stdout(out), redirect_stderr(err):
                code = self.pf.main(argv, call=notion, run=run,
                                    asn_lookup=lambda nets: {n: (asns or {}).get(n) for n in nets},
                                    now=dt.datetime(2026, 10, 30, 20, tzinfo=dt.timezone.utc))
            mode = oct(plan.stat().st_mode & 0o777)
            return code, out.getvalue() + err.getvalue(), json.loads(plan.read_text()), mode

    def test_reads_the_row_including_its_overflow_page(self):
        text = ("8.8.8.8 (40 req, last 2026-10-30T10:00Z)\n1.1.1.1 (2 req)\n"
                "… 1 more in Notion page 0123456789abcdef0123456789abcdef")
        code, out, plan, mode = self.run_pf(text, gcloud())
        self.assertEqual(code, 0)
        self.assertEqual(plan["ranges"], ["8.8.8.8/32", "1.1.1.1/32", "8.8.4.4/32"])
        self.assertEqual(mode, "0o600")
        self.assertIsNone(IP.search(out), out)

    def test_an_unreadable_overflow_page_stops_rather_than_blocks_a_partial_list(self):
        notion = FakeNotion(incident_page("8.8.8.8\n… 5 more in Notion page 0123456789abcdef0123456789abcdef"))
        broken = lambda m, path, body=None: ({"results": [{"type": "code", "code": {"rich_text": [
            {"plain_text": "{not json"}]}}], "has_more": False} if path.startswith("/blocks/") else notion(m, path, body))
        with tempfile.TemporaryDirectory() as d, mock.patch.object(self.pf.ab, "notion", broken), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
            code = self.pf.main(["--incident", URL, "--plan", f"{d}/p.json"], call=broken, run=gcloud(),
                                asn_lookup=lambda n: {})
        self.assertEqual(code, 2)
        self.assertIn("overflow page has no readable", err.getvalue())

    def test_spaces_after_commas_are_fine(self):
        code, _out, plan, _ = self.run_pf("", gcloud(), ranges="8.8.8.8, 1.1.1.1")
        self.assertEqual((code, plan["ranges"]), (0, ["8.8.8.8/32", "1.1.1.1/32"]))

    def test_a_capped_read_is_a_warning(self):
        many = [lb("8.8.8.8", 404)] * 3
        with mock.patch.object(self.pf, "traffic", lambda nets, since, run: {n: {"requests": 20000, "4xx": 20000,
                                                                                 "capped": 1} for n in nets}):
            _code, out, _plan, _ = self.run_pf("", gcloud(many), ranges="8.8.8.8")
        self.assertIn("WARN", out)
        self.assertIn("(capped)", out)
        self.assertIn("traffic not fully read", out)

    def test_verdicts(self):
        traffic = [lb("8.8.8.8", 200, "/api/users/me"), lb("1.1.1.1", 200, "/stripe/webhook"),
                   lb("9.9.9.9", 403, "/.env", denied=True)]
        code, out, plan, _ = self.run_pf("", gcloud(traffic, addresses=("35.1.2.3",)), team=[("15169", "Confirmed")],
                                         asns={"8.8.4.4/32": "15169"},
                                         ranges="8.8.8.8,1.1.1.1,9.9.9.9,35.1.2.0/24,8.8.4.4,10.0.0.1")
        levels = re.findall(r"#(\d) /\d+ IPv4  (\w+)", out)
        self.assertEqual(dict(levels), {"1": "WARN", "2": "WARN", "3": "OK", "4": "REFUSE", "5": "WARN"})
        self.assertIn("1 2xx on app routes", out)
        self.assertIn("2xx on webhook routes", out)
        self.assertIn("AS15169 is a Known Team Networks row (Confirmed)", out)
        self.assertIn("our own addresses", out)
        self.assertIn("range #6 is not a public", out)
        self.assertEqual(code, 3)
        self.assertEqual(plan["ranges"], ["8.8.8.8/32", "1.1.1.1/32", "9.9.9.9/32", "8.8.4.4/32"])
        self.assertIsNone(IP.search(out), out)

    def test_not_a_teammate_on_the_network_is_not_ours(self):
        _code, out, _plan, _ = self.run_pf("", gcloud(), team=[("15169", "Confirmed"), ("15169", "Not a teammate")],
                                           asns={"8.8.8.8/32": "15169"}, ranges="8.8.8.8")
        self.assertIn("OK", out)

    def test_an_unread_traffic_window_is_a_warning_not_ok(self):
        _code, out, _plan, _ = self.run_pf("", gcloud(fail_logs=True), ranges="8.8.8.8")
        self.assertIn("WARN", out)
        self.assertIn("traffic not checked", out)

    def test_our_own_addresses_must_be_readable(self):
        def broken(args, **kw):
            return done("", 1, "denied")
        notion = FakeNotion(incident_page("8.8.8.8"))
        with tempfile.TemporaryDirectory() as d, mock.patch.object(self.pf.ab, "notion", notion), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(self.pf.main(["--incident", URL, "--plan", f"{d}/p.json"], call=notion, run=broken,
                                          asn_lookup=lambda n: {}), 2)

    def test_traffic_buckets_by_range_in_chunks(self):
        calls = []

        def run(args, **kw):
            calls.append(args[2])
            return done(json.dumps([lb("8.8.8.8", 200), lb("8.8.8.9", 404), lb("2001:db8::1", 200)]))
        nets = ["8.8.8.0/24"] + [f"1.1.{i}.0/24" for i in range(10)] + ["2001:db8::/48"]
        out = self.pf.traffic(nets, "2026-10-29T00:00:00Z", run)
        self.assertEqual(len(calls), 2)
        self.assertEqual(out["8.8.8.0/24"]["requests"], 2)
        self.assertEqual(out["2001:db8::/48"]["requests"], 1)
        self.assertEqual(out["1.1.0.0/24"], {})
        self.assertIn('ip_in_net(httpRequest.remoteIp, "8.8.8.0/24")', calls[0])


class Confirm(unittest.TestCase):
    def setUp(self):
        self.cf = load(BLOCK, "confirm")

    def test_deny_count_and_exit(self):
        seen = []

        def run(args, **kw):
            seen.append(args[2])
            return done(json.dumps([{"timestamp": "t"}] * 3))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(self.cf.main(["--priority", "101", "--since", "2026-10-30T20:00:00Z"], run=run), 0)
        self.assertIn("jsonPayload.enforcedSecurityPolicy.priority=101", seen[0])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(self.cf.main(["--priority", "101", "--since", "x"], run=lambda a, **k: done("[]")), 10)
            self.assertEqual(self.cf.main(["--priority", "99", "--since", "x"], run=lambda a, **k: done("[]")), 1)

    def test_the_note_keeps_earlier_notes_and_names_no_address(self):
        page = {"properties": {"Notes": {"rich_text": [
            {"plain_text": "first ", "annotations": {"bold": True}},
            {"plain_text": "link", "text": {"content": "link", "link": {"url": "https://x"}}}]}}}
        patched = []

        def call(method, path, body=None):
            if method == "PATCH":
                patched.append(body)
            return page
        text = self.cf.note_text(101, "2026-11-29", 3, "https://app.notion.com/p/abc", today="2026-10-30")
        self.cf.append_note(ROW, text, call)
        runs = patched[0]["properties"]["Notes"]["rich_text"]
        self.assertEqual([r["text"]["content"] for r in runs[:2]], ["first ", "link"])
        self.assertEqual(runs[1]["text"]["link"], {"url": "https://x"})
        self.assertIn("rule 101, expires 2026-11-29, 3 Blocklist row(s)", runs[2]["text"]["content"])
        self.assertIsNone(IP.search(text))
        page["properties"]["Notes"]["rich_text"] = [{"plain_text": "x"}] * 100
        with self.assertRaises(RuntimeError):
            self.cf.append_note(ROW, text, call)


class UtcDates(unittest.TestCase):
    def test_expiry_compares_against_the_utc_date(self):
        for d in (BLOCK, REVIEW):
            src = (d / "armor_blocklist.py").read_text()
            self.assertNotIn("date.today()", src)
        self.assertNotIn("date.today()", (REVIEW / "review.py").read_text())
        self.assertNotIn("date.today()", (BLOCK / "confirm.py").read_text())


class Review(unittest.TestCase):
    def setUp(self):
        self.rv = load(REVIEW, "review")
        self.today = dt.date(2026, 10, 30)

    def rule(self, p, expires, ranges=("8.8.8.0/24",)):
        return {"priority": p, "ranges": list(ranges), "expires": expires, "row": ROW, "preview": False}

    def test_flags_and_proposals(self):
        live = [self.rule(100, "2026-11-20"), self.rule(101, "2026-10-01"), self.rule(102, "2026-10-02"),
                self.rule(103, None)]
        rows = [{"priority": 100, "status": "Active", "policy": "aura-web-edge"},
                {"priority": 101, "status": "Active", "policy": "aura-web-edge"},
                {"priority": 120, "status": "Active", "policy": "aura-web-edge"},
                {"priority": 104, "status": "Expired", "policy": "aura-web-edge"}]
        counts = {101: {"requests": 40, "edge_denied": 40, "4xx": 40, "capped": False},
                  102: {"requests": 0, "edge_denied": 0, "4xx": 0, "capped": False}}
        f = self.rv.review(live, rows, self.today, "since", 30, count=lambda r, s: counts[r["priority"]])
        self.assertEqual(f["rows_without_rule"], [120])
        self.assertEqual(f["rules_without_row"], [102, 103])
        self.assertEqual(f["no_expiry"], [103])
        self.assertEqual([(e["priority"], e["proposal"]) for e in f["expired"]],
                         [(101, "extend to 2026-11-29"), (102, "expire")])
        self.assertTrue(f["needs_decision"])
        text = self.rv.summary(f, 7)
        self.assertIn("Rule 101 expired 2026-10-01: 40 requests in 7d, 40 denied at the edge. Proposal: extend", text)
        self.assertIsNone(IP.search(text))

    def test_a_skipped_join_checks_rules_only_and_says_so(self):
        f = self.rv.review([self.rule(100, "2026-11-20")], None, self.today, "s", 30, count=None)
        self.assertEqual((f["rules_without_row"], f["needs_decision"]), ([], False))
        self.assertIn("Blocklist not read", self.rv.summary(f, 7))

    def test_a_traffic_failure_is_decided_by_hand(self):
        def boom(r, s):
            raise RuntimeError("denied")
        f = self.rv.review([self.rule(101, "2026-10-01")], [], self.today, "s", 30, count=boom)
        self.assertEqual(f["expired"][0]["proposal"], "decide by hand: traffic not read")

    def test_hits_query_covers_every_range(self):
        seen = []
        out = self.rv.hits(self.rule(101, "x", ("8.8.8.0/24", "1.1.1.1/32")), "since",
                           run=lambda a, **k: seen.append(a[2]) or done(json.dumps([lb("8.8.8.8", 403, denied=True)])))
        self.assertIn('ip_in_net(httpRequest.remoteIp, "8.8.8.0/24") OR ip_in_net(httpRequest.remoteIp, "1.1.1.1/32")',
                      seen[0])
        self.assertEqual((out["requests"], out["edge_denied"], out["4xx"]), (1, 1, 1))

    def test_expire_and_extend_refuse_without_cause(self):
        live = [self.rule(101, (self.rv.ab.utc_today() + dt.timedelta(days=5)).isoformat())]
        with mock.patch.object(self.rv.ab, "incident_rules", lambda: live), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as c:
                self.rv.main(["--expire", "101", "--apply"])
            self.assertIn("not an expired incident block", str(c.exception))
            with self.assertRaises(SystemExit):
                self.rv.extend(101, (self.rv.ab.utc_today() + dt.timedelta(days=91)).isoformat(), True)

    def test_a_slack_exit_is_reported_not_raised(self):
        fake = type(sys)("slack_post")
        fake.post_message = lambda *a, **k: sys.exit("SLACK_BOT_TOKEN not set")
        with mock.patch.dict(sys.modules, {"slack_post": fake}), \
                mock.patch.object(self.rv.ab, "incident_rules", lambda: []), \
                mock.patch.object(self.rv.ab, "blocklist_rows", lambda: []), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
            self.assertEqual(self.rv.main(["--post"]), 1)
        self.assertIn("Slack post failed", err.getvalue())

    def test_no_bound_default_reaches_a_live_call(self):
        for directory, name in ((BLOCK, "preflight"), (BLOCK, "confirm"), (REVIEW, "review")):
            mod = load(directory, name)
            for f in vars(mod).values():
                for d in getattr(f, "__defaults__", None) or ():
                    if callable(d) and getattr(d, "__module__", "") in ("gcloud_sa", "armor_blocklist"):
                        self.fail(f"{name}.{f.__name__} still defaults to the real {d.__name__}")
            with self.assertRaises(AssertionError):
                (mod.hits if name == "review" else mod.denies if name == "confirm" else mod.own_addresses)(
                    *({"review": (self.rule(101, "x"), "s"), "confirm": (101, "s")}.get(name, ())))

    def test_extend_dry_run_changes_nothing(self):
        live = [self.rule(101, "2026-10-01")]
        with mock.patch.object(self.rv.ab, "incident_rules", lambda: live), \
                mock.patch.object(self.rv.ab, "gcloud", side_effect=AssertionError("no write")), \
                redirect_stdout(io.StringIO()) as out:
            self.rv.extend(101, (self.rv.ab.utc_today() + dt.timedelta(days=30)).isoformat(), False)
        self.assertIn("dry run", out.getvalue())


class OneRowPerBlock(unittest.TestCase):
    """ENG-1759 (Jerome and Security, 2026-11-03): never one row per IP. A block is one Blocklist row
    listing every range, and each block action is one Security Change Log row naming no range."""

    def setUp(self):
        self.ab = load(BLOCK, "armor_blocklist")
        self.calls = []

        def notion(method, path, body=None):
            self.calls.append((method, path, body))
            if path.endswith("/query"):
                return {"results": self.existing}
            if method == "GET":
                return {"properties": {"Incident": {"type": "title",
                                                    "title": [{"plain_text": "Forced browsing (denied)"}]}}}
            return {"id": "new"}
        self.existing = []
        self.ab.notion = notion

    def writes(self, ds):
        return [b for m, p, b in self.calls if m == "POST" and p == "/pages"
                and b["parent"]["data_source_id"] == ds]

    def test_one_blocklist_row_lists_every_range(self):
        a = type("A", (), {"proposed_by": "Security", "approved_by": "Jerome", "reason": "scan", "card": ""})()
        self.ab.record(101, ["8.8.8.0/24", "1.1.1.1/32", "9.9.9.9/32"], ROW, dt.date(2026, 12, 1), "2026-11-03T05:00:00Z", a)
        (row,) = self.writes(self.ab.BLOCKLIST_DS)
        self.assertEqual(row["properties"]["Range"]["title"][0]["text"]["content"], "8.8.8.0/24, 1.1.1.1/32, 9.9.9.9/32")
        self.assertEqual(row["properties"]["Rule priority"]["number"], 101)

    def test_a_change_log_row_names_no_address_and_links_the_incident(self):
        self.assertTrue(self.ab.change_log("added", 101, ROW, "blocked 3 range(s), expires 2026-12-01", card="PROD-6926",
                                           expires="2026-12-01"))
        (row,) = self.writes(self.ab.CHANGELOG_DS)
        p = row["properties"]
        text_ = json.dumps(p)
        self.assertIsNone(IP.search(text_), text_)
        self.assertEqual(p["Component"]["rich_text"][0]["text"]["content"], "Cloud Armor aura-web-edge")
        self.assertEqual(p["System"]["select"]["name"], "Infrastructure")
        self.assertEqual(p["Env"]["multi_select"], [{"name": "prod"}])
        self.assertIn('rule 101: added, blocked 3 range(s), expires 2026-12-01 for incident "Forced browsing (denied)"',
                      p["Change"]["title"][0]["text"]["content"])
        self.assertEqual(p["PR / release"]["url"], f"https://app.notion.com/p/{ROW}")
        self.assertIn(f"key aura-web-edge/101/added/{ROW}/2026-12-01", p["Time source"]["rich_text"][0]["text"]["content"])

    def test_a_second_extension_is_its_own_row(self):
        keys = []
        query = self.ab.notion

        def notion(method, path, body=None):
            if path.endswith("/query"):
                keys.append(body["filter"]["rich_text"]["contains"])
            return query(method, path, body)
        self.ab.notion = notion
        self.ab.change_log("extended", 101, ROW, "expiry moved to 2026-12-01", expires="2026-12-01")
        self.ab.change_log("extended", 101, ROW, "expiry moved to 2027-01-15", expires="2027-01-15")
        self.assertEqual(len(set(keys)), 2)
        self.assertEqual(len(self.writes(self.ab.CHANGELOG_DS)), 2)

    def test_a_rerun_writes_no_second_change_log_row(self):
        self.existing = [{"id": "already"}]
        self.assertFalse(self.ab.change_log("added", 101, ROW, "blocked 3 range(s)"))
        self.assertEqual(self.writes(self.ab.CHANGELOG_DS), [])

    def test_a_re_add_on_the_same_day_is_its_own_row(self):
        # An early removal frees the priority; the next add can reuse it with the same expiry.
        keys = []
        query = self.ab.notion

        def notion(method, path, body=None):
            if path.endswith("/query"):
                keys.append(body["filter"]["rich_text"]["contains"])
            return query(method, path, body)
        self.ab.notion = notion
        self.ab.change_log("added", 100, ROW, "blocked 1 range(s)", expires="2026-12-01", at="2026-11-03T05:00")
        self.ab.change_log("added", 100, ROW, "blocked 1 range(s)", expires="2026-12-01", at="2026-11-03T09:30")
        self.assertEqual(len(set(keys)), 2)

    def test_two_incidents_sharing_an_id_prefix_get_their_own_rows(self):
        keys = []
        query = self.ab.notion

        def notion(method, path, body=None):
            if path.endswith("/query"):
                keys.append(body["filter"]["rich_text"]["contains"])
            return query(method, path, body)
        self.ab.notion = notion
        other = ROW[:8] + "0" * 24
        self.ab.change_log("extended", 101, ROW, "expiry moved to 2026-12-01", expires="2026-12-01")
        self.ab.change_log("extended", 101, other, "expiry moved to 2026-12-01", expires="2026-12-01")
        self.assertEqual(len(set(keys)), 2)

    def test_a_change_log_failure_is_reported_not_raised(self):
        def down(*a, **k):
            raise RuntimeError("Notion 502")
        self.ab.notion = down
        with redirect_stdout(io.StringIO()) as out:
            self.assertFalse(self.ab.change_log("expired", 101, ROW, "block removed (expired)"))
        self.assertIn("add it by hand", out.getvalue())

    def test_a_blocklist_failure_after_removal_keeps_the_change_log_row(self):
        def broken(prio, props):
            raise RuntimeError("Notion 502")
        with mock.patch.object(self.ab, "gcloud", lambda *a: ""), mock.patch.object(self.ab, "mark_rows", broken), \
                redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError):
            self.ab.remove(101, "Removed", True, ROW, "2026-12-01")
        self.assertEqual(len(self.writes(self.ab.CHANGELOG_DS)), 1)

    def test_removing_and_expiring_write_a_change_log_row(self):
        with mock.patch.object(self.ab, "gcloud", lambda *a: ""), \
                mock.patch.object(self.ab, "mark_rows", lambda prio, props: 1), redirect_stdout(io.StringIO()):
            self.ab.remove(101, "Expired", True, ROW, "2026-11-01")
        (row,) = self.writes(self.ab.CHANGELOG_DS)
        self.assertIn("rule 101: expired, block removed (expired)", row["properties"]["Change"]["title"][0]["text"]["content"])


if __name__ == "__main__":
    unittest.main()
