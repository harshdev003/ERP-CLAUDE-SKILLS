#!/usr/bin/env python3
"""`ip_location.py` — the ipgeolocation.io lookup, over every copy (canonical: eng:lookup-ip-location).

What has to hold:
- fields come from `location` and `asn` (ISP is `asn.organization`; `network` is paid-only);
- every failure is blank fields plus a reason code, never an exception;
- 423 is `private`, 429 is `quota`, 401/403 `auth`; a missing key is `no_key` and makes no request;
- one request per IP per process (a lookup is a credit), but quota and network failures are retried;
- only `apiKey` and `ip` are sent, and the CLI output never contains the IP or the key.

`requests.get` is the single chokepoint and is stubbed; no request leaves the machine.
"""
from __future__ import annotations

import io
import json
import os
import pathlib
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from reader_harness import load_reader, script_paths  # noqa: E402

import requests  # noqa: E402

BODY = {"ip": "203.0.113.7",
        "location": {"country_code2": "CA", "country_name": "Canada", "state_prov": "British Columbia",
                     "city": "West Vancouver"},
        "asn": {"as_number": "AS6327", "organization": "Shaw Communications Inc."}}


class Resp:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class Contract(unittest.TestCase):
    path: pathlib.Path = None  # type: ignore[assignment]

    def setUp(self):
        self.mod = load_reader(self.path, siblings=())
        self.calls = []
        env = mock.patch.dict(os.environ, {"IPGEOLOCATION_API_KEY": "k-secret"})
        env.start()
        self.addCleanup(env.stop)

    def serve(self, *responses):
        it = iter(responses)

        def get(url, params=None, timeout=None):
            self.calls.append((url, dict(params or {})))
            r = next(it)
            if isinstance(r, Exception):
                raise r
            return r
        return mock.patch.object(requests, "get", get)

    def test_fields_and_isp_from_asn_organization(self):
        with self.serve(Resp(200, BODY)):
            got = self.mod.lookup("203.0.113.7")
        self.assertEqual(got, {"country_code": "CA", "country": "Canada", "region": "British Columbia",
                               "city": "West Vancouver", "isp": "Shaw Communications Inc.", "asn": "6327",
                               "reason": "ok"})
        self.assertEqual(self.calls, [("https://api.ipgeolocation.io/v3/ipgeo",
                                       {"apiKey": "k-secret", "ip": "203.0.113.7"})])

    def test_status_codes_map_to_reasons(self):
        for status, reason in ((423, "private"), (429, "quota"), (401, "auth"), (403, "auth"), (500, "http_500")):
            self.mod._CACHE.clear()
            with self.serve(Resp(status)):
                got = self.mod.lookup("203.0.113.7")
            self.assertEqual(got["reason"], reason)
            self.assertEqual(got["city"], "")

    def test_failures_never_raise(self):
        with self.serve(requests.ConnectionError("down"), Resp(200, None), Resp(200, {"location": "x"}),
                        Resp(200, ["a"]), Resp(200, "text")):
            self.assertEqual(self.mod.lookup("203.0.113.1")["reason"], "network")
            self.assertEqual(self.mod.lookup("203.0.113.2")["reason"], "bad_json")
            self.assertEqual(self.mod.lookup("203.0.113.3")["reason"], "bad_json")
            self.assertEqual(self.mod.lookup("203.0.113.4")["reason"], "bad_json")
            self.assertEqual(self.mod.lookup("203.0.113.5")["reason"], "bad_json")

    def test_no_key_makes_no_request(self):
        with mock.patch.dict(os.environ, {"IPGEOLOCATION_API_KEY": ""}), self.serve():
            self.assertEqual(self.mod.lookup("203.0.113.7")["reason"], "no_key")
        self.assertEqual(self.calls, [])

    def test_one_request_per_ip_but_quota_is_retried(self):
        with self.serve(Resp(200, BODY)):
            got = self.mod.lookup_many(["203.0.113.7", "203.0.113.7"])
            self.mod.lookup("203.0.113.7")
        self.assertEqual((len(got), len(self.calls)), (1, 1))
        with self.serve(Resp(429), Resp(200, BODY)):
            self.assertEqual(self.mod.lookup("198.51.100.4")["reason"], "quota")
            self.assertEqual(self.mod.lookup("198.51.100.4")["reason"], "ok")

    def test_cli_output_carries_no_ip_or_key(self):
        buf = io.StringIO()
        with self.serve(Resp(200, BODY), Resp(423)), \
                mock.patch.object(sys, "argv", ["ip_location.py", "203.0.113.7", "10.0.0.1"]), redirect_stdout(buf):
            self.mod.main()
        out = buf.getvalue()
        self.assertEqual([r["reason"] for r in json.loads(out)], ["ok", "private"])
        for secret in ("203.0.113.7", "10.0.0.1", "k-secret"):
            self.assertNotIn(secret, out)


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    paths = script_paths("ip_location.py")
    if not paths:
        raise AssertionError("no ip_location.py found")
    for path in paths:
        cls = type(f"IpLocation_{path.parent.parent.name.replace('-', '_')}", (Contract,),
                   {"path": path, "__module__": __name__})
        suite.addTests(loader.loadTestsFromTestCase(cls))
    return suite


if __name__ == "__main__":
    unittest.main()
