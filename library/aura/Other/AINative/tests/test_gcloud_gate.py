"""Every gcloud call under plugins/ goes through the skill's own `gcloud_sa.py`.

A raw `gcloud` call runs as whichever account is active, usually the user's login.
That login lapses under Google's reauth policy, and an unattended skill then
degrades without saying why: on 2026-10-20 every conversation-guardrail triage
reply carried handoff category `unknown` for exactly this reason (ENG-1607). The
helper runs gcloud as the GCP_KEY service account instead.

Scanned: scripts (.py/.sh/.js/.mjs/.ts) and the commands a skill tells the model
to run (fenced blocks and `$(gcloud …)` in its markdown). A fenced block whose
first line carries `user-gcloud:` is exempt: setup steps that must run as a person
(granting the service account a role, creating it). Prose that merely names a
gcloud command is not a call.

The detector is checked against fixtures, including the line that caused the
incident — a gate that cannot find the known violation is worth nothing.
"""

from __future__ import annotations

import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = REPO / "plugins"
HELPER = "gcloud_sa.py"
MARKER = "user-gcloud:"

SHELL_CMD = re.compile(r"(^|[;&|]|\$\()\s*gcloud\s")
PY_ARGV = re.compile(r"""\[\s*["']gcloud["']""")  # an argv list, possibly across lines
PY_STRING = re.compile(r"""\b(run|Popen|call|check_call|check_output|system|getoutput)\(\s*f?["']gcloud\s""")
JS_CMD = re.compile(r"""["'`]gcloud[\s"'`]""")
HELPER_REF = re.compile(r"skills/([a-z0-9-]+)/scripts/gcloud_sa\.py")


def violations(path: str, text: str) -> list[int]:
    """1-based line numbers of raw gcloud calls in `text`, judged by `path`'s type."""
    name = pathlib.PurePath(path).name
    if name == HELPER:
        return []
    lines = text.split("\n")
    hits = []
    if name.endswith(".py"):
        code = "\n".join("" if ln.lstrip().startswith("#") else ln for ln in lines)
        for rx in (PY_ARGV, PY_STRING):
            for m in rx.finditer(code):
                hits.append(code.count("\n", 0, m.end()) + 1)
        hits = sorted(set(hits))
    elif name.endswith(".sh"):
        hits = [i for i, ln in enumerate(lines, 1)
                if not ln.lstrip().startswith("#") and SHELL_CMD.search(ln)]
    elif name.endswith((".js", ".mjs", ".ts")):
        hits = [i for i, ln in enumerate(lines, 1)
                if not ln.lstrip().startswith(("//", "*")) and JS_CMD.search(ln)]
    elif name.endswith(".md"):
        in_fence = exempt = False
        for i, ln in enumerate(lines, 1):
            s = ln.strip()
            if s.startswith("```"):
                in_fence = not in_fence
                exempt = in_fence and i < len(lines) and MARKER in lines[i]
                continue
            if in_fence:
                if not exempt and not s.startswith("#") and SHELL_CMD.search(ln):
                    hits.append(i)
            elif "$(gcloud " in ln:
                hits.append(i)
    return hits


def _files():
    for p in sorted(PLUGINS.rglob("*")):
        if p.is_file() and p.suffix in (".py", ".sh", ".js", ".mjs", ".ts", ".md"):
            yield p


class NoRawGcloudInPlugins(unittest.TestCase):
    def test_every_gcloud_call_goes_through_the_helper(self):
        found = []
        for p in _files():
            for line in violations(str(p), p.read_text(encoding="utf-8", errors="replace")):
                found.append(f"{p.relative_to(REPO)}:{line}")
        self.assertEqual(found, [], "raw gcloud call(s) — route through the skill's scripts/gcloud_sa.py "
                                    "(or mark a person-only setup block `# user-gcloud: <why>`)")

    def test_helper_references_point_at_the_skills_own_copy(self):
        """Self-contained: a skill invokes its own copy, and that copy exists."""
        bad = []
        for p in _files():
            text = p.read_text(encoding="utf-8", errors="replace")
            parts = p.relative_to(PLUGINS).parts
            if len(parts) < 3 or parts[1] != "skills":
                continue
            skill_dir = PLUGINS / parts[0] / "skills" / parts[2]
            for m in HELPER_REF.finditer(text):
                if m.group(1) != parts[2]:
                    bad.append(f"{p.relative_to(REPO)} -> {m.group(1)}")
            uses = (HELPER_REF.search(text)
                    or (p.suffix == ".py" and re.search(r"^\s*import gcloud_sa\b", text, re.M))
                    or (p.suffix == ".sh" and "/gcloud_sa.py" in text))
            if uses and p.name != HELPER and not (skill_dir / "scripts" / HELPER).exists():
                bad.append(f"{p.relative_to(REPO)}: no scripts/{HELPER} in its skill")
        self.assertEqual(bad, [])


class DetectorFixtures(unittest.TestCase):
    def test_the_incident_line_is_caught(self):
        # fetch_conversation.py on origin/main before ENG-1607.
        src = ('        out = subprocess.run(\n'
               '            ["gcloud", "logging", "read", flt, "--project", GCP_PROJECT,\n'
               '             "--format", "json", "--limit", "500"],\n')
        self.assertEqual(violations("x/fetch_conversation.py", src), [2])

    def test_python(self):
        self.assertEqual(violations("a.py", 'subprocess.check_output("gcloud auth print-access-token", shell=True)'), [1])
        self.assertEqual(violations("a.py", 'os.system("gcloud config list")'), [1])
        self.assertEqual(violations("a.py", 'out = gcloud_sa.run(["logging", "read"])'), [])
        self.assertEqual(violations("a.py", 'raise X("gcp: gcloud not on PATH")'), [])
        self.assertEqual(violations("a.py", '# subprocess.run(["gcloud", "x"])'), [])
        self.assertEqual(violations("gcloud_sa.py", 'subprocess.run(["gcloud", *args])'), [])
        self.assertEqual(violations("a.py", 'if not shutil.which("gcloud"):'), [])
        self.assertEqual(violations("a.py", 'out = subprocess.run(\n    [\n        "gcloud", "artifacts"]'), [3])

    def test_shell(self):
        self.assertEqual(violations("a.sh", 'TOKEN=$(gcloud auth print-access-token)'), [1])
        self.assertEqual(violations("a.sh", 'x || gcloud auth print-access-token'), [1])
        self.assertEqual(violations("a.sh", 'TOKEN=$(python3 "$(dirname "$0")/gcloud_sa.py" auth print-access-token)'), [])
        self.assertEqual(violations("a.sh", 'echo "run: gcloud auth login"'), [])

    def test_js(self):
        self.assertEqual(violations("a.js", "execSync('gcloud auth print-access-token')"), [1])
        self.assertEqual(violations("a.js", "// execSync('gcloud x')"), [])

    def test_markdown(self):
        md = "\n".join([
            "Run `gcloud logging read` to see it.",                        # prose: not a call
            "```bash",
            "gcloud logging read 'x' --project p",                         # 3: raw
            'echo "No SA env (gcloud auth application-default login)"',    # not a call
            "python3 ${CLAUDE_PLUGIN_ROOT}/skills/s/scripts/gcloud_sa.py logging read 'x'",
            "```",
            "```bash",
            "# user-gcloud: the user grants the role",
            "gcloud projects add-iam-policy-binding p --role r",           # exempt block
            "```",
            "with `Authorization: Bearer $(gcloud auth print-access-token)`",  # 11: inline call
        ])
        self.assertEqual(violations("SKILL.md", md), [3, 11])


if __name__ == "__main__":
    unittest.main()
