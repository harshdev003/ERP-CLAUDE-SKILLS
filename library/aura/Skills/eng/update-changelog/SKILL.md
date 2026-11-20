---
name: update-changelog
description: Builds a changelog from git commits since the last release, linked to Linear issues, grouped by label, with Slack-friendly formatting. Use when the user asks for a changelog, "what was just shipped", or wants release notes for a repo.
metadata:
  version: "3.0.1"
knowledge:
  reads:
    - Guide/Index
    - Tools/Linear                   # Linear MCP / GraphQL access
    - Tools/GitHub                   # gh CLI auth + PR conventions
---

# Changelog (git commits → Linear issues, since last release)

Produces a **changelog** from all commits since the last release, linked to **Linear issues** found in commit messages, grouped by **label**. Commits with no Linear issue go under **Misc**. Writes the result to `CHANGELOG.md` in the repo root.

## Prerequisites

- Linear MCP must be enabled and authenticated.
- `gh` CLI must be installed and authenticated for the repo being changelogged — used to recover Linear IDs from PR descriptions when they aren't in the commit message (Step 3b).

---

## Step 0: Identify the Repo

**If a repo name is provided as an argument:**

List available repos and fuzzy-match:

```bash
ls AuraHealth/
```

Pick the best match using common sense (e.g. "user service" → `UserService`, "payments" → `PaymentService`). If ambiguous, show candidates and ask. Set `REPO_PATH=AuraHealth/<repo>`.

**If no repo name is provided:**

Check whether the current working directory is inside a git repo:

```bash
git rev-parse --show-toplevel
```

If this succeeds, use the returned path as `REPO_PATH`. If it fails (not a git repo), stop and tell the user to either specify a repo name or run the command from inside a git repo.

---

## Step 1: Find the Last Release Reference

Try the following in order, stopping at the first success:

### 1a. Check for a changelog file

Look for `CHANGELOG.md` in `REPO_PATH`. If it exists, parse the first line matching `## X.Y.Z` or `## vX.Y.Z` (or `## X.Y.Z (N) - YYYY-MM-DD`). Extract the version string (e.g. `1.4.2`) as `LAST_VERSION`.

Then resolve it to a git tag:

```bash
git -C <REPO_PATH> tag --list | grep -F "<LAST_VERSION>"
```

If a matching tag is found, use it as `SINCE_REF`. If no matching tag is found, use the version string directly (git will try to resolve it) or fall back to 1b.

### 1b. Check for the latest git release tag

If no changelog file or no matching tag was found:

```bash
git -C <REPO_PATH> tag --sort=-version:refname | head -1
```

If a tag is returned, use it as `SINCE_REF`.

### 1c. No reference found — exit

If neither a changelog file nor any git tags exist, stop and tell the user:

> No changelog file and no release tags found in `<repo>`. Create an initial release tag (e.g. `git tag v0.1.0`) or provide a starting point to generate the changelog from.

---

## Step 2: List Commits Since Last Release

```bash
git -C <REPO_PATH> log <SINCE_REF>..HEAD --oneline --no-merges
```

Each line has the format: `<short-sha> <commit message>`.

If there are no commits since `SINCE_REF`, tell the user and stop.

---

## Step 3: Extract Linear Issue IDs from Commits

Scan every commit message for two patterns:

**Linear issue IDs** — uppercase letters, hyphen, digits: e.g. `ENG-123`, `PROD-456`, `INF-7`.
Regex: `\b[A-Z]{2,10}-\d+\b`

**Experiment names** — lowercase prefix, colon, camelCase name: e.g. `ab:hideDone`, `abc:webViewPaywall`.
Regex: `\b[a-z]{2,4}:[a-zA-Z][a-zA-Z0-9]+\b`

- Collect the **unique set** of issue IDs across all commits.
- For each issue ID, record the commit messages that referenced it (used for exp name extraction in Step 4).
- Commits that match **no** issue IDs are collected separately as **candidate orphaned commits**. Step 3b will try to recover Linear IDs from their PR descriptions before marking them as truly orphaned. If an orphaned commit still contains an exp name pattern, lead that commit's bullet with the exp name in backticks followed by a colon (same format as issue bullets).

---

## Step 3b: Recover Linear IDs from PR Descriptions (fallback)

Many commits land via squash-merge with a trailing `(#NNN)` PR number but no Linear ID in the commit message itself — the Linear link lives in the PR description (e.g. under a `## Related` section). For every candidate orphaned commit from Step 3:

1. **Extract the PR number** from the commit message via the trailing `(#NNN)` pattern (regex `\(#(\d+)\)\s*$`). If no PR number is present, mark the commit as truly orphaned and skip to the next.

2. **Fetch the PR title and body** via `gh`:

   ```bash
   gh pr view <PR_NUMBER> --json title,body --jq '.title + "\n---\n" + .body'
   ```

   If `gh` fails (PR not found, repo mismatch, network), mark the commit as truly orphaned and continue.

3. **Scan the PR body for Linear issue IDs** using the same regex as Step 3: `\b[A-Z]{2,10}-\d+\b`. Also scan for experiment-name patterns. The most reliable signal in Aura PR templates is a `## Related` section containing `Linear Issue: [ENG-123](...)` — but accept any match in the body.

4. **Promote recovered commits**: if one or more Linear IDs are found in the PR body, treat the commit as if those IDs appeared in the commit message itself — add the IDs to the unique set, attach the commit to each ID (for exp-name extraction), and remove the commit from the orphaned list.

5. **Run PR lookups in parallel** when there are several candidate orphans — one `gh pr view` call per PR is independent, so batch them.

6. **Truly orphaned commits** — commits with no Linear ID in the message AND no Linear ID in the PR body (or no PR number at all) — remain in the orphaned list and fall through to **Misc** in Step 6.

---

## Step 4: Resolve Linear Issues, Walk to Top Parent, and Extract Exp Name

For each unique issue ID:

1. Call `get_issue` with the issue identifier to fetch the issue (title, labels, parent, custom fields).
2. **Exp name:** Scan the issue title, description, and any custom fields for the experiment name pattern: a short lowercase prefix, a colon, and a camelCase identifier — e.g. `ab:hideDone`, `abc:webViewPaywall`. Regex: `\b[a-z]{2,4}:[a-zA-Z][a-zA-Z0-9]+\b`. Also scan the **commit messages** that referenced this issue for the same pattern. If found anywhere, record `expName`. If multiple matches are found, prefer the one from the issue title; otherwise use the first match.
3. If the issue has a parent, call `get_issue` on the parent and repeat until there is no parent. The **topmost ancestor** is the `topParent`.
4. Record for each issue: `{ issueId, issueTitle, issueUrl, expName, topParent: { id, title, url, labels } | null }`.

**Build the entry map** — three distinct cases after resolving all issues:

| Case | Condition | Changelog entry |
|------|-----------|-----------------|
| Same issue | Multiple commits all reference the **same** issue ID | One bullet for that issue (deduplication only; parent is ignored) |
| Shared parent | Multiple commits reference **different** issue IDs that share the **same parent** | One bullet for the parent issue; each distinct child issue appears as an indented sub-bullet |
| Standalone | A commit references an issue that has no parent, **or** an issue whose parent is not shared by any other commit's issue | One bullet for the issue itself |

**Key rule:** Parent consolidation only happens when 2 or more *distinct* issue IDs from the commit set share the same parent. A single issue with a parent renders as a standalone entry for that issue — not the parent.

**Unreachable issues:** If `get_issue` returns an error (issue not found, wrong workspace, etc.), treat those commits as orphaned.

---

## Step 5: Resolve Labels

Use the labels from the **top parent issue** (or the issue itself if standalone) for grouping.

- If the issue has no labels, put it under `Other`.
- Issues with the label `Bug` or `Bugs` go into the `Bugs` group.
- Issues with the label `Analytics & Data` go into the `Analytics & Data` group.

---

## Step 6: Build the Grouped List

Group entries as follows:

1. **Feature/product label groups** — alphabetical order, one section per label.
2. **Misc** — orphaned commits (no Linear issue found). List each commit's message as a bullet; omit the SHA.
3. **Other** — labeled issues with no matching group above.
4. **Bugs** — always second-to-last.
5. **Analytics & Data** — always last.

Under each label group, render entries according to the three cases from Step 4:

**Standalone issue** (no parent) — one bullet:
```
• Issue title [ENG-100](https://linear.app/aura-health/issue/ENG-100)
```

**Standalone issue with exp name** — lead with the exp name in backticks, then a colon, then the title:
```
• `ab:my-experiment-flag`: Issue title [ENG-100](https://linear.app/aura-health/issue/ENG-100)
```

**Sub-issue(s) sharing a parent** — parent as main bullet, sub-issues indented:
```
• Parent issue title [ENG-200](https://linear.app/aura-health/issue/ENG-200)
  - Sub-issue title [ENG-201](https://linear.app/aura-health/issue/ENG-201)
  - `ab:flag-name`: Another sub-issue [ENG-202](https://linear.app/aura-health/issue/ENG-202)
```

If the parent itself has an exp name, lead the parent bullet with it. If a sub-issue has a different exp name, lead that sub-issue bullet with its own exp name.

Under **Misc**, list each orphaned commit message as a bullet (no link):

```
• chore: update dependencies
• fix typo in onboarding copy
```

---

## Step 6.5: Preview and Confirm (before writing)

**Plan-and-confirm gate. `CHANGELOG.md` is a tracked file — do not write it until the user approves.**

Render the assembled changelog block (header + grouped entries) and show it to the user, then ask:

> Prepend this entry to `<REPO_PATH>/CHANGELOG.md`? Confirm the version header `## X.Y.Z - YYYY-MM-DD` is right (I inferred `<version>`), or tell me the correct version.

Wait for explicit approval. If the version was inferred (Step 7 placeholder), the user should confirm or correct it here before anything is written.

---

## Step 7: Write the Changelog

### Header

Determine the new version header:

- If the user provided a version, use it.
- Otherwise, use a placeholder (e.g. increment the patch of `LAST_VERSION`) and note it in your response so the user can correct it.
- Use today's date in **Pacific time** (America/Los_Angeles).

Format: `## X.Y.Z - YYYY-MM-DD`

### File output

Write to `<REPO_PATH>/CHANGELOG.md`. Prepend the new entry at the top of the file (newest first). If the file does not exist, create it.

**Idempotency guard.** Before prepending, check whether the top of `CHANGELOG.md` already has an entry for this same version header (`## X.Y.Z - YYYY-MM-DD`). If it does, a prior run already wrote this release — do **not** prepend a second block (that produces a duplicate top entry when the skill is re-run without a new release tag). Instead, tell the user the entry already exists and offer to replace that top block in place or bump the version.

---

## Output Format (Slack markup)

```
## 1.4.3 - 2026-05-14

*Feature Group A*
• Standalone feature [ENG-100](https://linear.app/aura-health/issue/ENG-100)
• `ab:hideDone`: Experiment feature [ENG-101](https://linear.app/aura-health/issue/ENG-101)
• Parent epic title [ENG-200](https://linear.app/aura-health/issue/ENG-200)
  - Sub-issue one [ENG-201](https://linear.app/aura-health/issue/ENG-201)
  - `abc:webViewPaywall`: Sub-issue two [ENG-202](https://linear.app/aura-health/issue/ENG-202)

*Misc*
• chore: bump lodash to 4.17.21
• fix copy on settings screen

*Bugs*
• Fix crash on startup [ENG-120](https://linear.app/aura-health/issue/ENG-120)

*Analytics & Data*
• Add session tracking event [ENG-130](https://linear.app/aura-health/issue/ENG-130)
```

**Link rule:** `[issueId](shortUrl)` — use `https://linear.app/aura-health/issue/<IDENTIFIER>` only (no slug, no `|`).

---

## Notes

- Use `--no-merges` when listing git commits to skip merge commits.
- If the repo uses a monorepo or non-standard tag format, ask the user to confirm `SINCE_REF` before proceeding.
- If `CHANGELOG.md` already exists in the repo with a different format, adapt the header parsing to match.
- If no Linear issues are found in any commit (and none recovered from PR descriptions in Step 3b), and all commits are truly orphaned, write the full list under `Misc` only.
- Squash-merged commits typically end with `(#NNN)` — Step 3b uses this to fetch the PR body. If the repo uses a different merge strategy (rebase or true merge), the `(#NNN)` suffix may be absent and PR-description fallback will not apply.
