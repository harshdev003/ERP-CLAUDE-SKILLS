# Architecture (ERP services)

Adapted from a production team's service rules (`library/aura/Rules/BusinessService/`).
Stack-neutral: applies whatever framework each `ERP-*-SERVICE` repo uses.

## Layers (backend)

| Layer | Responsibility |
|---|---|
| `http` (routes, handlers) | Thin controllers: parse the request, call the domain layer, return the response. Auth middleware lives on the route. |
| `domain` / `models` | **All business logic**: validation, calculations (tax, totals, stock), state transitions, posting rules. |
| `repositories` / data access | Database reads and writes, always tenant-scoped (security.md B2). |
| `services` / integrations | External systems: payment gateways, email/SMS, e-invoicing, banks, storage. |
| `jobs` | Scheduled or queued background work (reports, reminders, imports, period close). |
| `utils` | Pure, reusable helpers — no I/O, no business rules. |

## Thin controller pattern
- Handlers only do HTTP I/O. They never compute money, change stock, or query the
  database directly.
- The domain layer validates input and returns a result or a typed error; the handler
  checks for the error once and maps it to a status.
- Don't re-check in the handler what the domain layer already guaranteed.

## ERP domain modules
Keep each business area a module with a clear boundary, e.g. `accounting` (ledger,
journals, periods), `sales` (customers, quotes, orders, invoices), `purchasing`
(suppliers, POs, bills), `inventory` (items, warehouses, stock moves), `hr`/`payroll`,
`masters` (company, tax codes, currencies, units), `auth` (users, roles, permissions),
`reports`. A module calls another through its public functions, never by reaching into
its tables.

## Documents and postings
- Business documents (invoice, bill, payment, stock transfer, journal) follow a
  lifecycle: `draft → submitted/approved → posted → (cancelled/reversed)`.
- **Posting** is the single place that writes ledger and stock effects, inside one
  database transaction with the document's status change.
- Posted documents are immutable (security.md B3); corrections are new documents.

## Multi-tenancy
- Every tenant-owned table has a `tenantId` (company) column; every query is scoped by
  the caller's tenant in the data layer.
- Shared master data (currencies, countries) is the only data without a tenant.

## Health and operations
- Every service exposes `GET /ready` (dependencies reachable) and `GET /status`
  (version, uptime).
- Fail startup if a critical dependency (database, config) is missing — never start
  half-working.

## Fire-once side effects need a re-run path
A side effect that runs once at the end of a lifecycle (send invoice email, post to
e-invoicing portal, sync to bank) will eventually fail. Add a privileged, single-entity
re-run path that runs **only** that side effect, enforcing the same preconditions,
instead of re-running the whole lifecycle.
