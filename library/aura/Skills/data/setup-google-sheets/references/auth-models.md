# Google Sheets auth models

Three ways Claude can authenticate to the Sheets API. They differ in **who Claude acts as**,
which determines what it can see, who must approve it, and what a leaked credential costs.

`scripts/server.py` supports all three; the model is selected purely by environment
variables, so switching later needs no code change.

| | Per-user OAuth | Share-mode | Domain-wide delegation |
|---|---|---|---|
| Env vars | `TOKEN_PATH` | `SERVICE_ACCOUNT_PATH` | `SERVICE_ACCOUNT_PATH` + `IMPERSONATE_SUBJECT` |
| Identity | the human who consented | the service account itself | any user it impersonates |
| Access boundary | whatever that person can open | only files shared with the SA | everything, org-wide |
| Granularity | per OAuth scope | per file / per folder | per scope, domain-wide |
| Approver | the user, in a browser | anyone who can click Share | **Workspace super-admin** |
| Revoke | user's account settings, one click | unshare the file | remove delegation in Admin console |
| Audit shows | the real person | the SA's email (obviously a bot) | the impersonated person |
| Files it creates | owned by the user, in their Drive | owned by the SA, not in anyone's Drive | owned by the impersonated user |

## Per-user OAuth — the default

Each person consents once as themselves. Permissions mirror the human for free: no sharing
step, no admin, and when someone's access changes in Drive, their agent's access changes with
it. Attribution stays per-person.

The cost is one browser consent per person per machine. Beyond roughly 10–15 people that
becomes tedious, which is the point at which DWD starts to pay for itself.

## Share-mode — an isolated bot identity

The service account is not a member of the Workspace domain; it is a Cloud IAM principal that
can authenticate to Google APIs. It sees a file only when that file (or a containing folder,
or a Shared Drive it belongs to) is shared with its email.

Choose it when a deliberately tiny, explicitly-enumerated footprint matters more than
convenience. Two practical frictions:

- **File ownership.** Sheets it creates are owned by the service account and appear in nobody's
  My Drive; they must be shared back. Writing into a **Shared Drive** avoids this, and keeps
  files owned by the team so nothing is orphaned if the SA is deleted.
- **No group membership.** A service account cannot be in a Google Group, so it never inherits
  group-based access. Every grant is explicit.

## Domain-wide delegation — team-wide reach

A super-admin authorizes the service account's client ID for specific scopes across the whole
domain. The SA can then mint tokens as any user in that domain. One centrally-held credential
serves everyone, with no per-person setup, and permissions still resolve per-user.

### Risks — read before recommending it

- **The key becomes a master key to the domain.** The restriction to one subject lives only in
  the caller's config; Google does not enforce it. Anyone holding the key can change
  `IMPERSONATE_SUBJECT` to any user, including an admin.
- **No MFA, no interactive step.** Service accounts mint tokens straight from a static key
  file, so a leaked key is silent, remote-usable access.
- **The blast radius includes Cloud IAM.** Anyone who can create keys on that service account
  (`iam.serviceAccountKeys.create`) can mint a fresh one and inherit the delegation. Keep the
  SA in a dedicated, minimal-IAM project, never a shared production project.
- **Users cannot consent or revoke.** They are never notified; only a super-admin can undo it.
- **Forensics blur.** Audit entries name the impersonated user, so agent actions and human
  actions are hard to separate during an incident review.
- **Scope creep is one edit.** Adding a scope in the Admin console needs no code review and
  produces no user-visible signal.
- **It is a known attack target.** Published research covers enumerating and abusing existing
  DWD configurations for privilege escalation.

Health-data organisations should additionally confirm broad Drive access does not conflict with
existing privacy or compliance commitments before enabling it.

### If DWD is required

- Grant the narrowest scopes that work; never full `drive`.
- Isolate the service account in its own project with minimal IAM membership.
- Prefer short-lived credentials or workload identity over downloadable JSON keys; the org
  policy `constraints/iam.disableServiceAccountKeyCreation` blocks key creation entirely.
- Review the Admin console delegation list periodically and prune stale grants.

## Dedicated bot account — a fourth option

Create a real Workspace user (e.g. `claude@company.com`) and run **per-user OAuth** as that
account. It combines share-mode's explicit boundary with a real user identity:

- Can join **Google Groups** and **Shared Drives**, so access scales without per-file grants.
- Has a normal storage quota, so file creation behaves normally.
- Edits are attributed to an obvious non-human.
- Revoked by suspending one account.

Costs a Workspace seat, and creating the user needs a user-management admin (not necessarily a
super-admin). Right choice for a **centralised** agent whose credential lives server-side.

**Never distribute one bot account's credentials to several humans running their own agents.**
It flattens permissions — everyone inherits the union of that account's access, including
things they should not see — and destroys attribution, since every edit reads as the bot.

## Scope menu

| Scope | Grants | Class |
|---|---|---|
| `spreadsheets` | read/write all spreadsheets the identity can open | Sensitive |
| `spreadsheets.readonly` | read-only, all spreadsheets | Sensitive |
| `drive.metadata.readonly` | find files by name; **cannot read contents** | Sensitive |
| `drive.readonly` | read all Drive files, any type | Restricted |
| `drive` | full Drive access | Restricted |
| `drive.file` | only files the app itself created | Non-sensitive |

The default pair is **`spreadsheets` + `drive.metadata.readonly`**: enough to discover sheets by
name and read/write their contents, while non-spreadsheet documents stay unreadable and the
*restricted* scope class — which triggers Google's app-verification review — is avoided entirely.

Widen to `drive.readonly` only when the task genuinely needs to read non-sheet files, and update
`SCOPES` in both `scripts/server.py` and `scripts/authorize.py`, then re-run consent. Scopes are
recorded in the token, so changing the list without re-consenting leaves a token whose grants do
not match the code's expectations.
