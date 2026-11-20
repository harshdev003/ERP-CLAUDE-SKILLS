"""Tests for `investigate-circleci`'s infra-vs-real classifier.

The classifier's value is entirely in which side of the line a log lands on: INFRA sends the caller
to a rerun, REAL sends them hunting a defect. Both wrong answers cost real time, and one of them —
"rerun this" on a genuinely broken build — costs it repeatedly.

Two properties are worth pinning rather than trusting to a substring list:

* **Order.** The REAL list matches `error: ` as a bare substring, and almost every failed download
  prints `error:` somewhere. So an INFRA signal only works because the INFRA list is scanned first;
  the download cases below would each return REAL under the opposite order.
* **Where the download signal is keyed.** curl exits 22 for any HTTP >= 400, so keying on the exit
  code would classify a 404 as infra. A missing asset or a version that no longer exists is a REAL
  defect, and no number of reruns fixes it — hence the 404 case here.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "plugins/eng/skills/investigate-circleci/scripts/investigate.py"

# A real log tail from a LivekitVoiceAgent deploy: the get.livekit.io installer's inner curl got a
# 503 from the GitHub release CDN, so `lk` never landed on PATH. Note the trailing `error:` — the
# REAL list matches it, so this log is exactly the case the INFRA-first ordering decides.
CDN_503_LOG = """\
sudo is required to install to /usr/local/bin
Installing livekit-cli 2.18.2
Downloading from https://github.com/livekit/livekit-cli/releases/download/v2.18.2/lk_2.18.2_linux_amd64.tar.gz...
curl: (22) The requested URL returned error: 503

Exited with code exit status 22
"""

# Same shape, but the asset genuinely isn't there — a wrong URL or a pinned version that was pulled.
# curl still exits 22; only the status distinguishes it, and it must NOT be called infra.
MISSING_ASSET_LOG = """\
Installing livekit-cli 9.9.9
Downloading from https://github.com/livekit/livekit-cli/releases/download/v9.9.9/lk_9.9.9_linux_amd64.tar.gz...
curl: (22) The requested URL returned error: 404

Exited with code exit status 22
"""


def load():
    spec = importlib.util.spec_from_file_location("circleci_investigate", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["circleci_investigate"] = mod
    spec.loader.exec_module(mod)
    return mod


class ClassificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.classify = staticmethod(load()._classify)

    def test_cdn_503_during_a_download_is_infra(self):
        verdict, reason = self.classify(CDN_503_LOG, 41.0)
        self.assertEqual("INFRA", verdict)
        self.assertIn("503", reason)

    def test_missing_asset_404_is_not_infra(self):
        # Same curl exit 22, opposite meaning: rerunning this forever would never make it pass.
        verdict, _ = self.classify(MISSING_ASSET_LOG, 41.0)
        self.assertNotEqual("INFRA", verdict)

    def test_status_line_form_is_infra(self):
        # Some tools print the status line instead of curl's message.
        for line in (
            "HTTP/1.1 503 Service Unavailable",
            "HTTP/1.1 502 Bad Gateway",
            "HTTP/2 429 Too Many Requests",
        ):
            with self.subTest(line=line):
                self.assertEqual("INFRA", self.classify(f"fetching...\n{line}\n", 30.0)[0])

    def test_curl_transport_failures_are_infra(self):
        for line in (
            "curl: (6) Could not resolve host: github.com",
            "curl: (7) Failed to connect to github.com port 443",
            "curl: (28) Operation timed out after 30001 milliseconds",
            "curl: (52) Empty reply from server",
            "curl: (56) Recv failure: Connection reset by peer",
        ):
            with self.subTest(line=line):
                self.assertEqual("INFRA", self.classify(line, 30.0)[0])

    def test_infra_wins_over_a_real_signal_in_the_same_log(self):
        # The ordering guarantee: `error:` is present in both logs below, and must not decide them.
        self.assertEqual("INFRA", self.classify("i/o timeout\nerror: something\n", 30.0)[0])
        self.assertEqual("INFRA", self.classify(CDN_503_LOG + "error: build failed\n", 30.0)[0])

    def test_genuine_failures_still_classify_real(self):
        self.assertEqual("REAL", self.classify("error: use of undeclared identifier 'foo'", 120.0)[0])
        self.assertEqual("REAL", self.classify("SwiftLint: line_length violation", 120.0)[0])

    def test_short_duration_still_wins(self):
        # The duration heuristic runs ahead of every content signal.
        verdict, reason = self.classify("error: use of undeclared identifier 'foo'", 2.0)
        self.assertEqual("INFRA", verdict)
        self.assertIn("2.0s", reason)

    def test_empty_log_is_unknown(self):
        self.assertEqual("UNKNOWN", self.classify("", None)[0])


if __name__ == "__main__":
    unittest.main()
