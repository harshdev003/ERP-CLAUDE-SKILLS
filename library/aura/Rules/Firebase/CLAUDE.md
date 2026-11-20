# Firebase

This repo holds the **Realtime Database security rules** for Aura: one file, [`rules.json`](rules.json), with about 237 top-level locations. The same file is deployed to both databases:

**Security review:** `.claude/rules/security.md` is the checklist every reviewer applies before approving a PR: Claude, Cursor (`.cursor/rules/security.mdc`), BugBot (`.cursor/BUGBOT.md`) and people. All three are copies of AI Knowledge → Compliance → Security → Code Review Checklist, identical in every Firebase rules and Functions repo. Change that page first, then every copy.

| Env | Firebase project | Database URL |
|---|---|---|
| dev | `aura-dev` | `https://aura-dev.firebaseio.com` |
| prod | `firebase-auratech16` | `https://auratech16.firebaseio.com` |

**`master` is the source of truth.** The live dev and prod rules must equal `master`. Any difference means someone edited a rule in the console, so reconcile it before changing anything.

There is no CI, no `firebase.json` and no test harness in this repo yet. Deploys are manual (see below).

## Where each rule is documented: Notion

**Compliance → Security → [Firebase Rules](https://app.notion.com/p/3df57100ed3649dcb143b48c8dd9c411)** (data source `collection://04f11af1-a3f3-4e10-ac13-04967d5ea1d3`) has **one row per location**, with these columns:

- Category
- Current rule, Current read, Current write
- Target rule, Target rule detail
- Apps and Channels that read or write it, Feature, Client code
- Server users (Admin SDK)
- Risk, Status (`To do` → `In review` → `Dev` → `Prod` → `Verified`), Card, Notes, Last reviewed

The parent [Security](https://app.notion.com/p/3f175e23af208106a720d0f2fd1c5388) page holds the principles and rollout discipline.

- **Read a location's row before changing its rule.** It lists who reads and writes the location, so you know what will break.
- **Update the row in the same change.** That means Current rule, Current read/write, Status, Card, Notes and Last reviewed. Notion and `rules.json` must not drift.

## Context: the October 2026 security incident

[PROD-6926](https://linear.app/aura-health/issue/PROD-6926) followed from `/users` being world-readable. Since then:

- Every rule change goes through a Linear card under PROD-6926, with the card id in the PR **body**.
- In the PR **title**, only put a card id when merging that PR completes the card.
- The target state for user data is **owner only**: `auth.uid === $userId`. Never add a `auth.token.userId` branch to a rule.
  - `userId` was a custom claim stamped by the FirebaseAuth `authUserClaim` blocking function, because several Auth accounts, one per provider, could share one profile.
  - It is retired: stamping stopped in FirebaseAuth v1.2.6, v1.2.9 removed the code, a full prod scan on 2026-11-17 found no account carrying it, and Firebase#190 removed every claim branch from `rules.json`. *Why:* a claim branch can no longer match anyone, so it only hides which rule is really in force.

### Rules semantics that have bitten us

**Read [`docs/rules-reference.md`](docs/rules-reference.md) before writing a rule.** It covers the cascade, `.validate`, every rule variable, the patterns we use and how to test them. The points below are the ones that caused real problems.

- **`auth != null` still means *anyone*.** Anonymous sign-in has been off in both projects since 2026-10-30 (ENGBACK-2085), so no new anonymous accounts are created. But existing anonymous accounts keep refreshing their ID tokens, and sign-up is open, so a signed-in caller is not a trust boundary.
- **`.read`/`.write` can be set at any level, but grants cascade downward and cannot be revoked deeper.** A grant at a parent applies to every child; a child rule can only grant more. Keep parents grant-free and put grants on the children.
- **A `.read` on a parent allows a shallow read, which lists every child key.** That is how about 7.3M uids were enumerated from `/users`. Keys that encode data, such as `/emailUser` (`base64(email)`), leak that data through listing.
- **`.validate` is skipped at a location being deleted.** A field can't guard its own deletion. An ancestor's `.validate` still runs while the ancestor keeps data, so it can block a child delete (Firebase#131, `role`). Deleting the guarded node itself needs a `.write` guard, e.g. `... && newData.exists()`; side effect: removing a node's last child is then blocked. Verified in the emulator.
- **The Admin SDK bypasses rules.** Backend services and Cloud Functions are unaffected by any rule change. Only client SDK traffic (mobile, web, desktop) is.

## Changing a rule

1. **Find every client that touches the location.** Use the Notion row, then grep the client repos for the path: Aura mobile, Aurie iOS, UserWeb, CoachWeb, CRMWeb, AIWeb, desktop. Note what each one reads or writes, and whether it happens **before sign-in**.
   - Search for **any mention of the node name, case-insensitive**, not only quoted paths: a path built from a constant or string concatenation slips past a quoted-path grep. Cover every client repo, InvestWeb and AIDesktop included, and every live app branch, not just the default one. Say in the PR which repos and branches were searched.
   - Mobile users on old app versions keep the old access pattern until they upgrade. Tightening a rule that an old build depends on needs an adoption window or a forced-upgrade floor. Find them in git history: the last app version whose code read the node, and how many active users are still on it or older. "No current code reads it" is not enough (Firebase#173: allYearlyQuotes, last used by the app around 2024-02, still read by an old Android build).
2. **Confirm live equals `master`.** Fetch the live rules for both databases with the Admin SDK `getDatabase().getRules()` and diff against `master`. This reads rules only, never data.
3. **Branch in a worktree:** `git worktree add .claude/worktrees/<slug> -b <firstname>/<card>-<slug> origin/master`. Edit `rules.json`.
   - **Diff the parsed rules, not the text.** Load `rules.json` from your branch and from `master` as JSON, list every path whose rule changed, and check that the list is exactly the locations you meant to change. Say in the PR how many locations changed. *Why:* many locations carry character-for-character identical rules, so a find-and-replace edit hits all of them. In Firebase#166 the `activities` change also rewrote `smGoals`, which would have blocked the app's goal updates; BugBot caught it, and the parsed check then showed only the 8 intended locations.
4. **Emulator-test the change and a control.**
   - Use `@firebase/rules-unit-testing` with the database emulator: `firebase emulators:exec --only database --project demo-<x>`.
   - Cover signed-in, anonymous and unauthenticated clients, every write form (`set`, `update`, `remove`, `push`, a root multi-path `update`) and reads.
   - **Cover the first client write on a missing id, once per client writer, when no server creates the record.** Before adding an `exists()` or edit-existing-only condition, read the location's Notion row Notes and grep the server repos for RTDB writers of the path. Firebase#162 required the record to exist and blocked the first `appointments` write on prod for 18 minutes (ENGBACK-2181), as #51 had before.
   - Check that the Admin SDK (`withSecurityRulesDisabled`) still works.
   - Run the same suite against `master` as a control, so the tests prove the change.
5. **PR:** open it as a draft, comment `bugbot run`, fix the findings, then mark it ready. Put the test results in the PR body.
   - **Lead the PR body (and the card) with a plain-language table** of what each kind of caller can do before and after, one row per attempt:

     | Someone tries to… | Today | New rule |
     |---|---|---|
     | Delete all coaches | allowed | blocked |
     | Change their own preview background | allowed | allowed |

     *Why:* Jerome could not follow a rule diff on its own ("I don't understand what the rule does here"), and called this format "very good and easy to understand what we are trying to solve". A rule shipped in steps gets one column per step.
6. **Keep `master` deployable.** A merged change that can't ship yet (for example an owner-only rule waiting on the `userId` claim backfill) is **reverted from `master`** and re-applied in a new PR when it can ship — never skipped by deploying a hand-picked subset. Re-fetch `origin/master` right before deploying: a PR merged minutes before a deploy can miss it.
   - *Why:* on 2026-10-15 a merged rule (#42) reached neither database because it was merged the same minute another one was deployed, and a later deploy found `master` holding a change that would have locked out users without the claim; it was reverted (#48) so the release shipped only the intended key.
7. **Deploy after merge:** dev first, then prod. Push the exact `master` file with the Admin SDK `getDatabase().setRules(fs.readFileSync('rules.json','utf8'))`, using the project's service account. Re-fetch afterwards and diff: live must equal `master`, and only the intended keys may have changed. Keep the pre-deploy copy for rollback.
8. **Monitor prod.**
   - Watch the Cloud Functions that write the location for errors.
   - Watch for client permission errors: Bugsnag, and `permission_denied` in project logs.
   - RTDB itself has no request logs. Usage is only visible in Monitoring metrics (`network/api_hits_count`, `io/database_load`, `rules/evaluation_count` by `result`).
   - To see **which paths** are denied, use the skill **`eng:profile-firebase-rtdb`** (AINative), with `--watch` set to the nodes you changed. It profiles N minutes and prints, counts only:
     - denied operations by path and client;
     - every attempt to read the root or a whole top-level node, with scanner probes on guessed names (`/clients`, `/sms`) flagged;
     - allowed and denied counts on the watched nodes;
     - Cloud Monitoring denies for the same window.

     It deletes the raw log, which holds IPs and keys, and says `PROFILE FAILED` rather than reporting a clean run when the CLI can't connect. Profile again after each deploy, and hourly for a day after a batch.
9. **Record the change** in these places:
   - the Notion row;
   - the Linear card;
   - the rules section of AI Knowledge → Tools → Firebase (its change log and anything the change makes stale);
   - a row in the **Security Change Log** (AI Knowledge → Compliance → Security), with the setRules times for dev and prod;
   - a message to the **"Security findings from PenTestBot"** Claude session.

Example of the whole flow: [AuraHealth/Firebase#1](https://github.com/AuraHealth/Firebase/pull/1) (`/emailUser` `.write: false`, [ENGBACK-1471](https://linear.app/aura-health/issue/ENGBACK-1471)).

## Reviewing all rules

A review of the whole tree runs as a loop, one location at a time, each change through [Changing a rule](#changing-a-rule):

1. **Find where each node is used today**: every client and server that reads or writes it, recorded on its Notion row.
2. **Tighten as far as possible with no app or web change.** Phase 1, [ENGBACK-2192](https://linear.app/aura-health/issue/ENGBACK-2192), covered all 237 nodes.
3. **Change the app or web, then apply the stricter rule** once the change has shipped and old builds allow it. Phase 2, [ENGBACK-2223](https://linear.app/aura-health/issue/ENGBACK-2223).
4. **Re-run the loop when new nodes or clients appear.**

Two decisions apply to every location in the loop (Jerome, 2026-11-17):

- **A node no client reads or writes gets `".read": false, ".write": false`.** Backend writers are unaffected: the Admin SDK bypasses rules. *Why:* the review found nodes still open to every signed-in user that only a backend or a Cloud Function touches.
- **No client may read a whole top-level node.** Grant on the children (`$id`), never on the node itself, even when an old Android build still reads the whole node; those users have to update. *Why:* a whole-node or shallow read lists every key, which is how `/users` was enumerated.

Read [`docs/rules-reference.md`](docs/rules-reference.md) for the rule patterns each step uses.

## Safety rules

- **Never read `/users` itself on the prod database:** not the whole node, no listing, no query, no shallow read, not even `limitToFirst=1`. The root needs Jerome's OK too. An exact `/users/{id}` read of one known id is fine. Print only whether it exists, or field names, never values, and send the `AuraClaude` user agent. Otherwise inspect data in the Firebase console, and test with your own uids and test accounts only (Jerome, 2026-10-24).
- **Never use shallow reads (`?shallow=true`) on the prod database.** They slow Firebase down for everybody. Read the exact path you need, and check existence by reading that exact path; listing a large node's keys needs Jerome's OK first (Jerome, 2026-10-24).
- Never deploy rules to prod that aren't merged to `master`.
- Don't edit rules in the Firebase console. If an emergency forces it, back-port the change to `master` the same day.
- **Keep the aura-dev Auth sign-in settings the same as prod** (providers, anonymous sign-in). Read prod first, then set dev to match and read both back. *Why:* a dev test is only evidence about prod when dev signs people in the same way (Jerome, 2026-11-17, on anonymous sign-in).
