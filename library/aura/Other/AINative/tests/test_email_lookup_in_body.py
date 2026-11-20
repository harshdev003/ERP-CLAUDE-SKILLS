#!/usr/bin/env python3
"""A user lookup by email sends the address in the request body, never in a URL.

**The rule.** UserService locks `GET /users/email/:email` to admin and service roles, and an email in
a URL lands in every HTTP client's, proxy's and load balancer's logs. Skills look a user up through
`POST /users/email/lookup` with `{"email": …}` in the body (service role; agent once UserService allows it): the same
email -> profile answer, 200 when it resolves, 404 when it doesn't.

Pinned for each caller: `eng:cleanup-orphaned-auth` (`_email_resolves`), `eng:delete-facebook-user-data`
(`plan`), and a repo-wide scan that no script or documented curl builds a `/users/email/<address>` URL.

Stdlib `unittest`, no network: `urllib.request.urlopen` is stubbed and every request it gets is kept.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import re
import sys
import tempfile
import unittest
import urllib.error
import uuid
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[1]
EMAIL = "someone.real@example.com"
LOOKUP = "https://services.aurahealth.io/users/email/lookup"


def load(rel):
    name = f"_mod_{uuid.uuid4().hex}"
    path = REPO / rel
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(path.parent))
    return mod


class _Resp(io.BytesIO):
    def __init__(self, status, body):
        super().__init__(json.dumps(body).encode())
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Recorder:
    """Stands in for urlopen: answers by (method, url) and keeps every request."""

    def __init__(self, answers):
        self.answers, self.requests = answers, []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        status, body = self.answers(req)
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, "x", {}, io.BytesIO(json.dumps(body).encode()))
        return _Resp(status, body)

    def assert_no_email_in_a_url(self, case):
        for r in self.requests:
            for variant in (EMAIL, EMAIL.replace("@", "%40")):
                case.assertNotIn(variant, r.full_url)


def lookup_body(req):
    case = unittest.TestCase()
    case.assertEqual((req.get_method(), req.full_url), ("POST", LOOKUP))
    case.assertEqual(req.get_header("Content-type"), "application/json")
    return json.loads(req.data.decode())


class CleanupOrphanedAuth(unittest.TestCase):
    def setUp(self):
        self.mod = load("plugins/eng/skills/cleanup-orphaned-auth/scripts/orphan_cleanup.py")
        self.enterContext(mock.patch.dict(os.environ, {"AURA_API_TOKEN": "t", "AINATIVE_UA_WHO": "tester"}))

    def resolve(self, status):
        rec = Recorder(lambda req: (status, {}))
        with mock.patch("urllib.request.urlopen", rec):
            try:
                return self.mod._email_resolves(EMAIL), rec
            except urllib.error.HTTPError as e:
                return e, rec

    def test_the_email_goes_in_the_body(self):
        got, rec = self.resolve(200)
        self.assertIs(got, True)
        (req,) = rec.requests
        self.assertEqual(lookup_body(req), {"email": EMAIL})
        self.assertEqual(req.get_header("Authorization"), "Bearer t")
        rec.assert_no_email_in_a_url(self)

    def test_404_is_no_profile_and_any_other_error_raises(self):
        self.assertIs(self.resolve(404)[0], False)
        self.assertIsInstance(self.resolve(500)[0], urllib.error.HTTPError)
        self.assertIsInstance(self.resolve(403)[0], urllib.error.HTTPError)

    def test_the_orphan_check_never_puts_an_email_in_a_url(self):
        def answer(req):
            return (404, {}) if req.get_method() == "GET" else (404, {})
        rec = Recorder(answer)
        with mock.patch("urllib.request.urlopen", rec):
            self.assertTrue(self.mod._is_orphan_readonly("uid123", [EMAIL, "other@example.org"]))
        self.assertEqual([r.get_method() for r in rec.requests], ["GET", "POST", "POST"])
        rec.assert_no_email_in_a_url(self)


class DeleteFacebookUserData(unittest.TestCase):
    def setUp(self):
        self.mod = load("plugins/eng/skills/delete-facebook-user-data/scripts/fb_deletion.py")
        self.enterContext(mock.patch.dict(os.environ, {"AURA_API_TOKEN": "t", "AINATIVE_UA_WHO": "tester"}))
        self.tmp = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))

    def plan(self, lookup_status):
        (self.tmp / "batch1.csv").write_text("1234567890\n")
        user = {"localId": "fbUid0000000000000000000001", "email": EMAIL,
                "providerUserInfo": [{"providerId": "facebook.com", "email": EMAIL}]}

        def answer(req):
            if req.full_url == LOOKUP:
                return lookup_status, {"id": "mainProfile00000000000000001"} if lookup_status == 200 else {}
            return 404, {}  # GET /users/<uid>: no profile under this uid
        rec = Recorder(answer)
        out = self.tmp / "plan.json"
        with mock.patch.object(self.mod, "_idtk", return_value={"users": [user]}), \
                mock.patch("urllib.request.urlopen", rec), contextlib.redirect_stdout(io.StringIO()):
            self.mod.cmd_plan(argparse.Namespace(csv=[str(self.tmp / "batch1.csv")], out=str(out)))
        return json.loads(out.read_text()), rec

    def test_a_secondary_login_is_found_through_the_body_lookup(self):
        (row,), rec = self.plan(200)
        self.assertEqual((row["class"], row["main_profile"]), ("secondary", "mainProfile00000000000000001"))
        self.assertEqual(lookup_body(rec.requests[-1]), {"email": EMAIL})
        rec.assert_no_email_in_a_url(self)

    def test_no_profile_is_an_orphan_and_an_error_is_named(self):
        self.assertEqual(self.plan(404)[0][0]["class"], "orphan")
        (row,), _ = self.plan(403)
        self.assertEqual((row["class"], row["error"]), ("error", "POST /users/email/lookup -> 403"))


class NoEmailUrlAnywhere(unittest.TestCase):
    """No script, and no curl in a SKILL.md, builds a URL that carries an email address.

    Three shapes: a URL or curl line with `users/email/<anything but lookup or the :email route
    name>`, a `"/email/"` or `"users/email/"` literal an address is appended to, and an f-string or
    template that fills one in. A docstring showing a templated path (`/users/email/:email`) is not
    a URL builder and passes."""

    SHAPES = (
        re.compile(r"(https?://|curl\b).*users/email/(?!lookup\b|:email\b)"),
        re.compile(r"[\"'](?:/users)?/email/[\"']"),
        re.compile(r"users/email/(?:\{|\$\{)"),
    )

    def bad(self, line):
        return any(p.search(line) for p in self.SHAPES)

    def test_scan(self):
        hits, scanned = [], 0
        for path in (REPO / "plugins").rglob("*"):
            rel = path.relative_to(REPO)
            if not path.is_file() or ".claude" in rel.parts:
                continue
            if path.suffix in (".py", ".js", ".sh"):
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            elif path.name == "SKILL.md":
                lines = path.read_text(encoding="utf-8").splitlines()
            else:
                continue
            scanned += 1
            hits += [f"{rel}: {ln.strip()[:120]}" for ln in lines if self.bad(ln)]
        self.assertGreater(scanned, 100, "the scan must actually read the plugin files")
        self.assertEqual(hits, [])

    def test_the_scan_catches_the_old_shapes(self):
        for old in ('USERS_EMAIL_URL = "https://services.aurahealth.io/users/email/"',
                    'curl -sS -X GET "https://services.aurahealth.io/users/email/<email>"',
                    'c2, body = _aura("GET", "/email/" + urllib.parse.quote(e, safe=""), read_tok)',
                    'url = f"{BASE}/users/email/{email}"',
                    'fetch(`${base}/users/email/${email}`)'):
            self.assertTrue(self.bad(old), old)
        for fine in ('POST "https://services.aurahealth.io/users/email/lookup"',
                     '"""`/users/email/a@b.io` -> `/users/email/:email`"""',
                     "- Tools/Aura API  # GET /users/:id, POST /users/email/lookup"):
            self.assertFalse(self.bad(fine), fine)


if __name__ == "__main__":
    unittest.main()
