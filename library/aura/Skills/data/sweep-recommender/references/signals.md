# Signals, thresholds and provenance

Every threshold here was measured against production on **2026-09-22**. A threshold is only as
good as the baseline it was set from, so each row records what was observed at the time — if
the current value sits far from it, re-measure before reading a drift as news.

## `empty_model_rate` — layer `ranking`

**Breach:** more than **50/h** occurrences of the `No results from this model` audit line in
`content-service`.

**Observed 2026-09-22:** `activation-model` at roughly **5,290/h** — two orders of magnitude
over. It holds a third of the blend weight in `getNonActivatedUserRecipe` and contributed zero
attributed listens over seven days. `user-preferences-model` ~492/h, `failover-model` ~218/h.

**Why it matters:** a model returning an empty array does not fail. It drops out of the blend
and the survivors re-rank around it, and because scalars are computed from the full recipe and
never recomputed, nothing about the output says a contributor went silent. This is the same
failure shape that hid the neural-model collapse for seven years.

**Caveat:** only `content` logs this line. The other seven recommenders carry
`// ignore model that inject some contents` at the same branch and log nothing, so this signal
is blind outside content until ENGBACK-1392 lands.

## `empty_response_rate` — layer `filters`

**Breach:** more than **50/h** occurrences of the `No recommendation` audit line.

**Observed 2026-09-22:** **329/h**, on the order of 7,900 a day. Each is a user looking at a
row, shelf or feed with nothing in it. `program` shows the same shape at 60/h against 863
served — 6.5% of program requests.

**Why it matters:** the caller's-side view is worse. Aurie's `recommendTracks` retries with
progressively less of the user's intent each time the recommender comes back empty — 1,579
failover attempts for 506 answers in 24 hours, so the query that finally succeeds is the one
with the least intent left in it.

## `catalogue_coverage` — layer `candidate supply`

**Breach:** under **30%** of the catalogue drawing a completed listen in 30 days.

**Observed 2026-09-22:** **28.2%** — 20,446 of 72,486 tracks. Already breaching.

**Why it matters:** it is the counter-metric for every ranking change. A candidate that wins on
completions by serving the head harder is caught here and nowhere else. Note the composition
problem underneath it: `podcast` is 29.1% of the catalogue and supplies 2 of the top 500
popular tracks.

## `sub3s_skip_rate` — layer `ranking`

**Breach:** over **18%** of plays abandoned within three seconds.

**Observed 2026-09-22:** **~17%**, about 4,800 a day, against ~52% completion and ~41% skip.
Set just above the current value deliberately — this is the loop's release guardrail, so the
threshold is a regression alarm rather than an aspiration.

**Why it matters:** it is the sharpest available "wrong track" signal. The user saw the card,
tapped it, and bailed on sight.

## Not yet wired

Named here so their absence is visible rather than assumed:

- **Coach cohort exposure drift** — the tenure-bias axis (ENGBACK-1395). Needs the exposure
  readout from `data:evaluate-recommender`.
- **Model attribution share** — `model_source` on listens. Only 28% of listens carry it
  (ENGBACK-1379), so a shift cannot yet be told from a coverage change.
- **Aurie failover depth** — retries per answer from `recommendTracks`. Lives in
  `intelligence-service` logs, a second log source this script does not read yet.
- **The harness grid sweep** — source 2 of the loop's ideation design.
