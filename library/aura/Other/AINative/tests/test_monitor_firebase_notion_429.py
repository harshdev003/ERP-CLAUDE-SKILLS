"""monitor-firebase: a Notion 429 in the network check is retried, then tagged, never silent (ENG-1755).

On 2026-11-03 from 02:33Z the Known Team Networks read failed three passes running on Notion's
`public_api_request_rate_limit`. Other sessions were writing through the same NOTION_TOKEN integration,
and nothing retried. Each pass logged 2 allowed reads to the Security Incident Log with no teammate or
console verdict, and nothing on the rows said why.

- `common.curl_json(retries_429=N)` retries only a 429, waiting for Retry-After (else 2**attempt s, capped),
  and raises RateLimited when the last try is still 429. Any other status is not retried, so a write the
  server may have applied is never repeated.
- check_networks.py names the reason in its FAIL finding and in verdicts.json (`fail_reason`).
- The planner tags every line it logs only because of that failure, ` (unclassified: <reason>)`, and
  counts them. A later pass that extends the line keeps the tag.

curl is a fake that answers from a script of statuses; the planner runs under node.
"""
from __future__ import annotations

import contextlib
import importlib.util
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
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
MONITOR = ROOT / "plugins/eng/skills/monitor-firebase/scripts"
PLANNER = ROOT / "plugins/eng/skills/monitor-rtdb-probes/scripts/plan-incident-log.js"
NODE = shutil.which("node")
sys.path.insert(0, str(MONITOR))
import common  # noqa: E402

FAKE_CURL = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
plan = json.load(open(os.environ["FAKE_PLAN"]))
n = int(open(os.environ["FAKE_COUNT"]).read() or 0) if os.path.exists(os.environ["FAKE_COUNT"]) else 0
open(os.environ["FAKE_COUNT"], "w").write(str(n + 1))
status, retry_after, body = plan[min(n, len(plan) - 1)]
head = f"HTTP/2 {status}\r\n" + (f"retry-after: {retry_after}\r\n" if retry_after is not None else "") + "\r\n"
open(args[args.index("-D") + 1], "w").write(head)
sys.stdout.write(body)
sys.exit(0 if status < 400 else 22)
'''


class CurlRetry(unittest.TestCase):
    def setUp(self):
        d = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        (d / "curl").write_text(FAKE_CURL, encoding="utf-8")
        (d / "curl").chmod(stat.S_IRWXU)
        self.plan, self.count = d / "plan.json", d / "count"
        env = {"PATH": f"{d}{os.pathsep}{os.environ['PATH']}", "FAKE_PLAN": str(self.plan),
               "FAKE_COUNT": str(self.count)}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        self.slept = []

    def call(self, plan, **kw):
        self.plan.write_text(json.dumps(plan), encoding="utf-8")
        return common.curl_json("https://api.notion.com/v1/x", user_agent="t", sleep=self.slept.append, **kw)

    def calls(self):
        return int(self.count.read_text())

    def test_a_429_is_retried_honouring_retry_after(self):
        rl = [429, 7, '{"code":"rate_limited"}']
        self.assertEqual(self.call([rl, rl, [200, None, '{"ok": true}']], retries_429=6), {"ok": True})
        self.assertEqual((self.calls(), self.slept), (3, [7, 7]))

    def test_without_retry_after_it_backs_off_and_caps(self):
        rl = [429, None, "{}"]
        self.call([rl] * 7 + [[200, None, "{}"]], retries_429=7)
        self.assertEqual(self.slept, [1, 2, 4, 8, 16, 32, 60])

    def test_still_429_raises_rate_limited(self):
        with self.assertRaises(common.RateLimited) as c:
            self.call([[429, 1, '{"code":"rate_limited"}']], retries_429=2)
        self.assertIn("after 3 tries", str(c.exception))
        self.assertEqual(self.calls(), 3)

    def test_other_errors_are_never_retried(self):
        with self.assertRaises(RuntimeError) as c:
            self.call([[502, None, "bad gateway"], [200, None, "{}"]], retries_429=6)
        self.assertNotIsInstance(c.exception, common.RateLimited)
        self.assertEqual((self.calls(), self.slept), (1, []))

    def test_no_retry_by_default(self):
        with self.assertRaises(common.RateLimited):
            self.call([[429, 1, "{}"], [200, None, "{}"]])
        self.assertEqual(self.calls(), 1)


class NetworkCheckReason(unittest.TestCase):
    def test_a_rate_limit_is_named_in_the_finding_and_the_verdicts(self):
        spec = importlib.util.spec_from_file_location("check_networks_429", MONITOR / "check_networks.py")
        cn = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cn)
        with tempfile.TemporaryDirectory() as d:
            detail = pathlib.Path(d, "detail.jsonl")
            detail.write_text(json.dumps({"t": "2026-11-03T02:33:00Z", "ip": "198.51.100.40", "node": "/coaches",
                                          "op": "listener-listen", "verdict": "Allowed", "tag": "rule exists",
                                          "ua": "Mozilla/5.0", "client": "desktop/mac browser", "bytes": 0}) + "\n",
                              encoding="utf-8")
            v = pathlib.Path(d, "v.json")
            with mock.patch.object(cn, "known", side_effect=common.RateLimited("HTTP 429 rate-limited after 7 tries")), \
                    mock.patch.object(sys, "argv", ["x", "--detail", str(detail), "--verdicts", str(v)]), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                cn.main()
            verdicts = json.loads(v.read_text(encoding="utf-8"))
        finding = json.loads(out.getvalue().splitlines()[0])
        self.assertEqual((finding["level"], finding["key"]), ("fail", "networks-notion"))
        self.assertIn("(Notion rate-limited)", finding["text"])
        self.assertNotIn("198.51.100.40", finding["text"])
        self.assertEqual((verdicts["fail"], verdicts["fail_reason"]), (["198.51.100.40"], "Notion rate-limited"))


@unittest.skipUnless(NODE, "node not installed")
class PlannerTag(unittest.TestCase):
    READ = {"t": "2026-11-03T02:33:00Z", "ip": "198.51.100.40", "node": "/coaches", "op": "listener-listen",
            "verdict": "Allowed", "kind": "Whole real node via listener", "tag": "rule exists",
            "client": "desktop/mac browser", "ua": "Mozilla/5.0", "bytes": 0}

    def plan(self, lines, verdicts, rows=()):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            (d / "d.jsonl").write_text("".join(json.dumps(l) + "\n" for l in lines), encoding="utf-8")
            (d / "r.json").write_text(json.dumps({"results": list(rows)}), encoding="utf-8")
            (d / "v.json").write_text(json.dumps(verdicts), encoding="utf-8")
            r = subprocess.run([NODE, str(PLANNER), str(d / "d.jsonl"), str(d / "r.json"), "--verdicts", str(d / "v.json")],
                               capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def what(self, p):
        return p["creates"][0]["properties"]["What they did"]["rich_text"][0]["text"]["content"]

    def test_a_read_logged_for_a_failed_check_is_tagged_and_counted(self):
        p = self.plan([self.READ], {"fail": ["198.51.100.40"], "fail_reason": "Notion rate-limited"})
        self.assertTrue(self.what(p).endswith("[Mozilla/5.0] (unclassified: Notion rate-limited)"), self.what(p))
        self.assertEqual((p["summary"]["unclassified_reads"], p["summary"]["unclassified_reason"]),
                         (1, "Notion rate-limited"))

    def test_an_alerted_read_is_not_tagged(self):
        p = self.plan([self.READ], {"alert": ["198.51.100.40"]})
        self.assertNotIn("unclassified", self.what(p))
        self.assertEqual(p["summary"]["unclassified_reads"], 0)

    def test_an_older_verdicts_file_still_tags_with_a_generic_reason(self):
        p = self.plan([self.READ], {"fail": ["198.51.100.40"]})
        self.assertIn("(unclassified: network check failed)", self.what(p))

    def test_extending_the_line_keeps_the_tag(self):
        first = self.plan([self.READ], {"fail": ["198.51.100.40"], "fail_reason": "Notion rate-limited"})
        props = first["creates"][0]["properties"]
        row = {"id": "page-1", "properties": {
            **{k: v for k, v in props.items() if k not in ("Detected (UTC)", "Ended (UTC)")},
            "Detected (UTC)": {"date": {"start": "2026-11-03T02:33:00.000Z"}},
            "Ended (UTC)": {"date": {"start": "2026-11-03T02:33:00.000Z"}},
            "Found by": {"rich_text": [{"plain_text": "someone else"}]}}}
        for v in row["properties"].values():
            for item in v.get("rich_text", []) + v.get("title", []):
                item.setdefault("plain_text", item.get("text", {}).get("content", ""))
        later = dict(self.READ, t="2026-11-03T02:40:00Z")
        p = self.plan([self.READ, later], {"fail": ["198.51.100.40"], "fail_reason": "Notion rate-limited"}, [row])
        line = p["updates"][0]["properties"]["What they did"]["rich_text"][0]["text"]["content"]
        self.assertIn("02:33-02:40Z 198.51.100.40: 2 allowed", line)
        self.assertTrue(line.endswith("(unclassified: Notion rate-limited)"), line)


if __name__ == "__main__":
    unittest.main()
