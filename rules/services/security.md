# Security review checklist (ERP services)

Every reviewer of an ERP pull request — Claude (`review-pr`), BugBot, or a person —
checks this list before approving. A finding against a rule here is **High** severity
unless the rule says otherwise. Rule IDs (A1, B2, …) are cited in review comments.

Adapted from a production team's security checklist (see
`library/aura/Rules/BusinessService/claude/security.md` for the original with its
incident history), rewritten for a multi-tenant ERP.

## A. All repos

### A1. Secrets and personal data never leave the system
- **No secret in code, fixtures, tests, build output or a client bundle** — API keys,
  tokens, service-account JSON, PEM blocks, JWTs. Anything shipped to a browser or a
  phone is public (every `NEXT_PUBLIC_*` / `VITE_*` value). If a secret was ever
  committed, rotate it; deleting it from the code is not enough.
- **No real personal or business data in fixtures, tests, docs or PR text.** Use
  synthetic companies, people, GSTINs/VAT ids, bank details and emails.
- **Never log:** tokens or keys; emails, phone numbers, addresses; bank/card numbers,
  tax ids, salaries; request or response bodies; free text (notes, remarks); full URLs
  that carry a token or email; whole request headers.
  Log ids, counts, statuses, error codes and the matched route pattern only.
- **Log only fields you name explicitly.** Never log a whole object, row, payload or
  error object, and never spread one into log meta.
- **The user attached to logs is `{id, role, tenantId}` at most.**
- **Print an environment variable's name, never its value.** Never `env`, `printenv`,
  `echo $SECRET`, or `cat`/`grep` a settings or `.env` file. Test presence with
  `[ -n "${NAME:-}" ] && echo set`. Never pass a secret as a command-line argument.

### A2. Never trust the client
- **The server decides identity, role, tenant, money and entitlement.** A
  client-supplied `userId`, `role`, `tenantId`/`companyId`, `amount`, `price`, `tax`,
  `discount`, `total`, `status` or approval flag is ignored unless the caller is a
  privileged role. Totals and taxes are always recomputed on the server.
- **Creatable and updatable fields are allow-listed** per role.
- **Public endpoints return an allow-list of fields, never a deny-list.**
- **An id is a name, not a key.** Knowing an id never grants access; every by-id read
  checks who is asking (B2).
- **Redirect targets and server-side fetches of client URLs** pass an exact host
  allow-list; server fetches block private, link-local and metadata IPs (SSRF).
- **Uploaded files** (imports, attachments) are checked before parsing: allow-listed
  formats, size cap, row cap.
- **Links in emails carry short-lived signed tokens**, never a user id or long-lived token.

### A3. Change safely
- **Find every caller before tightening a route, rule or response shape** — the web
  app, the mobile app, other services, jobs, integrations.
- **Backfills and data fixes**: reuse the writer's own function, dry-run by default and
  write only with `--apply`, run in a transaction, page by id, print counts only.
  Re-run the dry run after applying and confirm zero changes.
- **Dropping a personal-data field takes three steps**: stop reading/writing and roll
  out; drop; verify it is gone.

### A4. PR hygiene
- **A PR opens as a draft** and stays a draft until CI is green and every review
  finding is fixed or answered. An agent never merges or releases.
- **Don't force-push a reviewed PR to rework it**; open a new PR and link it.
- **Code comments carry no ticket ids**; the ticket goes in the commit and PR.
- **Unrelated formatting goes in its own PR.**

## B. Backend service (ERP-BACKEND-SERVICE)

### B1. Authentication and route authorization
- **Every route is authenticated** except an explicit, reviewed public list
  (health checks, login, public catalogue). A guest/anonymous session counts as
  authenticated, so `authenticate` alone is not authorization.
- **Access control lives in the router/middleware**, declared per route:
  `authenticate, authorize({roles: [...], permission: 'invoice:post'})`.
- **Every HTTP method on a path is locked on its own.** A `POST …/search` is a read
  and needs the same guards as its `GET`.
- **Status codes:** 401 missing/invalid/expired token; 403 known caller not allowed;
  404 (no read, no write) for a row in another tenant or not owned; 400 for a missing
  or invalid filter; 503 when a dependency is down.
- **Roles are granted only through the user-admin flow**, never by a general update route.
- **A role never exempts a person from token expiry or revocation.**

### B2. Tenant isolation and ownership
- **Every query on tenant data is scoped by the caller's `tenantId` from the token**,
  enforced in the data layer (repository/ORM scope or row-level security), never by
  an id from the URL, body or query.
- **A row read, updated or deleted by id must belong to the caller's tenant** (and
  pass the record-level permission). Otherwise answer 404, read nothing, write nothing.
- **A missing, empty or invalid filter never means "no filter".** `{}` gets 400 or no
  rows — never every row.
- **Field-level authorization**: approval status, price lists, credit limits, salary
  and role fields need their own permission check even for an owner.
- **A response returns named fields**, never the raw row (no password hashes, tokens,
  internal flags), and a test asserts the exact keys.
- **Lists are paged on the server** with a configured default page size.

### B3. Money, stock and documents
- **Money is integer minor units or exact decimal** — never floating point. Round
  once, in one shared function, with a documented rule.
- **Every financial transaction posts balanced double-entry journals** in the same
  database transaction as the document that causes it.
- **Posted documents are immutable.** Correct them by cancel/reverse or a credit
  note, never by editing or deleting.
- **Document numbers are server-assigned, sequential per series, and gap-free** where
  the law requires it.
- **Closed accounting periods are locked**; back-dated postings into them are refused.
- **Anything that posts money or stock is idempotent** (idempotency key or unique
  constraint), so a retry or double-click never posts twice.
- **A multi-step write either fully completes or is fully rolled back.**
- **Webhooks (payment gateways, banks)** verify a signature, with no "skip if missing"
  branch, and are checked against the document, tenant and amount.

### B4. Data and secrets at rest
- **SQL is always parameterised.** Column, table and sort names come from code, never
  from the request.
- **Passwords are hashed with a slow KDF (argon2/bcrypt); API tokens are stored hashed.**
- **Sensitive fields (bank accounts, tax ids, salary) are encrypted at rest** where the
  platform allows, and masked in every UI except the one that needs them.
- **Don't store personal data the service doesn't need.**

### B5. Audit trail
- **Every create/update/delete/post/approve on business records writes an audit
  entry**: who, when, tenant, action, record type and id, and the changed field names
  (values only for non-sensitive fields). Audit entries are append-only.

### B6. Logging
- Log the matched route pattern and status, never the raw path or query string.
- A branch that rejects or drops something logs a warning with safe fields.

### B7. Tests
- **Access changes come with integration tests** covering: no/invalid token → 401;
  another tenant's row → 404 with no read/write; the owner → 200; every allowed role;
  every denied role → 403; `{}` body and invalid id → 400 or no rows; every other HTTP
  method → 401/405.
- **Money logic comes with tests** for rounding, multi-currency and balanced journals.

## C. Web app (ERP-WEB-SERVICE) and server-rendered pages
- A server-rendered page for an id that doesn't exist or isn't the caller's returns
  "not found" from the server and embeds no data for it.
- The web app never holds a secret key; all privileged work goes through the backend.
- Authorization is never enforced only by hiding a button — the API enforces it (B1).

## D. Database access rules
- **D1.** If the database enforces row-level security or access rules, every policy is
  scoped by tenant, and a rule that allows every read is a finding unless the data is
  public by design. Policies are tested the same way as routes (B7).

## E. Mobile app (ERP-APP-SERVICE)
- No secrets in the app bundle; tokens in the platform keystore/keychain only.
- Offline data is encrypted and scoped to the signed-in user and tenant, and cleared
  on sign-out.

## F. Release and monitoring
- **Confirm the deploy, not the tag** — the running version reports the new release.
- Database migrations are applied and verified before releasing code that depends on them.
- Backups are taken before a migration and restores are tested periodically.
