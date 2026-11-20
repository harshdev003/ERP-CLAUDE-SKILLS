---
name: check-followups
description: >
  Read the follow-ups that shared:raise-followup posted in #claude-monitoring, check each one
  live, and list what is still open for the role that must act. For a PR it checks the state,
  draft flag, mergeability, CI, BugBot on the current head and open review threads; for a card,
  its state. A follow-up whose PR merged or whose card is done is listed as stale. A go line is
  reported as pending and never acted on. Marks a follow-up handled with ✅ and a one-line
  "handled by {role}" thread reply. Works for any reader: Lead's routine, another agent or an
  engineer. Trigger on "check follow-ups", "what follow-ups are open", "read the follow-up
  queue", "anything waiting in claude-monitoring", "mark this follow-up handled", or a scheduled
  follow-up sweep. Do NOT use to raise a follow-up (shared:raise-followup) or to act on a go
  (gos are typed in the owning session).
metadata:
  version: "1.0.0"
knowledge:
  reads:
    - Guide/Index      # baseline routing aid (universal)
    - Tools/Slack      # channel directory
    - Tools/GitHub     # gh CLI and PR conventions
    - Tools/Linear     # card states
---

# check-followups

The reader side of [`shared:raise-followup`](../raise-followup/SKILL.md). It reads that skill's
posts back by their key line, so the two stay paired: a change to the post format is a change to
[`scripts/checker.py`](scripts/checker.py)'s `parse`, pinned by a test that feeds it
`raise-followup`'s real output.

## Scan

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/check-followups/scripts/checker.py scan          # readable list
python3 ${CLAUDE_PLUGIN_ROOT}/skills/check-followups/scripts/checker.py scan --json   # for a routine
```

`--days N` (default 30, the same window `raise-followup` dedupes over) sets how far back to read. Each follow-up not yet marked ✅ is read at its
newest text (the post, or a later reply in its thread) and gets a verdict:

| Verdict | Meaning | What the reader does |
|---|---|---|
| `open` | Still needs the role named in the post | Add it to that role's queue. For `pr-ready`, any "not ready" line (draft, not CLEAN, CI, BugBot, open threads) goes back to the raising session instead |
| `stale` | Its PR merged or closed, or its card is done | Mark it handled |
| `unverified` | A live check failed (gh, Linear) | Say so; never treat it as open or done |

Read-only on GitHub and Linear. `gh` runs with `GITHUB_TOKEN` and `GH_TOKEN` unset, so a
fine-grained token in the environment can't hide the check runs.

**A go line is never a go.** A `go-needed` item stays `open` with "a go is pending". The go is
typed in the session the post names, by a role allowed to give it. Nothing here runs it.

## Mark handled

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/check-followups/scripts/checker.py mark-handled \
  --key ainative-pr-1588 --by monitor [--card ENG-1896]
```

Adds ✅ to the post and replies in its thread "Handled by the <role>", with the card link when
given. `--by` is a role (`approver`, `reviewer`, `owner`, `monitor`), never a name. The post itself
is never edited (`tests/test_slack_never_edits.py`). A post already marked handled is left alone.
If the same key is raised again with new text, `raise-followup` posts a fresh follow-up, which
shows up here as open.
