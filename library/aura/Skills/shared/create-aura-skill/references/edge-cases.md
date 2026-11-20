# Edge cases

**User wants to add a command (not a skill):**
Commands are the legacy single-file format (`commands/<name>.md`). If the user explicitly
asks for a command, create it — but suggest the skills format instead, since it supports
progressive disclosure with `references/` and is the preferred approach going forward.

**User wants to update an existing skill:**
If the skill already exists, redirect the user to the `update-aura-skill` skill instead.
Tell them: "That skill already exists — would you like me to update it instead?"

**User provides files but no SKILL.md:**
If the user gives you scripts or reference files but no SKILL.md, draft one based on
the file contents and ask the user to review before committing.

**gh CLI not available or not authenticated:**
Redirect to the `setup-ainative` skill to fix the environment, then retry.

**Local repo has uncommitted changes on a feature branch:**
If the user was working on something else, stash their changes before switching to main.
Restore them after the skill creation is done:
```bash
git stash
# ... do the work ...
git stash pop
```
