# Posting troubleshooting

Read this file **only when Step 8 posting fails**. Don't pre-load it during a clean run — the failure modes here are rare, and pre-loading them inflates context for the 95% of runs where Step 8 succeeds first try.

## When inline posting fails consistently — rotate the token first

Before debugging anything else, if `POST /pulls/{pr}/comments` returns `422 — pull_request_review_thread.base internal error` (REST), or `addPullRequestReviewThread` returns `{"data": {"addPullRequestReviewThread": {"thread": null}}}` with NO `errors` field (GraphQL — looks like soft success but no thread is created and an empty pending review IS created server-side), the cause is almost always **GitHub-side state corruption keyed to the auth token**, not the payload.

### Diagnosis is one call

Try the same minimal mutation on a *different* PR in the same repo. Reuse the head SHA + line 1 of the first changed file:

```bash
TOKEN=$(gh auth token)
OTHER_PR=<any other open PR in the repo>
OTHER_SHA=$(gh pr view "$OTHER_PR" --json headRefOid --jq '.headRefOid')
OTHER_FILE=$(gh pr view "$OTHER_PR" --json files --jq '.files[0].path')
curl -sS -X POST "https://api.github.com/repos/<owner>/<repo>/pulls/$OTHER_PR/comments" \
  -H "Authorization: Bearer $TOKEN" -H "Accept: application/vnd.github+json" \
  -H "Content-Type: application/json" \
  -d "{\"commit_id\":\"$OTHER_SHA\",\"path\":\"$OTHER_FILE\",\"line\":1,\"side\":\"RIGHT\",\"body\":\"probe\"}" | head -c 200
```

- **Same error on the other PR** → confirmed token-state bug. **Skip directly to "Fix" below.** Do NOT spend time on commit-SHA variants, schema variants, GraphQL fallbacks, deprecated mutations, or `addPullRequestReview` with `commitOID`. They all fail the same way through the same broken code path. (A real session burned ~45 minutes ruling all of these out before rotating the token.)
- **Different error on the other PR (e.g. 404 / 422 with a *different* field)** → genuine PR-specific issue (out-of-range line, file not in diff, etc.). Debug the payload normally.
- **Probe succeeds on the other PR** → genuine PR-specific issue too. Continue debugging the original payload.

### Fix (when it's the token bug)

1. Re-issue the credential `gh` is using. Normal setup is `gh auth login` (keyring OAuth, no `GITHUB_TOKEN` env var — see AINative CLAUDE.md → "GitHub credentials"), so run `gh auth refresh -h github.com -s repo` or `gh auth login` again. Same user, same scopes — fresh issuance is the entire fix, and the bug doesn't care about token shape.
2. Confirm the new credential is the one in play: `gh auth status`, then `gh auth token | head -c 4` (expect `gho_`). If `GITHUB_TOKEN` is set in the environment it **shadows** the keyring credential and re-issuing achieves nothing — unset it (in `~/.claude/settings.json` `env`, then **restart Claude Code**; the `env` block is read at startup and mid-session changes don't reach subprocess `gh` calls).
3. Re-run Step 8 from the top. Sweep self-created phantom pending reviews first (Step 8a's phantom-pending sweep), then post. Should succeed first try.

### Cleanup of probe debris

From this diagnostic + any earlier failed attempts:

```bash
# Phantom pending reviews on the original PR
AUTH_USER=$(gh api user --jq '.login')
gh api "repos/<owner>/<repo>/pulls/<num>/reviews" \
  --jq ".[] | select(.state == \"PENDING\" and .user.login == \"$AUTH_USER\") | .id" | \
while read -r id; do gh api -X DELETE "repos/<owner>/<repo>/pulls/<num>/reviews/$id"; done

# The probe comment on the other PR (if it succeeded as a single-comment review,
# fetch its id from the response and DELETE)
```

Don't surface probe attempts to the user unless the diagnosis takes more than one round.
