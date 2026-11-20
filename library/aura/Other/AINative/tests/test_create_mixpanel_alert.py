"""eng:create-mixpanel-alert — spec guards, the rolling-window shape, and board re-attachment.

The transport is stubbed at its one chokepoint (`http`), so no Mixpanel call is made.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILL = ROOT / "plugins/eng/skills/create-mixpanel-alert"
spec_ = importlib.util.spec_from_file_location("mixpanel_reports", SKILL / "scripts/mixpanel_reports.py")
mr = importlib.util.module_from_spec(spec_)
spec_.loader.exec_module(mr)

EXAMPLE = json.loads((SKILL / "assets/spec-example.json").read_text(encoding="utf-8"))


def alert(**kw):
    a = {"key": "X1", "name": "X renewals stopped 12h", "event": "Subscription Renewed", "unit": "hour",
         "rolling": 12, "condition": "below 0.01", "check": "hourly", "channels": ["#c"]}
    a.update(kw)
    return a


class Spec(unittest.TestCase):
    def test_example_spec_is_valid(self):
        mr.validate(EXAMPLE)

    def test_refuses_a_filter_that_names_a_person(self):
        for prop, val in (("UserEmail", "x"), ("$user_id", "abc"), ("distinct_id", "abc"), ("note", "a@b.co")):
            with self.subTest(prop=prop), self.assertRaises(mr.SpecError):
                mr.validate({"board": "b", "alerts": [alert(filters={prop: val})]})

    def test_refuses_below_one_on_a_rolling_rule(self):
        with self.assertRaises(mr.SpecError):
            mr.validate({"board": "b", "alerts": [alert(condition="below 1")]})

    def test_refuses_a_rolling_threshold_one_event_would_cross(self):
        # one event in 24 hours averages 0.042, so "below 0.05" fires on a single renewal
        with self.assertRaises(mr.SpecError):
            mr.validate({"board": "b", "alerts": [alert(rolling=24, condition="below 0.05")]})
        mr.validate({"board": "b", "alerts": [alert(rolling=24, condition="below 0.01")]})

    def test_refuses_duplicate_names(self):
        with self.assertRaises(mr.SpecError):
            mr.validate({"board": "b", "alerts": [alert(), alert(key="X2")]})


class Params(unittest.TestCase):
    def test_rolling_lives_in_display_options_never_in_measurement(self):
        p = mr.params(alert())
        self.assertEqual(p["displayOptions"]["analysis"], "rolling")
        self.assertEqual(p["displayOptions"]["rollingWindowSize"], 12)
        self.assertIsNone(p["sections"]["show"][0]["measurement"]["rolling"])

    def test_no_rolling_stays_linear(self):
        p = mr.params(alert(rolling=None, condition="above 25"))
        self.assertEqual(p["displayOptions"]["analysis"], "linear")
        self.assertNotIn("rollingWindowSize", p["displayOptions"])

    def test_filter_is_an_equals_on_the_event_property(self):
        f = mr.params(alert(filters={"provider": "stripe"}))["sections"]["show"][0]["behavior"]["filters"][0]
        self.assertEqual((f["value"], f["filterOperator"], f["filterValue"]), ("provider", "equals", ["stripe"]))


class CurlFallback(unittest.TestCase):
    def test_body_goes_after_the_method_and_output_is_utf8(self):
        with mock.patch.object(mr, "_auth", return_value=("u", "s", "963821")), \
             mock.patch.dict("sys.modules", {"requests": None}), \
             mock.patch.object(mr.subprocess, "run") as run:
            run.return_value = mock.Mock(stdout='{"results": {"id": 1}}')
            mr.http("PATCH", "/api/app/projects/963821/bookmarks/1", {"dashboard_id": 7})
        cmd, kw = run.call_args.args[0], run.call_args.kwargs
        self.assertEqual(cmd[cmd.index("-X") + 1], "PATCH")
        self.assertEqual(cmd[cmd.index("--data") + 1], '{"dashboard_id": 7}')
        self.assertTrue(cmd[-1].startswith("https://mixpanel.com/"))
        self.assertEqual(kw.get("encoding"), "utf-8")


class Apply(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(mr, "_auth", return_value=("u", "s", "963821"))
        p.start()
        self.addCleanup(p.stop)

    def run_apply(self, create_returns_board: bool, board_exists=True, create_board=False, listing=None,
                  ticket=None):
        calls = []
        self.listing = listing or []
        self.stored_board = None  # the stored report starts off the board whatever the reply says

        def http(method, path, body=None):
            calls.append((method, path, body))
            if path.endswith("/dashboards/") and method == "GET":
                return {"results": [{"id": 7, "title": "b"}] if board_exists else []}
            if path.endswith("/dashboards/") and method == "POST":
                return {"results": {"id": 7, "title": "b"}}
            if path.endswith("/bookmarks/") and method == "GET":
                return {"results": self.listing}
            if "/bookmarks/11" in path and method == "GET":
                return {"results": {"id": 11, "dashboard_id": self.stored_board}}
            if path.endswith("/bookmarks/") and method == "POST":
                return {"results": {"id": 11, "dashboard_id": 7 if create_returns_board else None}}
            if "/bookmarks/11" in path and method == "PATCH":
                if "dashboard_id" in body:
                    self.stored_board = body["dashboard_id"]
                return {"results": {"id": 11, "dashboard_id": self.stored_board}}
            if path.startswith("/api/query/insights"):
                return {"series": {"Total": {"all": {"2026-11-20T00:00:00": 1.5}}}}
            raise AssertionError(f"unexpected call {method} {path}")

        with mock.patch.object(mr, "http", side_effect=http):
            out = mr.apply({"board": "b", "ticket": ticket, "alerts": [alert()]}, create_board)
        return out, calls

    def test_a_report_created_off_the_board_is_reattached(self):
        out, calls = self.run_apply(create_returns_board=False)
        self.assertIn(("PATCH", "/api/app/projects/963821/bookmarks/11", {"dashboard_id": 7}), calls)
        self.assertTrue(out[1]["on_board"])
        self.assertTrue(out[1]["ok"])

    def test_attach_is_patched_even_when_the_reply_claims_the_board(self):
        out, calls = self.run_apply(create_returns_board=True)
        self.assertIn(("PATCH", "/api/app/projects/963821/bookmarks/11", {"dashboard_id": 7}), calls)
        self.assertTrue(out[1]["on_board"])

    def test_a_rerun_adopts_a_stray_instead_of_duplicating(self):
        stray = [{"id": 11, "name": "X renewals stopped 12h", "dashboard_id": None,
                  "description": "T-1 normal alert. …"}]
        out, calls = self.run_apply(create_returns_board=False, listing=stray, ticket="T-1")
        self.assertFalse([c for c in calls if c[0] == "POST" and c[1].endswith("/bookmarks/")])
        self.assertEqual(out[1]["action"], "updated")
        self.assertTrue(out[1]["on_board"])

    def test_someone_elses_same_named_report_is_never_adopted(self):
        other = [{"id": 11, "name": "X renewals stopped 12h", "dashboard_id": None, "description": "mine"}]
        _, calls = self.run_apply(create_returns_board=False, listing=other, ticket="T-1")
        self.assertTrue([c for c in calls if c[0] == "POST" and c[1].endswith("/bookmarks/")])

    def test_plan_names_the_stray_it_will_adopt(self):
        stray = [{"id": 11, "name": "X renewals stopped 12h", "dashboard_id": None, "description": "T-1 x"}]
        with mock.patch.object(mr, "http", side_effect=lambda m, p, b=None: (
                {"results": [{"id": 7, "title": "b"}]} if p.endswith("/dashboards/") else {"results": stray})):
            rows = mr.plan({"board": "b", "ticket": "T-1", "alerts": [alert()]})
        self.assertEqual(rows[1]["action"], "adopt stray 11 and attach")

    def test_missing_board_needs_the_flag(self):
        with self.assertRaises(SystemExit):
            self.run_apply(create_returns_board=True, board_exists=False)

    def test_never_deletes(self):
        _, calls = self.run_apply(create_returns_board=False, board_exists=False, create_board=True)
        self.assertFalse([c for c in calls if c[0] == "DELETE"])


if __name__ == "__main__":
    unittest.main()
