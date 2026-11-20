#!/usr/bin/env python3
"""`eng:flag-suspicious-traffic`: groups, severity, what reaches Slack, and what is never logged.

#alerts-security got one post an hour that was almost all routine scanning, and our own
LiveKit voice agent was logged as "Other attack". The design these tests pin:

- every campaign gets a Group key `Web|<attack-type>|<outcome>` and a severity: Info
  (same type, nothing obtained), Low (a type or path family unseen for 14 days), Medium
  (brute force, a flood that moved the LB), High (a 2xx on a non-public route, data on a
  sensitive path, an answered sign-in after refused ones);
- one Incident Log row per open group: IPs and user agents are unioned with counts and
  never truncated (overflow goes to a child page), requests count by minute, severity
  only goes up, a group closes after 2 days without hits, and a missing column is a
  refusal (exit 2), never a schema change;
- Slack shows only severities at or above --slack-min-severity (default medium): one
  message per group updated in place, a new post for a new group or a higher severity,
  a thread reply for a new family or a 10x volume jump, and a lost message re-posted
  once;
- the agent's traffic (UA + route) is never flagged, logged or exported;
- blocklist.py exports the ban data read-only.

Plans are built by running `detect.analyse` over synthetic load-balancer rows, so apply
consumes what its real producer emits. Notion and Slack are replaced at their seams
(`apply.main(call=…, slack=…)`); nothing leaves the process.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import importlib
import importlib.util
import io
import json
import os
import pathlib
import sys
import tempfile
import types
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "plugins/eng/skills/flag-suspicious-traffic/scripts"

# Generic module names (apply, detect) may already be cached by another suite.
for _name in ("probe_rules", "incident_log", "apply", "detect", "blocklist", "slack_post"):
    sys.modules.pop(_name, None)
sys.path.insert(0, str(SCRIPTS))
rules = importlib.import_module("probe_rules")
log = importlib.import_module("incident_log")
detect = importlib.import_module("detect")
apply_ = importlib.import_module("apply")
blocklist = importlib.import_module("blocklist")

T0 = dt.datetime(2026, 10, 23, 10, 0, tzinfo=dt.timezone.utc)
AGENT_UA = "AsyncOpenAI/Python 1.109.1"
BROWSER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
FB = "Web|forced-browsing|refused"
SEMI_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


def row(ip, path, status=404, method="GET", host="www.aurahealth.io", ua=BROWSER, minute=0,
        query="", latency=0.01, size=0):
    return {"ts": T0 + dt.timedelta(minutes=minute, seconds=len(path) % 60), "ip": ip, "ua": ua,
            "method": method, "host": host, "path": path, "query": query, "status": status,
            "size": 0, "backend": "", "latency": latency, "bytes": size, "id": None}


def args(**over):
    a = detect.build_parser().parse_args([])
    for k, v in over.items():
        setattr(a, k, v)
    return a


def env_scan(ip, minute=0, n=60, host="www.aurahealth.io", status=404, ua=BROWSER):
    """A routine forced-browsing sweep: .env variants, all refused."""
    return [row(ip, f"/app{i}/.env", status=status, host=host, minute=minute + i // 20, ua=ua)
            for i in range(n)]


def plan_for(rows, since=T0, until=T0 + dt.timedelta(hours=1), sizes=None):
    flagged, ips, own = detect.analyse(rows, args(), since, until, sizes)
    return {"project": "test", "since": detect.iso(since), "until": detect.iso(until),
            "requests": len(rows), "ips": ips, "own_traffic": own, "flagged": flagged}


def at(hours):
    return T0 + dt.timedelta(hours=hours), T0 + dt.timedelta(hours=hours + 1)


SCHEMA = {"properties": {"Group key": {"type": "rich_text", "rich_text": {}},
                         "Severity": {"type": "select", "select": {"options": [
                             {"name": n} for n in ("Info", "Low", "Medium", "High")]}}}}


class FakeNotion:
    """In-memory Incident Log behind `call`, with failure injection."""

    def __init__(self, schema=SCHEMA):
        self.schema, self.pages, self.calls, self.fail_writes = schema, {}, [], False
        self.fail_row_patches = 0  # fail the next N property PATCHes of a row
        self.fail_archive = False

    def __call__(self, method, path, body=None):
        self.calls.append((method, path))
        if method == "GET" and path.startswith("/databases/"):
            return self.schema
        if method == "POST" and path.endswith("/query"):
            return {"results": self._query(body), "has_more": False}
        if method in ("POST", "PATCH") and self.fail_writes:
            raise RuntimeError(f"{method} {path} -> 502: bad gateway")
        if method == "POST" and path == "/pages":
            pid = f"{len(self.pages) + 1:032x}"
            self.pages[pid] = {"id": pid, "parent": body["parent"], "archived": False,
                               "properties": dict(body.get("properties", {})),
                               "children": list(body.get("children", []))}
            return {"id": pid}
        if method == "PATCH" and path.startswith("/pages/"):
            page = self.pages[path.rsplit("/", 1)[1]]
            if self.fail_archive and body.get("archived"):
                raise RuntimeError("PATCH /pages -> 502: bad gateway")
            if self.fail_row_patches and "properties" in body and "database_id" in page["parent"]:
                self.fail_row_patches -= 1
                raise RuntimeError("PATCH /pages -> 504: gateway timeout")
            page["properties"].update(body.get("properties", {}))
            page["archived"] = body.get("archived", page["archived"])
            return {"id": page["id"]}
        if method == "PATCH" and path.startswith("/blocks/"):
            self.pages[path.split("/")[2]]["children"] += body["children"]
            return {}
        if method == "GET" and path.startswith("/blocks/"):
            page = self.pages[path.split("/")[2]]
            return {"results": [{"type": "code", "code": {"rich_text": [
                {"plain_text": x["text"]["content"]} for x in b["code"]["rich_text"]]}}
                for b in page["children"]], "has_more": False}
        raise AssertionError(f"unexpected {method} {path}")

    def rows(self):
        return [p for p in self.pages.values() if "database_id" in p["parent"] and not p["archived"]]

    def read(self, page):
        out = {}
        for k, v in page["properties"].items():
            if "rich_text" in v:
                out[k] = {"rich_text": [{"plain_text": x["text"]["content"]} for x in v["rich_text"]]}
            elif "title" in v:
                out[k] = {"title": [{"plain_text": x["text"]["content"]} for x in v["title"]]}
            else:
                out[k] = v
        return {"id": page["id"], "archived": page["archived"], "properties": out}

    def _query(self, body):
        rows = [self.read(p) for p in self.rows()]
        flt = (body or {}).get("filter", {})
        eq = flt.get("rich_text", {}).get("equals") if flt.get("property") == "Group key" else None
        if eq is not None:
            rows = [r for r in rows if log.plain(r["properties"].get("Group key")) == eq]
        return sorted(rows, key=log.ended, reverse=True)

    def text(self, page, prop):
        return "".join(x["text"]["content"] for x in page["properties"][prop]["rich_text"])

    def sev(self, page):
        return page["properties"]["Severity"]["select"]["name"]


class FakeSlack:
    def __init__(self):
        self.posts, self.threads = [], []
        self.post_error = self.thread_error = None

    def post(self, text, thread_ts=None):
        if thread_ts:
            if self.thread_error:
                return {"ok": False, "error": self.thread_error}
            self.threads.append((thread_ts, text))
            return {"ok": True, "ts": f"t{len(self.threads)}"}
        if self.post_error:
            return {"ok": False, "error": self.post_error}
        self.posts.append(text)
        return {"ok": True, "ts": f"{len(self.posts)}.0"}

    def permalink(self, ts):
        return f"https://slack/p{ts}"


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        self.notion = FakeNotion()
        self.slack = FakeSlack()
        self.env = mock.patch.dict(os.environ, {"NOTION_TOKEN": "x"}, clear=False)
        self.env.start()
        for k in ("FLAG_TRAFFIC_MENTION", "FLAG_TRAFFIC_SLACK_MIN_SEVERITY",
                  "DETECT_ATTACKS_MENTION", "DETECT_ATTACKS_SLACK_MIN_SEVERITY"):
            os.environ.pop(k, None)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def seed(self, *families, kinds=("Forced browsing",)):
        """History in which these families and categories were seen yesterday."""
        last = detect.iso(T0 - dt.timedelta(days=1))
        seen = {f"fam:{f}": last for f in families}
        seen.update({f"cat:{k}": last for k in kinds})
        (self.dir / "history.json").write_text(json.dumps({"seen": seen, "slack": {}}))

    def apply(self, plan, *extra, dry=False):
        path = self.dir / "plan.json"
        path.write_text(json.dumps(plan))
        argv = [str(path), "--state-dir", str(self.dir), *([] if dry else ["--apply"]), *extra]
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            try:
                apply_.main(argv, call=self.notion, slack=self.slack)
                code = 0
            except SystemExit as e:
                code = e.code or 0
        return code, out.getvalue()

    def history(self):
        p = self.dir / "history.json"
        return json.loads(p.read_text()) if p.exists() else {}


class PathFamilies(unittest.TestCase):
    CASES = {
        "/.env": ".env files", "/api/.env.production": ".env files",
        "/wp-login.php": "WordPress", "/xmlrpc.php": "WordPress",
        "/.git/config": "Git / VCS", "/.aws/credentials": "Cloud credentials",
        "/phpmyadmin/index.php": "phpMyAdmin", "/backup.sql": "Backups and dumps",
        "/site.tar.gz": "Backups and dumps", "/config.json": "Config files",
        "/.DS_Store": "Config files", "/actuator/env": "Actuator / API docs",
        "/swagger-ui.html": "Actuator / API docs", "/../../etc/passwd": "Path traversal",
        "/cgi-bin/%252e%252e/%252e%252e/bin/sh": "Path traversal",
        "/remote/login": "VPN and appliance exploits", "/+CSCOE+/logon.html": "VPN and appliance exploits",
        "/mcp": "MCP / AI endpoints", "/sse": "MCP / AI endpoints",
        "/shell.php": "PHP scripts", "/": "Other paths", "/about": "Other paths",
    }

    def test_each_family(self):
        for path, fam in self.CASES.items():
            with self.subTest(path=path):
                self.assertEqual(rules.path_family(path), fam)

    def test_query_payload_and_encoding_land_together(self):
        self.assertEqual(rules.path_family("/index.php", "lang=../../../../tmp/x"), "Path traversal")
        self.assertEqual(rules.path_family("/%2e%2e/%2e%2e/etc/passwd"), "Path traversal")
        self.assertEqual(rules.path_family("/x", "q=${jndi:ldap://a}"), "Injection payloads")

    def test_a_query_does_not_hide_an_end_anchored_family(self):
        self.assertEqual(rules.path_family("/config.json", "v=1"), "Config files")
        self.assertEqual(rules.path_family("/site.tar.gz", "dl=1"), "Backups and dumps")
        self.assertEqual(rules.path_family("/mcp", "session=x"), "MCP / AI endpoints")
        self.assertFalse(rules.is_public("GET", "www.aurahealth.io", "/config.json", "v=1"))

    def test_our_own_routes_are_not_mcp_probes(self):
        self.assertEqual(rules.path_family("/ai/openai/models"), "Other paths")
        self.assertEqual(rules.path_family("/ai/conversations/abc/metrics"), "Other paths")

    def test_route_family_reads_plan_and_state_routes(self):
        self.assertEqual(rules.route_family("GET /.env"), ".env files")
        self.assertEqual(rules.route_family("GET www.aurahealth.io/wp-admin/"), "WordPress")


class PreFilterReadsEverySensitiveFamily(unittest.TestCase):
    """A 2xx is only scored if the server-side pre-filter fetched it. Every path whose family
    can make an outcome obtained or data-accessed must match the URL arm of SUSPECT_LQL."""

    def test_every_sensitive_example_is_fetched(self):
        import re
        url_rx = re.compile(re.search(r'requestUrl=~"\(\?i\)(.*)"\)$', detect.SUSPECT_LQL).group(1), re.I)
        for path, fam in PathFamilies.CASES.items():
            if fam in rules.SENSITIVE_FAMILIES:
                with self.subTest(path=path):
                    self.assertTrue(url_rx.search("https://www.aurahealth.io" + path), fam)
                    self.assertTrue(url_rx.search("https://www.aurahealth.io" + path + "?v=1"),
                                    f"{fam} with a query string")


class PublicRoutes(unittest.TestCase):
    def test_public(self):
        self.assertTrue(rules.is_public("GET", "www.aurahealth.io", "/meditations/sleep"))
        self.assertTrue(rules.is_public("GET", "cdn.example", "/_next/static/chunks/a.js"))
        self.assertTrue(rules.is_public("GET", "34.117.12.99", "/robots.txt"))
        self.assertTrue(rules.is_public("HEAD", "coach.aurahealth.io", "/dashboard"))

    def test_non_public(self):
        self.assertFalse(rules.is_public("GET", "services.aurahealth.io", "/content/list"))
        self.assertFalse(rules.is_public("GET", "services.aurahealth.io", "/static/a.png"))
        self.assertFalse(rules.is_public("POST", "www.aurahealth.io", "/api/contact"))
        self.assertFalse(rules.is_public("DELETE", "www.aurahealth.io", "/x"))
        # A served .env is not a page view even on a web host.
        self.assertFalse(rules.is_public("GET", "www.aurahealth.io", "/.env"))
        self.assertFalse(rules.is_public("GET", "coach.aurahealth.io", "/live/.env"))


class ContentServicePublicRoutes(unittest.TestCase):
    """ContentService GETs with no auth (or only the optional `identify`) answer anyone, so a 2xx
    there is not `obtained`. Read from its routers, not guessed; everything else on the services
    host, and every route registered with `authenticate`, stays non-public."""

    H = "services.aurahealth.io"

    def test_public(self):
        for path in ("/coaches/ready", "/coaches/status", "/channels", "/channels/c1",
                     "/contents", "/contents/list", "/contents/topicPreferencesConfig", "/contents/x1",
                     "/contents/assets/photo/c/x1", "/contents/assets/photo/m/x1/200", "/reels", "/reels/r1",
                     "/reels/assets/photo/c/r1/200", "/assets/coach/photo/c1", "/assets/coach/photo/c1/200"):
            with self.subTest(path):
                self.assertTrue(rules.is_public("GET", self.H, path))
                self.assertTrue(rules.is_public("HEAD", self.H, path))
        self.assertEqual(rules.is_public_service_route("/channels/x"), "ContentService")

    def test_authenticated_routes_stay_non_public(self):
        for path in ("/coaches", "/coaches/", "/coaches/list", "/coaches/aBc123", "/coaches/popular",
                     "/coaches/recommend", "/coaches/c1/payout", "/contents/favorites", "/contents/favorites/", "/contents/qa/status/x1",
                     "/contents/recommend/u1", "/reels/recommend/u1", "/series/s1", "/programs/p1",
                     "/challenges/c1", "/buttons", "/categories/list", "/users/u1", "/content/list", "/coachesX"):
            with self.subTest(path):
                self.assertFalse(rules.is_public("GET", self.H, path))

    def test_writes_and_probes_on_public_routes_stay_non_public(self):
        self.assertFalse(rules.is_public("POST", self.H, "/coaches/list"))
        self.assertFalse(rules.is_public("PUT", self.H, "/contents/x1"))
        self.assertFalse(rules.is_public("GET", self.H, "/coaches/..%2f..%2fetc%2fpasswd"))
        self.assertFalse(rules.is_public("GET", self.H, "/contents/x1", "q=${jndi:ldap://a}"))

    def test_coach_gets_need_a_token_since_2_304_25(self):
        # ContentService v2.304.25 put `authenticate` on GET /coaches, /coaches/list and /coaches/:id,
        # so a 2xx there is a token-carrying caller, not a public read. The nightly coach job is
        # exempt as own traffic instead (CoachUploadMonitorIsOwnTraffic).
        for path in ("/coaches", "/coaches/list", "/coaches/c1"):
            with self.subTest(path):
                self.assertIsNone(rules.is_public_service_route(path))
        self.assertFalse(rules.is_public("GET", "34.117.12.99", "/.git/config"))

    def test_preflight_is_not_success(self):
        self.assertFalse(rules.is_success("OPTIONS", 204))
        self.assertTrue(rules.is_success("POST", 201))


class OwnTrafficNeverFlagged(unittest.TestCase):
    """The LiveKit agent: Oracle Cloud IPs, AsyncOpenAI UA, POSTs to /ai/* answered 200."""

    def agent_rows(self, ip="150.136.115.37", n=600):
        paths = ["/ai/openai/responses", "/ai/conversations/c0nv3rs4t10n1d000000/metrics",
                 "/ai/openai/models"]
        uas = [AGENT_UA, "AsyncOpenAI/Python 1.108.0", "OpenAI/Python 1.109.1"]
        return [row(ip, paths[i % 3], status=200, method="POST" if i % 3 < 2 else "GET",
                    host="services.aurahealth.io", ua=uas[i % 3], minute=i % 600)
                for i in range(n)]

    def test_agent_pattern_is_exempt_and_counted(self):
        rows = self.agent_rows() + self.agent_rows("129.213.103.212") + self.agent_rows("129.213.38.225")
        flagged, _ips, own = detect.analyse(rows, args(), T0, T0 + dt.timedelta(hours=10))
        self.assertEqual(flagged, [])
        self.assertEqual(own, {"LiveKit voice agent": len(rows)})

    def test_answered_posts_never_count_toward_ua_rotation(self):
        # Same shape off the allow-list (another route): still not "refused or non-GET".
        rows = [dict(r, path="/ai/other") for r in self.agent_rows()]
        flagged, _ips, own = detect.analyse(rows, args(), T0, T0 + dt.timedelta(hours=10))
        self.assertEqual(own, {})
        self.assertEqual(flagged, [])

    def test_ua_alone_is_not_enough(self):
        # A spoofed agent UA probing other routes is still a scanner.
        rows = [dict(r, ua=AGENT_UA, host="services.aurahealth.io") for r in env_scan("9.9.9.9")]
        flagged, _ips, own = detect.analyse(rows, args())
        self.assertEqual(own, {})
        self.assertEqual(len(flagged), 1)

    def test_refused_agent_routes_are_not_exempt(self):
        # A brute force wearing the agent's UA on its routes still shows up.
        rows = [row("9.9.9.9", "/ai/openai/responses", status=401, method="POST",
                    host="services.aurahealth.io", ua=AGENT_UA, minute=i % 50) for i in range(120)]
        flagged, _ips, own = detect.analyse(rows, args())
        self.assertEqual(own, {})
        self.assertEqual(len(flagged), 1)


def coach_monitor_module():
    spec = importlib.util.spec_from_file_location(
        "coach_upload_monitor", pathlib.Path(__file__).resolve().parents[1] / "scripts/coach-upload-monitor.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class CoachUploadMonitorIsOwnTraffic(unittest.TestCase):
    """AINative's nightly coach-upload-monitor: ~430 GETs on /coaches, /coaches/:id, /contents/:id."""

    def job_ua(self):
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "GITHUB_RUN_ID": "123"}, clear=False):
            os.environ.pop("AINATIVE_UA_WHO", None)
            return coach_monitor_module().user_agent()

    def job_rows(self, ua, ip="20.1.2.3"):
        """The rows detect.fetch yields for the nightly run: its log entries go through fetch itself,
        which drops the <who>/ segment of an AuraClaude agent."""
        paths = ["/coaches"] + [f"/coaches/c04ch{i:015d}" for i in range(300)] + [f"/contents/tr4ck{i:015d}" for i in range(131)]
        entries = [types.SimpleNamespace(
            timestamp=T0 + dt.timedelta(minutes=i % 5, seconds=i % 60), insert_id=str(i), payload={},
            resource=types.SimpleNamespace(labels={}),
            http_request={"remoteIp": ip, "userAgent": ua, "requestMethod": "GET", "status": 200,
                          "requestUrl": f"https://services.aurahealth.io{p}"})
            for i, p in enumerate(paths)]
        with mock.patch.object(detect, "list_with_retry", lambda _c, _f: iter(entries)), \
                contextlib.redirect_stderr(io.StringIO()):
            rows = list(detect.fetch(None, T0, T0 + dt.timedelta(hours=1), limit=10_000, full=True))
        self.assertEqual(len(rows), len(paths))
        return rows

    def test_the_script_sends_its_user_agent(self):
        mod = coach_monitor_module()
        seen = {}

        class Resp(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake(req, timeout=None):
            seen.update(req.header_items())
            return Resp(b"[]")

        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "GITHUB_RUN_ID": "123"}), \
                mock.patch.object(mod.urllib.request, "urlopen", fake):
            os.environ.pop("AINATIVE_UA_WHO", None)
            mod._get_json("https://services.aurahealth.io/coaches", "t0k")
        self.assertEqual(seen.get("User-agent"),
                         "AuraClaude/github-actions/ci:coach-upload-monitor (session gha-123)")

    def test_the_nightly_run_is_exempt_and_counted(self):
        rows = self.job_rows(self.job_ua())
        flagged, _ips, own = detect.analyse(rows, args(), T0, T0 + dt.timedelta(hours=1))
        self.assertEqual(flagged, [])
        self.assertEqual(own, {"coach-upload-monitor": len(rows)})

    def test_the_same_run_without_the_label_is_still_flagged(self):
        flagged, _ips, own = detect.analyse(self.job_rows("Python-urllib/3.11"), args(), T0, T0 + dt.timedelta(hours=1))
        self.assertEqual(own, {})
        self.assertEqual(len(flagged), 1)

    def test_the_label_does_not_cover_other_routes(self):
        ua = self.job_ua()
        self.assertIsNotNone(rules.is_own_traffic(detect._no_who(ua), "services.aurahealth.io", "/coaches/c1", 200))
        for path in ("/contents/favorites", "/coaches/c1/payout", "/users/u1"):
            with self.subTest(path):
                self.assertIsNone(rules.is_own_traffic(ua, "services.aurahealth.io", path, 200))
        self.assertIsNone(rules.is_own_traffic(ua, "services.aurahealth.io", "/coaches/c1", 401))

def brute_rows(ip="45.9.9.9", n=40, minute=0):
    return [row(ip, "/users/session", status=401, method="POST", host="services.aurahealth.io",
                minute=minute + i % 30) for i in range(n)]


APP = "Aura/3.33.1 (com.aurahealth.app; iOS 18.1) CFNetwork"


def expired_app_rows(ip="203.0.113.77", n=224):
    """The 11-11 02:56Z case: our iOS app retrying with a sign-in that is no longer valid. The backend
    answers 403 on ordinary app routes; Cloud Armor refuses none."""
    routes = ["/appointments/list", "/buttons/recommend", "/users/session/cookie", "/ai/usage/stats"]
    return [row(ip, routes[i % 4], status=403, method="POST" if i % 2 else "GET", host="services.aurahealth.io",
                ua=APP, minute=i % 50) for i in range(n)]


class AppExpiredSignInTest(unittest.TestCase):
    """ENG-1879: an app with an expired sign-in is not brute force, and posts no alert."""

    def test_our_app_retrying_with_an_expired_sign_in_is_not_flagged(self):
        flagged, _ips, own = detect.analyse(expired_app_rows(), args())
        self.assertEqual(flagged, [])
        self.assertEqual(own.get(detect.APP_EXPIRED), 224)  # counted, not dropped

    def test_credential_routes_under_our_user_agent_still_count(self):
        rows = [row("203.0.113.78", "/users/login", status=401, method="POST", host="services.aurahealth.io",
                    ua=APP, minute=i % 30) for i in range(40)]
        (f,) = detect.analyse(rows, args())[0]
        self.assertEqual(f["kind"], rules.BRUTE_FORCE)

    def test_the_sign_in_route_itself_still_counts_under_our_user_agent(self):
        rows = [row("203.0.113.81", "/users/session", status=401, method="POST", host="services.aurahealth.io",
                    ua=APP, minute=i % 30) for i in range(40)]
        (f,) = detect.analyse(rows, args())[0]
        self.assertEqual(f["kind"], rules.BRUTE_FORCE)
        self.assertTrue(detect.app_signin_expired(row("x", "/users/session/cookie", status=403, ua=APP)))

    def test_a_browser_failing_on_app_routes_is_still_flagged(self):
        rows = [dict(r, ua=BROWSER) for r in expired_app_rows("203.0.113.79")]
        self.assertEqual(len(detect.analyse(rows, args())[0]), 1)

    def test_a_cloud_armor_deny_is_never_set_aside(self):
        rows = [dict(r, armor={"enforced": {"outcome": "DENY", "priority": 4000}}) for r in expired_app_rows("203.0.113.80", 60)]
        self.assertFalse(any(detect.app_signin_expired(r) for r in rows))


def leak_rows(ip="45.1.2.3", minute=0):
    rows = env_scan(ip, host="services.aurahealth.io", minute=minute)
    rows.append(row(ip, "/.env", status=200, host="services.aurahealth.io", size=812, minute=minute))
    return rows


class Severity(unittest.TestCase):
    def one(self, rows, new=None):
        (f,) = plan_for(rows)["flagged"]
        return f, rules.severity(f, new)

    def test_routine_refused_is_info(self):
        f, (sev, _why) = self.one(env_scan("45.1.2.3"))
        self.assertEqual((sev, rules.group_key(f)), ("Info", FB))

    def test_a_new_type_is_low(self):
        _f, (sev, why) = self.one(env_scan("45.1.2.3"), new=["path family .env files"])
        self.assertEqual(sev, "Low")
        self.assertIn("not seen in the last 14 days", why[0])

    def test_brute_force_is_medium(self):
        f, (sev, _why) = self.one(brute_rows())
        self.assertEqual((sev, rules.group_key(f)), ("Medium", "Web|brute-force|refused"))

    def test_flood_that_moved_the_lb_is_medium_and_one_that_did_not_is_info(self):
        loud = [row("66.6.6.6", "/x", status=503, minute=i % 30) for i in range(2100)]
        self.assertEqual(self.one(loud)[1][0], "Medium")
        quiet = [row("66.6.6.6", "/x", status=404, minute=i % 60) for i in range(2100)]
        self.assertEqual(self.one(quiet)[1][0], "Info")

    def test_2xx_with_data_on_a_sensitive_path_is_high_and_data_accessed(self):
        f, (sev, _why) = self.one(leak_rows())
        self.assertEqual((sev, rules.group_key(f)), ("High", "Web|forced-browsing|data-accessed"))

    def test_2xx_without_body_on_a_private_api_is_obtained(self):
        rows = env_scan("45.1.2.3", host="services.aurahealth.io")
        rows.append(row("45.1.2.3", "/content/list", status=200, host="services.aurahealth.io"))
        f, (sev, _why) = self.one(rows)
        self.assertEqual((sev, rules.group_key(f)), ("High", "Web|forced-browsing|obtained"))

    def test_a_public_page_2xx_stays_refused(self):
        f, (sev, _why) = self.one(env_scan("45.1.2.3") + [row("45.1.2.3", "/", status=200)])
        self.assertEqual((sev, rules.group_key(f)), ("Info", FB))

    def coach_hit(self, size):
        """The 2026-10-23 shape: a scanner gets 200 on coach.aurahealth.io/live/.env."""
        return env_scan("45.148.10.238", host="coach.aurahealth.io") + [
            row("45.148.10.238", "/live/.env", status=200, host="coach.aurahealth.io", size=size)]

    def test_a_soft_404_the_size_of_the_hosts_pages_stays_refused(self):
        # CoachWeb served its app shell: 13,502 B logged against a 13,870 B typical page.
        (f,) = plan_for(self.coach_hit(13502), sizes={"coach.aurahealth.io": 13870})["flagged"]
        self.assertEqual((rules.severity(f)[0], rules.group_key(f)), ("Info", FB))
        self.assertEqual(f["soft_404"], 1)

    def test_a_probe_in_the_query_matches_the_same_paths_own_size(self):
        rows = env_scan("45.148.10.64") + [row("45.148.10.64", "/", query="phpinfo=1", status=200, size=37917)]
        sizes = {"www.aurahealth.io": 23520, "www.aurahealth.io/": 36900}
        (f,) = plan_for(rows, sizes=sizes)["flagged"]
        self.assertEqual(rules.severity(f)[0], "Info")

    def test_a_clearly_different_size_is_still_high(self):
        # A real .env is a small text body, nothing like the app shell.
        (f,) = plan_for(self.coach_hit(412), sizes={"coach.aurahealth.io": 13870})["flagged"]
        self.assertEqual((rules.severity(f)[0], rules.group_key(f)), ("High", "Web|forced-browsing|data-accessed"))

    def test_no_baseline_stays_high(self):
        (f,) = plan_for(self.coach_hit(13502))["flagged"]
        self.assertEqual(rules.severity(f)[0], "High")

    def test_the_guard_never_applies_to_services(self):
        self.assertFalse(rules.is_soft_404("GET", "services.aurahealth.io", "/.env", 200, 100,
                                           {"services.aurahealth.io": 100}))

    def test_answered_sign_in_after_refused_ones_is_high(self):
        rows = brute_rows(minute=0) + [row("45.9.9.9", "/users/session", status=200, method="POST",
                                           host="services.aurahealth.io", minute=40)]
        _f, (sev, why) = self.one(rows)
        self.assertEqual(sev, "High")
        self.assertTrue(any("sign-in" in w for w in why))


class IncidentLogGroups(Harness):
    def test_one_row_per_group_with_the_union_of_ips_and_uas(self):
        self.seed(".env files")
        self.apply(plan_for(env_scan("45.1.2.3")))
        self.apply(plan_for(env_scan("77.7.7.7", minute=120, ua="curl/8.1"), *at(2)))
        (page,) = self.notion.rows()
        self.assertEqual(self.notion.text(page, "Group key"), FB)
        ips = log.parse_ips(self.notion.text(page, "IP addresses"))
        self.assertEqual({k: v["n"] for k, v in ips.items()}, {"45.1.2.3": 60, "77.7.7.7": 60})
        self.assertEqual(ips["77.7.7.7"]["last"], "2026-10-23T12:02Z")
        uas = log.parse_uas(self.notion.text(page, "User agent"))
        self.assertEqual(uas, {BROWSER: 60, "curl/8.1": 60})
        self.assertEqual(page["properties"]["Requests"]["number"], 120)
        self.assertEqual(page["properties"]["Max IPs"]["number"], 2)
        self.assertIn("45.1.2.0/24", self.notion.text(page, "Source network"))

    def test_overlapping_windows_never_count_a_minute_twice(self):
        self.seed(".env files")
        plan = plan_for(env_scan("45.1.2.3"))
        self.apply(plan)
        self.apply(plan)  # a retry, or the next hourly window re-reading the overlap
        (page,) = self.notion.rows()
        self.assertEqual(page["properties"]["Requests"]["number"], 60)
        self.assertEqual(log.parse_ips(self.notion.text(page, "IP addresses"))["45.1.2.3"]["n"], 60)

    def test_an_existing_row_keeps_its_ips_and_uas(self):
        # A row written by hand or by the 1.1 writer: comma IPs without counts, `ua (n); ua (n)`.
        self.notion.pages["a" * 32] = {"id": "a" * 32, "parent": {"database_id": "db"}, "archived": False,
                                       "children": [], "properties": {
            "Group key": log.rich(FB), "Severity": {"select": {"name": "Low"}},
            "Ended (UTC)": {"date": {"start": "2026-10-23T09:00:00Z"}}, "Requests": {"number": 500},
            "IP addresses": log.rich("9.9.9.1, 9.9.9.2"), "User agent": log.rich(SEMI_UA + " (40)\ncurl/7 (2)"),
            "Notes": log.rich(""), "What they did": log.rich("old line"), "Route / path": log.rich("")}}
        self.seed(".env files")
        self.apply(plan_for(env_scan("45.1.2.3")))
        (page,) = self.notion.rows()
        ips = log.parse_ips(self.notion.text(page, "IP addresses"))
        self.assertEqual(set(ips), {"9.9.9.1", "9.9.9.2", "45.1.2.3"})
        self.assertIsNone(ips["9.9.9.1"]["n"], "an unknown count is kept unknown, not invented")
        self.assertEqual(log.parse_uas(self.notion.text(page, "User agent")),
                         {SEMI_UA: 40, "curl/7": 2, BROWSER: 60})
        self.assertEqual(page["properties"]["Requests"]["number"], 560)
        self.assertEqual(self.notion.sev(page), "Low", "severity is never lowered")
        self.assertTrue(self.notion.text(page, "What they did").startswith("old line\n"))

    def test_a_group_closes_after_2_days_without_hits(self):
        # Jerome, 2026-11-03: the window was 14 days.
        self.seed(".env files")
        self.apply(plan_for(env_scan("45.1.2.3")))
        self.apply(plan_for(env_scan("45.1.2.3", minute=47 * 60), *at(47)))
        self.assertEqual(len(self.notion.rows()), 1)
        self.apply(plan_for(env_scan("45.1.2.3", minute=(47 + 49) * 60), *at(47 + 49)))
        self.assertEqual(len(self.notion.rows()), 2)

    def test_another_outcome_is_another_group_and_severity_only_goes_up(self):
        self.seed(".env files")
        self.apply(plan_for(env_scan("45.1.2.3")))
        self.apply(plan_for(leak_rows("45.1.2.4", minute=120), *at(2)))
        keys = sorted(self.notion.text(p, "Group key") for p in self.notion.rows())
        self.assertEqual(keys, ["Web|forced-browsing|data-accessed", FB])
        high = next(p for p in self.notion.rows() if "accessed" in self.notion.text(p, "Group key"))
        self.assertEqual(self.notion.sev(high), "High")
        self.assertEqual(high["properties"]["Status"]["select"]["name"], "Open")

    def test_severity_is_raised_on_an_open_row(self):
        self.seed(".env files", kinds=("Forced browsing",))
        self.apply(plan_for(env_scan("45.1.2.3")))
        (page,) = self.notion.rows()
        self.assertEqual(self.notion.sev(page), "Info")
        # A new family joins the same group: Low.
        rows = [row("45.1.2.3", f"/wp-admin/{i}.php", minute=120 + i % 30) for i in range(40)]
        self.apply(plan_for(rows, *at(2)))
        (page,) = self.notion.rows()
        self.assertEqual(self.notion.sev(page), "Low")
        self.assertIn("severity raised to Low", self.notion.text(page, "Notes"))

    def test_missing_columns_refuse_with_exit_2_and_write_nothing(self):
        self.notion.schema = {"properties": {"Severity": SCHEMA["properties"]["Severity"]}}
        code, _ = self.apply(plan_for(env_scan("45.1.2.3")))
        self.assertEqual(code, 2)
        self.assertEqual(self.notion.pages, {})
        self.notion.schema = {"properties": {"Group key": SCHEMA["properties"]["Group key"],
                                             "Severity": {"type": "select", "select": {"options": [{"name": "Low"}]}}}}
        self.assertEqual(self.apply(plan_for(env_scan("45.1.2.3")))[0], 2)
        with self.assertRaises(log.SchemaError):
            log.check_schema(self.notion)

    def test_staff_is_not_staff_once_the_column_exists_and_a_value_is_never_overwritten(self):
        # ENGBACK-2614: load-balancer rows are outside traffic, so "Not staff". Left out while the column or
        # the option is missing (Notion would create the option), and never written over a value.
        self.seed(".env files")
        self.apply(plan_for(env_scan("45.1.2.3")))
        (page,) = self.notion.rows()
        self.assertNotIn("Staff", page["properties"])
        self.notion.schema = {"properties": dict(SCHEMA["properties"], Staff={
            "type": "select", "select": {"options": [{"name": "Staff"}, {"name": "Not staff"}]}})}
        self.apply(plan_for(env_scan("77.7.7.7", minute=120), *at(2)))
        (page,) = self.notion.rows()
        self.assertEqual(page["properties"]["Staff"], {"select": {"name": "Not staff"}})  # filled on update
        page["properties"]["Staff"] = {"select": {"name": "Staff"}}  # a person's call
        self.apply(plan_for(env_scan("88.8.8.8", minute=240), *at(4)))
        (page,) = self.notion.rows()
        self.assertEqual(page["properties"]["Staff"], {"select": {"name": "Staff"}})
        self.notion.pages.clear()
        self.notion.schema = {"properties": dict(SCHEMA["properties"], Staff={
            "type": "select", "select": {"options": [{"name": "Staff"}]}})}
        self.apply(plan_for(env_scan("45.1.2.3", minute=360), *at(6)))
        (page,) = self.notion.rows()
        self.assertNotIn("Staff", page["properties"])

    def test_the_other_category_keeps_its_old_label_until_the_option_is_renamed(self):
        # ENG-1868: "Other attack" is renamed in place. Notion creates any option a write names, so the
        # writer names whichever of the two the live list has.
        def schema(*cats):
            return {"properties": dict(SCHEMA["properties"], Category={
                "type": "select", "select": {"options": [{"name": c} for c in cats]}})}
        for cats, want in ((("Forced browsing", "Other attack"), "Other attack"),
                           (("Forced browsing", "Other unrecognized access"), "Other unrecognized access")):
            self.notion.schema = schema(*cats)
            options = log.check_schema(self.notion)
            self.assertEqual(log.category_name("Other attack", options), want)
            self.assertEqual(log.category_name("Forced browsing", options), "Forced browsing")

    def test_ban_data_overflow_goes_to_a_child_page_and_is_never_lost(self):
        self.seed(".env files")
        many = [row(f"10.{i // 250}.{i % 250}.1", "/.env", minute=i % 50, ua=f"bot/{i % 7000}")
                for i in range(7000)]
        many = [r for r in many for _ in range(3)]  # 3 requests each: over the attack-path threshold
        self.apply(plan_for(many))
        (page,) = self.notion.rows()
        items = page["properties"]["IP addresses"]["rich_text"]
        self.assertEqual(len(items), log.MAX_ITEMS)
        self.assertRegex(items[-1]["text"]["content"], log.OVERFLOW)
        _child, ips, uas = log.row_ban_data(self.notion.read(page), self.notion)
        self.assertEqual(len(ips), 7000)
        self.assertEqual(len(uas), 7000)
        # A later run merges into the overflow, and the old child page is archived.
        self.apply(plan_for(env_scan("45.1.2.3", minute=120), *at(2)))
        (page,) = self.notion.rows()
        _child, ips, _uas = log.row_ban_data(self.notion.read(page), self.notion)
        self.assertEqual(len(ips), 7001)
        children = [p for p in self.notion.pages.values() if "page_id" in p["parent"]]
        self.assertEqual(sum(not p["archived"] for p in children), 1)

    def test_user_agents_split_on_newlines_only(self):
        text = f"{SEMI_UA} (1921)\n(empty user agent) (3)\nzgrab/0.x"
        uas = log.parse_uas(text)
        self.assertEqual(uas, {SEMI_UA: 1921, "": 3, "zgrab/0.x": None})
        self.assertEqual(log.format_uas(uas), text, "written back exactly as read")

    def test_chunks_never_split_or_drop_a_line(self):
        text = "\n".join(f"203.0.113.{i % 250} ({i} req, last 2026-10-23T10:00Z)" for i in range(2000))
        parts = log.chunks(text)
        self.assertTrue(all(len(p) <= log.CHUNK for p in parts))
        self.assertEqual("".join(parts), text, "Notion concatenates items as they are")
        self.assertTrue(all(p.endswith("\n") for p in parts[:-1]), "no line spans two items")


class SlackThreshold(Harness):
    def test_default_posts_only_medium_and_high(self):
        self.seed(".env files", kinds=("Forced browsing", rules.BRUTE_FORCE))
        self.apply(plan_for(env_scan("45.1.2.3")))  # Info
        self.apply(plan_for([row("45.1.2.3", f"/wp-admin/{i}.php", minute=120 + i % 30)
                             for i in range(40)], *at(2)))  # Low
        self.assertEqual(self.slack.posts, [])
        self.assertEqual(len(self.notion.rows()), 1, "Info and Low are still logged")
        self.apply(plan_for(brute_rows(minute=240), *at(4)))
        (msg,) = self.slack.posts
        self.assertTrue(msg.startswith("[MEDIUM]"))
        self.assertNotIn("<@", msg, "no mention unless one is configured")

    def test_mention_only_when_configured(self):
        self.seed(kinds=(rules.BRUTE_FORCE,))
        self.apply(plan_for(brute_rows()), "--mention-user", "U123")
        self.assertIn("<@U123>", self.slack.posts[0])

    def test_mention_from_env(self):
        self.seed(kinds=(rules.BRUTE_FORCE,))
        os.environ["FLAG_TRAFFIC_MENTION"] = "U999"
        self.apply(plan_for(brute_rows()))
        self.assertIn("<@U999>", self.slack.posts[0])

    def test_the_pre_rename_env_name_still_works_for_one_release(self):
        self.seed(kinds=(rules.BRUTE_FORCE,))
        os.environ["DETECT_ATTACKS_MENTION"] = "U777"
        self.apply(plan_for(brute_rows()))
        self.assertIn("<@U777>", self.slack.posts[0])

    def test_threshold_from_env(self):
        self.seed(".env files")
        os.environ["FLAG_TRAFFIC_SLACK_MIN_SEVERITY"] = "info"
        self.apply(plan_for(env_scan("45.1.2.3")))
        self.assertTrue(self.slack.posts[0].startswith("[INFO]"))

    def test_high_links_the_incident_row(self):
        self.seed(".env files")
        self.apply(plan_for(leak_rows()), "--mention-user", "U1")
        (msg,) = self.slack.posts
        self.assertTrue(msg.startswith("[HIGH]"))
        self.assertIn("app.notion.com/p/", msg)
        self.assertIn("<@U1>", msg)


class SlackGroups(Harness):
    """At --slack-min-severity info, so every group reaches Slack."""

    def run_at(self, rows, hours=0, *extra):
        return self.apply(plan_for(rows, *at(hours)), "--slack-min-severity", "info", "--mention-user", "U1",
                          *extra)

    def test_same_group_update_is_a_threaded_reply_never_an_edit(self):
        # A posted message is never edited; its later counts go in its thread.
        self.seed(".env files")
        self.run_at(env_scan("45.1.2.3"))
        self.run_at(env_scan("77.7.7.7", minute=60), 1)
        self.assertEqual(len(self.slack.posts), 1)
        self.assertFalse(hasattr(apply_.Slack, "update"))
        (ts, text), = self.slack.threads
        self.assertEqual(ts, "1.0")
        self.assertIn("120 requests", text)
        self.assertIn("2 IP(s) in 2 /24(s)", text)
        self.assertTrue(text.startswith("[INFO]"))

    def test_a_new_family_joining_gets_a_thread_reply_not_a_post(self):
        self.seed(".env files", "WordPress")
        self.run_at(env_scan("45.1.2.3"))
        self.run_at([row("45.1.2.3", f"/wp-admin/{i}.php", minute=60 + i % 30) for i in range(40)], 1)
        self.assertEqual(len(self.slack.posts), 1)
        (ts, text), = self.slack.threads
        self.assertEqual(ts, "1.0")
        self.assertIn("WordPress", text)

    def test_a_10x_volume_jump_gets_a_thread_reply(self):
        self.seed(".env files")
        self.run_at(env_scan("45.1.2.3", n=20))
        self.run_at(env_scan("45.1.2.3", minute=60, n=400), 1)
        self.assertEqual(len(self.slack.posts), 1)
        self.assertIn("volume jumped", self.slack.threads[0][1])

    def test_escalation_is_a_new_post_and_never_goes_back_down(self):
        self.seed(".env files", kinds=("Forced browsing",))
        self.run_at(env_scan("45.1.2.3"))
        # The same group key escalated by a new family: Low.
        self.run_at([row("45.1.2.9", f"/wp-admin/{i}.php", minute=60 + i % 30) for i in range(40)], 1)
        self.assertEqual(len(self.slack.posts), 2)
        self.assertTrue(self.slack.posts[1].startswith("[LOW]"))
        self.assertIn("Escalated from [INFO]", self.slack.posts[1])
        self.run_at(env_scan("45.1.2.3", minute=120), 2)  # Info hit on the same group
        self.assertEqual(len(self.slack.posts), 2)
        self.assertTrue(self.slack.threads[-1][1].startswith("[LOW]"), "kept at its highest level")
        self.assertEqual(self.slack.threads[-1][0], "2.0", "replies go under the escalated message")

    def test_mention_on_medium_and_high_only(self):
        self.seed(".env files", kinds=("Forced browsing", rules.BRUTE_FORCE))
        self.run_at(env_scan("45.1.2.3"))
        self.run_at(brute_rows(minute=60), 1)
        self.assertNotIn("<@U1>", self.slack.posts[0])
        self.assertIn("<@U1>", self.slack.posts[1])

    def test_a_group_ends_after_the_gap(self):
        self.seed(".env files")
        self.run_at(env_scan("45.1.2.3"))
        self.run_at(env_scan("45.1.2.3", minute=5 * 60), 5)
        self.assertEqual(len(self.slack.posts), 2)

    def test_a_failed_threaded_reply_is_retried_never_posted_twice(self):
        self.seed(".env files")
        self.run_at(env_scan("45.1.2.3"))
        self.slack.thread_error = "ratelimited"
        code, _ = self.run_at(env_scan("45.1.2.3", minute=60), 1)
        self.assertEqual(code, 3)
        self.assertEqual((len(self.slack.posts), len(self.slack.threads)), (1, 0))
        self.slack.thread_error = None
        self.run_at(env_scan("45.1.2.3", minute=60), 1)
        self.assertEqual((len(self.slack.posts), len(self.slack.threads)), (1, 1))

    def test_a_failed_post_is_retried_once(self):
        self.seed(kinds=(rules.BRUTE_FORCE,))
        self.slack.post_error = "channel_not_found"
        code, _ = self.apply(plan_for(brute_rows()))
        self.assertEqual(code, 3)
        self.assertEqual(self.history().get("slack", {}), {})
        self.assertEqual(len(self.notion.rows()), 1, "the row is written before the post")
        self.slack.post_error = None
        self.apply(plan_for(brute_rows()))
        self.apply(plan_for(brute_rows()))
        self.assertEqual(len(self.slack.posts), 1)
        self.assertEqual(len(self.notion.rows()), 1)

    def test_a_notion_failure_still_posts_and_the_retry_does_not_repost(self):
        self.seed(kinds=(rules.BRUTE_FORCE,))
        self.notion.fail_writes = True
        code, _ = self.apply(plan_for(brute_rows()))
        self.assertEqual(code, 4)
        self.assertEqual(len(self.slack.posts), 1)
        self.notion.fail_writes = False
        self.assertEqual(self.apply(plan_for(brute_rows()))[0], 0)
        self.assertEqual(len(self.notion.rows()), 1)
        self.assertEqual(len(self.slack.posts), 1)

    def test_dry_run_writes_and_posts_nothing(self):
        self.seed(kinds=(rules.BRUTE_FORCE,))
        code, out = self.apply(plan_for(brute_rows()), dry=True)
        self.assertEqual(code, 0)
        self.assertIn("[MEDIUM]", out)
        self.assertEqual((self.slack.posts, self.notion.pages), ([], {}))
        self.assertFalse((self.dir / "groups.json").exists())
        self.assertTrue(all(m == "GET" or p.endswith("/query") for m, p in self.notion.calls))


class ReviewFindings(Harness):
    def test_volume_without_errors_is_not_a_flood_category(self):
        rows = [row("66.6.6.6", f"/a{i}/.env", minute=i % 60) for i in range(300)]
        rows += [row("66.6.6.6", "/ok", status=301, minute=i % 60) for i in range(2000)]
        (f,) = plan_for(rows)["flagged"]
        self.assertEqual(f["kind"], "Forced browsing")
        rows = [row("66.6.6.6", "/x", status=404, minute=i % 60, ua=f"ua{i % 3}") for i in range(150)]
        rows += [row("66.6.6.6", "/ok", status=301, minute=i % 60, ua=f"ua{i % 3}") for i in range(2000)]
        (f,) = plan_for(rows)["flagged"]
        self.assertNotEqual(f["kind"], "Flood / DoS")

    def test_source_network_keeps_the_busiest_24s(self):
        ips = {f"10.{i // 250}.{i % 250}.{j}": {"n": 1000 - i, "last": None} for i in range(5000) for j in (1, 2)}
        text = log.head_lines(log.networks(ips))
        self.assertTrue(text.startswith("10.0.0.0/24: 2 IPs, 2000 req"))

    def test_a_grouped_scan_is_not_a_flood_however_many_requests_it_adds_up_to(self):
        # 30 IPs, 100 errors each, all window long, while the LB throws 5xx elsewhere.
        rows = [row(f"45.1.{i}.1", f"/a{j}/.env", minute=j % 60) for i in range(30) for j in range(100)]
        rows += [row("8.8.8.8", "/ok", status=503, minute=m) for m in range(60) for _ in range(10)]
        for f in plan_for(rows)["flagged"]:
            if "45.1." in f["ip"]:
                self.assertIsNone(f["impact"])
                self.assertEqual(rules.severity(f)[0], "Info")

    def test_a_flood_without_a_quiet_baseline_needs_its_own_5xx(self):
        busy = [row("66.6.6.6", "/x", status=404, minute=i % 60) for i in range(2100)]
        busy += [row("8.8.8.8", "/ok", status=503, minute=m) for m in range(60) for _ in range(10)]
        f = next(f for f in plan_for(busy)["flagged"] if f["ip"] == "66.6.6.6")
        self.assertFalse(f["impact"]["moved"])
        own = [row("66.6.6.6", "/x", status=503, minute=i % 60) for i in range(2100)]
        self.assertTrue(plan_for(own)["flagged"][0]["impact"]["moved"])

    def test_a_retried_post_carries_the_requests_the_row_already_counted(self):
        self.seed(kinds=(rules.BRUTE_FORCE,))
        self.slack.post_error = "ratelimited"
        self.apply(plan_for(brute_rows()))
        self.slack.post_error = None
        self.apply(plan_for(brute_rows()))
        self.assertIn("40 requests", self.slack.posts[0])
        (page,) = self.notion.rows()
        self.assertEqual(page["properties"]["Requests"]["number"], 40)
        self.assertEqual(self.notion.text(page, "What they did").count("\n"), 0, "one line, not two")

    def test_an_exception_in_one_group_does_not_stop_the_others(self):
        self.seed(".env files", kinds=("Forced browsing", rules.BRUTE_FORCE))
        args = ("--slack-min-severity", "info")
        self.apply(plan_for(env_scan("45.1.2.3")), *args)

        real = self.slack.post

        def boom(text, thread_ts=None):
            if thread_ts:
                raise TimeoutError("read timed out")
            return real(text)
        self.slack.post = boom
        code, _ = self.apply(plan_for(env_scan("45.1.2.3", minute=60) + brute_rows(minute=60), *at(1)), *args)
        self.assertEqual(code, 3)
        self.assertEqual(len(self.slack.posts), 2)
        self.assertTrue(self.slack.posts[1].startswith("[MEDIUM]"))

    def test_an_ended_group_is_not_escalated_from_its_closed_message(self):
        self.seed(".env files", kinds=("Forced browsing",))
        args = ("--slack-min-severity", "info")
        self.apply(plan_for(env_scan("45.1.2.3")), *args)
        rows = [row("45.1.2.9", f"/wp-admin/{i}.php", minute=6 * 60 + i % 30) for i in range(40)]
        self.apply(plan_for(rows, *at(6)), *args)
        self.assertTrue(self.slack.posts[1].startswith("[LOW]"))
        self.assertNotIn("Escalated", self.slack.posts[1])


class ReviewFindings2(Harness):
    def test_an_overlapping_window_adds_no_family_or_status_twice(self):
        self.seed(".env files")
        args = ("--slack-min-severity", "info")
        self.apply(plan_for(env_scan("45.1.2.3", n=60)), *args)            # minutes 0-2
        self.apply(plan_for(env_scan("45.1.2.3", n=120), *at(0)), *args)   # re-reads 0-2, adds 3-5
        (_ts, text), = self.slack.threads
        self.assertIn("120 requests", text)
        self.assertIn(".env files 120", text)
        self.assertIn("404:120", text)
        (page,) = self.notion.rows()
        self.assertTrue(self.notion.text(page, "What they did").endswith(": 60 req, .env files"),
                        "the line counts only the new requests")

    def test_a_date_only_ended_is_read_as_midnight_utc(self):
        self.notion.pages["b" * 32] = {"id": "b" * 32, "parent": {"database_id": "db"}, "archived": False,
                                       "children": [], "properties": {
            "Group key": log.rich(FB), "Severity": {"select": {"name": "Low"}},
            "Ended (UTC)": {"date": {"start": "2026-10-22"}}, "Requests": {"number": 5},
            "IP addresses": log.rich(""), "User agent": log.rich(""), "Notes": log.rich(""),
            "What they did": log.rich(""), "Route / path": log.rich("")}}
        self.seed(".env files")
        code, _ = self.apply(plan_for(env_scan("45.1.2.3")))
        self.assertEqual(code, 0)
        self.assertEqual(len(self.notion.rows()), 1)
        self.assertEqual(self.notion.rows()[0]["properties"]["Requests"]["number"], 65)


class ReviewFindings3(Harness):
    def many(self, n=7000, minute=0):
        rows = [row(f"10.{i // 250}.{i % 250}.1", "/.env", minute=minute + i % 50, ua=f"bot/{i}")
                for i in range(n)]
        return [r for r in rows for _ in range(3)]

    def test_a_page_load_of_login_is_not_a_sign_in(self):
        rows = brute_rows() + [row("45.9.9.9", "/login", status=200, host="coach.aurahealth.io", minute=40)]
        (f,) = plan_for(rows)["flagged"]
        self.assertEqual(f["signin_after_fail"], 0)
        self.assertEqual(rules.severity(f)[0], "Medium")

    def test_an_overflowing_new_row_that_fails_midway_is_filled_completely_on_retry(self):
        self.seed(".env files")
        plan = plan_for(self.many())
        self.notion.fail_row_patches = 1
        self.assertEqual(self.apply(plan)[0], 4)
        self.apply(plan)
        (page,) = self.notion.rows()
        _child, ips, uas = log.row_ban_data(self.notion.read(page), self.notion)
        self.assertEqual((len(ips), len(uas)), (7000, 7000))
        self.assertEqual(page["properties"]["Requests"]["number"], 21000)
        self.assertEqual(sum(v["n"] for v in ips.values()), 21000)
        self.assertEqual(self.notion.text(page, "What they did").count("\n"), 0, "one line, not two")

    def test_an_overflowing_update_that_fails_midway_does_not_double_count(self):
        self.seed(".env files")
        self.apply(plan_for(self.many()))
        later = plan_for(env_scan("45.1.2.3", minute=120), *at(2))
        self.notion.fail_row_patches = 1
        self.assertEqual(self.apply(later)[0], 4)
        self.apply(later)
        (page,) = self.notion.rows()
        _child, ips, _uas = log.row_ban_data(self.notion.read(page), self.notion)
        self.assertEqual(ips["45.1.2.3"]["n"], 60)
        self.assertEqual(page["properties"]["Requests"]["number"], 21060)
        children = [p for p in self.notion.pages.values() if "page_id" in p["parent"] and not p["archived"]]
        self.assertEqual(len(children), 1, "no orphan page from the failed attempt")

    def test_a_failed_archive_of_the_old_page_does_not_fail_the_write(self):
        self.seed(".env files")
        self.apply(plan_for(self.many()))
        self.notion.fail_archive = True
        code, _ = self.apply(plan_for(env_scan("45.1.2.3", minute=120), *at(2)))
        self.assertEqual(code, 0)
        self.apply(plan_for(env_scan("45.1.2.3", minute=120), *at(2)))
        (page,) = self.notion.rows()
        self.assertEqual(page["properties"]["Requests"]["number"], 21060, "not counted twice")


class OverlapAndHumanCalls(Harness):
    """ENG-1881: the 03:18Z hourly run re-raised a row a person had set to Info, noted "severity raised"
    and posted a thread reply, while adding no requests (its 65-minute window overlapped the last run)."""

    def patches(self):
        return [c for c in self.notion.calls if c[0] == "PATCH" and c[1].startswith("/pages/")]

    def test_a_rerun_that_adds_nothing_writes_and_posts_nothing(self):
        self.seed(kinds=(rules.BRUTE_FORCE,))
        plan = plan_for(brute_rows())
        self.apply(plan)
        (page,) = self.notion.rows()
        before = (len(self.patches()), len(self.slack.posts), len(self.slack.threads))
        code, out = self.apply(plan)
        self.assertEqual((len(self.patches()), len(self.slack.posts), len(self.slack.threads)), before)
        self.assertIn("skipped.noNewRequests 1", out)

    def test_a_row_a_person_set_to_info_is_never_re_raised_or_re_posted(self):
        self.seed(kinds=(rules.BRUTE_FORCE,))
        self.apply(plan_for(brute_rows()))
        (page,) = self.notion.rows()
        page["properties"]["Severity"] = {"select": {"name": "Info"}}  # a person's call
        posts = (len(self.slack.posts), len(self.slack.threads))
        code, out = self.apply(plan_for(brute_rows(minute=35)))      # new requests, same group
        (page,) = self.notion.rows()
        self.assertEqual(self.notion.sev(page), "Info")
        self.assertNotIn("severity raised", self.notion.text(page, "Notes"))
        self.assertEqual((len(self.slack.posts), len(self.slack.threads)), posts)
        self.assertEqual(page["properties"]["Requests"]["number"], 80)  # the requests are still recorded
        self.assertIn("skipped.humanClassified 1", out)

    def test_a_lowered_severity_holds_on_every_later_run(self):
        self.seed(kinds=(rules.BRUTE_FORCE,))
        self.apply(plan_for(brute_rows()))
        (page,) = self.notion.rows()
        page["properties"]["Severity"] = {"select": {"name": "Info"}}
        posts = (len(self.slack.posts), len(self.slack.threads))
        for h in (1, 2):  # two more hourly runs with new requests
            self.apply(plan_for(brute_rows(minute=60 * h), *at(h)))
        (page,) = self.notion.rows()
        self.assertEqual(self.notion.sev(page), "Info")
        self.assertNotIn("severity raised", self.notion.text(page, "Notes"))
        self.assertEqual((len(self.slack.posts), len(self.slack.threads)), posts)

    def test_a_resolved_group_does_not_reopen_while_it_stays_active(self):
        self.seed(kinds=(rules.BRUTE_FORCE,))
        self.apply(plan_for(brute_rows()))
        (page,) = self.notion.rows()
        page["properties"]["Status"] = {"select": {"name": "Resolved"}}
        # Hourly traffic for three days: more than the 2-day group window past the row's Ended.
        for h in range(1, 73, 6):
            self.apply(plan_for(brute_rows(minute=60 * h), *at(h)))
        self.assertEqual(len(self.notion.rows()), 1, "no new row for the group the person resolved")
        self.assertEqual(len(self.slack.posts), 1)

    def test_a_row_lowered_before_this_change_is_still_a_persons_call(self):
        # groups.json written by the previous version has no `severity`; the Slack history does.
        self.seed(kinds=(rules.BRUTE_FORCE,))
        self.apply(plan_for(brute_rows()))
        groups = self.dir / "groups.json"
        state = json.loads(groups.read_text(encoding="utf-8"))
        for v in state.values():
            v.pop("severity", None)
        groups.write_text(json.dumps(state), encoding="utf-8")
        (page,) = self.notion.rows()
        page["properties"]["Severity"] = {"select": {"name": "Info"}}
        posts = (len(self.slack.posts), len(self.slack.threads))
        self.apply(plan_for(brute_rows(minute=35)))
        (page,) = self.notion.rows()
        self.assertEqual(self.notion.sev(page), "Info")
        self.assertEqual((len(self.slack.posts), len(self.slack.threads)), posts)

    def test_a_resolved_row_is_left_alone(self):
        self.seed(kinds=(rules.BRUTE_FORCE,))
        self.apply(plan_for(brute_rows()))
        (page,) = self.notion.rows()
        page["properties"]["Status"] = {"select": {"name": "Resolved"}}
        n = len(self.patches())
        code, out = self.apply(plan_for(brute_rows(minute=35)))
        self.assertEqual(len(self.patches()), n)
        self.assertEqual(len(self.slack.posts), 1)
        self.assertIn("skipped.humanClassified 1", out)


class FreshHistory(Harness):
    def test_seeded_from_the_old_state_file(self):
        (self.dir / "state.json").write_text(json.dumps({"old": {
            "kind": "Forced browsing", "last": detect.iso(T0 - dt.timedelta(days=2)),
            "routes": ["GET /.env", "GET /x/.env"]}}))
        self.apply(plan_for(env_scan("45.1.2.3")))
        (page,) = self.notion.rows()
        self.assertEqual(self.notion.sev(page), "Info")


class Blocklist(Harness):
    def test_export_unions_rows_and_leaves_our_traffic_out(self):
        self.seed(".env files", "WordPress", kinds=("Forced browsing", rules.BRUTE_FORCE))
        self.apply(plan_for(env_scan("45.1.2.3") + env_scan("45.1.2.7", ua=AGENT_UA)))
        self.apply(plan_for(brute_rows("45.1.2.3", minute=120), *at(2)))
        rows = log.open_rows(self.notion, now=T0 + dt.timedelta(hours=3))
        out = blocklist.collect(rows, call=self.notion)
        ips = {x["ip"]: x for x in out["ips"]}
        self.assertEqual(ips["45.1.2.3"]["requests"], 100)
        self.assertEqual(len(ips["45.1.2.3"]["groups"]), 2)
        self.assertEqual(out["cidrs"][0]["cidr"], "45.1.2.0/24")
        self.assertEqual(len(out["cidrs"][0]["ips"]), 2)
        uas = {x["user_agent"] for x in out["user_agents"]}
        self.assertIn(BROWSER, uas)
        self.assertNotIn(AGENT_UA, uas, "our own user agent is never a ban candidate")
        medium = blocklist.collect(rows, min_severity="Medium", call=self.notion)
        self.assertEqual(medium["groups"], ["Web|brute-force|refused"])
        self.assertEqual(blocklist.collect(rows, exclude_ips={"45.1.2.3"}, call=self.notion)["ips"][0]["ip"],
                         "45.1.2.7")
        self.assertEqual(len(blocklist.collect(rows, min_requests=61, call=self.notion)["ips"]), 1)
        csv_text = blocklist.to_csv(out)
        self.assertIn("cidr,45.1.2.0/24", csv_text)

    def test_main_is_read_only(self):
        self.seed(".env files")
        self.apply(plan_for(env_scan("45.1.2.3")))
        before = len(self.notion.calls)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            blocklist.main(["--format", "json"], call=self.notion)
        self.assertEqual(json.loads(out.getvalue())["ips"][0]["ip"], "45.1.2.3")
        self.assertTrue(all(m == "GET" or p.endswith("/query") for m, p in self.notion.calls[before:]))


if __name__ == "__main__":
    unittest.main()


class StateRenameTest(unittest.TestCase):
    """ENG-1874: the state folder moves once from the old skill name, so open groups carry on."""

    def test_the_old_state_folder_is_adopted_once_and_never_over_a_new_one(self):
        with tempfile.TemporaryDirectory() as d:
            old, new = os.path.join(d, "detect-attacks"), os.path.join(d, "flag-suspicious-traffic")
            os.makedirs(old)
            with open(os.path.join(old, "groups.json"), "w") as f:
                f.write("{}")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertTrue(apply_.adopt_old_state(new, old))
            self.assertTrue(os.path.exists(os.path.join(new, "groups.json")))
            self.assertFalse(os.path.exists(old))
            os.makedirs(old)
            self.assertFalse(apply_.adopt_old_state(new, old))  # the new folder exists: leave both alone
            self.assertTrue(os.path.isdir(old))
