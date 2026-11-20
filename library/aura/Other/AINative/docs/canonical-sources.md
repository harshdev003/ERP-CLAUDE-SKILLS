# Canonical sources — shared files kept in sync

The registry of shared files that must be kept in sync across skill folders. Extracted from
[CLAUDE.md](../CLAUDE.md) so the always-loaded project memory stays under its size limit; this
file is the full, authoritative text. Read it before editing any file that has copies, and
propagate edits in the same PR.

Paths in `bash` blocks are repo-root-relative — run them from the repo root, not from `docs/`.

Below is the registry of shared files that must be kept in sync. **For each: the `find`/`diff -q` drift command and any shape-test are the authoritative membership check — the prose copy lists are only a convenience and have drifted before, so run the command rather than trusting the text.** Any output from a drift command is a warning to reconcile in the next PR.

**Canonical source for `best-practices.md`.** `plugins/shared/skills/create-aura-skill/references/best-practices.md` is canonical; `plugins/shared/skills/update-aura-skill/references/best-practices.md` is a copy (skills are self-contained per Architecture principle 1, so cross-skill reads aren't allowed). Copies in sibling skills; the `find` below is the authoritative membership check — edit the canonical first, propagate in the same PR.

```bash
canonical=plugins/shared/skills/create-aura-skill/references/best-practices.md
find plugins -name best-practices.md ! -path "$canonical" -exec diff -q "$canonical" {} \;
```

**Canonical source for `about-sections.md`.** [`plugins/shared/skills/harvest-knowledge/references/about-sections.md`](../plugins/shared/skills/harvest-knowledge/references/about-sections.md) is canonical for how the three knowledge skills resolve the AI Knowledge taxonomy entry point — that About's sections are inline body links rather than child pages, the `section_links` recipe, and the rule that an empty section map aborts the run. Copies in sibling skills; the `find` below is the authoritative membership check — edit the canonical first, propagate in the same PR.

```bash
canonical=plugins/shared/skills/harvest-knowledge/references/about-sections.md
find plugins -name about-sections.md ! -path "$canonical" -exec diff -q "$canonical" {} \;
```

**Canonical source for `insert_section.py`.** [`plugins/shared/skills/ingest-knowledge/scripts/insert_section.py`](../plugins/shared/skills/ingest-knowledge/scripts/insert_section.py) is canonical for adding a section or a `# Sources` line to an existing AI Knowledge page: `add_section` places the section above the page's Sources heading and the source lines at the end of its list, with one read before any write and never chaining on `results[-1]` ([ENG-1709](https://linear.app/aura-health/issue/ENG-1709)). `append_block_children` alone always appends at the end of the page, which put sections below Sources. Copy: `harvest-knowledge` ([ENG-1710](https://linear.app/aura-health/issue/ENG-1710)). Byte-identical, docstring included; behaviour is pinned over every copy by [`tests/test_ingest_section_before_sources.py`](../tests/test_ingest_section_before_sources.py). Edit the canonical first, propagate in the same PR.

```bash
canonical=plugins/shared/skills/ingest-knowledge/scripts/insert_section.py
find plugins -name insert_section.py ! -path "$canonical" -exec diff -q "$canonical" {} \;
```

**Canonical source for `armor_blocklist.py`.** [`plugins/eng/skills/block-ip/scripts/armor_blocklist.py`](../plugins/eng/skills/block-ip/scripts/armor_blocklist.py) is canonical for the Cloud Armor incident-block commands (`list`, `add`, `remove`, `expire` on `aura-web-edge` rules 100-149, with their Notion Cloud Armor Blocklist rows). It is adapted from AuraInfra `bin/armor-blocklist.py` (AuraInfra#162): gcloud through `gcloud_sa`, Notion through `requests`, importable, and no output naming an address. A change to the AuraInfra script is ported here by hand. Copy: `review-ip-blocks` ([ENGBACK-2114](https://linear.app/aura-health/issue/ENGBACK-2114)). Byte-identical, pinned by [`tests/test_ip_block_skills.py`](../tests/test_ip_block_skills.py). Edit the canonical first, propagate in the same PR.

```bash
canonical=plugins/eng/skills/block-ip/scripts/armor_blocklist.py
find plugins -name armor_blocklist.py ! -path "$canonical" -exec diff -q "$canonical" {} \;
```

**Canonical source for `thread_marks.py`.** [`plugins/product/skills/manage-ideas/scripts/thread_marks.py`](../plugins/product/skills/manage-ideas/scripts/thread_marks.py) is canonical for the marks the loop leaves on an idea's Slack thread as its row moves (the status → reaction map, the bot user id the harvester strips, `mark_thread`). Copies in sibling skills; the `find` below is the authoritative membership check — edit the canonical first, propagate in the same PR. Byte-identical, docstring included, enforced by [`tests/test_thread_marks.py`](../tests/test_thread_marks.py).

```bash
canonical=plugins/product/skills/manage-ideas/scripts/thread_marks.py
find plugins -name thread_marks.py ! -path "$canonical" -exec diff -q "$canonical" {} \;
```

**Canonical source for `outcomes.py` (cadence stops → follow-ups).** [`plugins/data/skills/run-all-harvest-cadence/scripts/outcomes.py`](../plugins/data/skills/run-all-harvest-cadence/scripts/outcomes.py) is canonical for turning an unattended cadence run's per-step outcomes into the `shared:raise-followup` fields it must post: the status → kind map (`failed`/`blocked`/`stopped` → `blocked`, `gate` → `decision`, `ok`/`out-of-period` → nothing), a due step with no outcome counted as `stopped`, the stable `<cadence>:<step>:stop` key, and ids cut to a prefix ([ENG-1975](https://linear.app/aura-health/issue/ENG-1975)). Copied into each skill a routine starts directly; the `find` below is the list. Byte-identical, docstring included; behaviour is pinned over every copy by [`tests/test_cadence_stop_followups.py`](../tests/test_cadence_stop_followups.py). Edit the canonical first, propagate in the same PR.

```bash
canonical=plugins/data/skills/run-all-harvest-cadence/scripts/outcomes.py
find plugins -name outcomes.py ! -path "$canonical" -exec diff -q "$canonical" {} \;
```

**Canonical source for `followup.py` and `routing.json` (raising a follow-up from a script).** [`plugins/shared/skills/raise-followup/scripts/followup.py`](../plugins/shared/skills/raise-followup/scripts/followup.py) and its [`references/routing.json`](../plugins/shared/skills/raise-followup/references/routing.json) are canonical for posting a follow-up: the refusals, one post per key, the card per kind and where follow-ups go. A job that runs outside Claude Code (a launchd script) cannot invoke the skill, so its skill carries copies of both, plus `slack_post.py` and `slack_read.py` from their own canonicals; today that is `eng:harvest-anthropic-usage`'s `weekly_check.py` ([ENG-1979](https://linear.app/aura-health/issue/ENG-1979)). Byte-identical, pinned by [`tests/test_usage_check_followups.py`](../tests/test_usage_check_followups.py). Edit the canonical first, propagate in the same PR; a change to `routing.json` (a channel, a role) must reach every copy.

```bash
for f in scripts/followup.py references/routing.json; do
  canonical=plugins/shared/skills/raise-followup/$f
  find plugins -path "*/$f" ! -path "$canonical" -exec diff -q "$canonical" {} \;
done
```

**Canonical source for `prior_guardrails.py`.** [`plugins/eng/skills/investigate-conversation-guardrail/scripts/prior_guardrails.py`](../plugins/eng/skills/investigate-conversation-guardrail/scripts/prior_guardrails.py) is canonical for the cross-channel "has this user tripped a guardrail before" lookup, the `📧 Last CS email` line ([ENG-1541](https://linear.app/aura-health/issue/ENG-1541)) and the one-CS-email-per-user-per-day reviewer line ([ENG-1540](https://linear.app/aura-health/issue/ENG-1540)). Copy in `investigate-memory-ingestion-guardrail`; the `find` below is the authoritative membership check — edit the canonical first, propagate in the same PR. Byte-identical, docstring included, enforced by [`tests/test_prior_guardrails.py`](../tests/test_prior_guardrails.py).

```bash
canonical=plugins/eng/skills/investigate-conversation-guardrail/scripts/prior_guardrails.py
find plugins -name prior_guardrails.py ! -path "$canonical" -exec diff -q "$canonical" {} \;
```

**Canonical source for `fetch_conversation.py` (whole guardrail conversation).** [`plugins/eng/skills/investigate-conversation-guardrail/scripts/fetch_conversation.py`](../plugins/eng/skills/investigate-conversation-guardrail/scripts/fetch_conversation.py) is canonical for reading one whole Aurie conversation behind a guardrail alert — stored-transcript export + llmCall ledger + GCP handoff categories, with the Responses fallback and the NOT ANALYSABLE exit. Copy in `data:analyze-conversation-guardrails`, which runs it per alert in bulk ([ENG-1568](https://linear.app/aura-health/issue/ENG-1568)). Edit the canonical first, propagate in the same PR. Behaviour is pinned over every copy by [`tests/test_conversation_guardrail_fetch.py`](../tests/test_conversation_guardrail_fetch.py).

```bash
canonical=plugins/eng/skills/investigate-conversation-guardrail/scripts/fetch_conversation.py
find plugins -name fetch_conversation.py ! -path "$canonical" -exec diff -q "$canonical" {} \;
```

**Canonical source for `gcloud_sa.py` (gcloud as the `GCP_KEY` service account).** [`plugins/eng/skills/investigate-conversation-guardrail/scripts/gcloud_sa.py`](../plugins/eng/skills/investigate-conversation-guardrail/scripts/gcloud_sa.py) is canonical for running the `gcloud` CLI from a skill. It activates `GCP_KEY` into a private temp `CLOUDSDK_CONFIG`, never touches the user's own gcloud config, and uses the user login only when `GCP_KEY` is unset. A library (`run()`, `credential()`) and a CLI (`python3 …/scripts/gcloud_sa.py <gcloud args>`) for SKILL.md commands. Every skill that calls `gcloud` ships a copy ([ENG-1607](https://linear.app/aura-health/issue/ENG-1607)). Byte-identical, docstring included. Behaviour is pinned over every copy by [`tests/test_gcloud_sa.py`](../tests/test_gcloud_sa.py), and [`tests/test_gcloud_gate.py`](../tests/test_gcloud_gate.py) fails on any `gcloud` call under `plugins/` that bypasses it. Edit the canonical first, propagate in the same PR.

```bash
canonical=plugins/eng/skills/investigate-conversation-guardrail/scripts/gcloud_sa.py
find plugins -name gcloud_sa.py ! -path "$canonical" -exec diff -q "$canonical" {} \;
```

**Canonical source for `rtdb_get.js` (reading prod RTDB).** [`plugins/eng/skills/read-rtdb-paths/scripts/rtdb_get.js`](../plugins/eng/skills/read-rtdb-paths/scripts/rtdb_get.js) is canonical for reading exact prod RTDB paths from a skill: the Admin SDK realtime protocol, never REST or Python `firebase_admin.db`; it refuses the root, top-level nodes, `/users` itself and shallow or query reads; at most 20 reads in flight; a bulk run past 200 paths needs `--bulk`; it prints field names and counts, not values, by default. The rule it follows is `Tools/Firebase` → "Reading prod data" in AI Knowledge. A skill that reads RTDB ships a copy ([ENGBACK-2601](https://linear.app/aura-health/issue/ENGBACK-2601)). Byte-identical. Behaviour is pinned over every copy by [`tests/test_rtdb_get.py`](../tests/test_rtdb_get.py), which also checks the copies match. Edit the canonical first, propagate in the same PR.

```bash
canonical=plugins/eng/skills/read-rtdb-paths/scripts/rtdb_get.js
find plugins -name rtdb_get.js ! -path "$canonical" -exec diff -q "$canonical" {} \;
```

**Canonical source for `trigger_reasons.md` (why a guardrail fired).** [`plugins/eng/skills/investigate-conversation-guardrail/references/trigger_reasons.md`](../plugins/eng/skills/investigate-conversation-guardrail/references/trigger_reasons.md) is canonical for the trigger-reason list, its attributes and the `Trigger reason:` line the triage skill writes. `data:analyze-conversation-guardrails` carries a byte-identical copy, classifies untriaged alerts in the same vocabulary and parses the line back from Slack ([ENG-1567](https://linear.app/aura-health/issue/ENG-1567) / [ENG-1568](https://linear.app/aura-health/issue/ENG-1568)). Edit the canonical first, propagate in the same PR. Byte identity, the line grammar and the reason set are enforced by [`tests/test_analyze_conversation_guardrails.py`](../tests/test_analyze_conversation_guardrails.py).

```bash
canonical=plugins/eng/skills/investigate-conversation-guardrail/references/trigger_reasons.md
find plugins -name trigger_reasons.md ! -path "$canonical" -exec diff -q "$canonical" {} \;
```

**Canonical source for `resolve_window.py`.** [`plugins/eng/skills/analyze-listens/scripts/resolve_window.py`](../plugins/eng/skills/analyze-listens/scripts/resolve_window.py) is canonical for the Aura cost / listens skill family. Copies in sibling skills; the `find` below is the authoritative membership check — edit the canonical first, propagate in the same PR.

**Copies are byte-identical, docstring included, and CI enforces it** ([`tests/test_resolve_window_copies.py`](../tests/test_resolve_window_copies.py), copies discovered by glob). The docstring is skill-agnostic for that reason — never personalise a copy's header with its own skill name, which is how 8 of 9 copies read as drifted while their code was identical ([ENG-1414](https://linear.app/aura-health/issue/ENG-1414)). `data:report-aurie-usage` is the one exception: a different implementation with an inclusive `to`.

```bash
canonical=plugins/eng/skills/analyze-listens/scripts/resolve_window.py
find plugins -name resolve_window.py ! -path "$canonical" \
  ! -path "*/report-aurie-usage/*" \
  -exec diff -q "$canonical" {} \;
```

**Canonical source for `gcp.py` / `normalize.py`.** [`plugins/eng/skills/investigate-gcp-logs/scripts/gcp.py`](../plugins/eng/skills/investigate-gcp-logs/scripts/gcp.py) and [`normalize.py`](../plugins/eng/skills/investigate-gcp-logs/scripts/normalize.py) are canonical for the GCP-logs skill family (auth ladder, Cloud Logging query wrapper, Logs Explorer URL decoder, and the Format A/B log normalizer). Copies in sibling skills; the `find` below is the authoritative membership check — edit the canonical first, propagate in the same PR.

**`search-gcp-by-user` carried a copy this list did not name**

**Two files named `normalize.py` are not in this family and must never be reconciled into it.**

```bash
canonical=plugins/eng/skills/investigate-gcp-logs/scripts/gcp.py
find plugins -name gcp.py ! -path "$canonical" -exec diff -q "$canonical" {} \;

canonical=plugins/eng/skills/investigate-gcp-logs/scripts/normalize.py
find plugins -name normalize.py ! -path "$canonical" \
  ! -path "*/investigate-bugsnag-errors/*" ! -path "*/investigate-openai-trace/*" \
  -exec diff -q "$canonical" {} \;
```

**Membership is asserted by shape, not by filename.** (tests/test_gcp_normalize.py; [ENG-1138](https://linear.app/aura-health/issue/ENG-1138)).

**Canonical source for `bugsnag.py`.** [`plugins/eng/skills/investigate-bugsnag-errors/scripts/bugsnag.py`](../plugins/eng/skills/investigate-bugsnag-errors/scripts/bugsnag.py) is canonical for the Bugsnag Data Access API v2 helper — token resolution, the mandatory `X-Version: 2` header, `Link: rel="next"` pagination, 429 backoff within a per-request budget plus a cooldown file shared by every process on the token (raising `BugsnagRateLimited`), a one-day disk cache for org/projects/event fields, the read methods (`list_projects` / `list_errors` / `list_releases` / `get_error` / `list_events` / `get_event`), the write methods (`update_error` / `add_comment` over `_write`), and `parse_bugsnag_url`. Copies in sibling skills; the `find` below is the authoritative membership check — edit the canonical first, propagate in the same PR.

**The canonical is the union of what the forks had, so every copy carries the write methods**

```bash
canonical=plugins/eng/skills/investigate-bugsnag-errors/scripts/bugsnag.py
find plugins -name bugsnag.py ! -path "$canonical" -exec diff -q "$canonical" {} \;
```

**`skills/build-bug-metrics-dashboard/scripts/fetch_bugsnag.py`, now in
[AuraHealth/OKRs](https://github.com/AuraHealth/OKRs), is not in this family and must not be reconciled into it.**

**Since the dashboard skills moved to their own repo this is now a cross-repo divergence,
which makes it strictly harder to catch: no `find plugins` in this repo will ever reach that
file.**

**This family was forked six ways with no canonical and no entry here, and the drift was not cosmetic** (tests/test_bugsnag_helper.py; [ENG-1138](https://linear.app/aura-health/issue/ENG-1138)).

**Canonical source for `projects.py` (Bugsnag routing).** [`plugins/eng/skills/investigate-bugsnag-errors/scripts/projects.py`](../plugins/eng/skills/investigate-bugsnag-errors/scripts/projects.py) is canonical for the Bugsnag slug → app-family + **suspect-repo** mapping — the `stack` key that picks the stacktrace formatter, the `high_pii` flag that tightens Mode 3 redaction, and the `repo` that Mode 2.5 hands to `eng:code-investigator`. Copies in sibling skills; the `find` below is the authoritative membership check — edit the canonical first, propagate in the same PR.

**`plugins/eng/skills/investigate-openai-trace/scripts/projects.py` is not in this family and must never be reconciled into it.** (tests/test_bugsnag_projects.py).

```bash
canonical=plugins/eng/skills/investigate-bugsnag-errors/scripts/projects.py
find plugins -name projects.py ! -path "$canonical" \
  ! -path "*/investigate-openai-trace/*" \
  -exec diff -q "$canonical" {} \;
```

**A wrong `repo` here does not fail — it produces a confident wrong answer, which is why the mapping is verified rather than inferred.** ([ENG-1169](https://linear.app/aura-health/issue/ENG-1169)).

**Verify a row against Bugsnag's own `api_key`, never against the slug.**

**The mapping is implemented twice — in Python and as a prose table** (tests/test_bugsnag_projects.py).

**Canonical source for `livekit_core.py`.** [`plugins/eng/skills/analyze-livekit-usage/scripts/livekit_core.py`](../plugins/eng/skills/analyze-livekit-usage/scripts/livekit_core.py) is canonical for the LiveKit monitoring skill family (GCP auth ladder, the forwarded-log query helpers anchored to `logName=projects/firebase-auratech16/logs/aurie-voice-agent`, session/turn-latency parsing, and UID redaction). Copies in sibling skills; the `find` below is the authoritative membership check — edit the canonical first, propagate in the same PR.

A **partial derived copy** — NOT byte-identical — lives at [`plugins/eng/skills/store-aurie-user-daily-cost/scripts/livekit_minutes.py`](../plugins/eng/skills/store-aurie-user-daily-cost/scripts/livekit_minutes.py).

**`livekit_minutes.py` diverges from the canonical on quota handling, deliberately.** ([ENG-1214](https://linear.app/aura-health/issue/ENG-1214)).

**A quota rejection raises rather than returning what it got.**

```bash
# byte-identical copies only — livekit_minutes.py is a partial derived copy and is excluded
canonical=plugins/eng/skills/analyze-livekit-usage/scripts/livekit_core.py
find plugins -name livekit_core.py ! -path "$canonical" -exec diff -q "$canonical" {} \;
```

**`LATENCY_METRICS` is the one constant in this file that a copy has diverged on, and the fix was to widen the canonical rather than carve out an exception** ([ENG-1218](https://linear.app/aura-health/issue/ENG-1218)).

**The `diff -q` guard is green when all six copies agree on a *narrowed* tuple** (tests/test_livekit_latency_metrics.py).

**`report-livekit-quota` carried a copy this list did not name** ([#510](https://github.com/AuraHealth/AINative/pull/510); [#518](https://github.com/AuraHealth/AINative/pull/518); [ENG-1214](https://linear.app/aura-health/issue/ENG-1214)).

**Membership is checked by shape, in both directions.** (tests/test_livekit_core_family.py).

**A capped read is raised, not returned** ([ENG-1297](https://linear.app/aura-health/issue/ENG-1297)).

**The sibling `gcp.py` family already had the answer, at the call site**

**Raising was not sufficient on its own, and the reason generalises.** (tests/test_livekit_truncation.py).

**`report-livekit-quota` no longer bills from a log walk at all.**

**Canonical source for `livekit_plan.py`.** [`plugins/eng/skills/report-livekit-quota/scripts/livekit_plan.py`](../plugins/eng/skills/report-livekit-quota/scripts/livekit_plan.py) is canonical for the LiveKit Cloud **plan of record** — which plan we are on (**Scale**, renewal day 1), every metered pool's included allotment and overage rate, the Inference rate card, and `effective_rate_per_min()`. Copies in sibling skills; the `find` below is the authoritative membership check — edit the canonical first, propagate in the same PR.

**The two consumers had drifted on the one fact that decides whether LiveKit costs anything at the margin.**

**Three properties are pinned by [`tests/test_livekit_plan.py`](../tests/test_livekit_plan.py) because each fails by producing a plausible number rather than an error**

- **Inside an allotment a unit is free.** The published per-minute figures are *overage* rates. Pricing every minute at one books a charge that does not exist — `voice_livekit` booked $119.90 for August 2026 against a $501.14 invoice whose variable part was **$1.14**.
- **The overage rate is the floor, not the cost.** Each plan is priced so `subscription / allotment` equals the overage rate exactly at 100% utilization, so the effective rate is always ≥ it. Quoting `$0.01/min` as the cost of a minute quotes the theoretical minimum. The honest figure is `effective_rate_per_min()` — actual invoice ÷ actual units, which keeps the dollar total an actual and derives only the allocation.
- **The pools bind at different times, so an unmeasured pool must be *listed*, never dropped.** Six of the seven pools are not derivable from the forwarded logs, and the one that is — agent-session minutes — is the furthest from binding. **Inference credits is now reported without becoming derivable**: `eng:store-aurie-user-daily-cost` stores the drawdown, so the quota skill reads the cost store rather than the logs, and the pool's `derivable` flag stays `False` because the source changed, not the logs. A month spanning days the store could not measure reports a **PARTIAL** lower bound (`≥ $X`, `N of M days unmeasured`) rather than a figure — reporting the bound as the value would be a confident understatement of how full the block is, which defeats the only question a quota report exists to answer. The pool that actually blew was **Krisp** (August 2026: 10,000 of 10,000, 955 minutes over), while session minutes sat at 23%. An absent pool reads exactly like a healthy one, so `build_pool_report` emits `UNMEASURED` rows and the `--alert` verdict states its own scope. Same rule as *Tag, don't drop, wherever the output persists* in [CLAUDE.md](../CLAUDE.md).

The table is **hand-read off the dashboard and dated** (`PLAN_FACTS_VERIFIED`) — LiveKit exposes no billing or usage API, and the Cloud dashboard is server-rendered with no JSON endpoint behind it, so nothing here can refresh itself and an undated table ages into fiction without changing.

```bash
canonical=plugins/eng/skills/report-livekit-quota/scripts/livekit_plan.py
find plugins -name livekit_plan.py ! -path "$canonical" -exec diff -q "$canonical" {} \;
```

**An unread billing period must not render like a clean one** ([ENG-1298](https://linear.app/aura-health/issue/ENG-1298)).

`DASHBOARD_READINGS` gives the monthly read a home: one dated entry per billing period, hand-read off LiveKit Cloud, units matching the dashboard's own rows.

**`OVERAGE_BOOKING_THRESHOLD_USD` decides whether an amount is *booked*, never whether it is *shown*.**

**An actual outranks a bound, so the preference is conditional.** (tests/test_livekit_dashboard_reading.py).

**LiveKit is booked from the invoice, not a per-minute rate** ([PROD-5998](https://linear.app/aura-health/issue/PROD-5998)).

`aurie_per_user_daily_cost` therefore carries three LiveKit columns rather than one:

| column | what it is |
|---|---|
| `voice_livekit` | the user's minutes × the plan's **credit rate** — `subscription ÷ included allotment`, Scale = **$0.01/min**.

**The subscription is a block of prepaid credits, so a used minute costs what the block paid for it.**

**Unused capacity is booked, and it is booked to nobody.**

**Unused is a month-to-date delta, never a per-day `max(0, accrual − consumed)`.**

**The two regimes meet without a seam** (tests/test_livekit_booking.py).

**The minutes column exists to end a recovery trick, and the trick had to survive one last time to do it.**

**Invoiced overage on a pool the logs cannot measure is booked to the `Other` row, never split per user.**

Pinned by [`tests/test_livekit_booking.py`](../tests/test_livekit_booking.py): a month of accruals sums to the subscription, February accrues more per day than August, a quiet day still books the fee, daily overages sum to the month's, August restates from $119.90 to $501.14, and the two schemas (CSV + BigQuery) agree.

**STT is billed by whoever the transport routed it through, and reading the wrong source returns a well-formed $0** ([PROD-5995](https://linear.app/aura-health/issue/PROD-5995)).

**$0 is the correct figure post-cutover, which is why the fix is not a number.**

Three properties, each pinned by [`tests/test_deepgram_transport.py`](../tests/test_deepgram_transport.py) because each fails by producing a plausible number:

- **The transport is read from the agent's structured log fields**, never a regex over a message — `stt_provider` / `stt_seconds` arrive as `extra=` payload keys, so a wording change cannot silently reclassify a day.
- **An unmeasured session is excluded and counted, never folded in as zero.** Speech seconds are on a different clock from room minutes — a different quantity, not a scaled one, and the ratio between them is **not measured** — and the field only exists from the 2026-09 agent release — so a day that merely predates it must read as *unmeasured* (`stt_sessions_measured`), not as a day with no speech. Same rule as *Tag, don't drop, wherever the output persists* in [CLAUDE.md](../CLAUDE.md). Correspondingly, a *stated* transport with no measured duration still routes to Inference: trusting the absent measurement over the stated routing is the whole mechanism being removed.
- **A $0 nothing explains is tagged, loudly.** `unexplained_deepgram_zero` returns a `deepgram_transport_unknown` note when the column is $0, LiveKit minutes are > 0 and nothing else explains it — naming the transports it saw, so a broken read stays distinguishable from a routing change, and saying **UNMEASURED STT** rather than "$0 of speech". `deepgram_day_total` already never returns a bare zero for a failed *read*; this closes the other half.

**How full the credit block is is a stored column, because nothing can derive it** ([PROD-6008](https://linear.app/aura-health/issue/PROD-6008)).

It cannot be recomputed: `voice_deepgram` is $0 inside the block **by design**, so the dollar column erases the very quantity needed to reconstruct the drawdown, and the store is append-only per day.

**That guard first shipped as a refusal, and the blast radius was wrong.**

**The same deploy gap makes 2026-09-10 → 09-14 permanently unmeasured.**

**The rejection was argued with a number that had never been measured**

Two properties are pinned by [`tests/test_deepgram_transport.py`](../tests/test_deepgram_transport.py) because a test of the arithmetic alone passes against the broken store — which is how the literal shipped: the read-back is **actually wired into `main()`**, and the `Other` row's gate includes the drawdown remainder (the gate is driven by cost totals, and a drawdown is not a cost, so a day whose only leftover is drawdown would write no row and lose it — delaying the month's crossing day, silently).

**Canonical source for `circleci_api.py`.** [`plugins/eng/skills/investigate-circleci/scripts/circleci_api.py`](../plugins/eng/skills/investigate-circleci/scripts/circleci_api.py) is canonical for the CircleCI REST helper (token resolution, v1.1 build/log fetch, v2 pipeline/workflow/job fetch, v2 rerun). Copies in sibling skills; the `find` below is the authoritative membership check — edit the canonical first, propagate in the same PR.

```bash
canonical=plugins/eng/skills/investigate-circleci/scripts/circleci_api.py
find plugins -name circleci_api.py ! -path "$canonical" -exec diff -q "$canonical" {} \;
```

**Canonical source for `mixpanel_api.py`.** [`plugins/data/skills/analyze-user-behavior/scripts/mixpanel_api.py`](../plugins/data/skills/analyze-user-behavior/scripts/mixpanel_api.py) is canonical for the Mixpanel REST API helper (HTTP Basic auth, Engage + Export + Insights wrappers, property-fallback `distinct_id` resolution, and the project-calendar clamp on the Export window). Copies in sibling skills; the `find` below is the authoritative membership check — edit the canonical first, propagate in the same PR.

**On the read-side dedupe.**

**The key is `(event, $distinct_id, time, $insert_id)`, never `$insert_id` alone**

**Match the ingest contract exactly rather than the observed duplicates.** (tests/test_mixpanel_api.py).

**On the project-calendar clamp.** (tests/test_mixpanel_api.py; [ENG-1071](https://linear.app/aura-health/issue/ENG-1071)).

```bash
canonical=plugins/data/skills/analyze-user-behavior/scripts/mixpanel_api.py
find plugins -name mixpanel_api.py ! -path "$canonical" -exec diff -q "$canonical" {} \;
```

**Canonical source for `slack_post.py`.** [`plugins/shared/skills/post-to-slack/scripts/slack_post.py`](../plugins/shared/skills/post-to-slack/scripts/slack_post.py) is canonical for the Slack posting helper — the shared mechanics for `chat.postMessage`, the external-upload file flow, thread replies, `chat.getPermalink`, `conversations.list` channel resolution, `search.messages`, and the `Tools/Slack` attribution footer (transport prefers `requests`, falls back to `curl`; never `urllib`). Copies in sibling skills; the `find` below is the authoritative membership check — edit the canonical first, propagate in the same PR. ([PROD-2757](https://linear.app/aura-health/issue/PROD-2757)).

```bash
canonical=plugins/shared/skills/post-to-slack/scripts/slack_post.py
find plugins -name slack_post.py ! -path "$canonical" -exec diff -q "$canonical" {} \;
```

**Canonical source for `slack_read.py`.** [`plugins/shared/skills/read-slack/scripts/slack_read.py`](../plugins/shared/skills/read-slack/scripts/slack_read.py) is canonical for the Slack **read** helper — the companion to `slack_post.py`, covering the read mechanics: windowed `conversations.history` (cursor-paginated), single-message fetch by ts, `conversations.replies` (thread fetch), over a shared 429-retry transport (prefers `requests`, falls back to `curl`; never `urllib`). Copies in sibling skills; the `find` below is the authoritative membership check — edit the canonical first, propagate in the same PR. ([PROD-2764](https://linear.app/aura-health/issue/PROD-2764)).

```bash
canonical=plugins/shared/skills/read-slack/scripts/slack_read.py
find plugins -name slack_read.py ! -path "$canonical" -exec diff -q "$canonical" {} \;
```

**Canonical source for `fetch_cancellations.py`.** [`plugins/data/skills/investigate-cancellation-feedback/scripts/fetch_cancellations.py`](../plugins/data/skills/investigate-cancellation-feedback/scripts/fetch_cancellations.py) is canonical for the `#ai-feedback` cancellation-feed parser — the k8bot message parser that reads `*New Cancellation Feedback*` threads from Slack, normalises fields (uid, reason, feedback, days_to_cancel, permalink, is_test), and emits `{"entries": [...], "counts": {...}}` JSON. Copies in sibling skills; the `find` below is the authoritative membership check — edit the canonical first, propagate in the same PR.

**The two timestamps in a k8bot post are on different clocks.** (tests/test_days_to_cancel.py).

```bash
canonical=plugins/data/skills/investigate-cancellation-feedback/scripts/fetch_cancellations.py
find plugins -name fetch_cancellations.py ! -path "$canonical" -exec diff -q "$canonical" {} \;
```

**Canonical source for `responses_reader.py`.** [`plugins/data/skills/analyze-user-conversations/scripts/responses_reader.py`](../plugins/data/skills/analyze-user-conversations/scripts/responses_reader.py) is canonical for the ledger→OpenAI Responses read path (BigQuery `llm.llm_call` via `llm_ledger.py` → `/v1/responses` + `input_items`) and for `CONVERSATION_PROMPTERS`, the set of prompters whose rows are transcript turns. Copies are byte-identical; behaviour is pinned over every copy by [`tests/test_responses_reader.py`](../tests/test_responses_reader.py). **The prompter set is also encoded under a different filename** — `report-aurie-conversations/scripts/report.py` `CONVERSATION_TYPES` / `SECONDARY_TYPES` — which the `diff -q` below cannot see; `test_every_transcript_prompter_is_a_known_type` in [`tests/test_ledger_reader.py`](../tests/test_ledger_reader.py) holds the two together. **Neither list can see IntelligenceService adding a name**, which is how `guardrails-handler-prompter` (#766) and `quick-processing-sentinel-prompter` (#1192) went missing. [`tests/test_is_prompter_names.py`](../tests/test_is_prompter_names.py) holds both, and the qa `--prompter` choices, to a snapshot of IS's stored names. CI can't reach IS, so after IS adds, renames or merges a prompter, refresh the snapshot with `python3 scripts/refresh-is-prompter-names.py` (`--check` reports drift) and the test names every list that still needs the change.

```bash
canonical=plugins/data/skills/analyze-user-conversations/scripts/responses_reader.py
find plugins -name responses_reader.py ! -path "$canonical" -exec diff -q "$canonical" {} \;
```

**Canonical source for `ip_location.py`.** [`plugins/eng/skills/lookup-ip-location/scripts/ip_location.py`](../plugins/eng/skills/lookup-ip-location/scripts/ip_location.py) is canonical for locating an IP address with ipgeolocation.io (`IPGEOLOCATION_API_KEY`): country, region, city, ISP (`asn.organization`) and ASN, a reason code instead of an exception, and a per-process cache because each lookup is a credit. Skills that need a location carry a byte-identical copy. Behaviour is pinned over every copy by [`tests/test_ip_location.py`](../tests/test_ip_location.py).

```bash
canonical=plugins/eng/skills/lookup-ip-location/scripts/ip_location.py
find plugins -name ip_location.py ! -path "$canonical" -exec diff -q "$canonical" {} \;
```

**Canonical source for `llm_ledger.py`.** [`plugins/data/skills/analyze-user-conversations/scripts/llm_ledger.py`](../plugins/data/skills/analyze-user-conversations/scripts/llm_ledger.py) is canonical for reading the IntelligenceService `llmCall` ledger from BigQuery (`aura-main.llm.llm_call`) ([ENG-1657](https://linear.app/aura-health/issue/ENG-1657)). `rows(params)` takes the endpoint's query parameters and returns rows in its shape. It always sends a `createdAt` bound (the table requires a partition filter) and never deduplicates: a repeated `openaiId` can be a real handoff to another prompt. It imports the sibling `bq.py`, so every copy sits beside one. Copies are byte-identical; behaviour is pinned over every copy by [`tests/test_llm_ledger.py`](../tests/test_llm_ledger.py).

```bash
canonical=plugins/data/skills/analyze-user-conversations/scripts/llm_ledger.py
find plugins -name llm_ledger.py ! -path "$canonical" -exec diff -q "$canonical" {} \;
```

**Canonical source for `coach_calls.py`.** [`plugins/data/skills/analyze-conversation-guardrails/scripts/coach_calls.py`](../plugins/data/skills/analyze-conversation-guardrails/scripts/coach_calls.py) is canonical for turning a stored coach call into an eval-dataset row: the request as sent (`exact_request`), its parts in any Intelligence Service layout — prompt body, context, `### Current Time` developer instructions, conversation without the 1.40 `---` delimiters (`coach_parts`, `with_parts`), production's tools/params (`production_config`) and de-identification (`deidentify`, `residue`). The guardrail flow (`reproduce.py`) and the conversation-rating flow (`investigate-user-rating/scripts/feedback_dataset.py`) both import it, so a row is built and de-identified one way. Copies are byte-identical; [`tests/test_feedback_dataset.py`](../tests/test_feedback_dataset.py) asserts that and runs `deidentify` against every copy.

```bash
canonical=plugins/data/skills/analyze-conversation-guardrails/scripts/coach_calls.py
find plugins -name coach_calls.py ! -path "$canonical" -exec diff -q "$canonical" {} \;
```

**Canonical source for `release_contract.yaml` / `release_contract.py`.** [`plugins/eng/skills/qa-conversation/references/release_contract.yaml`](../plugins/eng/skills/qa-conversation/references/release_contract.yaml) is canonical for what blocks a prompt release per change class (Safety Suite draws, locked-suite scope, drives, source cases). `product:evaluate-prompt-candidate` and `product:share-prompt-candidate` carry byte-identical copies of the YAML and of the product reader `release_contract.py`; [`tests/test_release_contract.py`](../tests/test_release_contract.py) finds every copy by glob and fails on drift, and checks the evaluate SKILL.md table against the file.

```bash
canonical=plugins/eng/skills/qa-conversation/references/release_contract.yaml
find plugins -name release_contract.yaml ! -path "$canonical" -exec diff -q "$canonical" {} \;
```

**Canonical source for `fetch_logs.py`.** [`plugins/data/skills/analyze-user-conversations/scripts/fetch_logs.py`](../plugins/data/skills/analyze-user-conversations/scripts/fetch_logs.py) is canonical for the OpenAI stored-completions reader (conversation walk/pagination, `run_user_id()` / `run_conversation_id()`, turn reconstruction, Braintrust fallback). Copies in sibling skills; the `find` below is the authoritative membership check — edit the canonical first, propagate in the same PR.

```bash
canonical=plugins/data/skills/analyze-user-conversations/scripts/fetch_logs.py
find plugins -name fetch_logs.py ! -path "$canonical" \
  ! -path "*/investigate-user-rating/*" ! -path "*/analyze-user-journal/*" \
  ! -path "*/run-feedback-analysis-cadence/*" ! -path "*/report-conversation-patterns/*" \
  -exec diff -q "$canonical" {} \;
```

**`report-conversation-patterns` carried a copy this section did not name, and the omission made the guard above permanently red** ([ENG-1311](https://linear.app/aura-health/issue/ENG-1311)).

**Three of the five copies carrying the Braintrust conversation builder are forks, so no `diff -q` can answer who carries a given behaviour** (tests/test_bt_transcript_span.py; [ENG-1311](https://linear.app/aura-health/issue/ENG-1311)).

**One pre-existing behavioural divergence is deliberately left alone here**

**Canonical source for `fetch_ratings.py`.** [`plugins/data/skills/investigate-user-rating/scripts/fetch_ratings.py`](../plugins/data/skills/investigate-user-rating/scripts/fetch_ratings.py) is canonical for the k8bot `*[TYPE] RATED*` parser — it reads `#ai-data-intelligence` message history, parses all six rating shapes, applies window/polarity/shape filters, and drops internal + infra-only entries. Copies in sibling skills; the `find` below is the authoritative membership check — edit the canonical first, propagate in the same PR.

**Rating volume is not a test signal, and this is the one place that states the rule.** ([ENG-1048](https://linear.app/aura-health/issue/ENG-1048)).

- **The bar is ≥20, never tighter.** The three `fetch_ratings.py` copies sit at 20; `chart-monthly-ratings`' `chart_ratings.py` at 40 (a monthly window).
- **The count's span belongs to the rule, never to the caller's window.** `harvest-conversation-patterns`' `fetch_sweep_analyses.py` takes the peak inside any rolling **7 days**. A window-relative total lets the pass's width decide who counts as a test loop — the same account at the same rate qualifies in a wide re-harvest and not in a weekly sweep — which is a filter nobody can state the meaning of without also naming the pass. Measured cost of getting this wrong, and the reason the destructive version went unnoticed for so long: [ENG-1132](https://linear.app/aura-health/issue/ENG-1132), and *The drift guards compare copies, never implementations* in [CLAUDE.md](../CLAUDE.md).

Enforced rather than documented: [`tests/test_rating_volume_rule.py`](../tests/test_rating_volume_rule.py) asserts the rule across every implementation **and fails when a new one appears under any filename** — the check a `diff -q` structurally cannot be.

**Two signals mark an account internal, and both are statements about the account.**

**`high_volume` and the API pass read different denominators — keep them in separate functions.**

**A fourth consumer carries no copy.**

**`FILTER_GENERATION` is the filter's declared version.** ([ENG-1055](https://linear.app/aura-health/issue/ENG-1055)).

```bash
canonical=plugins/data/skills/investigate-user-rating/scripts/fetch_ratings.py
find plugins -name fetch_ratings.py ! -path "$canonical" -exec diff -q "$canonical" {} \;
```

**Canonical source for `internal_accounts.py`.** [`plugins/data/skills/investigate-user-rating/scripts/internal_accounts.py`](../plugins/data/skills/investigate-user-rating/scripts/internal_accounts.py) is canonical for reading the `Team/Internal Accounts` page into an allowlist — Firebase UIDs, provider ids (`google:…` / `apple:…`), emails and wildcard email patterns, scoped to the *Employee accounts* → *Sources* span. Every script that takes `--internal-ids` also takes `--internal-accounts-file` and calls it, so no consumer hand-assembles the id list ([ENG-1436](https://linear.app/aura-health/issue/ENG-1436): a hand-built list dropped both `google:` ids). Copies ship next to `fetch_ratings.py` (all three), `fetch_pmf_responses.py` and `chart_ratings.py`; behaviour is pinned over every copy by [`tests/test_internal_accounts.py`](../tests/test_internal_accounts.py).

```bash
canonical=plugins/data/skills/investigate-user-rating/scripts/internal_accounts.py
find plugins -name internal_accounts.py ! -path "$canonical" -exec diff -q "$canonical" {} \;
```

**Canonical source for the tenure-rendering rules.** [`plugins/data/skills/profile-user/references/engagement.md`](../plugins/data/skills/profile-user/references/engagement.md) → "Tenure rendering" is canonical for how an Aura sign-up / Aurie join date is rendered from `GET /ai/usage/engagement/:userId` — the two bulk-stamp guards (Guard A `aiCreatedAt` sentinel prefix, Guard B `overall.dayCount > daysSinceAiCreated + 1`), the Aura `createdAt` stamps, and the six render forms. Copies in sibling skills; the `find` below is the authoritative membership check — edit the canonical first, propagate in the same PR.

Two **rules-only** copies live at `plugins/data/skills/run-rating-feedback-analysis-cadence/references/tenure-rendering.md` and `plugins/data/skills/run-feedback-analysis-cadence/references/tenure-rendering.md`.

```bash
canonical=plugins/data/skills/run-rating-feedback-analysis-cadence/references/tenure-rendering.md
find plugins -name tenure-rendering.md ! -path "$canonical" -exec diff -q "$canonical" {} \;
```

**Canonical source for `dedup.md`.** [`plugins/data/skills/run-feedback-analysis-cadence/references/dedup.md`](../plugins/data/skills/run-feedback-analysis-cadence/references/dedup.md) is canonical for how the two rating-analysis cadences decide what is unprocessed: **the Slack thread's own analysis-bot reply, and nothing else.** It covers the `auth.test` + `conversations.replies` check, the rule that every terminal outcome posts *something* (review / safety placeholder / error note) because a thread with no reply is by definition still unprocessed, the transient-vs-terminal split that decides retry vs error-reply, oldest-first with no cap, and the pre-post race check. Copies in sibling skills; the `find` below is the authoritative membership check — edit the canonical first, propagate in the same PR.

**It replaced `state-file.md`** ([ENG-1055](https://linear.app/aura-health/issue/ENG-1055)).

```bash
canonical=plugins/data/skills/run-feedback-analysis-cadence/references/dedup.md
find plugins -name dedup.md ! -path "$canonical" -exec diff -q "$canonical" {} \;
```

**Canonical source for `record-conversation.md`.** [`plugins/data/skills/run-feedback-analysis-cadence/references/record-conversation.md`](../plugins/data/skills/run-feedback-analysis-cadence/references/record-conversation.md) is canonical for how the two rating-analysis cadences write a reviewed rating to `Learnings/Conversations`: negative and neutral conversation-rated entries only (never positives, journal or profile ratings, safety placeholders or error replies), dedup on `conversation_id` with update in place, the row-field mapping from the entry and its posted review, and the rule that a failed write never touches the reply and is counted as `create_failed` (recovered by the backfill) or `update_failed` (named for a manual re-record). Copies in sibling skills; edit the canonical first, propagate in the same PR ([ENG-1448](https://linear.app/aura-health/issue/ENG-1448)).

```bash
canonical=plugins/data/skills/run-feedback-analysis-cadence/references/record-conversation.md
find plugins -name record-conversation.md ! -path "$canonical" -exec diff -q "$canonical" {} \;
```

**Canonical source for `signal-scope.md`.** [`plugins/data/skills/investigate-user-rating/references/signal-scope.md`](../plugins/data/skills/investigate-user-rating/references/signal-scope.md) is canonical for how far a **single** rating review may reason: the deliverable is the write-up (and, where one is recorded, the `Learnings/Conversations` row), a `Signal:` line carries an observation plus a **falsifiable hypothesis** rather than a prompt change, the evidentiary weight has to be stated, and a **designed** behaviour must not be written up as a defect. Copies in sibling skills; the `find` below is the authoritative membership check — edit the canonical first, propagate in the same PR.

**The rule exists because a per-entry reviewer reading only a transcript cannot tell a product decision from a defect.** ([ENG-1323](https://linear.app/aura-health/issue/ENG-1323); [ENG-1311](https://linear.app/aura-health/issue/ENG-1311)).

```bash
canonical=plugins/data/skills/investigate-user-rating/references/signal-scope.md
find plugins -name signal-scope.md ! -path "$canonical" -exec diff -q "$canonical" {} \;
```

**No analysis cadence keeps a dedup watermark on disk.**

**No cadence skill schedules itself.**

This replaces a self-arming model where each group owned its own cron, schedule, and fire body. ([ENG-1045](https://linear.app/aura-health/issue/ENG-1045); [ENG-1051](https://linear.app/aura-health/issue/ENG-1051)).

**Canonical source for `bq.py`.** [`plugins/eng/skills/query-bigquery/scripts/bq.py`](../plugins/eng/skills/query-bigquery/scripts/bq.py) is canonical for the BigQuery helper (GCP_KEY service-account auth, read-only `query` with dry-run + byte-cap + params, `partition_exists` / `table_exists` / `list_tables` / `describe_table`, `assert_fresh` / `max_build_stamp` for gating reads of wholesale-rebuilt dimension tables, `load_partition` for idempotent per-day WRITE_TRUNCATE partition replace, and `recreate_table` for schema migrations). Copies in sibling skills; the `find` below is the authoritative membership check — edit the canonical first, propagate in the same PR.

**On `assert_fresh`.** ([ENG-1214](https://linear.app/aura-health/issue/ENG-1214)).

```bash
canonical=plugins/eng/skills/query-bigquery/scripts/bq.py
find plugins -name bq.py ! -path "$canonical" -exec diff -q "$canonical" {} \;
```

**Canonical source for the `uid_prefix()` derivation.** [`plugins/data/skills/report-conversation-patterns/scripts/sample.py`](../plugins/data/skills/report-conversation-patterns/scripts/sample.py) is canonical for how a Firebase/provider user id is shortened for display. Copies in sibling skills; the `find` below is the authoritative membership check — edit the canonical first, propagate in the same PR.

**Nothing here is filename-keyed, so there is no `diff -q` for it** (tests/test_uid_prefix_rule.py; [ENG-1142](https://linear.app/aura-health/issue/ENG-1142)).

**Defining `uid_prefix` is what the guard looks for, so a writer that never defines one is invisible to it** ([ENG-1324](https://linear.app/aura-health/issue/ENG-1324)).

**The second half of that fix is the reusable part, and its scope is the load-bearing decision.** (tests/test_uid_prefix_rule.py).

The rule the copies encode: **a bare `uid[:8]` is not a prefix, it is a guess that the first 8 characters distinguish users.** For `google:112…` they don't — every such id yields `google:1` ([ENG-1060](https://linear.app/aura-health/issue/ENG-1060)).

**`uid_prefix` is not a dedup key in the Signals table** ([ENG-1115](https://linear.app/aura-health/issue/ENG-1115)).

**Canonical source for `service_tier.py`.** [`plugins/eng/skills/qa-prompt/scripts/service_tier.py`](../plugins/eng/skills/qa-prompt/scripts/service_tier.py) is canonical for how a skill that **spends** on OpenAI inference picks its service tier — the `flex` default, the choice→wire mapping (`standard` is sent as `default`; the pre-rename `priority` still maps to `fast`), the `auto` fallback OpenAI's flex guide names for a capacity retry, `require_explicit` for the latency exemption, and the two flex-refusal signals. Copies in sibling skills; the `find` below is the authoritative membership check — edit the canonical first, propagate in the same PR.

**The rule it encodes: an omitted `service_tier` bills at standard** ([ENG-1247](https://linear.app/aura-health/issue/ENG-1247); [ENG-1171](https://linear.app/aura-health/issue/ENG-1171)).

**A `diff -q` cannot see the property that matters here** (tests/test_service_tier.py).

```bash
canonical=plugins/eng/skills/qa-prompt/scripts/service_tier.py
find plugins -name service_tier.py ! -path "$canonical" -exec diff -q "$canonical" {} \;
```

**Canonical source for `braintrust_prompt.py` and `render_template.py`.** [`plugins/eng/skills/qa-prompt/scripts/`](../plugins/eng/skills/qa-prompt/scripts/) is canonical for fetching a Braintrust prompt version (template, model, params, `tool_functions` resolved to schemas, the `_xact_id` actually fetched) and rendering its `{{input}}` / `{{metadata.*}}` template against a dataset row. [`eng:compare-prompt-versions`](../plugins/eng/skills/compare-prompt-versions/scripts/) carries byte-identical copies. Edit the canonical first, propagate in the same PR.

```bash
for f in braintrust_prompt.py render_template.py; do
  canonical=plugins/eng/skills/qa-prompt/scripts/$f
  find plugins -name $f ! -path "$canonical" -exec diff -q "$canonical" {} \;
done
```

**Canonical source for `manifest.py`.** [`plugins/shared/skills/retrieve-knowledge/scripts/manifest.py`](../plugins/shared/skills/retrieve-knowledge/scripts/manifest.py) is canonical for reading a skill's or an agent's `knowledge:` manifest back from disk at run time — the frontmatter-slice parser (block lists indented or indentless — the install cache re-serialises every list indentless — with comments, quoted items, inline `["*"]` / `[]`), the `<plugin>:<skill>` → SKILL.md and `<plugin>:<agent>` → `agents/<agent>.md` resolution ladder (in-repo, install cache, local-agent-mode opaque ids via `manifest.json`; roots from the script's own path + `CLAUDE_PLUGIN_ROOT`, never cwd — the same ladder as `slack_post.py`'s footer version), bare agent names under `$CLAUDE_PROJECT_DIR/.claude/agents` then `~/.claude/agents` (filename or frontmatter `name`), and `--check-write`, which applies the wildcard / `Learnings/`+`Signals/` boundary rules from *Knowledge manifest* below and exits 0 / 1 / 2 for allowed / refused / unresolvable. Copies in sibling skills; the `find` below is the authoritative membership check — edit the canonical first, propagate in the same PR. (tests/test_knowledge_manifest_resolver.py).

```bash
canonical=plugins/shared/skills/retrieve-knowledge/scripts/manifest.py
find plugins -name manifest.py ! -path "$canonical" -exec diff -q "$canonical" {} \;
```

**Canonical source for `resource_map.md` (the crisis-line tables).** [`plugins/eng/skills/investigate-memory-ingestion-guardrail/references/resource_map.md`](../plugins/eng/skills/investigate-memory-ingestion-guardrail/references/resource_map.md) is canonical for the country × category crisis/help lines both guardrail skills draft CS outreach from; [`eng:investigate-conversation-guardrail`](../plugins/eng/skills/investigate-conversation-guardrail/references/resource_map.md) carries a copy ([PROD-7238](https://linear.app/aura-health/issue/PROD-7238)), and [`eng:review-conversation-guardrails`](../plugins/eng/skills/review-conversation-guardrails/references/resource_map.md) carries a third so a human reviewer's draft check uses the same lines. **Only the dial-able span is shared** — `## Verification cadence` through `## Global backstops`; each file's header and `## Mapping …` section is skill-specific, so a whole-file `diff -q` is red by design. The span is compared byte for byte, and a new `resource_map.md` anywhere under `plugins/` fails the family check, by [`tests/test_resource_map_copies.py`](../tests/test_resource_map_copies.py). Edit a number in the canonical first and copy the span verbatim. The two triage skills' `email_template.md` and the two cadences' `sweep_unanalyzed.py` are **forks**, not copies — adapted for a live conversation vs a passive upload, and for a different alert body — so they carry no drift check; a rule change in one (a tone rule, a closing line, the Slack transport) is propagated by reading the other.

**Canonical source for the conversation-guardrail `email_template.md`.** [`plugins/eng/skills/investigate-conversation-guardrail/references/email_template.md`](../plugins/eng/skills/investigate-conversation-guardrail/references/email_template.md) is canonical; [`eng:review-conversation-guardrails`](../plugins/eng/skills/review-conversation-guardrails/references/email_template.md) carries a **whole-file, byte-identical** copy, because a reviewer's corrected draft must pass the same rules the triage skill drafted it by. Edit the canonical first and copy the whole file. The memory-ingestion skill's template is a fork and stays outside this pair. Pinned by [`tests/test_email_template_copies.py`](../tests/test_email_template_copies.py), which also fails on a new `email_template.md` that isn't declared a copy or a fork.

**Canonical source for `outreach_levels.md` (who is tagged, and whether a conversation-guardrail case gets an email).** [`plugins/eng/skills/investigate-conversation-guardrail/references/outreach_levels.md`](../plugins/eng/skills/investigate-conversation-guardrail/references/outreach_levels.md) is canonical; [`eng:review-conversation-guardrails`](../plugins/eng/skills/review-conversation-guardrails/references/outreach_levels.md) carries a **whole-file, byte-identical** copy, so the reviewer's send rule and the triage's no-email level can never disagree. Edit the canonical first and copy the whole file. Pinned by the same test file.

```bash
python3 -m unittest tests.test_email_template_copies -q
```

```bash
python3 -m unittest tests.test_resource_map_copies -q
```

**Canonical source for `held_uids.py` (the held-account keep list).** [`plugins/eng/skills/cleanup-orphaned-auth/scripts/held_uids.py`](../plugins/eng/skills/cleanup-orphaned-auth/scripts/held_uids.py) is canonical. Every skill that deletes or rewrites users ships a byte-identical copy: clean-up-users, disable-user-account, delete-coach, delete-facebook-user-data, respond-to-erasure, preserve-incident-account and fix-one-auth. It reads `~/.claude/held-accounts/held-uids.json` (`HELD_UIDS_JSON` overrides; mode 600, outside the repo, so no uid is ever written here), refuses a missing, empty or untrusted list, and names a skipped uid by its prefix only. preserve-incident-account alone loads it with `required=False`, since a held account is what it preserves. Pinned over every copy by [`tests/test_held_uids.py`](../tests/test_held_uids.py), which also fails when a user-delete skill lacks a copy ([ENG-1890](https://linear.app/aura-health/issue/ENG-1890)).

```bash
canonical=plugins/eng/skills/cleanup-orphaned-auth/scripts/held_uids.py
find plugins -name held_uids.py ! -path "$canonical" -exec diff -q "$canonical" {} \;
```

**Canonical source for `writeup.py` (house rules for incident write-ups).** [`plugins/eng/skills/preserve-incident-account/scripts/writeup.py`](../plugins/eng/skills/preserve-incident-account/scripts/writeup.py) is canonical; [`eng:preserve-prod-logs`](../plugins/eng/skills/preserve-prod-logs/scripts/writeup.py) carries a byte-identical copy. It stamps "Draft — Privileged & Confidential — <date> — reflects information currently known" on an incident document and refuses the words on the wording list, each with its replacement, and (check and wrap) personal data and a card id in the file name (ENG-1818, ENG-1827, ENG-1889). Edit the canonical first and copy the whole file. Behaviour is pinned over every copy, and a new `writeup.py` anywhere under `plugins/` must be listed, by [`tests/test_incident_writeup.py`](../tests/test_incident_writeup.py).

```bash
canonical=plugins/eng/skills/preserve-incident-account/scripts/writeup.py
find plugins -name writeup.py ! -path "$canonical" -exec diff -q "$canonical" {} \;
```

**Canonical source for `incident_guard.py` (no distill of an incident session).** [`plugins/shared/skills/distill-sessions-daily/scripts/incident_guard.py`](../plugins/shared/skills/distill-sessions-daily/scripts/incident_guard.py) is canonical; [`shared:distill-session`](../plugins/shared/skills/distill-session/scripts/incident_guard.py) carries a byte-identical copy. It skips a session whose title starts with `[Privileged`, whose id is in `~/.claude/legal-hold/exclude-sessions`, or any session while an incident hold is marked and that file is missing. Edit the canonical first and copy the whole file; the copies and both skip paths are pinned by [`tests/test_distill_sessions_find.py`](../tests/test_distill_sessions_find.py).

```bash
canonical=plugins/shared/skills/distill-sessions-daily/scripts/incident_guard.py
find plugins -name incident_guard.py ! -path "$canonical" -exec diff -q "$canonical" {} \;
```
```

**Canonical source for `export_ai_items.py` (the insights, affirmations and memory data-request exports).** [`plugins/eng/skills/export-user-insights/scripts/export_ai_items.py`](../plugins/eng/skills/export-user-insights/scripts/export_ai_items.py) is canonical. Each of these export skills ships a byte-identical copy and passes its own `--entity`, so one script decides what is readable, who wrote it, and how the PDF looks. Its layout follows `eng:export-user-journal`'s (the same CSS and author labels), but that is a separate script, so a layout change there is checked here by hand. Edit the canonical first and copy the whole file. The copies and their behaviour are pinned by [`tests/test_export_ai_items.py`](../tests/test_export_ai_items.py), which runs every test over every copy.

```bash
canonical=plugins/eng/skills/export-user-insights/scripts/export_ai_items.py
find plugins -name export_ai_items.py ! -path "$canonical" -exec diff -q "$canonical" {} \;
```
