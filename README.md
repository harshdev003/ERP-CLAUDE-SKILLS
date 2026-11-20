# ERP-CLAUDE-SKILLS

Claude Code skills, agents and rules for building the ERP, kept in one place and
synced to every machine.

## How it works

- Every Claude Code session start runs `scripts/sync.sh` (SessionStart hook):
  it `git pull`s this repo, then installs `skills/*` into `~/.claude/skills/`,
  `agents/*.md` into `~/.claude/agents/`, and `rules/CLAUDE.md` into `~/.claude/CLAUDE.md`.
- Whenever Claude creates or changes a skill or rule, it commits it here and pushes
  with `scripts/publish.sh` as `harshdev003` (that instruction lives in `rules/CLAUDE.md`).

## Layout

| Folder     | What goes there | Installed? |
|------------|-----------------|------------|
| `skills/`  | One folder per skill: `skills/<name>/SKILL.md` (+ scripts/references) | yes |
| `agents/`  | Subagent definitions `<name>.md` | yes |
| `rules/`   | `CLAUDE.md` (global rules), `personal.md`, `services/` (rules for every ERP-*-SERVICE repo) | `CLAUDE.md` (imports `personal.md`) |
| `library/` | Reference material, e.g. `library/aura/` — another team's setup (see `library/README.md`) | **no** |
| `hooks/`   | Hook config fragments (`settings.hooks.json`) | via `install.sh` |
| `scripts/` | `sync.sh`, `publish.sh`, `install.sh` | — |
| `docs/`    | Notes, conventions, `glossary.md` | — |

## Set up on a new machine

```bash
git clone https://github.com/harshdev003/ERP-CLAUDE-SKILLS.git ~/ERP-CLAUDE-SKILLS
bash ~/ERP-CLAUDE-SKILLS/scripts/install.sh
```

## Publish a change by hand

```bash
bash ~/ERP-CLAUDE-SKILLS/scripts/publish.sh "Describe the change"
```

## Skills

| Skill | Description |
|-------|-------------|
| `review-pr` | Review a GitHub PR against the ERP service rules and post inline comments (asks before posting) |
| `linear-task` | Drive an issue end to end: right repo, plan, implement, self-review, PR, close out |
| `stack-prs` | Build and drive a stack of dependent PRs with `gh stack` |
| `eng-handoff` | Turn an issue into an engineering handoff (requirements → per-repo breakdown) |
| `update-changelog` | Build a changelog from commits since the last release, linked to issues |
| `bugbot-review` | Trigger and wait for a Cursor BugBot review on a PR |
| `triage-review-requests` | List open PRs waiting on your review across repos |
| `protect-github-branches` | Audit and apply branch protection on the ERP repos |
| `clean-up-worktrees` | Find and remove stale git worktrees safely |
| `convene-panel` | Multi-persona debate (incl. ERP compliance, finance controller, operator, data-integrity reviewers) |
| `guide-me` | Walk through everything waiting on you, one step at a time |
| `senior-frontend` | React/Next.js frontend patterns, scaffolding and bundle analysis |

## Agents

| Agent | Description |
|-------|-------------|
| `implementation-agent` | Implements one issue in one ERP repo on its own branch and opens a draft PR (used by `linear-task`) |
| `code-investigator` | Read-only: given a stacktrace, finds the code and recent commits and reports a hypothesis |
| `error-analyzer` | Systematic 5–7-cause bug diagnosis, validated with logs before fixing |

## Rules

- [`rules/CLAUDE.md`](rules/CLAUDE.md) — global rules (account, naming, sync, skills workflow)
- [`rules/personal.md`](rules/personal.md) — credit-usage guard, handoff notes
- [`rules/services/`](rules/services/README.md) — engineering & security rules for every `ERP-*-SERVICE` repo

## Origin

12 skills, 3 agents and the service rules were adapted from another team's Claude setup;
the unchanged source is in `library/aura/` — see [`library/README.md`](library/README.md)
for what came from where and what is still a candidate.
