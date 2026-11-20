# Edge cases

Situations this skill hits often enough to have a settled answer. Consult before improvising.

**User wants to update a skill that doesn't exist:**
If the skill name isn't found in any plugin, suggest using `create-aura-skill` instead.
Tell them: "I couldn't find a skill called '<name>'. Would you like to create it instead?"

**Multiple changes across different skills:**
If the user wants to update several skills at once, handle them one at a time. Create
separate branches and PRs for each skill to keep reviews clean.

**Merge conflicts when pushing:**
If the push fails due to conflicts (someone else modified the same skill), pull the latest
and attempt to rebase from inside the worktree:
```bash
cd "$WT"
git fetch origin "$DEFAULT_BRANCH"
git rebase "origin/$DEFAULT_BRANCH"
```
If the rebase has conflicts, show the user and help them resolve manually.

**User wants to delete an entire skill:**
This is supported. Remove the skill directory, commit, and create a PR — from an isolated
worktree (`<repo>/.claude/worktrees/<slug>`, never a sibling of the repo):
```bash
git -C "$REPO_DIR" fetch origin --quiet
DEFAULT_BRANCH=$(git -C "$REPO_DIR" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's|^origin/||'); DEFAULT_BRANCH=${DEFAULT_BRANCH:-main}
WT="$REPO_DIR/.claude/worktrees/remove-skill-<skill-name>"
git -C "$REPO_DIR" worktree add "$WT" -b remove-skill/<skill-name> "origin/$DEFAULT_BRANCH"
cd "$WT"
git rm -r plugins/<plugin-name>/skills/<skill-name>/
git commit -m "Remove <skill-name> skill from <plugin-name> plugin"
git push -u origin remove-skill/<skill-name>
gh pr create --draft --title "Remove <skill-name> skill from <plugin-name>" --body "..."
```
Open as a draft and run the Step 8b Bugbot-then-`gh pr ready` cycle here too.

**User wants to move a skill between plugins:**
Handle as a combined remove + add, in an isolated worktree:
```bash
git -C "$REPO_DIR" fetch origin --quiet
DEFAULT_BRANCH=$(git -C "$REPO_DIR" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's|^origin/||'); DEFAULT_BRANCH=${DEFAULT_BRANCH:-main}
WT="$REPO_DIR/.claude/worktrees/move-skill-<skill-name>"
git -C "$REPO_DIR" worktree add "$WT" -b move-skill/<skill-name> "origin/$DEFAULT_BRANCH"
cd "$WT"
git mv <old-plugin>/skills/<skill-name> <new-plugin>/skills/<skill-name>
git commit -m "Move <skill-name> skill from <old-plugin> to <new-plugin>"
git push -u origin move-skill/<skill-name>
```

**gh CLI not available or not authenticated:**
Redirect to the `setup-ainative` skill to fix the environment, then retry.
