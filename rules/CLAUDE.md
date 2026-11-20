# Global rules (managed by ERP-claude_Skill)

> This file is installed from `~/ERP-claude_Skill/rules/CLAUDE.md` at every session start.
> Do NOT edit `~/.claude/CLAUDE.md` directly - edit the repo copy and publish, or the change is overwritten.

## Skills repository

All my Claude skills and supporting rules live in the GitHub repo
`https://github.com/harshdev003/ERP-claude_Skill`, cloned at `~/ERP-claude_Skill`
(`C:\Users\Harshit\ERP-claude_Skill`).

### GitHub account (strict)
- Commit and push to this repo ONLY as the GitHub account **harshdev003**
  (author `harshdev003 <358348782+harshdev003@users.noreply.github.com>`).
  Never use any other account or identity (e.g. the global git user).
- `scripts/publish.sh` sets this identity on every run - always go through it.
- If a push fails with an auth error or the signed-in account is not harshdev003,
  stop and ask me to sign in; do not switch to another account.

### Sync on start
- A SessionStart hook runs `scripts/sync.sh`, which pulls the repo and installs
  `skills/*` into `~/.claude/skills/` and this file into `~/.claude/CLAUDE.md`.
- If its message says the pull FAILED, tell me at the start of the conversation
  so the skills are not silently out of date.

### Creating or changing a skill (always)
1. Create/edit it in the repo: `~/ERP-claude_Skill/skills/<skill-name>/SKILL.md`
   (plus any scripts/references inside that folder). Kebab-case names.
   If a tool (e.g. skill-creator) wrote it to `~/.claude/skills/<name>`, import it
   with the publish script's skill-name argument.
2. Add/update its row in `~/ERP-claude_Skill/README.md` (Skills table).
3. Publish - commit, push, reinstall:
   `bash ~/ERP-claude_Skill/scripts/publish.sh "Add <skill-name> skill" [<skill-name>]`
4. Confirm to me that it was pushed (or show the error if the push failed).

### Supporting rules and material
Store everything systematically in the repo, never only on this PC:
- `rules/`   - global rules (this file) and topic rule files (`rules/<topic>.md`),
               referenced from this file.
- `skills/`  - one folder per skill.
- `hooks/`   - hook snippets/settings fragments used by the setup.
- `scripts/` - sync / publish / helper scripts.
- `docs/`    - notes, conventions, how-tos.
After adding or changing any of these, publish the same way.
