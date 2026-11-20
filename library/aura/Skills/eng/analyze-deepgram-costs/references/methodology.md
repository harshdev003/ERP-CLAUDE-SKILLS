# Deepgram cost methodology

How `analyze-deepgram-costs` turns Deepgram management-API data into a spend
report, and where every number comes from. The guiding principle (same as the
sibling `analyze-elevenlabs-costs`): **report actuals; label anything derived.**

## Deepgram at Aura

Deepgram is our speech-to-text (STT) provider. We run **nova-3** in real-time
(streaming) mode on the **LiveKit voice path** (the Aurie voice agent). There is
no Deepgram MCP — this skill is API-first.

## Auth

- Base URL: `https://api.deepgram.com`
- Every request carries `Authorization: Token <key>`.
- Key resolution: `DEEPGRAM_OWNER_API_KEY` (preferred) → `DEEPGRAM_API_KEY`
  (fallback). Read from the environment only — never printed, never in argv,
  never written to a file.

## Endpoints used

| Endpoint | Purpose | Actual or derived |
|---|---|---|
| `GET /v1/projects` | List projects the key is a member of; resolve which one to report on (or validate `--project-id`). Shape: `{"projects":[{"project_id","name",...}]}`. | — |
| `GET /v1/projects/{id}/usage?start=&end=` | Usage summary — audio hours + request counts per period. Shape: `{"start","end","resolution",{"results":[...]}}`; `results` may be empty. | **actual** hours/requests |
| `GET /v1/projects/{id}/usage/breakdown?start=&end=` | Per-model / per-feature detail (nested under each result period). | **actual** hours/requests |
| `GET /v1/projects/{id}/balances` | Prepaid balance. Shape: `{"balances":[{"balance_id","amount":<float>,"units":"usd",...}]}`. | **actual $** |
| `GET /v1/projects/{id}/billing/breakdown?start=&end=` | Per-period billed dollars, when the plan exposes it. Preferred source for actual spend. | **actual $** when non-empty |

Dates are plain `YYYY-MM-DD` (UTC), `end` inclusive. All windows in the script
are UTC-calendar-anchored.

## Project scoping caveat (important)

A management key can only see projects it is a **member of**. The Aura
**PRODUCTION STT traffic** lives in project `7e42b984-d5d4-4cdb-aba3-b5bd8408933f`
('Aura Health') — the project the LiveKit voice agent authenticates against
(`LivekitVoiceAgent/.env` `DEEPGRAM_API_KEY` is a member of it). The
`DEEPGRAM_OWNER_API_KEY` in settings is an Owner key in that same project, and a
member of only it, so the skill auto-selects the project via `/v1/projects` —
**no `--project-id` needed**. Usage is small during early voice rollout (~2–3
audio hrs/week); the prepaid balance (~$188) lives here too.

The skill still never hardcodes a project — it resolves via `/v1/projects` and
accepts `--project-id` if the key ever becomes a member of several projects.
Hitting a project the key isn't a member of returns HTTP 404
`{"category":"NOT_FOUND"}` (or 403 `INSUFFICIENT_PERMISSIONS`); the skill prints
a clear message and exits non-zero without a stack trace.

## Cost model — actual vs derived

1. **Prefer actual $.** If `billing/breakdown` returns per-period dollar figures
   (`amount` / `usd` / `total` / `cost`), the report leads with that sum labelled
   "actual, from billing breakdown". The prepaid `balances` amount is always
   rendered as an actual $ figure regardless.
2. **Otherwise derive.** When no actual per-period $ is available, the report
   shows **actual hours + requests** and derives
   `USD = total_hours × --rate-per-hour`, clearly labelled as an estimate. The
   only actual $ figure in that case is the prepaid balance.

The report always leads with the actuals (hours, requests, balance) and treats
derived USD as secondary, with a caveat line stating which mode was used.

## Per-hour rate default + source

`--rate-per-hour` defaults to **$0.348/hour** — our current model + plan.

- **Derivation:** Aura runs **Nova-3 Multilingual streaming** on **on-demand
  (pay-as-you-go)**: **$0.0058/min** → ×60 = **$0.348/hour**. Streaming (not
  pre-recorded) because the LiveKit path transcribes in real time.
- **Source:** <https://deepgram.com/pricing>, confirmed **2026-07-02**.
- **Plan / model rates (2026-07-02):**

| Model | On-demand (PAYG) | Growth |
|---|---|---|
| Nova-3 **Multilingual** streaming (current) | **$0.0058/min = $0.348/hr** | $0.0050/min = $0.300/hr |
| Nova-3 Monolingual streaming | $0.0048/min = $0.288/hr | $0.0042/min = $0.252/hr |

- **Cost-reduction levers (tracked as TODOs):**
  - **Monolingual instead of Multilingual** saves ~$0.0010/min (**~17%**) — evaluate whether Aurie's languages allow it.
  - **Growth plan** (from PAYG) saves ~14% on multilingual ($0.0050/min); we plan to sign up + turn on auto-load.
- We're on **On Demand**: buy-what-you-need, purchased credit never expires, and existing PAYG credit rolls into a Growth plan on upgrade. $200 free credit to start.

Override `--rate-per-hour` on a plan/model change (e.g. `0.300` for Growth-multilingual, `0.288` to model monolingual).

This rate is an **assumption** used only when actual $ is unavailable. It never
overrides an actual billed figure.

## No projection

Every number reflects what has already happened — no end-of-period
extrapolation. For a forward signal, read the hrs/day run-rate the report
prints, or run the skill repeatedly through the period.
