"""eng:update-node-packages bump_packages.sh picks the right pins (ENG-1751).

The repo-specific pins were chosen by the checkout directory's name, so a worktree named
`wt-2170-PaymentService` matched no repo and silently skipped the jose and app-store-server-api pins
(PaymentService, since lifted) and the jose override and metascraper pins (UserService). Both repos' test suites then
failed to load on ESM-only packages (ENGBACK-2170). The name now comes from the origin remote, can be
passed explicitly, and every applied pin is printed. Functions also hold @types/node at their
engines.node major.

The real script runs against a real git repo; `npm` and `npx` are fakes that log their arguments, so
nothing is installed and nothing reaches the registry.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import stat
import subprocess
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "plugins/eng/skills/update-node-packages/scripts/bump_packages.sh"
FAKE = """#!/bin/sh
echo "$(basename "$0") $*" >> "$CALLS"
exit 0
"""


@unittest.skipUnless(shutil.which("node") and shutil.which("git"), "node and git needed")
class BumpPins(unittest.TestCase):
    def repo(self, dirname, origin, package, sub=""):
        root = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        bins = root / "bin"
        bins.mkdir()
        for name in ("npm", "npx"):
            (bins / name).write_text(FAKE, encoding="utf-8")
            (bins / name).chmod(stat.S_IRWXU)
        repo = root / dirname
        work = repo / sub if sub else repo
        work.mkdir(parents=True)
        (work / "package.json").write_text(json.dumps(package), encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        if origin:
            subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", origin], check=True)
        self.calls = root / "calls.log"
        self.env = {**os.environ, "PATH": f"{bins}{os.pathsep}{os.environ['PATH']}", "CALLS": str(self.calls),
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
        return repo

    def bump(self, repo, tier, *extra):
        r = subprocess.run(["bash", str(SCRIPT), str(repo), tier, *extra], capture_output=True, text=True,
                           encoding="utf-8", env=self.env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        calls = self.calls.read_text(encoding="utf-8") if self.calls.exists() else ""
        return r.stdout, calls

    def test_paymentservice_takes_the_latest_with_only_the_eslint_pin(self):
        # Since v2.231.31 PaymentService runs jose 6, uuid 14, nanoid 6 and
        # app-store-server-api 1.0, so none of them is held back.
        repo = self.repo("wt-2170-PaymentService", "git@github.com:AuraHealth/PaymentService.git",
                         {"name": "x", "dependencies": {"jose": "^6.0.0"}})
        out, calls = self.bump(repo, "services")
        for held in ("jose", "app-store-server-api", "nanoid", "uuid"):
            with self.subTest(package=held):
                self.assertNotIn(held, calls)
        self.assertIn("PINS [PaymentService/services]: eslint@^9\n", out)

    def test_userservice_over_https_gets_its_override_and_metascraper_pins(self):
        repo = self.repo("wt-2170-UserService", "https://github.com/AuraHealth/UserService.git",
                         {"name": "x", "dependencies": {"metascraper": "^5.51.0", "metascraper-title": "^5.51.0"}})
        out, calls = self.bump(repo, "services")
        self.assertIn("npm pkg set overrides.jose=5.10.0", calls)
        self.assertIn("npm i -S metascraper@5.50.6 metascraper-title@5.50.6", calls)
        self.assertIn("PINS [UserService/services]:", out)
        self.assertIn("metascraper-title@5.50.6", out)

    def test_an_explicit_name_wins_and_no_remote_falls_back_to_the_directory(self):
        repo = self.repo("checkout", "git@github.com:AuraHealth/FeedService.git", {"name": "x"})
        out, calls = self.bump(repo, "services", "UserService")
        self.assertIn("overrides.jose=5.10.0", calls)
        repo = self.repo("UserService", None, {"name": "x"})
        out, _ = self.bump(repo, "services")
        self.assertIn("PINS [UserService/services]:", out)

    def test_another_service_gets_only_the_tier_pins(self):
        repo = self.repo("wt-feed", "git@github.com:AuraHealth/FeedService.git", {"name": "x"})
        out, calls = self.bump(repo, "services")
        self.assertNotIn("jose", calls)
        self.assertIn("PINS [FeedService/services]: eslint@^9 nanoid@^3 uuid@^11\n", out)

    def test_functions_hold_types_node_at_the_engines_major(self):
        repo = self.repo("wt-fn", "git@github.com:AuraHealth/Functions-Auth.git",
                         {"name": "fn", "engines": {"node": "24"}, "devDependencies": {"@types/node": "^24.3.0"}},
                         sub="functions")
        out, calls = self.bump(repo, "functions")
        self.assertIn("npm i -D @types/node@^24", calls)
        self.assertIn("PINS [Functions-Auth/functions]: eslint@^9 @types/node@^24", out)
        self.assertNotIn("firebase-admin", calls)

    def test_functions_without_types_node_or_engines_skip_that_pin(self):
        repo = self.repo("wt-fn", "git@github.com:AuraHealth/Functions-Auth.git",
                         {"name": "fn", "engines": {"node": ">=24"}}, sub="functions")
        out, calls = self.bump(repo, "functions")
        self.assertNotIn("@types/node", calls)
        self.assertIn("PINS [Functions-Auth/functions]: eslint@^9\n", out)

    # websocket-driver 0.8.0 breaks RTDB reads through faye-websocket: every repo on firebase or
    # firebase-admin, in any tier, keeps the 0.7.5 override; a repo on neither gets none.
    def test_a_functions_repo_on_firebase_admin_keeps_the_override(self):
        repo = self.repo("wt-fn", "git@github.com:AuraHealth/Functions-Auth.git",
                         {"name": "fn", "dependencies": {"firebase-admin": "^14.5.1"}}, sub="functions")
        out, calls = self.bump(repo, "functions")
        self.assertIn("npm pkg set overrides.websocket-driver=0.7.5", calls)
        self.assertIn("PINS [Functions-Auth/functions]: eslint@^9 overrides.websocket-driver=0.7.5\n", out)

    def test_the_cli_on_firebase_keeps_the_override(self):
        repo = self.repo("CLI", "git@github.com:AuraHealth/CLI.git",
                         {"name": "cli", "devDependencies": {"firebase": "^12.0.0"}})
        out, calls = self.bump(repo, "cli")
        self.assertIn("npm pkg set overrides.websocket-driver=0.7.5", calls)

    def test_a_repo_on_neither_gets_no_override(self):
        repo = self.repo("wt-feed", "git@github.com:AuraHealth/FeedService.git", {"name": "x"})
        out, calls = self.bump(repo, "services")
        self.assertNotIn("websocket-driver", calls)


if __name__ == "__main__":
    unittest.main()
