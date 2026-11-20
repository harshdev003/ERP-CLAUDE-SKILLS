#!/usr/bin/env python3
"""Generate AGENTS.md from CLAUDE.md, and check the committed copy still matches.

AGENTS.md is the same project memory, read by Codex instead of Claude Code. It is
NOT a hand-maintained file: edit CLAUDE.md, run this, commit both.

**Why generated rather than hand-synced.** The two files drifted badly while both
were hand-maintained — 23 of 35 shared sections diverged, 11 sections went missing
from AGENTS.md entirely, and it carried no unique content of its own. A second
hand-maintained copy of the project memory is a parallel source of truth, which
Architecture principle 3 says produces drift; it did.

**Why an explicit rule list rather than s/Claude Code/Codex/.** A blanket rewrite is
how AGENTS.md ended up on main telling Codex to read `.Codex-plugin/marketplace.json`
and `~/.Codex/settings.json` — paths that do not exist — and to make worktrees under
`.Codex/worktrees/`. Twelve literals were corrupted that way. The same failure is
already written up in CLAUDE.md under "A blanket rewrite corrupts the one sentence
that contrasts the two spellings"; this script is that lesson in executable form.

**The line the rules draw.** Substitute where the text means *the host you are
running in* ("Claude Code loads `env` at startup", "Claude Code's plugin loader").
Preserve where it names Claude Code as *one host among several* — the
`.claude-plugin/` catalog row, and every sentence contrasting the hosts, e.g. "Hooks
are Claude Code + Cursor only — Codex ignores `hooks/`", which is correct as written
for a Codex reader and becomes false if rewritten. Paths, env vars and config keys
are literals and are never substituted, whatever their spelling.

**Every rule must match exactly its declared count**, or the build fails naming the
rule. That is what makes a CLAUDE.md reword loud: without it, a rule that no longer
matches would silently emit an un-retargeted AGENTS.md, which is the quiet failure
this whole script exists to prevent.

    python3 scripts/build-agents-md.py --write    # regenerate AGENTS.md
    python3 scripts/build-agents-md.py --check    # CI: fail if it is stale

Exit: 0 clean · 1 stale (--check) or a rule/guard failed · 2 the script could not run.
"""
from __future__ import annotations

import argparse
import difflib
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
SOURCE = REPO / "CLAUDE.md"
TARGET = REPO / "AGENTS.md"

BANNER = """<!-- Generated from CLAUDE.md by scripts/build-agents-md.py — do not edit by hand.
     Edit CLAUDE.md, run `python3 scripts/build-agents-md.py --write`, commit both. -->
"""

# (expected_count, old, new). Applied in order; each must match exactly.
RULES: list[tuple[int, str, str]] = [
    # -- "this file" self-references ------------------------------------------
    (1, "├── CLAUDE.md                       # This file — project memory",
        "├── AGENTS.md                       # This file — project memory"),
    (1, "document the canonical location in CLAUDE.md",
        "document the canonical location in AGENTS.md"),
    (1, "Applies to prose in SKILL.md, CLAUDE.md, AI Knowledge pages",
        "Applies to prose in SKILL.md, AGENTS.md, AI Knowledge pages"),
    (1, "carries the scaffold it emits for a new skill, `shared:update-aura-skill` carries the seed example in its version-presence check, and this section of CLAUDE.md carries one too.",
        "carries the scaffold it emits for a new skill, `shared:update-aura-skill` carries the seed example in its version-presence check, and this section of AGENTS.md carries one too."),

    # -- the host you are running in ------------------------------------------
    (1, "whose location is fixed by Claude Code's loader",
        "whose location is fixed by the plugin loader"),
    (1, "Claude Code's plugin loader discovers agents **only**",
        "The plugin loader discovers agents **only**"),
    (1, "**This path is fixed by Claude Code's plugin loader, not a stylistic choice.**",
        "**This path is fixed by the plugin loader, not a stylistic choice.**"),
    (1, "the available-agents list at the start of a Claude Code session",
        "the available-agents list at the start of a session"),
    (1, "those are blocked by Claude Code's plugin security model",
        "those are blocked by the plugin security model"),
    (1, "revisit if Claude Code adds a manifest field for nested agent paths",
        "revisit if the host adds a manifest field for nested agent paths"),
    (1, "Claude Code keys the install-cache path on the manifest version",
        "The host keys the install-cache path on the manifest version"),
    (1, "A bare top-level `version:` is silently ignored by the Claude Code loader",
        "A bare top-level `version:` is silently ignored by the plugin loader"),
    (1, "Claude Code loads `env` at startup, so changes only take effect after a restart.",
        "Codex loads `env` at startup, so changes only take effect after a restart."),
    (1, "then prompt to restart Claude Code.",
        "then prompt to restart Codex."),
    (1, "the MCP connectors that host Claude Code does",
        "the MCP connectors the host does"),
    (1, "under the `env` block as `NOTION_TOKEN`. Restart Claude Code.",
        "under the `env` block as `NOTION_TOKEN`. Restart Codex."),
    (1, "Claude Code then falls back to the commit sha",
        "the host then falls back to the commit sha"),
]

# Literals that must survive the rewrite untouched: real paths, env vars and config
# keys. Their occurrence count in the output must equal the count in the source.
# This is the guard the blanket rewrite failed.
PROTECTED = [
    ".claude-plugin/", ".codex-plugin/", ".cursor-plugin/",
    "~/.claude/settings.json", "~/.claude/plugins/cache/",
    "CLAUDE_PLUGIN_ROOT", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_TMPDIR",
    ".claude/worktrees/", ".claude/agents/", "~/.cache/ai-knowledge",
]

# Spellings the blanket rewrite produced. None may ever appear in the output.
CORRUPTIONS = [".Codex-plugin", "~/.Codex/", ".Codex/worktrees", "CODEX_PLUGIN_ROOT",
               "Codex_PLUGIN_ROOT", ".Codex/agents", "Codex Code"]

# Prose *about* the rewrite quotes the corrupted spellings and the substituted phrases
# as examples, so a substring guard cannot tell it from a real corruption. Exempt those
# lines explicitly rather than loosening the guard — the same "exclude the prose that
# documents the rename from the sweep" rule CLAUDE.md states for a path migration.
PROSE_ABOUT_THE_REWRITE = [
    "**Why an explicit rule list and not",
    "**The line the rules draw:**",
]

# After substitution, a remaining "Claude Code" must sit in one of these contexts —
# each names Claude Code as one host among several, where rewriting makes it false.
ALLOWED_CLAUDE_CODE = [
    "for Codex, Claude Code, and Cursor",          # the three-host framing
    "(Codex and Claude Code)",                     # install line
    "# Claude Code catalog",                       # repo-layout tree
    "Claude Code + Cursor only",                   # hooks: host contrast
    "the open standard Claude Code follows",       # Agent Skills spec attribution
    "`.claude/agents/`",                           # project-local agent path
    "Hooks are Claude Code",
]


def die(msg: str, code: int = 2):
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(code)


def guard_problems(src: str, out: str) -> list[str]:
    """Everything that must hold of the rendered output. Exposed so the tests assert
    this implementation rather than a second copy of the rules — a duplicated guard
    is the drift the repo already has a rule about."""
    problems = []
    for lit in PROTECTED:
        a, b = src.count(lit), out.count(lit)
        if a != b:
            problems.append(f"protected literal {lit!r}: {a} in CLAUDE.md, {b} in output")
    for ln, line in enumerate(out.split("\n"), 1):
        if any(d in line for d in PROSE_ABOUT_THE_REWRITE):
            continue  # prose quoting the corruption, not the corruption
        for bad in CORRUPTIONS:
            if bad in line:
                problems.append(f"line {ln}: corrupted spelling {bad!r} in the output")
        if "Claude Code" in line and not any(a in line for a in ALLOWED_CLAUDE_CODE):
            problems.append(f"line {ln}: un-retargeted \"Claude Code\" — add a rule, or "
                            f"allowlist it if it names Claude Code as one host among "
                            f"several:\n      {line.strip()[:130]}")
    return problems


def render() -> str:
    try:
        src = SOURCE.read_text(encoding="utf-8")
    except OSError as exc:
        die(f"{SOURCE.name} could not be read ({exc})")

    out, failures = src, []
    for i, (count, old, new) in enumerate(RULES, 1):
        n = out.count(old)
        if n != count:
            failures.append(f"rule {i}: expected {count} match(es), found {n}\n"
                            f"      looking for: {old[:110]!r}")
            continue
        out = out.replace(old, new)
    if failures:
        print("FAIL — the rule list no longer fits CLAUDE.md:\n", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        print("\n  A rule stops matching when CLAUDE.md is reworded. Update the rule to the\n"
              "  new wording (or drop it if the sentence is gone) — do NOT loosen it into a\n"
              "  regex. The exact-count assertion is what keeps a silent miss impossible.\n",
              file=sys.stderr)
        raise SystemExit(1)

    problems = guard_problems(src, out)
    if problems:
        print("FAIL — guard:\n", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        raise SystemExit(1)

    return BANNER + "\n" + out


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--write", action="store_true", help="regenerate AGENTS.md")
    g.add_argument("--check", action="store_true", help="fail if AGENTS.md is stale")
    args = ap.parse_args()

    want = render()
    if args.write:
        TARGET.write_text(want, encoding="utf-8")
        print(f"Wrote {TARGET.name} — {len(want):,} chars, {len(RULES)} rules applied.")
        return 0

    have = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
    if have == want:
        print(f"{TARGET.name} is up to date with {SOURCE.name} ({len(want):,} chars).")
        return 0
    print(f"FAIL — {TARGET.name} is stale. Run:\n\n"
          f"    python3 scripts/build-agents-md.py --write\n\n"
          f"and commit it alongside {SOURCE.name}. Diff (committed -> generated):\n",
          file=sys.stderr)
    for line in list(difflib.unified_diff(have.split("\n"), want.split("\n"),
                                          "AGENTS.md", "generated", lineterm=""))[:40]:
        print(f"  {line[:160]}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
