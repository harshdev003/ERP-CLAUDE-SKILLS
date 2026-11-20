# BugBot cycle for the prompt PR

The PR opens as a **draft**. It goes ready only after BugBot is clean on its current head and CI is green.
This is the standard cycle (AINative `CLAUDE.md` → "PR workflow", and `Procedures/Engineering/PR Process`
in AI Knowledge). A PR that is ready the moment it exists can merge before the review lands; one prompt PR
merged 7 minutes after opening (ENG-1996).

**Clean** means a BugBot run on the **current head** has finished and there are **0 unresolved BugBot review
threads from any run, outdated ones included**. A latest run with 0 findings is not enough: a run reviews
only its own diff and never re-posts an earlier finding. In GraphQL the bot's thread author is `cursor`
(the REST login is `cursor[bot]`): filter threads on `author.login == "cursor"`, or the list comes back
empty while findings are still open.

1. **Trigger and wait with `eng:bugbot-review` in wait mode** ("run bugbot and wait" on this PR). It posts
   `bugbot run` as a comment of its own, polls until the `Cursor Bugbot` check-run on the current head has
   completed (a missing, queued or in-progress run is still in flight), paginates, handles the credential
   403, and lists the unresolved threads. Don't reimplement the poll here: a one-shot read can find no
   check-run yet and let the ready flip through.

2. **Address every unresolved thread.** For each one, fix it in the worktree (lint, commit, push), or decide
   it won't be fixed. Then reply in **that thread** with what changed and the commit sha, or why not, and
   resolve the thread. Never resolve one silently.

   ```bash
   gh api graphql -f query='mutation($t:ID!,$b:String!){addPullRequestReviewThreadReply(input:{pullRequestReviewThreadId:$t,body:$b}){comment{id}}}' -F t=<thread id> -F b="Fixed in <sha>: <what changed>"
   gh api graphql -f query='mutation($t:ID!){resolveReviewThread(input:{threadId:$t}){thread{isResolved}}}' -F t=<thread id>
   ```

3. **After any push, go back to step 1** for the new head, until a run on it is clean.

4. **Ready.** Only when step 1 reported a completed run on the current head and 0 unresolved threads, wait
   for every CI check on that head, then flip it. A draft reports `mergeStateStatus` as `DRAFT`, never
   `CLEAN`, so the checks are the signal:

   ```bash
   gh pr checks <N> --repo AuraHealth/IntelligenceService --watch   # waits; every check must pass
   gh pr ready <N> --repo AuraHealth/IntelligenceService
   ```

   A `403 Resource not accessible` on checks is a credential fault, not a CI verdict: retry with
   `env -u GITHUB_TOKEN -u GH_TOKEN gh pr checks …`.

5. **After the flip, look again; don't re-run step 1.** Wait mode sees the finished run on this head and
   returns at once, and `gh pr checks --watch` can return on the earlier green run before a new one is even
   created. So count the head's `Cursor Bugbot` runs before the flip, give a new one up to 5 minutes to
   appear, wait for it to finish, then list the unresolved threads (author `cursor`):

   ```bash
   R=AuraHealth/IntelligenceService; SHA=$(gh pr view <N> --repo $R --json headRefOid --jq .headRefOid)
   # filter=all: the default (latest) returns one run per name, so a second run would never raise the count
   runs() { gh api "repos/$R/commits/$SHA/check-runs?check_name=Cursor%20Bugbot&filter=all" --jq "$1"; }
   BEFORE=$(runs '.total_count')        # read this before `gh pr ready`
   # … gh pr ready …
   for i in $(seq 10); do [ "$(runs '.total_count')" -gt "$BEFORE" ] && break; sleep 30; done
   # "0" exactly: a failed read prints nothing, and nothing is never "done"
   until [ "$(runs '[.check_runs[]|select(.status!="completed")]|length')" = "0" ]; do sleep 30; done
   ```

   Any new thread goes through step 2, and the push sends you back to step 1.

Report the PR URL as ready only after step 5. Merging stays with a person.
