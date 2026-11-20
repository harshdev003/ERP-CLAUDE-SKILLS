# Infra-vs-Real Failure Classification

## Goal

Distinguish transient infrastructure failures (safe to rerun immediately) from genuine code or config defects (require a fix before rerunning).

## Decision table

| Signal | Classification | Notes |
|---|---|---|
| Job duration ≤ 5s for a job that normally runs >30s | **INFRA** | Indicates the runner died or the job was killed before doing work |
| `"context deadline exceeded"` in log | **INFRA** | Network timeout — GCP/runner infra |
| `"i/o timeout"` in log | **INFRA** | Network I/O failure |
| `"connection reset by peer"` | **INFRA** | Network reset |
| `"connection refused"` | **INFRA** | Service unavailable on runner |
| `"resource temporarily unavailable"` | **INFRA** | OS resource exhaustion on runner |
| `"no space left on device"` | **INFRA** | Runner disk full |
| `exit status 70` (xcodebuild) | **INFRA** | Simulator boot failure — Apple-side flake |
| `SIGKILL` in log | **INFRA** | Process killed — likely OOM on runner |
| `"Xcode license"` | **INFRA** | Xcode not accepted on this runner image |
| `"No route to host"` (git checkout) | **INFRA** | Runner couldn't reach GitHub — network flake; job dies before building |
| `"Could not read from remote repository"` | **INFRA** | Git checkout couldn't read the remote — network/infra |
| `"could not fetch … from promisor remote"` | **INFRA** | Partial-clone fetch failed during checkout — network/infra |
| `"failed to checkout: exit status 128"` | **INFRA** | Checkout step failed (git/network) before any build step ran |
| `"returned error: 500 / 502 / 503 / 504 / 429"` (curl) | **INFRA** | A download the job needs was refused — tool installer, package index, release CDN |
| `500 Internal Server Error` / `502 Bad Gateway` / `503 Service Unavailable` / `504 Gateway Timeout` / `429 Too Many Requests` | **INFRA** | The same failure reported as a status line rather than by curl |
| `curl: (6)` `(7)` `(28)` `(35)` `(52)` `(55)` `(56)` | **INFRA** | curl transport failure — DNS, connect, timeout, TLS, empty reply, send/recv. No HTTP response arrived at all |
| `"Temporary failure in name resolution"` | **INFRA** | DNS failure on the runner |
| SwiftLint violation names in log | **REAL** | Code quality — must be fixed |
| `Build FAILED` (xcodebuild) | **REAL** | Compile error |
| `error: ` + meaningful output | **REAL** | Configuration or compile error |
| Test assertion failure | **REAL** | Logic defect |
| `fatal error:` | **REAL** | Crash in build or tests |
| No log output captured | **UNKNOWN** | Can't classify — surface full log URL |
| Log present but no matching signal | **UNKNOWN** | Review manually |

## Next-step mapping

| Classification | Recommended next step |
|---|---|
| INFRA | `rerun-circleci --workflow <id>` |
| REAL | "Fix the issue described above, then push a new commit" |
| UNKNOWN | Link to the CircleCI job URL |

## Notes

- **Duration heuristic is applied first** — a 0-second job always wins over content signals.
- **INFRA signals override REAL signals** — the INFRA list is scanned before the REAL list, so a log
  carrying both `i/o timeout` *and* `error:` classifies as INFRA. That ordering is what makes the
  network signals work at all: almost every failed download also prints `error:` somewhere, and the
  REAL list matches `error: ` as a bare substring, so a signal added to the wrong list is silently
  outvoted. A real failure wouldn't have reached the error line.
- **A download failure is keyed on the HTTP status, not on curl's exit code.** curl exits 22 for any
  HTTP ≥ 400, so matching `curl: (22)` alone would classify a **404** as infra — and a wrong URL or a
  version that no longer exists is a REAL defect that no number of reruns fixes. The transport-level
  codes (6/7/28/35/52/55/56) are unambiguous because no HTTP response arrived at all.
- **Simulator exit 70** is a well-known macOS runner flake in CircleCI — Xcode fails to boot the simulator, returns exit 70, job is killed. Always safe to rerun.
- **OOM (SIGKILL)** may warrant increasing the resource class in `.circleci/config.yml` if it happens repeatedly — but the immediate action is still a rerun.
