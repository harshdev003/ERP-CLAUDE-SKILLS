# report-deepgram-quota — methodology

## Balance-runway model (Deepgram is prepaid PAYG — no monthly quota)

Deepgram is prepaid pay-as-you-go: there is **no monthly allotment**, so "% of quota" is meaningless.
The signal is **runway** — how many days of prepaid balance remain at the current burn:

| Figure | Source |
|---|---|
| **Prepaid balance** (USD) | ACTUAL — `GET /v1/projects/{id}/balances` (sum of `amount` where `units=usd`) |
| **Audio hours** in the window | ACTUAL — `GET /v1/projects/{id}/usage?start=&end=` |
| **Spend** | DERIVED — `hours × --rate-per-hour` (the usage endpoint returns hours, not $) |
| **Burn/day** | DERIVED — `spend / --days` |
| **Runway** | DERIVED — `balance / burn_per_day` (days until the prepaid balance is empty) |

Default rate is **$0.348/hr** (Nova-3 Multilingual streaming PAYG — our current model); pass
`--rate-per-hour 0.288` to model monolingual (see PROD-3204). Default burn window is `--days 30`.

## Alert model (`--alert`)

- **Runway** — WARN at `--warn-runway-days` (30), CRITICAL at `--crit-runway-days` (14).
- **Balance floor** — CRITICAL when the prepaid balance is `<= --min-balance` (default 0).

When there's no usage in the window (burn ~$0) runway is n/a and the verdict is OK — the balance
isn't drawing down. Exit codes match the quota-skill family: `0`=OK, **`10`=WARN / `20`=CRITICAL**;
`1` (API failure) and `2` (config / project-scope error) stay reserved, so `$? -ge 10` is a real
alert. `--slack` posts to `SLACK_WEBHOOK_URL` (env only) when not OK.

## Auth + project scope

`DEEPGRAM_OWNER_API_KEY` (preferred, management key) or `DEEPGRAM_API_KEY`, sent as
`Authorization: Token …`, read from env only. The key must be a **member of the STT project** (a
`usage:read` key on it); a non-member key raises a scope error (exit 2) with guidance — see
`analyze-deepgram-costs` and PROD-3179 for the project-alignment story. The Deepgram console is the
source of truth for the exact balance.
