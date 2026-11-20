"""eng:read-rtdb-paths `rtdb_get.js`: exact prod RTDB paths through the Admin SDK, nothing wider.

A cooperative guard (admin keys bypass rules), so every refusal is tested here rather than trusted:
the root, top-level nodes, /users itself, shallow and query reads, more than 20 in flight, a bulk
run without --bulk, and values printed by default. Every copy of the file runs the same suite.
"""
import glob
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
CANONICAL = REPO / "plugins/eng/skills/read-rtdb-paths/scripts/rtdb_get.js"
COPIES = sorted(pathlib.Path(p) for p in glob.glob(str(REPO / "plugins/**/scripts/rtdb_get.js"), recursive=True))
FORMAT = re.compile(r"^AuraClaude/(?P<who>[A-Za-z0-9._-]+)/(?P<plugin>[a-z]+):(?P<skill>[a-z0-9-]+)@\S+ \(session (?P<session>[A-Za-z0-9._-]+)\)$")
# No credentials and no firebase-admin: a run that got past the checks would fail on those instead.
BARE = {k: v for k, v in os.environ.items() if k not in ("GCP_KEY", "RTDB_URL", "AURA_CLI_DIR",
                                                          "AINATIVE_FIREBASE_ADMIN_DIR", "AINATIVE_USER_AGENT")}

# A stub database: tracks reads in flight and answers from a fixed tree.
STUB = r"""
const m = require(process.argv[1]);
const tree = JSON.parse(process.argv[2]);
const opts = JSON.parse(process.argv[3]);
let inFlight = 0, peak = 0;
const db = { ref: (p) => ({ get: async () => {
  inFlight++; peak = Math.max(peak, inFlight);
  if (p.startsWith('/slow/')) return new Promise(() => {});
  if (p.startsWith('/late/')) return new Promise((_, reject) => setTimeout(() => reject(new Error('closed')), 150));
  await new Promise((r) => setTimeout(r, 5));
  inFlight--;
  const v = p.split('/').slice(1).reduce((o, k) => (o == null ? undefined : o[k]), tree);
  return { exists: () => v !== undefined && v !== null, val: () => v };
} }) };
// A real database connection keeps Node alive while a read hangs; the stub needs a timer for that.
const alive = setInterval(() => {}, 1000);
let unhandled = 0;
process.on('unhandledRejection', () => { unhandled++; });
m.readPaths(opts.paths, { ...opts, db }).then(
  (rows) => new Promise((r) => setTimeout(r, opts.linger || 0)).then(() => console.log(JSON.stringify({ rows, peak, unhandled }))),
  (e) => console.log(JSON.stringify({ error: e.code, message: e.message }))).finally(() => clearInterval(alive));
"""


def run(copy, *args, stdin=None, env=None):
    return subprocess.run([NODE, str(copy), *args], capture_output=True, text=True, encoding="utf-8", input=stdin, env=env or BARE, timeout=60)


def stub(copy, tree, **opts):
    out = subprocess.run([NODE, "-e", STUB, str(copy), json.dumps(tree), json.dumps(opts)],
                         capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@unittest.skipUnless(NODE, "node not installed")
class Copies(unittest.TestCase):
    def test_every_copy_matches_the_canonical(self):
        self.assertIn(CANONICAL, COPIES)
        for copy in COPIES:
            with self.subTest(copy=os.path.relpath(copy, REPO)):
                self.assertEqual(copy.read_bytes(), CANONICAL.read_bytes())


@unittest.skipUnless(NODE, "node not installed")
class Refusals(unittest.TestCase):
    def test_self_test_passes(self):
        for copy in COPIES:
            out = run(copy, "--self-test")
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(json.loads(out.stdout)["selfTest"], "ok")

    def test_refused_paths_never_connect(self):
        for path in ("/", "/users", "/coaches", "/users/abc?shallow=true", "/coaches/a&orderBy=x",
                     "/coaches/a.json", "/coaches/a.b", "coaches/a"):
            with self.subTest(path=path):
                out = run(CANONICAL, "/coaches/ok", path)
                self.assertEqual(out.returncode, 2)
                self.assertTrue(out.stderr.startswith("refused:"), out.stderr)
                self.assertEqual(out.stdout, "")

    def test_paths_from_stdin_are_checked_too(self):
        out = run(CANONICAL, stdin="/coaches/ok\n/\n")
        self.assertEqual(out.returncode, 2)
        self.assertTrue(out.stderr.startswith("refused:"), out.stderr)

    def test_a_good_path_gets_as_far_as_the_connection(self):
        out = run(CANONICAL, "/coaches/ok", "/emailUser/YWJj")
        self.assertEqual(out.returncode, 2)
        self.assertIn("AURA_CLI_DIR", out.stderr)

    def test_bulk_needs_the_flag(self):
        paths = "".join(f"/coaches/c{i}\n" for i in range(201))
        out = run(CANONICAL, stdin=paths)
        self.assertEqual(out.returncode, 2)
        self.assertIn("bulk read", out.stderr)
        self.assertIn("Announce it first", out.stderr)
        self.assertIn("AURA_CLI_DIR", run(CANONICAL, "--bulk", stdin=paths).stderr)
        self.assertIn("AURA_CLI_DIR", run(CANONICAL, stdin="".join(f"/coaches/c{i}\n" for i in range(200))).stderr)

    def test_concurrency_is_capped(self):
        for n in ("0", "26", "x"):
            with self.subTest(n=n):
                out = run(CANONICAL, "--concurrency", n, "/coaches/ok")
                self.assertEqual(out.returncode, 2)
                self.assertIn("concurrency", out.stderr)


@unittest.skipUnless(NODE, "node not installed")
class Reads(unittest.TestCase):
    TREE = {"coaches": {f"c{i}": {"email": f"coach{i}@example.com", "name": "Secret Name"} for i in range(60)},
            "users": {"u1": {"email": "person@example.com"}}}

    def test_never_more_than_20_in_flight(self):
        got = stub(CANONICAL, self.TREE, paths=[f"/coaches/c{i}" for i in range(60)])
        self.assertEqual(len(got["rows"]), 60)
        self.assertLessEqual(got["peak"], 20)
        self.assertGreater(got["peak"], 1)
        self.assertLessEqual(stub(CANONICAL, self.TREE, paths=[f"/coaches/c{i}" for i in range(60)], concurrency=3)["peak"], 3)

    def test_names_and_counts_never_values_by_default(self):
        got = stub(CANONICAL, self.TREE, paths=["/coaches/c1", "/users/u1/email", "/coaches/none"])
        text = json.dumps(got)
        for value in ("coach1@example.com", "Secret Name", "person@example.com"):
            self.assertNotIn(value, text)
        self.assertEqual(got["rows"][0], {"path": "/coaches/c1", "exists": True, "type": "object", "count": 2,
                                          "fields": ["email", "name"]})
        self.assertEqual(got["rows"][1], {"path": "/users/u1/email", "exists": True, "type": "string"})
        self.assertEqual(got["rows"][2], {"path": "/coaches/none", "exists": False})

    def test_values_only_on_request(self):
        got = stub(CANONICAL, self.TREE, paths=["/users/u1/email"], values=True)
        self.assertEqual(got["rows"], [{"path": "/users/u1/email", "value": "person@example.com"}])

    def test_a_timeout_stops_new_reads_so_the_cap_holds(self):
        paths = ["/slow/a", "/slow/b"] + [f"/coaches/c{i}" for i in range(10)]
        got = stub(CANONICAL, self.TREE, paths=paths, concurrency=2, timeoutMs=50)
        self.assertLessEqual(got["peak"], 2)
        self.assertEqual([r.get("error") for r in got["rows"][:2]], ["TIMEOUT", "TIMEOUT"])
        self.assertEqual({r.get("error") for r in got["rows"][2:]}, {"NOT_READ_AFTER_TIMEOUT"})
        self.assertEqual(len(got["rows"]), 12)

    def test_no_unhandled_rejections_from_the_race(self):
        # A late-failing abandoned read, and fast reads whose timers would otherwise fire later.
        got = stub(CANONICAL, self.TREE, paths=["/late/a", "/coaches/c1", "/coaches/c2"], concurrency=3,
                   timeoutMs=50, linger=400)
        self.assertEqual(got["unhandled"], 0)
        self.assertEqual(got["rows"][0]["error"], "TIMEOUT")
        got = stub(CANONICAL, self.TREE, paths=["/coaches/c1"], timeoutMs=100, linger=300)
        self.assertEqual(got["unhandled"], 0)

    def test_the_module_refuses_too(self):
        self.assertEqual(stub(CANONICAL, self.TREE, paths=["/coaches/c1", "/users"])["error"], "REFUSED")
        self.assertEqual(stub(CANONICAL, self.TREE, paths=[f"/coaches/c{i}" for i in range(201)])["error"], "BULK")


@unittest.skipUnless(NODE, "node not installed")
class UserAgent(unittest.TestCase):
    def js(self, env):
        # Only the variables the test names reach Node: an override left in the shell must not leak in.
        base = {k: v for k, v in BARE.items() if k not in ("AINATIVE_UA_WHO", "AINATIVE_SESSION_NAME")}
        out = subprocess.run([NODE, "-e", "console.log(require(process.argv[1]).userAgent())", str(CANONICAL)],
                             capture_output=True, text=True, encoding="utf-8", env={**base, **env}, timeout=30)
        return out.stdout.strip()

    def test_same_format_as_gcloud_sa(self):
        ua = self.js({"AINATIVE_UA_WHO": "j.doe@example.com", "AINATIVE_SESSION_NAME": "Firebase Security"})
        m = FORMAT.match(ua)
        self.assertIsNotNone(m, ua)
        self.assertEqual((m["who"], m["plugin"], m["skill"], m["session"]), ("j.doe", "eng", "read-rtdb-paths", "Firebase-Security"))

    def test_same_person_as_gcloud_sa(self):
        g = REPO / "plugins/eng/skills/investigate-conversation-guardrail/scripts/gcloud_sa.py"
        spec = importlib.util.spec_from_file_location("gcloud_sa_for_rtdb_get", g)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with mock.patch.dict(os.environ, {k: v for k, v in os.environ.items() if k != "AINATIVE_UA_WHO"}, clear=True):
            self.assertEqual(FORMAT.match(self.js({}))["who"], mod._ua_who())
        for value in ("12345+jdoe@users.noreply.github.com", "Jo Doe"):
            with self.subTest(value=value), mock.patch.dict(os.environ, {"AINATIVE_UA_WHO": value}):
                self.assertEqual(FORMAT.match(self.js({"AINATIVE_UA_WHO": value}))["who"], mod._ua_who())

    def test_a_python_caller_can_pass_its_own(self):
        self.assertEqual(self.js({"AINATIVE_USER_AGENT": "AuraClaude/x/eng:check-one-auth@1 (session s)"}),
                         "AuraClaude/x/eng:check-one-auth@1 (session s)")


if __name__ == "__main__":
    unittest.main()
