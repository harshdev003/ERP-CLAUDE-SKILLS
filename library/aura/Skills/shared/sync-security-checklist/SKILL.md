---
name: sync-security-checklist
description: >
  Carries the security review checklist (AI Knowledge Compliance/Security/Code Review Checklist)
  and the services logging rule (Procedures/Engineering/Logging Standards) from their canonical
  Notion pages to every Aura repo's copies (.claude/rules, .cursor/rules, .cursor/BUGBOT.md):
  renders both pages to snapshots, applies only the changed lines to each repo's files, opens one
  docs-only draft PR per repo with a BugBot trigger, reports layer drift, and merges the clean PRs
  in order. Trigger on "sync the security checklist", "roll out the checklist change", "propagate
  the checklist to every repo", "update the logging rule copies", "checklist drift", "which repos
  have an old security.md", "merge the checklist PRs". Do NOT use to edit the canonical page
  (edit it in Notion first, after the approval), for code PRs, or for one repo's own CLAUDE.md.
metadata:
  version: "1.0.0"
knowledge:
  reads:
    - Guide/Index
    - Compliance/Security/Code Review Checklist
    - Procedures/Engineering/Logging Standards
    - Procedures/Engineering/PR Process
    - Tools/Notion
    - Tools/GitHub
---

# Sync the security checklist to every repo

The checklist and the logging rule are canonical in AI Knowledge and copied into 44 repos. A
copy that drifts is a reviewer applying an old rule, and nothing fails when that happens. This
skill makes the canonical page the only input: it never writes rule text of its own.

**Content goes in only after the approval.** The canonical page is the gate: a change is made on
the Notion page once it is approved, and only then does this skill carry it. Never edit a copy
first, and never add text to a copy that is not on the page.

## Rules

- **Repos one at a time**, from [`assets/repos.txt`](assets/repos.txt) (paths under
  `AURA_DEV_DIR`, default `~/Development/Aura`). No parallel cross-repo work, no worktree copies.
- **No person, session or team names in a copy, a commit or a PR.** Write "decision {date}" or
  "approved by an explicit go".
- **No card id in a branch or title** unless that PR closes the card. Link the tracking card from
  the body with link text (`[the checklist card](https://linear.app/…)`), never a bare id.
- **Docs only.** The rollout stages only `.claude/`, `.cursor/`, `CLAUDE.md`, `AGENTS.md` and the
  PR template, and stops if anything else is staged. Code PRs are never merged here.
- **Hooks run.** Never `--no-verify`; a repo's hook (AuraConsumerApp skips docs-only pushes) is
  respected.
- **Every BugBot finding gets a reply in its thread** (what changed and the sha), then is
  resolved. Never resolve silently.

## Files per repo

| Repo | Files |
|---|---|
| every repo | `.claude/rules/security.md`, `.cursor/rules/security.mdc`, `.cursor/BUGBOT.md` |
| Aura, AuraConsumerApp, LivekitVoiceAgent | the same, named `security-review.md` / `.mdc` |
| the 13 services | also `.claude/rules/logging.md`, `.cursor/rules/logging.mdc` |

Layers carry sections: services A+B+F, web A+C+F, Firebase rules and Functions A+D+F, mobile
A+E+F, everything else A+F. **`.cursor/BUGBOT.md` is not a layer copy**: each repo's has its own
wording around the checklist, so it is never overwritten, only edited line by line.

## Workflow

### 1. Snapshot the pages

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/sync-security-checklist/scripts/sync.py fetch
```

Renders both pages (Notion REST, `NOTION_TOKEN`) to `checklist.md` and `logging.md` in a new
folder under `~/.claude/sync-security-checklist/snapshots/`. The page's Sources section is left
out, as in the copies. Keep the previous snapshot: it is the "old" side of the next change. The
first time, the old side is the snapshot taken before the page was edited.

### 2. Plan the edits (read-only)

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/sync-security-checklist/scripts/sync.py plan --old <before> --new <after>
```

It diffs the two snapshots line by line and applies each edit to each repo's files as they are
on `origin/<default>`: a changed line goes in where its old text appears exactly once; an added
line goes after its anchor line. Nothing is rebuilt or overwritten. Per repo it prints:

- `files`: the files that change, with the edits applied;
- `missed`: edits whose old text isn't in that file. Expected for a section the layer doesn't
  carry, and for page-only text (the logging page's intro and worked cases). Read them: a miss
  in a section the layer **does** carry means that copy has drifted (step 3);
- `multi`: an edit whose old text appears more than once. That file is not changed, and the run
  exits 1. Fix the copy by hand or make the page edit unambiguous.

Show the plan (repos, files, misses) to the user before step 4.

### 3. Drift (read-only, any time)

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/sync-security-checklist/scripts/sync.py drift
```

Compares each rule file's blob across the repos of its layer and lists the repos whose copy
differs from the most common one, or is missing. BUGBOT.md is not compared. A drifted repo is
brought back by a rollout from the snapshot it missed, never by copying a sibling's file over it.

### 4. Roll out (writes: one draft PR per repo)

Write the commit message and PR body to files first: what changed, by section, "Docs only", and
the tracking card as a link. Then:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/sync-security-checklist/scripts/sync.py rollout --plan <plan dir> \
  --branch docs/security-checklist-<slug> --title "docs: security checklist: <what changed>" \
  --message-file <commit.txt> --body-file <pr.md>
```

Per repo: a **fresh** branch from the current default (never the default merged into an old
branch), the planned files written and force-added (some repos ignore `.claude/` or `.cursor/`),
the docs-only guard, commit, push, a draft PR, then `bugbot run` as its own comment. A repo with
an open PR on that branch is skipped. It prints one JSON line per repo; report the PR list.

### 5. Merge (writes: merges docs-only PRs)

Only a session allowed to merge docs-only PRs runs this step. **Never while a rollout is still
pushing.**

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/sync-security-checklist/scripts/sync.py merge Repo#N Repo#N … [--wait 30]
```

In the order given, it merges a PR only when every file is docs-only, BugBot has run on the
head with 0 unresolved threads (outdated ones included), and every check passed. It stops at the
first PR that is not clean, or whose repo is on the release-hold list
(`~/.claude/librarian/release-holds.txt`, one repo per line, or `SYNC_CHECKLIST_HOLDS`). A
missing hold list stops every merge.

### 6. Close

Report what merged, what is waiting and why, and each repo's misses. Log the change in the
Security Change Log the same day.

## Exit codes

`0` finished. `1` stopped on a finding: an ambiguous edit, drift, a failed repo or PR, or a merge
that stopped. A `missed` edit alone is not a failure. `2` a bad argument or missing input.
