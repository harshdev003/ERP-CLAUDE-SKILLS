---
name: check-one-auth
description: >
  Check one person against the ONE auth rules: one Auth (Firebase Auth account) per email, its
  id equal to the Profile id, and the Profile email equal to the Auth email and every Sign-in's
  email. Takes a Firebase uid, a profile id or an email; reads Auth, the exact RTDB
  profile fields and the userDB row, read-only; prints verdict codes with their rule ids and the
  next step, masked emails and uid prefixes only. Trigger on "check one auth for", "is this
  person ONE auth", "does this email have one account", "check auth email matches profile",
  "why does this user have two accounts", or "check one auth email to one profile". Do NOT use to
  merge or change accounts (the ONE auth batches run elsewhere, on Jerome's go), to disable an
  account (eng:disable-user-account), or for counts across all users.
metadata:
  version: "1.3.0"
knowledge:
  reads:
    - Guide/Index        # baseline routing aid (universal)
    - Tools/Firebase     # Auth accounts, the userId claim, RTDB /users
    - Tools/Aura API     # UserService GET /users/{id}
    - Compliance/Privacy # masked emails and uid prefixes only
---

# Check One Auth

## Goal

For one person, say whether ONE auth holds and, if not, which rule applies and what happens next.
The rules are the ONE auth doc on Linear, attached to ENGBACK-2048 ("ONE auth: rules, examples,
test cases and dev scenario"). Every verdict line names its rule id.

**Terms** (approved, the ONE auth doc): **Profile**; **Auth**, a Firebase Auth account;
**Sign-in**, Google, Apple, Facebook or Password on an Auth; **Main Auth**, the Auth whose id is
the Profile id; **Extra Auth**, any other Auth reaching the Profile; **Mapping**, the email
claim (`userId`) link: an Auth reaches a Profile by its own id or by the claim, nothing else; **Empty Auth**, an Auth with no Sign-in and no email.

## Rules

- **Read-only.** It changes nothing in Auth, RTDB or userDB.
- **Exact paths only.** Auth: `accounts:lookup` for one uid, `accounts:query` for one email. RTDB:
  `/users/{id}/email`, `/users/{id}/deletedAt` and `/users/{id}/role`, never `/users/{id}` whole,
  never a listing or a shallow read. `/emailUser` is never read: that node is being deleted, and the
  helper refuses it. userDB: UserService `GET /users/{id}`, and `POST /users/email/lookup` (the email
  in the body) for the live Profile that holds an email. Every HTTP call carries the AuraClaude user agent.
- **RTDB through the Admin SDK realtime protocol, never REST** (Jerome, 2026-11-11). The prod
  "RTDB REST read surge" alert counts REST gets only, and a run over many people tripped it at 03:49Z.
  The bundled `scripts/rtdb_realtime.js` holds one realtime connection and reads one exact path at a
  time (`getDatabase().ref(path).get()`); both it and `check.py` refuse any other path. A realtime
  connection carries no user agent, so the Firebase app is named with the AuraClaude label instead;
  the profiler still shows these reads as admin from our IP. Checking Firebase Auth and RTDB state
  is this skill's job. Its reads stay within eng:read-rtdb-paths' limits (exact paths, at most 20
  in flight), and a large run is announced before it starts. A question about many accounts that
  doesn't need Firebase state is answered with SQL on UserService's Postgres (ask its owner).
- **Output is masked.** Emails as `j***@g***.com`, uids as 6-character prefixes, providers and their
  trust, dates. Never a full email, a full uid, a token or a hash, whoever asks.
- **Held people stop.** If any uid involved is in `~/.claude/held-accounts/held-uids.json` (read
  fresh each run, never printed), the output is `HELD` and nothing else. For `--uid` the check runs
  before any read; for `--email` it runs after the Auth reads and the userDB email lookup and before anything
  is printed or any Profile is read.

## Step 0 — Prerequisites

`GCP_KEY` (Identity Toolkit through the bundled `gcloud_sa.py`, and the RTDB reads), `node`, and
the Aura CLI checkout whose `node_modules` has `firebase-admin`: name it with `AURA_CLI_DIR` or
`--cli-dir` (there is no default; the RTDB read fails, exit 2, without it). Plus `AURA_API_TOKEN`
(UserService). Without the token the userDB column says
`AURA_API_TOKEN unset`, the Profile comes from RTDB alone, and the person is never called `OK`: the
verdict is `NOT_CHECKED` instead. Any other UserService failure (not a 404) is a failed read, exit 2.

## Step 1 — Run it

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/check-one-auth/scripts/check.py --email "<email>"
python3 ${CLAUDE_PLUGIN_ROOT}/skills/check-one-auth/scripts/check.py --uid "<uid or profile id>"
```

It prints the Auths, each marked Main, Extra, Empty, or reaching no Profile (prefix, masked email, verified, disabled,
whether the claim Mapping is set, last sign-in, each Sign-in with its trust), the Profiles reached
(by id, by the claim, and the live Profile whose userDB email is this one: live, deleted or missing,
masked email, whether a role is set), the Profile holding the email in userDB, then the verdicts. Exit 2 means a read failed: say which, and never report the person as OK.

## Verdicts

Several can apply; all are listed.

| Code | Rule | Meaning |
|---|---|---|
| `OK` | R1, R2 | One Auth carries the email, it is the Main Auth, and all emails match |
| `NOT_CHECKED` | R1, R2 | Would be OK, but the userDB row wasn't read (`AURA_API_TOKEN` unset) |
| `DUPLICATE_ACCOUNTS` | R3, R5 | 2+ Auths carry the email; names the Profile R3 would keep |
| `CLAIM_ONLY` | R4, ENGBACK-2485 | An Extra Auth reaches its Profile only through the claim Mapping |
| `PROFILE_EMAIL_EMPTY` | R13 (P6, P7) | `copyable`, `taken-live`, `deleted-only`, `no-email`, or `unchecked` (no `AURA_API_TOKEN`: never called copyable) |
| `ONLY_SIGNIN_EMAIL_DIFFERS` | R15 case 1, R7 (E1, P6, P7) | The only Sign-in carries another email; sub-type and `free` / `taken-live` / `deleted-only` / `unchecked` |
| `OTHER_EMAIL_SIGNIN` | R15 case 2, R5, R8 | A Sign-in on another email beside one on the Profile email; a Password here is UNCONFIRMED |
| `NO_EMAIL_SIGNIN` | E5 | A Sign-in with no email; left alone |
| `ANONYMOUS` | R13 note | An Auth with no Sign-in (an Empty Auth when it has no email either); left alone |
| `ORPHAN_ACCOUNT` | R10 | The Auth reaches no live Profile |
| `PROFILE_DELETED` | R10 | A Profile reached is deleted |
| `ACCOUNT_AT_PROFILE_ID` | P1 | A Main Auth sits at the Profile id outside the email's group |
| `LEGACY_UID` | P2 | The Profile id contains `:` |
| `UNTRUSTED` | R6, ENGBACK-2455 | Unverified Password, Facebook, or Google on a non-Gmail domain |
| `STAFF_OR_TEST` | P8 | A role is set, or the email is @aurahealth.io or starts with `test+` |
| `CASE_VARIANT` | R1 | The Profile email has uppercase letters or surrounding spaces |
| `AUTH_EMAIL_DIFFERS` | R1 | The Auth's own email differs from the Profile email while every Sign-in matches |
| `SIGNINS_NONE_MATCH` | R1, OPEN | 2+ Sign-ins, none on the Profile email; no rule yet |
| `NOT_ALIGNED` | R1 | Last resort: not aligned in a way no other verdict covers |
| `EMAIL_ON_2PLUS_PROFILES` | R3, R12 | 2+ live Profiles have this email |

R3's kept Profile is picked by last use, then most Auths reaching it, then the oldest Auth, then
the lowest id. **Paying isn't checked** (the skill reads no payments), and the line says so.

## Step 2 — Report

Lead with the verdicts and their next steps, then the accounts and profiles as printed. Anything
OPEN (P1, P2) or needing a decision goes to the SecurityAuth session, not to a fix.

## Edge cases

- **Provider emails aren't searched.** `accounts:query` is assumed to match the top-level email
  only (not yet verified on aura-dev). An Auth that holds the email only as a Sign-in's provider
  email isn't found from `--email`, and `DUPLICATE_ACCOUNTS` can miss it. Starting from that Auth's
  `--uid` does find it.

- **Taken-live comes from Auth and the userDB email lookup.** The lookup matches the email
  case-insensitively among live Profiles and returns one. `deleted-only` is found through Auths only:
  the lookup never returns a deleted Profile.
- **RTDB and userDB emails differ:** the profile line says so; the userDB value is used.
