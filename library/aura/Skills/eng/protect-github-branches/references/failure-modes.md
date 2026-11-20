# Protection failure modes

Five configurations that a **presence check** scores as protected and a **shape check**
catches. Every one was found live in the AuraHealth org on 2026-08-04, in an org that
believed it had branch protection.

| # | Configuration | What it actually permits | How to spot it |
|---|---|---|---|
| 1 | `enforcement: disabled` | everything — the ruleset is inert | `enforcement != "active"` |
| 2 | Active, but `rules: []` and `ref_name.include: []` | everything — targets no branch, enforces no rule | empty `rules` or empty `include` |
| 3 | `rules: [deletion]` only | **force-push** | `non_fast_forward` absent |
| 4 | `bypass_actors: [{RepositoryRole 5, always}]` | everything, for admins | `bypass_actors` non-empty |
| 5 | Classic protection, `enforce_admins: false` | force-push + review bypass, for admins | `enforce_admins.enabled == false` |
| 6 | A ruleset targeting only tags or a release branch | everything on the default branch | `ref_name.include` does not reach `refs/heads/<default>` |
| 7 | `pull_request` rule but no `required_status_checks` | **merging a red build** | `required_status_checks` absent from `rules` |

Case 2 is the nastiest: setting `enforcement: active` on it looks like a fix, reports
success, and changes nothing — the ruleset still targets no refs and carries no rules.

Case 3 explains an observation that otherwise looks contradictory: two repos "had branch
protection" and were still force-pushed by an outside account. They blocked *deletion*, not
force-push.

Case 7 is the quiet one. A `pull_request` rule routes every change through a PR, so CI runs
and its result is visible — which reads as enforcement. It is not: nothing consults the
result, and a red build merges on a green button. An org can require PRs on every repo and
still have no repo where failing tests stop a merge.

## The audit predicate

A repo is protected only when **all** hold:

```
the ruleset's ref condition REACHES the default branch
   include ∋ ~DEFAULT_BRANCH | ~ALL | refs/heads/<default>,  and exclude does not
enforcement == "active"
rules        ⊇ {deletion, non_fast_forward}
bypass_actors == []
```

### Why glob patterns are reported, not resolved

`ref_coverage()` returns `covers` / `no` / `uncertain`, and anything containing `*`, `?` or
`[` is `uncertain` — treated as unprotected and surfaced for a human.

This is deliberate. An earlier version reimplemented GitHub's glob grammar and every review
round found a defect in it: `*` was first matched as a literal, then via Python `fnmatch`
(where `*` crosses `/` — **more permissive than GitHub**, so a ruleset GitHub would not
enforce on the default branch read as `PROTECTED`), then bracket classes were missing. Each
fix was inferred from a counterexample rather than a spec, and GitHub can change the grammar
underneath us.

A survey of the org settled it: **96 of 96 rulesets use `~DEFAULT_BRANCH` and none contains a
glob.** The grammar bought nothing real and cost three rounds of defects. Reporting the
undecidable case errs toward flagging a gap, which is the direction this tool must fail in.

Evaluate **every** ruleset covering the default branch, not the first one returned:
`GET /repos/{o}/{r}/rulesets` is unordered and includes inherited org/enterprise
rulesets, so a junk, tag-scoped, or parent ruleset can mask a good one — or become the
one a naive `apply` rewrites. Only a `Repository`-source ruleset may be mutated.

For classic protection: `allow_force_pushes == false` **and** `enforce_admins == true`.

## Required status checks

### An unreported context is worse than a failing one

A context that reports `failure` blocks the merge — the intended behaviour. A context that
never reports leaves the PR at *"Expected — waiting for status to be reported"*. GitHub
applies **no timeout**, and with `bypass_actors: []` no one, admin included, can merge past
it. The only remedy is editing the ruleset back out.

So the dangerous direction is not requiring too strict a check; it is requiring a name that
nothing publishes — a renamed job, a workflow gated behind a path filter, a repo that never
adopted that CI. `apply` therefore refuses to install a context it has not watched report on
every one of the most recent merged PRs (`--check-sample`, default 10), and reports the
refusal as `skip-coverage` rather than skipping quietly.

A ruleset context matches **either** a commit status **or** a check-run name, so coverage
must be measured against both — and **both endpoints stop at 30 items by default.** A context
dropped by truncation looks exactly like one that never ran, so the gate would exclude a repo
that is in fact covered. `commit_contexts()` paginates `/statuses` and pages `/check-runs`
until `total_count` is satisfied.

`strict` is part of the shape, not a detail: a repo carrying every requested context with
`strict` off has not yet had a requested off→on upgrade applied, so `conforms()` weighs it.
Otherwise `--strict-checks` is a silent no-op on exactly the repos it is aimed at.

### Sample by `merged_at`, not `updated`

`GET /repos/{o}/{r}/pulls` sorts by `created` or `updated` — never by merge date. On closed
PRs `updated` ranks by last comment, so the sample drifts toward old PRs and a repo that
adopted the check recently looks uncovered. Measuring one repo that way gave 1/10 where the
true figure was 10/10 — a false alarm that would have excluded the busiest repo in the org.
Fetch a wide page of closed PRs and sort by `merged_at` locally.

### Contexts inherited from classic are gated too

When `apply` seeds a ruleset on a classic-protected repo it carries classic's contexts across,
because `migrate` later deletes classic and anything not carried is lost. Those inherited
contexts get the **same** coverage gate as the requested ones, and the reason is the bypass
change: classic can permit admin bypass (`enforce_admins: false`), so a context that no longer
reports was merely annoying there. Under a ruleset with `bypass_actors: []` it is absolute, and
nobody can merge. The gate must therefore measure the contexts already present *in the ruleset*,
not the ones `inspect()` reports — on a classic repo the latter includes classic's own, which
would exempt exactly the set that needs checking.

A refusal names which failing contexts came from classic, since those have to be fixed or
removed there first.

### `integration_id` bindings survive

A required check may be `{context, integration_id}`, pinning the name to one app. Rebuilding the
rule from context strings drops the binding, so any provider can then satisfy the name — a
downgrade, and an invisible one. `merge_status_checks()` carries existing entries verbatim, and
classic's `checks[].app_id` maps to `integration_id` rather than being flattened to a bare
context.

### Merging contexts is not a downgrade

Adding a context to a repo that already requires another **unions** the two, and `strict`
only ever moves off→on. Replacing the set, or dropping `strict`, would be a downgrade and is
refused — the same rule that protects an existing `pull_request` approval count.

## "Automatically delete head branches"

`delete_branch_on_merge` is a repo setting, not a rule, and two API behaviours make it
easy to misread:

- **The org listing omits it.** `GET orgs/{org}/repos` returns no `delete_branch_on_merge`
  key at all, so reading it from the listing reports every repo as unset. The audit does
  one `GET repos/{owner}/{repo}` per repo instead.
- **It is admin-only.** For a caller without admin on the repo the field is absent from
  the full repo object too. That is reported as unknown (`?`), never as on.

`apply` verifies the write from the `PATCH` response: a value that does not read back
`true` is `FAILED-VERIFY`, whatever the status code.

## Reading posture: rulesets vs classic

`GET /repos/{o}/{r}/branches/{b}/protection` reports **classic** branch protection only. On a
branch protected by a ruleset it returns `404 Branch not protected`, which reads as "no
protection" and is wrong. Auditing an org this way scored 32 of 39 repos unprotected when 38
of 39 were enforced.

| Endpoint | Sees classic | Sees rulesets |
|---|---|---|
| `branches/{b}/protection` | yes | **no** |
| `rules/branches/{b}` | yes | yes |
| `rulesets` + `rulesets/{id}` | no | yes (incl. inherited) |

`inspect()` reads `rulesets` first and falls back to classic. Anything working outside this
script should use `rules/branches/{b}` — the effective-rules endpoint — which resolves both
in one call.

## Why the API can lie about the answer

Two behaviours turn a failed check into a clean-looking result:

- **A `--jq` parse error empties stdout.** `gh api` exits non-zero, but the common
  wrapper shape `return stdout if returncode == 0 else ""` collapses the failure onto
  the same value as a legitimate empty result. A sweep once reported "0 infected across
  35 repos" because the jq regex used `\.` where jq requires `\\.` in a string literal.
  Distinguish *error* from *empty*; do not just check the return code harder.
- **Do not measure exit codes through a pipe.** `cmd | head; echo $?` gives *head's*
  status. Use `${PIPESTATUS[0]}`. Getting this wrong is how the bullet above was first
  written up as "gh exits 0", which is false.
- **The secondary rate limit 403s while `/rate_limit` still shows ~5000 remaining.**
  It is a concurrency limiter. A helper that treats a failed fetch as "nothing found"
  reports clean.

`scripts/gh_api.py` fails closed on both. Any non-200 becomes an error, and the run is
reported **incomplete** unless the error count is zero. Never read a zero-gap result from
a run whose error count is non-zero.

## What breaks when you apply protection

- **fastlane `match` certificate repos** push directly and non-interactively and cannot
  open a PR. A `pull_request` rule breaks iOS code signing at the next certificate
  rotation — months later, far from the change that caused it. `protect.py` auto-excludes
  repos matching `cert|fastlane|match` in `apply`/`migrate`; override with
  `--include-cert-repos` only if you have verified nothing automated pushes there.
- **Any bot or CI identity** that pushes to a default branch. `preflight` lists 90 days of
  pushers per repo and flags non-human actors as blockers.
- **Locked forks.** GitHub returns `403 Repository has been locked` on ruleset creation for
  some forks. Nothing to do; report and move on.

## Migration ordering

Create the ruleset **first**, verify it, and only then delete classic protection. Both
apply briefly, which is strictly stronger — there is never a window where neither does,
and a failed verify leaves the old protection intact.

Translate classic faithfully or you silently weaken the repo:

| Classic | Ruleset rule |
|---|---|
| `required_pull_request_reviews.required_approving_review_count` | `pull_request.required_approving_review_count` |
| `required_status_checks.contexts` + `.strict` | `required_status_checks.required_status_checks` + `strict_required_status_checks_policy` |
| `required_linear_history.enabled` | `required_linear_history` |
| `enforce_admins: true` | `bypass_actors: []` |

**Never downgrade.** If a `pull_request` rule already exists, leave it untouched rather
than overwriting it with a lower approval count.

## Ref enumeration

Where the job needs refs rather than repo settings, use `git ls-remote` — it returns the
full ref advertisement and costs no REST quota. The REST `/branches` endpoint cannot see
`refs/pull` at all, which on one org was 15,158 of 38,377 refs.
