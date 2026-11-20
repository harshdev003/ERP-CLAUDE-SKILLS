# ERP-CLAUDE-SKILLS

My Claude Code skills and global rules, kept in one place and synced to every machine.

## How it works

- Every Claude Code session start runs `scripts/sync.sh` (SessionStart hook):
  it `git pull`s this repo, then installs `skills/*` into `~/.claude/skills/` and
  `rules/CLAUDE.md` into `~/.claude/CLAUDE.md`.
- Whenever Claude creates or changes a skill or rule, it commits it here and pushes
  with `scripts/publish.sh` (that instruction lives in `rules/CLAUDE.md`).

## Layout

| Folder     | What goes there |
|------------|-----------------|
| `skills/`  | One folder per skill: `skills/<name>/SKILL.md` (+ scripts/references) |
| `rules/`   | `CLAUDE.md` (global rules) and topic rule files `rules/<topic>.md` |
| `hooks/`   | Hook config fragments (`settings.hooks.json`) |
| `scripts/` | `sync.sh`, `publish.sh`, `install.sh` |
| `docs/`    | Notes, conventions, how-tos |

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
| _none yet_ | |
