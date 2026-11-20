# AnalyticsReports

Monthly coach and community analytics — JSON pulled from the PaymentService Postgres `analytics` table, plus their CSV counterparts produced by `src/jsontocsv.js`. Files are organized under `reports/coaches/` and `reports/communities/` by report type (combined, payouts, subscriptions, sessions, etc.) with `{YYYY}-{MM}-…` naming.

**Security review:** `.claude/rules/security.md` is the checklist every reviewer applies before approving a PR: Claude, Cursor (`.cursor/rules/security.mdc`), BugBot (`.cursor/BUGBOT.md`) and people. All three are copies of AI Knowledge → Compliance → Security → Code Review Checklist, identical in every other code repo. Change that page first, then every copy.

## Skills

Skills live under `.claude/skills/<name>/SKILL.md` with supporting code in `<name>/scripts/`. Add an entry here whenever a new skill is added so Claude can find it.

| Skill | What it does | Trigger |
|---|---|---|
| [gather-previous-month-analytics](.claude/skills/gather-previous-month-analytics/SKILL.md) | Pulls last month's coach + community analytics from the PaymentService Postgres `analytics` table, writes the seven monthly JSON reports (with `result`-wrapper stripped where appropriate), and runs `src/jsontocsv.js` to emit CSVs. | "gather previous month analytics", "run the monthly coach reports", "monthly analytics" |

## Conventions

- Year-month prefix in filenames is two-digit zero-padded (`2026-04-…`, not `2026-4-…`). Older pre-2022 files used unpadded months — do not match that.
- `combined`, `payouts`, and `communities/payouts` JSON files are top-level arrays (no `result` wrapper). Subscription and session JSON files keep the `{result: […]}` shape.
- `src/jsontocsv.js` only walks `reports/coaches/combined/`, `reports/coaches/payouts/`, and `reports/communities/payouts/`. Other folders (`subscriptions/`, `sessions/`) intentionally have no CSV counterpart.
- The PaymentService `.env` (`/Users/jeromecurlier/Development/Aura/Services/PaymentService/.env`) holds production credentials. Never echo `POSTGRES_DATABASE_URI` into logs, commits, or responses.
