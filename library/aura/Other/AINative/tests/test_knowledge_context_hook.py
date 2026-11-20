"""Pins the ``shared`` plugin's knowledge hook: PostToolUse on the Skill tool, and SubagentStart.

The hook is the only thing that gets a skill's ``knowledge:`` manifest in front of the model
without a per-skill body edit, so two properties are load-bearing and both are checked by
running the real script over stdin exactly as Claude Code would:

- it emits ``hookSpecificOutput.additionalContext`` naming every declared read and write for
  a real skill, resolving the SKILL.md from the host's ``Base directory`` line first and from
  the ``<plugin>:<skill>`` ladder when that line is absent;
- **every failure path is silent and exits 0** — a hook that errors on every Skill call would
  make every skill noisy or unusable, and PostToolUse cannot block anyway.

On ``SubagentStart`` it hands a subagent its agent file's manifest, addressed by path, and
stays silent for built-ins and for agents that declare no ``knowledge:`` block.

It also asserts the hook is actually registered (``hooks/hooks.json`` matches ``Skill`` and
points at this script) — a script nobody wires up is the same as no hook.

Stdlib only, no network.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
HOOK = REPO / "plugins/shared/hooks/knowledge_context.py"
HOOKS_JSON = REPO / "plugins/shared/hooks/hooks.json"


def run_hook(payload, env_extra=None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("CLAUDE_PLUGIN_ROOT", None)
    env.update(env_extra or {})
    data = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run([sys.executable, str(HOOK)], input=data, capture_output=True, text=True,
                          env=env, cwd=tempfile.gettempdir())


def context_of(proc) -> str:
    out = json.loads(proc.stdout)
    return out["hookSpecificOutput"]["additionalContext"]


class Registration(unittest.TestCase):
    def test_hooks_json_wires_this_script_to_the_skill_tool(self):
        cfg = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
        entries = cfg["hooks"]["PostToolUse"]
        matchers = [e["matcher"] for e in entries]
        self.assertIn("Skill", matchers)
        cmds = [h["command"] for e in entries for h in e["hooks"]]
        self.assertTrue(any("knowledge_context.py" in c and "${CLAUDE_PLUGIN_ROOT}" in c for c in cmds), cmds)

    def test_hooks_json_wires_this_script_to_subagent_start(self):
        cfg = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
        cmds = [h["command"] for e in cfg["hooks"]["SubagentStart"] for h in e["hooks"]]
        self.assertTrue(any("knowledge_context.py" in c and "${CLAUDE_PLUGIN_ROOT}" in c for c in cmds), cmds)

    def test_no_other_plugin_ships_a_competing_hook_on_skill(self):
        # One source of the injected context; a second would double it on every load.
        for hj in REPO.glob("plugins/*/hooks/hooks.json"):
            if hj == HOOKS_JSON:
                continue
            cfg = json.loads(hj.read_text(encoding="utf-8"))
            for e in cfg.get("hooks", {}).get("PostToolUse", []):
                self.assertNotEqual(e.get("matcher"), "Skill", f"{hj} also hooks Skill")


class HappyPath(unittest.TestCase):
    def test_base_directory_line_is_authoritative(self):
        base = REPO / "plugins/data/skills/analyze-experiment"
        proc = run_hook({"tool_name": "Skill", "tool_input": {"skill": "data:analyze-experiment"},
                         "tool_response": f"Base directory for this skill: {base}\n\n# Analyze Experiment"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        ctx = context_of(proc)
        self.assertIn("`data:analyze-experiment`", ctx)
        self.assertIn("`Guide/Index`", ctx)
        self.assertIn("`Learnings/Experiments`", ctx)
        self.assertIn("caller=data:analyze-experiment", ctx)
        self.assertIn("skill=data:analyze-experiment", ctx)

    def test_base_directory_is_found_inside_a_structured_response_too(self):
        base = REPO / "plugins/eng/skills/report-quota"
        proc = run_hook({"tool_name": "Skill", "tool_input": {"skill": "eng:report-quota"},
                         "tool_response": {"content": [{"type": "text", "text": f"Base directory for this skill: {base}\n# Report quota"}]}})
        self.assertIn("`Tools/Deepgram`", context_of(proc))

    def test_ladder_fallback_when_no_base_directory_line(self):
        proc = run_hook({"tool_name": "Skill", "tool_input": {"skill": "eng:report-quota"}, "tool_response": "no path here"})
        ctx = context_of(proc)
        self.assertIn("`eng:report-quota`", ctx)
        self.assertIn("none declared", ctx)          # report-quota has no writes: key
        self.assertIn("allow_undeclared", ctx)

    def test_wildcard_manifest_says_no_prefetch(self):
        ctx = context_of(run_hook({"tool_name": "Skill", "tool_input": {"skill": "shared:harvest-knowledge"}}))
        self.assertIn("no pre-fetch", ctx)
        self.assertIn("caller=shared:harvest-knowledge", ctx)

    def test_context_names_every_declared_read(self):
        # Derive expectations from the file so the test cannot drift from the manifest.
        sys.path.insert(0, str(REPO / "plugins/shared/skills/retrieve-knowledge/scripts"))
        import manifest  # noqa: E402
        man = manifest.load_manifest(str(REPO / "plugins/eng/skills/investigate-bugsnag-errors/SKILL.md"))
        ctx = context_of(run_hook({"tool_name": "Skill", "tool_input": {"skill": "eng:investigate-bugsnag-errors"}}))
        for r in man["reads"]:
            self.assertIn(f"`{r}`", ctx)


class HostLayouts(unittest.TestCase):
    """Findings from the first review: escape handling and ids on installed copies."""

    def _install(self, tmp, with_plugin_json=True):
        root = pathlib.Path(tmp) / "cache/aura-tools/eng/3.99.0"
        if with_plugin_json:
            (root / ".claude-plugin").mkdir(parents=True)
            (root / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "eng", "version": "3.99.0"}))
        skill = root / "skills/report-quota"; skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: report-quota\nmetadata:\n  version: \"9.0.0\"\nknowledge:\n  reads:\n    - Guide/Index\n    - Tools/Deepgram\n  writes:\n    - Signals/Quota\n---\n")
        return skill

    def test_backslash_n_in_a_path_is_not_a_line_break(self):
        # A JSON-structured response whose path component starts with "n" must survive intact.
        with tempfile.TemporaryDirectory() as tmp:
            skill = self._install(tmp)
            weird = pathlib.Path(tmp) / "new" / "next"      # components starting with n
            weird.mkdir(parents=True)
            (weird / "SKILL.md").write_text((skill / "SKILL.md").read_text())
            proc = run_hook({"tool_name": "Skill", "tool_input": {"skill": "eng:report-quota"},
                             "tool_response": {"content": [{"type": "text", "text": f"Base directory for this skill: {weird}\n# body"}]}})
            ctx = context_of(proc)
            self.assertIn("`Tools/Deepgram`", ctx)
            self.assertIn("v9.0.0", ctx)                     # proves the weird dir, not the repo copy, was read

    def test_no_plugin_prefix_on_an_install_uses_plugin_json_for_the_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = self._install(tmp)
            proc = run_hook({"tool_name": "Skill", "tool_input": {"skill": "report-quota"},
                             "tool_response": f"Base directory for this skill: {skill}\n# body"})
            ctx = context_of(proc)
            self.assertIn("skill=eng:report-quota", ctx)
            self.assertIn("caller=eng:report-quota", ctx)
            self.assertNotIn("3.99.0:", ctx)

    def test_no_plugin_prefix_and_no_plugin_json_falls_back_to_the_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = self._install(tmp, with_plugin_json=False)
            proc = run_hook({"tool_name": "Skill", "tool_input": {"skill": "report-quota"},
                             "tool_response": f"Base directory for this skill: {skill}\n# body"})
            ctx = context_of(proc)
            self.assertNotIn("3.99.0:", ctx)                 # never a version folder as a plugin id
            self.assertIn(f"skill_dir={skill}", ctx)
            self.assertIn(f"caller={skill}", ctx)


class SilentFailure(unittest.TestCase):
    """Anything the hook cannot handle produces no output and exit 0."""

    def assert_silent(self, payload):
        proc = run_hook(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(proc.stderr, "")   # debug output only under the env flag

    def test_other_tools_are_ignored(self):
        self.assert_silent({"tool_name": "Bash", "tool_input": {"command": "ls"}})

    def test_access_layer_skills_are_skipped(self):
        for s in ("shared:retrieve-knowledge", "shared:record-knowledge", "shared:search-knowledge"):
            with self.subTest(skill=s):
                self.assert_silent({"tool_name": "Skill", "tool_input": {"skill": s}})

    def test_unknown_skill_is_silent(self):
        self.assert_silent({"tool_name": "Skill", "tool_input": {"skill": "nope:nothing"}})

    def test_project_local_skill_without_plugin_prefix_is_silent(self):
        self.assert_silent({"tool_name": "Skill", "tool_input": {"skill": "my-local-skill"}})

    def test_malformed_stdin_is_silent(self):
        self.assert_silent("this is not json")
        self.assert_silent("")

    def test_debug_flag_explains_on_stderr_but_still_exits_zero(self):
        proc = run_hook({"tool_name": "Skill", "tool_input": {"skill": "nope:nothing"}}, {"AINATIVE_KNOWLEDGE_HOOK_DEBUG": "1"})
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")
        self.assertIn("knowledge_context", proc.stderr)


if __name__ == "__main__":
    unittest.main()


class SubagentStart(unittest.TestCase):
    AGENT = "---\nname: {name}\ndescription: d\nknowledge:\n  reads:\n    - Guide/Index\n    - Tools/Mixpanel/Events\n  writes:\n    - Signals/Production Health\n---\nbody\n"

    def start(self, agent_type, **env):
        return run_hook({"session_id": "s", "hook_event_name": "SubagentStart",
                         "agent_id": "a1", "agent_type": agent_type}, env_extra=env)

    def test_project_agent_gets_its_manifest_by_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = pathlib.Path(tmp) / ".claude/agents/mp.md"      # filename differs from name
            agent.parent.mkdir(parents=True)
            agent.write_text(self.AGENT.format(name="mixpanel-reviewer"), encoding="utf-8")
            proc = self.start("mixpanel-reviewer", CLAUDE_PROJECT_DIR=tmp)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            out = json.loads(proc.stdout)["hookSpecificOutput"]
            self.assertEqual(out["hookEventName"], "SubagentStart")
            ctx = out["additionalContext"]
            self.assertIn("agent `mixpanel-reviewer`", ctx)
            self.assertIn("`Tools/Mixpanel/Events`", ctx)
            self.assertIn(f"agent_file={agent}", ctx)
            self.assertIn(f"caller={agent}", ctx)

    def test_plugin_agent_through_claude_plugin_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "cache/aura-tools/eng/9.9.9"
            (root / ".claude-plugin").mkdir(parents=True)
            (root / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "eng"}))
            (root / "agents").mkdir()
            (root / "agents/rev.md").write_text(self.AGENT.format(name="rev"), encoding="utf-8")
            ctx = context_of(self.start("eng:rev", CLAUDE_PLUGIN_ROOT=str(root)))
            self.assertIn("agent `eng:rev`", ctx)
            self.assertIn(f"caller={root / 'agents/rev.md'}", ctx)

    def test_silent_for_builtins_undeclared_agents_and_junk(self):
        cases = [self.start("Explore"), self.start("general-purpose"),
                 self.start("eng:code-investigator"),           # real agent, no knowledge: block
                 run_hook({"hook_event_name": "SubagentStart"}),  # no agent_type
                 run_hook("[]")]
        for proc in cases:
            with self.subTest(stdout=proc.stdout):
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stdout, "")
