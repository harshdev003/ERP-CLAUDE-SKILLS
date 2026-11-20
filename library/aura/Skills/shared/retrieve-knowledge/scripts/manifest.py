#!/usr/bin/env python3
"""Read a skill's or an agent's ``knowledge:`` manifest from its file — at run time, from disk.

The Skill tool hands the model only the SKILL.md *body*, and a subagent gets only its agent
file's body as its system prompt; in both cases the YAML frontmatter, and with it the
``knowledge: reads / writes`` block, never reaches the running context. So a skill or agent
that wants its declared reads pre-fetched, or an access-layer skill that wants to enforce a
caller's declared writes, has to read the manifest back from the file. This script is how.

Usage::

    manifest.py <skill-dir | SKILL.md | agent.md>      # a manifest by path
    manifest.py --skill <plugin>:<skill>               # another skill's, resolved like a footer version
    manifest.py --agent <plugin>:<agent>               # a plugin agent's (``plugins/<plugin>/agents/<agent>.md``)
    manifest.py --agent <agent>                        # a project or user agent (``.claude/agents/<agent>.md``)
    manifest.py --skill data:analyze-experiment --check-write "Learnings/Experiments"

Output is one JSON object on stdout::

    {"skill": "data:analyze-experiment", "kind": "skill", "plugin_source": "manifest",
     "version": "3.2.1", "path": ".../SKILL.md", "reads": ["Guide/Index", ...],
     "writes": ["Learnings/Experiments"] | null, "reads_wildcard": false, "writes_wildcard": false}

``skill`` is the id to address the manifest by — ``<plugin>:<name>`` for a plugin skill or
agent, the bare name for a project or user agent — and keeps its key for an agent so
existing callers read one field. ``kind`` is ``"skill"`` or ``"agent"``.

``plugin_source`` is ``"manifest"`` when the plugin half of ``skill`` came from the plugin's
own ``plugin.json`` (every real layout), ``"local"`` for an agent under a ``.claude/agents``
directory (no plugin, so the id is the bare name), and ``"dirname"`` when it fell back to
the directory name — only a checkout has a directory named after the plugin, so a
``dirname`` id from an installed copy is a version folder and must not be trusted.

With ``--check-write <path>`` the object gains ``"allowed": true|false`` and ``"reason"``,
and the exit code says the same thing: 0 allowed, 1 refused. Exit 2 means the manifest
could not be resolved or parsed at all — a *different* failure from "not declared", and
callers must not collapse the two: an unresolvable caller is a tooling fault to surface,
not a write to allow.

Write authorisation encodes the CLAUDE.md → Knowledge manifest rules and nothing more:
a path is allowed iff it appears in ``writes`` verbatim, or ``writes`` contains ``"*"``
AND the path is inside the normal write boundary (``Learnings/`` or ``Signals/``). A
missing ``writes:`` key refuses everything. Neither ``reads`` nor the body is consulted.

Resolution of ``<plugin>:<skill>`` and ``<plugin>:<agent>`` mirrors ``slack_post.py``'s
footer-version ladder: candidate roots come only from this script's own path and
``CLAUDE_PLUGIN_ROOT`` — never cwd, never a hardcoded checkout — so a run executing from an
install cache reads the copy the host actually loaded. Three layouts are covered: in-repo
``plugins/<plugin>/{skills,agents}``, the install cache
``<marketplace>/<plugin>/<version>/{skills,agents}``, and local-agent-mode
``rpm/plugin_<opaque-id>`` roots mapped through the sibling ``manifest.json``. A bare agent
name (no plugin) is a project or user agent: ``$CLAUDE_PROJECT_DIR/.claude/agents`` first,
then ``~/.claude/agents`` — the host's own project root, still never cwd.

Canonical copy: ``plugins/shared/skills/retrieve-knowledge/scripts/manifest.py``.
Byte-identical copy in ``record-knowledge/scripts/`` (skills are self-contained).
Stdlib only.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from typing import Dict, List, Optional

WRITE_BOUNDARY = ("Learnings/", "Signals/")

# Indentation is measured, not assumed: the install cache re-serialises frontmatter with
# indentless sequences (``- item`` at the same column as its key), which is valid YAML.
_ITEM = re.compile(r"^( *)- (.*?)(?:\s+#.*)?\s*$")
_KEY = re.compile(r"^( +)(reads|writes):\s*(\[.*?\])?(?:\s+#.*)?\s*$")
_NESTED_KEY = re.compile(r"^( +)[^\s#-][^:]*:(?:\s|$)")


class ManifestError(Exception):
    """The SKILL.md could not be found or has no parseable frontmatter."""


# --------------------------------------------------------------------------- parsing


def split_frontmatter(text: str) -> str:
    """Return the YAML slice between the first two ``---`` fences.

    Operates on the slice only — several skills document the frontmatter convention by
    example in their body, so a whole-file scan reads the wrong block.
    """
    parts = text.split("---", 2)
    if len(parts) < 3 or parts[0].strip():
        raise ManifestError("no YAML frontmatter fence at the top of the file")
    return parts[1]


def _unquote(item: str) -> str:
    item = item.strip()
    if len(item) >= 2 and item[0] == item[-1] and item[0] in "\"'":
        return item[1:-1]
    return item


def parse_knowledge(frontmatter: str) -> Dict[str, Optional[List[str]]]:
    """Parse the ``knowledge:`` block. Lists absent from the file come back as ``None``.

    Handles the shapes the repo and the install cache actually use: block lists indented
    under their key or indentless (item at the key's column), trailing ``# comments``,
    quoted items, and inline flow lists (``writes: ["*"]``, ``writes: []``).
    """
    out: Dict[str, Optional[List[str]]] = {"reads": None, "writes": None}
    lines = frontmatter.split("\n")
    try:
        start = next(i for i, l in enumerate(lines) if re.match(r"^knowledge:\s*(?:#.*)?$", l))
    except StopIteration:
        return out
    key: Optional[str] = None
    key_indent = 0
    for line in lines[start + 1:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            break  # next top-level key
        m = _KEY.match(line)
        if m:
            key, key_indent = m.group(2), len(m.group(1))
            if m.group(3) is not None:
                inner = m.group(3).strip()[1:-1]
                out[key] = [_unquote(x) for x in inner.split(",") if x.strip()]
                key = None
            else:
                out[key] = []
            continue
        if key is None:
            continue
        im = _ITEM.match(line)
        if im and len(im.group(1)) >= key_indent:
            out[key].append(_unquote(im.group(2)))
        elif _NESTED_KEY.match(line) or (im and len(im.group(1)) < key_indent):
            key = None  # some other nested key under knowledge:
    return out


def parse_version(frontmatter: str) -> str:
    m = re.search(r'^metadata:\s*\n(?:[ \t]+.*\n)*?[ \t]+version:\s*"([^"]+)"', frontmatter, re.M)
    return m.group(1) if m else ""


def parse_name(frontmatter: str) -> str:
    m = re.search(r"^name:\s*(\S+)", frontmatter, re.M)
    return m.group(1) if m else ""


# --------------------------------------------------------------------------- resolution


def _manifest_plugin_paths(siblings: str, plugin: str) -> List[str]:
    """Opaque-id plugin roots under ``siblings`` whose ``manifest.json`` entry names ``plugin``."""
    try:
        with open(os.path.join(siblings, "manifest.json"), encoding="utf-8") as fh:
            entries = json.load(fh).get("plugins", [])
    except (OSError, ValueError, AttributeError):
        return []
    return [os.path.join(siblings, e["id"])
            for e in entries
            if isinstance(e, dict) and e.get("name") == plugin and e.get("id")]


def _ladder_candidates(plugin: str, rel: str, here: Optional[str] = None,
                       plugin_root_env: Optional[str] = None) -> List[str]:
    """Every place ``<plugin-root>/<rel>`` may live, most authoritative first.

    ``here`` is this script's directory (``<plugin-root>/skills/<skill>/scripts``);
    ``plugin_root_env`` is ``CLAUDE_PLUGIN_ROOT``. Both are parameters so tests can point
    the ladder at synthetic layouts; the CLI passes the real ones.
    """
    here = here or os.path.dirname(os.path.abspath(__file__))
    roots = [os.path.dirname(os.path.dirname(os.path.dirname(here)))]
    cpr = os.environ.get("CLAUDE_PLUGIN_ROOT") if plugin_root_env is None else plugin_root_env
    if cpr:
        roots.append(os.path.abspath(cpr))
    # In ``<plugin>/<version>/<rel>`` the version segment sits just above ``rel``.
    version_seg = -(len(rel.split(os.sep)) + 1)

    seen: List[str] = []
    for root in roots:
        siblings = os.path.dirname(root)
        versioned = glob.glob(os.path.join(os.path.dirname(siblings), plugin, "*", rel))
        versioned.sort(key=lambda p: p.split(os.sep)[version_seg] != os.path.basename(root))
        for path in (os.path.join(root, rel),
                     os.path.join(siblings, plugin, rel),
                     *versioned,
                     *(os.path.join(p, rel) for p in _manifest_plugin_paths(siblings, plugin))):
            if path not in seen:
                seen.append(path)
    return seen


def skill_md_path(plugin: str, skill: str, here: Optional[str] = None,
                  plugin_root_env: Optional[str] = None) -> str:
    """Locate ``<plugin>:<skill>``'s SKILL.md as the running host loaded it, or ``""``."""
    rel = os.path.join("skills", skill, "SKILL.md")
    return next((p for p in _ladder_candidates(plugin, rel, here, plugin_root_env)
                 if os.path.isfile(p)), "")


def _agent_in(agents_dir: str, agent: str) -> str:
    """``<agent>.md`` in ``agents_dir``, else the file whose frontmatter ``name`` is ``agent``.

    The host names an agent by its frontmatter ``name``, not its filename; the two agree by
    convention only, so the filename is the fast path and the scan is the rule.
    """
    direct = os.path.join(agents_dir, f"{agent}.md")
    if os.path.isfile(direct):
        return direct
    try:
        entries = sorted(os.listdir(agents_dir))
    except OSError:
        return ""
    for entry in entries:
        path = os.path.join(agents_dir, entry)
        if not entry.endswith(".md") or not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                if parse_name(split_frontmatter(fh.read())) == agent:
                    return path
        except (OSError, ManifestError):
            continue
    return ""


def agent_md_path(plugin: str, agent: str, here: Optional[str] = None,
                  plugin_root_env: Optional[str] = None, project_dir_env: Optional[str] = None,
                  home: Optional[str] = None) -> str:
    """Locate an agent file, or ``""``.

    With a ``plugin``, ``<plugin-root>/agents/`` through the same ladder as a skill. Without
    one, a project or user agent: ``$CLAUDE_PROJECT_DIR/.claude/agents``, then
    ``~/.claude/agents`` — the precedence Claude Code itself gives the two. A
    ``CLAUDE_PROJECT_DIR`` the host did not set is skipped, never replaced by cwd.
    """
    if plugin:
        dirs = _ladder_candidates(plugin, "agents", here, plugin_root_env)
    else:
        project = os.environ.get("CLAUDE_PROJECT_DIR") if project_dir_env is None else project_dir_env
        home = os.path.expanduser("~") if home is None else home
        dirs = [os.path.join(base, ".claude", "agents") for base in (project, home) if base]
    for d in dirs:
        found = _agent_in(d, agent) if os.path.isdir(d) else ""
        if found:
            return found
    return ""


def resolve_target(target: Optional[str], skill_id: Optional[str],
                   agent_id: Optional[str] = None) -> str:
    if sum(bool(x) for x in (target, skill_id, agent_id)) > 1:
        raise ManifestError("pass one of: a path, --skill or --agent")
    if agent_id:
        plugin, _, agent = agent_id.rpartition(":")
        path = agent_md_path(plugin, agent)
        if not path:
            where = ("this script's install root or CLAUDE_PLUGIN_ROOT" if plugin
                     else "$CLAUDE_PROJECT_DIR/.claude/agents or ~/.claude/agents")
            raise ManifestError(f"could not locate the agent file for {agent_id} from {where}")
        return path
    if skill_id:
        if ":" not in skill_id:
            raise ManifestError(f"--skill wants <plugin>:<skill>, got {skill_id!r}")
        plugin, skill = skill_id.split(":", 1)
        path = skill_md_path(plugin, skill)
        if not path:
            raise ManifestError(f"could not locate SKILL.md for {skill_id} from this script's "
                                "install root or CLAUDE_PLUGIN_ROOT")
        return path
    if not target:
        raise ManifestError("pass a skill dir / SKILL.md / agent .md path, --skill or --agent")
    path = target if target.endswith(".md") else os.path.join(target, "SKILL.md")
    if not os.path.isfile(path):
        raise ManifestError(f"no {os.path.basename(path)} at {path}")
    return path


# --------------------------------------------------------------------------- manifest


def kind_of(path: str) -> str:
    """``"skill"`` for a SKILL.md, ``"agent"`` for any other ``.md`` (an agent file)."""
    return "skill" if os.path.basename(path) == "SKILL.md" else "agent"


def plugin_name_for(path: str) -> (str, str):
    """Return ``(plugin_name, source)`` for the plugin that owns ``path``.

    The plugin root is two levels above a skill directory (``<root>/skills/<s>/SKILL.md``)
    and one above an agents directory (``<root>/agents/<a>.md``) in every layout, but its
    *basename* is only the plugin name in a git checkout — on an installed copy it is the
    version folder (``…/shared/3.27.0/``), in local-agent mode an opaque id. The manifest
    at ``<root>/.claude-plugin/plugin.json`` (or the codex / cursor twin) carries the real
    name in all three, so read that first; ``source`` says which answer you got, so a
    caller can decline to build a ``<plugin>:<name>`` id from a directory guess.

    An agent under ``.claude/agents`` (a project or user agent) has no plugin: the answer
    is ``("", "local")`` and its id is the bare name.
    """
    up = 3 if kind_of(path) == "skill" else 2
    root = os.path.abspath(path)
    for _ in range(up):
        root = os.path.dirname(root)
    for flavour in (".claude-plugin", ".codex-plugin", ".cursor-plugin"):
        try:
            with open(os.path.join(root, flavour, "plugin.json"), encoding="utf-8") as fh:
                name = json.load(fh).get("name")
            if isinstance(name, str) and name:
                return name, "manifest"
        except (OSError, ValueError, AttributeError):
            continue
    if kind_of(path) == "agent" and os.path.basename(root) == ".claude":
        return "", "local"
    return os.path.basename(root), "dirname"


def load_manifest(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        fm = split_frontmatter(fh.read())
    kb = parse_knowledge(fm)
    reads, writes = kb["reads"], kb["writes"]
    kind = kind_of(path)
    plugin, plugin_source = plugin_name_for(path)
    name = parse_name(fm) or (os.path.basename(os.path.dirname(path)) if kind == "skill"
                              else os.path.splitext(os.path.basename(path))[0])
    return {
        "skill": f"{plugin}:{name}" if plugin else name,
        "kind": kind,
        "plugin_source": plugin_source,
        "version": parse_version(fm),
        "path": os.path.abspath(path),
        "reads": reads,
        "writes": writes,
        "reads_wildcard": bool(reads and "*" in reads),
        "writes_wildcard": bool(writes and "*" in writes),
    }


def check_write(manifest: dict, path: str) -> (bool, str):
    """Apply the CLAUDE.md write rules to one target path."""
    writes = manifest.get("writes")
    if writes is None:
        return False, "caller declares no knowledge.writes — pass allow_undeclared: true for a deliberate one-off"
    if path in writes:
        return True, "declared"
    if "*" in writes:
        if path.startswith(WRITE_BOUNDARY):
            return True, "wildcard within the Learnings/ + Signals/ write boundary"
        return False, f"wildcard does not extend outside {'/'.join(b.rstrip('/') for b in WRITE_BOUNDARY)}"
    return False, f"not in caller's knowledge.writes {writes}"


# --------------------------------------------------------------------------- cli


def main(argv: Optional[List[str]] = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("target", nargs="?", help="skill directory, SKILL.md or agent .md path")
    ap.add_argument("--skill", help="<plugin>:<skill> to resolve instead of a path")
    ap.add_argument("--agent", help="<plugin>:<agent>, or a bare <agent> for a project or user agent")
    ap.add_argument("--check-write", metavar="PATH", help="exit 0 if PATH is an allowed write for this skill or agent, 1 if not")
    args = ap.parse_args(argv)
    try:
        manifest = load_manifest(resolve_target(args.target, args.skill, args.agent))
    except ManifestError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    rc = 0
    if args.check_write is not None:
        allowed, reason = check_write(manifest, args.check_write)
        manifest.update({"check_write": args.check_write, "allowed": allowed, "reason": reason})
        rc = 0 if allowed else 1
    print(json.dumps(manifest, indent=1))
    return rc


if __name__ == "__main__":
    sys.exit(main())
