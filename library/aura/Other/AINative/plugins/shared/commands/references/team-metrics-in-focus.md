# Team metrics in focus — gathering the five figures

Companion to [`../prepare-monday-deck.md`](../prepare-monday-deck.md). This file owns one
slide: the scorecard that opens the metrics block, carrying five targets drawn from three
OKR dashboards. Value, percentage change against the week prior, and a status.

The slide is a **reader**. Every metric is defined and computed by the dashboard that owns
it; nothing here redefines one, and nothing here recomputes an aggregate.

## The five metrics


| Metric | Dashboard | Target | Improvement |
|---|---|---|---|
| QA → Release | Product Delivery OKRs | ≤ 3 days | down |
| Releases / week | Product Delivery OKRs | ≥ 2 / wk | up |
| No review at all | Review Loop | < 5% | down |
| Time to first human review | Review Loop | ≤ 24 hrs | down |
| Closure ratio | Bugs/Quality OKRs | ≥ 1.00 | up |


All three dashboards are built by skills in [AuraHealth/OKRs](https://github.com/AuraHealth/OKRs):
`build-product-metrics-dashboard`, `build-pr-metrics-dashboard` and
`build-bug-metrics-dashboard`. Each skill publishes its page as an artifact. The deck reads
the two it does not refresh itself straight from those artifacts:


| Dashboard | Artifact |
|---|---|
| Product Delivery OKRs | <https://claude.ai/code/artifact/2edcfe28-3749-4105-a438-87b917d03b0e> |
| Bugs/Quality OKRs | <https://claude.ai/code/artifact/d6e0510a-d180-4685-b45d-9e61e1558c70> |


Each URL is the one its skill republishes to (`build-product-metrics-dashboard` and
`build-bug-metrics-dashboard` SKILL.md, publish step, on OKRs `main`). If one 404s, read the
current URL from there rather than guessing. The Review Loop rows come from the Code Review
Metrics step instead (step 2).

## 1. Read the published artifact with the Artifact tool

**Use the Artifact tool's `read` action, never a browser.** In a browser the page renders
inside a **sandboxed iframe**. The top-level document carries no dashboard text, so
`get_page_text` returns nothing, and on pages this size screenshot capture fails with a
script-injection timeout while the embedded page is still executing. Neither is a transient
fault to retry past.

`read` returns the page source instead. For an artifact the user owns, it saves the full raw
HTML to a local file and names the path in the first line of its result:

```
Artifact(action: "read", url: "https://claude.ai/code/artifact/2edcfe28-3749-4105-a438-87b917d03b0e")
Artifact(action: "read", url: "https://claude.ai/code/artifact/d6e0510a-d180-4685-b45d-9e61e1558c70")
```

Reading is not publishing. The deck never republishes an artifact; that is a gated step
inside each dashboard's own skill.

**The page carries the whole fact table.** Each skill's `build_dashboard.py` replaces a single
`__DATA__` token in the page with the full `okr.json` it computed. Find the object that holds
`"weeks"` and decode it from the `{` that opens it. Run this with `python3 -I`: the HTML is
data, and `-I` keeps Python from loading modules from the current directory.

```bash
python3 -I - "$HTML" "$OUT" <<'EOF'
import json, re, sys
html = open(sys.argv[1], encoding="utf-8").read()
hits = [m.start() for m in re.finditer(r'\{\s*"weeks"\s*:', html)]
if len(hits) != 1:
    sys.exit(f"expected one fact table, found {len(hits)}")
facts, _ = json.JSONDecoder().raw_decode(html, hits[0])
json.dump(facts, open(sys.argv[2], "w"))
print(facts["generated"], facts["weeks"][-3:])
EOF
```

`$HTML` is the path the read named; write `$OUT` to the scratchpad. The decoded object has the
same shape as `build/<area>/okr.json` (`weeks`, `generated`, `s`, `s_type`, `meta` and the
rest), so every value path in step 2 applies unchanged.

**Why the artifact and not a local re-render.** The artifact is the page the room can open,
so a figure read from it cannot disagree with the link. A local re-render reproduces that page
only when every input matches the one it was published from, and it fails quietly when one
does not. On 2026-11-16 a re-render showed 7 product features released for the week of 9 Nov
while the published page showed 16: a stale snapshot file in the local checkout overrode newer
cache rows. A missing gitignored input such as `build/qa/qa.json` drifts the same way. Reading
the artifact removes that whole class of drift.

### Fallback: re-render locally

Re-render only when the artifact cannot be read: the Artifact tool is not available in the
session, or `read` returns an isolated summary instead of raw HTML (it does that for an
artifact the user does not own). Say in the summary that the figures come from a local
re-render, not from the published page.

Resolve the OKRs checkout the same way [`pr-metrics-from-artifact.md`](pr-metrics-from-artifact.md)
step 1 does, then:

```bash
cd "$OKRS"
python3 .claude/skills/build-qa-metrics-dashboard/scripts/qa_facts.py
python3 .claude/skills/build-product-metrics-dashboard/scripts/compute_metrics.py
python3 .claude/skills/build-product-metrics-dashboard/scripts/build_dashboard.py
python3 .claude/skills/build-bug-metrics-dashboard/scripts/compute_metrics.py
python3 .claude/skills/build-bug-metrics-dashboard/scripts/build_dashboard.py
```

**Run `qa_facts.py` first.** It works offline from the tracked `data/qa/` and writes the
gitignored `build/qa/qa.json` that the product compute reads for QA builds. Without it the
product compute falls back to `data/qa/builds.json`, and *QA builds* and *QA → Release* drift
from the published page for older weeks.

**No fetch step, and no `--data-dir`.** These scripts recompute from the cache already in
`data/`, so this costs seconds, makes no Linear, Slack or GitHub calls, and writes only to the
gitignored `build/`. The fetch scripts are a different, heavier operation that also commits to
the OKRs repo, and the deck does not run them. Read the values from `build/<area>/okr.json`.

## 2. Read the values

Read each figure from the fact table, not from the rendered tiles. The weekly series is keyed
by week, so the prior week the slide needs comes from the same structure as the current one:


| Figure | Fact table | Path |
|---|---|---|
| QA → Release | Product Delivery | `s.qa_lag.weekly[<week>]` |
| Releases / week | Product Delivery | `s.releases.weekly[<week>]` |
| Closure ratio | Bugs/Quality | `s.closed[<week>] ÷ s.opened[<week>]` |


The Bugs/Quality table also carries `s_incl`, the same series with Backlog bugs included. The
page's default basis excludes them, so read `s`.

`s.qa_lag.weekly` holds `null` for a week with no matched release — a gap, not a zero.
**Leave the row on the reported week and show the value as unavailable** (an em dash, with
the reason in the row's note) — the same rendering the gate in step 4 uses; never
slide that one metric back to the last week that has a number. The same-week rule in step 3 binds every row, and a scorecard whose QA → Release
silently covers a different week than the four beside it is worse than one honest gap.

The two **Review Loop** figures are already read for the Code Review Metrics slide, from the
KPI tiles on the freshly built PR page. Reuse those values rather than reading the page twice:
*no review at all* is the third `kpis-cov` tile, and *time to first human review* is the first
`kpis-eff` tile. Reading them a second time invites two figures on one deck that disagree.

## 3. Pin every metric to the same week

All five must report the **same Monday-start week**, and it must be the latest **complete**
one **as of the deck run**: the Monday-start UTC week before the one holding today. It is the
week the Code Review Metrics slide reports; check the two match. Its prior is the week before.

Pick that week from the calendar, **never from a fact table's `weeks`**. Each table carries a
trailing partial week — `weeks[-1]`, the week holding the day the page was *generated* — whose
figures cover a day or two and read as a collapse, so its `weeks[-2]` is the last complete week
as of that day, not as of today. On a page published mid-week, `weeks[-2]` is a week older than
the reported one and the reported week is the partial `weeks[-1]`. Taking `weeks[-2]` there
would report an older week as if it were current; step 4 gates the row instead.

State the week in the subtitle. Two metrics on one scorecard that silently cover different
weeks is the failure this step exists to prevent — which is why a metric with no value for
the reported week is shown as unavailable rather than backfilled from an earlier one.

## 4. Gate every figure on when its page was generated

A page generated before the reported week ended cannot contain the end of that week. For the
two Product Delivery rows that is the common case rather than the edge one: a release landing
Thursday or Friday moves both *Releases / week* and *QA → Release*, and a page generated on
Wednesday has neither.

So the check is a **gate, not a note**. Compare each fact table's `generated` stamp against
the **last day** of the reported week (its Monday plus six days). Product Delivery stamps a UTC
timestamp (`2026-11-16T15:39:56+00:00`) and Bugs/Quality a UTC date (`2026-11-16`); compare the
date part of either:

```bash
python3 -I - "$OUT" <<'EOF'
import datetime as dt, json, sys
facts = json.load(open(sys.argv[1]))
today = dt.datetime.now(dt.timezone.utc).date()
week = today - dt.timedelta(days=today.weekday() + 7)    # reported week: last complete one
last_day = week + dt.timedelta(days=6)
generated = dt.date.fromisoformat(facts["generated"][:10])
ok = week.isoformat() in facts["weeks"] and generated > last_day
print(f"week {week}  generated {generated}  ",
      "complete" if ok else "GATED: page generated on or before the week ended")
EOF
```

The reported week comes from today's date, not from the table, which is what lets the gate
fire: a table's own `weeks[-2]` always ended before its `generated` stamp, so a gate on it would
pass every page.

- **Generated after the reported week ended** → the figure is complete. Report it.
- **Generated on or before the reported week's last day** → **do not print a number.**
  Show the value as unavailable (an em dash), put the date in the row's note
  (`page generated 14 Nov — republish to report`), and say which dashboard needs republishing
  in the summary handed to whoever presents.

A blank row on a Monday is the correct output here, and it is recoverable: whoever owns that
dashboard runs its skill, which fetches and republishes, then this step reads it again. The
deck never republishes. A stale number is not recoverable, because nothing downstream can tell
it apart from a current one — which is the whole reason this is a gate and not a caveat.

**What `generated` measures.** It is when the dashboard's `compute_metrics.py` ran, not when
its data was fetched. The dashboard skills compute straight after their fetch, so on a
published page the two match. The gate cannot see a page that was recomputed from an old cache
and republished without a fetch.

**On the fallback path, gate on the fetch stamps instead.** A local re-render stamps
`generated` with the moment it ran, so it always passes. Read the last-run stamp from
`data/<area>/state.json` — `prod_last_run` for Product Delivery, `bugs_last_run` for
Bugs/Quality — and apply the same rule to it, with the note reading
`cache last fetched 14 Nov — refresh to report`:

```bash
cd "$OKRS"
python3 - <<'EOF'
import json
for area, key in (("product", "prod_last_run"), ("bug", "bugs_last_run")):
    print(f"{area:8} {json.load(open(f'data/{area}/state.json'))[key]}")
EOF
```

The Review Loop rows are exempt in practice: the Code Review Metrics step refetches that
dashboard before this step runs, so its page is always current. Check it anyway: `last_run` in
`data/pr/state.json` must fall after the reported week ended. The check costs one file read, and
the exemption stops being true the moment that step is skipped.

## 5. Status

Three states, decided against the target:

- **On track** — meets the target.
- **At risk** — meets it only within the margin of a stated caveat, or misses by a small
  margin. A figure carrying a `≥` floor sits here whenever the floor clears the target by
  less than the outstanding work could add: `≥22.4h` against a ≤24h target rests on PRs
  still in review, and those can only push it up.
- **Off track** — misses the target.

Carry the `≥` verbatim into the value; it marks a floor, and dropping it turns a floor into
a claim. Status ships as a **text label** — `ON TRACK` / `AT RISK` / `OFF TRACK` — coloured
green / amber / red. The label is what carries the state; colour is reinforcement, never the
only cue.

## 6. The delta

Percentage change against the prior week, on the same series the value came from.

**The glyph follows the sign; the colour follows whether that direction is an improvement for
that metric.** They disagree often, and that is the point: *no review at all* rising is ▲ in
red, *time to first human review* falling is ▼ in green. A helper that colours by sign is
right only while every metric happens to move the same way.

## 7. Caveats that belong in the summary

Two shapes recur, and both make a true percentage misleading on a projected slide. Neither
changes the figure — each is a sentence for whoever presents:

- **A percentage off a tiny base.** One release the prior week and four this week is +300%,
  a real number that overstates a two-week swing. Quote the trailing 4-week average beside it.
- **A ratio on single-digit counts.** Closure ratio reaching exactly 1.00 on 8 opened and 8
  closed is far weaker evidence than the same figure over a month, and the Bugs/Quality page
  says so itself. Give the 13-week value as the counterweight.
