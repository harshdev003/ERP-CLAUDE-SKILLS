"""`eng:monitor-firebase` check_shallow_gets.py: the shallow REST get count from Cloud Monitoring.

The profiler's raw ops don't carry ?shallow=true: on 2026-11-09 at 13:00Z the rest_shallow_get metric
counted 103 shallow gets while the profile of the same minutes counted 0. The transport is stubbed at
common.curl_json; the fixtures are in the shape the timeSeries API returned for that hour.
"""

from __future__ import annotations

import contextlib
import io
import json
import pathlib
import sys
import unittest
from unittest import mock

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "plugins/eng/skills/monitor-firebase/scripts"
sys.path.insert(0, str(SCRIPTS))
import check_shallow_gets  # noqa: E402
import common  # noqa: E402


def series(op, values, start="2026-11-09T13:00"):
    return {"metric": {"type": check_shallow_gets.METRIC, "labels": {"operation_type": op}},
            "points": [{"interval": {"endTime": f"{start[:-2]}{int(start[-2:]) + i:02d}:00Z"},
                        "value": {"int64Value": str(v)}} for i, v in enumerate(values)]}


def run(response=None, error=None):
    out = io.StringIO()
    curl = mock.Mock(side_effect=error) if error else mock.Mock(return_value=response)
    with mock.patch.object(common, "curl_json", curl), mock.patch.object(common, "access_token", return_value="t"), \
            mock.patch.object(sys, "argv", ["check_shallow_gets.py", "--minutes", "10"]), contextlib.redirect_stdout(out):
        check_shallow_gets.main()
    return [json.loads(l) for l in out.getvalue().splitlines()], curl


class ShallowGets(unittest.TestCase):
    def test_the_1300z_burst_is_counted(self):
        fs, curl = run({"timeSeries": [series("rest_get", [40, 52]), series("rest_shallow_get", [103, 0]),
                                       series("realtime_listen", [9000, 9100])]})
        self.assertEqual([(f["key"], f["level"]) for f in fs], [("shallow-gets", "note")])
        self.assertIn("rest_shallow_get): 103 in the last 10 min (metric to 13:01Z)", fs[0]["text"])
        self.assertIn("allowed REST reads note", fs[0]["text"])
        self.assertIn("metric.label.operation_type", curl.call_args.kwargs["params"]["aggregation.groupByFields"])

    def test_no_shallow_series_is_a_verified_zero(self):
        fs, _ = run({"timeSeries": [series("rest_get", [3]), series("realtime_get", [500])]})
        self.assertIn("rest_shallow_get): 0 in the last 10 min", fs[0]["text"])
        self.assertEqual(fs[0]["level"], "note")

    def test_an_empty_read_is_a_fail_never_zero(self):
        fs, _ = run({})
        self.assertEqual([(f["key"], f["level"]) for f in fs], [("shallow-gets-empty", "fail")])

    def test_a_failed_read_is_a_fail(self):
        fs, _ = run(error=RuntimeError("HTTP failed (22): 403"))
        self.assertEqual([(f["key"], f["level"]) for f in fs], [("shallow-gets-read", "fail")])


if __name__ == "__main__":
    unittest.main()
