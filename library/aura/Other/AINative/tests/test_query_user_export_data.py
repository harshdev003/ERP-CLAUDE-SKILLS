#!/usr/bin/env python3
"""`eng:query-user-export-data`: one user's stored IntelligenceService data, read-only, to JSON files.

The guards pinned here, without a database (CI has none; the SQL ran end to end against a local
stand-in schema before the PR):
- **Read-only twice:** the script opens a READ ONLY transaction and psql runs with
  `default_transaction_read_only=on`; nothing in the SQL writes.
- **One user, never in the text:** psql reads the uid from its file at run time; every SELECT is
  filtered by `:'uid'`; the uid is in neither the .sql nor the command nor the output.
- **Named columns only** (no `SELECT *`), and the unknown-uid check comes before any output.
- **Private files:** the .sql is 0600; a uid file others can read is refused; nothing is overwritten.
- **The approver's fallback:** with no database it prints the one command and connects to nothing.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import pathlib
import re
import stat
import sys
import tempfile
import unittest
import uuid
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "plugins/eng/skills/query-user-export-data/scripts/export_sql.py"
UID = "TestUidTestUidTestUid0000001"


def load():
    name = f"_export_sql_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class Sql(unittest.TestCase):
    def setUp(self):
        self.mod = load()
        self.tmp = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.uid_file = self.tmp / "uid"
        self.uid_file.write_text(UID)
        os.chmod(self.uid_file, 0o600)

    def sql(self, sections=None):
        paths = self.mod.plan(UID, self.tmp, sections or list(self.mod.SECTIONS), "20261116T000000Z")
        return self.mod.build_sql(self.uid_file, paths, "2026-11-16T00:00:00.000Z", self.mod.uid_prefix(UID)), paths

    def test_every_select_reads_named_columns_for_the_one_uid(self):
        for name, select in self.mod.SECTIONS.items():
            with self.subTest(section=name):
                self.assertNotRegex(select, r"SELECT\s+\*|\.\*")
                self.assertIn(":'uid'", select)
                self.assertNotRegex(select.upper(), r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|GRANT|CREATE)\b")

    def test_one_read_only_snapshot_that_rolls_back(self):
        sql, _ = self.sql()
        self.assertIn("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;", sql)
        self.assertTrue(sql.rstrip().endswith("ROLLBACK;"))
        self.assertNotIn("COMMIT", sql)
        self.assertIn("default_transaction_read_only=on", self.mod.PGOPTIONS)

    def test_the_uid_is_read_at_run_time_and_never_written_down(self):
        sql, _ = self.sql()
        self.assertIn(f"\\set uid `cat {self.uid_file}`", sql)
        self.assertNotIn(UID, sql)
        self.assertNotIn(UID, self.mod.command(self.tmp / "q.sql"))

    def test_an_unknown_uid_stops_before_any_output(self):
        sql, _ = self.sql()
        self.assertLess(sql.index("NO_SUCH_USER"), sql.index("\\o "))

    def test_each_section_writes_its_own_envelope(self):
        sql, paths = self.sql(["memory", "journal"])
        for entity, path in paths.items():
            self.assertIn(f"'entity', '{entity}'", sql)
            self.assertIn(str(path), sql)
            self.assertTrue(path.name.startswith(UID[:8]) and UID not in path.name)
        self.assertIn(f"'userIdPrefix', '{UID[:8]}'", sql)
        self.assertIn(f"'version', {self.mod.VERSION}", sql)

    def test_rows_keep_their_order_inside_the_aggregate(self):
        sql, _ = self.sql()
        for entity in ("memory", "insights", "affirmations", "conversations", "journal"):
            with self.subTest(entity=entity):
                self.assertIn(f"json_agg(t ORDER BY t.\"createdAt\"", sql.split(f"'entity', '{entity}'", 1)[1].split("\\o", 1)[0])

    def test_a_provider_uid_keeps_its_prefix(self):
        uid = "google:111555896239269076429"
        self.assertEqual(self.mod.uid_prefix(uid), "google:11155589")
        paths = self.mod.plan(uid, self.tmp, ["memory"], "S")
        self.assertEqual(paths["memory"].name, "google-11155589-memory-S.json")

    def test_timestamps_are_iso_z(self):
        for name, select in self.mod.SECTIONS.items():
            for col in re.findall(r'"(\w*At)"', select):
                if col in ("removedAt", "deletedAt", "expiryAt"):
                    continue
                with self.subTest(section=name, column=col):
                    self.assertRegex(select, rf'to_char\((\w\.)?"{col}" AT TIME ZONE \'UTC\'')

    def test_highlights_drop_their_embedded_transcripts(self):
        select = self.mod.SECTIONS["insights"]
        self.assertIn("h - 'transcripts'", select)
        # Most stored rows hold {"data": null} or no data key: jsonb_array_elements would fail on them.
        self.assertIn("jsonb_typeof(\"highlights\"->'data') = 'array'", select)
        # The highlights keep their stored order, and an empty array stays [].
        self.assertIn("WITH ORDINALITY AS x(h, o)", select)
        self.assertIn("ORDER BY o), '[]'::jsonb)", select)
        self.assertNotIn('"highlights"->\'data\' AS "highlights"', select)

    def test_the_password_leaves_the_url_for_the_environment(self):
        url, env = self.mod.split_url("postgresql://reader:p%40ss@db.host:5432/is?sslmode=require")
        self.assertEqual(url, "postgresql://reader@db.host:5432/is?sslmode=require")
        self.assertEqual(env, {"PGPASSWORD": "p@ss"})
        self.assertEqual(self.mod.split_url("postgresql://db.host/is"), ("postgresql://db.host/is", {}))

    def test_never_overwrites(self):
        paths = self.mod.plan(UID, self.tmp, ["memory"], "S")
        paths["memory"].write_text("{}")
        with self.assertRaises(SystemExit):
            self.mod.plan(UID, self.tmp, ["memory"], "S")


class Main(unittest.TestCase):
    def setUp(self):
        self.mod = load()
        self.tmp = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.uid_file = self.tmp / "uid"
        self.uid_file.write_text(UID + "\n")
        os.chmod(self.uid_file, 0o600)

    def run_main(self, *argv, env=None):
        out = io.StringIO()
        with mock.patch.dict(os.environ, env or {}, clear=True), contextlib.redirect_stdout(out):
            code = self.mod.main(["--uid-file", str(self.uid_file), "--out-dir", str(self.tmp / "out"), *argv])
        return code, out.getvalue()

    def test_without_a_database_it_prints_the_command_and_connects_to_nothing(self):
        with mock.patch.object(self.mod.subprocess, "run", side_effect=AssertionError("connected")):
            code, out = self.run_main()
        self.assertEqual(code, 3)
        self.assertIn("psql -X -q", out)
        self.assertNotIn("INTELLIGENCE_DB_URL\" -X", out)
        self.assertIn("--check", out)
        self.assertNotIn(UID, out)
        folder = self.tmp / "out"
        self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)
        sqls = list(folder.glob("*.sql"))
        self.assertEqual(len(sqls), 1)
        self.assertEqual(stat.S_IMODE(sqls[0].stat().st_mode), 0o600)

    def test_a_failed_read_tries_nothing_else_and_hides_the_uid(self):
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            self.assertIn("default_transaction_read_only=on", kw["env"]["PGOPTIONS"])
            self.assertFalse(any("s3cret" in part for part in cmd), "no password in argv")
            self.assertEqual(kw["env"]["PGPASSWORD"], "s3cret")
            return mock.Mock(returncode=2, stdout="", stderr=f'invalid input for "{UID}"\nmore')
        with mock.patch.object(self.mod.subprocess, "run", run):
            code, out = self.run_main(env={"INTELLIGENCE_DB_URL": "postgresql://reader:s3cret@db.host:5432/is"})
        self.assertEqual(code, 2)
        self.assertNotIn("s3cret", out)
        self.assertEqual(len(calls), 1, "one attempt, no other credential")
        self.assertNotIn(UID, out)
        self.assertIn("<uid>", out)

    def test_an_unknown_user_writes_nothing(self):
        with mock.patch.object(self.mod.subprocess, "run",
                               return_value=mock.Mock(returncode=0, stdout="NO_SUCH_USER: nothing written", stderr="")), \
                self.assertRaises(SystemExit) as e:
            self.run_main(env={"INTELLIGENCE_DB_URL": "postgresql://x"})
        self.assertNotIn(UID, str(e.exception))

    def test_check_reports_counts_and_paths_only(self):
        folder = self.tmp / "out"
        folder.mkdir(mode=0o700)
        f = folder / f"{UID[:8]}-memory-S.json"
        f.write_text(json.dumps({"version": 1, "entity": "memory", "rows": [{"memory": "SECRET-TEXT"}]}))
        code, out = self.run_main("--sections", "memory", "--check", "S")
        self.assertEqual(code, 0)
        self.assertIn("memory: 1 rows", out)
        self.assertNotIn("SECRET-TEXT", out)
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)

    def test_refuses_a_uid_file_others_can_read_and_unknown_sections(self):
        os.chmod(self.uid_file, 0o644)
        with self.assertRaises(SystemExit):
            self.run_main()
        os.chmod(self.uid_file, 0o600)
        with self.assertRaises(SystemExit):
            self.run_main("--sections", "moods")


if __name__ == "__main__":
    unittest.main()
