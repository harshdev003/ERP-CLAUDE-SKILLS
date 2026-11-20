# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Security review:** `.claude/rules/security.md` is the checklist every reviewer applies before approving a PR: Claude, Cursor (`.cursor/rules/security.mdc`), BugBot (`.cursor/BUGBOT.md`) and people. All three are copies of AI Knowledge → Compliance → Security → Code Review Checklist, identical in every backend service repo. Change that page first, then every copy.

## Commands

```bash
nvm use                # Switch to correct Node.js version (ALWAYS run first)
npm run dev            # Start server in development mode
npm run test           # Run all tests
npm run test -- path/to/test.spec.js  # Run a single test file
npm run lint           # Run ESLint (run after every code change)
npm run lint:cache     # Run ESLint with cache
```

**After every code change, run `npm run lint` and fix all errors before proceeding.**

### Jobs

```bash
npm run job:activation
npm run job:blast        # BLAST_LIMIT_SIZE=1 npm run job:blast
npm run job:daily
npm run job:retention
npm run job:winback
npm run job:unsubscriber # UNSUBSCRIBER_LIMIT_SIZE=1 UNSUBSCRIBER_SOURCE=sendgrid npm run job:unsubscriber
npm run job:localize     # translates template strings; runs hourly in prod
# See README.md for full job list
```

### Local development

```bash
docker-compose up --build   # Start PostgreSQL (port 5432)
gcloud beta emulators pubsub start --project aura-local  # PubSub emulator
# Then: PUBSUB_EMULATOR_HOST=localhost:8085
```

## Architecture

This is a Node.js/Express service for managing email campaigns. The layers under `src/` are:

- **`http/`** — Thin handlers (controllers) + middleware + router
- **`models/`** — All business logic and PostgreSQL persistence
- **`services/`** — External integrations: `sendgrid`, `postgres`, `redis`, `pubsub`, `slack`, `openai`, `mixpanel`, `branch`, `iterable`, `zeroBounce`, and others
- **`jobs/`** — Background processing; all jobs extend the base `Job` class (`initialize()` / `run()` / `finalize()`)
- **`listeners/`** — Google Pub/Sub event listeners managed by `ListenerManager`
- **`utils/`** — Pure utility functions
- **`connectors/`** — Reusable HTTP connector (`http.js`)

### Thin controller pattern

Handlers extract params and check `context.error` **once**. Models own all business logic, input validation, and error handling via `context.addError('ERROR_KEY', {data})`.

```javascript
// Handler — thin
async function retrieve(request, response) {
  const {context} = request;
  const result = await model.retrieve(context.userId, {context});
  if (context.error) return response.status(context.error.status).json({error: context.error});
  return response.status(200).json({result});
}

// Model — all logic here
async function retrieve(userId, {context}) {
  const user = await postgres.getOneById(USER_TABLE, userId, {context});
  if (!user) {
    context.addError('USER_NOT_FOUND', {userId});
    return null;
  }
  return performOperation(user);
}
```

### Error keys

All error keys must be defined in `src/errors.js`, sorted alphabetically. Keys are semantic (describe the error, not the model). Use appropriate HTTP status codes (400, 401, 403, 404, 422, 500).

### Logging

Use `logger` (Winston) — never `console`. Log levels: `debug` (operational traces), `info` (business events / completion), `warn` (potential issues), `error` (failures), `crit` (critical). Always include structured metadata:

```javascript
const meta = {component: 'myModel', method: 'retrieve', userId};
logger.debug('Starting lookup', meta);
logger.info('Done', {...meta, resultId: result.id});
```

### Config

Destructure config at the top of each module. Add new env vars to `README.md` in the same change. Use config constants for environment-agnostic values; use env vars for secrets, URLs, and per-environment flags.

```javascript
const {
  service: {version: SERVICE_VERSION},
} = config;
```

### Database schema

- **`database/create.sql`** — full schema for fresh installs
- **`database/update.sql`** — incremental `ALTER TABLE` statements, appended at bottom
- Use double-quoted identifiers: `"user"`, `"createdAt"`
- Timestamps: `TIMESTAMPTZ` with `DEFAULT (NOW() AT TIME ZONE 'utc')`
- Primary key pattern: `"rowid" SERIAL PRIMARY KEY` + business `"id"` TEXT/uuid
- Use `ADD COLUMN IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS` for idempotency

**Shared postgres core.** In `src/services/postgres.js`, the block between the `Shared postgres core` and `End of the shared postgres core` markers is byte-identical in all 13 backend services. Never change it in one service only: make the same change in every service and diff the marked block against another service before opening the PR. Service-specific functions go below the end marker. `test/unit/services/postgres.spec.js` is shared the same way.

**Bind every SQL value.** Never interpolate a value into SQL text. Use `getMany` / `count` filters, or pass a query config `{text, values}` to `execute` / `executeMany`. Column, table and operator names come from code only, never from a request or an event.

**Campaign SQL.** Campaigns and jobs that build their own queries use the `sql` template tag in `src/utils/sql.js` (identical in Feed, Email and Notification): every value in `` sql`...${value}...` `` is bound by `.toQuery()`. Use `sql.identifier()` for a column name, and `sql.raw()` only for SQL an admin stored with a campaign (`recipe.users.sql`).

### Naming

- Files: `camelCase.js` | Functions: `camelCase` | Constants: `UPPER_SNAKE_CASE` | Classes: `PascalCase`
- CRUD handlers: `create`, `retrieve`, `list`, `update`, `remove` (for JS reserved word `delete`, use e.g. `deleteJournal` internally, export as `delete: deleteJournal`)

### Git workflow

Branch naming: `feature/description`, `bugfix/description`, `refactor/description`, `quality/description`. Commits use conventional format: `feat(scope): description` (first line under 50 chars).

## Emails, templates and campaigns

The rules that follow from this are in `.claude/rules/service-specific.md`.

### How an email is sent

Every email is a `POST /emails` (or `POST /emails/:userId`) with a `campaignSlug`, a `templateSlug` and a user. `src/models/email.js` `create`:

1. Looks up the `campaign` row and the `template` row by slug, and ignores `active` on both.
2. Picks the language: the caller's `locale`, then the user's `locale` in emailDB, then `en`. A locale outside `localization.locales` becomes `en`.
3. Swaps the template row's English `localization` strings for that locale's rows in the `localization` table, string by string, and keeps English where a translation is missing.
4. Builds the substitution data with the handler named after the template's `name` (`src/models/handlers/`, PascalCase), or `default.js`. Then renders the strings with it and passes them to SendGrid as `localization`.
5. Queues the email. SendGrid renders the template `sendgridId` with that data, to `recipient` when given, otherwise the user's address.

Two kinds of caller:

- **Transactional emails** are sent by slug by another service with the service token: PaymentService (family plan, Aurie), CoachService, IntelligenceService. The apps can send only the two family-plan emails, through `POST /emails/familyPlan/*`. Their campaign row exists only because `create` needs one: it uses the same slug as the template, `frequency: unlimited`, and stays inactive.
- **Campaigns** are run by the campaign jobs (`job:activation`, `job:retention`, `job:winback`, …), which take the active campaigns of their type. A campaign's `recipe.template` picks the template from its `choices` (by slug or tags) with a `random`, `experiment` or `custom` selector.

### Templates, rows and campaigns

- A **SendGrid dynamic template** holds the HTML. Change it by adding a version and activating it.
- A **`template` row** maps a unique `slug` to a `sendgridId`. Several rows can share one SendGrid template and differ by `tags`, `substitutionData` or `localization`. `name` selects the handler.
- A **`campaign` row** has a unique `slug`, a `type` (the job that runs it), a `frequency` and a `recipe`.
- Slugs are kebab-case and name the email (`family-plan-invite`). The slug is the contract with callers, so never rename a slug that has callers.
- Most older templates carry their English text in the SendGrid HTML and have no `localization`. Those are English only.

### Localization

`job:localize` (`src/models/localization.js` `synchronize`) runs every hour in prod. For every **active** template row that has a `localization` object, it checks each string whose key is in `localization.types.template.attributes`. When a locale is missing or the English text changed, it asks OpenAI (`openai.projects.translation`) for all the other locales at once. It writes them to the `localization` table (id `template-<templateId>-<attribute>-<locale>`) with `isReviewed: false`, and stores the English source as the `en` row. Sends use the rows immediately. A run where nothing changed calls nothing and takes seconds. Prod logs at `warn`, so a normal run leaves only npm's banner in the logs.

- **`isReviewed`** is set to `true` only on the stored English copy. A few older rows carry it from Lokalise, the provider before the LLM, since removed. Nothing reads the flag.
- **A translation edited by hand** survives until its English string changes. Then the job overwrites it.
- **Placeholders** in a string use the substitution data with triple braces (`{{{user.givenName}}}`), and `{{{if-x}}}…{{{else}}}…` for conditionals. EmailService renders them before SendGrid, HTML-escaping values everywhere except the subject.

### Adding a template

Steps marked **prod** need Jerome's go first. Dev needs none.

1. Agree the English copy, and split it into strings named from `localization.types.template.attributes` (`subject`, `title`, `message`, `button`, `signature`, …).
2. **prod** (SendGrid has one account): create the dynamic template. Use `{{{localization.subject}}}` as the subject and `{{{localization.<attribute>}}}` in the body, plus the template's own data (links, names). Note its `d-…` id.
3. Dev emailDB: insert the `template` row (`slug`, `name`, `sendgridId`, `active: true`, `localization` with the English strings) and, for a transactional email, an inactive `campaign` row with the same slug and `frequency: unlimited`. Add a handler in `src/models/handlers/` only if the email needs computed data.
4. Let the dev localize job run, or run `npm run job:localize` against dev, and have the 6 translations reviewed.
5. Send a test from dev in English and in one other locale (`POST /emails/:userId` with `locale`) to a test account, and check the subject, the strings and the links.
6. **prod**: insert the same rows in prod emailDB. The prod job translates again within the hour, so its text can differ from the reviewed dev text: copy the reviewed rows over if they must match (also **prod**).
7. Release the caller.

**Rollback:** revert or disable the caller. Setting the rows inactive doesn't stop sends. For a SendGrid change, re-activate the previous version.

## Shared files

Some files must be byte-identical in all 13 backend services: the logger, `context.js`, `redact.js`, the HTTP connector and the shared HTTP middlewares. `shared-files.txt` lists each one with its canonical git blob sha. It is identical in every service, and so is `scripts/check-shared-files.sh`, the first step of the CircleCI `test` job, which fails the build when a listed file differs. A file marked `optional` is checked only in the services that have it.

To change a shared file on purpose:

1. Make the same change in every service that has the file. For a middleware, follow the HTTP middleware standard in Linear.
2. Get the new sha with `git hash-object <path>`, and update its line in `shared-files.txt` in every service, in the same PR batch.
3. Before you push, run `scripts/check-shared-files.sh --compare <dir>`, where `<dir>` holds all the service checkouts. It checks those checkouts against this list too, so a stale list or file in another service shows up.

Only files documented as shared (as the HTTP middleware standard documents the middlewares) belong on the list. Some other files are identical today only by coincidence (`cache.js`, `pubsub.js`, `guard.js`, the listeners, the `status` handler, `sleep.js`), so they stay off, to leave room for legitimate per-service changes. `.claude/rules/security.md` and `.cursor/rules/security.mdc` also stay off: they are built from the shared security checklist and kept in sync across every repo type separately.
