# Performance (ERP services)

- Database connection pooling; release connections and listeners on shutdown.
- Stream or page large exports, imports and reports — never load a whole table.
- Every list endpoint is paged with a configured default page size (security.md B2).
- Index for the filters reports actually use; check plans (`EXPLAIN ANALYZE`) for slow
  queries before adding indexes.
- Heavy reports and period-close run as background jobs, with progress and a result
  link, not in the request.
- Background jobs: queued, prioritised, idempotent, with retry + backoff and a
  recorded run time.
- Cache only data that is safe to be slightly stale (master data), and key caches by tenant.
- Compression and rate limiting on the API.
