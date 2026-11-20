"""cleanup-orphaned-auth's harvest reads the verifyToken uid under both of its log keys.

user-service logged the sign-in uid as `tokenUserId` until ENGBACK-2797 renamed it
`signInUid` (the log redaction hides any key containing "token"). Entries under both keys
stay in the logs' window, so the harvest must read both. No network: the Cloud Logging
call is stubbed.
"""
from __future__ import annotations

import argparse
import io
import pathlib
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_orphan_reconcile_claim_candidates import load  # noqa: E402


class HarvestUidKeysTest(unittest.TestCase):
    def test_both_keys_and_the_other_paths_are_harvested(self):
        mod = load()
        out = pathlib.Path(tempfile.mkdtemp()) / "uids.txt"
        entries = [
            {"jsonPayload": {"signInUid": "uid-new", "message": "No profile for the token"}},
            {"jsonPayload": {"tokenUserId": "uid-old", "message": "No profile for the token"}},
            {"jsonPayload": {"verifiedToken": {"tokenUserId": "uid-mw"}, "message": "Invalid user id from decoded token"}},
            {"jsonPayload": {"userId": "uid-flat", "message": "Stored user no profile in database"}},
            {"jsonPayload": {"message": "No profile for the token"}},
        ]
        args = argparse.Namespace(since="1d", container="user-service", out=str(out))
        with mock.patch.object(mod, "_post", return_value={"entries": entries}), \
                mock.patch.object(mod, "_creds", return_value=mock.Mock(token="t")), \
                redirect_stdout(io.StringIO()):
            mod.cmd_harvest(args)
        self.assertEqual(out.read_text().split(), ["uid-flat", "uid-mw", "uid-new", "uid-old"])


if __name__ == "__main__":
    unittest.main()
