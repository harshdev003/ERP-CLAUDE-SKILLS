"""eng:bugbot-review calls a PR clean only when no BugBot thread from any run is unresolved (ENG-1697).

A BugBot run reviews only its own diff, so a run with 0 comments said nothing about a High left
open by the run before it, and wait mode offered `gh pr ready` anyway. The SKILL.md now reads
every unresolved `cursor` review thread over GraphQL. These tests run that exact jq filter,
extracted from the SKILL.md, over pages shaped like `gh api graphql --paginate` output.
"""

import json
import pathlib
import re
import shutil
import subprocess
import unittest

SKILL = pathlib.Path(__file__).resolve().parents[1] / "plugins/eng/skills/bugbot-review/SKILL.md"


def _open_threads_command():
    text = SKILL.read_text(encoding="utf-8")
    m = re.search(r"OPEN_THREADS=\$\(gh api graphql.*?\n\s*\| jq -s '([^']*)'\)", text, re.S)
    if not m:
        raise AssertionError("OPEN_THREADS command not found in bugbot-review SKILL.md")
    return m.group(0), m.group(1)


def _thread(author, resolved, outdated=False, line=7, original=7, url="u"):
    return {"isResolved": resolved, "isOutdated": outdated, "comments": {"nodes": [{
        "author": {"login": author}, "path": "a.py", "line": line, "originalLine": original,
        "url": url, "body": "### Finding\n\n**High Severity**"}]}}


def _page(*threads):
    return {"data": {"repository": {"pullRequest": {"reviewThreads": {
        "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": list(threads)}}}}}


@unittest.skipUnless(shutil.which("jq"), "jq not installed")
class OpenThreadsFilter(unittest.TestCase):
    def run_filter(self, *pages):
        _, program = _open_threads_command()
        stdin = "".join(json.dumps(p) + "\n" for p in pages)
        out = subprocess.run(["jq", "-s", program], input=stdin, capture_output=True, text=True, encoding="utf-8", check=True)
        return json.loads(out.stdout)

    def test_an_older_unresolved_thread_keeps_a_zero_finding_run_from_reading_clean(self):
        latest_run_findings = []
        got = self.run_filter(_page(_thread("cursor", False, outdated=True, line=None, original=12, url="old")))
        self.assertEqual(latest_run_findings, [])
        self.assertEqual([(t["url"], t["line"], t["outdated"]) for t in got], [("old", 12, True)])

    def test_resolved_and_human_threads_are_not_counted(self):
        got = self.run_filter(_page(_thread("cursor", True), _thread("jcurlier", False)))
        self.assertEqual(got, [])

    def test_threads_on_every_page_are_counted(self):
        got = self.run_filter(_page(_thread("cursor", False, url="p1")), _page(_thread("cursor", False, url="p2")))
        self.assertEqual([t["url"] for t in got], ["p1", "p2"])


class QueryAndVerdictWiring(unittest.TestCase):
    def test_the_query_asks_for_what_the_filter_reads(self):
        command, _ = _open_threads_command()
        for field in ("isResolved", "isOutdated", "author{login}", "originalLine", "url", "body",
                      "pageInfo{hasNextPage endCursor}", "--paginate"):
            self.assertIn(field, command)

    def test_clean_requires_no_open_threads(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertRegex(text, r"\*\*Clean\*\* only when this run found 0 issues, `OPEN_THREADS` is `\[\]`")


if __name__ == "__main__":
    unittest.main()
