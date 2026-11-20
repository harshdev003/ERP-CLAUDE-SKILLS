#!/usr/bin/env python3
"""`reconcile` writes the userId-claim candidates, and never a live profile's own uid.

**The rule.** A secondary login (an Auth account whose email belongs to another, live
profile) is never a delete candidate, but it is exactly the input of the FirebaseAuth
userId-claim backfill. `reconcile` writes them to `reconcile_claim_candidates.txt`: the
`sso_resolvable` bucket (email matches an active profile) plus the emailUser-mapped orphans
(the claim resolves through `/emailUser`, so those need it too). A uid that owns its profile,
a profile-less orphan, or an account mapped to a *deleted* profile must not appear: the
backfill would re-resolve and skip them, but a candidate list that silently mixes delete
candidates in is the kind of file someone later feeds to `delete`.

Stdlib `unittest`, no network — `reconcile` only reads local files.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import unittest
import uuid

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "plugins/eng/skills/cleanup-orphaned-auth/scripts/orphan_cleanup.py"


def load():
    name = f"_orphan_cleanup_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def write_csv(path, header, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


class _ReconcileHarness:
    def run_reconcile(self, accounts, users, emailusers):
        mod = load()
        tmp = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        export = tmp / "export.ndjson"
        export.write_text("".join(json.dumps(a) + "\n" for a in accounts))
        write_csv(tmp / "user.csv", ["id", "email", "deletedAt"], users)
        write_csv(tmp / "emailuser.csv", ["email", "userId"], emailusers)
        args = argparse.Namespace(export=str(export), user_csv=str(tmp / "user.csv"),
                                  emailuser_csv=str(tmp / "emailuser.csv"), out_dir=str(tmp / "out"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            mod.cmd_reconcile(args)
        self.out_dir = tmp / "out"
        path = tmp / "out" / "reconcile_claim_candidates.txt"
        return path.read_text().split(), out.getvalue()

    def list_file(self, name):
        return (self.out_dir / name).read_text().split()


class ReconcileClaimCandidates(_ReconcileHarness, unittest.TestCase):

    def test_writes_secondary_logins_only(self):
        accounts = [
            {"uid": "owner", "email": "a@x.io"},                                       # healthy
            {"uid": "googleSecondary", "providerData": [{"email": "A@x.io"}]},         # sso_resolvable
            {"uid": "mappedSecondary", "providerData": [{"email": "alias@x.io"}]},     # emailUser-mapped
            {"uid": "profileless", "email": "nobody@x.io"},                            # orphan, delete candidate
            {"uid": "deletedMatch", "email": "gone@x.io"},                             # matches a deleted profile
            {"uid": "staleMapping", "email": "stale@x.io"},                            # emailUser -> deleted profile
            {"uid": "anonymous"},                                                      # no email
        ]
        users = [["owner", "a@x.io", ""], ["goneProfile", "gone@x.io", "2026-01-01"]]
        emailusers = [["alias@x.io", "owner"], ["stale@x.io", "goneProfile"]]
        uids, out = self.run_reconcile(accounts, users, emailusers)
        self.assertEqual(uids, ["googleSecondary", "mappedSecondary"])
        self.assertIn("userId-claim candidates (sso-resolvable + emailUser-mapped): 2", out)

    def test_empty_list_is_an_empty_file(self):
        uids, _ = self.run_reconcile([{"uid": "owner", "email": "a@x.io"}], [["owner", "a@x.io", ""]], [])
        self.assertEqual(uids, [])


class ReconcileAmbiguityAndCoverage(_ReconcileHarness, unittest.TestCase):
    """ENGBACK-1764: an account whose emails resolve to MORE than one active profile is
    ambiguous and must never reach the backfill; claims already on the export are counted."""

    USERS = [
        ["p1", "one@x.io", ""],
        ["p2", "two@x.io", ""],
        ["p3", "three@x.io", ""],
        ["gone", "gone@x.io", "2026-01-01"],
    ]
    EMAILUSERS = [
        ["alias-two@x.io", "p2"],
        ["alias-gone@x.io", "gone"],       # deleted target: must be ignored
        ["alias-one@x.io", "p1"],
    ]

    def fixture(self):
        return [
            {"uid": "p1", "email": "one@x.io"},                                       # owns profile
            {"uid": "single", "providerEmails": ["ONE@X.io"]},                         # mixed case -> p1
            {"uid": "ambiguous", "email": "one@x.io",
             "providerUserInfo": [{"email": "Alias-Two@x.io"}]},                       # p1 + p2
            {"uid": "stamped", "email": "three@x.io",
             "customAttributes": json.dumps({"userId": "p3"})},                       # already stamped
            {"uid": "stale", "email": "alias-one@x.io",
             "customClaims": {"userId": "p9"}},                                       # stale claim
            {"uid": "deletedMap", "email": "x@x.io",
             "providerData": [{"email": "alias-gone@x.io"}]},                         # deleted mapping only
            {"uid": "nobody", "email": "nobody@x.io"},                                # unresolvable
        ]

    def test_single_match_mixed_case_is_a_candidate(self):
        uids, _ = self.run_reconcile(self.fixture(), self.USERS, self.EMAILUSERS)
        self.assertIn("single", uids)

    def test_ambiguous_is_excluded_from_candidates(self):
        uids, _ = self.run_reconcile(self.fixture(), self.USERS, self.EMAILUSERS)
        self.assertNotIn("ambiguous", uids)
        self.assertEqual(self.list_file("reconcile_claim_ambiguous.txt"), ["ambiguous"])
        self.assertNotIn("ambiguous", self.list_file("reconcile_claim_new_candidates.txt"))

    def test_duplicate_active_profiles_on_one_email_are_ambiguous(self):
        users = [["a", "dup@x.io", ""], ["b", "DUP@x.io", ""]]
        uids, _ = self.run_reconcile([{"uid": "login", "email": "dup@x.io"}], users, [])
        self.assertEqual(uids, [])
        self.assertEqual(self.list_file("reconcile_claim_ambiguous.txt"), ["login"])

    def test_deleted_profile_mapping_is_ignored(self):
        uids, _ = self.run_reconcile(self.fixture(), self.USERS, self.EMAILUSERS)
        self.assertNotIn("deletedMap", uids)
        self.assertNotIn("deletedMap", self.list_file("reconcile_claim_ambiguous.txt"))

    def test_candidate_and_new_candidate_lists(self):
        uids, _ = self.run_reconcile(self.fixture(), self.USERS, self.EMAILUSERS)
        self.assertEqual(uids, ["single", "stamped", "stale"])
        self.assertEqual(self.list_file("reconcile_claim_new_candidates.txt"), ["single", "stale"])

    def test_coverage_section_counts(self):
        _, out = self.run_reconcile(self.fixture(), self.USERS, self.EMAILUSERS)
        section = out[out.index("userId claim coverage"):]
        expected = {
            "no own profile, has email": 6,
            "already stamped": 1,
            "stale claim (other userId)": 1,
            "new candidates": 2,
            "ambiguous (>1 profile)": 1,
            "unresolvable (no profile)": 2,
        }
        for label, n in expected.items():
            line = next(l for l in section.splitlines() if l.strip().startswith(label))
            self.assertEqual(int(line.split(":", 1)[1].split()[0]), n, line)

    def test_production_export_shape(self):
        # The shape the real export emits, one object per line:
        # {uid, email, providerData: [{providerId, email}], customClaims: {userId} | null}.
        accounts = [
            {"uid": "p1", "email": "one@x.io",
             "providerData": [{"providerId": "password", "email": "one@x.io"}], "customClaims": None},
            {"uid": "gNew", "email": None,
             "providerData": [{"providerId": "google.com", "email": "One@X.io"}], "customClaims": None},
            {"uid": "gStamped", "email": "three@x.io",
             "providerData": [{"providerId": "google.com", "email": "three@x.io"}],
             "customClaims": {"userId": "p3"}},
            {"uid": "aStale", "email": "alias-two@x.io",
             "providerData": [{"providerId": "apple.com", "email": "alias-two@x.io"}],
             "customClaims": {"userId": "p1"}},
            {"uid": "gAmbiguous", "email": "one@x.io",
             "providerData": [{"providerId": "google.com", "email": "two@x.io"}], "customClaims": None},
        ]
        uids, out = self.run_reconcile(accounts, self.USERS, self.EMAILUSERS)
        self.assertEqual(uids, ["gNew", "gStamped", "aStale"])
        self.assertEqual(self.list_file("reconcile_claim_new_candidates.txt"), ["gNew", "aStale"])
        self.assertEqual(self.list_file("reconcile_claim_ambiguous.txt"), ["gAmbiguous"])
        self.assertRegex(out, r"already stamped\s*:\s*1\b")
        self.assertRegex(out, r"stale claim \(other userId\):\s*1\b")

    def test_custom_claims_object_wins_over_custom_attributes(self):
        accounts = [{"uid": "login", "email": "one@x.io", "customClaims": {"userId": "p1"},
                     "customAttributes": json.dumps({"userId": "p9"})}]
        self.run_reconcile(accounts, self.USERS, [])
        self.assertEqual(self.list_file("reconcile_claim_new_candidates.txt"), [])

    def test_malformed_custom_attributes_count_as_unstamped(self):
        accounts = [{"uid": "login", "email": "one@x.io", "customAttributes": "{not json"}]
        self.run_reconcile(accounts, self.USERS, [])
        self.assertEqual(self.list_file("reconcile_claim_new_candidates.txt"), ["login"])


if __name__ == "__main__":
    unittest.main()
