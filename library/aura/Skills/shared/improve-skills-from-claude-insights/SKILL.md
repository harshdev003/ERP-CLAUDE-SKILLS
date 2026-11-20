---
name: improve-skills-from-claude-insights
description: >
  Turn a Claude Code usage-insights report (the HTML from `/insights`, saved at
  ~/.claude/usage-data/report-*.html) into vetted, approved, implemented AINative skill + AI
  Knowledge improvements. Parses the report's friction / CLAUDE.md / pattern / horizon
  sections, TRIAGES each candidate against the repo (real-gap vs already-covered vs
  not-a-skill vs speculative, with file:line evidence), proposes only the survivors as Linear
  cards, and — on a second explicit approval — implements them via eng:linear-task. Two human
  gates; never auto-implements. Trigger when the user says: "review my claude insights",
  "improve skills from claude insights", "improve skills from insights", "process the insights
  report", "what should we improve from the insights", or runs this right after `/insights`.
  Do NOT use for distilling a session into rules (shared:distill-session) or syncing AI
  Knowledge from Notion (shared:harvest-knowledge).

metadata:
  version: "3.0.1"
knowledge:
  reads:
    - Guide/Index                    # baseline routing aid (universal)
    - Procedures/Engineering/Estimation  # FAST scale for sizing proposed cards
    - Tools/Linear                   # card creation (via shared:add-linear-issues)
    - Tools/GitHub                   # implementation PRs (via eng:linear-task)
---

# improve-skills-from-claude-insights

Turn the periodic Claude Code **usage-insights report** into concrete AINative
skill improvements — the same loop a human runs by hand, encoded so it stays
**honest** (most report items are already covered) and **gated** (nothing is
carded or built without an explicit yes).

You are a skeptical reviewer, not an eager implementer. The report is a list of
*candidates*, not a to-do list — your job is to disprove each one against the
repo before proposing it, then let the user decide what becomes a card and what
gets built.

## When to use

- "review my claude insights" / "improve skills from claude insights"
- "process the insights report" / "what should we improve from the insights"
- right after running `/insights` (which regenerates the report)

**Do NOT use for:**

- Distilling one session's learnings into rules → `shared:distill-session`.
- Syncing AI Knowledge from upstream Notion → `shared:harvest-knowledge`.
- Building a metrics report from Mixpanel/BigQuery → the data/eng report skills.

## Inputs

- **`report`** (optional) — path to a `report-*.html`. Default: the newest in
  `~/.claude/usage-data/`.
- **`scope`** (optional, default `ainative`) — kept for future use; today the
  skill only proposes AINative skill / AI Knowledge changes and surfaces
  everything else as notes.

## Workflow

### Step 1 — Parse the report (deterministic)

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/improve-skills-from-claude-insights/scripts/parse_insights.py [--report <path>]
# or list what's available: parse_insights.py --list
```

The script emits JSON with `meta` (date range, counts), `glance`, `friction`
(`{title, desc, examples[]}`), `claude_md` (`{text, why}` — the suggested
CLAUDE.md additions), `patterns`, and `horizon`. If it exits with "No insights
report found", tell the user to run `/insights` first (or pass `--report`).

The **candidate set** is: every `friction` item, every `claude_md` suggestion,
and any `pattern` / `horizon` item that names a concrete, skill-shaped change.
Ignore the celebratory/aggregate sections (`glance`, impressive-things, charts) —
they're context, not candidates.

### Step 2 — Triage each candidate against the repo (the load-bearing step)

For **every** candidate, investigate the AINative repo before proposing anything.
Grep and read the actual skills — do not trust the report's framing. Classify
each into exactly one of:

- **real-gap** — the improvement is genuinely missing or wrong in the repo today.
  This is the only class that can become a card.
- **already-covered** — a skill already does this. **Cite the proof** as
  `file:line` (e.g. "`daily-status/SKILL.md:77` already sizes by changedFiles").
  Do not propose these.
- **not-a-skill** — real, but it's an environment/config issue (e.g. a shell/npm
  setting) or lives in an external repo, not an AINative skill. Carry as a note.
- **speculative** — plausible but unverified; no concrete failure found. Carry as
  a note ("worth a look if it bites"), not a card.

Guidance that makes triage accurate:

- Prefer `grep -rn` across `plugins/**/SKILL.md` and `plugins/**/scripts/` for
  the behaviour the candidate asks for, then read the hits.
- The estimation, heredoc, branch-preflight, PII, UTF-8, and portability
  conventions are **already** encoded in `create-aura-skill`/`update-aura-skill`
  best-practices + audits — check there before proposing anything in those areas.
- When in doubt, downgrade (already-covered / speculative) rather than propose.
  A false "real-gap" wastes a card and a PR; a missed one costs nothing but a
  future re-run.

Produce a triage table: candidate → class → one-line evidence.

### Step 3 — Propose survivors (HUMAN GATE #1)

Show **only** the `real-gap` survivors as proposed cards, each with:

- a commit-style title (`fix(...)` / `docs(skills): ...` / `feat(...)`),
- one-line scope,
- a **FAST-scale estimate** — read `Procedures/Engineering/Estimation` (via
  `retrieve-knowledge`) and size by blast radius (new skill ≈ 5; multi-file
  fix ≈ 3-5; docs/one-liner ≈ 1-2),
- the triage evidence.

Then list the `not-a-skill` and `speculative` items as **notes** (not cards), so
the user sees the full accounting and nothing looks silently dropped.

**Stop and let the user pick** which survivors become cards. Do not create
anything yet. If the user says "none", end with the readout.

### Step 4 — Create the picked cards

Resolve the current umbrella dynamically — the "Update Knowledge and skills
[Cycle N]" issue for the **current** cycle (the umbrella lags the real cycle
number; find the current cycle via `list_cycles` for the Product/Design team,
then the umbrella issue in that cycle).

**Re-run guard (idempotency — do this before creating anything).** This skill is
meant to be run repeatedly (e.g. weekly on a fresh report), and the same
underlying friction can recur across reports, so a naive create would duplicate
cards. Before creating, list the umbrella's existing sub-issues
(`list_issues parentId=<umbrella>`, include non-open states) and **skip any picked
card whose title matches an existing child** (compare on the normalised title —
case-insensitive, ignoring the `fix(...)`/`docs(...)` prefix). For a near-match
that's clearly the same work, surface it and ask whether to skip, update the
existing card, or create anyway — never silently create a duplicate. Report the
skipped-as-duplicate set alongside the created set.

Then create each *surviving* picked card as a **sub-issue** of that umbrella by
composing **`shared:add-linear-issues`** (it validates project / label / parent /
estimate) with:

- `parentId` = the umbrella,
- `team` = the umbrella's team, `project` = AI Native, `assignee` = me,
- `estimate` from Step 3, a sensible `priority`, `state` = Todo,
- a description carrying the source (report date), the triage evidence, the
  proposed change, and a definition of done.

Report the created card identifiers + URLs (and any skipped as duplicates).

### Step 5 — Implement approved cards (HUMAN GATE #2 — never automatic)

Ask the user which of the just-created cards to **build now**. Only for those,
implement each by composing **`eng:linear-task`** (one run per card) — it owns
the full loop: worktree off `origin/main` → implement → draft PR → `bugbot run`
→ address findings → mark ready. Do the cards **one at a time** unless the user
says otherwise, and state the merge order if any are stacked (per
`/CLAUDE.md` → PR workflow: after a base merges, confirm the stacked PR
retargeted to `main` before merging it).

If the user approves none, stop — the cards stand as planned work.

### Step 6 — Report

Summarise: the report reviewed (date/counts), the triage tally (how many
real-gap / already-covered / not-a-skill / speculative), the cards created (with
URLs), the PRs opened (with state), and the carried notes. Keep the
already-covered proof in the readout — it's the evidence the review was honest.

## Guardrails

- **Two human gates, always.** Candidate→card (Step 3) and card→build (Step 5).
  Never create cards or open PRs without the corresponding yes.
- **Scope is AINative.** Only skills + AI Knowledge become cards. Env fixes,
  machine config, and external-repo issues are notes.
- **Evidence or it didn't happen.** Every "already-covered" call carries a
  `file:line`; every "real-gap" carries what's missing. No hand-waving.
- **On-demand.** Scheduling ("run this weekly") is a separate cron/routine that
  invokes this skill; the skill itself does one report per run.

## Edge cases

- **No report yet** → tell the user to run `/insights`, or pass `--report`.
- **Stale report** → note the report's date range in the readout; offer to
  re-run `/insights` if it's old.
- **Everything already covered** → a clean, honest outcome. Report the
  already-covered tally with proof and create no cards.
- **Cowork / headless** → Linear + `linear-task` need their usual access; if the
  environment can't create cards, stop after the Step 3 proposal and hand the
  user the proposed list.
