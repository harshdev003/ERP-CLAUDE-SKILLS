"""cleanup-orphaned-auth must never delete a held account.

An incident's accounts (here, a reporter's test accounts) look exactly like orphans, so
the only thing standing between them and `accounts:batchDelete` is the keep list. A missing
or empty list refuses the delete (ENG-1890); the other failures worth pinning are a broken
list read as an empty one, a list anyone can read, and a listed uid that still reaches the
delete call — in a dry run or a real one. No network:
the Identity Toolkit call is stubbed.
"""
from __future__ import annotations

import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_orphan_reconcile_claim_candidates import load  # noqa: E402

HELD = "heldUidHeldUidHeldUid0000001"
FREE = ["freeUidFreeUidFreeUid0000001", "freeUidFreeUidFreeUid0000002"]


class HeldUidsTest(unittest.TestCase):
    def setUp(self):
        self.mod = load()
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.held = self.tmp / "held.json"
        (self.tmp / "in.txt").write_text("\n".join([HELD, *FREE]) + "\n")

    def write_held(self, data, mode=0o600):
        self.held.write_text(data if isinstance(data, str) else json.dumps(data))
        self.held.chmod(mode)

    def main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", ["orphan_cleanup.py", *argv]), \
                mock.patch.dict(os.environ, {"HELD_UIDS_JSON": str(self.held)}), \
                redirect_stdout(out), redirect_stderr(err):
            try:
                self.mod.main()
                code = 0
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def delete(self, *extra):
        posted = []

        def post(url, body, bearer=None, headers=None):
            posted.extend(body["localIds"])
            return {}
        creds = mock.Mock(token="t")
        with mock.patch.object(self.mod, "_post", side_effect=post), \
                mock.patch.object(self.mod, "_creds", return_value=creds), \
                mock.patch.object(self.mod, "_lookup", side_effect=lambda uids: {u: {"localId": u} for u in uids}), \
                mock.patch.object(self.mod.time, "sleep"):
            code, out, err = self.main("delete", "--in", str(self.tmp / "in.txt"),
                                       "--done", str(self.tmp / "done.log"),
                                       "--backup", str(self.tmp / "backup.ndjson"), *extra)
        return code, out, err, posted

    def assert_refused(self, why):
        code, out, err, posted = self.delete("--execute")
        self.assertEqual(code, self.mod.HELD_EXIT)
        self.assertIn(why, err)
        self.assertEqual(posted, [])
        self.assertFalse((self.tmp / "backup.ndjson").exists())

    def test_missing_list_refuses(self):
        self.assert_refused("no keep list")

    def test_dry_run_refuses_without_the_list_too(self):
        code, _, err, posted = self.delete()
        self.assertEqual((code, posted), (self.mod.HELD_EXIT, []))
        self.assertIn("no keep list", err)

    def test_malformed_list_refuses(self):
        for bad in ("not json", {"uids": []}, [""], [1]):
            with self.subTest(bad=bad):
                self.write_held(bad)
                self.assert_refused("not JSON" if bad == "not json" else "JSON array of non-empty uid")

    def test_group_or_world_readable_list_refuses(self):
        self.write_held([HELD], mode=0o644)
        self.assert_refused("group or others")

    def test_held_uid_never_reaches_batch_delete(self):
        self.write_held([HELD])
        code, out, _, posted = self.delete("--execute")
        self.assertEqual(code, 0, out)
        self.assertEqual(sorted(posted), sorted(FREE))
        self.assertIn("kept: 1", out)
        self.assertIn(f"skip {HELD[:6]}…: keep list", out)
        self.assertNotIn(HELD, out)
        self.assertNotIn(HELD, (self.tmp / "backup.ndjson").read_text())

    def test_held_uid_skipped_in_dry_run_too(self):
        self.write_held([HELD])
        code, out, _, posted = self.delete()
        self.assertEqual((code, posted), (0, []))
        self.assertIn("kept: 1", out)
        self.assertIn("would delete 2 ", out)
        self.assertNotIn(HELD, out)

    def test_empty_list_refuses(self):
        self.write_held([])
        self.assert_refused("is empty")


if __name__ == "__main__":
    unittest.main()
