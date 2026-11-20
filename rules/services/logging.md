# Logging (ERP services)

Adapted from a production team's logging rules. Privacy rules for logs are in
security.md A1 and B6 and always win.

- Use the service's logger, never `console.*` in production code.
- Levels:
  - `debug` — operational traces (default for internals)
  - `info` — one line per completed business operation (invoice posted, import finished)
  - `warn` — rejected or dropped input, fallbacks, retries
  - `error` / `crit` — failures / needs immediate attention
  - `audit` — security-relevant events (role changes, privileged access); always written
- Every log call has a `meta` object of **named safe fields**: `{component, method,
  tenantId, userId, recordType, recordId, count, status, errorKey}`.
- Log the route **pattern** and status, never the raw URL, path or query.
- Time slow operations (reports, imports, period close) with start/end durations.
- A branch that rejects or drops something logs a `warn` — silent drops are invisible.
- Business audit history (who changed what) goes to the **audit trail table**
  (security.md B5), not to logs.
