# BugBot review rules: FirebaseAuth

Apply every rule below to each PR. Report a violation as a High severity finding unless the rule says otherwise.

## Repo exception to A2 (ENGBACK-2799, 2026-11-15)

- authMergeLock refuses sign-ups whose user agent is a script's default label, as a stopgap speed bump. It never allows or exempts anything because of a user agent. The exception ends when App Check or reCAPTCHA replaces it.

# Security review checklist

Every reviewer of an Aura pull request checks this list before approving, whether it is BugBot, Claude (`eng:review-pr`), Cursor or a person. A finding against a rule here is **High** severity unless the rule says otherwise. Each rule says what to check and why. The canonical copy lives in AI Knowledge at **Compliance → Security → Code Review Checklist**. Each repo carries a copy of the sections for its type, identical across every repo of that type. Change the canonical page first, then every copy in the same pass.

This copy carries the sections for Firebase rules and Functions. The canonical page also covers the other repo types.

## A. All repos

### A1. Secrets and personal data never leave the system
- **No secret goes in code, a fixture, a test, a committed build output or a client bundle.** That covers API keys, tokens, service-account JSON, PEM blocks, `eyJ…` JWTs and OAuth codes. Anything shipped to a browser or a phone is public, including every `NEXT_PUBLIC_*` and Vite `VITE_*` (`import.meta.env`) value, and internal tools (a support sidebar, an admin page) are clients too. The shared `mobile` and `web` client tokens are the exception in kind: not public, but the easiest tokens to obtain if something goes wrong, so they rank last (B1) and rotate most often. A client gets only publishable or restricted keys, and secret-key work happens on the server. If a secret was ever committed, rotate it. Removing it from the code is not enough. Why: the mobile bundle history exposed Stripe, Twilio, AWS, Mux and Iterable secrets, and the Front support sidebar (AuraFrontPlugin) shipped an agent-role Aura token and an OpenAI key in its public bundle.
- **No real personal data in fixtures, tests, docs or PR text.** Use synthetic ids and emails.
- **Never log:**
  - tokens or keys of any kind;
  - emails, phone numbers or payout emails;
  - request or response bodies (one exception, below);
  - free text (questions, notes, reasons, messages);
  - redeemable codes;
  - full URLs that carry a secret or an email;
  - whole request headers, which include the cookie and authorization headers.

  Log ids, hosts, counts and error codes. Log headers from an allow-list, and replace credential query values (`sessionCookie`, `token`, `code`) with `[REDACTED]` in every URL you log. That includes the `referer` header and a browser logger's page URL: a credential left in one URL leaks again through the next request's referer. A browser error report logs a page as origin + pathname, never `window.location.href`, and reports the useful query values as their own fields: logger URL redaction covers only fields named `url` and `originalUrl`, so a URL under any other key (`checkoutUrl`) passes through. Reviewer signal: a full URL (`window.location.href`, `document.URL`, or a `*Url` built from one) passed into a log or error-report call. Why: AIWeb's checkout errors logged `?sessionCookie=` at 9 call sites. Why: user-web logged whole request headers and `?sessionCookie=` URLs into prod Cloud Logging.
- **A failure log carries a reason that is safe to log, as plain fields.** Give an error code, HTTP status or short error class; for media, `MediaError.code`, `networkState` and `readyState`; and an asset name, never a signed URL (Firebase Storage URLs carry `token=`). Never pass an `Error` object as the whole payload where the logger doesn't serialize errors, because it logs as `{}`. Don't log an expected state as a warning: a media error with code 1 (aborted) means the user left. Why: UserWeb's "Error loading video" rose 5–10× with no cause in any log line.
- **The user attached to logs is `{id, role}` at most.** That covers logger base metadata, a context object and a crash reporter's `setUser`. Never put an email, name, phone or address there, even for support: client logs never pass through the services' redaction. Why: UserWeb and CoachWeb sent the signed-in user's email and first name with every warning.
- **Log only fields you name explicitly:** an allow-list of ids, counts, statuses and error codes. Never log a whole object, request or response body, payload, or error object, and never spread one into log meta. That includes provider objects: a Stripe charge, customer or invoice, a webhook's `data`, an LLM request or response. The shared `redact.js` catches only credential and email keys, plus Bearer tokens and JWTs in a log line's text fields, so anything passed whole reaches prod. Reviewer signal: a logger call whose meta is a variable holding a fetched or received object, or `...obj` of one. No service logs request or response bodies, query strings or headers, the shared `context.js` "Starting transaction" log included. To debug a request, use its transaction id (the `X-Response-ID` response header) with the error code and message. Why: whole Stripe charges with billing details and full LLM payloads reached prod logs (IntelligenceService#677, PaymentService#271).
- **Build one safe meta object and send it to every sink.** The same explicit fields go to both the log line and the audit row, so the sinks can't drift apart.
- **Log redaction is shallow.** A fixed list of sensitive field names, checked at most two levels deep, never a recursive walk. Scrub Bearer/JWT patterns only in the emitted string fields (message, error messages, stack, url). Reduce error and axios objects to name, message, code, status and stack. Include a bounded-cost test. If old clients post unredacted logs to a server endpoint, scrub there the same way. Why: deep redaction caused memory issues in the services (UserWeb#2540).
- **IP addresses are logged today only on failed requests (4xx/5xx), on parse errors, and in some per-service fields.** That is accepted as is. A PR that adds IP logging anywhere else needs Jerome's OK.
- **Around crypto or key material, log `errorCode` and `errorName`, not the raw error.** A raw error can quote the key or the token it failed on.
- **Never pass a secret as an id or a parameter into a generic helper that logs its arguments.** Use parameterised `execute({text, values})`, which logs only the SQL. Why: a raw-token lookup leaked the token into error logs.
- **Any script or report that prints routes or paths from logs masks every non-fixed segment first** (`/:email`, `/:id`). Mask by route shape, not by segment length: anything after a known dynamic prefix (`refer`, `coaches`, `users`, `u`) becomes `:x`, and query strings are dropped. Referral codes and coach slugs are short and still identify a person. Next.js page-data warnings print the real path and query, so treat those lines as identifying. Why: email-in-path routes leaked into agent output twice, and a length-based mask let referral codes through.
- **A lookup key the user typed (an email, a code) never goes into a log line or an error body.**
- **Scripts and CLIs read bearer tokens from the environment.** They never hardcode them.
- **Print an environment variable's name, never its value.**
  - Safe: `compgen -e` (bash), `python3 -c 'import os; print(*sorted(os.environ), sep="\n")'`, `[ -n "$VAR" ] && echo set`, or parse it in code and print only a non-secret field (a key id prefix, `client_email`).
  - `env | cut -d= -f1` is not safe: `cut` works line by line, so a multi-line value such as a PEM key prints its continuation lines in full.
  - Never: `env`, `env | grep`, `printenv VAR`, `echo $VAR`, `set`, or reading a settings file (`~/.claude/settings.json`, `settings.local.json`, any other settings file) with `cat`, `grep` or any tool that shows its lines: a grep for an unrelated word prints the matching line with its value. To check a key is set, test the variable (`[ -n "${NAME:-}" ] && echo set`).
  - Never pass a secret as a command-line argument, since argv shows up in process lists and logs.
  - Write a key to a file with `umask 077; printf '%s' "$VAR" > file`, and never print the file.
  - Why: an `env | grep` printed part of the GCP_KEY private key into a session transcript, and the key had to be rotated. On 2026-11-16 a grep for "prod" on a settings file printed an API key into a local transcript, pending rotation.

### A2. Never trust the client
- **The server decides identity, role, money and entitlement.** A client-supplied `userId` in the body or query, `role`, `premium`, `amount`, `price`, `transactionId`, `businessId` or `coachId`, and any entitlement field, is ignored unless the caller is admin or service; a `/:userId` route's URL id follows the next bullet. Creatable fields are allow-listed. Why: signup with `role: 'admin'` granted admin in every service.
- **`/:userId` routes are for backend callers (admin, service, system, external and agent), plus `web` and `mobile` only where a flow without a signed-in user needs the route:** onboarding (the app, UserWeb, and CoachWeb's coach onboarding) and CRMWeb, where the person is identified by a link but not signed in. Once a user is signed in, the client uses that user's token on the self route (`/me` style, which takes the user from the token); the client token is never used when a user is signed in. Without a user token it calls the `/:userId` route with its client token. Never add a "caller is `:userId`" ownership exception to a `/:userId` route. Pre-login flows (checkout, links in emails) call the `/:userId` route with the client token until the pre-login flow review gives them a login or a signed single-purpose token. To lock a `/:userId` route an app still calls: move the app to the self route, wait for adoption or set a minimum-version gate, then lock it to service roles. Why: ExperimentService's `/:userId` routes were open to clients, and 27 more still allow the web role.
- **Public endpoints and public dumps use an allow-list of fields, never a deny-list.** A field added later must not leak by default.
- **An id is a name, not a key.** Knowing an id never grants access: ids appear in public pages, invite links and other responses, and Firebase uids and row ids can be collected or enumerated. A route or rule that returns data by id checks who is asking, unless the data is public by design. Reviewer signal: an unauthenticated route or a `.read: true` rule keyed by an id, defended as "you'd need the id". Why: outside testers collected user ids from public coach pages, a leaking list route and an email lookup, then read each profile.
- **Never pass a client-chosen vendor resource straight through.** Agent ids, voice agents, provider names and bucket names are checked against an allow-list. Why: they spend paid third-party resources.
- **Client metadata forwarded to an agent or another backend is input, not config.** Allow-list its keys, check each value, and ownership-check any id inside it: the downstream service trusts whatever arrives under the service token. Why: the LiveKit token route passed the client's metadata object (provider, voice, prompter, `conversation_id`) straight to the voice agent.
- **Redirect targets that come from data or a query must pass an exact-or-subdomain host allow-list.**
  - Parse the target with WHATWG `URL`.
  - Reject look-alike hosts, `@` userinfo, backslashes, `//` and trailing dots.
  - Drop any userinfo.
  - Fall back to our own page.
- **Server-side fetches of a client-supplied URL block private, link-local and metadata IPs**, after resolving DNS. That's an SSRF guard.
- **An RTDB lookup by a key built from user input refuses an empty key.** With the Admin SDK, `ref('/node/' + '')` reads the whole node, and `trim()` or lowercasing can turn whitespace into an empty key. Why: an empty /emailUser key would have read about 6.7M entries.
- **Only the web front door has a WAF.** Cloud Armor fronts the web apps (user-web, coach-web, business-web and the marketing hosts) with scanner-path denies, OWASP signatures, crawler rate limits and incident IP blocks. The services ingress (the api hosts) has no WAF and no edge rate limit, so every backend route defends itself with authentication, ownership checks and in-app rate limits. Never write "the WAF blocks it" for an api route, and never rely on the edge in place of an app check. Why: the edge covers only the web hosts (AI Knowledge → Tools → Cloud Armor), and a request can reach the api hosts directly.
- **A user agent is a label, never proof of who is calling.** Never drop, allow-list or exempt from rate limits a request because of its `User-Agent`, and never hide a *denied* request on it. Treat a request as ours only when the user agent matches *and* it succeeded in a way only our credentials allow. Why: the RTDB profiler dropped every request whose user agent looked like the Admin SDK (axios, node-fetch), denied ones included, so a probe sending one of those was invisible.
- **A lookup never creates a record.** A support, admin or display lookup uses a read-only endpoint that returns nothing when nothing exists; never call a get-or-create route just to show an id. Why: the Front support sidebar called PaymentService `getCustomerId` on every user it opened and created 23 Stripe customers in one day, one of them for a person asking to be erased.
- **Uploaded files are checked before any parser touches them.** Allow-list the input formats and cap the size. Why: ffmpeg on an unchecked user file is a parser attack surface.
- **Links and emails carry short-lived signed tokens.** Never put a user id, email, session cookie or long-lived token in a URL. See Secure Code Guide §6.
- **Approved exception (FirebaseAuth, 2026-11-15):** authMergeLock refuses sign-ups whose user agent is a script's default label, as a stopgap speed bump. It never allows or exempts anything because of a user agent. The exception ends when App Check or reCAPTCHA replaces it. Only FirebaseAuth carries it (repo header of its .cursor/BUGBOT.md and its CLAUDE.md, FirebaseAuth#112).

### A3. Change safely
- **A new check ships enforced directly, with no enforce config flag: be ready to roll back, and monitor the logs after release.** Ship log-only first (with a flag) only when we need more information before enforcing, e.g. the Iterable token check: we logged first to confirm no calls arrived without a token.
  - When a check does log first, the flag defaults to log-only, and the warning carries safe fields only.
  - Review the counts, then flip the flag on Jerome's go.
- **Find every caller before tightening a route, a rule or a response shape.** Callers include:
  - the Aura app (iOS and Android, including the Android release branch);
  - Aurie;
  - CoachWeb, UserWeb, BusinessWeb and CRMWeb;
  - the other services.

  Then check 30 days of prod logs. Don't lock a route before its client sends auth. Why: the legacy Aura app still calls routes Aurie never uses.
- **Answer "who calls X", "what breaks if Y" or "is this bug still live" from origin's default branch.** Run `git fetch`, then search `origin/main` (or `origin/master`) or a fresh worktree, never a local checkout: it may be days behind. Why: a stale local Aura checkout showed the Onboarding6 `handleLogin` createdAt reset as live after Aura#7446 had fixed it (Aura 3.33.0), and nearly put a needless Mobile fix in front of the ONE auth work.
- **Config defaults keyed on `NODE_ENV` must be overridable by an env var.** Wrap the ternary in parentheses: `X || NODE_ENV === 'production' ? a : b` parses as `(X || prod) ? a : b`. Test and CI builds use dev config. Why: e2e CI lanes built with prod config wrote to prod Firebase.
- **Backfills that scrub or rewrite stored rows** must:
  - reuse the writer's own function;
  - dry-run by default and write only with `--apply`;
  - run in one transaction;
  - page by id;
  - print counts only;
  - report failures as `code` and `name`.

  After applying, re-run the dry run and confirm it shows zero changes.
- **A tool that undoes a bulk job's side effect picks its records from the job's own record of what it wrote, never from "it changed inside the job's time window".** Other writers act in the same window. Read the job's write list (only the lines the changing code path wrote), and skip anything the job didn't write. Why: a session restore treated every revocation during a bulk copy as the copy's and re-opened 2 accounts that something else had revoked (fixed in FirebaseAuth#80).
- **Before removing a migration fallback, run the migration in every environment the service runs in**, dev included. Why: removing the raw-token fallback after only prod's token table was rewritten broke every dev e2e lane.
- **Dropping a PII field takes three steps:**
  1. Stop reading and writing it, and roll that out.
  2. Drop the field.
  3. Check that it's gone.

  Grep every other repo first, because the same field often lives elsewhere.

### A4. PR hygiene
- **Code comments carry no ticket ids.** The ticket goes in the commit and the PR. A comment states only a non-obvious why.
- **A PR opens as a draft.** It stays a draft until CI is green and every BugBot finding is addressed, from the latest run and every earlier one: fixed or answered in its thread, then resolved. Clean means 0 unresolved BugBot review threads on the PR, not a latest run that says "no new issues": a re-run reports only new findings, so older open ones stay invisible. An agent never merges or releases, and "go" or "deploy" is not approval to merge. Why: LivekitVoiceAgent#326 (High) and #324 merged with open BugBot findings because the check trusted the latest run's summary.
- **Don't force-push a PR branch to rework it.** Open a new PR and close the old one with a link to it.
- **Whitespace the change causes stays in the PR; unrelated formatting goes in its own PR.** Re-indentation that the change itself causes (for example, code wrapped in a new try/catch) belongs in the PR. Choose the best design; re-indentation is no reason to pick a worse one. Formatting or whitespace changes to unrelated sections go in a separate PR. Reviewers can hide whitespace with `?w=1` on the GitHub diff URL. Why: IntelligenceService#790 showed +1042/-484 for a +91/-5 logic change.
- **Test against current master, not the master the branch was cut from.** Before marking a PR ready, merge master in and re-run the tests if a shared file or a cross-service standard (auth status codes, `logger.js`, `redact.js`) changed after the branch was cut. When two open PRs touch the same model or spec, re-run the tests on master after both merge, before releasing. Each one can be green alone and red together. Why: BusinessService#60 still expected 403 for a missing token after the 401 standard landed, and turned master red once merged (fixed in BusinessService#80).

## D. Firebase rules and Functions

### D1. RTDB rules (`AuraHealth/Firebase` `rules.json`)
- **Grants cascade down and can't be revoked deeper.** A child `false` does nothing under a parent `true`. Keep parents grant-free and put the grants on children. Why: `/users` `.read: true` made every profile readable.
- **Rules aren't filters, so a parent `.read` lists every key.** Per-user and PII data is read at `$key`, never at the node.
- **Credentials never sit on a client-readable node.** OAuth tokens, push and device tokens, cookies and advertising ids go on a server-only node. Why: a client-readable record lets any signed-in user read the tokens on it.
- **Use one of the five node classes:**
  - server-only: `.read: false`, `.write: false`;
  - per-user, owner (`auth.uid`);
  - read by key, with no listing;
  - create-only: `!data.exists() && newData.exists()`;
  - author-only, plus moderation.
- **Owner is `auth.uid === $uid`.** `auth != null` is not an access rule, because anyone can sign up, and existing anonymous accounts still refresh their tokens (anonymous sign-in has been off since 2026-10-30).
- **Writes are per field.** There's no write on `$uid`, and server-owned fields are named explicitly. A named key replaces the `$field` wildcard, so give it its own owner rule if the owner should still edit it.
- **A write rule allows the first client write when no server creates the record.** Before adding an `exists()` or edit-existing-only condition, read the location's Firebase Rules row Notes and grep the server repos for RTDB writers of the path. Add emulator cases for the first write on a missing id, one per client writer. Why: Firebase#162 required the record to exist and blocked the first `appointments` write on prod for 18 minutes, as Firebase#51 had before.
- **`.validate` is skipped on deletes.** Guard deletes in `.write` with `&& newData.exists()`. Freeze entitlement fields (role, premium, `premiumTrial`) with `newData.val() === data.val()` plus a delete clause. That only works on primitives, because rules can't compare objects: lock a server-owned object with `".validate": "false"` and no write grant.
- **Process:**
  - Profile a node while its feature is live before restricting it.
  - Before closing a node, check git history for the last app version that read it. Old builds in the field still do, so "no current code reads it" is not enough; close it behind a minimum-version floor. Why: Firebase#173 (allYearlyQuotes, still read by an old Android build).
  - Before locking a field, check who still writes it and whether anything reads it. A field that is written but never read can stay writable while the client fix ships, which avoids permission_denied noise.
  - After editing `rules.json`, diff the parsed rules, not the text. Load `rules.json` from your branch and from `master` as JSON, list every path whose rule changed, check that the list is exactly the locations you meant to change, and give the count in the PR. Why: many locations carry identical rules, so a find-and-replace edit hits all of them; Firebase#166's `activities` change also rewrote `smGoals` (Firebase#172).
  - Size a change's client impact from Postgres, never from prod RTDB queries.
  - Deploy rules changes on busy nodes outside peak hours.
  - Use one card and one PR per node, with emulator tests and a master control.
  - Every new data location ships with a rule in `AuraHealth/Firebase` `rules.json` and a row in AI Knowledge → Compliance → Security → **Firebase Rules** (one row per location: current and target rule, the clients that use it, status). That includes a location first written by a Function. Each deploy also gets a row in the **Security Change Log** (same page) and an entry in the Tools → Firebase rule change log.
  - A batch deploys only on Jerome's go.
- **The Admin SDK and the console bypass rules.** Only client traffic changes.

### D2. Functions
- **Trust `event.authType` (`app_user`, `admin`, `unknown`, `unauthenticated`) before propagating privileged data from a client-writable path.** The emulator always reports `unknown`, so verify in dev.
- **Never trust the type of an RTDB value.** React Native Firebase writes `{__rnfbNull: true}` for `null`. Normalise by `typeof`, warn with `{field, type}` and continue. An RTDB trigger has no retry, so a throw silently drops the downstream event.
- **`logger.js` is byte-identical across the Functions repos.** Use `logger.audit` for security-relevant state changes; it is always written. The logger level is warn.
- **A blocking function (`beforeUserSignedIn` and similar) fits its time budget.** Log the timing when a lookup is slow.
- **Never copy or republish a server-private field to a public node.** Keep it on a server-only node instead.
- **Payout identity comes from one trusted server path.** Never take it from an RTDB diff a client can write.
- **Deleting a Function's source also undeploys it.** The PR adds the function to the deploy's `functions:delete` list, and the release checks the function is gone from the project. Why: `onRoleUpdated` kept running about 14 months after its source was deleted, copying client-written roles into every service's Postgres.
- **An Admin update of a user's email or password signs that user out.** `updateUser` with `email` or `password`, and `importUsers`, revoke the account's refresh tokens. A bulk Auth tool keeps sessions only on purpose, and its verify step compares `tokensValidAfterTime` before and after for every account it touched. Why: the bulk email copy signed out the accounts it updated until FirebaseAuth#73 kept `validSince` and made verifyCopy fail on a `tokensValidAfterTime` change.

## F. Release and monitoring

- **Confirm the deploy, not the tag.**
  - Services: Spinnaker "complete for <APP>" in #deployment, and pods reporting the new version.
  - Web: Bugsnag "New release in PROD".
  - If a tag created no CI pipeline, trigger one through the CircleCI API.
- **Before re-running a stopped release, delete the stale `chore/release-x.y.z` branch and its worktree.**
- **Alerts with an ignore list still surface an ignored signature that grows ≥3× (and ≥20) week over week.**
- **The Security Incident Log is for outside access attempts only.** Our own vulnerabilities, secrets that need rotation and misconfigurations go on Linear.
