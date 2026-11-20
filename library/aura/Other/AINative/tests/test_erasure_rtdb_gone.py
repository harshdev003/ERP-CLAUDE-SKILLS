"""Tests for `respond-to-erasure`'s `check_rtdb_gone.py`.

The gone-check reads every node FirebaseDenormalizer's `userDeleted` removes, in one batch through
`rtdb_get.js`. The rules that matter: a list node is read only when the audit log vouches for it,
because a failed delete would download it whole; the read wins over the log; and nothing but node
names and counts is printed. The log (`check_rtdb_audit.check`) and the read (`read`) are stubbed
at their chokepoints; the node list is checked against the real `rtdb_get.js` refusals.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "plugins/eng/skills/respond-to-erasure/scripts"
UID = "TESTUIDaaaaaaaaaaaaaaaaaaaa01"
NODE = shutil.which("node")

# gcp.py sys.exit(2)s at import without google-cloud-logging, and CI installs none.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_deepgram_transport import _stubbed_google  # noqa: E402


def _load():
    spec = importlib.util.spec_from_file_location("check_rtdb_gone", SCRIPTS / "check_rtdb_gone.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gone = _load()
LIST_NODES = [n for n in gone.NODES if n not in gone.SMALL]
UNTOUCHED_LISTS = [n for n in gone.UNTOUCHED if n not in gone.SMALL]
UNTOUCHED_SMALL = [n for n in gone.UNTOUCHED if n in gone.SMALL]


def run(audit, present=(), errors=(), counts=None):
    """check() with the audit verdict and the read stubbed; `present` nodes read as existing."""
    seen = []

    def fake_read(paths):
        seen.extend(paths)
        rows = {}
        for p in paths:
            node = p.split("/")[1]
            if node in errors:
                rows[p] = {"path": p, "error": "TIMEOUT"}
            elif node in present:
                rows[p] = {"path": p, "exists": True, "type": "object", "count": (counts or {}).get(node, 3)}
            else:
                rows[p] = {"path": p, "exists": False}
        return rows

    fake_audit = mock.Mock()
    fake_audit.check.return_value = audit
    with mock.patch.dict(sys.modules, {"check_rtdb_audit": fake_audit}), \
            mock.patch.object(gone, "read", side_effect=fake_read):
        out = gone.check(UID, "2026-11-11T10:00:00Z")
    return out, [p.split("/")[1] for p in seen]


DONE = {"status": "done", "removed": 69, "failed": 0}


class NodeList(unittest.TestCase):
    def test_matches_firebase_denormalizer(self):
        # 69 removeReference calls in userDeleted.js; /userDonations is commented out there.
        self.assertEqual(len(gone.NODES), 69)
        self.assertEqual(len(set(gone.NODES)), 69)
        self.assertNotIn("userDonations", gone.NODES)
        self.assertIn("userListened", LIST_NODES)

    def test_covers_the_90_per_user_nodes(self):
        # Firebase's ENGSUP-171 check: users, 69 userDeleted nodes, 19 per-user nodes it never
        # removes and the tombstone. userDonations is one of the 19, kept on erasure and never read.
        nodes = [e["node"] for e in gone.plan(UID, DONE)]
        self.assertEqual(len(nodes) + len(gone.KEPT), 90)
        self.assertEqual(len(set(nodes) | set(gone.KEPT)), 90)
        self.assertFalse(set(nodes) & set(gone.KEPT))
        self.assertNotIn("emailUser", nodes)
        self.assertFalse(set(gone.UNTOUCHED) & set(gone.NODES))
        self.assertEqual(nodes[-1], gone.TOMBSTONE)
        self.assertTrue(all(e["path"] == f"/{e['node']}/{UID}" for e in gone.plan(UID, DONE)))

    @unittest.skipUnless(NODE, "node not installed")
    def test_every_planned_path_passes_rtdb_get_refusals(self):
        paths = [e["path"] for e in gone.plan(UID, DONE)]
        js = (f"const m=require({json.dumps(str(SCRIPTS / 'rtdb_get.js'))});"
              f"m.validate({json.dumps(paths)});console.log('ok')")
        out = subprocess.run([NODE, "-e", js], capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(out.stdout.strip(), "ok", out.stderr)


class WhatIsRead(unittest.TestCase):
    def test_done_reads_every_vouched_node_and_hands_untouched_lists_to_firebase(self):
        out, read = run(DONE)
        self.assertEqual(sorted(read), sorted(["users", *gone.NODES, *UNTOUCHED_SMALL, gone.TOMBSTONE]))
        self.assertEqual(out["for_firebase"], UNTOUCHED_LISTS)
        self.assertEqual(out["status"], "gone")  # covers what it reads; Firebase closes the rest
        self.assertEqual(out["gone"], 1 + 69 + len(UNTOUCHED_SMALL))
        self.assertEqual(out["tombstone"], "gone")

    def test_untouched_list_nodes_are_never_read(self):
        for audit in (DONE, {"status": "unverified", "reason": "no audit line"}):
            with self.subTest(audit["status"]):
                _, read = run(audit)
                self.assertFalse(set(read) & set(UNTOUCHED_LISTS))

    def test_userdonations_is_kept_and_never_read(self):
        out, read = run(DONE)
        self.assertNotIn("userDonations", read)
        self.assertNotIn("userDonations", out["for_firebase"])
        self.assertEqual(out["kept"], ["userDonations"])

    def test_failed_list_nodes_named_by_the_log_are_not_read(self):
        audit = {"status": "failed", "removed": 67, "failed": 2, "failed_nodes": ["userListened", "userAddress"]}
        out, read = run(audit)
        self.assertNotIn("userListened", read)
        self.assertIn("userAddress", read)  # small: read even when it failed
        self.assertIn("userPlayed", read)  # a list node the log vouches for
        self.assertEqual(out["present_per_log"], ["userListened"])
        self.assertEqual(out["status"], "present")

    def test_after_a_crash_list_nodes_are_unverified_and_not_read(self):
        out, read = run({"status": "unverified", "reason": "no audit line yet"})
        self.assertEqual(set(read), gone.SMALL)
        self.assertEqual(sorted(out["unverified"]), sorted(LIST_NODES))
        self.assertEqual(out["for_firebase"], UNTOUCHED_LISTS)
        self.assertEqual(out["status"], "unverified")

    def test_failed_nodes_not_all_named_reads_no_list_node(self):
        for nodes in (None, ["userListened"]):
            with self.subTest(nodes):
                _, read = run({"status": "failed", "removed": 66, "failed": 3, "failed_nodes": nodes})
                self.assertFalse(set(read) & set(LIST_NODES))

    def test_a_short_failed_nodes_list_reads_no_list_node(self):
        # Two failed, one named (the audit script deduped a repeated line): userPlayed may be the other.
        _, read = run({"status": "failed", "removed": 67, "failed": 2, "failed_nodes": ["userListened"]})
        self.assertNotIn("userPlayed", read)

    def test_dry_run_reads_nothing(self):
        fake_audit = mock.Mock()
        fake_audit.check.return_value = {"status": "unverified", "reason": "no audit line yet"}
        with mock.patch.dict(sys.modules, {"check_rtdb_audit": fake_audit}), \
                mock.patch.object(gone, "read", side_effect=AssertionError("read on a dry run")):
            out = gone.check(UID, "2026-11-11T10:00:00Z", dry_run=True)
        self.assertEqual(out["not_read"]["userListened"], "unverified")
        self.assertIn("users", out["read"])
        self.assertIn(gone.TOMBSTONE, out["read"])
        self.assertEqual(out["not_read"]["userEvents"], "for_firebase")


class Tombstone(unittest.TestCase):
    """/deletedUsers/{uid} is reported on its own: the delete writes it, the erasure removes it last."""

    def test_present_tombstone_does_not_change_the_status(self):
        out, _ = run(DONE, present=(gone.TOMBSTONE,))
        self.assertEqual(out["tombstone"], "present")
        self.assertNotIn(gone.TOMBSTONE, " ".join(out["present"]))
        self.assertEqual(out["unverified"], [])

    def test_a_tombstone_read_error_is_unverified(self):
        out, _ = run(DONE, errors=(gone.TOMBSTONE,))
        self.assertEqual(out["tombstone"], "unverified")


class TheReadWins(unittest.TestCase):
    def test_present_on_read_beats_a_clean_log(self):
        out, _ = run(DONE, present=("userListened", "users"), counts={"userListened": 17000})
        self.assertEqual(out["status"], "present")
        self.assertEqual(out["present"], ["users (3)", "userListened (17000)"])

    def test_a_read_error_is_unverified_never_gone(self):
        out, _ = run(DONE, errors=("userPlayed",))
        self.assertEqual(out["status"], "unverified")
        self.assertEqual(out["unverified"], ["userPlayed"])

    def test_a_missing_row_is_unverified(self):
        fake_audit = mock.Mock()
        fake_audit.check.return_value = DONE
        with mock.patch.dict(sys.modules, {"check_rtdb_audit": fake_audit}), \
                mock.patch.object(gone, "read", return_value={}):
            out = gone.check(UID, "2026-11-11T10:00:00Z")
        self.assertEqual(out["status"], "unverified")
        self.assertEqual(out["gone"], 0)


class Output(unittest.TestCase):
    def test_never_prints_the_uid_or_a_path(self):
        out, _ = run({"status": "failed", "removed": 68, "failed": 1, "failed_nodes": ["userListened"]},
                     present=("users", gone.TOMBSTONE))
        text = json.dumps(out)
        for secret in (UID, "/"):
            self.assertNotIn(secret, text)


class Read(unittest.TestCase):
    """read() hands rtdb_get.js the paths on stdin, labels the run, and keeps only rows it asked for."""

    def test_runs_the_bundled_copy_with_the_skill_user_agent(self):
        captured = {}

        def fake_run(cmd, **kw):
            captured.update(cmd=cmd, **kw)
            lines = [json.dumps({"path": "/userPlayed/x", "exists": False}),
                     json.dumps({"path": "/other/y", "exists": True}), "rtdb_get: noise"]
            return subprocess.CompletedProcess(cmd, 0, "\n".join(lines), "")

        with _stubbed_google(), mock.patch.object(gone.subprocess, "run", side_effect=fake_run):
            rows = gone.read(["/userPlayed/x"])
        self.assertEqual(list(rows), ["/userPlayed/x"])
        self.assertEqual(captured["cmd"][1], str(SCRIPTS / "rtdb_get.js"))
        self.assertEqual(captured["input"], "/userPlayed/x")
        self.assertRegex(captured["env"]["AINATIVE_USER_AGENT"], r"^AuraClaude/.+/eng:respond-to-erasure@")


if __name__ == "__main__":
    unittest.main()
