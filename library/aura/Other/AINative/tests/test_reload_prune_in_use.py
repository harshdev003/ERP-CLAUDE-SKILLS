#!/usr/bin/env python3
"""`shared:reload-aura-skills` `check_install.py prune` never deletes a version dir a process is using.

2026-10-28 16:42Z: a prune removed `eng/3.184.0` while a monitor-firebase pass was running from it,
and every check after the profile failed. What has to hold, with the process listing stubbed:
- a dir named in a running command's arguments is skipped, and the output names the pid;
- a dir a process works inside (its cwd) is skipped;
- an idle dir is pruned;
- a process listing that fails skips every dir rather than deleting it;
- `--dry-run` reports the same skips.
"""
from __future__ import annotations

import io
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from reader_harness import REPO_ROOT, load_reader  # noqa: E402

SCRIPT = REPO_ROOT / "plugins/shared/skills/reload-aura-skills/scripts/check_install.py"


class PruneInUse(unittest.TestCase):
    def setUp(self):
        self.mod = load_reader(SCRIPT, siblings=())
        self.cache = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.cache, True)
        for v in ("3.184.0", "3.183.0", "3.182.0"):
            d = self.cache / "aura-tools" / "eng" / v
            d.mkdir(parents=True)
            (d / "f.py").write_text("x" * 10)
        self.result = {"marketplace": "aura-tools",
                       "plugins": [{"plugin": "eng", "orphans": ["3.184.0", "3.183.0", "3.182.0"]}]}
        p = mock.patch.object(self.mod, "cache_root", lambda: str(self.cache))
        p.start()
        self.addCleanup(p.stop)

    def dir(self, v):
        return str(self.cache / "aura-tools" / "eng" / v)

    def prune(self, procs=(), cwds=(), fail=None, dry_run=False):
        def listing(rows):
            def f():
                if fail:
                    raise self.mod.ProcessCheckFailed(fail)
                return list(rows)
            return f
        with mock.patch.object(self.mod, "processes", listing(procs)), \
                mock.patch.object(self.mod, "working_dirs", listing(cwds)):
            return {label: outcome for label, outcome, _ in self.mod.prune(self.result, dry_run=dry_run)}

    def test_a_dir_named_in_a_running_command_is_skipped(self):
        cmd = f"python3 {self.dir('3.184.0')}/skills/monitor-firebase/scripts/check_db_load.py --minutes 10"
        got = self.prune(procs=[(4242, cmd)])
        self.assertTrue(got["eng/3.184.0"].startswith("skipped: in use by pid 4242 (python3 "))
        self.assertLessEqual(len(got["eng/3.184.0"]), len("skipped: in use by pid 4242 ()") + 60)
        self.assertTrue(os.path.isdir(self.dir("3.184.0")))
        self.assertEqual((got["eng/3.183.0"], got["eng/3.182.0"]), ("removed", "removed"))
        self.assertFalse(os.path.exists(self.dir("3.183.0")))

    def test_a_working_directory_inside_the_dir_is_skipped(self):
        got = self.prune(cwds=[(77, "bash", self.dir("3.183.0") + "/skills/monitor-firebase")])
        self.assertTrue(got["eng/3.183.0"].startswith("skipped: in use by pid 77 (bash (working directory "))
        self.assertTrue(os.path.isdir(self.dir("3.183.0")))

    def test_an_idle_dir_is_pruned_and_a_sibling_name_is_not_a_match(self):
        # 3.18 is a prefix of 3.184.0's name but a different directory.
        got = self.prune(procs=[(5, f"python3 {self.cache}/aura-tools/eng/3.18/x.py")])
        self.assertEqual(set(got.values()), {"removed"})

    def test_a_failed_listing_skips_every_dir(self):
        got = self.prune(fail="lsof exited 1")
        self.assertEqual(set(got.values()), {"skipped: process check failed (lsof exited 1)"})
        for v in ("3.184.0", "3.183.0", "3.182.0"):
            self.assertTrue(os.path.isdir(self.dir(v)))

    def test_any_listing_error_skips_rather_than_crashes(self):
        def boom():
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad byte")
        with mock.patch.object(self.mod, "processes", boom), mock.patch.object(self.mod, "working_dirs", lambda: []):
            got = {l: o for l, o, _ in self.mod.prune(self.result)}
        self.assertTrue(all(o.startswith("skipped: process check failed (UnicodeDecodeError") for o in got.values()))
        self.assertTrue(os.path.isdir(self.dir("3.184.0")))

    def test_odd_bytes_and_a_bad_pid_line_do_not_break_the_listing(self):
        out = mock.Mock(returncode=0, stdout="pabc\ncbash\nn/tmp\np12\ncpython3\nn/x\udcff\n")
        with mock.patch.object(self.mod.subprocess, "run", return_value=out):
            rows = self.mod.working_dirs()
        self.assertEqual([r[0] for r in rows], [12])

    def test_dry_run_reports_the_same_skip(self):
        cmd = f"bash {self.dir('3.184.0')}/skills/monitor-firebase/scripts/run_pass.sh"
        got = self.prune(procs=[(9, cmd)], dry_run=True)
        self.assertTrue(got["eng/3.184.0"].startswith("skipped: in use by pid 9 (bash "))
        self.assertEqual(got["eng/3.183.0"], "would remove")
        self.assertTrue(os.path.isdir(self.dir("3.183.0")))

    def test_main_counts_only_removed_bytes_and_prints_the_skip(self):
        cmd = f"bash {self.dir('3.184.0')}/run_pass.sh"
        buf = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        with mock.patch.object(self.mod, "survey", lambda *a, **k: self.result), \
                mock.patch.object(self.mod, "processes", lambda: [(9, cmd)]), \
                mock.patch.object(self.mod, "working_dirs", lambda: []), \
                mock.patch.object(sys, "argv", ["check_install.py", "prune", "--json", "--no-fetch"]), \
                redirect_stdout(buf):
            self.mod.main()
        buf.flush()
        text = buf.buffer.getvalue().decode("utf-8")
        out = json.loads(text[text.index("{"):])
        self.assertEqual(out["bytes_freed"], 20)
        skipped = [p for p in out["pruned"] if p["dir"] == "eng/3.184.0"][0]
        self.assertTrue(skipped["outcome"].startswith("skipped: in use by pid 9"))


if __name__ == "__main__":
    unittest.main()
