# Error handling (ERP services)

Adapted from a production team's error-handling rules (`library/aura/Rules/BusinessService/claude/error-handling.md`).

## Error keys
- Every error has a stable **key** defined in one registry file (e.g. `src/errors`),
  sorted alphabetically within its category, with its HTTP status and a clear message.
- Keys describe **what** the error is (`INVOICE_ALREADY_POSTED`, `PERIOD_LOCKED`,
  `INSUFFICIENT_STOCK`), not which module raised it.
- Error response shape:
  ```json
  {"error": {"key": "INVOICE_NOT_FOUND", "message": "The invoice was not found", "status": 404, "transaction": "<request id>"}}
  ```
  Never include personal data or the raw lookup value in the error body.

## Patterns
- **Fail fast on missing required parameters.**
- **Single point of error handling** in the handler: the domain layer records/returns
  the error; the handler maps it to a response once.
- **Success is a value, failure is an error.** Never return `true`/`false` to signal
  whether an operation succeeded.
- **Loud misconfiguration, never silent fallback.** A feature that is enabled but
  misconfigured fails at startup or with a clear error, not by quietly doing nothing.
- **Every handler path sends a response.**

## Provider and upstream errors
- Match provider errors on their stable **code**, not the message text.
- Treat transient failures (timeouts, 429, 5xx) as retryable with backoff and a cap;
  everything else fails immediately.
- SDK retries are not a fallback strategy — decide explicitly what happens after they give up.

## Transactions
- A failure in the middle of a posting rolls back the whole transaction — no
  half-posted documents, no ledger without its document.
