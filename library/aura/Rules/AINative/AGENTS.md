<!-- Generated from CLAUDE.md by scripts/build-agents-md.py — do not edit by hand.
     Edit CLAUDE.md, run `python3 scripts/build-agents-md.py --write`, commit both. -->

# AINative

**This repo exists to author skills and commands for Codex, Claude Code, and Cursor.** Everything in it — the directory layout, the marketplace manifests, the shared helper skills — is in service of that one job: making it easy for AuraHealth teams to ship AI-native tooling.

When you work in this repo, you're almost always doing one of:

- **Creating a new skill** (use the `create-aura-skill` skill in the `shared` plugin)
- **Updating an existing skill** (use `update-aura-skill`)
- **Authoring a slash command** (`plugins/<team>/commands/<name>.md`)
- **Maintaining the plugin marketplace manifests** at the root

## What this repo is

- A **marketplace** — `.agents/plugins/marketplace.json`, `.claude-plugin/marketplace.json`, and `.cursor-plugin/marketplace.json` at the root catalog the plugins.
- A set of **plugins** under `plugins/<team>/`, each with its own `skills/` and `commands/` directories.
- Plugins install via `/plugin install <name>@aura-tools` (Codex and Claude Code) or Cursor's team marketplace import flow.

## Plugins

| Plugin | Scope | Purpose |
|---|---|---|
| `shared` | Everyone | Org-wide skills — Linear, Slack, Notion (AI Knowledge), PR metrics, skill authoring |
| `eng` | Engineering | Backend/mobile/web dev skills — handoffs, Linear tasks, 11Labs, intelligence prompts |
| `product` | Product | Spec writing, ticket management, roadmap workflows |
| `data` | Data | Analytics, ML pipelines, eval workflows (e.g. `analyze-experiment`) |
| `marketing` | Marketing | Ad creative, growth, campaign workflows |
| `qa` | QA | End-to-end testing — Aurie conversation drivers, regression sweeps, simulated user QA |

See [README.md](./README.md) for install instructions.

## Repo layout

```
AINative/
├── AGENTS.md                       # This file — project memory
├── README.md                       # Human-facing install + contribute docs
├── .agents/plugins/marketplace.json # Codex catalog
├── .claude-plugin/marketplace.json # Claude Code catalog
├── .cursor-plugin/marketplace.json # Cursor catalog
└── plugins/
    ├── shared/
    │   ├── .codex-plugin/plugin.json
    │   ├── .claude-plugin/plugin.json
    │   ├── .cursor-plugin/plugin.json
    │   ├── skills/<skill-name>/SKILL.md
    │   └── commands/<cmd-name>.md
    ├── eng/
    ├── product/
    ├── data/
    └── marketing/
```

Each skill lives in its own directory with a required `SKILL.md`. Optional subdirs: `scripts/` (executables), `references/` (docs the skill reads), `assets/` (static files).

## Architecture principles

These are non-negotiable shape rules for the repo. Add new principles here when a recurring decision crystallises into a rule.

### 1. Skills are self-contained

**A skill folder ships everything it needs to run.** A skill at `plugins/<team>/skills/<skill-name>/` must not import, read, or reference any file outside its own directory at runtime.

This means:

- **No cross-skill file references.** `data:sync-experiments` may not `import` from `plugins/shared/skills/retrieve-knowledge/scripts/`. Ditto for reading reference markdown, including assets, etc.
- **No reaching into sibling plugins.** Scripts in `plugins/data/skills/<x>/scripts/` do not load files from `plugins/shared/`.
- **No central `plugins/<team>/scripts/` directories.** If you find yourself wanting one, it's a sign the code belongs inside one of the consuming skills (or the skill needs to be split). The closest violation today is the original `plugins/shared/scripts/notion_rest.py` placement — explicitly removed in [PROD-1336](https://linear.app/aura-health/issue/PROD-1336) when this principle was codified.

The trade-off is a small amount of code duplication for shared utilities. We accept it because:

- Each skill is portable — drop the folder anywhere with the right env and it works.
- Drift is visible (one file diverges from a sibling) instead of silent (one file changes underneath many callers).
- Skills can be deleted without auditing the rest of the repo for orphan references.

**Shared utilities** (e.g. `notion_rest.py`) are handled by **per-skill copies + a canonical source** — pick one consumer skill as canonical, copy the file into the others' `scripts/` dirs, document the canonical location in AGENTS.md, and propagate edits across copies in the same PR. The duplicate-detection one-liner lives in the relevant integration section (e.g. "Notion REST integration" below).

**Two explicit exceptions: subagent definitions and plugin hooks.** Both are plugin-level infrastructure whose location is fixed by the plugin loader (`plugins/<plugin>/agents/*.md`, `plugins/<plugin>/hooks/hooks.json`), not skills, so principle 1 does not govern them: a hook may import a skill's bundled script by relative path (`shared/hooks/knowledge_context.py` imports `retrieve-knowledge/scripts/manifest.py` rather than carrying a third copy). Agents in detail:

**Subagent definitions.** Reusable subagents are the only file type that cannot live inside the skill folder. The plugin loader discovers agents **only** at `plugins/<plugin>/agents/<name>.md` — agents nested inside a skill (e.g. `plugins/<plugin>/skills/<skill>/agents/<name>.md`) are silently dropped and never registered as a `subagent_type`. So the agent file lives at the plugin root, and the skill that uses it references the file via a relative path. We've tried to push back on this (the `qa-prompt-sweep-runner` originally landed inside `plugins/eng/skills/qa-prompt/agents/` per [#250](https://github.com/AuraHealth/AINative/pull/250)'s self-containment instinct, then was moved to the plugin root once the loader limit was confirmed); revisit if the host adds a manifest field for nested agent paths. Full constraint and naming rules: Principle 4 below.

### 2. Skills declare their `knowledge:` reads/writes

Every skill declares a `knowledge:` block in its SKILL.md frontmatter (full spec in [Knowledge manifest](#knowledge-manifest) below). At minimum every skill reads `Guide/Index` — the live Path → Description map of the AI Knowledge teamspace — so it has a routing aid for any context it didn't pre-declare. Additional reads/writes are added as the skill needs them.

### 3. SKILL.md trigger descriptions are the only registry

The skill registry that matters at runtime is the union of every SKILL.md `description` field. There is no separate `registry.json`. Don't add one — adding parallel sources of truth produces drift.

### 4. Subagents live at the plugin root, not inside the skill folder

Reusable subagent definitions go at `plugins/<plugin>/agents/<name>.md` — same YAML-frontmatter + system-prompt-body shape as skills, but the body is the agent's full prompt rather than a how-to-invoke procedure.

**This path is fixed by the plugin loader, not a stylistic choice.** The loader scans `plugins/<plugin>/agents/` only — agents placed inside a skill folder (e.g. `plugins/<plugin>/skills/<skill>/agents/<name>.md`) are silently dropped and never registered as a `subagent_type`. No `plugin.json` field overrides this. Skill self-containment (Principle 1) does not apply to agents for this reason; this is the only exception.

The cross-check: the names that appear in the available-agents list at the start of a session should match exactly the set of `plugins/<plugin>/agents/*.md` files. If an agent file exists but isn't in the list, the loader didn't see it — almost always because it's nested inside a skill folder.

Examples in `eng/`:

- [`implementation-agent`](plugins/eng/agents/implementation-agent.md) — write-side. Spawned by `eng:linear-task` to implement a Linear ticket end-to-end (worktree, commit, push, draft PR).
- [`code-investigator`](plugins/eng/agents/code-investigator.md) — read-side counterpart. Spawned by `eng:investigate-bugsnag-errors` (Mode 2.5, [PROD-1659](https://linear.app/aura-health/issue/PROD-1659)) and any future `investigate-*` skill that needs source-code context. Owns repo-discovery + pull-latest semantics so callers don't reimplement them.
- [`qa-prompt-variant-runner`](plugins/eng/agents/qa-prompt-variant-runner.md) — spawned by `eng:qa-prompt` in comparison mode, one per variant.
- [`qa-prompt-sweep-runner`](plugins/eng/agents/qa-prompt-sweep-runner.md) — spawned by `eng:qa-prompt` in sweep mode, one per scenario.

**Naming family** mirrors skills — verb-first when an action (`code-investigator`, `pr-reviewer`), noun-first only when the agent represents an artifact.

**When to extract a named subagent** vs use the generic `Explore` / `general-purpose` agents: define a named one when the same prompt + workflow + output shape is needed by 2+ skills, OR when the work has a bounded read window (investigation) or a bounded write surface (implementation) that benefits from being declared once and reused. One-off investigations stay on `general-purpose`.

**Plugin-agent constraint — read this before adding a subagent.** Plugin-shipped subagents **cannot** declare `tools:`, `disallowedTools:`, `hooks:`, `mcpServers:`, or `permissionMode:` in frontmatter — those are blocked by the plugin security model. So tool restrictions can't be enforced by the harness; they have to be enforced by the system prompt and the agent's discipline. If you genuinely need a `tools:` allowlist, the agent has to live at `.claude/agents/` (project-local) instead — but then it's not part of the plugin marketplace and contributors who install the plugin don't get it. We accept the tradeoff: plugin agents document their boundaries clearly in the system prompt (see `code-investigator` for the pattern: a "Read-only contract — non-negotiable" section listing what the agent must not do) and trust the model to respect them.

**Knowledge declarations.** Subagents declare their own `knowledge:` reads/writes in frontmatter, same rule as skills, so `healthcheck-knowledge` can audit them.

## Skill authoring quick reference

- **Best practices:** read [`plugins/shared/skills/create-aura-skill/references/best-practices.md`](plugins/shared/skills/create-aura-skill/references/best-practices.md) before drafting or revising a skill. Covers atomic scope, plan-before-side-effects, deterministic-first and more (the file lists them and carries no count, so it can't go stale — don't count them here either, per *Canonical facts* above); loaded by `create-aura-skill` and `update-aura-skill` automatically. Narrative version with rationale lives at `Procedures/AI Native/Skill Authoring` in the AI Knowledge teamspace.
- **Create:** use the `create-aura-skill` skill from `shared` — it scaffolds the files, picks the plugin, commits, and opens a PR.
- **Update:** use `update-aura-skill`.
- **Invoke a script from a skill:** `python3 ${CLAUDE_PLUGIN_ROOT}/skills/<skill-name>/scripts/<file>.py`.
- **Every SKILL.md needs** YAML frontmatter with `name`, `description`, and `metadata.version` (version nested under `metadata:` per the Agent Skills spec — see "Skill versioning" below). The description should include trigger phrases so the model can match user intent (see existing skills for examples).
- **Python scripts force UTF-8 stdout.** Any `scripts/*.py` that prints to stdout must call `sys.stdout.reconfigure(encoding="utf-8")` at the top of `main()` (before argparse, before any output). Python's default stdout encoding on Windows is `cp1252`, which raises `UnicodeEncodeError` on Markdown arrows (`→`), em-dashes (`—`), curly quotes, or emoji — all routine in our rendered reports. `create-aura-skill` scaffolds the guard, `update-aura-skill` flags it when missing. Origin: [#219](https://github.com/AuraHealth/AINative/pull/219).

**The registry of shared files that must be kept in sync lives in [`docs/canonical-sources.md`](docs/canonical-sources.md)** — one entry per shared file naming its canonical copy, what the copies must agree on, and the `find` / `diff -q` drift command. Read it before editing any file that has copies.

Two rules travel with it, and they are why the registry is a reference rather than something to recall:

- **The drift command and any shape-test are the authoritative membership check.** The prose copy lists in that file are a convenience and have drifted before — run the command rather than trusting the text. Any output from a drift command is a warning to reconcile in the next PR.
- **Edit the canonical first, propagate in the same PR.**

### The drift guards compare copies, never implementations

**Every `diff -q` in [`docs/canonical-sources.md`](docs/canonical-sources.md) answers one question: do the copies of this filename match?** None can see a *second implementation of the same rule under a different filename* — a structural blind spot. When you propagate a *decision*, grep for the **behaviour** (the threshold, the field name, the effect), not the file:

```bash
grep -rn "THRESHOLD\|_threshold" plugins/ --include='*.py'   # ✅ propagating a decision
find plugins -name the_one_file.py -exec diff -q ... \;      # ❌ trusting the file check
```

**Incident:** [ENG-1048](https://linear.app/aura-health/issue/ENG-1048) made `fetch_ratings.py` *report* prolific UIDs (threshold 20, report-only); it never reached `harvest-conversation-patterns`'s `fetch_sweep_analyses.py`, which reimplemented the rule at 5 and **dropped** — a 4× tighter destructive bar on the path that *writes* the durable table. Every guard stayed green (the drift check saw 3 files; 5 encode the rule). Found via a re-harvest dropping ~37% of entries ([ENG-1132](https://linear.app/aura-health/issue/ENG-1132)). It has bitten twice — a "diff by eye" note is itself the marker that a rule lives across filenames, so **when you'd write a manual cross-check into this file, write the test instead** ([ENG-1142](https://linear.app/aura-health/issue/ENG-1142)). A "no drift" result means the copies match — never that the rule holds everywhere it is implemented.

### A finding about the tool belongs in the tool, not in the output

**The rule above is the narrow case — a rule living across filenames needs a test, not a note in this file. The general case is any finding about how a tool behaves, made while using it.** A skill's reference, a script and a test are *executed, every time, for everything*. A row body, a retro entry, a `notes` field and a Slack thread are read by *a human, later, about that one thing* — so a mechanism-level finding written into one of those protects exactly one row and no others.

So when you diagnose a defect mid-task, **put the correction where the mechanism is**, in the same session where you can. When you can't — it's out of scope, or it needs a decision — **the finding gets a card, and the note in the artifact links it.** A workaround note with no card is the marker that one is owed, not a substitute for one.

**Incident:** four defects in one session were each diagnosed correctly *in writing*, worked around, and left live for months ([PROD-6225](https://linear.app/aura-health/issue/PROD-6225)). The clearest: `analyze-experiment`'s Mixpanel template breaks down by event, so every rate divides by a near-zero `N` — a complete, correct diagnosis with a sound workaround, written at analysis time into **thirteen `Learnings/Experiments` row bodies word for word** ([PROD-6175](https://linear.app/aura-health/issue/PROD-6175)). It was not rediscovered thirteen times; it was copy-pasted forward until it read as a house convention, while the skill kept shipping the broken query to the next reader. The other three: a retro entry naming a row that turned out to be assigning real users seven months on, a `notes` field on a dev fixture that assigned 98,532 users over 240 days, and a monitor's own Slack thread asking nine days running who could fix an instrumentation gap.

Nobody's analysis was wrong in any of the four — in three, the person produced sound work *because* they had diagnosed the problem. The whole cost was that the correction reached one row instead of the tool.

**This one has a detector, because the pattern has a written signature.** `shared:healthcheck-knowledge` detector #13 clusters workaround sentences across knowledge database rows: **two rows carrying the same note is the signal**, and spotting it needs no judgement — the thirteen were greppable the whole time. Per the rule above the detector itself has a test, run against the real 13-row corpus ([`tests/test_workaround_notes.py`](tests/test_workaround_notes.py)): a detector for a known cluster that can't find that cluster is worth nothing, and only a real fixture can assert it.

### Canonical facts: point, don't restate

The rules above govern shared **files**; the same applies to shared **facts** (a project ID, endpoint, rate, channel ID, table name), except the fix is to **not duplicate the value at all**. A fact about an external service has one home — its `Tools/<service>` page in AI Knowledge — and repo docs **point at it rather than restate it**.

```markdown
✅  Rates: see `Tools/Deepgram` → Plan & pricing (canonical).
❌  Rates: nova-3 streaming $0.0048/min = $0.288/hr.
```

**When you genuinely must inline a value** (a token-inventory row, a real table ID in a script), it becomes a copy — so grep the OLD value and update every hit in the same PR:

```bash
grep -rn --include='*.md' --include='*.py' '<old-value>' . | grep -v '.claude/worktrees'
```

Prefer a pointer even where prose already carries the value. This is a convention, not a checker — a checker detects drift only after it ships and needs an allowlist ([ENG-961](https://linear.app/aura-health/issue/ENG-961) / [ENG-962](https://linear.app/aura-health/issue/ENG-962), both cancelled). **Incident:** a corrected Deepgram project ID stayed stale in 7 repo locations for 4 days and had been encoded into behaviour before anyone noticed ([#674](https://github.com/AuraHealth/AINative/pull/674) → [#690](https://github.com/AuraHealth/AINative/pull/690)).

### Orchestrators: restrict what you show, don't silently withhold the deliverable

A skill that **composes** another should **point at the composed skill's rules, not restate them**. Overriding is legitimate (an unattended runner has no human at decision time), but an override must: **name what it overrides and why**, as its own decision — never phrased as the composed skill's rule, and never with fabricated provenance ("ratified by", "do not re-litigate"); **be re-checked when the composed skill changes**; and **state which direction it errs**, on any safety path.

**Restricting what you show ≠ withholding the deliverable.** Redacting a UID, coarsening a location, summarizing — those remove detail from an artifact that still exists. *Declining to produce* the artifact looks identical to a clean run (a missing output has no shape) and must be declared as its own decision. Suppression is **not symmetric on a safety path**: not showing a UID reduces exposure; not showing a crisis-resource email withholds help. **Incident:** a cadence gave itself a "permanent, do-not-re-litigate" strict draft gate that re-encoded a superseded rule, so every active-risk trip posted a verify-note instead of a CS draft — an active domestic-violence case undrafted ~2 days, six suppressed drafts reading as six handled alerts ([ENG-1045](https://linear.app/aura-health/issue/ENG-1045) / [#645](https://github.com/AuraHealth/AINative/pull/645) / [#657](https://github.com/AuraHealth/AINative/pull/657) / [#746](https://github.com/AuraHealth/AINative/pull/746)).

### Tag, don't drop, wherever the output persists

**A dropped row leaves no trace in a durable artifact, so a later reader can't tell a filtered corpus from a complete one.** Prefer **tagging** (write the row, annotate it, report the count) anywhere the output is a table/snapshot/store/file; filter at *read* time, where the filter is visible and reversible. A **wrong** value is loud; a **missing** one has no shape — so dropping is the *less* conservative choice even though it reads as careful. Three consequences:

- **A tag needs a reported count**, or it is just a quieter drop (`skipped_volume: 0`, not a lone boolean nobody totals).
- **Inferred criteria don't justify a drop; criteria about the record itself might.** "This account rates a lot" is an inference; "this uid is in the internal-accounts allowlist" is a fact about the account.
- **When you do drop, count it in the output** (`skipped_infra`) — the drop stays defensible *and* visible.

This is the resolution of [ENG-1132](https://linear.app/aura-health/issue/ENG-1132)'s first acceptance criterion — recorded with that decision, not ahead of it. A future card may decide a writer genuinely *should* drop; it just has to argue that in the open and say why it diverges.

### Verify the path production takes, not one that resembles it

**Every check above is about the code; this one is about the check.** Four PRs in one session ([#981](https://github.com/AuraHealth/AINative/pull/981) / [#983](https://github.com/AuraHealth/AINative/pull/983) / [#984](https://github.com/AuraHealth/AINative/pull/984) / [#989](https://github.com/AuraHealth/AINative/pull/989)) each shipped a real defect past a green suite and a hand-run "live" verification, because the verification touched a **different input than production does**:

- **A fixture only the test can construct** — a `meta` dict whose key the producer cannot emit, so the branch was unreachable on real data while the test stayed green. **Feed a consumer its real producer's output** when both live in this repo.
- **A hand-supplied input production computes differently** — a BigQuery read checked by hand-typing `"aura-main"` while `main()` passed the Cloud *Logging* project; the wiring was the defect and any failure was swallowed into `UNMEASURED`. **A live check resolves its inputs the way the caller does** (go through `main()`'s own accessor).

**An empty or failed read is unverified, never a match.** A check that compares two values asserts both are non-empty first: `[ "$live" = "$expected" ]` is true when both reads came back empty. A live-digest check run on a lapsed user gcloud login ("Reauthentication failed") got nothing from `kubectl` and the registry and printed "matches live" for four apps; it was caught before the PR and redone as the `GCP_KEY` service account (ENGWEB-348, AuraInfra#130).

**Say "verified" only about the path that runs** — reporting a hand-run call as a live verification transfers false confidence and stops the reader looking. This one has a test (`TestConsumerReadsOnlyWhatTheProducerEmits` in [`tests/test_deepgram_transport.py`](tests/test_deepgram_transport.py); read set discovered, not listed); the **wiring** half is per-case (`test_the_call_uses_the_warehouse_project`), written each time a resolver is introduced.

**A third face — the producer is another system.** When a parser consumes another service's output, the corpus is a **sample**: evidence about what has happened, never about what the producer can emit. A branch nothing has exercised leaves no trace (a Slack footer `[givenName, day, id].filter(Boolean).join(' - ')` drops an empty name — 0 occurrences in 857 messages, but four lines of the producer show it); and a sample cannot bound a closed set (the canned-answer enum is exactly five and closed per `PMFNotDisappointedOption.swift`). **So read the other system's emitting code, and prefer fixtures generated by running it** (check one byte-identical against real output you already hold). No test in this repo can reach cross-repo producers, so it stays a habit ([ENG-1312](https://linear.app/aura-health/issue/ENG-1312) / [ENG-1313](https://linear.app/aura-health/issue/ENG-1313) / [ENG-1315](https://linear.app/aura-health/issue/ENG-1315)).

**A fourth face — the producer is a third-party source.** A skill that fetches from an outside service (a store feed, an archive, a public API) **runs once against the live source before its PR is opened as ready**; stubbed tests prove the parsing, never that the source answers this machine. If the live run can't happen, the PR stays a draft and says so. **Incident:** `marketing:harvest-social-mentions` merged with green tests and had never returned a row: its Reddit archive refuses automated callers outright (HTTP 429, "does not provide free scraping resources for agents"), so every run would have reported errors and zero mentions ([ENG-1580](https://linear.app/aura-health/issue/ENG-1580)).

## Knowledge manifest

Skills that read from or write to the AI Knowledge Notion teamspace declare their dependencies up front in the SKILL.md YAML frontmatter:

```yaml
---
name: analyze-experiment
description: ...
knowledge:
  reads:
    - Guide/Index                  # baseline routing aid (universal)
    - Products/Aurie          # brand voice
    - Identity/Audience            # vertical enum
    - Learnings/Experiments        # prior experiment rows
    - Signals/Conversation Patterns # recent rated-conversation signal
    - Tools/Mixpanel               # how to fetch dashboard data
  writes:
    - Learnings/Experiments        # records new learning
---
```

**Path syntax.** Use the exact Notion page/database titles, slash-separated from the top-level section. Case- and whitespace-sensitive (`Ad Campaigns`, not `ad-campaigns`). The canonical, always-fresh inventory is the live `Guide/Index` page in the AI Knowledge teamspace — regenerated by `harvest-knowledge` on every sweep, audited by `healthcheck-knowledge`, and consumed by `search-knowledge` and `retrieve-knowledge` as a routing aid. No bundled copy in this repo.

**Discovering paths.** When a skill author or agent doesn't know which path fits a need, use the `search-knowledge` skill (`shared` plugin) — it queries `Guide/Index` first, falls back to teamspace-scoped Notion search, and returns ranked candidate paths.

**Page vs database.** `Identity/*`, `Products/*`, `Domain/*`, `Procedures/*`, `Tools/*` are pages. `Learnings/*` are databases (curated, lifecycle-managed rows). `Signals/*` is the **observational layer** and holds *two* shapes: **snapshot pages** — an index page with dated immutable child pages, each snapshot its own page (e.g. `Signals/Aurie Conversations`) — **and** **observational tables** — databases whose rows accumulate as the world produces them (e.g. `Signals/Conversation Patterns`, one write-once row per rating; `Signals/Cancellations`, one row per cancellation event, enriched in place). The distinction Learnings-vs-Signals is *curation*, not *storage kind*: Learnings is synthesised/actioned knowledge, Signals is the raw observational stream (snapshots or tables). A Signals path's kind (`snapshot` vs `table`) is declared by its schema file; `retrieve-knowledge` and `record-knowledge` pick the recipe from that. Skills don't declare the kind.

**Products section.** `Products/<app>` documents what we ship — one subtree per app (`Aurie`, `Aura`), each covering screens, features, subscription/pricing and technology. It was promoted out of `Identity/Apps` in [PROD-6205](https://linear.app/aura-health/issue/PROD-6205): three of Identity's four children really are identity, and a screenshot gallery plus subscription SKUs are product surface, not who we are. `Identity` keeps `Company`, `Brand`, `Audience`. **The old `Identity/Apps/<app>` spelling is dead** — the Notion pages kept their ids and URLs across the move, so nothing linked broke, but every *declared knowledge path* was repointed in one cut (33 SKILL.md files across five plugins). A skill still declaring `Identity/Apps/...` is reading a path that no longer resolves. Like `Identity/`, `Products/` sits outside `record-knowledge`'s write boundary — it is human-curated, not agent-written.

**Tools section.** `Tools/<name>` documents external systems Aura calls into (Notion, Slack, Linear, GitHub, Braintrust, Mixpanel, 11Labs, Gemini, OpenAI, YouTube, Aura API). A skill that depends on a tool — for purpose, env-var name, or access pattern — should declare it in `knowledge.reads`. Actual secret values still live in `~/.claude/settings.json`, never in Notion.

**What it unlocks.**

| Capability | Mechanism |
|---|---|
| Pre-fetch | `retrieve-knowledge` (Recipe F) reads the caller's `knowledge.reads` **from disk** via its bundled `manifest.py` — a skill's SKILL.md or an agent's `.md` — resolves every path, and hands the content to the skill or agent before its main logic runs |
| Write guardrail | `record-knowledge` reads the caller's `knowledge.writes` from disk (`manifest.py --skill <caller> --check-write <path>`, or `manifest.py <agent.md> --check-write <path>` for a subagent) and refuses any path not declared, unless the caller explicitly passes `allow_undeclared: true` |
| Healthcheck integration | `healthcheck-knowledge` [detector #14](plugins/shared/skills/healthcheck-knowledge/references/detectors.md) resolves every declared path against the live tree and flags the ones that resolve nowhere. This row described the capability for a long time before anything implemented it ([ENG-1353](https://linear.app/aura-health/issue/ENG-1353)) |
| Reverse dependency lookup | Grep `knowledge.reads` across all SKILL.md to see who depends on any given path — handy before editing Identity/Brand or renaming a section |

**The frontmatter is not in context at run time — read it from disk.** The Skill tool injects only the SKILL.md *body* (verified 2026-09-03: the tool result is a `Base directory for this skill:` line followed by the body; `name`, `description`, `metadata.version` and the whole `knowledge:` block are stripped). So a running skill does not know what it declared, and an access-layer skill cannot see its caller's manifest "in context" — that phrase described a mechanism that never existed, and for 147 of 204 skills the pre-fetch it implied simply never ran ([ENG-1238](https://linear.app/aura-health/issue/ENG-1238)). The manifest is still the right place for the declaration — it is machine-readable, `healthcheck-knowledge` and `check-version-bumps.py` already parse it, and the Agent Skills spec puts metadata there — but anything that *acts* on it at run time goes through **`manifest.py`** (canonical source below), which reads the SKILL.md the host actually loaded. The two rows above are the only run-time consumers; the healthcheck and reverse-dependency rows were always design-time and are unaffected.

**How the manifest reaches a running skill without a per-skill edit: the `shared` plugin's Skill hook.** [`plugins/shared/hooks/hooks.json`](plugins/shared/hooks/hooks.json) registers a `PostToolUse` hook matching the `Skill` tool; [`hooks/knowledge_context.py`](plugins/shared/hooks/knowledge_context.py) reads the loaded skill's manifest (from the `Base directory for this skill:` line the host prints, falling back to `manifest.py`'s ladder) and returns it as `additionalContext` — the declared reads with the instruction to pre-load them via `retrieve-knowledge` Recipe F, and the declared writes with the instruction to pass `caller=<plugin>:<skill>` to `record-knowledge`. Only 21 of 204 skill bodies carried that instruction themselves, so this is what makes the declaration live for the other 183. Every failure path in the hook is silent and exits 0 (a hook must never make a skill unusable; `AINATIVE_KNOWLEDGE_HOOK_DEBUG=1` explains on stderr), it skips the three access-layer knowledge skills, and it is pinned by [`tests/test_knowledge_context_hook.py`](tests/test_knowledge_context_hook.py). **Agents too:** the same script is registered on `SubagentStart`. A subagent's system prompt is its agent file's body, so its `knowledge:` frontmatter is just as invisible; the hook resolves the agent file from the host's `agent_type` (`<plugin>:<agent>` through `manifest.py`'s ladder, a bare name under `$CLAUDE_PROJECT_DIR/.claude/agents` then `~/.claude/agents`, matched by filename or frontmatter `name`) and hands the subagent its manifest before its first prompt, addressing it by file path (`agent_file=` for Recipe F, `caller=<path>` for `record-knowledge`). Agents without a `knowledge:` block — every built-in — get nothing. This also covers project-local agents in other repos (`.claude/agents/*.md`) wherever the `shared` plugin is installed. Hooks are Claude Code + Cursor only — Codex ignores `hooks/` — so a skill whose pre-fetch is essential on Codex still says so in its body. Reaches installs via the `shared` plugin version bump like any other file; `/reload-plugins` after an install, because hook paths are pinned per session.

**When to declare.** Required on every skill — no exemptions. At minimum, `reads: [Guide/Index]` (see "Baseline read" below). Add other paths the skill actually needs on its first version. Omit the `writes:` key entirely if the skill never writes; don't write empty `writes: []`.

**Baseline read: `Guide/Index`.** Every skill includes `Guide/Index` in `knowledge.reads`. The index is a one-line-per-path map of the whole AI Knowledge teamspace, regenerated by `harvest-knowledge` on every sweep. It gives any running skill a current view of what's in the teamspace, so the skill can spot relevant context it didn't pre-declare and pull it on demand via `retrieve-knowledge`. The cost is effectively zero — `retrieve-knowledge` rides a 24h local cache (see "Local cache" below), so the first call of the day fetches once and every later call across every skill reads from disk. No exemptions: wildcard skills declare it explicitly alongside `"*"`, and access-layer skills (`retrieve-knowledge`, `record-knowledge`, `create-aura-skill`, `update-aura-skill`) declare it too.

**Wildcard for cross-cutting skills.** A handful of skills are AI-Knowledge-management by design — they read or write across the whole teamspace (`ingest-knowledge`, `harvest-knowledge`, `healthcheck-knowledge`). These use `"*"` to signal unrestricted scope, alongside the baseline `Guide/Index`:

```yaml
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - "*"           # cross-cutting: reads anywhere in the teamspace
  writes: ["*"]     # writes anywhere — routes to the right section at runtime
```

Rules for using `"*"`:

- **Limited to cross-cutting skills.** If you can enumerate the paths, enumerate them. `"*"` is for skills whose job is to operate across the whole teamspace, not an escape hatch for "I haven't thought about it yet."
- **Add a rationale comment** right next to the wildcard explaining *why* the skill is cross-cutting. Example: `# wildcard: taxonomy-driven routing; target section is decided at runtime`.
- **`retrieve-knowledge` with `reads: ["*"]`** skips pre-fetching — no single content blob can represent "everything." The skill fetches on demand.
- **`record-knowledge` with `writes: ["*"]`** treats the caller as pre-authorized for any path inside `Learnings/` or `Signals/` (its normal write boundary). The boundary still applies; `"*"` doesn't grant writes to Identity/Domain/Procedures.

**Access-layer skills declare only the baseline.** `retrieve-knowledge`, `record-knowledge`, `create-aura-skill`, and `update-aura-skill` declare `reads: [Guide/Index]` and nothing more — their job is to handle whatever paths a caller passes them, so a tighter manifest would be misleading. The pre-fetch is effectively a no-op for `retrieve-knowledge` itself (it'd read the same cache it serves) but kept for symmetry, so a grep for `Guide/Index` covers every skill in the repo.

**Canonical example:** [`data:analyze-experiment`](plugins/data/skills/analyze-experiment/SKILL.md) declares a full `knowledge:` block and is wired to `retrieve-knowledge` / `record-knowledge` end-to-end. Copy its shape when retrofitting other skills.

**Local cache.** `retrieve-knowledge` consults an on-disk markdown cache under `${AI_KNOWLEDGE_CACHE_DIR:-~/.cache/ai-knowledge}` before hitting Notion. Universal **24-hour TTL**, no freshness probe — correctness comes from sibling skills deleting cache files when they write to Notion (`record-knowledge`, `ingest-knowledge`, `harvest-knowledge`, `update-experiments-top10`, `update-growth-readings-top10`, `sync-mixpanel-events`, `healthcheck-knowledge`). Force a refresh with `/refresh-knowledge` (full or by path). Full spec: [`plugins/shared/skills/retrieve-knowledge/references/cache.md`](plugins/shared/skills/retrieve-knowledge/references/cache.md).

**Placement rules.** `Guide/Placement Rules` is the runtime policy for *where* a new page or row belongs in AI Knowledge — no root-level pages, pick the most specific existing subsection, propose a new subsection rather than file at section root. Read by `ingest-knowledge`, `record-knowledge`, and `harvest-knowledge` before any write. When authoring or auditing a knowledge-writing skill, declare `Guide/Placement Rules` in `knowledge.reads` and have the skill consult it before deciding placement.

## Known Cowork constraints

Cowork sessions run skills inside an isolated Linux container. A few constraints to design around:

- **Workspace bash strips host env.** `mcp__workspace__bash` only forwards Cowork scaffolding (`HOME`, `PATH`, `CLAUDE_CODE_TMPDIR`, `CLAUDE_TMPDIR`, `LANG`, `LOGNAME`, `PWD`, `SHELL`, `SHLVL`, `USER`, `INVOCATION_ID`, `JOURNAL_STREAM`, `SYSTEMD_EXEC_PID`, `_`). **Nothing from `~/.claude/settings.json`'s `env` block reaches the sandbox** — so `AURA_API_TOKEN`, `BRAINTRUST_API_KEY`, etc. are unset inside workspace bash. Tracked in [PROD-1305](https://linear.app/aura-health/issue/PROD-1305). Until upstream supports a `cowork.workspaceEnv` allowlist (or a Workspace Secrets UI), skills that need authenticated calls to Aura-internal services from workspace bash must degrade gracefully — fall back to a public/unauthenticated path or note the miss in the readout's Sources rather than blocking.
- **Writable mounts are user-visible.** `outputs/` is the writable surface; treat it as visible to the user and never use it for secrets.

**Workaround (per-session export).** When a skill genuinely needs a secret inside Cowork bash and degrading isn't acceptable, ask the user to paste the value once and export it in-process for that session:

```bash
# inside mcp__workspace__bash, at the start of the session
export AURA_API_TOKEN='<paste from host ~/.claude/settings.json>'
```

The export persists for the lifetime of the workspace bash process, so subsequent calls in the same Cowork session pick it up. Rules:

- **Always prompt the user** — never read the value from `outputs/` or any file the user dropped in.
- **Never echo, log, or write the value to `outputs/`.** Treat it like a one-shot in-memory secret.
- **Re-prompt on session restart.** If Cowork rotates the workspace, the export is gone — ask again rather than caching.
- **Prefer the graceful-degradation path first.** Only fall back to this workaround when the unauthenticated path can't answer the question (e.g., `data:analyze-experiment` hitting a Block Kit end-experiment edge case where Slack alone is ambiguous).

## Plugin versioning — every plugin change bumps the manifest

**Every `plugins/<team>/.{claude,codex,cursor}-plugin/plugin.json` carries a `version`, and any change to anything under `plugins/<team>/` must bump it in all three flavours.** This is enforced pre-merge, not by convention alone.

The host keys the install-cache path on the manifest version: `~/.claude/plugins/cache/aura-tools/<plugin>/<version>`. `/plugin install` sees that version already installed and **skips the copy entirely**, so shipping a change under an unchanged version means every existing install silently keeps the old files. `/plugin marketplace update` doesn't help — it only advances the marketplace clone, a separate layer from the copy into `cache/`. Neither does `/reload-plugins` or restarting; both re-read the same stale files. Nothing errors, and the reload reports success.

**The enforcement is [`scripts/check-version-bumps.py`](scripts/check-version-bumps.py)**, run by the **Version bump** GitHub Action on every PR touching `plugins/**`. It checks two levels:

- **skill** — any change under `plugins/<plugin>/skills/<skill>/` must bump that skill's `metadata.version`
- **plugin** — any change under `plugins/<plugin>/` must bump `version` in all three manifests

Exit codes: `0` all bumped (or no plugin files touched), `1` something didn't bump, `2` the check itself couldn't run. Run it yourself before opening a PR:

```bash
python3 scripts/check-version-bumps.py --base origin/main
```

**Never pipe a gate through `tail` or `head` in an `&& git push` chain.** The pipeline's exit code is `tail`'s, so a failing gate still pushes. Run the gate on its own, capture `$?`, and push only when it is 0; this applies to the test suite and every other check here too. A version-gate failure reached [#1346](https://github.com/AuraHealth/AINative/pull/1346) this way.

**Why a gate and not just the authoring skills.** `create-aura-skill` / `update-aura-skill` bump automatically, but most plugin edits never go through them — bug fixes, review fixes, and doc corrections are hand edits. Auto-bump alone would cover a minority of plugin-changing commits, and every miss silently freezes installs org-wide.

**Plugin version is independent of skill versions.** Do not recompute it as max(skill versions): bumping a skill that isn't the current maximum leaves the max unchanged, so the manifest wouldn't move and the install would no-op. Max-of-skills was only how the versions were originally seeded ([ENG-966](https://linear.app/aura-health/issue/ENG-966)) — from then on the plugin version moves on its own whenever anything in the plugin changes.

**The incident behind this.** All six plugins shipped `"version": "1.0.0"` and never bumped it, so installs silently no-opped from the first install onward. A host ran `eng:investigate-memory-ingestion-guardrail` **1.2.0** for four days while the repo was on 1.3.0 — missing the `/ai/llm-calls` ledger rung entirely, so every guardrail alert fell through to the Braintrust fallback, whose traces carry an empty `metadata.user`, so identity lookups 404'd and replies claimed "no email on file". Diagnosed as a token/network fault for two days before the stale install was found. [ENG-963](https://linear.app/aura-health/issue/ENG-963) removed the key entirely as the immediate fix (the host then falls back to the commit sha, which can't go stale but is unreadable); [ENG-966](https://linear.app/aura-health/issue/ENG-966) restored readable semver and added the gate that makes the bump guaranteed rather than merely intended.

## Tests

**Tests live in `tests/` at the repo root, not inside a skill folder, and CI blocks on them.** Full rationale, coverage table and the historical-file override live in [`tests/README.md`](tests/README.md) — read that rather than restating it here.

The three rules worth carrying in your head:

- **Skill self-containment (principle 1) is a *runtime* rule and does not extend to tests.** Nothing a skill does at runtime reads its tests. The deciding factor is duplication: `responses_reader.py` exists in six skill directories, so per-folder tests would mean six copies of the suite — and a drifting copy of a *test* is worse than a drifting copy of the code, because the drift is invisible until the weaker copy is the one you trusted.
- **One suite runs against every copy.** That makes the suite a behavioural drift check the `diff -q` one-liners can't be: a copy can be byte-identical to the canonical and still be the wrong canonical. When a file listed in a "canonical source" section of [`docs/canonical-sources.md`](docs/canonical-sources.md) gains tests, parameterise over the copies rather than testing one.
- **Failure injection is the point, not a style choice.** The defects worth catching live in branches real data cannot reach — a response that will not fetch, an endpoint that paginates past its cap, a call that raises. Happy-path verification against a live user passes while those branches are broken; that is precisely how four consecutive PRs ([#733](https://github.com/AuraHealth/AINative/pull/733)–[#738](https://github.com/AuraHealth/AINative/pull/738)) shipped real defects that hand-verification signed off on. Stub the transport at its single chokepoint and make it fail on demand.

**`tests/` sits outside `plugins/`, so it is not subject to the version-bump gate** — adding or editing a test needs no skill or plugin bump. The `Tests` workflow's `paths` filter is a deliberate allowlist of what is actually covered; anything absent is ungated, which is honest rather than implying coverage that doesn't exist. Widen it as coverage grows.

Stdlib `unittest`, no pytest, no API keys, no network. Introduced in [ENG-1024](https://linear.app/aura-health/issue/ENG-1024).

## SKILL.md length is gated

**A SKILL.md stays under ~500 lines, and CI enforces it.** The budget itself is stated once, in [`create-aura-skill/references/best-practices.md`](plugins/shared/skills/create-aura-skill/references/best-practices.md) — [`scripts/check-skill-length.py`](scripts/check-skill-length.py) parses it from there rather than carrying a second copy of the number.

Run it yourself before opening a PR:

```bash
python3 scripts/check-skill-length.py --base origin/main
```

**It is a ratchet, not a threshold.** Each changed file is compared against **its own length at the base ref**, not against the budget alone: a file under budget may not cross it, a file already over may not grow (shrinking or holding is fine), and a new file must be under. That shape is deliberate — a plain "must be under 500" gate would block every PR touching an already-over file, which in practice means the gate gets switched off. An allowlist of the current offenders would rot instead: a file could sit at 594 forever and the list would quietly bless it. Comparing to base needs no list, and the allowance shrinks on its own.

**Why it needs a gate.** The budget was documented from the beginning and drifted anyway, because no single PR's handful of added lines looks unreasonable and nobody is holding the line. Five files were over when this shipped — including **both** authoring skills, the ones that tell everyone else about the rule. That is the argument in one sentence: a documented budget with no enforcement does not hold. A SKILL.md is also loaded in full on every invocation, so length is a cost paid every run, not just at review.

**What moves to `references/`**: schemas, output templates, edge-case catalogues, recipe detail, long examples, hardcoded ID tables. **What stays**: the procedure a reader follows step by step, and enough context to know when to open the reference. Leave a pointer that says what is in the file, not just a bare link.

**Extract a semantic section, never a line range** — and check the result, because a botched extraction is silent. It still parses as Markdown, still renders, and still passes the budget; it has just lost a chunk of procedure. Both failure modes below shipped in this very PR and were caught in review:

- **A cut through a code block.** An extraction that started inside a `cat > … <<'EOF'` heredoc took the closing `EOF`, the `gh pr create --draft` call and the entire Bugbot-then-ready cycle out of *both* authoring skills. The remaining file looked plausible.
- **Links that no longer resolve.** Content moved into `references/` is one directory deeper, so `](references/x.md)` now points at `references/references/x.md`, and every `](../…)` needs one more level.

[`tests/test_skill_integrity.py`](tests/test_skill_integrity.py) now checks all of this repo-wide — balanced fences, closed heredocs, closed frontmatter, no un-rewritten `references/` links. Run the suite after any extraction:

```bash
python3 -m unittest discover -s tests -q
```

Introduced in [ENG-1064](https://linear.app/aura-health/issue/ENG-1064), which also brought the five over-budget files under by extraction (content moved, never deleted).

## CLAUDE.md length is gated

**An agent-memory file stays under the harness's 150,000-character limit, and CI enforces it.** [`scripts/check-memory-length.py`](scripts/check-memory-length.py) covers `CLAUDE.md` and `AGENTS.md`, at the repo root and nested.

```bash
python3 scripts/check-memory-length.py --base origin/main
```

**Why it needs a gate rather than a convention.** Past the limit the harness stops loading the file and says so once, in the session that crosses. Nothing in the repo fails, no output changes shape, and every later session simply runs without the project memory everyone assumes is in context. The file does not break — it quietly stops being memory. Same argument as the SKILL.md budget above, with a worse failure: there, length is a cost paid every run; here, the content is silently absent.

**Three bands, with the ratchet in the middle.** Free below 135,000 characters; inside the warn band the file may hold or shrink but not grow; at 150,000 it fails. The middle band is what holds the line — a bare "must be under the limit" check passes at 149,999 and fails on the next paragraph, turning an extraction into an emergency. The PR that first crosses into the band is warned, not blocked, so the band can be entered legitimately; only a file already inside it is held. Like the SKILL.md ratchet, this needs no allowlist of today's offenders, and the allowance shrinks on its own.

**The unit is characters, not bytes or lines** — what the harness itself reports. The difference is not cosmetic on prose this arrow- and em-dash-heavy: CLAUDE.md at 154,044 characters was 155,113 bytes.

**What moves to `docs/`**: lookup tables — registries, inventories, schemas, hardcoded ID tables — anything you consult for one specific task. **What stays**: the procedure, the rules, and whatever a reader needs *before* they know to open the file. The extraction traps are the same ones the SKILL.md section lists, plus one more: relative links move one level up, and any cross-reference saying "above" or "below" about the moved text has to name the file instead. Worked example: [`docs/canonical-sources.md`](docs/canonical-sources.md) and [`docs/token-inventory.md`](docs/token-inventory.md), extracted when CLAUDE.md first crossed the limit.

### AGENTS.md is generated from this file

**`AGENTS.md` is the same project memory read by Codex, and it is not hand-maintained.** Edit `CLAUDE.md`, then:

```bash
python3 scripts/build-agents-md.py --write   # regenerate
python3 scripts/build-agents-md.py --check   # what CI asserts
```

Commit both. The **AGENTS.md sync** workflow fails a PR that edits one without the other.

**Why generated.** Both files were hand-maintained and drifted: 23 of 35 shared sections diverged, **11 sections went missing** from AGENTS.md, and it carried no unique content of its own. A second hand-maintained copy of the project memory is a parallel source of truth, which Architecture principle 3 says produces drift — it did, for months, invisibly.

**Why an explicit rule list and not `s/Claude Code/Codex/`.** That blanket rewrite had already shipped: AGENTS.md sat on `main` telling Codex to read `.Codex-plugin/marketplace.json` and `~/.claude/settings.json` spelled `~/.Codex/settings.json`, and to put worktrees in `.Codex/worktrees/`. **Twelve literals**, none of which exist, and nothing failed — the file still rendered. Same failure as *A blanket rewrite corrupts the one sentence that contrasts the two spellings* above, which is why the generator is that lesson in executable form.

**The line the rules draw:** substitute where the text means *the host you are running in* ("Claude Code loads `env` at startup"); preserve where it names Claude Code as *one host among several* — the `.claude-plugin/` catalog row, and sentences like "Hooks are Claude Code + Cursor only — Codex ignores `hooks/`", which is **true as written** for a Codex reader and false if rewritten. Paths, env vars and config keys are literals and are never substituted, whatever they are spelled.

**Every rule must match its declared count**, or the build fails naming the rule. Without that, a reworded sentence in `CLAUDE.md` would make a rule silently no-op and ship an un-retargeted `AGENTS.md` — the quiet failure the generator exists to prevent. When a rule stops matching, update it to the new wording or drop it; **never loosen it into a regex**.

## Skill versioning

Every skill carries a semantic version in its `SKILL.md` frontmatter, so a change is traceable and any output can name the exact revision that produced it. Introduced in [PROD-3630](https://linear.app/aura-health/issue/PROD-3630) (under the [PROD-3244](https://linear.app/aura-health/issue/PROD-3244) skills-and-knowledge umbrella).

**Frontmatter shape — follow the Agent Skills spec.** The [Agent Skills spec](https://agentskills.io/specification) (the open standard Claude Code follows) has **no top-level `version` field**; the required fields are `name` + `description`, and version lives inside the optional `metadata:` map (an arbitrary string key-value map) as a **quoted string**. We place the `metadata:` block **after `description` and before `knowledge`**:

```yaml
---
name: investigate-user-rating
description: …
metadata:
  version: "1.0.3"
knowledge:
  reads:
    - Guide/Index
---
```

A bare top-level `version:` is silently ignored by the plugin loader and flagged by `skills-ref validate` — always nest it under `metadata`.

**Read and write the version in the frontmatter block only — never by grepping the whole file.** Several skills *document* the frontmatter convention by example, so their SKILL.md body contains additional `metadata:` / `version: "…"` blocks that are content, not configuration. `shared:create-aura-skill` carries the scaffold it emits for a new skill, `shared:update-aura-skill` carries the seed example in its version-presence check, and this section of AGENTS.md carries one too. A whole-file `grep`/`sed`/`replace` hits whichever comes first in the file and silently edits the wrong one.

The failure is quiet both ways: you read a version that isn't the skill's, then write one into a doc example. In [#709](https://github.com/AuraHealth/AINative/pull/709) a whole-file grep reported `1.0.0` for two skills actually on `1.3.0`/`1.4.0` and rewrote `update-aura-skill`'s scaffold example instead of the real versions — `check-version-bumps.py` catches a missing bump but not a corrupted example.

Split on the frontmatter fence and operate on that slice alone:

```python
head, frontmatter, body = pathlib.Path(skill_md).read_text().split("---", 2)
current = re.search(r'version:\s*"([^"]+)"', frontmatter).group(1)
frontmatter = re.sub(r'(version:\s*")[^"]+(")', rf'\g<1>{new}\g<2>', frontmatter, count=1)
```

After any bump, assert the changed `version:` line is **inside** the frontmatter. Counting `-`/`+` pairs is not enough — a corrupted body example produces exactly one pair too, which is indistinguishable from a real bump and is precisely the failure this rule exists to prevent. Check the line numbers:

```bash
python3 - plugins/<plugin>/skills/<skill>/SKILL.md <<'PY'
import re, subprocess, sys
p = sys.argv[1]
fm_end = [i for i, l in enumerate(open(p).read().split("\n"), 1) if l.strip() == "---"][1]
diff = subprocess.run(["git","diff","-U0","origin/main","--",p], capture_output=True, text=True).stdout
ln, hits = 0, []
for l in diff.split("\n"):
    m = re.match(r"@@ -\S+ \+(\d+)", l)
    if m: ln = int(m.group(1)); continue
    if l.startswith("+") and not l.startswith("+++"):
        if re.match(r"\s*version:", l[1:]): hits.append(ln)
        ln += 1
bad = [h for h in hits if h > fm_end]
if bad: sys.exit(f"FAIL: edited a body example at line(s) {bad}, not the frontmatter (ends L{fm_end})")
if len(hits) != 1: sys.exit(f"FAIL: expected exactly 1 frontmatter version change, got {len(hits)}")
print(f"OK: one version change at L{hits[0]}, inside the frontmatter")
PY
```

**Semver rule for skills.** New skills start at `"1.0.0"`. Every change bumps the version — `update-aura-skill` proposes the level from the change type and confirms before applying:

- **patch** (`x.y.Z`) — no behaviour or trigger change: docs/wording, a typo, a comment, formatting, a bug fix that restores intended behaviour.
- **minor** (`x.Y.0`) — new backward-compatible capability: a new step, flag, mode, script, reference, or an *added* trigger phrase. Existing invocations still work.
- **major** (`X.0.0`) — breaking change: rename the skill, remove or repurpose behaviour, change the output contract or a script's CLI, or materially narrow/redefine the trigger surface.

A single edit spanning categories takes the **highest** applicable bump.

**A major bump requires the user asking for it.** Propose the level, state why, and wait — before editing any version field. Never infer a major on your own: when a change looks breaking, name what would break and for whom, then let the user decide. "A bundled script's flags changed" is not breaking when that script's only caller is updated in the same PR — the test is whether something *outside* the change can no longer invoke what it invoked before. Default to minor or patch, and when the level is genuinely arguable take the lower one and say so.

This is one-way. [`scripts/check-version-bumps.py`](scripts/check-version-bumps.py) refuses any decrease, and the `main` ruleset sets `non_fast_forward`, so a version cannot be walked back once merged — not by a follow-up PR, not by rewriting history. An over-called major is permanent, and it drags all three plugin manifests with it, because the plugin version is the install-cache path. `data:analyze-user-journal` went `1.1.4 → 2.0.0` (and plugin `data` `2.12.0 → 3.0.0`) on exactly this mistake: a script's CLI changed, its one in-repo caller was updated in the same PR, nothing outside the diff could break, and minor was correct.

**The version stamps output.** `metadata.version` is the single source of truth and becomes the `:<version>` segment of the `Tools/Slack` attribution footer (see "Slack post conventions") and of any report footer — e.g. `data:investigate-user-rating:1.0.3`. Two mechanisms:

- **Slack footers — script-built, never model-typed** (the deterministic default). Pass `--footer <plugin>:<skill>` to `slack_post.py` / `shared:post-to-slack` and the helper **auto-resolves** the `:<version>` from that skill's `metadata.version` (`build_footer` → `_resolve_skill_version` → `_skill_md_path`). `--footer-version <v>` overrides; `--footer-version ''` forces none.

  **Passing a `--footer-version` that contradicts the resolved `metadata.version` is now refused** — `build_footer` cross-checks and raises rather than posting ([ENG-1038](https://linear.app/aura-health/issue/ENG-1038)). An explicit version suppresses the resolver, so a wrong one yields a genuine script-built footer wrapped around a version that never shipped, which is harder to catch than the dropped-version case the flag exists for: ~90 posts went out stamped `1.6.0`/`1.9.0` on a skill whose all-time max was `1.3.1`. When the SKILL.md is unreachable (the rpm plugin-snapshot layout the flag exists for) there is nothing to compare against and the explicit value stands.

  The same call also auto-resolves the **runner** field — `who@surface`, e.g. `jerome@claude-desktop` (`build_footer` → `_resolve_runner`). *who* is the git identity (email local-part → `user.name` → OS account — the only session signal naming a **person**); *surface* is `CLAUDE_CODE_ENTRYPOINT`. **The git identity is read from `--global` config** so the tag is cwd-independent — an unscoped read answered differently from a scratchpad cwd (the normal case for a cadence sweep — [ENG-1039](https://linear.app/aura-health/issue/ENG-1039)). Hostname is excluded (machine-level; a random pod name under cron). **Let it auto-resolve even on unattended sweeps** — the run was armed by a person and that's the part not otherwise recoverable. `--footer-runner <tag>` overrides per call, `''` omits, `AINATIVE_FOOTER_RUNNER` overrides process-wide. Added in [ENG-993](https://linear.app/aura-health/issue/ENG-993).

Resolution reads the SKILL.md **of the copy the host actually loaded** — candidate roots come only from the running script's own path and `CLAUDE_PLUGIN_ROOT` (covering both the in-repo tree and the plugin install cache), hopping to a sibling plugin when the footer names a skill outside the running one. It never consults cwd or a hardcoded repo path: resolving against a checkout while executing from the install cache let a stale `1.2.0` guardrail run stamp itself `1.3.0` and anchor two days of misdiagnosis ([ENG-964](https://linear.app/aura-health/issue/ENG-964); staleness itself was [ENG-963](https://linear.app/aura-health/issue/ENG-963)). When the version can't be resolved the helper warns on stderr rather than emit a bare id. A skill must **not** hand-compose the footer literal — a model-typed footer silently drops the version ([PROD-3636](https://linear.app/aura-health/issue/PROD-3636)); when a body is assembled outside a post call, generate the line with `slack_post.py footer --skill <id>` and paste it verbatim.
- **Script-emitted footers** read the version from the sibling `SKILL.md` `metadata.version` at runtime, so they follow a bump automatically with no edit (reference: [`plugins/eng/skills/report-bugsnag-errors/scripts/format.py`](plugins/eng/skills/report-bugsnag-errors/scripts/format.py) `_skill_version()`).
- **HTML report artifacts** stamp the same `<plugin>:<skill>:<version>` in their rendered `<footer>` (published via the Artifact tool), so a shared report is traceable to the revision that produced it. The render script reads `metadata.version` from its sibling `SKILL.md` — same zero-drift pattern as script-emitted footers. Live in [`chart-openai-project-cost`](plugins/eng/skills/chart-openai-project-cost/scripts/render.py) and [`build-aurie-cost-report`](plugins/eng/skills/build-aurie-cost-report/scripts/render.py) (`_skill_ident()`); any new artifact-rendering skill follows suit.

`shared:create-aura-skill` scaffolds `metadata.version: "1.0.0"`; `shared:update-aura-skill` owns the bump (its Step 4b). Agents (`plugins/<plugin>/agents/*.md`) are **not** versioned yet.

## Naming conventions

Applies to skills, commands, directories, script files, branch names.

### 1. kebab-case everywhere

All names are lowercase, hyphen-separated. No underscores, no camelCase, no spaces. Applies to skill dirs, command files, script files, branch names.

✅ `fetch-coaching-credits`, `summarize-weekly-output`, `prepare-monday-deck`
❌ `fetchCoachingCredits`, `fetch_coaching_credits`, `FetchCoachingCredits`

### 2. Verb-first for action skills

Skills that **do something** (fetch, update, create, send, run) start with an imperative verb. The verb should describe the primary effect.

Common verbs in this repo: `add-`, `create-`, `fetch-`, `update-`, `setup-`, `pull-`, `push-`, `ingest-`, `harvest-`, `prepare-`, `healthcheck-`.

✅ `add-linear-issues`, `fetch-coaching-credits`, `update-intelligence-prompt`, `ingest-knowledge`
❌ `coaching-credits-fetcher` (noun-noun-noun), `linear-issues-adder`

### 3. Noun-first only for artifact/report skills

Skills that **represent a thing** rather than an action can be noun-first — reserved for cases where the artifact noun carries the meaning better than any verb would.

✅ `daily-status` (a status, not an action), `linear-task` (a task), `eng-handoff` (a handoff document)

If in doubt, prefer verb-first. Artifact-first is the exception.

### 4. Third-party / product names are kept verbatim

Use the product's canonical spelling as one token: `linear-`, `braintrust-`, `elevenlabs-`, `slack-`, `notion-`, `pr-` (GitHub PR), `ai-knowledge` (internal teamspace).

✅ `review-pr`, `fetch-coaching-credits`, `investigate-elevenlabs-conversations`
❌ `review-github-pr` (redundant), `fetch-brain-trust-topics` (wrong spelling)

### 5. Aim for 2–3 tokens, cap at 4

Shorter is better. Trim filler words (`the`, `a`, `for`) and generic suffixes (`-skill`, `-tool`, `-script`).

✅ `fetch-coaching-credits` (3)
⚠️ `setup-ad-creative-project` (4 — acceptable when each token carries meaning)
❌ `fetch-the-braintrust-topics-for-conversation-project-report` (7)

**Metric/report family carve-out (4–6 tokens is fine here).** Cost, quota, and
report skills name a point in a `(verb)-(vendor)-(grain)-(metric)` matrix — every
token disambiguates the skill from a real sibling, so trimming would create
collisions, not clarity. `analyze-openai-cost-per-subscription-state`,
`analyze-elevenlabs-cost-per-user`, `report-elevenlabs-user-cost`,
`store-aurie-user-daily-cost`, `report-top-conversation-users` are all correct as-is
— do **not** rename them to hit the 3-token target. The `aurie-web-*` marketing
family (`aurie-web-flow-snapshot`, `aurie-web-onboarding-audit`, …) is the same case:
`aurie-web` is one product-surface token (rule 4) that clusters the family, followed
by the specific artifact. The 2–3 token target is the default; these enumerated
families are the sanctioned exception, not drift to be "fixed".

### 6. Family naming for related skills

When multiple skills share a domain, use a **consistent suffix** so they cluster in alphabetical listings.

Examples already in the repo:
- Knowledge family: `ingest-knowledge`, `harvest-knowledge`, `healthcheck-knowledge`, `retrieve-knowledge`, `record-knowledge`
- Project updates family: `pull-project-updates`, `push-project-updates`

### 7. Branch names

`<firstname>/<ticket-id>-<slug>` — e.g. `jerome/eng-381-add-braintust-topic-knowledge`. Slug is kebab-case, derived from the issue title.

### 8. Commit messages

Short imperative subject line describing the change + the Linear ticket in brackets: `Add report-conversation-patterns skill to data plugin [PROD-3490]`.

### 9. Renaming a skill — keep the old name as a back-compat trigger phrase

When you rename a skill (e.g. because what it does changed), the directory + the
frontmatter `name:` move to the new name, but **keep the old name's distinctive
words as a trigger phrase in the `description`** so existing muscle memory and
any saved prompts still route to it. The skill registry is the union of every
`description` (Architecture principle 3), so a retained trigger phrase is the
only thing that preserves discoverability across a rename — there's no alias
mechanism.

✅ `investigate-braintrust-trace` → `investigate-openai-trace` kept "investigate
braintrust" as a trigger phrase ([#353](https://github.com/AuraHealth/AINative/pull/353)).
✅ `analyze-braintrust-latency` → `analyze-aura-latency` kept "analyze braintrust
latency" / "braintrust latency" ([#352](https://github.com/AuraHealth/AINative/pull/352)).

Also update every sibling SKILL.md that cross-references the old name/path, and
the `→ Drill in:` / invocation hints in any scripts — a directory rename alone
leaves dangling `../old-name/SKILL.md` links. Grep the repo for the old
hyphenated token before opening the PR. (Both renames above shipped under the
[PROD-2456](https://linear.app/aura-health/issue/PROD-2456) Braintrust migration.)

## Prose conventions

Applies to prose in SKILL.md, AGENTS.md, AI Knowledge pages, PR descriptions, and commit
bodies — not to the kebab-case file naming covered above.

### Write internal service names in full

Use **Intelligence Service** in full — never the abbreviation `IS`. The two-letter form
collides with too many other things (the English verb "is", "Information Systems", various
status fields) and reads poorly in search and code review. Same rule applies to any future
internal service that gets a short name: prefer the full canonical name in prose.

✅ `Intelligence Service is the prompt registry today.`
❌ `IS is the prompt registry today.`

External products keep their canonical short names where those *are* the canonical names
(`Braintrust`, `Linear`, `Notion`, `11Labs`, `GCP`, `PR` for GitHub pull request) — the
rule targets internal-service initialisms only. Existing SKILL.md files using `IS` get
updated opportunistically when touched for other reasons; no sweeping rewrite needed.

## Secrets and environment

API keys live in `~/.claude/settings.json` under the `env` block — the team standard.
Don't recommend shell rc, Keychain, or project `.env` for personal keys inside skills.
Codex loads `env` at startup, so changes only take effect after a restart.

**Print an environment variable's name, never its value.** The rule and its safe and forbidden commands are in the security checklist, A1 (`Compliance/Security/Code Review Checklist`). Why: an `env | grep` printed part of the `GCP_KEY` private key into a session transcript, and the key has to be rotated ([ENGBACK-2172](https://linear.app/aura-health/issue/ENGBACK-2172)).

### GCP: the `GCP_KEY` service account, never the user's gcloud login

**Anything a skill does with GCP authenticates as `GCP_KEY`, the service account; the user's own gcloud login is the fallback only when `GCP_KEY` is unset.** A user login falls under Google's reauth policy, and once it lapses every non-interactive call fails with `Reauthentication failed` — so an unattended skill degrades without saying why. A service account is not subject to reauth.

- **Client libraries** (`gcp.py`, `livekit_core.py`, `bq.py`, the Firebase admin scripts) build credentials from `GCP_KEY` first and reach application-default credentials only when it is unset.
- **The `gcloud` CLI goes through the skill's own copy of `gcloud_sa.py`** — `gcloud_sa.run([...])` from a script, `python3 ${CLAUDE_PLUGIN_ROOT}/skills/<skill>/scripts/gcloud_sa.py <args>` from a SKILL.md command. It activates the key into a private temp `CLOUDSDK_CONFIG`, so the user's gcloud config is never read or changed. A key that fails to activate fails the call, naming `GCP_KEY`; it never falls through to the user login. Canonical copy and drift command: [`docs/canonical-sources.md`](docs/canonical-sources.md).
- **Name the credential in every GCP error line** (`gcloud_sa.credential()`), so a failure says which account failed. A missing permission is fixed by granting it to the service account, not by switching back to the user login.
- **Keep the skill's degrade behaviour.** A GCP failure never blocks output that does not depend on it.

**Every call to Aura RTDB, GCP or the Aura API (`services.aurahealth.io`, `services.dev.aurahealth.io`) carries the `AuraClaude` user agent** (Aura API added by Jerome, 2026-10-26). That user agent is `AuraClaude/<who>/<plugin>:<skill>@<version> (session <id>)`, where the session is `AINATIVE_SESSION_NAME` or the first 8 characters of `CLAUDE_CODE_SESSION_ID`, and `<who>` names the person who ran it (since 2026-11-11): `AINATIVE_UA_WHO`, else the global git email's local part, git `user.name`, the OS account, or `unknown`. A login name, never an email. It never reaches Slack or the Incident Log: everything that posts or writes a row cuts it, and only local capture files keep it. **Why:** an unclaimed prod RTDB read on 2026-11-11 carried only `AuraClaude/adhoc:coach-list`, and nobody could tell who ran it ([ENG-1884](https://linear.app/aura-health/issue/ENG-1884)).
- **Scripts:** a raw HTTP call uses the skill's `user_agent()`: from `gcloud_sa.py`, or the inline copy in a script that has no `gcloud_sa.py`. [`tests/test_claude_user_agent.py`](tests/test_claude_user_agent.py) fails on a script that names an RTDB, GCP or Aura API host, or reads an Aura token, and never calls it.
- **gcloud:** `gcloud_sa.run()` labels it as `environment/AuraClaude environment-version/<who>/<plugin>:<skill>@<version>+<id>`.
- **SKILL.md curl:** pass `-A "$(python3 …/gcloud_sa.py --user-agent)"`. A skill that ships no `gcloud_sa.py` passes `-A "AuraClaude/<who>/<plugin>:<skill> (session ${AINATIVE_SESSION_NAME:-adhoc})"`, with `<who>` written as the one shell expression the doc curl lines share (`tests/test_claude_user_agent.py` pins it and checks it against `_ua_who()`); it leaves the version out, because a literal version would go stale on every bump.
- **Ad-hoc curl** to `*.googleapis.com` or the Aura API: pass `-A "AuraClaude/<who>/adhoc (session <id>)"`.
- **Prod RTDB reads are never curl or REST.** Data that lives in a service's Postgres is read with SQL on that service's database, run by its owner; RTDB is read through the Admin SDK realtime protocol, exact paths only, and Python `firebase_admin.db` is REST too. The rule and its limits: `Tools/Firebase` → "Reading prod data" in AI Knowledge (canonical). **Why:** the RTDB REST read alert counts REST gets only and cannot tell our reads from a scraper's.
- **Aura API credential:** the Aura API takes `AURA_API_TOKEN` (or a user or admin token), never `GCP_KEY`. `GCP_KEY` is only for gcloud and GCP REST calls.
- **Ad-hoc scripts** (Node `fetch`, axios, Python `requests`, including a one-off script in another repo's worktree) set the header explicitly, because their default user agent is generic: `AuraClaude/<who>/adhoc:<card> (session <id>)`.
- **The Aura CLI (Tools/CLI) sends its own label, `AuraCLI/<who>`:** a login name only (the same `<who>` ladder, from `AURA_UA_WHO`), with no version, path or email. Like `AuraClaude`, it is a label, never proof of who is calling.

**Never use shallow reads (`?shallow=true`) on the prod RTDB** (Jerome, 2026-10-24). They slow Firebase down for everybody.
- **Read the exact path you need** (`/userListened/<uid>`), and check that something exists by reading that exact path.
- **Never list a large node's keys, even with an OK.** If that data is needed, it comes from a batch job (an export or a batch builder) run by the code owner with Jerome's go (Jerome, 2026-11-04).
- **Why:** the "[Security] RTDB shallow-read load spike" alert's metric (`network/api_hits_count`, `rest_shallow_get`) carries no caller or user-agent label, so our own reads look exactly like enumeration. All three alerts on 2026-10-23 and 24 were ours: a Support session, and ENGBACK-1905's cleanup twice.
- **`/users` itself is never read:** not the whole node, no listing, no query, no shallow read; not the root either, with or without an OK (the same batch-job route applies). An exact `/users/{id}` read of one known id is fine. Print only exists or gone, or field names, never values, and send the AuraClaude user agent (Jerome, 2026-10-24).

**A local run of Firebase Functions code writes to prod.** Requiring any Functions module that uses the shared `logger.js` sends its log lines to prod Cloud Logging through `@google-cloud/logging`, because `GOOGLE_APPLICATION_CREDENTIALS` on Jerome's Mac points at a prod service account. A trigger helper called without stubbing `services/firebase` first can also reach the real Admin SDK. For a local check, run under `env -u GOOGLE_APPLICATION_CREDENTIALS`, and stub `logger` and `services/firebase` **before** requiring the module (a destructured import captures the real function at require time), or use the emulator. A check of FirebaseDenormalizer#29 wrote 3 stray error lines to prod logs this way; no database operation happened.

**The user agent is a label, never trust:** anyone can send it. A classifier calls a request internal only when it also *succeeded* with admin credentials, and it never filters out a denied request on its user agent.

**Incident:** on 2026-10-23 a Support session's shallow reads of one user's paths set off the prod "[Security] RTDB shallow-read load spike" alert. The profiler showed only `curl/8.7.1`, so attributing it took a transcript search ([PROD-7814](https://linear.app/aura-health/issue/PROD-7814)).

The only exemptions are setup steps that have to run as a person: granting the service account a role, creating it, and the per-person database proxy login. Each fenced block like that starts with a `# user-gcloud: <why>` line. [`tests/test_gcloud_gate.py`](tests/test_gcloud_gate.py) fails on any other `gcloud` call under `plugins/`, in a script or a SKILL.md command. **Incident:** on 2026-10-20 all 15 conversation-guardrail triage replies carried handoff category `unknown`. `fetch_conversation.py` had run `gcloud logging read` on a lapsed personal login, and re-running `gcloud auth login` did not reach the agent session. The same read as the service account worked at once ([ENG-1607](https://linear.app/aura-health/issue/ENG-1607)).

### Tokens are soft requirements

**Skills should attempt to run without a token set.** A missing token isn't a hard
fail — it's a degraded mode. The expected order of preference inside any skill that
talks to an external service is:

1. **Try the MCP server first** for that service. This is the lowest-friction path:
   a new contributor doesn't need to install N tokens before any skill works, and MCP
   handles auth out-of-band. Most read paths (Linear MCP, `notion-search`, Slack MCP)
   and many write paths cover the common case fine.
2. **Fall back to the direct API token** (`NOTION_TOKEN`, `LINEAR_API_KEY`,
   `BRAINTRUST_API_KEY`, …) when MCP can't answer the question — capped result sets,
   missing pagination, bulk operations, advanced filters, or capabilities MCP doesn't
   expose. A skill that *must* lead with the API token (because MCP fundamentally
   can't do the job — e.g. enumerating a 50-row Notion database past the 25-result
   `notion-search` cap, or hitting an Admin-only endpoint) should declare that in
   its SKILL.md with a one-line rationale and skip step 1.
3. **Ask the user, only when blocked.** If neither MCP nor the API token can answer
   the question, walk the user through adding the token via the `update-config` skill,
   then prompt to restart Codex. Don't ask pre-emptively at skill start — only
   when the skill has actually hit a wall it can't route around.

This means most skills don't need every token in the table below — install only what
you need for the skills you actually run. A new contributor running `data:sync-experiments`
needs `NOTION_TOKEN` because that skill is API-first by necessity (bulk DB sweep beyond
the 25-result MCP cap); they don't need `GCP_KEY` until they reach for
`eng:investigate-gcp-logs`.

**Cowork caveat.** Inside Cowork / local-agent sandboxes, `mcp__workspace__bash` doesn't
have the MCP connectors the host does — Linear MCP, notion-search MCP,
Slack MCP, GitHub Connector are all unreachable from workspace bash (see [Known Cowork
constraints](#known-cowork-constraints) below). Cowork-runnable skills will fall through
to the API-token path immediately in those sessions. That's fine — the convention is
about the *order of preference*, not about disabling the API path. The Linear MCP →
GraphQL migration in [PROD-2216](https://linear.app/aura-health/issue/PROD-2216)
preserved the script path for exactly this reason.

**API-first by necessity (today's exempt skills).** These lead with the API token because MCP can't do the job, would silently truncate, or isn't reachable in the target runtime. Add new exempt skills here with a one-line rationale.

- **Bulk Notion DB enumeration** past the `notion-search` 25-result cap: `data:sync-experiments`, `data:analyze-experiment`, `data:update-experiments-top10`, `data:update-conversations-top10`, `marketing:update-growth-readings-top10`. `shared:harvest-knowledge` (full-teamspace REST cursor sweep; `shared:healthcheck-knowledge` is otherwise MCP-first). `eng:harvest-cost-changes` and `eng:sync-feedback-config` also write DB rows / block-level edits that `notion-search` can't. `shared:sync-security-checklist` renders the two canonical rule pages block by block over REST, because its line edits must match the repo copies' Markdown exactly and `notion-fetch` returns its own.
- **Bulk Linear sweeps** MCP truncates: `shared:daily-status`, `eng:summarize-weekly-output` (GraphQL `pageInfo.endCursor`). `shared:add-linear-issues` — Linear MCP unreachable in Cowork ([PROD-1305](https://linear.app/aura-health/issue/PROD-1305)); bundled `linear_api.py` is the one host+Cowork path ([PROD-2216](https://linear.app/aura-health/issue/PROD-2216)).
- **OpenAI Admin-scoped `/v1/organization/*`** (no MCP): `eng:analyze-openai-costs`, `eng:analyze-openai-cost-per-user`, `eng:analyze-openai-cost-per-subscription-state`, `eng:chart-openai-project-cost`. `eng:report-prompt-testing-cost` — daily **derived** cost of the `Claude`-named service-account keys (the Costs API reports per project, never per key, so per-`(model, service_tier, dimension)` rates are back-calculated from org `/costs` ÷ `/usage/completions`). Two silent-failure properties, both pinned by [`tests/test_prompt_testing_cost.py`](tests/test_prompt_testing_cost.py): tier must be normalised across the two endpoints (`flex-tier` vs `flex |`, else real traffic bills $0.00), and cached input priced separately from uncached (~10× cheaper). It exists because Aura eval traffic ran inside *product* projects and got spread across real users (~$837 moved per-user cost from ~$0.06–0.09 to $0.318 on 2026-08-21, ~85% of it `eng:qa-conversation`); both QA skills moved to `Evaluations [Production]` ([ENG-1171](https://linear.app/aura-health/issue/ENG-1171)). Aggregate key-level $ only, never a UID.
- **No vendor MCP:** `eng:lookup-ip-location` (ipgeolocation.io; optional key, blank fields with `no_key` without it); `eng:update-postman-collection` (Postman REST: collection edits need `POSTMAN_API_KEY`, per its token-inventory row; without it the entry is printed to add by hand); `eng:check-postman-drift` (one Postman REST GET of the whole collection, compared in a script with every service's routes; without `POSTMAN_API_KEY` it stops with a follow-up); `eng:block-ip`, `eng:review-ip-blocks` (Cloud Armor through `gcloud_sa`, Notion Blocklist REST); `eng:monitor-cloud-armor` (read-only Cloud Armor logs and policy through `gcloud_sa`); `eng:monitor-memory-lookups` (daily share of coach calls that ran a memory lookup: Cloud Logging through `gcloud_sa` over the BigQuery `llm.llm_call` ledger, counts only); `eng:check-one-auth` (one person's Auth accounts via Identity Toolkit, exact RTDB child paths and UserService `GET /users/{id}`, read-only, masked); `eng:fix-one-auth` (the daily ONE auth check over a full Auth export and the fixes by shape: Identity Toolkit `accounts:update` keeping `validSince`, Identity Toolkit logs through `gcloud_sa`, userDB through UserService; writes only on Jerome's go inside an IAM window, counts and prefixes only); `eng:investigate-bugsnag-errors` (Bugsnag); `eng:investigate-circleci`, `eng:rerun-circleci` (CircleCI); `eng:investigate-gcp-logs`, `eng:analyze-gcp-logs` (GCP Logging); `eng:analyze-elevenlabs-*`, `eng:report-elevenlabs-costs` (11Labs); `eng:update-intelligence-prompt`, `eng:investigate-braintrust-trace`, `data:fetch-conversation-topics`, `data:analyze-user-conversations`, `data:analyze-user-journal`, `data:save-to-braintrust-dataset`, `eng:scan-stray-language` (Braintrust). `eng:investigate-voice-issue` correlates Bugsnag + 11Labs + GCP, each leg degrading independently; its reply-watchdog verdict stays `unverifiable` without the 11Labs leg by design ([PROD-4395](https://linear.app/aura-health/issue/PROD-4395)). `build-bug-metrics-dashboard` (now in [AuraHealth/OKRs](https://github.com/AuraHealth/OKRs), kept as precedent) is half API-first — Linear MCP-only, Bugsnag via script; a missing `BUGSNAG_TOKEN` drops one section, no Linear MCP stops the run.
- **Aura backend / Mixpanel bulk-export (no usable MCP):** `data:sync-mixpanel-events`, `data:report-aurie-usage`, `eng:analyze-listens`, `eng:report-top-aura-*`, `eng:analyze-aura-cost*`, `eng:analyze-aura-latency` (`/ai/dialogue/metrics`), `qa:chat-with-aurie` (user JWT). `data:profile-user` / `data:analyze-user-behavior` use the `MIXPANEL_SERVICE_ACCOUNT_*` API fallback that survives Cowork ([ENG-607](https://linear.app/aura-health/issue/ENG-607)); the latter also cross-references the Aura `/users` + `/subscriptions` API. `data:report-aurie-conversations` indexes activity from the IntelligenceService `llmCall` ledger (BigQuery `aura-main.llm.llm_call` since [ENG-1657](https://linear.app/aura-health/issue/ENG-1657)) — the only enumerable index for Responses-API traffic — and hard-fails without it rather than under-counting via the completions walk ([ENG-1113](https://linear.app/aura-health/issue/ENG-1113)). `data:run-feedback-analysis-cadence` composes Slack + OpenAI completions + Aura API in an unattended loop. `eng:investigate-conversation-guardrail` reads a whole conversation from the Intelligence Service stored-transcript export (`POST /ai/conversations/export/:userId`, privileged) joined to the `llmCall` ledger, with `gcloud logging read` for handoff categories and OpenAI Responses as the fallback — no MCP covers any of the three ([PROD-7238](https://linear.app/aura-health/issue/PROD-7238)). `data:analyze-conversation-guardrails` runs the same reader in bulk across a window of alerts ([ENG-1568](https://linear.app/aura-health/issue/ENG-1568)). `eng:map-vendor-data-flows` measures each event's reach with one aggregate Mixpanel JQL query per window, counts only ([ENG-1919](https://linear.app/aura-health/issue/ENG-1919)). `eng:create-mixpanel-alert` saves alert reports and a board through the Mixpanel app API with the service account: the MCP cannot save reports, and the service account cannot create alerts, so a person adds each alert ([PROD-4803](https://linear.app/aura-health/issue/PROD-4803)).
- **BigQuery cost family** (bundled `bq.py` + `GCP_KEY`; no BigQuery MCP). All read/write `aura-main.aurie_cost`; **aggregate-only, never emit a UID** unless noted. `eng:query-bigquery` runs arbitrary SQL and ships `bq.py`. `eng:report-openai-cache` sums the `llm.llm_call` ledger per UTC day into a prompt-cache cost table (rates passed in from `Tools/OpenAI`, never stored). `eng:store-aurie-user-daily-cost` writes the three daily stores and **hard-requires BigQuery** (source of truth; CSVs rebuildable via `bq_pull.py`). The dimension backfills maintain per-user tables via the Aura API + BQ append, append-only, UID+timestamps only, no PII: `eng:backfill-aurie-user-ai-created` (`aiCreatedAt`; a permanent non-zero unresolved residue is normal — [ENG-1119](https://linear.app/aura-health/issue/ENG-1119)), `eng:backfill-aurie-user-channel`, and `eng:backfill-aurie-user-env`. **`eng:backfill-aurie-user-env` owns the shared `aurie_per_user_daily_cost_prod` view** (env='prod' minus `UNUSABLE_ATTRIBUTION_DATES` — [ENG-1120](https://linear.app/aura-health/issue/ENG-1120); its SKILL.md "Excluded days" is canonical), which the whole `chart-aurie-*` per-user-cost family reads instead of the base table (PROD-3589 Part B): `chart-aurie-cohort-cost`, `-cohort-payback`, `-tenure-cost`, `-cost-concentration`, `-cost-mix`, `-free-user-cost`, `-cost-by-channel`, `-cost-curve`, `-retention`, `-cumulative-cost`, `-lifetime-cost`, `-daily-margin`, `-revenue`, `-revenue-by-tier`, plus `eng:build-aurie-user-subscription` (revenue dimension). `eng:build-aurie-cost-report` composes the family into one self-contained HTML report (stdlib `render.py`, published via Artifact). **Exceptions:** `eng:chart-aurie-daily-cost` and `eng:report-livekit-daily-cost` read the **base** table, not the `_prod` view, so the headline reconciles to the authoritative vendor bill; `eng:report-aurie-margin-users` is **INTERNAL, UID-bearing** (surfaces Firebase UIDs to ops — never Artifact/Slack without an explicit ask); `eng:report-livekit-user-cost` is a single-UID read of the base table (the LiveKit leg of `eng:report-user-costs`), internal for the same reason, as is `eng:report-elevenlabs-user-cost`'s store half (its days past ElevenLabs' 10-day retention).
- **claude.ai subscription usage** (cookie-authenticated CSV exports; no API exists for Teams): `eng:harvest-anthropic-usage` writes `aura-main.anthropic_usage`, `eng:report-anthropic-usage` reads it. The **token columns cover all usage including inside seat allowances; the dollar columns are overage-only** — ranking people by spend ranks who overflowed a personal cap, not who uses Claude. Report output is keyed by work email and is **internal** (seat-allocation tool); aggregate rows are safe to share, per-person rows are not.
- **Script-side Slack reads:** `eng:release-version`'s `verify` reads spinbot's posts in #deployment with `SLACK_BOT_TOKEN` to confirm the Spinnaker rollout — a script cannot call the Slack MCP, and the verdict must come from code, not from reading messages by eye. Without the token it reports `unknown` and takes a `shared:read-slack` read back through `--deployment-log`, classified by the same parser ([ENG-1465](https://linear.app/aura-health/issue/ENG-1465)).
- **Follow-ups:** `shared:raise-followup` posts one message per dedupe key as the bot, finds it again by `conversations.history` to reply in its thread when the text changes, and files deduped Linear cards; the Slack MCP cannot read back a bot post by key.
- **Slack as a bot, across the workspace:** `shared:reply-to-aura-ai-mentions` needs `search.messages` (user token) to find Aura AI mentions and the bot token to post *as* Aura AI. The Slack MCP can do neither as the bot. Without `SLACK_USER_TOKEN`, discovery degrades to an MCP search fed in via `--threads`.
- **Direct provider calls under test:** `eng:qa-prompt`, `eng:qa-apple-intelligence-prompt` (OpenAI / Anthropic / Gemini). `eng:compare-prompt-versions` also writes Braintrust experiments, which no MCP can create.

**GitHub — `gh` CLI is the standard access path, not a per-skill exemption.**
GitHub-touching skills (`eng:review-pr`, `eng:bugbot-review`, `eng:rerun-circleci`,
`eng:sweep-bugsnag`, `eng:sweep-ended-experiments`, `eng:update-changelog`'s PR-body
lookup, `eng:linear-task`) shell out to the `gh` CLI, which handles its own auth
(`gh auth login` / `gh auth token`). This is deliberate and repo-wide: `gh`-first is the
norm for GitHub, so these skills don't each need an MCP-first rationale the way a
Notion/Linear/Slack skill would. The GitHub Connector/MCP is the fallback for the one
place `gh` isn't available — Cowork workspace bash. So "no MCP-first framing" in a
GitHub skill is expected, not a violation.

**GitHub credentials — authenticate with `gh auth login`, and leave `GITHUB_TOKEN` unset.**
`gh` gives the `GITHUB_TOKEN` environment variable precedence over its own keyring
credential, so whatever sits in the env *shadows* the credential that actually works.
A fine-grained PAT (`github_pat_…`) in that slot cannot read Checks — fine-grained tokens
need an explicit `Checks` repository permission, which was not grantable in our org setup
(attempted 2026-07-27) — while `git push`, PR reads, and `gh api user` keep working. The
failure is therefore *selective*, which is what makes it easy to misdiagnose:
`gh run list`, `gh pr checks`, `gh pr view --json statusCheckRollup`, and
`gh api …/check-runs` all return `403 Resource not accessible by personal access token`.

Three rules that follow, and they matter more than the config itself:

- **A 403 on `check-runs` is a credential fault, not a CI verdict.** It does not mean CI
  is failing, pending, or unknowable. Never report a PR as unverifiable or unmergeable on
  that basis — the usual reaction is to fall back to a weaker signal, which is exactly how
  this kept recurring instead of getting fixed.
- **A capable credential may already be present but shadowed.** Run `gh auth status` before
  concluding the account lacks access; when this was diagnosed, two perfectly capable
  classic tokens were sitting unused behind the env var. One-call escape hatch:
  `env -u GITHUB_TOKEN -u GH_TOKEN gh api …`.
- **Two merge-readiness verdicts need no Checks permission at all.**
  `gh api repos/<owner>/<repo>/commits/<sha>/status` returns legacy commit statuses —
  usable, but a repo whose CI reports *only* Checks shows `state=pending` with zero
  contexts, which must **not** be read as passing. `gh pr view --json mergeable,mergeStateStatus`
  is the sturdier one: `CLEAN` means mergeable with checks passing; `BLOCKED` / `UNSTABLE` /
  `DIRTY` mean stop.

Recorded in [ENG-971](https://linear.app/aura-health/issue/ENG-971), which also removed
`GITHUB_TOKEN` from the settings template — the variable is intentionally absent from the
token inventory in [`docs/token-inventory.md`](docs/token-inventory.md).

This list is the **source of truth** — keep it in sync when adding new API-first
skills. The convention decision is recorded in
[PROD-2234](https://linear.app/aura-health/issue/PROD-2234) and executed in
[PROD-2236](https://linear.app/aura-health/issue/PROD-2236).

### Token inventory

**The full table lives in [`docs/token-inventory.md`](docs/token-inventory.md)** — one row per environment variable: which skills use it, whether it is self-serve or team-issued, whether an MCP fallback exists, and how to obtain it. Consult it when a skill needs a credential you don't have, or when adding a new token dependency.

### Adding a new token to the inventory

When a new skill introduces a token dependency:

1. Add the variable to the table in [`docs/token-inventory.md`](docs/token-inventory.md) with all four columns filled in.
2. Update [`plugins/shared/skills/setup-ainative/SKILL.md`](plugins/shared/skills/setup-ainative/SKILL.md)
   so the contributor onboarding flow inventories and walks through the new key.
3. Document the soft-requirement behaviour in the new skill itself: how it degrades
   when the token is missing (which MCP fallback, what gets capped, when to ask).

## Notion REST integration

Per the [MCP-first convention](#tokens-are-soft-requirements), Notion-touching skills
try MCP first by default — fine for single-page lookups via `notion-fetch` or small
semantic searches via `notion-search`. They fall through to the **Notion REST API**
(via `NOTION_TOKEN`) when MCP can't do the job — which, in practice, is most database
work in AI Knowledge:

- `notion-search` (the MCP equivalent) is semantic, capped at 25 results, and has no cursor — it cannot enumerate a database.
- REST `data_sources/<id>/query` accepts property filters and paginates cleanly. A 51-row Status-blank backfill on `Learnings/Experiments` finishes in ~1 minute via REST; the same task via MCP would take 30+ minutes (or just isn't possible at scale).

**Skills that must lead with REST** (bulk DB sweeps, full-teamspace audits, property
filtering) declare that up front in their SKILL.md and skip the MCP attempt — see the
"API-first by necessity" list above. Skills doing point reads or small writes should
try the MCP path first and degrade to REST only when MCP returns truncated or
unsatisfactory results.

### Setup (one-time)

1. **Create the integration.** Go to <https://www.notion.so/profile/integrations> → New integration. Name `Aura AI Knowledge`, workspace `aurahealth`, type Internal. Capabilities: Read content, Update content, Insert content. Copy the **Internal Integration Secret** (`ntn_…`) — shown once.
2. **Connect to the teamspace** (the part everyone misses). Open the AI Knowledge teamspace in Notion → top-right `···` → **Connections** → **Connect to** → search `Aura AI Knowledge` → confirm. Without this, REST calls 404 with "Make sure the relevant pages and databases are shared with your integration."
3. **Store the token.** Add to `~/.claude/settings.json` under the `env` block as `NOTION_TOKEN`. Restart Codex.

### Using it from a skill

**Convention: each Notion-touching skill ships its own copy of `notion_rest.py` in its `scripts/` dir.** Skills are self-contained — no skill reaches into another skill's directory. The trade-off is intentional: a small amount of code duplication buys full skill portability (drop a skill folder anywhere and it just works).

```python
# from a skill's scripts/<file>.py
import os, sys
sys.path.insert(0, f"{os.environ['CLAUDE_PLUGIN_ROOT']}/skills/<this-skill-name>/scripts")
from notion_rest import notion, MCPFallback, title_text, select_value

if notion.available:
    rows = list(notion.query_data_source(
        ds_id,
        filter={"property": "Status", "select": {"is_empty": True}},
        page_size=100,
    ))
else:
    raise MCPFallback("use the MCP path documented in this SKILL.md")
```

The helper hides cursor handling, retries on 429, and exposes small property accessors (`title_text`, `select_value`, `date_start`, `rich_text_value`) plus payload builders (`make_title`, `make_rich_text`, `make_select`, `make_date`). All four builders return a wrapped property-value object — pass them directly as values in a `properties` dict.

**Retry policy.** `_request` retries up to 8 times on `429 Too Many Requests`, honouring the full `Retry-After` header and falling back to exponential backoff (`2^attempt` seconds) when the header is absent. The budget is sized for **full-DB sweeps and teamspace audits** — `data_source.query` paginating through thousands of rows, or `healthcheck-knowledge` crawling every page — without callers re-wrapping with their own retry loop. If you see persistent 429s with this budget, the issue is upstream throughput (collide with another Claude session sharing the integration secret), not the helper.

**Page/block CRUD** (added in [PROD-2207](https://linear.app/aura-health/issue/PROD-2207) so the knowledge skills could create pages over REST instead of falling back to MCP `notion-create-pages`): `notion.create_page(parent_page_id, title, blocks)` for a normal child page, `notion.create_database_row(data_source_id, properties, blocks)` for a Learnings/* row, `get_page` / `get_block_children(..., recursive=True)` for reads, and `update_block` / `append_block_children` for surgical section edits. Page bodies are assembled with the `block_*` builders (`block_heading`, `block_paragraph`, `block_bullet`, `block_numbered`, `block_code`, `block_callout`, `block_table`, `block_divider`) and a `rich_text()` parser that turns `**bold**`, `[label](url)` and `` `code` `` into rich_text — so skills write readable strings instead of hand-building block JSON.

**`rich_text()` recurses, and that is the point.** Bold and link parse their own content and fold their attribute into whatever comes back, so nesting works both ways: `**[label](url)**` is a bold link, `[`code`](url)` is a linked code span. Code spans are leaves — backticks are literal in Markdown, so `**` inside one stays as characters. A flat single-pass parser instead emits the inner markdown as **literal text**, and that failure is silent in the direction that matters: the page is created, the API returns 200, and the string still reads correctly to whoever wrote it — only a reader notices the link is gone. Both defects shipped and survived 18 copies ([ENG-1076](https://linear.app/aura-health/issue/ENG-1076)): backticks rendered raw on `Guide/Index` (regenerated by `harvest-knowledge` through this parser), and `**[label](url)**` produced bold text containing a literal `[label](https://…)`. Behaviour is covered by [`tests/test_notion_rich_text.py`](tests/test_notion_rich_text.py), parameterised over every copy, whose load-bearing assertion is the negative one — no run's text may still contain raw markdown. Per-construct tests pass against the broken parser.

**Title-by-ID gotcha.** Under Notion API `2025-09-03`, when creating a page whose parent is a `data_source_id`, the title property must be keyed by its property ID `"title"` (lowercase), not by its display name. `update_page_props` calls accept the display name. The mismatch surfaces as a generic `body.properties.<X>.id should be defined` 400 — easy to mis-attribute. Use `make_title()` and key it as `"title"` on creates.

**Pages cannot be created at, or moved to, the teamspace root over REST — and the move fails silently.** `POST /pages` with `parent: {"type": "workspace", "workspace": true}` returns **400**; that one is loud. The dangerous one is `PATCH /pages/{id}` with a new `parent`, which returns **200 with the page's unchanged parent in the response body** and moves nothing — verified against both `2025-09-03` and `2022-06-28` with a probe page, so it is not a version or capability issue. A re-parenting script written against it reports every move as a success while the tree is untouched. There is no block-move endpoint either. So any restructure of the top-level taxonomy — promoting a subtree to a section, re-homing a page — is a **human drag in the Notion UI**; the API half is limited to creating children under an existing parent, editing bodies, and re-reading the result. Plan restructure work as *"you move it, I rewire everything that points at it"*, and verify a move by re-fetching `parent` rather than by checking a status code. Found while executing [PROD-6205](https://linear.app/aura-health/issue/PROD-6205).

**Two more write traps from the same session, both silent.** Neither raises, and both leave a page that renders correctly to whoever wrote it.

- **A read payload is not a writable payload.** Notion serialises absent optionals as `null` on read and **rejects** `null` on write (`body.children[3].paragraph.icon should be an object or ``undefined``, instead was ``null```). So a block fetched and handed straight back — the obvious way to copy or recreate one — fails on whichever optional happened to be absent. Strip nulls before recreating a block.
- **Editing a block by re-rendering its plain text destroys formatting.** Doing this to the Guide page silently dropped a page link and a bold lead-in; the block still parsed, still rendered, and still read correctly, so nothing surfaced it. Edit the `rich_text` runs in place — never round-trip through plain text. This is the same failure the `rich_text()` recursion bug had above, arriving from the other direction.

**A path is a concept, not a string — grep every spelling before declaring a sweep complete.** Size a path migration from a grep over the whole tree, never from recall: the PROD-6205 scoping pass estimated 5 references in one plugin and the real number was 33 SKILL.md files across all five, which a single `grep -rl "Identity/Apps" --include='SKILL.md' plugins/` would have returned in under a second. And the slash form is not the only spelling — the knowledge skills also write the breadcrumb form `Identity › Apps`, which that grep does not match: `ingest-knowledge` was left classifying content to the retired subtree and would have **recreated it** on the next ingest, and `harvest-knowledge` still named it as an index to descend. Grep the slash form, the `›` form, and prose.

```bash
grep -rn 'Identity/Apps\|Identity › Apps\|Identity Apps' plugins/ --include='*.md' --include='*.py'
```

**A blanket rewrite corrupts the one sentence that contrasts the two spellings.** The sweep that repoints `Old` → `New` also hits prose *about* the rename — `` `A` is really `B` ``, a naming-convention table, a changelog line — and turns it into a tautology that still reads like English. It happened on [#1056](https://github.com/AuraHealth/AINative/pull/1056): a `Tools/11Labs` → `Tools/ElevenLabs` sweep rewrote ```Tools/ElevenLabs` is really `Tools/11Labs``` into ```Tools/ElevenLabs` is really `Tools/ElevenLabs``` — inside the detector-#14 paragraph explaining why it declines to guess a successor. Nothing failed; the gate was green and Bugbot found nothing, because the output is well-formed. **After a path sweep, grep the NEW spelling for sentences that now say the same thing twice** (`grep -n '`X` is really `X`'`, `X → X`, `X, formerly X`), and exclude prose that documents the rename from the replace in the first place.

**Settle the whole target shape — including which existing pages get re-homed into it — before repointing declared paths.** The repoint is the expensive half, and a re-home decided after it is green costs the sweep twice. Two pages needed a second move on PROD-6205 for exactly this reason (`Coach Upload Policy` → `Products/Aura/Coaches`, `Aurie Web Signup Flow — Static Snapshot` → `Products/Aurie/Features/Onboarding`), and the snapshot is a declared **write** as well as a read, so a stale spelling would have missed the `--refresh-snapshot` destination too.

**The standing check is [`healthcheck-knowledge` detector #14](plugins/shared/skills/healthcheck-knowledge/references/detectors.md), not this prose.** It resolves every declared `knowledge.reads` / `knowledge.writes` path against the live tree and reports the dead ones. Prose above is what to do once it fires; the detector is what makes it fire. These rules are recorded in [ENG-1351](https://linear.app/aura-health/issue/ENG-1351) and the detector in [ENG-1353](https://linear.app/aura-health/issue/ENG-1353).


**Positioned-insert (`after`) gotcha — don't chain on `results[-1]`.** The helper's `append_block_children` always appends at the *end* of the parent's children (no `after` param). To insert blocks at a specific position you call the REST endpoint directly — `notion._request("PATCH", f"/blocks/{parent}/children", data=json.dumps({"children": [...], "after": <block_id>}))`. The trap: the response `results` is **not** just the newly-created blocks — `results[-1]` is the parent's tail block, not your last inserted block. So if you chain multiple ordered batches by anchoring each insert on the previous call's `results[-1]`, every batch after the first lands at the **end** of the parent instead of in sequence. Fix: anchor each positioned insert on a **known, pre-fetched block id** (re-fetch `get_block_children` between inserts if needed), or build one single batch (≤100 top-level blocks) and insert it once. Observed 2026-06-05 consolidating two AI Knowledge pages — a chained second batch silently landed after the `Sources` section and had to be moved + the stray copy deleted.

### The canonical copy and keeping copies in sync

Treat [`plugins/shared/skills/retrieve-knowledge/scripts/notion_rest.py`](plugins/shared/skills/retrieve-knowledge/scripts/notion_rest.py) as the **canonical source**. When fixing a bug or adding a method, edit the canonical copy first, then propagate to every other consumer skill that ships a copy. To find them:

```bash
find plugins -name notion_rest.py
```

Today's consumers (carrying copies):

**37 copies today.** The `find` command below is the source of truth for membership — this list is a convenience and has drifted before (it sat at 15 while the real count was 18). If they disagree, `find` is right.

- `plugins/shared/skills/retrieve-knowledge/scripts/notion_rest.py` — **canonical**
- `data` — `backfill-learnings-conversations`, `enrich-cancellation-feedback`, `harvest-cancellation-feedback`, `report-conversation-patterns`, `sync-experiments`, `update-conversations-top10`
- `eng` — `archive-ended-experiments`, `audit-intelligence-prompts`, `harvest-cost-changes`, `sync-feedback-config`
- `marketing` — `aurie-web-experiment-history`, `summarize-youtube-to-notion`
- `product` — `build-idea`, `design-idea`, `digest-conversation-signals`, `draft-prompt-candidate`, `evaluate-prompt-candidate`, `harvest-idea-decisions`, `harvest-prompt-decisions`, `learn-idea`, `learn-prompt-candidate`, `manage-ideas`, `manage-prompt-candidates`, `record-ideas`, `record-prompt-candidates`, `share-ideas`, `share-prompt-candidate`, `ship-idea`, `ship-prompt-candidate`
- `shared` — `harvest-knowledge`, `healthcheck-knowledge`, `ingest-knowledge`, `record-knowledge`, `search-knowledge`, `sync-security-checklist`

To verify all copies match the canonical:

```bash
canonical=plugins/shared/skills/retrieve-knowledge/scripts/notion_rest.py
find plugins -name notion_rest.py ! -path "$canonical" -exec diff -q "$canonical" {} \;
```

Any output from that command is a drift warning and should be reconciled in the next PR. Future cleanup option: a small `scripts/sync-notion-rest.sh` to copy the canonical into every consumer in one command — worth adding when there are 5+ copies.

### `qa_notion.py` — the QA databases

The `qa` plugin's two regression-suite skills share `qa_notion.py`, which reads and writes
the Aurie Test Cases and Test Runs databases in `QA/`. It is a separate utility from
`notion_rest.py`: it hardcodes the two database ids and their property schema, so it is
only useful to those skills.

Canonical: [`plugins/qa/skills/write-test-cases/scripts/qa_notion.py`](plugins/qa/skills/write-test-cases/scripts/qa_notion.py).

```bash
canonical=plugins/qa/skills/write-test-cases/scripts/qa_notion.py
find plugins -name qa_notion.py ! -path "$canonical" -exec diff -q "$canonical" {} \;
```

### Cowork caveat

`mcp__workspace__bash` strips host env (PROD-1305), so `NOTION_TOKEN` won't reach Cowork sandboxes. Skills must keep the MCP fallback for that case. Same workaround as `AURA_API_TOKEN` — paste-export per session if the unauthenticated MCP path can't answer.

### Audit + rotation

- All writes via the integration are authored as `Aura AI Knowledge` in Notion's history — easy to distinguish automated edits from human ones.
- If the token leaks (commit, screenshot), rotate instantly from the integration page in Notion. New token, old token immediately revoked.

Tracked under [PROD-1336](https://linear.app/aura-health/issue/PROD-1336) (sub-issue of [PROD-1162](https://linear.app/aura-health/issue/PROD-1162)).

## Mixpanel profile writes carry `ip=0`

**Any raw write to Mixpanel's ingestion endpoint `https://api.mixpanel.com/engage` sends `ip=0` in the query string (or `$ip: 0` in each record), and CI enforces it.** The endpoint's default is `ip=1`: Mixpanel re-derives the profile's `$city` / `$region` / `$country_code` / `$timezone` from the IP of whoever sent the request — a laptop's home ISP, a pod's cluster egress — and profile geo is last-write-wins with no timestamp. The *query* API (`mixpanel.com/api/query/engage`, `/api/2.0/engage`) is a read and is out of scope.

The gate is [`tests/test_mixpanel_engage_ip0.py`](tests/test_mixpanel_engage_ip0.py): every occurrence of the ingestion URL under `plugins/` must sit in a file that also carries `ip=0` or `$ip`, and the detector is checked against a fixture in the shape of the incident below.

**Prefer the service path over a hand-rolled script.** `mixpanel-node` hardcodes `ip=0` on every request (since 0.13.0) and the backend wrappers pass `$ignore_time`, so the services and Cloud Functions are safe by construction — verified by probe, including for reserved properties like `$iterable_user_id`. When a bulk write "doesn't land" on read-back, suspect the read first: ID-merged profiles are canonical on `$device:` ids, and engage queries by Firebase UID lag or miss.

**Incident:** the [PROD-5908](https://linear.app/aura-health/issue/PROD-5908) `$iterable_user_id` backfill was re-run on 2026-09-11 as a raw `POST /engage` (2,063,384 writes) from a laptop whose ISP geolocates to West Vancouver, BC, after the `mixpanel-node` job path was misread as not applying. Every touched profile was re-stamped Canadian — 20.6% of all profiles, 88% of everything labelled Canada — and, viewed by signup cohort, it looked like a years-old all-platform drift that "fixed itself" the day the input list was cut. Root cause, evidence and the restore: [PROD-6322](https://linear.app/aura-health/issue/PROD-6322).

## Slack post conventions

Every skill that posts to Slack ends its message with a one-line italic footer attributing
the post to the source skill. This makes it possible to trace any Slack message back to
the skill that produced it without grepping git blame.

The full spec — required vs optional fields, formatting rules, worked examples — lives on
the **`Tools/Slack` page in AI Knowledge → Post format**. Every Slack-writing skill
declares `Tools/Slack` in its `knowledge.reads` so the spec is pre-fetched via
`retrieve-knowledge` before the skill runs; the skill body shouldn't restate the format.

The footer shape, for quick reference:

```
_Generated by `<plugin>:<skill-name>:<version>` · <YYYY-MM-DD> · <runner> · <env-or-source>_
```

- **Required:** fully qualified skill identifier + `:<version>` (backticked, `<version>` is
  the running skill's `version:` frontmatter — see "Skill versioning" below) + run date (`YYYY-MM-DD`).
- **Runner** (`who@surface`, e.g. `jcurlier@claude-desktop`): who ran it and from where —
  script-resolved, never model-typed, same as the version. See "The version stamps output"
  below for the resolution ladder and the cron override.
- **Optional:** env tag (`prod` / `dev` / `cowork`) and/or a trailing source link (Linear /
  Braintrust / GCP) inside the same italic line. Don't put a person here — that's the runner's job.
- Italic, last line of the message, one blank line of separation from the body. Slack
  mrkdwn — `_italic_` / `*bold*` / backticks.

Working reference implementation: [`eng:analyze-openai-costs`](plugins/eng/skills/analyze-openai-costs/SKILL.md) Step 5 template. Convention introduced in [PROD-2473](https://linear.app/aura-health/issue/PROD-2473).

## PR workflow

**Never commit directly to `main`.** Every change — even a one-line skill tweak or doc fix — goes through a branch + PR. Local edits sitting on `main` are easy to lose (a `git pull` rebase, a fresh clone, an accidental `git restore`) and there's no review trail. As soon as you start editing a file in this repo, branch first.

**Default to a git worktree, not a branch in the shared checkout.** This repo sees heavy concurrent agent activity (dozens of `claude/*` branches/worktrees at any time). When multiple sessions share one working tree, a parallel `git checkout` can flip `HEAD` between your `checkout -b` and your `commit`, silently landing your commit on `main`. A worktree gives each session its own working directory + `HEAD` + index (sharing the one `.git` object store), so checkouts can't stomp each other. Use one unless the user explicitly says otherwise:

```bash
git worktree add .claude/worktrees/<ticket-id>-<slug> -b <firstname>/<ticket-id>-<slug> origin/main
cd .claude/worktrees/<ticket-id>-<slug>   # isolated — do all edits / commit / push / PR here
# … work …
git worktree remove .claude/worktrees/<ticket-id>-<slug>   # after the PR merges
```

**Worktrees live *inside* the repo at `<repo>/.claude/worktrees/<slug>`, never as a sibling of it.** A sibling like `../AINative-<slug>` — or `../IntelligenceService-engprom-57` for an external repo — clutters the parent directory (`~/Development/Aura/Services/` fills with half-finished checkouts) and isn't where any of our tooling looks. `.claude/worktrees/` is gitignored in every Aura repo, is exactly where the harness `EnterWorktree` tool and `eng:clean-up-worktrees` already put and find worktrees, and keeps each repo's parent directory clean. **This rule is repo-agnostic** — it holds for AINative and for any external repo a skill operates on (IntelligenceService, UserWeb, Aura, the i18n repos…). When a skill needs an isolated checkout of a repo at `$REPO_DIR`, the worktree path is always `$REPO_DIR/.claude/worktrees/<slug>`.

Then follow the same steps below inside the worktree. Run `eng:clean-up-worktrees` periodically — worktrees trade the checkout-collision problem for a disk/cleanup one, and stale ones accumulate fast.

**Anything a session needs after a restart lives outside the scratchpad.** The session scratchpad (`/private/tmp/claude-501/…`) is wiped when the machine restarts, while the session and its cron jobs resume. So a recurring loop's script, its `requirements.txt` and venv, or a long-running role's state go in a durable folder such as `~/.claude/<name>/`, and the loop prompt says how to rebuild a missing venv. When re-pointing a cron job, create the new one first and delete the old one only after the create succeeds: a create can be refused, and deleting first leaves no job at all. Cron jobs belong to the session, so they survive a restart only if that same session resumes. **Incident:** a restart on 2026-10-23 wiped a monitoring loop's script and venv, and the security checklist's canonical source and rollout scripts; both were rebuilt from the transcript.

**Concurrent PRs collide on version lines, and one of the two ways it fails is silent.** With several agents editing `plugins/**` at once, two branches routinely bump the *same* skill `metadata.version` and the *same* three manifests. `git fetch` immediately before `check-version-bumps.py` — it compares against `origin/main`, so a stale ref makes it pass locally and fail in CI. Then read `mergeStateStatus` as *no textual conflict*, **never** as *versions still increase*:

- **Different target values → `DIRTY`.** Loud, obvious, rebase and keep the higher value.
- **Identical target values → auto-merges, `CLEAN`, and is wrong.** Both sides wrote the same bump, so git has nothing to reconcile. The merge leaves the version at what the first PR already shipped, so the second PR's change lands under a version that is already installed — the install-cache no-op this whole section exists to prevent, and the reload still reports success. The gate only catches it once you rebase.

**On a rebase, the conflict sides are inverted relative to a merge:** the `<<<<<<< HEAD` half is *upstream*, not yours. "Keep ours" by taking the first hunk half therefore reverts your own bump to main's value — which looks like a plausible number rather than a botch. If the reverted value still happens to be an increase the gate passes and ships the wrong version. **So after any version-line resolution, print the resulting versions and compare them to `origin/main` rather than trusting the resolution** — the file parses fine either way, and both failure modes are invisible in a diff that looks clean.

Both halves were hit in one session (2026-08-10): the silent-identical case on [#815](https://github.com/AuraHealth/AINative/pull/815) after [#816](https://github.com/AuraHealth/AINative/pull/816) merged first, and the inverted-rebase-side case on [#819](https://github.com/AuraHealth/AINative/pull/819) resolving against it.

**`git checkout --theirs` on a manifest is only safe when your side is genuinely ahead.** It is the reflex during a version-line rebase, and it is right most of the time — which is what makes the exception dangerous. On [#819](https://github.com/AuraHealth/AINative/pull/819) upstream had taken `shared` to 3.1.0 while the branch held 3.0.4, so `--theirs` would have shipped a regression. **The version gate caught it, not the rebase** — nothing about the conflict resolution itself reports which side is higher. Read both values before choosing a side; do not let the flag pick for you.

**The collision window is between the rebase and the merge button, not before the rebase.** `check-version-bumps.py` compares against `origin/main` **at CI time**, so a PR that was correct when rebased becomes wrong the moment something else lands. The check that actually closes the window is *nothing plugin-touching is open behind you* — `gh pr list --state open` filtered to the plugin you are bumping — because a green gate on a stale base proves only that it was correct earlier. [#821](https://github.com/AuraHealth/AINative/pull/821) needed four rebases and got through on that check, not on the gate.

Ordering follows from this and is worth stating before merging a batch: **when two open PRs bump the same plugin, land the lower version first.** If the higher lands first, the lower one is now *below* `origin/main`, the gate refuses the decrease as a regression, and it needs a fresh bump past the new value. Landing the lower first costs the other only a textual rebase.

**One session with several PRs on the same plugin stacks them.** Open the second PR on the first PR's branch, the third on the second's, and so on, and give Jerome the merge order with the PRs (`eng:stack-prs` does the mechanics). Parallel PRs cut from `main` each bump the same plugin version and touch the same manifests, so every merge breaks the next one: it goes `DIRTY` or fails the version gate and needs another rebase, bump and BugBot pass. **Why:** on 2026-10-28 #1400, #1402 and #1403 were opened side by side on one plugin; each merge left the next one conflicted, and Jerome asked for stacking with a stated merge order from then on.

**A merge in a stack voids the readiness of every PR above it.** When the bottom PR merges on its own, GitHub rebases the next PR onto `main`: it gets a new head commit, CI restarts, and BugBot's clean review is now on a commit that no longer exists. Re-check each PR above on its **new** head (the `Cursor Bugbot` check-run `completed`, the summary citing that sha, 0 unresolved threads, CI green, re-triggering BugBot if needed) before calling it ready again. Never carry a "ready" from before the merge into a status report or a merge-order message. **Why:** on 2026-11-11 Jerome had to answer "not ready" twice (#1569 after #1568 merged, #1582 after #1581 merged), each a PR reported ready from its pre-merge state while its rebased head was still unreviewed or its checks still pending.

1. Branch from `main` with the naming scheme above (`<firstname>/<ticket-id>-<slug>`) — created via `git worktree add -b` per the above.
2. Commit with the `[ENG-XXX]` ticket suffix.
3. `gh pr create --draft` with a PR body that includes a **Summary** and a **Test plan** checklist.
4. `bugbot run`, address every finding, re-trigger until a run on the **current head** is clean. **Clean means 0 unresolved `cursor[bot]` review threads from any run**, outdated ones included, not just a latest run with 0 findings: a run reviews only its own diff and never re-posts an earlier finding ([ENG-1697](https://linear.app/aura-health/issue/ENG-1697)).
5. `gh pr ready` — and only then. A PR with open review findings is not ready for review; that is what the word means.
6. PRs go live as soon as they merge — no separate release step.

**Create as a draft, not "create then remember to hold it".** `bugbot run` is asynchronous, so a PR that is ready the instant it exists can be merged before its review lands — and the fixes are then stranded on a branch that is already in `main`. This is not hypothetical: [#979](https://github.com/AuraHealth/AINative/pull/979) merged with six open findings, one High, while they were being fixed, and they had to be re-landed as a follow-up.

The `--draft` step has been lost from this repo's guidance once before — an extraction cut through a heredoc and took `gh pr create --draft` and the whole Bugbot-then-ready cycle out of both authoring skills (see *A cut through a code block* above). It is written out as its own numbered step here so the next accidental deletion is visible rather than silent.

**A ticket id in a PR title is an assertion that the PR completes the ticket, not a cross-reference.** The Linear/GitHub integration moves a card on the merge of any PR whose title references it, so `[ENG-XXX]` on a partial PR closes a card whose work is still live. Put the id in the title **only** when merging that PR should close that card. To reference a ticket you are not closing — a docs note recording the lesson from a defect, groundwork for a fix landing later, one PR of several — mention it in the **body** in a form that does not link: a Markdown link whose text is not the id (`[the refund card](https://linear.app/aura-health/issue/ENGBACK-2225)`) or the id in backticks. **Never a bare id when the PR's title or branch carries a card id:** the integration then links the bare id's card too, and the merge can move it. Give the PR its own card if it deserves one.

**The branch name counts too.** The integration also moves a card when a PR whose *branch name* contains the card id merges, even with no id in the title — so a partial PR's branch carries no card id either. Name it by its slug, or by its own card. ENGWEB-329 moved to *To Be Released* this way while its rollout was still pending, and had to be moved back by hand.

**Incident:** PaymentService#352 (titled with ENGBACK-2261) named ENGBACK-2225 bare in its body; 2225 sat In Review after the merge. Tested in ENG-1789 (#1487, #1488): with no id in the title or branch, a bare body id never linked.

**The link also works the other way: canceling a card closes its open PRs.** Moving a card to a canceled-type state (Canceled, Duplicate) makes the integration close every open PR linked to it, including one linked only through its branch name, and the close is attributed to the connected account (`jcurlier`), so it looks like a person or a session did it. Before marking a card Duplicate or Canceled, re-link its open PRs to the surviving card (rename the branch or change the PR's linked issue), or wait until they merge. When a PR closes unexpectedly, check its linked card's state history first. Marking ENGBACK-1906 a Duplicate of PROD-7822 closed AINative#1340, and three sessions spent time working out who had closed it.

Two habits follow, because the failure is silent in both directions:

- **Check the card's state after your merge**, not just the PR's. Nothing errors when a card advances early; it simply reads as done to everyone who looks next.
- **Before landing a card-closing PR, move any gated follow-on out of that card's description into its own sub-issue.** A step still waiting on a human decision — an approval, a range, a go/no-go — gets buried by the closing merge, and buried in the one state (*done*) that invites someone to run it.

**The incident this comes from.** [#829](https://github.com/AuraHealth/AINative/pull/829) was titled `… [ENG-1132]` and was docs-only: it recorded *The drift guards compare copies, never implementations* above, which is the lesson from ENG-1132, not its fix. The merge moved ENG-1132 to *To Be Released* while `UID_BLAST_THRESHOLD = 5` was still dropping ~37% of rated entries on `main`. Caught and reverted by hand. The fix landed separately in [#831](https://github.com/AuraHealth/AINative/pull/831), whose title carries the id legitimately; its gated re-harvest was split to [ENG-1133](https://linear.app/aura-health/issue/ENG-1133) first, per the second habit above.

**PR body / commit message sourcing.** Prefer `gh pr create --body-file /tmp/pr-body-<slug>.md` (written via the `Write` tool) over the `"$(cat <<'EOF' … EOF)"` heredoc form — bash intermittently mis-parses that heredoc on rich markdown (apostrophes, em-dashes, arrows, code fences) and aborts with `unexpected EOF`. The same failure hits `git commit -m "$(cat <<…)"` the moment the message has non-ASCII glyphs (`—`, `→`, curly quotes); write it to a file and use `git commit -F`. Heredocs are fine only for short, ASCII-only messages. Observed in [#363](https://github.com/AuraHealth/AINative/pull/363) and the ENGBACK-1132 session.

If you realise mid-task that you've been editing on `main` (e.g. a parallel session flipped `HEAD`), stop: create a branch at your commit (`git branch <branch> <sha>`), hard-reset `main` to `origin/main` (`git reset --hard origin/main`), then push the branch. Working in a worktree avoids this entirely.

**Cursor-bot is enabled on this repo** (2026-05-19). The full cycle is on the [`Procedures/Engineering/PR Process`](https://www.notion.so/34a75e23af2081aab969e6e56a095111) page in AI Knowledge; the open-as-draft rule is steps 3–5 above (the `create-aura-skill` / `update-aura-skill` PR steps bake it in).

**Team recommendation: manual-trigger mode.** Auto-review on every push burns Bugbot credits and clutters PRs with low-signal comments on docs-only or trivial changes. The recommended config for AuraHealth engineering is **manual trigger only** — Bugbot runs when you explicitly ask via `bugbot run` (as a PR comment) or `@cursor review`. Whether the bot fires automatically vs only on manual trigger is a **per-user setting**, not a per-repo setting, so each contributor flips this themselves at <https://www.cursor.com/dashboard/bugbot>.

**Contributor onboarding.** The [`setup-ainative`](plugins/shared/skills/setup-ainative/SKILL.md) skill walks new contributors through flipping their Bugbot config in Phase 5e.

**Manual trigger phrases** (work on any cursor-bot-enabled repo, regardless of auto-mode setting):

- `bugbot run` — bot reviews the current PR head
- `@cursor review` — same thing
- Post either as a PR comment, then wait ~1–2 minutes for the review

**The trigger must be the whole comment, on its own.** A `bugbot run` appended to the end of a longer reply — the natural thing to write when you're answering a finding and re-requesting review in one comment — **does not fire**. Nothing errors; no review is queued and no `Cursor Bugbot` check-run appears on the commit, so it reads exactly like a bot that is merely slow. Post the explanation and the trigger as **two separate comments**.

**Confirm the re-review actually ran before calling a PR clean.** Two failure modes look identical to success:

- The bot's summary comment is **edited in place**, so `created_at` stays at the first review and a stale verdict can look current. Check the commit sha named in the summary body against the PR head instead.
- A review can report **0 findings and then surface one on a later run** for the same head (e.g. the run triggered by `gh pr ready`). Wait for the `Cursor Bugbot` check-run to reach `completed`, not just for a summary to appear.

```bash
# reviewed-the-current-head + check actually finished
gh api repos/AuraHealth/AINative/issues/<pr>/comments \
  --jq '[.[]|select(.user.login=="cursor[bot]")]|last|.body' | grep -oE 'commit [0-9a-f]{40}'
gh api repos/AuraHealth/AINative/commits/<sha>/check-runs \
  --jq '.check_runs[]|select(.name=="Cursor Bugbot")|"\(.status) \(.conclusion)"'
```

Unresolved findings come from the GraphQL `reviewThreads` (filter `isResolved == false`); an `isOutdated: true` thread is one whose anchored lines your fix already changed. Both traps hit real PRs in the [ENG-995](https://linear.app/aura-health/issue/ENG-995) session — a buried trigger silently no-opped twice, and a "0 findings" first pass was followed by a real Medium finding on the ready-flip re-run.

**Answer every Bugbot finding in its own thread, then resolve it; never resolve silently.** The reply says what changed and the commit sha, or why it won't be fixed. Canonical: [`Procedures/Engineering/PR Process`](https://www.notion.so/34a75e23af2081aab969e6e56a095111) in AI Knowledge.

If your config is set to auto-mode, you'll see cursor-bot review every push without prompting — same as on `AuraHealth/Aura`, `AuraHealth/AuraConsumerApp`, `AuraHealth/UserWeb`, `AuraHealth/CoachWeb`, and `AuraHealth/PaymentService` (which are also enabled). The PR workflow doesn't change either way: don't request human review until cursor-bot's findings (if any) are addressed or explicitly waived in a reply.
