---
name: delete-coach
description: >
  Delete an Aura coach end to end from a Support request in Slack: find the coach id by
  name in coachDB, open the ENGBACK "Remove coach" card under the cycle's Support parent,
  then walk the runbook one checkpoint at a time — subscriptions and appointments, OnSched,
  the Firebase `deleted` flag and creator role, the subscription revoke, the CLI cleanup
  sweeps, BigQuery tracks, the Compensation sheet — verify, tick the card, reply in Slack.
  Trigger on a support Slack link about a coach wanting their profile removed together with
  "can you work on this", "can you delete this coach", "delete coach {name}", "remove coach
  {name}", "coach wants to delete their profile", or "remove this coach's account". Do NOT
  use to pause a coaching subscription (`eng:pause-coaching-subscription`), adjust credits
  (`eng:adjust-coaching-credits`), investigate a coach's uploads (`data:investigate-coach`),
  or delete a regular end user. This skill WRITES to production — every write is gated.
metadata:
  version: "1.4.0"
knowledge:
  reads:
    - Guide/Index                 # baseline routing aid (universal)
    - Tools/Slack                 # read the support thread, post the closing reply
    - Tools/Linear                # create and tick the Remove coach card
    - Tools/Aura API              # POST /subscriptions/revoke/{coachId}
    - Compliance/Privacy          # coach id + name go into Linear and Slack
---

# Delete Coach

Remove a coach who asked Support to delete their profile. The request arrives as a Slack
thread (usually in the support channel, from Customer Support, with screenshots of the
coach's email). The runbook is the ENGBACK Linear template **"Remove coach %coach name$"**;
this skill fills it in, walks it with the user one checkpoint at a time, and closes the loop.

**Work one checkpoint at a time.** Say what the next checkpoint is, do the read-only part
yourself, and for anything that writes either run it after the user's go or hand the user
the exact console link. Do not batch writes. Report each checkpoint's outcome in one or two
lines before moving to the next.

## Who does what

| Kind of step | Done by |
|---|---|
| Read-only SQL, RTDB reads, Slack/Linear reads | the agent, no confirmation needed |
| Linear card creation | the agent, after the user approves the plan (Step 3) |
| Firebase console edits (`deleted` flag, `role`) | **the user** — the agent gives the deep link, then verifies |
| Coach Compensation sheet edits | **the user** — the agent says exactly what to change |
| Revoke API call, Tools/CLI sweeps | the agent, **after an explicit go for that step** |
| Slack reply | the agent, after the user approves the text (Step 6) |

Never write SQL against a shared database from this skill. Never print a database URI —
read it into a shell variable and mask it (`sed -E 's#//[^@]*@#//***@#'`) if it must appear.

## Environment

- **Prod Postgres tunnel** on `127.0.0.1:5432`. Each service's URI is the **last**
  `POSTGRES_DATABASE_URI` line in `~/Development/Aura/Services/<Service>/.env` (the first
  line is the in-cluster IP). Databases used: `coachDB` (CoachService), `paymentDB`
  (PaymentService), `contentDB` (ContentService), UserService's DB.
  `URI=$(grep POSTGRES_DATABASE_URI <svc>/.env | tail -1 | cut -d= -f2-)`
- **Firebase RTDB** prod: project `firebase-auratech16`, database `auratech16`. Read via REST
  with `Authorization: Bearer $(python3 ${CLAUDE_PLUGIN_ROOT}/skills/delete-coach/scripts/gcloud_sa.py auth print-access-token ${GCP_KEY:+--scopes=https://www.googleapis.com/auth/firebase.database,https://www.googleapis.com/auth/userinfo.email})` on
  `https://auratech16.firebaseio.com/<path>.json`, with
  `-A "$(python3 ${CLAUDE_PLUGIN_ROOT}/skills/delete-coach/scripts/gcloud_sa.py --user-agent)"` so the
  read is labelled as ours in RTDB logs.
- **`AURA_USER_TOKEN`** (admin role) for the revoke call — `AURA_API_TOKEN` returns 403 there (wrong role).
- **Tools/CLI** at `~/Development/Aura/Tools/CLI`, Node `v24.19.0`. `nvm use` is unreliable —
  put `~/.nvm/versions/node/v24.19.0/bin` on `PATH` explicitly. The CLI calls
  `dotenv.config()`, so it **must run with that directory as cwd**. The user's `aura` alias
  is `node --max-old-space-size=12192 src/cli/commander.js`; it is not available to the agent.

## Step 1 — Read the request

Read the thread with `shared:read-slack` (thread fetch by channel + ts). Extract the
coach's name, the requester (to tag in the closing reply), and confirmation that the coach
agreed to losing their tracks — Support normally gets that in writing first. Support often
puts the coach's reply in a screenshot; when the name or the consent is only in an image,
ask the user to confirm it rather than reading the attachment outside the shared skill.

## Step 2 — Find the coach id

The coach id is the coach's Firebase UID, which is also their user id. Look it up in
coachDB — **not** `GET /coaches`, which lists only visible coaches and misses deletable ones:

Never put the name into a shell or SQL string — names carry quotes, and the shell holding
the prod URI must not evaluate them. Write the name to a scratch file with the Write tool
(not `echo`), then let psql read it and bind it:

```bash
psql "$COACH_URI" -X -A -v namefile="$SCRATCH/coach-name.txt" <<'SQL'
\set name `cat :'namefile'`
SELECT id, name, nickname, slug, approved, "deletedAt", "resourceId", "coachingEnabledAt",
       "coachingClientsCount"
FROM coach
WHERE length(trim(:'name')) >= 3
  AND (name ILIKE '%' || :'name' || '%' OR nickname ILIKE '%' || :'name' || '%'
       OR slug ILIKE '%' || :'name' || '%');
SQL
```

psql ignores a failed or empty `cat`, so the `length(trim(...)) >= 3` guard is what stops a
missing file from turning the filter into `ILIKE '%%'` and returning every coach. If the
query returns more than a handful of rows, the input was wrong — stop and check the file.

A substring match is a **candidate, never the answer** — a partial name can uniquely hit a
different coach. Show every candidate (id, name, slug, approved, `deletedAt`) and have the
user confirm which one is the coach in the request, even when there is a single row. No
match → try the sender name from the email screenshot, then ask the user (their fallback is
the Coach Compensation sheet, which has Coach Id, Name and Coach Email columns). Every later
step uses only the confirmed id. A coach already deleted has `deletedAt` set and an empty
`name`, so a re-run finds nothing by name — look them up by id instead.

## Step 3 — Plan, confirm, then create the Linear card

**Keep list first.** Check the coach's Firebase uid before any write:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/delete-coach/scripts/held_uids.py check <uid>
```
Exit 0: not listed, go on. Exit 3 (`skip <prefix>…: keep list`): a held account, stop; nothing about it
is deleted or written, and tell Security. Exit 2: no usable keep list, stop. The keep list is `~/.claude/held-accounts/held-uids.json` (or `HELD_UIDS_JSON`): a JSON array of
uids, mode 600, written by the hold owner, never in this repo. Without it (missing, empty, unreadable,
malformed or readable by others) a write is refused; never create or relax it to get a run through.

Before any write, show the plan and wait for an explicit go:

- the confirmed coach (name + id) and the Slack request it came from,
- the card that will be created (title, parent `Support [Cycle N]`, cycle),
- the checkpoint list below, marking which steps the user does in a console, which the
  agent runs after a per-step go, and which are skipped.

Only after the go, create the card. Use `shared:add-linear-issues` conventions with these fixed values (precedent ENGBACK-1375,
ENGBACK-1590):

| Field | Value |
|---|---|
| Template | ENGBACK `Remove coach %coach name$` (`6eabcb63-25d7-4bbc-9df0-ee1fa1d638b4`) — copy its description checklist |
| Title | `Remove coach <Name>` |
| Team | ENGBACK `e25556d5-9f7f-4758-8ac3-4505102aa550` |
| Parent | current PROD `Support [Cycle N]` — N from the **name** of `current-cycle --team 7e95fa4a-7510-447d-adc7-c663612e70c6`, then `find-issue --query "Support [Cycle N]"` |
| Cycle | ENGBACK current cycle (`current-cycle --team e25556d5-…`) |
| State | ENGBACK Todo `d8e59075-48c8-4f4e-bdd3-b35a1e02ad53` (the template's Backlog does not fit a cycle card) |
| Assignee | Jerome `18e32f71-e4f4-4d35-a378-37825d2b21c8` |
| Project | Aura - Core App `a13d07fd-f427-4f35-a5af-fba68b015f95` |
| Label | Support `d9aab87b-749a-4991-ac29-1bf0cebf5da3` |
| Estimate / priority | 3 / High (2) |

Description: first line `User id: \`<coachId>\``, then a line linking the Slack request,
then the template checklist. Attach the Slack URL with `attach-url`.

## Step 4 — The checkpoints

Template order. Checkpoints the template marks "(automated)" are verified in Step 5 only.

1. **Coaching subscriptions and appointments** (read-only). All three must be 0; otherwise
   stop and ask the user how to handle the clients first.
   - coachDB: `SELECT count(*) FILTER (WHERE start > now()) FROM appointment WHERE "coachId"=$id`
   - coachDB: `SELECT count(*) FROM "coachingSubscription" WHERE "coachId"=$id`
   - paymentDB: `SELECT count(*) FROM "coachSubscription" WHERE "coachId"=$id AND "endedAt" IS NULL`
2. **OnSched account.** CoachService deletes it through `coach.resourceId`. Empty
   `resourceId` → no OnSched account, mark the item done with "nothing to delete". Otherwise
   stop and ask the user — the delete goes through the OnSched Postman request on the card.
3. **Set `deleted` for the coach** — the user does it in the console. Give the link
   `https://console.firebase.google.com/project/firebase-auratech16/database/auratech16/data/~2Fcoaches~2F<coachId>`
   and ask them to add `deleted: true`. Then read `coaches/<coachId>/deleted` back; it must be `true`.
4. **Remove the creator role** — the user deletes the key (not set it to another value) at
   `…/data/~2Fusers~2F<coachId>~2Frole` (current value `"creator"`). This emits `roleUpdated`;
   UserService sets `isCoach = false` and unsets the Mixpanel `Role`. Read it back: `null`.
5. **Revoke the subscription.** Coaches hold a free lifetime content subscription
   (`premiumlifet_free`, code `COACH-…`). After the user's go:
   ```bash
   curl -A "$(python3 ${CLAUDE_PLUGIN_ROOT}/skills/delete-coach/scripts/gcloud_sa.py --user-agent)" -s -w '\nHTTP %{http_code}\n' -X POST \
     -H "Authorization: Bearer $AURA_USER_TOKEN" -H 'Content-Type: application/json' \
     -d '{"type":"content","reason":"Coach account deleted on request (<card id>)"}' \
     https://services.aurahealth.io/subscriptions/revoke/<coachId>
   ```
   Expect `204`. Verify: paymentDB `subscription` row for the user has `deletedAt` set, and
   `subscriptionEvent` has a new `ended` row.
6. **Channel `deleted` (automated)** and 7. **Content `deleted` (automated)** — triggered by
   step 3. Verified in Step 5.
8. –10. **Cleanup sweeps** — `cleanupContents`, then `cleanupChannels`, then
   `cleanupCoaches`. These sweep the **whole** prod catalog, not one coach, so get the
   user's go once for the three. Run each in the background, sequentially, logging to the
   scratchpad, and check the exit code and `grep -ci error` before starting the next:
   ```bash
   cd ~/Development/Aura/Tools/CLI && export PATH="$HOME/.nvm/versions/node/v24.19.0/bin:$PATH" \
     && LOGGER_LEVEL=info DO_UPDATE=true node --max-old-space-size=12192 \
        src/cli/commander.js cleanupContents > "$SCRATCH/cleanupContents.log" 2>&1; echo "exit $?"
   ```
   `cleanupCoaches` should log `Backed up deleted coach '<coachId>'` — that is the coach
   node being archived and removed from RTDB.
   If the auto-mode classifier refuses the run, give the user the same three commands with
   their `aura` alias and wait for them to report back.
11. **Algolia indices** — skipped by decision; annotate the item "skipped (not needed)".
12. **BigQuery tracks table** — `autoTagContent` with the same CLI invocation, after the
    sweeps, with `--max-old-space-size=24576` (12 GB runs out of memory while loading).
    Known issue (2026-10-14): at 24 GB it loads, then throws `Cannot convert undefined or null
    to object` in step 6 "Add tags based on rules". That is before its write block, so it
    writes nothing and the coach deletion is unaffected. Until the CLI is fixed, leave this
    item unticked with that note rather than retrying.
13. **Coach compensation** — no listens check, always done. The user edits the
    [Coach Compensation Analytics](https://docs.google.com/spreadsheets/d/1na8e_cCTpLm1hHuRN-2nL6C_x1hMtDHD3esp8HlELeU/edit)
    sheet, on the **previous month's** tabs (the last completed analytics month):
    - `compensation <YYYY-MM>`: fill the coach's row orange and add a note on the Coach Id
      cell (column A): `Deleted on MM/DD`.
    - `payout <YYYY-MM>`: delete the coach's row.

## Step 5 — Verify

All read-only. Every line must hold before closing:

| Check | Expected |
|---|---|
| RTDB `coaches/<coachId>` | `null` (archived by `cleanupCoaches`) |
| RTDB `users/<coachId>/role` | `null` |
| contentDB `SELECT count(*) FROM content WHERE "authorId"=$id AND "deletedAt" IS NULL` | 0 |
| contentDB `SELECT count(*) FROM channel WHERE "coachId"=$id AND "deletedAt" IS NULL` | 0 |
| paymentDB `subscription` for the user with `"deletedAt" IS NULL` | 0 |
| UserService `user`: `role`, `"isCoach"` | empty, `false` |
| coachDB `coach."deletedAt"` | set |

A failed check goes back to its checkpoint; do not close the card over it.

## Step 6 — Close

1. Tick every completed checklist item on the card (`- [ ]` → `- [x]` via `issueUpdate`),
   annotating skipped ones (`— skipped (not needed)`, `— no OnSched resource, nothing to
   delete`). Leave an item unticked with a reason if it failed.
2. Show the user the reply text, and after their go post it in the request thread through
   `shared:post-to-slack` (`post --channel <id> --thread-ts <ts> --as-user`, so it reads as
   the engineer answering Support, with the script-built footer):
   > `<@requester>` Coach `<Name>` has been deleted: profile, tracks, creator role and
   > subscription are all removed. Thanks!
3. Report to the user: card link, Slack reply link, anything left open.

## Gotchas

- `GET /coaches` returns ~465 visible coaches and ignores `offset` — useless for finding a
  coach to delete.
- The revoke route is role-guarded (`admin`/`service`); the service token 403s, the admin
  user token works.
- `appointment` has a `start` column, not `startAt`.
- The CLI's `.env` is read from the cwd; running it from elsewhere silently loads nothing.
- The sweeps write to prod (`DO_UPDATE=true`); never run them with that flag unconfirmed.
