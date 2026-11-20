#!/usr/bin/env python3
"""`data:analyze-user-journal`'s fetch_journal.py: a rated journal is read by its footer id and user.

`GET /ai/journal/:id` answers only the entry's owner, and the k8bot `*JOURNAL RATED*` footer
`[id: <uuid>, user: <uid>]` carries the owner's full uid. So the script reads the row from that
user's `POST /ai/journals/list/:userId` (newest first, removed entries included) and never asks
the service who owns a row: the owner-lookup route is being removed.

Stdlib `unittest`, no network: the transport is stubbed at `_post`.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import pathlib
import sys
import unittest
import urllib.error
import uuid
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "plugins/data/skills/analyze-user-journal/scripts/fetch_journal.py"
JID = "0b6c2a3e-1f4d-4c5e-9a7b-8c9d0e1f2a3b"
UID = "TestUidTestUidTestUid0000001"
FOOTER = f"`[id: {JID}, user: {UID}]`"


def load():
    name = f"_fetch_journal_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def http_error(code):
    return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(b"{}"))


class Fetch(unittest.TestCase):
    def setUp(self):
        self.mod = load()
        self.calls = []

    def stub(self, pages):
        def fake(path, token, body):
            self.calls.append((path, body))
            page = pages[body["page"]]
            if isinstance(page, Exception):
                raise page
            return page
        return mock.patch.object(self.mod, "_post", fake)

    def test_reads_the_row_from_the_footer_users_list_and_never_looks_up_an_owner(self):
        row = {"id": JID, "userId": UID, "type": "daily", "title": "T", "rating": 1, "feedback": "f"}
        with self.stub([[{"id": "other"}, row]]):
            got = self.mod.fetch_journal(JID, UID, "t")
        self.assertEqual((got["journal_id"], got["user_id"], got["rating"], got["feedback"]), (JID, UID, 1, "f"))
        self.assertEqual([p for p, _ in self.calls], [f"/ai/journals/list/{UID}"])
        body = self.calls[0][1]
        self.assertTrue(body["includeRemoved"])  # a rated entry may since have been removed
        self.assertEqual(body["sort"], "desc")
        self.assertNotIn("owners", SCRIPT.read_text(), "the owner-lookup route is being removed")

    def test_pages_until_the_row_and_stops_on_a_short_page(self):
        full = [{"id": f"x{i}"} for i in range(self.mod.PAGE)]
        with self.stub([full, [{"id": JID, "userId": UID}]]):
            self.assertEqual(self.mod.fetch_journal(JID, UID, "t")["journal_id"], JID)
        self.assertEqual([b["page"] for _, b in self.calls], [0, 1])
        with self.stub([full, [{"id": "y"}]]), self.assertRaises(self.mod.FetchJournalError) as e:
            self.mod.fetch_journal(JID, UID, "t")
        self.assertIn("not in user", str(e.exception))
        self.assertNotIn(UID, str(e.exception))

    def test_list_errors_name_the_cause_without_the_uid(self):
        for code, words in ((404, "No user"), (403, "admin/agent"), (401, "admin/agent")):
            with self.subTest(code=code), self.stub([http_error(code)]), \
                    self.assertRaises(self.mod.FetchJournalError) as e:
                self.mod.fetch_journal(JID, UID, "t")
            self.assertIn(words, str(e.exception))
            self.assertNotIn(UID, str(e.exception))

    def test_the_footer_gives_both_ids(self):
        self.assertEqual(self.mod.parse_footer(f"*JOURNAL RATED* ... {FOOTER}"), (JID, UID))
        self.assertEqual(self.mod.parse_footer("[id: a-b, user: google:123]"), ("a-b", "google:123"))
        with self.assertRaises(self.mod.FetchJournalError):
            self.mod.parse_footer("no footer here")


class Main(unittest.TestCase):
    def run_main(self, *argv, rows=None):
        row = {"id": JID, "userId": UID, "type": "daily", "title": "T", "summary": "s"}
        out, calls = io.StringIO(), []

        def fake(path, token, body):
            calls.append(path)
            return rows if rows is not None else [row]
        with mock.patch.object(self.mod, "_post", fake), mock.patch.dict(os.environ, {"AURA_API_TOKEN": "t"}), \
                mock.patch.object(sys, "argv", ["x", *argv, "--json", os.devnull]), contextlib.redirect_stdout(out):
            self.mod.main()
        return out.getvalue(), calls

    def setUp(self):
        self.mod = load()

    def test_footer_or_journal_id_with_uid(self):
        for argv in (["--footer", FOOTER], ["--journal-id", JID, "--uid", UID]):
            with self.subTest(argv=argv[0]):
                out, calls = self.run_main(*argv)
                self.assertEqual(calls, [f"/ai/journals/list/{UID}"])
                self.assertIn(f"user={UID[:8]}…", out)
                self.assertNotIn(UID, out)

    def test_a_provider_uid_keeps_its_prefix(self):
        uid = "google:111555896239269076429"
        row = {"id": JID, "userId": uid, "type": "daily", "title": "T", "summary": "s"}
        out, _ = self.run_main("--journal-id", JID, "--uid", uid, rows=[row])
        self.assertIn("user=google:11155589…", out)
        self.assertNotIn(uid, out)

    def test_needs_the_owner(self):
        for argv in (["--journal-id", JID], ["--uid", UID], [], ["--footer", FOOTER, "--uid", UID]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.run_main(*argv)


if __name__ == "__main__":
    unittest.main()
