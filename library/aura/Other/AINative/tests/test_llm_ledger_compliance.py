#!/usr/bin/env python3
"""ENG-1657 part 4: the llmCall ledger in the SAR pack, and release-version's serving probe.

- `respond-to-sar` reads the person's llmCall rows (`collect.py llm-calls`) and `build_pack.py`
  turns them into `export/07_ai_call_log.csv`, which goes in the zip. A pack without the file
  simply has no 07, like every other optional source.
- `release-version`'s serving probe reads the newest rows of today and reports their versions;
  a missing GCP_KEY or a BigQuery failure is `unknown`, never a pass.

`llm_ledger.rows` is the stubbed chokepoint; no GCP call is made.
"""
from __future__ import annotations

import csv
import json
import os
import pathlib
import sys
import tempfile
import types
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from reader_harness import REPO_ROOT, load_reader  # noqa: E402

SAR = REPO_ROOT / "plugins/eng/skills/respond-to-sar/scripts"
RELEASE = REPO_ROOT / "plugins/eng/skills/release-version/scripts/release.py"
ROW = {"id": "a", "createdAt": "2026-10-27T22:35:59.129Z", "prompterName": "coach-prompter", "model": "gpt-5",
       "platform": "mobile", "version": "1.43.4", "conversationId": "C1", "inputTokens": 900, "outputTokens": 40,
       "userId": "u1"}


def fake_ledger(rows=(), error=None):
    class LedgerError(RuntimeError):
        pass

    calls = []

    def read(params):
        calls.append(dict(params))
        if error:
            raise LedgerError(error)
        return list(rows)

    return types.SimpleNamespace(LedgerError=LedgerError, rows=read, calls=calls)


class SarPack(unittest.TestCase):
    def setUp(self):
        self.out = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.out, True))
        (self.out / "raw").mkdir()
        (self.out / "raw" / "postgres_user.json").write_text(json.dumps({"name": "Test", "createdAt": "2026-01-01T00:00:00Z"}))

    def collect(self, rows):
        mod = load_reader(SAR / "collect.py", siblings=("llm_ledger", "bq"))
        fake = fake_ledger(rows)
        with mock.patch.dict(sys.modules, {"llm_ledger": fake}):
            got = mod.cmd_llm_calls(types.SimpleNamespace(out=str(self.out), uid="u1"))
        return got, fake.calls

    def build(self):
        mod = load_reader(SAR / "build_pack.py", siblings=())
        return mod.build(mod.Pack(str(self.out), "Europe/Dublin", "America/Los_Angeles"), {})

    def test_collect_reads_every_row_for_the_uid_oldest_first(self):
        got, calls = self.collect([ROW])
        self.assertEqual(calls, [{"userId": "u1", "order": "asc"}])
        self.assertEqual((got["rows"], got["prompters"]), (1, ["coach-prompter"]))
        self.assertEqual(json.loads((self.out / "raw" / "llm_calls.json").read_text())[0]["id"], "a")

    def test_a_blank_uid_is_refused_before_any_read(self):
        mod = load_reader(SAR / "collect.py", siblings=("llm_ledger", "bq"))
        fake = fake_ledger([ROW])
        for uid in ("", "   ", None):
            with mock.patch.dict(sys.modules, {"llm_ledger": fake}), self.assertRaises(SystemExit):
                mod.cmd_llm_calls(types.SimpleNamespace(out=str(self.out), uid=uid))
        self.assertEqual(fake.calls, [])
        self.assertFalse((self.out / "raw" / "llm_calls.json").exists())

    def test_the_call_log_is_exported_and_zipped(self):
        self.collect([ROW])
        res = self.build()
        path = self.out / "export" / "07_ai_call_log.csv"
        with path.open(encoding="utf-8") as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0][2], "AI feature (prompter)")
        self.assertEqual(rows[1][0], "2026-10-27 22:35:59 UTC")
        self.assertEqual(rows[1][2:7], ["coach-prompter", "gpt-5", "mobile", "1.43.4", "C1"])
        with zipfile.ZipFile(res["zip"]) as z:
            self.assertIn("07_ai_call_log.csv", z.namelist())

    def test_no_ledger_file_means_no_07(self):
        self.build()
        self.assertFalse((self.out / "export" / "07_ai_call_log.csv").exists())


class ServingProbe(unittest.TestCase):
    def probe(self, fake, env=True):
        mod = load_reader(RELEASE, siblings=("llm_ledger", "bq"))
        with mock.patch.dict(sys.modules, {"llm_ledger": fake}), \
                mock.patch.dict(os.environ, {"GCP_KEY": "{}"} if env else {}, clear=not env):
            return mod._serving_versions_aura_ledger()

    def test_versions_on_todays_newest_rows(self):
        fake = fake_ledger([ROW, {**ROW, "version": "1.43.3"}, {**ROW, "version": None}])
        versions, err = self.probe(fake)
        self.assertEqual((versions, err), ({"1.43.4", "1.43.3"}, None))
        self.assertEqual(fake.calls[0]["limit"], 100)
        self.assertEqual(len(fake.calls[0]["from"]), 10)

    def test_a_failure_is_an_error_not_an_empty_pass(self):
        versions, err = self.probe(fake_ledger(error="403 access denied"))
        self.assertEqual(versions, set())
        self.assertIn("ledger unreachable", err)

    def test_no_key_says_so(self):
        versions, err = self.probe(fake_ledger([ROW]), env=False)
        self.assertEqual((versions, err), (set(), "GCP_KEY not set"))


if __name__ == "__main__":
    unittest.main()
