# Global rules (managed by ERP-CLAUDE-SKILLS)

> This file is installed from `~/ERP-CLAUDE-SKILLS/rules/CLAUDE.md` at every session start.
> Do NOT edit `~/.claude/CLAUDE.md` directly - edit the repo copy and publish, or the change is overwritten.

## Skills repository

All my Claude skills and supporting rules live in the GitHub repo
`https://github.com/harshdev003/ERP-CLAUDE-SKILLS`, cloned at `~/ERP-CLAUDE-SKILLS`
(`C:\Users\Harshit\ERP-CLAUDE-SKILLS`).

### GitHub account (strict)
- Commit and push to this repo and every ERP-* repo ONLY as the GitHub account **harshdev003**
  (author `harshdev003 <358348782+harshdev003@users.noreply.github.com>`).
  Never use any other account or identity (e.g. the global git user).
- `scripts/publish.sh` sets this identity on every run - always go through it.
- If a push fails with an auth error or the signed-in account is not harshdev003,
  stop and ask me to sign in; do not switch to another account.

### Repository naming (strict)
- Every repo name is ALL CAPS, starts with `ERP-`, words joined with `-`
  (no underscores, spaces, lowercase or version numbers).
- Service code repos end in `-SERVICE`: `ERP-<PART>-SERVICE`.
- Current repos:
  - `ERP-APP-SERVICE` - app (formerly ERP_Stack15)
  - `ERP-WEB-SERVICE` - web frontend
  - `ERP-BACKEND-SERVICE` - backend
  - `ERP-CLAUDE-SKILLS` - Claude skills & rules (tooling, not a service)
- When a repo is added or renamed, update this list.
- The README title (`# ...`) must match the repo name, and local clones use
  the same folder name (e.g. `C:\Users\Harshit\ERP-WEB`).
- Exception: `harshdev003/harshdev003` is GitHub's profile-README repo and must
  keep that exact name.

### Sync on start
- A SessionStart hook runs `scripts/sync.sh`, which pulls the repo and installs
  `skills/*` into `~/.claude/skills/` and this file into `~/.claude/CLAUDE.md`.
- If its message says the pull FAILED, tell me at the start of the conversation
  so the skills are not silently out of date.

### Creating or changing a skill (always)
1. Create/edit it in the repo: `~/ERP-CLAUDE-SKILLS/skills/<skill-name>/SKILL.md`
   (plus any scripts/references inside that folder). Kebab-case names.
   If a tool (e.g. skill-creator) wrote it to `~/.claude/skills/<name>`, import it
   with the publish script's skill-name argument.
2. Add/update its row in `~/ERP-CLAUDE-SKILLS/README.md` (Skills table).
3. Publish - commit, push, reinstall:
   `bash ~/ERP-CLAUDE-SKILLS/scripts/publish.sh "Add <skill-name> skill" [<skill-name>]`
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
