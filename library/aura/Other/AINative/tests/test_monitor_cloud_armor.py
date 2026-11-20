"""eng:monitor-cloud-armor — real-user checks on Cloud Armor logs (ENG-1873).

The checks run on the bundled fixture, and on hand-built entries for each exclusion; GCP is
stubbed at gcloud_sa.run, the one seam, so a failed read is shown to be exit 2, never clean.
No address may reach any output line.
"""

import contextlib
import importlib.util
import io
import json
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILL = ROOT / "plugins/eng/skills/monitor-cloud-armor"
spec = importlib.util.spec_from_file_location("_armor", SKILL / "scripts/armor.py")
armor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(armor)
CFG = armor.load_config()
FIXTURE = json.loads((SKILL / "assets/fixture-log.json").read_text())


def e(url, ip="203.0.113.50", ua="Mozilla/5.0 Chrome/131", status=403, prio=None, be="aura-web-user-web"):
    d = {"httpRequest": {"requestUrl": url, "remoteIp": ip, "userAgent": ua, "status": status},
         "resource": {"labels": {"backend_service_name": be}}, "jsonPayload": {}}
    if prio is not None:
        d["jsonPayload"]["enforcedSecurityPolicy"] = {"outcome": "DENY", "priority": prio}
    return d


class ChecksTest(unittest.TestCase):
    def test_the_fixture_flags_the_app_deny_and_the_static_throttles_only(self):
        a = armor.analyze(FIXTURE, CFG)
        self.assertEqual(a["flags"], ["app_ua", "static_throttle"])
        self.assertEqual(a["counts"]["e5xx"], 2)  # the /blog 5xx are Webflow's
        self.assertEqual((a["counts"]["api_throttle"], a["counts"]["coach_deny"]), (0, 0))

    def test_no_address_reaches_the_output(self):
        out = armor.line(armor.analyze(FIXTURE, CFG))
        for ip in {x["httpRequest"]["remoteIp"] for x in FIXTURE}:
            self.assertNotIn(ip, out)
        self.assertNotIn("https://", out)

    def test_api_throttles_flag_only_above_the_threshold_and_only_for_real_clients(self):
        hits = [e("https://www.aurahealth.io/api/log", status=429, prio=4000)] * 21
        self.assertIn("api_throttle", armor.analyze(hits, CFG)["flags"])
        self.assertNotIn("api_throttle", armor.analyze(hits[:20], CFG)["flags"])
        bare = [e("http://34.1.2.3/api/log", status=429, prio=4000)] * 30
        bots = [e("https://www.aurahealth.io/api/log", ua="ClaudeBot/1.0", status=429, prio=4000)] * 30
        scanner = [e("https://www.aurahealth.io/.env", ip="198.51.100.1", prio=200)] + \
                  [e("https://www.aurahealth.io/api/log", ip="198.51.100.1", status=429, prio=4000)] * 30
        for rows in (bare, bots, scanner):
            self.assertEqual(armor.analyze(rows, CFG)["counts"]["api_throttle"], 0)

    def test_a_rule_200_deny_of_our_app_is_not_a_real_user_signal(self):
        rows = [e("https://app.aurahealth.io/.git/config", ua="Aura/2.0", prio=200)]
        self.assertEqual(armor.analyze(rows, CFG)["counts"]["app_ua"], 0)

    def test_static_throttles_need_www_a_static_path_and_a_non_bot(self):
        js = "https://www.aurahealth.io/_next/static/x.js"
        self.assertEqual(armor.analyze([e(js, status=429, prio=4000)], CFG)["counts"]["static_throttle"], 1)
        for rows in ([e(js, ua="Googlebot/2.1", status=429, prio=4000)],
                     [e("https://coach.aurahealth.io/_next/static/x.js", status=429, prio=4000)],
                     [e("https://www.aurahealth.io/pricing", status=429, prio=4000)]):
            self.assertEqual(armor.analyze(rows, CFG)["counts"]["static_throttle"], 0)

    def test_static_throttles_leave_out_link_previews_and_scanners(self):
        img = "https://www.aurahealth.io/images/og.png"
        preview = [e(img, ua="facebookexternalhit/1.1", status=429, prio=4000),
                   e(img, ua="WhatsApp/2.23", status=429, prio=4000)]
        scanner = [e("https://www.aurahealth.io/.env", ip="198.51.100.1", prio=200),
                   e("https://www.aurahealth.io/favicon.ico", ip="198.51.100.1", status=429, prio=4000)]
        for rows in (preview, scanner):
            self.assertEqual(armor.analyze(rows, CFG)["counts"]["static_throttle"], 0)

    def test_an_in_app_browser_is_a_real_user(self):
        # Slack's, Discord's and LinkedIn's own browsers are people; their preview fetchers end in "bot".
        img = "https://www.aurahealth.io/images/og.png"
        for ua in ("Mozilla/5.0 Slack/4.41 Electron", "Mozilla/5.0 discord/1.0", "Mozilla/5.0 LinkedInApp"):
            self.assertEqual(armor.analyze([e(img, ua=ua, status=429, prio=4000)], CFG)["counts"]["static_throttle"], 1)

    def test_an_aurie_app_deny_is_our_app(self):
        rows = [e("https://app.aurahealth.io/auraServices/x", ua="Aurie/1.33 (iPhone)", prio=1050)]
        self.assertEqual(armor.analyze(rows, CFG)["counts"]["app_ua"], 1)
        self.assertEqual(armor.must_not_deny(rows, CFG)["our apps"], 1)

    def test_must_not_deny_ignores_scanner_path_denies(self):
        rows = [e("https://www.aurahealth.io/.git/config", ua="Googlebot/2.1", prio=200),
                e("https://www.aurahealth.io/.env", ua="Aura/2.0", prio=200)]
        self.assertEqual(sum(armor.must_not_deny(rows, CFG).values()), 0)

    def test_must_not_deny_names_the_category(self):
        rows = [e("https://www.aurahealth.io/x", ua="facebookexternalhit/1.1", prio=2910),
                e("https://www.aurahealth.io/.well-known/apple-app-site-association", prio=4000),
                e("https://www.aurahealth.io/y", ua="Googlebot/2.1")]  # served: no deny
        got = armor.must_not_deny(rows, CFG)
        self.assertEqual((got["link previews"], got["/.well-known/"], got["search engines"]), (1, 1, 0))

    def test_an_unlisted_rule_is_reported_not_guessed(self):
        policy = {"rules": [{"priority": 200}, {"priority": 120}, {"priority": 3500}, {"priority": 2147483647}]}
        self.assertEqual(armor.unknown_priorities(policy, CFG), [3500])

    def test_an_ipv6_bare_host_is_the_load_balancer_and_never_printed(self):
        v6 = "http://[2600:1901:0:1234::]/api/log"
        rows = [e(v6, status=429, prio=4000)] * 30
        a = armor.analyze(rows, CFG)
        self.assertEqual(a["counts"]["api_throttle"], 0)
        self.assertNotIn("2600", armor.line(a))
        self.assertEqual(armor.shape("http://34.1.2.3:80/api/log"), "ip /api/log")

    def test_route_shapes_mask_ids(self):
        self.assertEqual(armor.shape("https://coach.aurahealth.io/coaches/8f3a2b?x=1"), "coach /coaches/:x")
        self.assertEqual(armor.mask_ua("AuraConsumerApp/3.33.1 (iPhone; iOS 18.1)"), "AuraConsumerApp/ (iPhone; iOS )")


class ReadFailureTest(unittest.TestCase):
    def run_main(self, *argv, returncode=1, stdout=""):
        fake = subprocess.CompletedProcess(["gcloud"], returncode, stdout, "ERROR: (gcloud) Reauthentication failed")
        out = io.StringIO()
        with mock.patch.object(armor.gcloud_sa, "run", return_value=fake), \
                mock.patch.object(armor.gcloud_sa, "credential", return_value="GCP_KEY (sa)"), \
                mock.patch.object(sys, "argv", ["armor.py", *argv]), contextlib.redirect_stdout(out):
            code = armor.main()
        return code, out.getvalue()

    def test_a_failed_read_is_exit_2_and_never_clean(self):
        for mode in (["watch", "--checks", "1"], ["daily"]):
            code, out = self.run_main(*mode)
            self.assertEqual(code, 2)
            self.assertIn("READ FAILED", out)
            self.assertNotIn("clean", out)

    def test_the_selftest_passes(self):
        code, out = self.run_main("selftest")
        self.assertEqual(code, 0)
        self.assertIn("selftest ok", out)


if __name__ == "__main__":
    unittest.main()
