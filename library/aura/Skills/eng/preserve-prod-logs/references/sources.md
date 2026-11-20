# What a legal hold can and can't contain

Read on 2026-11-03: log routing, and what the first hold's run (ENGBACK-2176) found. Re-check the
routing before relying on it for a new incident.

## Cloud Logging, per project

`firebase-auratech16` and `aura-main` each have only the two built-in log buckets and sinks. There are
no custom buckets, no sinks to BigQuery, GCS or Pub/Sub, and no exclusions on `_Default`. So copying
`_Default` and `_Required` copies everything those projects log.

| Log bucket | Retention | Holds |
|---|---|---|
| `_Default` | 90 days, unlocked (30 until 2026-11-02) | everything except the audit logs below |
| `_Required` | 400 days, locked by Google | admin activity and system event audit logs, access transparency |

Where the Firebase logs land, all in `_Default` unless noted:

- **Firebase Auth (Identity Toolkit):** request logs, `identitytoolkit.googleapis.com/requests`. They
  cover sign-ups, sign-ins and account updates. Data-access audit logs for Auth are not enabled, so
  there are no audit lines for them.
- **RTDB:**
  - Audit config is ADMIN_READ only: `cloudaudit.googleapis.com/data_access`.
  - Admin activity, such as `setRules`, is in `_Required`.
  - **Reads and writes are never in Cloud Logging.** DATA_READ and DATA_WRITE are off.
- **Cloud Functions (gen 2):** `run.googleapis.com/requests`, plus a log name per function.
- **Load balancer request logs:** these exist only from about 2026-10-04.

**What is already gone can't be copied.** `_Default` kept 30 days until 2026-11-02, so when the first
hold ran, anything older than about 2026-10-03 was already gone. The plan still covers the whole
window; the verify step shows what was actually there.

## Other evidence

- **RTDB Cloud Monitoring metrics:** `firebasedatabase.googleapis.com/network/api_hits_count` and
  `…/network/sent_bytes_count`. These are the only RTDB evidence. Monitoring keeps them **6 weeks**, so
  export first when the window is near that edge. Check the metric names on the first run.
- **Firebase Analytics:** BigQuery dataset `analytics_153603506` (US), with daily `events_YYYYMMDD`
  tables and one `events_intraday_*` table for the current day. The plan extracts each day's table as
  Avro.
- **Auth records** (`--uids-file`): Identity Toolkit `accounts:lookup`. The skill drops the password
  hash, salt, and password timestamps before writing; everything else (metadata, providers, created,
  last sign-in) is kept as evidence.

## Size and time (ENGBACK-2176, a 33-day window)

| Source | Objects | Size | Copy time |
|---|---|---|---|
| fa16 `_Default` | 16,865 | 1.55 TB | 3 h 16 min |
| fa16 `_Required` | 919 | 21.2 GB | about 1 h 30 min |
| aura-main `_Default` | 1,442 | 3.32 GB | 1 h 16 min |
| aura-main `_Required` | 805 | 0.24 GB | 56 min |

**Object naming (confirmed):** one object per log name per hour,
`<logName>/YYYY/MM/DD/HH:00:00_HH:59:59_copy_log_entries_<op>_<region>_S<n>.json`, newline-delimited JSON
with one entry per line. The verify step's sample-hour match relies on this.

**Verification that worked (ENGBACK-2176, an exact match on 10-15):** 2,838 = 2,838 for aura-main and
126,932 = 126,932 for fa16 `_Required`.
- **Source side:** `gcloud logging read '<window>' --bucket=<b> --location=global --view=_AllLogs --format='value(insertId)' | wc -l`.
- **Copy side:** `gcloud storage cat "gs://$B/**/YYYY/MM/DD/*" | wc -l`, streamed, nothing written to disk.
- **Trap:** `gcloud storage cp` of a wildcard into a new directory silently copied nothing, which read as a
  false 0. Count by streaming `cat`, as the skill does.

Coldline costs about $6 a month for 1.55 TB, with a 90-day minimum. Estimate a new window from the
`_Default` bucket's ingested bytes in Monitoring before planning.

## Lessons from the first hold (ENGBACK-2176)

- **Bindings can come back.** The legacy projectViewer and projectEditor bindings reappeared after the
  first removal. The plan's last step per bucket re-reads its IAM, and only the readers and the admin may
  remain.
- **Grant first, then restrict.** Removing a new bucket's legacy project bindings before granting
  anyone access locked everyone out. The recovery was a conditional `roles/storage.admin`.
- **The copy checks that the caller can write**, not only the source's Logging service agent. Both get
  a temporary objectCreator, removed after.
- **One active copy per project.** A second copy fails with `ABORTED: Active copy operations count is at
  the limit: 1`. Different projects can run in parallel.
- **Poll by state, not by name match.** A queue script stopped seeing a finished copy. `status` lists
  the project's `CopyLogEntries` operations and matches the full name.
- **Retention `400d`** came back as 34495200 s, about 399.25 days. The plan sets `34560000s`, which is
  exactly 400 days.
- **Bucket names** follow the first hold, `aura-legal-hold-logs-<fa16|aura-main>-<default|required>`,
  plus the hold's id, so a second hold never writes into the first's bucket.
