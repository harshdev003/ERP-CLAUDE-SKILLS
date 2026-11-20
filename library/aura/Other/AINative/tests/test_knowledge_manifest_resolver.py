"""Pins ``manifest.py`` — the run-time reader of a skill's ``knowledge:`` frontmatter.

Parameterised over every copy (canonical in ``retrieve-knowledge``, byte-identical copy in
``record-knowledge``). Membership is asserted by shape — any module under ``plugins/`` that
defines ``check_write`` must be declared here — so a third copy under any filename fails
rather than silently escaping coverage.

What is pinned, and why each matters:

- the parser reads the frontmatter *slice* only (body examples of the convention exist);
- the parser accepts indentless sequences (``- item`` at its key's column) — the shape the
  install cache re-serialises every SKILL.md into, pinned by real fixtures from it;
- the resolution ladder finds a sibling plugin's SKILL.md in all three layouts the host
  uses, from the script's own root or ``CLAUDE_PLUGIN_ROOT`` — never cwd;
- agents resolve too: ``<plugin>:<agent>`` through the same ladder, a bare name under
  ``$CLAUDE_PROJECT_DIR/.claude/agents`` then ``~/.claude/agents`` (never cwd), by filename
  or by frontmatter ``name`` — the id the host itself uses;
- ``--check-write`` distinguishes refused (1) from unresolvable (2), because collapsing the
  two turns a tooling fault into either a silent allow or a spurious refusal.

Stdlib only, no network.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
CANONICAL = REPO / "plugins/shared/skills/retrieve-knowledge/scripts/manifest.py"
DECLARED = sorted({
    CANONICAL,
    REPO / "plugins/shared/skills/record-knowledge/scripts/manifest.py",
})


def _load(path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(f"manifest_{abs(hash(path))}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _discovered() -> list:
    out = []
    for p in REPO.glob("plugins/**/scripts/*.py"):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        if "def check_write(" in text and "knowledge" in text:
            out.append(p)
    return sorted(out)


FM = '\nname: demo\ndescription: d\nmetadata:\n  version: "1.2.3"\nknowledge:\n  reads:\n    - Guide/Index          # baseline\n    - "Tools/Slack"        # quoted\n    - Tools/Aura API/Users # spaces in path\n  writes:\n    - Learnings/Experiments\n'


class Membership(unittest.TestCase):
    def test_every_copy_is_declared_and_every_declared_copy_exists(self):
        self.assertEqual(_discovered(), DECLARED, "a manifest resolver appeared or vanished — update DECLARED")

    def test_copies_are_byte_identical_to_the_canonical(self):
        for p in DECLARED:
            with self.subTest(copy=str(p.relative_to(REPO))):
                self.assertEqual(p.read_bytes(), CANONICAL.read_bytes())


class Parsing(unittest.TestCase):
    def test_shapes(self):
        for p in DECLARED:
            m = _load(p)
            with self.subTest(copy=p.name):
                kb = m.parse_knowledge(FM)
                self.assertEqual(kb["reads"], ["Guide/Index", "Tools/Slack", "Tools/Aura API/Users"])
                self.assertEqual(kb["writes"], ["Learnings/Experiments"])
                self.assertEqual(m.parse_version(FM), "1.2.3")
                self.assertEqual(m.parse_name(FM), "demo")

    def test_inline_lists_and_missing_writes(self):
        for p in DECLARED:
            m = _load(p)
            with self.subTest(copy=p.name):
                self.assertEqual(m.parse_knowledge('\nknowledge:\n  reads:\n    - Guide/Index\n  writes: ["*"]\n')["writes"], ["*"])
                self.assertEqual(m.parse_knowledge("\nknowledge:\n  reads:\n    - Guide/Index\n  writes: []\n")["writes"], [])
                self.assertIsNone(m.parse_knowledge("\nknowledge:\n  reads:\n    - Guide/Index\n")["writes"])

    def test_only_the_frontmatter_slice_is_read(self):
        text = "---\nname: x\nmetadata:\n  version: \"9.9.9\"\nknowledge:\n  reads:\n    - Guide/Index\n---\n\n```yaml\nmetadata:\n  version: \"1.0.0\"\nknowledge:\n  writes: [\"*\"]\n```\n"
        for p in DECLARED:
            m = _load(p)
            with self.subTest(copy=p.name):
                fm = m.split_frontmatter(text)
                self.assertEqual(m.parse_version(fm), "9.9.9")
                self.assertIsNone(m.parse_knowledge(fm)["writes"])

    def test_missing_fence_is_an_error_not_an_empty_manifest(self):
        for p in DECLARED:
            m = _load(p)
            with self.subTest(copy=p.name):
                with self.assertRaises(m.ManifestError):
                    m.split_frontmatter("# just a body\n")

    def test_indentless_sequences(self):
        fm = '\nknowledge:\n  reads:\n  - Guide/Index   # baseline\n  - "Tools/Slack"\n  writes:\n  - Learnings/Experiments\nother: x\n'
        for p in DECLARED:
            m = _load(p)
            with self.subTest(copy=p.name):
                kb = m.parse_knowledge(fm)
                self.assertEqual(kb["reads"], ["Guide/Index", "Tools/Slack"])
                self.assertEqual(kb["writes"], ["Learnings/Experiments"])

    def test_sibling_nested_key_ends_an_indentless_list(self):
        fm = "\nknowledge:\n  reads:\n  - Guide/Index\n  notes:\n  - not-a-read\n"
        for p in DECLARED:
            m = _load(p)
            with self.subTest(copy=p.name):
                self.assertEqual(m.parse_knowledge(fm)["reads"], ["Guide/Index"])

    def test_flow_list_with_trailing_comment(self):
        fm = '\nknowledge:\n  reads:\n    - Guide/Index\n  writes: ["*"]     # wildcard: routes at runtime\n'
        for p in DECLARED:
            m = _load(p)
            with self.subTest(copy=p.name):
                self.assertEqual(m.parse_knowledge(fm)["writes"], ["*"])

    def test_real_installed_snapshots(self):
        """Frontmatter copied verbatim from the rpm install cache, where every list is indentless."""
        fixtures = REPO / "tests/fixtures"
        for p in DECLARED:
            m = _load(p)
            with self.subTest(copy=p.name):
                sync = m.load_manifest(str(fixtures / "installed_sync_experiments_SKILL.md"))
                self.assertEqual(sync["writes"], ["Learnings/Experiments"])
                self.assertEqual(sync["reads"][0], "Guide/Index")
                self.assertEqual(len(sync["reads"]), 7)
                self.assertTrue(m.check_write(sync, "Learnings/Experiments")[0])
                self.assertFalse(m.check_write(sync, "Signals/Cancellations")[0])
                harvest = m.load_manifest(str(fixtures / "installed_harvest_knowledge_SKILL.md"))
                self.assertIn("*", harvest["reads"])
                self.assertEqual(harvest["writes"], ["*"])

    def test_every_real_skill_parses(self):
        m = _load(CANONICAL)
        for skill_md in REPO.glob("plugins/*/skills/*/SKILL.md"):
            man = m.load_manifest(str(skill_md))
            self.assertTrue(man["version"], skill_md)
            self.assertIsNotNone(man["reads"], skill_md)


class CheckWrite(unittest.TestCase):
    def cases(self, m):
        declared = {"writes": ["Learnings/Experiments"]}
        wildcard = {"writes": ["*"]}
        none = {"writes": None}
        yield declared, "Learnings/Experiments", True
        yield declared, "Learnings/Ideas", False
        yield declared, "Signals/Cancellations", False
        yield wildcard, "Signals/Anything", True
        yield wildcard, "Learnings/Anything", True
        yield wildcard, "Identity/Brand", False          # boundary holds under "*"
        yield wildcard, "Guide/Index", False
        yield none, "Learnings/Experiments", False       # no key → refuse everything
        yield {"writes": []}, "Learnings/Experiments", False

    def test_rules(self):
        for p in DECLARED:
            m = _load(p)
            for manifest, path, expect in self.cases(m):
                with self.subTest(copy=p.name, writes=manifest["writes"], path=path):
                    allowed, reason = m.check_write(manifest, path)
                    self.assertEqual(allowed, expect, reason)
                    self.assertTrue(reason)


class Resolution(unittest.TestCase):
    def test_in_repo_cross_plugin_hop(self):
        for p in DECLARED:
            m = _load(p)
            with self.subTest(copy=p.name):
                found = m.skill_md_path("data", "analyze-experiment", here=str(p.parent), plugin_root_env="")
                self.assertEqual(pathlib.Path(found), REPO / "plugins/data/skills/analyze-experiment/SKILL.md")
                self.assertEqual(m.skill_md_path("nope", "nothing", here=str(p.parent), plugin_root_env=""), "")

    def _write(self, root: pathlib.Path, rel: str, text: str) -> pathlib.Path:
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
        return f

    def test_install_cache_layout_prefers_matching_version_segment(self):
        m = _load(CANONICAL)
        with tempfile.TemporaryDirectory() as tmp:
            cache = pathlib.Path(tmp) / "aura-tools"
            here = cache / "shared/3.26.4/skills/retrieve-knowledge/scripts"
            here.mkdir(parents=True)
            self._write(cache, "data/3.18.3/skills/analyze-experiment/SKILL.md", "---\nname: a\n---\nnew")
            self._write(cache, "data/3.18.2/skills/analyze-experiment/SKILL.md", "---\nname: a\n---\nold")
            self._write(cache, "data/3.26.4/skills/analyze-experiment/SKILL.md", "---\nname: a\n---\nsame-seg")
            found = m.skill_md_path("data", "analyze-experiment", here=str(here), plugin_root_env="")
            # a version segment equal to the running plugin's sorts first
            self.assertTrue(found.endswith(os.path.join("data", "3.26.4", "skills", "analyze-experiment", "SKILL.md")), found)

    def test_local_agent_mode_opaque_ids_resolve_via_manifest_json(self):
        m = _load(CANONICAL)
        with tempfile.TemporaryDirectory() as tmp:
            rpm = pathlib.Path(tmp) / "rpm"
            here = rpm / "plugin_SHARED/skills/retrieve-knowledge/scripts"
            here.mkdir(parents=True)
            target = self._write(rpm, "plugin_DATA/skills/analyze-experiment/SKILL.md", "---\nname: a\n---\n")
            (rpm / "manifest.json").write_text(json.dumps({"plugins": [
                {"id": "plugin_SHARED", "name": "shared"}, {"id": "plugin_DATA", "name": "data"}]}))
            found = m.skill_md_path("data", "analyze-experiment", here=str(here), plugin_root_env="")
            self.assertEqual(pathlib.Path(found), target)
            # a malformed manifest degrades to "not found", never to a guess
            (rpm / "manifest.json").write_text("{not json")
            self.assertEqual(m.skill_md_path("data", "analyze-experiment", here=str(here), plugin_root_env=""), "")

    def test_claude_plugin_root_is_a_second_root_not_cwd(self):
        m = _load(CANONICAL)
        with tempfile.TemporaryDirectory() as tmp:
            # script lives somewhere unrelated; CLAUDE_PLUGIN_ROOT points at an install root
            here = pathlib.Path(tmp) / "elsewhere/skills/x/scripts"
            here.mkdir(parents=True)
            root = pathlib.Path(tmp) / "cache/shared"
            target = self._write(root, "skills/post-to-slack/SKILL.md", "---\nname: p\n---\n")
            found = m.skill_md_path("shared", "post-to-slack", here=str(here), plugin_root_env=str(root))
            self.assertEqual(pathlib.Path(found), target)
            # with the env root removed, cwd must not rescue it
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                self.assertEqual(m.skill_md_path("shared", "post-to-slack", here=str(here), plugin_root_env=""), "")
            finally:
                os.chdir(cwd)


class Agents(unittest.TestCase):
    """Agent files carry the same manifest; the host names them by frontmatter ``name``."""

    AGENT = "---\nname: {name}\ndescription: d\nknowledge:\n  reads:\n    - Guide/Index\n    - Tools/Mixpanel\n  writes:\n    - Signals/Production Health\n---\nbody\n"

    def _write(self, root: pathlib.Path, rel: str, text: str) -> pathlib.Path:
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
        return f

    def test_in_repo_plugin_agent(self):
        for p in DECLARED:
            m = _load(p)
            with self.subTest(copy=p.name):
                found = m.agent_md_path("eng", "code-investigator", here=str(p.parent), plugin_root_env="")
                self.assertEqual(pathlib.Path(found), REPO / "plugins/eng/agents/code-investigator.md")
                self.assertEqual(m.agent_md_path("eng", "nobody", here=str(p.parent), plugin_root_env=""), "")

    def test_install_cache_layout_prefers_matching_version_segment(self):
        m = _load(CANONICAL)
        with tempfile.TemporaryDirectory() as tmp:
            cache = pathlib.Path(tmp) / "aura-tools"
            here = cache / "shared/3.26.4/skills/retrieve-knowledge/scripts"
            here.mkdir(parents=True)
            self._write(cache, "eng/3.18.3/agents/rev.md", self.AGENT.format(name="rev"))
            self._write(cache, "eng/3.26.4/agents/rev.md", self.AGENT.format(name="rev"))
            found = m.agent_md_path("eng", "rev", here=str(here), plugin_root_env="")
            self.assertTrue(found.endswith(os.path.join("eng", "3.26.4", "agents", "rev.md")), found)

    def test_frontmatter_name_when_the_filename_differs(self):
        m = _load(CANONICAL)
        with tempfile.TemporaryDirectory() as tmp:
            target = self._write(pathlib.Path(tmp), ".claude/agents/mp.md", self.AGENT.format(name="mixpanel-reviewer"))
            self._write(pathlib.Path(tmp), ".claude/agents/broken.md", "no frontmatter here\n")
            found = m.agent_md_path("", "mixpanel-reviewer", project_dir_env=tmp, home="")
            self.assertEqual(pathlib.Path(found), target)

    def test_project_beats_user_and_cwd_never_rescues(self):
        m = _load(CANONICAL)
        with tempfile.TemporaryDirectory() as proj, tempfile.TemporaryDirectory() as home:
            user = self._write(pathlib.Path(home), ".claude/agents/rev.md", self.AGENT.format(name="rev"))
            self.assertEqual(pathlib.Path(m.agent_md_path("", "rev", project_dir_env=proj, home=home)), user)
            mine = self._write(pathlib.Path(proj), ".claude/agents/rev.md", self.AGENT.format(name="rev"))
            self.assertEqual(pathlib.Path(m.agent_md_path("", "rev", project_dir_env=proj, home=home)), mine)
            cwd = os.getcwd()
            os.chdir(proj)
            try:
                self.assertEqual(m.agent_md_path("", "rev", project_dir_env="", home=""), "")
            finally:
                os.chdir(cwd)

    def test_manifest_kind_and_id(self):
        m = _load(CANONICAL)
        man = m.load_manifest(str(REPO / "plugins/eng/agents/code-investigator.md"))
        self.assertEqual((man["skill"], man["kind"], man["plugin_source"]), ("eng:code-investigator", "agent", "manifest"))
        with tempfile.TemporaryDirectory() as tmp:
            local = self._write(pathlib.Path(tmp), ".claude/agents/mp.md", self.AGENT.format(name="mixpanel-reviewer"))
            man = m.load_manifest(str(local))
            self.assertEqual((man["skill"], man["kind"], man["plugin_source"]), ("mixpanel-reviewer", "agent", "local"))
            self.assertEqual(man["writes"], ["Signals/Production Health"])
            self.assertTrue(m.check_write(man, "Signals/Production Health")[0])
            self.assertFalse(m.check_write(man, "Signals/Cancellations")[0])
        skill = m.load_manifest(str(REPO / "plugins/data/skills/analyze-experiment/SKILL.md"))
        self.assertEqual(skill["kind"], "skill")

    def test_every_real_agent_parses(self):
        m = _load(CANONICAL)
        for agent_md in REPO.glob("plugins/*/agents/*.md"):
            man = m.load_manifest(str(agent_md))
            self.assertEqual(man["kind"], "agent", agent_md)
            self.assertEqual(man["plugin_source"], "manifest", agent_md)

    def test_one_selector_at_a_time(self):
        m = _load(CANONICAL)
        with self.assertRaises(m.ManifestError):
            m.resolve_target("x.md", "data:analyze-experiment", None)
        with self.assertRaises(m.ManifestError):
            m.resolve_target(None, "data:analyze-experiment", "eng:code-investigator")


class PluginName(unittest.TestCase):
    """The plugin half of the id must come from plugin.json — the directory is the version folder on installs."""

    def test_checkout_layout_reads_plugin_json(self):
        m = _load(CANONICAL)
        man = m.load_manifest(str(REPO / "plugins/data/skills/analyze-experiment/SKILL.md"))
        self.assertEqual((man["skill"], man["plugin_source"]), ("data:analyze-experiment", "manifest"))

    def test_versioned_install_layout_does_not_report_the_version_folder(self):
        m = _load(CANONICAL)
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "aura-tools/shared/3.27.0"
            (root / ".claude-plugin").mkdir(parents=True)
            (root / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "shared", "version": "3.27.0"}))
            skill = root / "skills/search-knowledge"; skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("---\nname: search-knowledge\nmetadata:\n  version: \"3.0.0\"\nknowledge:\n  reads:\n    - Guide/Index\n---\n")
            man = m.load_manifest(str(skill / "SKILL.md"))
            self.assertEqual(man["skill"], "shared:search-knowledge")
            self.assertEqual(man["plugin_source"], "manifest")
            # without any plugin.json the fallback is honest about being a guess
            (root / ".claude-plugin/plugin.json").unlink()
            man = m.load_manifest(str(skill / "SKILL.md"))
            self.assertEqual(man["plugin_source"], "dirname")
            self.assertEqual(man["skill"], "3.27.0:search-knowledge")


class Cli(unittest.TestCase):
    def run_cli(self, copy, *args):
        env = dict(os.environ)
        env.pop("CLAUDE_PLUGIN_ROOT", None)
        return subprocess.run([sys.executable, str(copy), *args], capture_output=True, text=True, env=env, cwd=tempfile.gettempdir())

    def test_exit_codes_are_allowed_refused_unresolvable(self):
        for p in DECLARED:
            with self.subTest(copy=p.name):
                ok = self.run_cli(p, "--skill", "data:analyze-experiment", "--check-write", "Learnings/Experiments")
                self.assertEqual(ok.returncode, 0, ok.stderr)
                self.assertTrue(json.loads(ok.stdout)["allowed"])
                no = self.run_cli(p, "--skill", "data:analyze-experiment", "--check-write", "Signals/Cancellations")
                self.assertEqual(no.returncode, 1)
                self.assertFalse(json.loads(no.stdout)["allowed"])
                gone = self.run_cli(p, "--skill", "nope:nothing", "--check-write", "Learnings/X")
                self.assertEqual(gone.returncode, 2)
                self.assertEqual(gone.stdout, "")
                self.assertIn("error", json.loads(gone.stderr))

    def test_plain_dump_names_the_skill_and_version(self):
        out = self.run_cli(CANONICAL, str(REPO / "plugins/shared/skills/retrieve-knowledge"))
        self.assertEqual(out.returncode, 0, out.stderr)
        data = json.loads(out.stdout)
        self.assertEqual(data["skill"], "shared:retrieve-knowledge")
        self.assertIn("Guide/Index", data["reads"])
        self.assertIsNone(data["writes"])


    def test_agent_selector_and_agent_path_check_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = pathlib.Path(tmp) / ".claude/agents/mp.md"
            agent.parent.mkdir(parents=True)
            agent.write_text(Agents.AGENT.format(name="mixpanel-reviewer"), encoding="utf-8")
            for p in DECLARED:
                with self.subTest(copy=p.name):
                    ok = self.run_cli(p, str(agent), "--check-write", "Signals/Production Health")
                    self.assertEqual(ok.returncode, 0, ok.stderr)
                    self.assertEqual(json.loads(ok.stdout)["kind"], "agent")
                    no = self.run_cli(p, str(agent), "--check-write", "Learnings/Experiments")
                    self.assertEqual(no.returncode, 1)
            env = dict(os.environ, CLAUDE_PROJECT_DIR=tmp)
            env.pop("CLAUDE_PLUGIN_ROOT", None)
            by_name = subprocess.run([sys.executable, str(CANONICAL), "--agent", "mixpanel-reviewer"],
                                     capture_output=True, text=True, env=env, cwd=tempfile.gettempdir())
            self.assertEqual(by_name.returncode, 0, by_name.stderr)
            self.assertEqual(json.loads(by_name.stdout)["skill"], "mixpanel-reviewer")
        plugin = self.run_cli(CANONICAL, "--agent", "eng:code-investigator")
        self.assertEqual(plugin.returncode, 0, plugin.stderr)
        self.assertEqual(json.loads(plugin.stdout)["skill"], "eng:code-investigator")
        gone = self.run_cli(CANONICAL, "--agent", "eng:nobody")
        self.assertEqual(gone.returncode, 2)


if __name__ == "__main__":
    unittest.main()
