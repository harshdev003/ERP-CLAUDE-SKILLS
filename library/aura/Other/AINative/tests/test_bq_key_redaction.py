#!/usr/bin/env python3
"""`bq.py` must never put the GCP_KEY value into an error message.

**The defect.** `_load_gcp_key` falls back to treating GCP_KEY as a file path, and the
resulting exception was interpolated whole:

    raise BQUnavailable(f"GCP_KEY set but not JSON or a readable path ({e})")

When GCP_KEY holds the service-account JSON (the normal case) rather than a path,
`open(raw)` raises `OSError: [Errno 63] File name too long: '<the entire key>'`. So a
*malformed* GCP_KEY — a shell-mangled newline, a truncated paste, a CI env that re-quotes
it — printed the **private key** to stderr, wherever stderr lands: terminal scrollback,
CI logs, a captured output file, a traceback pasted into Slack or a PR. Observed live on
2026-09-16. All 26 copies carried it. PROD-6111.

**Why the happy path hid it.** A well-formed key parses as JSON on the first try and the
fallback never runs, so no amount of ordinary use reaches the leak. It only fires when
something is already going wrong — which is exactly when people copy the output somewhere
to ask for help.

**The assertion that matters is the negative one**: the message must contain *no*
substring of the value. Asserting "the message mentions GCP_KEY" or "an error was raised"
passes against the leaking version. Diagnosability is checked separately — the exception
type and the value's length must survive, because a truncated paste and a mangled newline
need to stay distinguishable without showing the key.

Parameterised over all 26 copies: this is behaviour a `diff -q` cannot see, since every
copy could agree and still leak.

Stdlib unittest, no network, no real credentials.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
COPIES = sorted(REPO.glob("plugins/**/bq.py"))

# A realistic service-account blob with a marker in the secret position. Never a real key.
MARKER = "MARKER_SECRET_MATERIAL_DO_NOT_LEAK"
FAKE_KEY = (
    '{"type": "service_account", "project_id": "p", '
    '"private_key": "-----BEGIN PRIVATE KEY-----\\n' + MARKER + '\\n-----END PRIVATE KEY-----\\n", '
    '"client_email": "<redacted-email>"}'
)


def _load(path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(f"bq_{abs(hash(str(path)))}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestKeyNeverAppearsInErrors(unittest.TestCase):
    def setUp(self):
        self._prev = os.environ.get("GCP_KEY")

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("GCP_KEY", None)
        else:
            os.environ["GCP_KEY"] = self._prev

    def test_there_are_copies_to_check(self):
        """Guards against the whole suite passing vacuously on a glob that matches none."""
        self.assertGreaterEqual(len(COPIES), 20, f"only found {len(COPIES)} bq.py copies")

    def test_malformed_key_error_leaks_nothing(self):
        """THE load-bearing assertion. A message that names the var but not the value is
        the fix; a message that interpolates the OSError is the defect."""
        # JSON-shaped but not valid JSON (single quotes) -> json.loads fails ->
        # open(raw) fails -> the historic leak path.
        broken = FAKE_KEY.replace('"', "'")
        for path in COPIES:
            with self.subTest(copy=str(path.relative_to(REPO))):
                mod = _load(path)
                os.environ["GCP_KEY"] = broken
                with self.assertRaises(mod.BQUnavailable) as ctx:
                    mod._load_gcp_key()
                msg = str(ctx.exception)
                self.assertNotIn(MARKER, msg, "the secret leaked into the error message")
                self.assertNotIn("PRIVATE KEY", msg)
                self.assertNotIn("service_account", msg)
                self.assertNotIn("iam.gserviceaccount.com", msg)
                # The message is not the only surface. `raise ... from None` does NOT
                # clear __context__ — it sets __suppress_context__, which is what stops
                # the traceback printing the chained OSError. Assert the flag, and assert
                # the rendered traceback separately below.
                self.assertTrue(ctx.exception.__suppress_context__,
                                "context not suppressed; a default traceback would print "
                                "the chained OSError, which embeds the key")

    def test_the_rendered_traceback_leaks_nothing(self):
        """`str(exc)` being clean is NOT enough, and checking only that is how the first
        cut of this fix passed while still leaking.

        Raising inside `except ... as e` sets `__context__`, so Python prints the original
        `OSError` — which embeds the whole key — above the redacted message under "During
        handling of the above exception". The message was redacted; the traceback was not.
        `raise ... from None` is what actually closes it, and only a rendered traceback
        can tell the difference."""
        import traceback
        broken = FAKE_KEY.replace('"', "'")
        for path in COPIES:
            with self.subTest(copy=str(path.relative_to(REPO))):
                mod = _load(path)
                os.environ["GCP_KEY"] = broken
                try:
                    mod._load_gcp_key()
                except Exception:  # noqa: BLE001
                    tb = traceback.format_exc()
                else:
                    self.fail("expected a failure")
                self.assertNotIn(MARKER, tb, "the key leaked via the chained exception")
                self.assertNotIn("PRIVATE KEY", tb)
                self.assertNotIn("During handling", tb)

    def test_error_stays_diagnosable(self):
        """Redaction must not make the failure unreadable: the exception type and the
        value's length distinguish a truncated paste from a mangled newline."""
        broken = FAKE_KEY.replace('"', "'")
        mod = _load(COPIES[0])
        os.environ["GCP_KEY"] = broken
        with self.assertRaises(mod.BQUnavailable) as ctx:
            mod._load_gcp_key()
        msg = str(ctx.exception)
        self.assertIn("GCP_KEY", msg)
        self.assertIn(str(len(broken)), msg, "value length should be reported")
        self.assertTrue(any(t in msg for t in ("Error", "OSError", "FileNotFound")),
                        f"exception type should be named: {msg}")

    def test_valid_key_still_parses(self):
        """The redaction must not break the path that actually works."""
        for path in COPIES[:3]:
            with self.subTest(copy=str(path.relative_to(REPO))):
                mod = _load(path)
                os.environ["GCP_KEY"] = FAKE_KEY
                info = mod._load_gcp_key()
                self.assertEqual(info["client_email"], "<redacted-email>")

    def test_unset_key_still_reports_clearly(self):
        mod = _load(COPIES[0])
        os.environ.pop("GCP_KEY", None)
        with self.assertRaises(mod.BQUnavailable) as ctx:
            mod._load_gcp_key()
        self.assertIn("GCP_KEY", str(ctx.exception))

    def test_the_marker_would_actually_be_detected(self):
        """Proves the detection works — without this, the leak assertions could pass
        because the marker never survives into any message under any implementation."""
        broken = FAKE_KEY.replace('"', "'")
        try:
            open(broken)
        except Exception as e:  # noqa: BLE001
            self.assertIn(MARKER, str(e),
                          "the OSError no longer embeds the value; this test's premise "
                          "is stale and the assertions above may be vacuous")


if __name__ == "__main__":
    unittest.main()
