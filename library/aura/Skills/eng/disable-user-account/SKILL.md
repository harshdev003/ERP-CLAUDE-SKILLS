---
name: disable-user-account
description: >
  Disable, re-enable or check the status of one Firebase Auth account in aura-dev or prod
  (firebase-auratech16), by uid or by email (the email is looked up, never printed). Disable sets
  disabled=true and revokes the refresh tokens, so the person is signed out within an hour and the app
  shows "This account has been disabled. Contact support". Prod writes need Jerome's go, quoted verbatim.
  Trigger on "disable this account", "disable user {uid}", "block this user's account", "re-enable the
  account", "enable user {uid} again", "is this account disabled", or "suspend this Firebase user". Do NOT
  use to delete an account (UserService's deleteUser flow, or `eng:cleanup-orphaned-auth` for orphans),
  for staff roles (UserService job:staff-role), or to block an IP (`eng:block-ip`).
metadata:
  version: "1.2.0"
knowledge:
  reads:
    - Guide/Index
    - Tools/Firebase     # Identity Toolkit accounts:lookup / accounts:update
    - Tools/GCP          # the GCP_KEY service account and its roles
    - Tools/Linear       # recording each prod use on the requesting card
---

# disable-user-account

## Goal

Stop one account from signing in, or let it sign in again, and leave a record. Disable is a
reversible switch, not a delete: the profile, subscriptions and data are untouched.

## What each action does

| Action | Call | Effect |
|---|---|---|
| `status` | `accounts:lookup` | read-only: disabled yes/no, sign-in providers, tokens valid since |
| `disable` | `accounts:update` with `disableUser: true` and `validSince: <now>` | sign-in and token refresh fail (`USER_DISABLED`); refresh tokens are revoked, the same as the Admin SDK's `revokeRefreshTokens` |
| `enable` | `accounts:update` with `disableUser: false` | sign-in works again; `validSince` is left alone |

**Disable takes effect at the next token refresh, not instantly.** An open session ends when its ID
token expires, within an hour. Until then, a service that verifies ID tokens without `checkRevoked`
keeps accepting the current one.

## Prerequisites

- `GCP_KEY`, the service account, with `roles/firebaseauth.admin` on the target project for a write
  (a viewer role is enough for `status`). A missing role is reported by name, with the project and the
  account to grant it to. Never switch to a personal login or someone else's key file.
- For prod writes: Jerome's go, typed by Jerome, and the Linear card the request came from.

## Step 1 — Status

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/disable-user-account/scripts/account.py status --project <aura-dev|firebase-auratech16> --uid <uid>
```

Use `--email <address>` instead of `--uid` when that is all you have. The email is sent to the lookup and
never printed; the output names the account by its uid prefix only. The lookup matches the top-level email
only: a federated sign-in (Apple, Google) can have none, so "no such account" by email doesn't mean the
account doesn't exist. Use `--uid` for those. Read-only prod checks need no go.

**Keep list.** `disable` and `enable` refuse a uid on the keep list (`skip <prefix>…: keep list`):
a held account is never disabled, re-enabled or rewritten here. The keep list is `~/.claude/held-accounts/held-uids.json` (or `HELD_UIDS_JSON`): a JSON array of
uids, mode 600, written by the hold owner, never in this repo. Without it (missing, empty, unreadable,
malformed or readable by others) a write is refused; never create or relax it to get a run through. `status` needs no list.

## Step 2 — Plan, then confirm

Before any write, show the requester the plan and stop for a yes:

- the action (`disable` or `enable`), the project, and the account's uid prefix;
- its Step 1 status (already disabled? which providers?);
- the exact command you will run.

Run nothing until the requester confirms that plan in their own words. On prod, the confirmation is
Jerome's go, and it must name this action and this account.

## Step 3 — Disable or enable

aura-dev needs no go:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/disable-user-account/scripts/account.py disable --project aura-dev --uid <uid>
```

**Prod is a prod change.** The script refuses unless it gets all three of `--prod`, `--go` with Jerome's
go quoted word for word, and `--card`:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/disable-user-account/scripts/account.py disable --project firebase-auratech16 --uid <uid> \
  --prod --go "<Jerome's go, verbatim>" --card <CARD-ID> [--reason "<why>"]
```

A go covers only the action and account it names. Never write a go yourself or stretch one: a go to
disable one account is not a go to enable it later, or to disable another.

Every write prints the account before and after, and exits 1 if the read-back doesn't match. Exit 2
means a read failed: report it as unverified, never as done.

## Step 4 — Record it

For each prod write, add the script's `card record` line (an email in the go is masked) as a comment on the requesting card (uid
prefix, action, time, the go, and the reason if given). The reason goes on the card only.

For an abuse case, Security decides whether it also gets a Security Incident Log row; this skill
writes none.

## Output rules

Uid prefix (6 characters, after any `provider:` prefix), counts, providers and times only. No email, name, phone number or token,
and no credential value: `GCP_KEY` is named, never printed. Every call carries the AuraClaude user
agent.
