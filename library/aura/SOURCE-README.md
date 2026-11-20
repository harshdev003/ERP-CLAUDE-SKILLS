# Aura AI rules and skills

A snapshot taken 2026-11-21 of every rule, skill, agent and supporting file the team uses with Claude and Cursor. It covers this Mac and GitHub (the AuraHealth org), and is grouped by type. It is a copy for reading: editing a file here changes nothing in the repos or in Claude.

| Folder | What's inside | Grouped by |
|---|---|---|
| **Rules** | `CLAUDE.md`, `AGENTS.md`, `BUGBOT.md`, Claude rules (`claude/`), Cursor rules (`cursor/`), Copilot instructions. 665 files | Repo. `My rules` holds your personal `CLAUDE.md` |
| **Skills** | 366 skills, each folder with its `SKILL.md`, references and scripts | Owner (see below) |
| **Agents** | 40 subagent definitions (Claude and Cursor) | Repo or plugin |
| **Memory** | Claude's saved notes per project: Aura (164), AuraConsumerApp (14), Aura older copy (23). Start at `MEMORY.md` | Project |
| **Handoffs** | Handoff notes from long sessions, with QA screenshots and logs | Date or task |
| **Settings** | Claude settings per repo, plugin manifests, `mcp.json`, and your own settings with keys hidden | Repo |
| **Other** | AINative docs, scripts, tests and commands; your saved plans; the scheduled support-loop task | — |

## Skills, by owner

| Folder | Skills | Where they come from |
|---|---|---|
| `eng` | 184 | Team plugin (AINative), used as `eng:...` |
| `data` | 49 | Team plugin, `data:...` |
| `product` | 27 | Team plugin, `product:...` |
| `shared` | 25 | Team plugin, `shared:...` |
| `marketing` | 11 | Team plugin, `marketing:...` |
| `qa` | 9 | Team plugin, `qa:...` |
| `AIAdCreatives` | 19 | AIAdCreatives repo |
| `AuraConsumerApp` | 7 | Aurie app repo |
| `Aura` | 6 | Aura app repo: `sim-test`, `release-ios`, `figma-to-code`, ... |
| `LivekitVoiceAgent` | 2 | Voice agent repo |
| `AnalyticsReports` | 1 | Analytics repo |
| `My skills` | 3 | Only on your Mac: `todays-report`, `weekly-retro`, `senior-frontend` (from Downloads) |
| `Claude app` | 23 | The desktop app: org skills (`coach-refund-context`, `morning`, `daily-work-report`, ...) and Anthropic's own (`docx`, `pdf`, `skill-creator`, ...) |

## "Not merged yet"

The Rules, Skills and Settings folders each have a `Not merged yet` folder. It holds versions that are newer than GitHub's default branch:

- **My Mac - ...**: your local edits that aren't on master, such as the new `detect-attacks` skill (AINative), the new `voice-match` skill (AuraConsumerApp), and `sim-test` and `figma-to-code` edits in an Aura worktree.
- **PR ... by ...**: open PRs from teammates that change rules or skills: AINative #1668 and #1669 (jcurlier), and Aura #7665 and #6793 (rohanx96). Aura #7635 was skipped, because it only syncs master into the Android branch.

## Left out on purpose

- **Files that hold keys:** your `settings.json` (a copy with keys hidden is in Settings), every `settings.local.json`, `~/.claude.json` (MCP logins) and the Aura cask.
- **Other tools' built-in skills,** which are not the team's:
  - Codex (`~/.codex`)
  - Cursor (`~/.cursor`)
  - Antigravity and Gemini (`~/.gemini`, `~/.antigravity*`)
  - The Figma plugin
  - Skills bundled inside Ruby gems and Python packages
- **Large build output in handoffs:** app bundles, frameworks, videos, a Python venv, and any file over 2 MB.
- **`.claude/worktrees`** code checkouts.
- **Archived repos, and repos with no pushes since 2025.**

Teammates' personal skills on their own Macs can't be reached from here. This folder has only what is on GitHub plus what is on this Mac.

A secret-pattern scan found no real keys. The only matches were a fake Slack token in an AINative test and the documented sim-test account password, both already on GitHub.
