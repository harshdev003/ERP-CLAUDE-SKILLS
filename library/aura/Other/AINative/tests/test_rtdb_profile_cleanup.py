"""delete-raw.js deletes only the stream temp files of its own profiler run.

firebase-tools streams `database:profile` into $TMPDIR/tmp-<pid>-<rand> and re-opens that file by
name at the end to write -o. The old cleanup deleted every tmp-* newer than the run's marker, so when
two runs overlapped the first to finish deleted the other's live file and the survivor captured 0
operations (ENG-1560). These tests pin the ownership scope with a fake $TMPDIR.
"""
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parent.parent
          / "plugins/eng/skills/profile-firebase-rtdb/scripts/delete-raw.js")
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "node not installed")
class DeleteRawOwnershipTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.marker = self.tmp / "start.marker"
        self.marker.touch()
        past = time.time() - 3600
        os.utime(self.marker, (past, past))

    def make(self, name, old=False):
        p = self.tmp / name
        p.write_text("raw")
        if old:
            t = time.time() - 7200
            os.utime(p, (t, t))
        return p

    def run_delete(self, *args):
        env = dict(os.environ, TMPDIR=str(self.tmp))
        return subprocess.run([NODE, str(SCRIPT), *args], env=env,
                              capture_output=True, text=True)

    def test_deletes_only_own_pid(self):
        mine = self.make("tmp-4242-abc")
        other = self.make("tmp-9999-xyz")
        prefix_trap = self.make("tmp-42420-def")  # pid 42420 must not match pid 4242
        r = self.run_delete(str(self.marker), "4242")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(mine.exists())
        self.assertTrue(other.exists(), "another run's live stream file was deleted")
        self.assertTrue(prefix_trap.exists())
        self.assertIn("Deleted 1 CLI temp file(s).", r.stdout)

    def test_multiple_pids(self):
        a = self.make("tmp-100-a")
        b = self.make("tmp-200-b")
        other = self.make("tmp-300-c")
        r = self.run_delete(str(self.marker), "100,200")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(a.exists())
        self.assertFalse(b.exists())
        self.assertTrue(other.exists())

    def test_marker_still_guards_older_files(self):
        old = self.make("tmp-4242-old", old=True)
        r = self.run_delete(str(self.marker), "4242")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(old.exists())

    def test_refuses_without_pid(self):
        other = self.make("tmp-9999-xyz")
        for args in ([str(self.marker)], [str(self.marker), ""], [str(self.marker), "abc"]):
            r = self.run_delete(*args)
            self.assertEqual(r.returncode, 2, args)
        self.assertTrue(other.exists())


if __name__ == "__main__":
    unittest.main()
