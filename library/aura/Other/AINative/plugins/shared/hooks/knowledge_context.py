#!/usr/bin/env python3
"""Hand the model a skill's or a subagent's ``knowledge:`` manifest when it starts.

Two events, one script: ``PostToolUse`` on the ``Skill`` tool (a skill loaded) and
``SubagentStart`` (an agent spawned).

Why this exists. The Skill tool injects only a SKILL.md *body*; the YAML frontmatter —
and with it ``knowledge: reads / writes`` — never reaches the running context. Only 21 of
204 skills carry a body instruction to pre-load their declared reads, so for the rest the
declaration is inert at run time ([ENG-1238](https://linear.app/aura-health/issue/ENG-1238)).
This hook closes that gap for every skill at once, with no per-skill edit: whenever a skill
loads, it reads the manifest from the SKILL.md the host actually loaded and returns it as
``additionalContext``, with the two instructions the declaration implies.

Agents have the same gap: a subagent's system prompt is its agent file's *body*, so an
agent that declares ``knowledge:`` in its frontmatter never sees the declaration either.
On ``SubagentStart`` the hook resolves the agent file from ``agent_type`` (``<plugin>:<agent>``
for a plugin agent, the bare frontmatter ``name`` for a project or user agent under
``.claude/agents``) and returns its manifest as ``additionalContext``, which the host adds
to the subagent's context before its first prompt. An agent with no ``knowledge:`` block —
every built-in, and most agents today — gets nothing: unlike skills, agents are not all
required to declare, so "none declared" would be noise on every spawn.

Contract:

- Reads the hook JSON on stdin; acts on ``PostToolUse`` when ``tool_name == "Skill"``, and
  on ``SubagentStart`` (identified by ``hook_event_name`` or by ``agent_type`` without a
  ``tool_name``).
- Locates the SKILL.md **from the tool result** first — the host prints
  ``Base directory for this skill: <path>`` as the first line, which is authoritative for
  the copy that was loaded — and falls back to resolving ``tool_input.skill``
  (``<plugin>:<skill>``) through ``manifest.py``'s ladder.
- Emits ``{"hookSpecificOutput": {"hookEventName": <the event>, "additionalContext": …}}``
  and exits 0. **Every failure path is silent and exits 0**: a hook that cannot read a
  manifest must never make a skill unusable, and PostToolUse cannot block anyway. Set
  ``AINATIVE_KNOWLEDGE_HOOK_DEBUG=1`` to get the reason on stderr.
- Skips the three access-layer knowledge skills (``retrieve-knowledge``, ``record-knowledge``,
  ``search-knowledge``) — telling ``retrieve-knowledge`` to call itself is noise.

``manifest.py`` is imported from ``../skills/retrieve-knowledge/scripts`` relative to this
file. A hook is plugin-level infrastructure like an agent (CLAUDE.md, Architecture
principle 4), not a skill, so skill self-containment does not apply to it; keeping one
canonical parser is worth the relative import.

Stdlib only.
"""

from __future__ import annotations

import json
import os
import re
import sys

ACCESS_LAYER = {"retrieve-knowledge", "record-knowledge", "search-knowledge"}
_BASE_DIR = re.compile(r"Base directory for this skill:\s*(\S.*?)\s*(?:\n|$)")
DEBUG = os.environ.get("AINATIVE_KNOWLEDGE_HOOK_DEBUG") == "1"


def _debug(msg: str) -> None:
    if DEBUG:
        print(f"knowledge_context: {msg}", file=sys.stderr)


def _load_resolver():
    here = os.path.dirname(os.path.abspath(__file__))
    scripts = os.path.join(os.path.dirname(here), "skills", "retrieve-knowledge", "scripts")
    sys.path.insert(0, scripts)
    import manifest  # noqa: E402  (bundled with the plugin)
    return manifest


def _string_leaves(node):
    """Yield every string inside a nested dict/list/str tool_response, untouched."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _string_leaves(v)
    elif isinstance(node, (list, tuple)):
        for v in node:
            yield from _string_leaves(v)


def _base_dir_from_response(tool_response) -> str:
    """Find the ``Base directory for this skill:`` line wherever the host put it.

    Searches each string leaf as-is. It deliberately does **not** ``json.dumps`` the
    structure and un-escape ``\\n`` — that turns a Windows path component starting with
    ``n`` (``C:\\new\\skills``) into a line break and truncates the directory.
    """
    for text in _string_leaves(tool_response):
        m = _BASE_DIR.search(text)
        if m:
            return m.group(1).strip().strip('"')
    return ""


def build_context(man: dict) -> str:
    reads = man.get("reads") or []
    writes = man.get("writes")
    if man.get("kind") == "agent":
        # An agent is addressed by its file: unambiguous next to skill ids, and it is the
        # copy the host actually spawned. record-knowledge's resolver takes a path as-is.
        skill = man["skill"]
        ref = f"agent_file={man['path']}"
        caller = man["path"]
        lines = [f"Knowledge manifest for agent `{skill}`, read from its agent file's frontmatter — "
                 "your system prompt does not include it:"]
    elif man.get("skill_dir"):
        # No trustworthy <plugin>:<skill> id — address the skill by its directory instead.
        skill = man["skill"].split(":", 1)[-1]
        ref = f"skill_dir={man['skill_dir']}"
        caller = man["skill_dir"]
    else:
        skill = man["skill"]
        ref = f"skill={skill}"
        caller = skill
    if man.get("kind") != "agent":
        lines = [f"Knowledge manifest for `{skill}` (v{man.get('version') or '?'}), read from its SKILL.md "
                 "frontmatter — the Skill tool does not show it to you:"]
    subject = "agent's" if man.get("kind") == "agent" else "skill's"
    if man.get("reads_wildcard"):
        lines.append("- reads: `\"*\"` (cross-cutting) — no pre-fetch; fetch specific paths on demand via `shared:retrieve-knowledge`.")
    elif reads:
        lines.append("- reads: " + ", ".join(f"`{r}`" for r in reads))
        lines.append(f"  → Before the {subject} main logic, pre-load these via `shared:retrieve-knowledge` "
                     f"(Recipe F, `{ref}`) unless the body already does so.")
    if writes is None:
        lines.append(f"- writes: none declared → `shared:record-knowledge` will refuse any write from this {subject[:-2]}; "
                     "pass `allow_undeclared: true` only for a deliberate one-off.")
    elif man.get("writes_wildcard"):
        lines.append(f"- writes: `\"*\"` → any `Learnings/*` or `Signals/*` path; pass `caller={caller}` to `shared:record-knowledge`.")
    else:
        lines.append("- writes: " + ", ".join(f"`{w}`" for w in writes)
                     + f" → pass `caller={caller}` to `shared:record-knowledge`; any other path is refused.")
    return "\n".join(lines)


def _emit(event: str, man: dict) -> None:
    out = {"hookSpecificOutput": {"hookEventName": event, "additionalContext": build_context(man)}}
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(out))


def subagent_start(payload: dict) -> int:
    agent_type = str(payload.get("agent_type") or "")
    if not agent_type:
        _debug("SubagentStart without agent_type")
        return 0
    try:
        manifest = _load_resolver()
        plugin, _, agent = agent_type.rpartition(":")
        path = manifest.agent_md_path(plugin, agent,
                                      here=os.path.dirname(os.path.abspath(manifest.__file__)))
        if not path:
            _debug(f"no agent file for {agent_type!r}")   # built-ins land here
            return 0
        man = manifest.load_manifest(path)
        if man["reads"] is None and man["writes"] is None:
            _debug(f"{agent_type!r} declares no knowledge: block")
            return 0
        if plugin:
            man["skill"] = agent_type          # the host's own id is authoritative
        _emit("SubagentStart", man)
    except Exception as exc:  # noqa: BLE001
        _debug(f"failed: {exc!r}")
    return 0


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:  # noqa: BLE001
        _debug(f"stdin not JSON: {exc}")
        return 0
    if not isinstance(payload, dict):
        return 0
    if payload.get("hook_event_name") == "SubagentStart" or (
            "agent_type" in payload and "tool_name" not in payload):
        return subagent_start(payload)
    if payload.get("tool_name") != "Skill":
        return 0
    skill_arg = str((payload.get("tool_input") or {}).get("skill") or "")
    if skill_arg.split(":")[-1] in ACCESS_LAYER:
        _debug("access-layer skill, skipping")
        return 0
    try:
        manifest = _load_resolver()
        path = ""
        base = _base_dir_from_response(payload.get("tool_response"))
        if base and os.path.isfile(os.path.join(base, "SKILL.md")):
            path = os.path.join(base, "SKILL.md")
        elif ":" in skill_arg:
            plugin, skill = skill_arg.split(":", 1)
            path = manifest.skill_md_path(plugin, skill,
                                          here=os.path.join(os.path.dirname(os.path.abspath(manifest.__file__))))
        if not path:
            _debug(f"no SKILL.md for {skill_arg!r} (base={base!r})")
            return 0
        man = manifest.load_manifest(path)
        if ":" in skill_arg:
            man["skill"] = skill_arg          # the host's own id is authoritative
        elif man.get("plugin_source") != "manifest":
            # No plugin id from the host and none from plugin.json: the directory-derived
            # half is a version folder on installed copies, so don't emit an id at all.
            man["skill_dir"] = os.path.dirname(path)
        _emit("PostToolUse", man)
    except Exception as exc:  # noqa: BLE001
        _debug(f"failed: {exc!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
